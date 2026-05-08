#!/usr/bin/env python3
"""
report.py — Print campaign send and engagement statistics.

Usage:
    python report.py                          # all campaigns
    python report.py --campaign window-inspection-apr-2026
"""

import click
from db import get_conn, init_db


def _pct(n, total) -> str:
    if not total:
        return "—"
    return f"{(n or 0) / total * 100:.1f}%"


@click.command()
@click.option("--campaign", default=None,
              help="Filter to a specific campaign ID")
def report(campaign: str):
    """Show send/delivery/engagement stats per campaign."""
    init_db()
    conn  = get_conn()
    where = "WHERE r.campaign_id = ?" if campaign else ""
    params = (campaign,) if campaign else ()

    rows = conn.execute(f"""
        SELECT
            c.id,
            c.name,
            c.from_email,
            c.created_at,
            COUNT(*)                                    AS total,
            SUM(r.status != 'queued')                   AS sent,
            SUM(r.status = 'delivered')                 AS delivered,
            SUM(r.opened_at  IS NOT NULL)               AS opened,
            SUM(r.clicked_at IS NOT NULL)               AS clicked,
            SUM(r.status = 'bounced')                   AS bounced,
            SUM(r.status = 'complained')                AS complained,
            SUM(r.status = 'failed')                    AS failed,
            SUM(r.status = 'queued')                    AS queued
        FROM campaigns c
        JOIN recipients r ON r.campaign_id = c.id
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
