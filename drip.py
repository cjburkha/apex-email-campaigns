#!/usr/bin/env python3
"""
drip.py — Automated drip campaign runner.

Usage:
    python drip.py enroll --campaign window-inspection --query "SELECT * FROM leads WHERE state='WI'"
    python drip.py run --campaign window-inspection

This creates drip enrollment rows and sends scheduled drip steps from campaign config.
"""

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import boto3
import click
from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader, Template

from db import get_conn, init_db
from send import _add_utm, _normalize_phone, _make_unsubscribe_token, _unsubscribe_url, _send_sms

load_dotenv()


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


def _render_step_templates(step: dict, config: dict, env: Environment, vars: dict):
    subject = Template(step.get("subject", config["subject"]).strip()).render(**vars)
    html = None
    txt = None
    sms = None

    html_file = step.get("template_html") or config.get("template_html") or "template.html"
    txt_file = step.get("template_txt") or config.get("template_txt") or "template.txt"
    sms_file = step.get("template_sms") or config.get("sms_template")

    if html_file:
        html = _add_utm(env.get_template(html_file).render(**vars), {
            "utm_source": config.get("utm_source", "email"),
            "utm_medium": config.get("utm_medium", "email"),
            "utm_campaign": config.get("utm_campaign", "drip"),
        })
    if txt_file:
        txt = env.get_template(txt_file).render(**vars)
    if sms_file:
        sms = env.get_template(sms_file).render(**vars)

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

        subject, html, txt, sms = _render_step_templates(step, config, env, vars)
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


if __name__ == "__main__":
    drip()
