#!/usr/bin/env python3
"""
send.py — Send a campaign to recipients from a CSV.

Usage:
    python send.py --campaign window-inspection --csv data/leads.csv
    python send.py --campaign window-inspection --csv data/leads.csv --dry-run
    python send.py --campaign window-inspection --csv data/leads.csv --limit 5

CSV required columns : first_name, email
CSV optional columns : last_name, city, (any extra become template variables)
"""

import json
import os
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import boto3
import click
import pandas as pd
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
@click.option("--csv", "csv_path", required=True, type=click.Path(exists=True),
              help="CSV file with recipient list")
@click.option("--dry-run", is_flag=True,
              help="Render & preview every email without sending")
@click.option("--rate", default=14, type=float, show_default=True,
              help="Emails per second (14 = SES production max, 1 = sandbox)")
@click.option("--limit", default=None, type=int,
              help="Send to first N recipients only (handy for test batches)")
def send(campaign: str, csv_path: str, dry_run: bool, rate: float, limit: int):
    """Send a campaign to all queued recipients in a CSV."""
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

    # ── load + validate CSV ───────────────────────────────────────────────────
    df = pd.read_csv(csv_path)
    df.columns = df.columns.str.lower().str.strip()
    missing = {"first_name", "email"} - set(df.columns)
    if missing:
        raise click.ClickException(f"CSV missing required columns: {missing}")
    if limit:
        df = df.head(limit)

    # ── upsert campaign + recipients into DB (idempotent) ────────────────────
    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO campaigns "
        "(id, name, subject, from_email, from_name) VALUES (?,?,?,?,?)",
        (campaign_id, config["name"], config["subject"],
         config["from_email"], config["from_name"])
    )
    for row in df.to_dict("records"):
        extra = {k: v for k, v in row.items()
                 if k not in ("first_name", "last_name", "email", "city")}
        conn.execute(
            "INSERT OR IGNORE INTO recipients "
            "(campaign_id, first_name, last_name, email, city, extra_vars) "
            "VALUES (?,?,?,?,?,?)",
            (
                campaign_id,
                str(row.get("first_name", "")).strip(),
                str(row.get("last_name", "")).strip() if "last_name" in row else None,
                str(row["email"]).strip().lower(),
                str(row.get("city", "")).strip() if "city" in row else None,
                json.dumps(extra) if extra else None,
            )
        )
    conn.commit()

    # ── fetch only recipients not yet sent ───────────────────────────────────
    pending = conn.execute(
        "SELECT * FROM recipients WHERE campaign_id=? AND status='queued'",
        (campaign_id,)
    ).fetchall()

    total = len(pending)
    click.echo(f"\n📧  {config['name']}")
    click.echo(f"    From  : {config['from_name']} <{config['from_email']}>")
    click.echo(f"    Queue : {total} recipients to send")
    click.echo(f"    Mode  : {'⚠️  DRY RUN — nothing will be sent' if dry_run else '🚀 LIVE'}\n")

    if total == 0:
        click.secho("✅  Nothing to send — all recipients already processed.", fg="green")
        conn.close()
        return

    if not dry_run:
        click.confirm(f"Send {total} emails now?", abort=True)

    # ── SES client + config ───────────────────────────────────────────────────
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
    for i, r in enumerate(pending, 1):
        vars = dict(
            first_name=r["first_name"],
            last_name=r["last_name"] or "",
            email=r["email"],
            city=r["city"] or "",
        )
        if r["extra_vars"]:
            vars.update(json.loads(r["extra_vars"]))

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
                "UPDATE recipients "
                "SET status='sent', message_id=?, sent_at=datetime('now') "
                "WHERE id=?",
                (resp["MessageId"], r["id"])
            )
            conn.commit()
            sent += 1
            click.secho(f"  [{i}/{total}] ✓  {r['email']}", fg="green")

        except Exception as exc:
            conn.execute(
                "UPDATE recipients SET status='failed', failed_reason=? WHERE id=?",
                (str(exc), r["id"])
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
