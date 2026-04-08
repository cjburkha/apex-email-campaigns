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
    env       = Environment(loader=FileSystemLoader(str(campaign_dir)))
    html_tmpl = env.get_template("template.html")
    txt_tmpl  = env.get_template("template.txt")

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
               l.state, l.postal_code, l.phone_primary
        FROM campaign_sends cs
        JOIN leads l ON l.id = cs.lead_id
        WHERE cs.campaign_id = %s AND cs.status = 'queued'
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

    # ── SES client ────────────────────────────────────────────────────────────
    ses        = boto3.client("sesv2", region_name=os.getenv("AWS_REGION", "us-east-1"))
    config_set = os.getenv("SES_CONFIG_SET", "apex-campaigns")
    utm_base   = {
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
            phone=r["phone_primary"] or "",
        )

        subject = Template(config["subject"]).render(**vars)
        html    = _add_utm(html_tmpl.render(**vars), utm_base)
        txt     = txt_tmpl.render(**vars)

        if dry_run:
            click.echo(f"  [{i}/{total}] → {r['email']:40s}  {subject}")
            continue

        try:
            resp = ses.send_email(
                FromEmailAddress=f"{config['from_name']} <{config['from_email']}>",
                Destination={"ToAddresses": [r["email"]]},
                ReplyToAddresses=[config.get("reply_to", config["from_email"])],
                Content={
                    "Simple": {
                        "Subject": {"Data": subject, "Charset": "UTF-8"},
                        "Body": {
                            "Text": {"Data": txt,  "Charset": "UTF-8"},
                            "Html": {"Data": html, "Charset": "UTF-8"},
                        },
                        "Headers": [
                            {
                                "Name": "List-Unsubscribe",
                                "Value": f"<mailto:{config.get('reply_to', config['from_email'])}?subject=unsubscribe>"
                            },
                            {
                                "Name": "List-Unsubscribe-Post",
                                "Value": "List-Unsubscribe=One-Click"
                            },
                        ],
                    }
                },
                ConfigurationSetName=config_set,
            )
            conn.execute(
                "UPDATE campaign_sends "
                "SET status='sent', message_id=%s, sent_at=NOW() "
                "WHERE id=%s",
                (resp["MessageId"], r["send_id"])
            )
            conn.commit()
            sent += 1
            click.secho(f"  [{i}/{total}] ✓  {r['email']}", fg="green")

        except Exception as exc:
            conn.execute(
                "UPDATE campaign_sends SET status='failed', failed_reason=%s WHERE id=%s",
                (str(exc), r["send_id"])
            )
            conn.commit()
            failed += 1
            click.secho(f"  [{i}/{total}] ✗  {r['email']} — {exc}", fg="red", err=True)

        time.sleep(interval)

    conn.close()
    color = "green" if failed == 0 else "yellow"
    click.secho(
        f"\n{'✅' if not failed else '⚠️ '} Sent {sent}  |  Failed {failed}  |  Total {total}\n",
        fg=color
    )


if __name__ == "__main__":
    send()
