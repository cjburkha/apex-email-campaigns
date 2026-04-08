#!/usr/bin/env python3
"""
report.py — Sync SES events then print campaign statistics.

Usage:
    python report.py                          # all campaigns
    python report.py --campaign window-inspection-apr-2026
    python report.py --no-sync               # skip SES sync
"""

import os
import click
import boto3
from dotenv import load_dotenv
from db import get_conn, init_db
from sync_events import _process_batch

load_dotenv()


def _pct(n, total) -> str:
    if not total:
        return "—"
    return f"{(n or 0) / total * 100:.1f}%"


@click.command()
@click.option("--campaign", default=None,
              help="Filter to a specific campaign ID")
@click.option("--no-sync", is_flag=True,
              help="Skip syncing SES events before reporting")
def report(campaign: str, no_sync: bool):
    """Sync SES events then show send/delivery/engagement stats per campaign."""
    init_db()
    conn = get_conn()

    if not no_sync:
        queue_url = os.getenv("SES_EVENTS_QUEUE_URL")
        if queue_url:
            sqs = boto3.client("sqs", region_name=os.getenv("AWS_REGION", "us-east-1"))
            n = _process_batch(sqs, queue_url, conn)
            if n:
                click.secho(f"  ↻  Synced {n} event(s) from SES\n", fg="cyan")
        else:
            click.secho("  ⚠  SES_EVENTS_QUEUE_URL not set, skipping sync\n", fg="yellow")
    where = "WHERE cs.campaign_id = %s" if campaign else ""
    params = (campaign,) if campaign else ()

    rows = conn.execute(f"""
        SELECT
            c.id,
            c.name,
            c.from_email,
            c.created_at,
            COUNT(*)                                                 AS total,
            COUNT(*) FILTER (WHERE cs.status != 'queued')            AS sent,
            COUNT(*) FILTER (WHERE cs.status = 'delivered')          AS delivered,
            COUNT(*) FILTER (WHERE cs.opened_at  IS NOT NULL)        AS opened,
            COUNT(*) FILTER (WHERE cs.clicked_at IS NOT NULL)        AS clicked,
            COUNT(*) FILTER (WHERE cs.status = 'bounced')            AS bounced,
            COUNT(*) FILTER (WHERE cs.status = 'complained')         AS complained,
            COUNT(*) FILTER (WHERE cs.status = 'failed')             AS failed,
            COUNT(*) FILTER (WHERE cs.status = 'queued')             AS queued
        FROM campaigns c
        JOIN campaign_sends cs ON cs.campaign_id = c.id
        {where}
        GROUP BY c.id
        ORDER BY c.created_at DESC
    """, params).fetchall()

    if not rows:
        click.echo("No campaigns found.")
        conn.close()
        return

    for row in rows:
        sent = row["sent"] or 0
        w    = 60
        click.echo(f"\n{'─' * w}")
        click.echo(f"  Campaign  : {row['name']}")
        click.echo(f"  ID        : {row['id']}")
        click.echo(f"  From      : {row['from_email']}")
        click.echo(f"  Created   : {row['created_at']}")
        click.echo(f"{'─' * w}")
        click.echo(f"  Total     : {row['total']}")
        click.echo(f"  Sent      : {sent}")
        click.echo(f"  Delivered : {row['delivered']}  ({_pct(row['delivered'], sent)})")
        click.secho(
            f"  Opened    : {row['opened']}  ({_pct(row['opened'], sent)})",
            fg="green" if (row["opened"] or 0) > 0 else None
        )
        click.secho(
            f"  Clicked   : {row['clicked']}  ({_pct(row['clicked'], sent)})",
            fg="green" if (row["clicked"] or 0) > 0 else None
        )
        click.secho(
            f"  Bounced   : {row['bounced']}  ({_pct(row['bounced'], sent)})",
            fg="red" if (row["bounced"] or 0) > 0 else None
        )
        click.echo(f"  Complained: {row['complained']}")
        click.echo(f"  Failed    : {row['failed']}")
        click.echo(f"  Queued    : {row['queued']}  (not yet sent)")

    click.echo(f"\n{'─' * 60}\n")
    conn.close()


if __name__ == "__main__":
    report()
