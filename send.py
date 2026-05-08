#!/usr/bin/env python3
"""
send.py — Send a campaign to leads selected by a SQL query.

Usage:
    python send.py --campaign window-inspection --query "SELECT * FROM leads WHERE state='WI'"
    python send.py --campaign window-inspection --query "SELECT * FROM leads WHERE state='WI'" --dry-run
    python send.py --campaign window-inspection --query "SELECT * FROM leads WHERE state='WI'" --limit 5

The query must SELECT from the leads table (or any query that returns leads columns).
Required lead columns : first_name, email
Useful lead columns   : last_name, city  (used as template variables)

Each (campaign, lead) pair is tracked in campaign_sends. Running the same command
twice will skip leads already queued/sent — safe to re-run after a partial failure.
"""

import hashlib
import hmac
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import boto3
import click
from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader, Template

from db import get_conn, init_db

load_dotenv()


# ── helpers ───────────────────────────────────────────────────────────────────

def _add_utm(html: str, utm: dict) -> str:
    """Append UTM params to every http(s) href in the HTML."""
    def _rewrite(match):
        url = match.group(1)
        parsed = urlparse(url)
        qs = parse_qs(parsed.query, keep_blank_values=True)
        for k, v in utm.items():
            qs[k] = [v]
        new_qs = urlencode({k: v[0] for k, v in qs.items()})
        return f'href="{urlunparse(parsed._replace(query=new_qs))}"'
    return re.sub(r'href="(https?://[^"]+)"', _rewrite, html)

def _normalize_phone(phone: str) -> str:
    if not phone:
        return ""
    phone = re.sub(r"[^0-9+]", "", phone)
    if phone.startswith("+"):
        return phone
    if len(phone) == 10:
        return f"+1{phone}"
    if len(phone) == 11 and phone.startswith("1"):
        return f"+{phone}"
    return phone


def _make_unsubscribe_token(lead_id: int, email: str) -> str:
    secret = os.getenv("UNSUBSCRIBE_SECRET", "change-me").encode("utf-8")
    return hmac.new(secret, f"{lead_id}:{email}".encode("utf-8"), hashlib.sha256).hexdigest()


def _unsubscribe_url(lead_id: int, token: str) -> str:
    base = os.getenv("UNSUBSCRIBE_BASE_URL", "https://windowsbyburkhardt.com/unsubscribe")
    return f"{base}?id={lead_id}&t={token}"


def _send_sms(pinpoint, application_id, phone_number: str, message: str):
    if not application_id:
        raise RuntimeError("PINPOINT_APPLICATION_ID is not set in .env")
    body = {"Body": message, "MessageType": "TRANSACTIONAL"}
    sender_id = os.getenv("SMS_SENDER_ID")
    if sender_id:
        body["SenderId"] = sender_id
    origination_number = os.getenv("SMS_ORIGINATING_NUMBER")
    if origination_number:
        body["OriginationNumber"] = origination_number

    response = pinpoint.send_messages(
        ApplicationId=application_id,
        MessageRequest={
            "Addresses": {
                phone_number: {"ChannelType": "SMS"},
            },
            "MessageConfiguration": {
                "SMSMessage": body,
            },
        },
    )
    result = response["MessageResponse"]["Result"][phone_number]
    return result.get("MessageId"), result.get("Status")

# ── CLI ───────────────────────────────────────────────────────────────────────

@click.command()
@click.option("--campaign", required=True,
              help="Campaign directory name under campaigns/")
@click.option("--query", "sql_query", default=None,
              help="SQL SELECT query against the leads table (prompted if omitted)")
@click.option("--dry-run", is_flag=True,
              help="Render & preview every email without sending")
@click.option("--rate", default=14, type=float, show_default=True,
              help="Emails per second (14 = SES production max, 1 = sandbox)")
@click.option("--limit", default=None, type=int,
              help="Cap the number of leads processed (handy for test batches)")
def send(campaign: str, sql_query: str, dry_run: bool, rate: float, limit: int):
    """Send a campaign to leads selected by a SQL query."""
    init_db()

    # ── load campaign config ───────────────────────────────────────────────────
    campaign_dir = Path("campaigns") / campaign
    config_path  = campaign_dir / "config.json"
    if not config_path.exists():
        raise click.ClickException(f"config.json not found: {config_path}")

    config      = json.loads(config_path.read_text())
    campaign_id = config.get("id", campaign)

    # ── load Jinja2 templates ─────────────────────────────────────────────────
    env             = Environment(loader=FileSystemLoader(str(campaign_dir)))
    html_tmpl       = env.get_template("template.html")
    txt_tmpl        = env.get_template("template.txt")
    sms_template    = campaign_dir / "template.sms.txt"
    sms_tmpl        = env.get_template("template.sms.txt") if sms_template.exists() else None

    # ── prompt for query if not provided ─────────────────────────────────────
    if not sql_query:
        click.echo("Enter a SQL query to select leads (e.g. SELECT * FROM leads WHERE state='WI' LIMIT 50):")
        sql_query = click.prompt("Query")

    # ── run lead query ────────────────────────────────────────────────────────
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

    # Validate required columns
    lead_keys = leads[0].keys()
    missing = {"first_name", "email"} - set(lead_keys)
    if missing:
        raise click.ClickException(f"Query results missing required columns: {missing}")

    # ── upsert campaign into DB ───────────────────────────────────────────────
    conn.execute(
        "INSERT INTO campaigns "
        "(id, name, subject, from_email, from_name) VALUES (%s,%s,%s,%s,%s) "
        "ON CONFLICT (id) DO NOTHING",
        (campaign_id, config["name"], config["subject"],
         config["from_email"], config["from_name"])
    )

    # ── enroll leads into campaign_sends (idempotent) ────────────────────────
    new_count = 0
    for lead in leads:
        if lead.get("unsubscribed_at") is not None:
            continue
        result = conn.execute(
            "INSERT INTO campaign_sends (campaign_id, lead_id) VALUES (%s,%s) "
            "ON CONFLICT (campaign_id, lead_id) DO NOTHING",
            (campaign_id, lead["id"])
        )
        if result.rowcount:
            new_count += 1
    conn.commit()

    already = len(leads) - new_count
    if already:
        click.echo(f"  ℹ  {already} lead(s) already enrolled in this campaign — skipped")

    # ── fetch only queued sends ───────────────────────────────────────────────
    pending_rows = conn.execute(
        """
        SELECT cs.id as send_id, cs.lead_id,
               l.first_name, l.last_name, l.email, l.city,
               l.state, l.postal_code, l.phone_primary, l.phone_secondary
        FROM campaign_sends cs
        JOIN leads l ON l.id = cs.lead_id
        WHERE cs.campaign_id = %s AND cs.status = 'queued' AND l.unsubscribed_at IS NULL
        """,
        (campaign_id,)
    ).fetchall()

    total = len(pending_rows)
    click.echo(f"\n📧  {config['name']}")
    click.echo(f"    From  : {config['from_name']} <{config['from_email']}>")
    click.echo(f"    Query : {sql_query[:80]}{'…' if len(sql_query) > 80 else ''}")
    click.echo(f"    Queue : {total} recipient(s) to send")
    click.echo(f"    Mode  : {'⚠️  DRY RUN — nothing will be sent' if dry_run else '🚀 LIVE'}\n")

    if total == 0:
        click.secho("✅  Nothing to send — all matched leads already processed for this campaign.", fg="green")
        conn.close()
        return

    if not dry_run:
        click.confirm(f"Send {total} emails now?", abort=True)

    # ── SES + Pinpoint clients ─────────────────────────────────────────────────
    ses                  = boto3.client("sesv2", region_name=os.getenv("AWS_REGION", "us-east-1"))
    pinpoint             = boto3.client("pinpoint", region_name=os.getenv("AWS_REGION", "us-east-1")) if sms_tmpl else None
    pinpoint_app_id      = os.getenv("PINPOINT_APPLICATION_ID")
    config_set           = os.getenv("SES_CONFIG_SET", "apex-campaigns")
    utm_base             = {
        "utm_source":   config.get("utm_source",   "email"),
        "utm_medium":   config.get("utm_medium",   "email"),
        "utm_campaign": config.get("utm_campaign", campaign_id),
    }
    interval = 1.0 / rate
    sent = failed = 0

    # ── send loop ─────────────────────────────────────────────────────────────
    for i, r in enumerate(pending_rows, 1):
        vars = dict(
            first_name=r["first_name"] or "",
            last_name=r["last_name"] or "",
            email=r["email"],
            city=r["city"] or "",
            state=r["state"] or "",
            postal_code=r["postal_code"] or "",
            phone=(r.get("phone_primary") or r.get("phone_secondary") or ""),
        )

        subject = Template(config["subject"]).render(**vars)
        html    = _add_utm(html_tmpl.render(**vars), utm_base)
        txt     = txt_tmpl.render(**vars)
        phone   = _normalize_phone(vars["phone"])
        sms_body = sms_tmpl.render(**vars) if sms_tmpl and phone else None
        unsubscribe_tok = _make_unsubscribe_token(r["lead_id"], vars["email"])
        unsubscribe_url = _unsubscribe_url(r["lead_id"], unsubscribe_tok)
        reply_to = config.get("reply_to", config["from_email"])

        if dry_run:
            channels = ["email" if vars["email"] else None, "sms" if sms_body else None]
            channels = ", ".join([c for c in channels if c]) or "none"
            click.echo(f"  [{i}/{total}] → {r.get('email') or phone:40s}  {subject}  ({channels})")
            continue

        headers = [
            {
                "Name": "List-Unsubscribe",
                "Value": f"<mailto:{reply_to}?subject=unsubscribe>, <{unsubscribe_url}>",
            },
            {
                "Name": "List-Unsubscribe-Post",
                "Value": "List-Unsubscribe=One-Click",
            },
        ]

        email_sent = False
        sms_sent = False
        sms_message_id = None
        sms_status = None
        errors = []
        message_id = None

        if vars["email"]:
            try:
                resp = ses.send_email(
                    FromEmailAddress=f"{config['from_name']} <{config['from_email']}>",
                    Destination={"ToAddresses": [vars["email"]]},
                    ReplyToAddresses=[reply_to],
                    Content={
                        "Simple": {
                            "Subject": {"Data": subject, "Charset": "UTF-8"},
                            "Body": {
                                "Text": {"Data": txt,  "Charset": "UTF-8"},
                                "Html": {"Data": html, "Charset": "UTF-8"},
                            },
                            "Headers": headers,
                        }
                    },
                    ConfigurationSetName=config_set,
                )
                email_sent = True
                message_id = resp["MessageId"]
            except Exception as exc:
                errors.append(f"email: {exc}")

        if sms_body:
            try:
                sms_message_id, sms_status = _send_sms(pinpoint, pinpoint_app_id, phone, sms_body)
                sms_sent = True
            except Exception as exc:
                errors.append(f"sms: {exc}")

        if not vars["email"] and not sms_body:
            errors.append("no email or SMS contact available")

        if not email_sent and not sms_sent:
            conn.execute(
                "UPDATE campaign_sends SET status='failed', failed_reason=%s WHERE id=%s",
                ("; ".join(errors), r["send_id"])
            )
            conn.commit()
            failed += 1
            click.secho(f"  [{i}/{total}] ✗  {r.get('email') or phone} — {'; '.join(errors)}", fg="red", err=True)
        else:
            update_sql = ["status='sent'", "sent_at=NOW()", "message_id=%s"]
            update_params = [message_id]
            if sms_message_id:
                update_sql.append("sms_message_id=%s")
                update_sql.append("sms_sent_at=NOW()")
                update_sql.append("sms_status=%s")
                update_params.extend([sms_message_id, sms_status])
            update_params.append(r["send_id"])
            conn.execute(
                f"UPDATE campaign_sends SET {', '.join(update_sql)} WHERE id=%s",
                tuple(update_params)
            )
            conn.commit()
            sent += 1
            contact_summary = ", ".join(c for c in ["email" if email_sent else None, "sms" if sms_sent else None] if c)
            click.secho(f"  [{i}/{total}] ✓  {r.get('email') or phone}  ({contact_summary})", fg="green")

        time.sleep(interval)

    conn.close()
    color = "green" if failed == 0 else "yellow"
    click.secho(
        f"\n{'✅' if not failed else '⚠️ '} Sent {sent}  |  Failed {failed}  |  Total {total}\n",
        fg=color
    )


if __name__ == "__main__":
    send()
