#!/usr/bin/env python3
"""
sync_events.py — Poll SQS for SES delivery events and update recipient statuses.

SES sends events (delivery, bounce, open, click, etc.) to SNS, which forwards
them to an SQS queue.  This script drains that queue and updates the DB.

Safe to run repeatedly — each processed message is deleted from SQS.

Usage:
    python sync_events.py              # single pass
    python sync_events.py --watch      # loop every 30 seconds until Ctrl-C
"""

import json
import os
import time

import boto3
import click
from dotenv import load_dotenv

from db import get_conn, init_db

load_dotenv()

# Maps SES eventType → (new status or None, timestamp column or None)
# "None" for status means "don't change status, just update the timestamp"
EVENT_MAP = {
    "Send":             (None,          None),           # already marked 'sent' by send.py
    "Delivery":         ("delivered",   "delivered_at"),
    "Bounce":           ("bounced",     "bounced_at"),
    "Complaint":        ("complained",  None),
    "Open":             (None,          "opened_at"),    # keep highest status, just stamp time
    "Click":            (None,          "clicked_at"),
    "Reject":           ("failed",      None),
    "RenderingFailure": ("failed",      None),
}

# Higher index = higher priority; never downgrade status
STATUS_RANK = [
    "queued", "sent", "delivered", "opened", "clicked",
    "bounced", "complained", "failed",
]


def _rank(status: str) -> int:
    try:
        return STATUS_RANK.index(status)
    except ValueError:
        return 0


def _process_batch(sqs, queue_url: str, conn) -> int:
    """Drain one batch of up to 10 messages.  Returns number of DB rows updated."""
    resp     = sqs.receive_message(
        QueueUrl=queue_url,
        MaxNumberOfMessages=10,
        WaitTimeSeconds=5,          # long-poll — reduces empty receives
    )
    messages = resp.get("Messages", [])
    updated  = 0

    for msg in messages:
        try:
            body = json.loads(msg["Body"])

            # Handle both raw-delivery (body IS the SES event) and
            # SNS-envelope delivery (body has a "Message" string field)
            if "Message" in body and "Type" in body:
                event = json.loads(body["Message"])
            else:
                event = body

            event_type = event.get("eventType") or event.get("notificationType")
            message_id = (event.get("mail") or {}).get("messageId")

            if not message_id or event_type not in EVENT_MAP:
                sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=msg["ReceiptHandle"])
                continue

            new_status, ts_col = EVENT_MAP[event_type]

            # Look up the send record in campaign_sends by SES message id
            row = conn.execute(
                "SELECT id, status FROM campaign_sends WHERE message_id = %s",
                (message_id,)
            ).fetchone()

            if not row:
                sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=msg["ReceiptHandle"])
                continue

            sets, params = [], []

            # Only upgrade status — never downgrade (e.g. a late 'sent' event
            # must not overwrite a 'delivered' status)
            if new_status and _rank(new_status) > _rank(row["status"]):
                sets.append("status=%s")
                params.append(new_status)

            # Stamp timestamp only on first occurrence (COALESCE keeps first)
            if ts_col:
                sets.append(f'{ts_col}=COALESCE({ts_col}, NOW())')

            if event_type == "Bounce":
                bounce_type = (event.get("bounce") or {}).get("bounceType")
                if bounce_type:
                    sets.append("bounce_type=%s")
                    params.append(bounce_type)

            if sets:
                params.append(row["id"])
                conn.execute(
                    f'UPDATE campaign_sends SET {", ".join(sets)} WHERE id=%s', params
                )
                conn.commit()
                updated += 1
                click.echo(f"  {event_type:<18} {message_id[:24]}…")

        except Exception as exc:
            click.secho(f"  ⚠  Error processing message: {exc}", fg="yellow", err=True)

        sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=msg["ReceiptHandle"])

    return updated


@click.command()
@click.option("--watch", is_flag=True,
              help="Poll continuously until Ctrl-C")
@click.option("--interval", default=30, show_default=True,
              help="Seconds between polls in watch mode")
def sync_events(watch: bool, interval: int):
    """Pull SES delivery events from SQS and update recipient statuses."""
    init_db()

    queue_url = os.getenv("SES_EVENTS_QUEUE_URL")
    if not queue_url:
        raise click.ClickException("SES_EVENTS_QUEUE_URL not set in .env")

    sqs  = boto3.client("sqs", region_name=os.getenv("AWS_REGION", "us-east-1"))
    conn = get_conn()

    if watch:
        click.echo(f"👀  Watching SQS every {interval}s — Ctrl-C to stop\n")
        while True:
            n = _process_batch(sqs, queue_url, conn)
            if n:
                click.secho(f"  → Updated {n} recipient(s)\n", fg="green")
            time.sleep(interval)
    else:
        n = _process_batch(sqs, queue_url, conn)
        click.secho(f"\n✅  Updated {n} recipient(s)\n", fg="green")

    conn.close()


if __name__ == "__main__":
    sync_events()
