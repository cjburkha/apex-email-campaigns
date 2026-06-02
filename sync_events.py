#!/usr/bin/env python3
"""
sync_events.py — Drain SES events from SQS and apply lead-level suppression.

SES publishes events (delivery, bounce, complaint, open, click) for the
`apex-campaigns` configuration set to SNS → the `apex-campaigns-events` SQS
queue.  This script drains that queue and updates the DB.

Matching is by **recipient email address** — every SES event names the
recipient, so no message_id is required (the cohort sender never stored one).

Applied updates:
  • Bounce    → leads.bounced_at + leads.bounce_type   (excludes from future sends)
  • Complaint → leads.unsubscribed_at                  (opt-out)
  • Bounce/Complaint also stamp the lead's campaign_sends rows for reporting.

Opens and clicks are tracked separately by the website pixel / shortlink
endpoints (campaign_open_events / shortlinks tables), so they are ignored here.

Usage:
    python sync_events.py                 # single drain pass
    python sync_events.py --watch         # loop until Ctrl-C
    python sync_events.py --dry-run       # show what would change, delete nothing
"""

import json
import os
import time

import boto3
import click
from dotenv import load_dotenv

from db import get_conn, init_db

load_dotenv()

_QUEUE_DEFAULT = "https://sqs.us-east-1.amazonaws.com/669143131098/apex-campaigns-events"


def _extract(event):
    """Return (event_type, [recipient_emails], bounce_type|None, timestamp|None)."""
    etype = event.get("eventType") or event.get("notificationType")
    mail = event.get("mail") or {}
    ts = mail.get("timestamp")

    if etype == "Bounce":
        b = event.get("bounce") or {}
        emails = [r.get("emailAddress") for r in b.get("bouncedRecipients", [])]
        return etype, emails, b.get("bounceType"), b.get("timestamp") or ts
    if etype == "Complaint":
        c = event.get("complaint") or {}
        emails = [r.get("emailAddress") for r in c.get("complainedRecipients", [])]
        return etype, emails, None, c.get("timestamp") or ts

    # Delivery/Send/Open/Click — recipient(s) live on mail.destination
    return etype, mail.get("destination", []), None, ts


def _apply(conn, etype, email, bounce_type, ts):
    """Apply one (event, email) to the DB. Returns a short label if it changed a lead."""
    email = (email or "").strip().lower()
    if not email:
        return None
    when = "COALESCE(%s::timestamptz, NOW())"

    if etype == "Bounce":
        # Lead-level suppression — the address is bad for every campaign.
        cur = conn.execute(
            f"UPDATE leads SET bounced_at = COALESCE(bounced_at, {when}), "
            f"bounce_type = COALESCE(bounce_type, %s) "
            f"WHERE lower(email) = %s AND bounced_at IS NULL",
            (ts, bounce_type, email),
        )
        changed = cur.rowcount
        # Reflect on this lead's campaign_sends rows for reporting.
        conn.execute(
            f"UPDATE campaign_sends cs SET bounced_at = COALESCE(cs.bounced_at, {when}), "
            f"status = 'bounced', bounce_type = COALESCE(cs.bounce_type, %s) "
            f"FROM leads l WHERE cs.lead_id = l.id AND lower(l.email) = %s "
            f"AND cs.status NOT IN ('bounced', 'complained')",
            (ts, bounce_type, email),
        )
        return f"bounce({bounce_type})" if changed else None

    if etype == "Complaint":
        cur = conn.execute(
            f"UPDATE leads SET unsubscribed_at = COALESCE(unsubscribed_at, {when}) "
            f"WHERE lower(email) = %s AND unsubscribed_at IS NULL",
            (ts, email),
        )
        changed = cur.rowcount
        conn.execute(
            "UPDATE campaign_sends cs SET status = 'complained' "
            "FROM leads l WHERE cs.lead_id = l.id AND lower(l.email) = %s "
            "AND cs.status NOT IN ('complained')",
            (email,),
        )
        return "complaint" if changed else None

    return None  # Delivery/Send/Open/Click — no-op here


def _process_batch(sqs, queue_url, conn, dry_run):
    """Drain one batch of up to 10 messages. Returns (seen, changed)."""
    resp = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=5)
    messages = resp.get("Messages", [])
    seen = changed = 0

    for msg in messages:
        try:
            body = json.loads(msg["Body"])
            event = json.loads(body["Message"]) if ("Message" in body and "Type" in body) else body
            etype, emails, bounce_type, ts = _extract(event)
            seen += 1

            for email in emails:
                label = _apply(conn, etype, email, bounce_type, ts)
                if label:
                    changed += 1
                    click.echo(f"  {label:<18} {email}")
            if not dry_run:
                conn.commit()
        except Exception as exc:
            conn._conn.rollback()
            click.secho(f"  ⚠  error: {exc}", fg="yellow", err=True)
            continue  # leave message in queue for retry

        if not dry_run:
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=msg["ReceiptHandle"])

    return seen, changed


@click.command()
@click.option("--watch", is_flag=True, help="Poll continuously until Ctrl-C")
@click.option("--interval", default=30, show_default=True, help="Seconds between polls in watch mode")
@click.option("--dry-run", is_flag=True, help="Show changes but delete nothing and commit nothing")
@click.option("--max-batches", default=0, help="Stop after N batches (0 = until queue drains)")
def sync_events(watch, interval, dry_run, max_batches):
    """Drain SES events from SQS and suppress bounced / complained leads (match by email)."""
    init_db()
    queue_url = os.getenv("SES_EVENTS_QUEUE_URL", _QUEUE_DEFAULT)
    sqs = boto3.client("sqs", region_name=os.getenv("AWS_REGION", "us-east-1"))
    conn = get_conn()

    if dry_run:
        click.secho("DRY-RUN — no deletes, no commits\n", fg="yellow")

    total_seen = total_changed = batches = empty = 0
    try:
        while True:
            seen, changed = _process_batch(sqs, queue_url, conn, dry_run)
            total_seen += seen
            total_changed += changed
            batches += 1
            empty = empty + 1 if seen == 0 else 0

            if watch:
                if changed:
                    click.secho(f"  → suppressed {changed} lead(s)\n", fg="green")
                time.sleep(interval)
            else:
                if empty >= 2:  # queue drained
                    break
            if max_batches and batches >= max_batches:
                break
    except KeyboardInterrupt:
        pass
    finally:
        conn.close()

    click.secho(
        f"\n✅  processed {total_seen} events, suppressed {total_changed} lead(s)"
        f"{' (dry-run)' if dry_run else ''}\n",
        fg="green",
    )


if __name__ == "__main__":
    sync_events()
