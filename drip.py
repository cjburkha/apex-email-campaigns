#!/usr/bin/env python3
"""
drip.py — Automated drip campaign runner.

Usage:
    python drip.py enroll --campaign window-inspection --query "SELECT * FROM leads WHERE state='WI'"
    python drip.py run --campaign window-inspection

This creates drip enrollment rows and sends scheduled drip steps from campaign config.
"""

import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import boto3
import click
from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader, Template, TemplateNotFound

from db import get_conn, init_db
from send import _add_utm, _add_utm_text, _normalize_phone, _make_unsubscribe_token, _unsubscribe_url, _send_sms, _pixel_html, _html_to_text

load_dotenv()


SHORTLINK_HOST = os.getenv("SHORTLINK_HOST", "https://windowsbyburkhardt.com")
SHORTLINK_TARGET_FRAGMENT = "#schedule"


_BASE36 = "0123456789abcdefghijklmnopqrstuvwxyz"


def _base36(n: int) -> str:
    if n == 0:
        return "0"
    out: list[str] = []
    while n:
        n, r = divmod(n, 36)
        out.append(_BASE36[r])
    return "".join(reversed(out))


def _referral_code(lead_id: int) -> str:
    """Per-lead referral code for the sold-customer drip. Decoded by the website
    at /r/:code (services/referralService.js). Format: base36(id) + 6 hex chars of
    HMAC-SHA256("ref:" + id, secret). Secret resolution mirrors the Node side so
    the same code round-trips back to the same lead_id."""
    if lead_id is None or int(lead_id) <= 0:
        raise ValueError("lead_id must be a positive integer")
    secret = os.getenv("REFERRAL_SECRET",
              os.getenv("PIXEL_SECRET",
              os.getenv("UNSUBSCRIBE_SECRET", "change-me"))).encode("utf-8")
    mac = hmac.new(secret, f"ref:{int(lead_id)}".encode("utf-8"), hashlib.sha256).hexdigest()[:6]
    return f"{_base36(int(lead_id))}{mac}"


def _referral_url(lead_id: int) -> str:
    return f"{SHORTLINK_HOST}/r/{_referral_code(lead_id)}"


def _load_campaign(campaign: str):
    campaign_dir = Path("campaigns") / campaign
    config_path = campaign_dir / "config.json"
    if not config_path.exists():
        raise click.ClickException(f"config.json not found: {config_path}")

    config = json.loads(config_path.read_text())
    if "drip_steps" not in config or not config["drip_steps"]:
        raise click.ClickException("Campaign config must include drip_steps to use drip.py")

    env = Environment(loader=FileSystemLoader(str(campaign_dir)))
    return campaign_dir, config, env


def _render_step_templates(step: dict, config: dict, env: Environment, vars: dict, campaign_id: str,
                           lead_id: int | None = None, week: int | None = None):
    subject = Template(step.get("subject", config["subject"]).strip()).render(**vars)
    html = None
    txt = None
    sms = None

    html_file = step.get("template_html") or config.get("template_html") or "template.html"
    txt_file = step.get("template_txt") or config.get("template_txt")
    sms_file = step.get("template_sms") or config.get("sms_template")

    # utm_campaign is sourced from the campaigns table id (one source of truth);
    # source/medium remain configurable per campaign in config.json.
    utm = {
        "utm_source":   config.get("utm_source",   "email"),
        "utm_medium":   config.get("utm_medium",   "email"),
        "utm_campaign": campaign_id,
    }
    # short_url_sms / short_url_email may be pre-set by the caller (run-all knows
    # current_week + short_slug). Fall back to the bare site URL so any template
    # still renders even if shortlinks aren't wired for this campaign.
    vars.setdefault("short_url_sms",   f"{SHORTLINK_HOST}/#schedule")
    vars.setdefault("short_url_email", f"{SHORTLINK_HOST}/#schedule")
    # Backwards-compat alias for any template that still uses {{ short_url }}.
    vars.setdefault("short_url", vars["short_url_sms"])

    if html_file:
        try:
            html = _add_utm(env.get_template(html_file).render(**vars), utm)
            # Append open-tracking pixel just before </body> (or end if no body tag)
            if lead_id is not None and week is not None:
                pixel = _pixel_html(campaign_id, lead_id, week)
                html = html.replace("</body>", pixel + "</body>", 1) if "</body>" in html else (html + pixel)
        except TemplateNotFound:
            html = None
    # Plain-text fallback: explicit template_txt > implicit template.txt > derived from html.
    # SES multipart/alternative wants a text part for deliverability + watch/screen-reader UX.
    if txt_file:
        try:
            txt = _add_utm_text(env.get_template(txt_file).render(**vars), utm)
        except TemplateNotFound:
            pass
    if txt is None:
        try:
            txt = _add_utm_text(env.get_template("template.txt").render(**vars), utm)
        except TemplateNotFound:
            if html:
                txt = _html_to_text(html)
    if sms_file:
        sms = _add_utm_text(env.get_template(sms_file).render(**vars), utm)

    return subject, html, txt, sms


def _prepare_headers(reply_to: str, lead_id: int, email: str):
    token = _make_unsubscribe_token(lead_id, email)
    unsubscribe_url = _unsubscribe_url(lead_id, token)
    return [
        {
            "Name": "List-Unsubscribe",
            "Value": f"<mailto:{reply_to}?subject=unsubscribe>, <{unsubscribe_url}>",
        },
        {
            "Name": "List-Unsubscribe-Post",
            "Value": "List-Unsubscribe=One-Click",
        },
    ]


@click.group()
def drip():
    """Drip campaign commands."""
    pass


@drip.command()
@click.option("--campaign", required=True,
              help="Campaign directory name under campaigns/")
@click.option("--query", "sql_query", default=None,
              help="SQL SELECT query against the leads table (prompted if omitted)")
@click.option("--limit", default=None, type=int,
              help="Cap the number of leads enrolled")
@click.option("--dry-run", is_flag=True,
              help="Preview enrollment without writing to the DB")
def enroll(campaign: str, sql_query: str, limit: int, dry_run: bool):
    init_db()
    campaign_dir, config, env = _load_campaign(campaign)
    campaign_id = config.get("id", campaign)

    if not sql_query:
        # Try campaign's stored enrollment_query first (cohort campaigns)
        conn0 = get_conn()
        row = conn0.execute(
            "SELECT enrollment_query FROM campaigns WHERE id = %s",
            (campaign_id,),
        ).fetchone()
        conn0.close()
        if row and row.get("enrollment_query"):
            sql_query = row["enrollment_query"]
            click.echo(f"  using enrollment_query from campaigns table for {campaign_id}")
        else:
            click.echo("Enter a SQL query to select leads for drip enrollment:")
            sql_query = click.prompt("Query")

    conn = get_conn()
    try:
        leads = conn.execute(sql_query).fetchall()
    except Exception as exc:
        raise click.ClickException(f"Query failed: {exc}")

    if not leads:
        click.secho("No leads returned by query.", fg="yellow")
        conn.close()
        return

    if limit:
        leads = leads[:limit]

    inserted = 0
    for lead in leads:
        if lead.get("unsubscribed_at") is not None:
            continue
        if dry_run:
            click.echo(f"  enroll lead {lead['id']} {lead.get('email') or '<no email>'}")
            inserted += 1
            continue
        result = conn.execute(
            "INSERT INTO campaigns (id, name, subject, from_email, from_name) "
            "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
            (campaign_id, config["name"], config["subject"], config["from_email"], config["from_name"])
        )
        conn.execute(
            "INSERT INTO campaign_sends (campaign_id, lead_id, drip_step, next_send_at) "
            "VALUES (%s,%s,1,NOW()) "
            "ON CONFLICT (campaign_id, lead_id) DO NOTHING",
            (campaign_id, lead["id"])
        )
        if result.rowcount:
            inserted += 1
    if not dry_run:
        conn.commit()
    conn.close()

    click.secho(f"✅ Enrolled {inserted} lead(s) into drip campaign '{campaign_id}'", fg="green")


@drip.command()
@click.option("--campaign", required=True,
              help="Campaign directory name under campaigns/")
@click.option("--limit", default=50, type=int,
              help="Number of due drip rows to process")
@click.option("--dry-run", is_flag=True,
              help="Render and preview drip sends without updating DB")
def run(campaign: str, limit: int, dry_run: bool):
    init_db()
    campaign_dir, config, env = _load_campaign(campaign)
    campaign_id = config.get("id", campaign)

    conn = get_conn()
    pending_rows = conn.execute(
        """
        SELECT cs.id as send_id, cs.lead_id, cs.drip_step,
               l.first_name, l.last_name, l.email, l.city,
               l.state, l.postal_code, l.phone_primary, l.phone_secondary
        FROM campaign_sends cs
        JOIN leads l ON l.id = cs.lead_id
        WHERE cs.campaign_id = %s
          AND cs.next_send_at <= NOW()
          AND l.unsubscribed_at IS NULL
          AND cs.status != 'failed'
        ORDER BY cs.next_send_at
        LIMIT %s
        """,
        (campaign_id, limit)
    ).fetchall()

    if not pending_rows:
        click.secho("✅ No drip rows are due yet.", fg="green")
        conn.close()
        return

    ses = boto3.client("sesv2", region_name=os.getenv("AWS_REGION", "us-east-1"))
    sent = failed = 0

    for i, r in enumerate(pending_rows, 1):
        step_idx = r["drip_step"]
        if step_idx < 1 or step_idx > len(config["drip_steps"]):
            conn.execute(
                "UPDATE campaign_sends SET status='completed', next_send_at=NULL WHERE id=%s",
                (r["send_id"],)
            )
            conn.commit()
            continue

        step = config["drip_steps"][step_idx - 1]
        vars = dict(
            first_name=r["first_name"] or "",
            last_name=r["last_name"] or "",
            email=r["email"],
            city=r["city"] or "",
            state=r["state"] or "",
            postal_code=r["postal_code"] or "",
            phone=_normalize_phone((r.get("phone_primary") or r.get("phone_secondary") or "")),
        )

        subject, html, txt, sms = _render_step_templates(step, config, env, vars, campaign_id, r["lead_id"], step_idx)
        reply_to = config.get("reply_to", config["from_email"])
        headers = _prepare_headers(reply_to, r["lead_id"], vars["email"] or "")

        email_sent = False
        sms_sent = False
        message_id = None
        sms_message_id = None
        sms_status = None
        errors = []

        channel = step.get("channel", "email").lower()
        if channel in ("email", "both") and vars["email"] and html and txt:
            try:
                resp = ses.send_email(
                    FromEmailAddress=f"{config['from_name']} <{config['from_email']}>",
                    Destination={"ToAddresses": [vars["email"]]},
                    ReplyToAddresses=[reply_to],
                    Content={
                        "Simple": {
                            "Subject": {"Data": subject, "Charset": "UTF-8"},
                            "Body": {
                                "Text": {"Data": txt, "Charset": "UTF-8"},
                                "Html": {"Data": html, "Charset": "UTF-8"},
                            },
                            "Headers": headers,
                        }
                    },
                    ConfigurationSetName=os.getenv("SES_CONFIG_SET", "apex-campaigns"),
                )
                email_sent = True
                message_id = resp["MessageId"]
            except Exception as exc:
                errors.append(f"email: {exc}")

        if channel in ("sms", "both") and sms and vars["phone"]:
            try:
                sms_message_id, sms_status = _send_sms(vars["phone"], sms)
                sms_sent = True
            except Exception as exc:
                errors.append(f"sms: {exc}")

        if not email_sent and not sms_sent:
            if not errors:
                errors.append("no valid contact channel available")
            conn.execute(
                "UPDATE campaign_sends SET status='failed', failed_reason=%s WHERE id=%s",
                ("; ".join(errors), r["send_id"]),
            )
            conn.commit()
            failed += 1
            click.secho(f"  [{i}/{len(pending_rows)}] ✗ {r.get('email') or vars['phone']} — {'; '.join(errors)}", fg="red")
            continue

        next_step_idx = step_idx + 1
        if next_step_idx > len(config["drip_steps"]):
            next_send_at = None
            next_status = "completed"
        else:
            delay_days = step.get("delay_days", config.get("default_delay_days", 7))
            next_send_at = datetime.utcnow() + timedelta(days=delay_days)
            next_status = "sent"

        update_sql = ["drip_step=%s", "status=%s", "next_send_at=%s"]
        update_params = [next_step_idx, next_status, next_send_at]
        if message_id:
            update_sql.extend(["message_id=%s", "sent_at=NOW()"])
            update_params.extend([message_id])
        if sms_message_id:
            update_sql.extend(["sms_message_id=%s", "sms_sent_at=NOW()", "sms_status=%s"])
            update_params.extend([sms_message_id, sms_status])
        update_params.append(r["send_id"])
        conn.execute(
            f"UPDATE campaign_sends SET {', '.join(update_sql)} WHERE id=%s",
            tuple(update_params),
        )
        conn.commit()
        sent += 1
        contact_summary = ", ".join(c for c in ["email" if email_sent else None, "sms" if sms_sent else None] if c)
        click.secho(f"  [{i}/{len(pending_rows)}] ✓ {r.get('email') or vars['phone']} ({contact_summary})", fg="green")
        time.sleep(0.5)

    conn.close()
    click.secho(f"\n✅ Drip run complete: {sent} sent, {failed} failed\n", fg="green")


@drip.command(name="run-all")
@click.option("--dry-run", is_flag=True, help="Preview the cohort step without writing to DB or sending.")
@click.option("--force", is_flag=True, help="Advance even if last_advanced_at is within the past 6 days.")
def run_all(dry_run: bool, force: bool):
    """Cohort runner for table-driven campaigns.

    For each campaign WHERE active = TRUE AND current_week < weeks AND enrollment_query IS NOT NULL,
    advance current_week by 1 and send that step's message to every enrolled lead.
    Skips campaigns whose last_advanced_at is within the past 6 days unless --force.
    """
    init_db()
    conn = get_conn()

    campaigns = conn.execute(
        """
        SELECT id, name, weeks, current_week, last_advanced_at, short_slug
          FROM campaigns
         WHERE active = TRUE
           AND enrollment_query IS NOT NULL
           AND current_week < weeks
         ORDER BY id
        """
    ).fetchall()

    if not campaigns:
        click.secho("✅ No active cohort campaigns are due.", fg="green")
        conn.close()
        return

    ses = boto3.client("sesv2", region_name=os.getenv("AWS_REGION", "us-east-1"))

    for camp in campaigns:
        camp_id = camp["id"]
        next_week = camp["current_week"] + 1

        # Throttle: don't fire twice in the same week unless --force
        if camp["last_advanced_at"] and not force:
            row = conn.execute(
                "SELECT (NOW() - %s) < INTERVAL '6 days' AS too_soon",
                (camp["last_advanced_at"],),
            ).fetchone()
            if row and row.get("too_soon"):
                click.secho(f"  ⏩ {camp_id}: skipped (last_advanced_at < 6 days ago)", fg="yellow")
                continue

        # Load this campaign's templates from config.json
        try:
            campaign_dir, config, env = _load_campaign(camp_id)
        except click.ClickException:
            # Try directory by short slug derived from id (drop trailing -<month>-<year>)
            campaign_dir, config, env = _load_campaign(_dir_slug(camp_id))

        steps = config.get("drip_steps") or []
        if next_week < 1 or next_week > len(steps):
            click.secho(f"  ✗ {camp_id}: weeks={camp['weeks']} but config has {len(steps)} steps; skipping", fg="red")
            continue
        step = steps[next_week - 1]

        # All enrolled, opt-in, non-test leads for this campaign
        leads = conn.execute(
            """
            SELECT cs.id AS send_id, l.id AS lead_id, l.first_name, l.last_name,
                   l.email, l.city, l.state, l.postal_code,
                   l.phone_primary, l.phone_secondary
              FROM campaign_sends cs
              JOIN leads l ON l.id = cs.lead_id
             WHERE cs.campaign_id = %s
               AND l.unsubscribed_at IS NULL
               AND l.test_lead = 0
            """,
            (camp_id,),
        ).fetchall()

        click.secho(f"\n  ▶ {camp_id}: week {next_week}/{camp['weeks']}  → {len(leads):,} enrolled leads", fg="cyan")

        if dry_run:
            click.echo(f"     DRY-RUN: would send '{step.get('name', f'step {next_week}')}' to {len(leads):,} leads")
            continue

        sent = failed = 0
        for r in leads:
            vars = dict(
                first_name=r["first_name"] or "",
                last_name=r["last_name"] or "",
                email=r["email"],
                city=r["city"] or "",
                state=r["state"] or "",
                postal_code=r["postal_code"] or "",
                phone=_normalize_phone((r.get("phone_primary") or r.get("phone_secondary") or "")),
            )
            if camp.get("short_slug"):
                vars["short_url_sms"]   = f"{SHORTLINK_HOST}/{_slug_for(camp['short_slug'], 'sms',   next_week)}"
                vars["short_url_email"] = f"{SHORTLINK_HOST}/{_slug_for(camp['short_slug'], 'email', next_week)}"
            if config.get("referral_link"):
                # Referral campaigns: every recipient gets a per-lead /r/<code> URL
                # so the website can credit the right referrer when a referral converts.
                # Pre-bake utm_content=week-N so GA4 can attribute clicks to a specific
                # drip step — _add_utm will merge the rest (source/medium/campaign).
                ref_url = f"{_referral_url(r['lead_id'])}?utm_content=week-{next_week}"
                vars["short_url_email"] = ref_url
                vars["short_url_sms"]   = ref_url
            subject, html, txt, sms = _render_step_templates(step, config, env, vars, camp_id, r["lead_id"], next_week)
            reply_to = config.get("reply_to", config["from_email"])
            headers = _prepare_headers(reply_to, r["lead_id"], vars["email"] or "")

            channel = step.get("channel", "email").lower()
            email_ok = sms_ok = False
            errors: list[str] = []

            if channel in ("email", "both") and vars["email"] and (html or txt):
                try:
                    body = {}
                    if txt: body["Text"] = {"Data": txt, "Charset": "UTF-8"}
                    if html: body["Html"] = {"Data": html, "Charset": "UTF-8"}
                    ses.send_email(
                        FromEmailAddress=f"{config['from_name']} <{config['from_email']}>",
                        Destination={"ToAddresses": [vars["email"]]},
                        ReplyToAddresses=[reply_to],
                        Content={"Simple": {
                            "Subject": {"Data": subject, "Charset": "UTF-8"},
                            "Body": body,
                            "Headers": headers,
                        }},
                        ConfigurationSetName=os.getenv("SES_CONFIG_SET", "apex-campaigns"),
                    )
                    email_ok = True
                except Exception as exc:
                    errors.append(f"email: {exc}")

            if channel in ("sms", "both") and sms and vars["phone"]:
                try:
                    _send_sms(vars["phone"], sms)
                    sms_ok = True
                except Exception as exc:
                    errors.append(f"sms: {exc}")

            if email_ok or sms_ok:
                sent += 1
            else:
                failed += 1

            # SES production account allows 14/sec; we pace at ~10/sec to leave headroom
            # for the SES burst bucket and avoid hammering the SMTP boundary.
            time.sleep(0.1)

        # Advance the cohort
        conn.execute(
            "UPDATE campaigns SET current_week = %s, last_advanced_at = NOW() WHERE id = %s",
            (next_week, camp_id),
        )
        conn.commit()
        click.secho(f"     ✓ advanced to week {next_week}: {sent:,} sent, {failed:,} failed", fg="green")

    conn.close()


def _dir_slug(campaign_id: str) -> str:
    """Strip trailing -mon-year (e.g. '-may-2026') from a campaign id to get its dir slug."""
    import re
    return re.sub(r"-[a-z]{3}-\d{4}$", "", campaign_id)


def _build_target_url(campaign_id: str, week: int, channel: str) -> str:
    """Long URL the redirect resolves to, with channel-correct UTMs.

    channel ∈ {'sms', 'email'} — drives utm_source AND utm_medium so click
    attribution in GA4 / Meta matches where the click actually came from.
    """
    return (
        f"{SHORTLINK_HOST}/"
        f"?utm_source={channel}&utm_medium={channel}"
        f"&utm_campaign={campaign_id}&utm_content=week-{week}"
        f"{SHORTLINK_TARGET_FRAGMENT}"
    )


def _slug_for(short_slug: str, channel: str, week: int) -> str:
    """Per-channel slug, e.g. 'spring2026-sms-1' or 'spring2026-em-1'."""
    suffix = "sms" if channel == "sms" else "em"
    return f"{short_slug}-{suffix}-{week}"


@drip.command(name="sync-shortlinks")
def sync_shortlinks():
    """Generate / refresh shortlinks rows for every campaign with short_slug set.

    For each (campaign, week N) creates TWO slugs (one per channel):
      - {short_slug}-sms-{N} → target with utm_source=sms
      - {short_slug}-em-{N}  → target with utm_source=email
    Idempotent: existing slugs get target_url refreshed.
    """
    init_db()
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, short_slug, weeks FROM campaigns WHERE short_slug IS NOT NULL"
    ).fetchall()
    if not rows:
        click.secho("No campaigns with short_slug set.", fg="yellow")
        conn.close()
        return
    written = 0
    for r in rows:
        for week in range(1, r["weeks"] + 1):
            for channel in ("sms", "email"):
                slug = _slug_for(r["short_slug"], channel, week)
                target = _build_target_url(r["id"], week, channel)
                conn.execute(
                    """
                    INSERT INTO shortlinks (slug, target_url, campaign_id, week)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (slug) DO UPDATE
                      SET target_url  = EXCLUDED.target_url,
                          campaign_id = EXCLUDED.campaign_id,
                          week        = EXCLUDED.week
                    """,
                    (slug, target, r["id"], week),
                )
                written += 1
                click.echo(f"  {slug:30s} -> {target}")
    conn.commit()
    conn.close()
    click.secho(f"\n✅  {written} shortlink(s) synced", fg="green")


@drip.command(name="stop-rate")
@click.option("--campaign", default=None, help="Limit to one campaign id (default: all)")
@click.option("--since", default=None, help="ISO date/time, e.g. 2026-05-15 (default: 7 days ago)")
def stop_rate(campaign: str | None, since: str | None):
    """Show SMS opt-out (STOP) rate from inbound webhook events.

    Counts STOPs per matched campaign by joining sms_inbound_events.lead_id to
    campaign_sends. Unmatched STOPs (no matching lead) are reported separately.
    """
    init_db()
    conn = get_conn()

    where_camp = "AND cs.campaign_id = %s" if campaign else ""
    where_since = "AND e.received_at >= %s" if since else "AND e.received_at >= NOW() - INTERVAL '7 days'"
    params: list = []
    if campaign: params.append(campaign)
    if since:    params.append(since)

    rows = conn.execute(
        f"""
        SELECT cs.campaign_id,
               COUNT(DISTINCT cs.lead_id)                                        AS sms_recipients,
               COUNT(DISTINCT e.lead_id) FILTER (WHERE e.is_stop)                AS stops
          FROM campaign_sends cs
          LEFT JOIN sms_inbound_events e
                 ON e.lead_id = cs.lead_id
                 {where_since}
         WHERE cs.sms_sent_at IS NOT NULL
           {where_camp}
         GROUP BY cs.campaign_id
         ORDER BY cs.campaign_id
        """,
        tuple(params),
    ).fetchall()

    if not rows:
        click.secho("No SMS sends found in window.", fg="yellow")
    else:
        click.secho(f"\n  {'campaign':<35}  {'sent':>6}  {'stops':>6}  {'rate':>6}", bold=True)
        for r in rows:
            n = r["sms_recipients"] or 0
            s = r["stops"] or 0
            rate = (s / n * 100) if n else 0
            color = "red" if rate > 4 else ("yellow" if rate > 2 else "green")
            click.secho(f"  {r['campaign_id']:<35}  {n:>6,}  {s:>6,}  {rate:>5.2f}%", fg=color)

    unmatched = conn.execute(
        f"""SELECT COUNT(*) AS n
              FROM sms_inbound_events e
             WHERE e.is_stop AND e.lead_id IS NULL
               {where_since}""",
        tuple([since] if since else []),
    ).fetchone()
    if unmatched and unmatched["n"]:
        click.secho(f"\n  ⚠ {unmatched['n']} STOP(s) from phones with no matching lead", fg="yellow")

    conn.close()


@drip.command(name="test-send")
@click.option("--campaign", required=True, help="Campaign id (matches campaigns.id)")
@click.option("--week", required=True, type=int, help="Which drip step to send (1-indexed)")
@click.option("--channel", type=click.Choice(["email", "sms", "both"]), default=None,
              help="Override the step's channel (default: use what config.json says)")
@click.option("--lead-ids", default=None,
              help="Comma-separated lead ids to limit the send to (default: all test_lead=1 leads)")
def test_send(campaign: str, week: int, channel: str | None, lead_ids: str | None):
    """Send a single drip step to test_lead=1 leads without mutating campaign state.

    No campaign_sends rows written; current_week is NOT advanced. Safe to re-run.
    """
    init_db()
    conn = get_conn()

    camp = conn.execute(
        "SELECT id, name, weeks, short_slug FROM campaigns WHERE id = %s",
        (campaign,),
    ).fetchone()
    if not camp:
        raise click.ClickException(f"campaign id not found in campaigns table: {campaign}")

    try:
        campaign_dir, config, env = _load_campaign(camp["id"])
    except click.ClickException:
        campaign_dir, config, env = _load_campaign(_dir_slug(camp["id"]))

    steps = config.get("drip_steps") or []
    if week < 1 or week > len(steps):
        raise click.ClickException(f"week {week} out of range (config has {len(steps)} steps)")
    step = steps[week - 1]
    effective_channel = (channel or step.get("channel", "email")).lower()

    where = "test_lead = 1"
    params: tuple = ()
    if lead_ids:
        ids = tuple(int(x) for x in lead_ids.split(","))
        where = "test_lead = 1 AND id IN %s"
        params = (ids,)
    leads = conn.execute(
        f"""
        SELECT id, first_name, last_name, email, city, state, postal_code,
               phone_primary, phone_secondary
          FROM leads
         WHERE {where}
         ORDER BY id
        """,
        params,
    ).fetchall()

    if not leads:
        click.secho("No matching test leads.", fg="yellow")
        conn.close()
        return

    click.secho(
        f"\n  ▶ test-send: {camp['id']} week {week} ({effective_channel})  → {len(leads)} test lead(s)\n",
        fg="cyan",
    )

    ses = boto3.client("sesv2", region_name=os.getenv("AWS_REGION", "us-east-1"))
    sent = failed = 0

    for r in leads:
        vars = dict(
            first_name=r["first_name"] or "",
            last_name=r["last_name"] or "",
            email=r["email"],
            city=r["city"] or "",
            state=r["state"] or "",
            postal_code=r["postal_code"] or "",
            phone=_normalize_phone((r.get("phone_primary") or r.get("phone_secondary") or "")),
        )
        if camp.get("short_slug"):
            vars["short_url_sms"]   = f"{SHORTLINK_HOST}/{_slug_for(camp['short_slug'], 'sms',   week)}"
            vars["short_url_email"] = f"{SHORTLINK_HOST}/{_slug_for(camp['short_slug'], 'email', week)}"
        if config.get("referral_link"):
            ref_url = f"{_referral_url(r['id'])}?utm_content=week-{week}"
            vars["short_url_email"] = ref_url
            vars["short_url_sms"]   = ref_url
        subject, html, txt, sms = _render_step_templates(step, config, env, vars, camp["id"], r["id"], week)
        reply_to = config.get("reply_to", config["from_email"])
        headers = _prepare_headers(reply_to, r["id"], vars["email"] or "")

        email_ok = sms_ok = False
        errors: list[str] = []

        if effective_channel in ("email", "both") and vars["email"] and (html or txt):
            try:
                body = {}
                if txt: body["Text"] = {"Data": txt, "Charset": "UTF-8"}
                if html: body["Html"] = {"Data": html, "Charset": "UTF-8"}
                ses.send_email(
                    FromEmailAddress=f"{config['from_name']} <{config['from_email']}>",
                    Destination={"ToAddresses": [vars["email"]]},
                    ReplyToAddresses=[reply_to],
                    Content={"Simple": {
                        "Subject": {"Data": subject, "Charset": "UTF-8"},
                        "Body": body,
                        "Headers": headers,
                    }},
                    ConfigurationSetName=os.getenv("SES_CONFIG_SET", "apex-campaigns"),
                )
                email_ok = True
            except Exception as exc:
                errors.append(f"email: {exc}")

        if effective_channel in ("sms", "both") and sms and vars["phone"]:
            try:
                _send_sms(vars["phone"], sms)
                sms_ok = True
            except Exception as exc:
                errors.append(f"sms: {exc}")

        status_bits = []
        if email_ok: status_bits.append("email→" + (vars["email"] or ""))
        if sms_ok:   status_bits.append("sms→" + (vars["phone"] or ""))
        if errors:   status_bits.extend(errors)
        click.echo(f"    lead {r['id']:>5}  " + ("  ".join(status_bits) if status_bits else "skipped"))

        if email_ok or sms_ok: sent += 1
        else:                  failed += 1
        time.sleep(0.2)

    click.secho(f"\n  ✓ {sent} sent, {failed} failed (no DB state mutated)", fg="green" if failed == 0 else "yellow")
    conn.close()


if __name__ == "__main__":
    drip()
