#!/usr/bin/env python3
"""
scripts/migrate-to-pg.py — One-time migration from SQLite to PostgreSQL.

Reads the existing db/campaigns.db and inserts all rows into the PostgreSQL
database specified by DATABASE_URL in .env.

Run this once, after:
  1. bash scripts/create-apex-db.sh   (creates the 'apex' DB on RDS)
  2. python -c 'from db import init_db; init_db()'  (creates schema)

Usage:
    python scripts/migrate-to-pg.py             # live migration
    python scripts/migrate-to-pg.py --dry-run   # preview counts only
"""

import os
import sqlite3
import sys
from pathlib import Path

import click
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

# Add project root to path so we can import db
sys.path.insert(0, str(Path(__file__).parent.parent))

load_dotenv()

SQLITE_PATH = Path(__file__).parent.parent / "db" / "campaigns.db"


def get_sqlite():
    conn = sqlite3.connect(str(SQLITE_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def get_pg():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL not set — add it to your .env file.")
    return psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)


@click.command()
@click.option("--dry-run", is_flag=True, help="Show row counts without writing to PostgreSQL")
def migrate(dry_run: bool):
    """Migrate all data from the local SQLite database to PostgreSQL."""
    if not SQLITE_PATH.exists():
        raise click.ClickException(f"SQLite database not found: {SQLITE_PATH}")

    sqlite = get_sqlite()
    pg     = get_pg()
    cur    = pg.cursor()

    click.echo(f"\n{'🔍 DRY RUN — ' if dry_run else ''}Migrating {SQLITE_PATH} → PostgreSQL\n")

    # ── source_files ──────────────────────────────────────────────────────────
    rows = sqlite.execute("SELECT * FROM source_files ORDER BY id").fetchall()
    click.echo(f"  source_files   : {len(rows):,} rows")
    if not dry_run:
        for r in rows:
            cur.execute(
                "INSERT INTO source_files (id, path) VALUES (%s, %s) ON CONFLICT (path) DO NOTHING",
                (r["id"], r["path"])
            )
        cur.execute("SELECT setval('source_files_id_seq', COALESCE(MAX(id), 1)) FROM source_files")

    # ── lead_statuses ─────────────────────────────────────────────────────────
    rows = sqlite.execute("SELECT * FROM lead_statuses ORDER BY id").fetchall()
    click.echo(f"  lead_statuses  : {len(rows):,} rows")
    if not dry_run:
        for r in rows:
            cur.execute(
                "INSERT INTO lead_statuses (id, name) VALUES (%s, %s) ON CONFLICT (name) DO NOTHING",
                (r["id"], r["name"])
            )
        cur.execute("SELECT setval('lead_statuses_id_seq', COALESCE(MAX(id), 1)) FROM lead_statuses")

    # ── leads ─────────────────────────────────────────────────────────────────
    rows = sqlite.execute("SELECT * FROM leads ORDER BY id").fetchall()
    click.echo(f"  leads          : {len(rows):,} rows")
    if not dry_run:
        for r in rows:
            cur.execute("""
                INSERT INTO leads (
                    id, source_id, user_id, lead_owner, lead_active, business_name,
                    import_batch_id, first_name, last_name, email, phone_primary,
                    phone_secondary, appointment_time, street1, street2, city, state,
                    postal_code, latitude, longitude, status_id, note, form_data,
                    updated_at, inserted_at, deleted, source_file_id, test_lead
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                ) ON CONFLICT (id) DO NOTHING
            """, (
                r["id"], r["source_id"], r["user_id"], r["lead_owner"], r["lead_active"],
                r["business_name"], r["import_batch_id"], r["first_name"], r["last_name"],
                r["email"], r["phone_primary"], r["phone_secondary"], r["appointment_time"],
                r["street1"], r["street2"], r["city"], r["state"], r["postal_code"],
                r["latitude"], r["longitude"], r["status_id"], r["note"], r["form_data"],
                r["updated_at"], r["inserted_at"], r["deleted"], r["source_file_id"],
                r["test_lead"]
            ))
        cur.execute("SELECT setval('leads_id_seq', COALESCE(MAX(id), 1)) FROM leads")

    # ── campaigns ─────────────────────────────────────────────────────────────
    rows = sqlite.execute("SELECT * FROM campaigns ORDER BY id").fetchall()
    click.echo(f"  campaigns      : {len(rows):,} rows")
    if not dry_run:
        for r in rows:
            cur.execute("""
                INSERT INTO campaigns (id, name, subject, from_email, from_name, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
            """, (r["id"], r["name"], r["subject"], r["from_email"], r["from_name"], r["created_at"]))

    # ── campaign_sends ────────────────────────────────────────────────────────
    rows = sqlite.execute("SELECT * FROM campaign_sends ORDER BY id").fetchall()
    click.echo(f"  campaign_sends : {len(rows):,} rows")
    if not dry_run:
        for r in rows:
            cur.execute("""
                INSERT INTO campaign_sends (
                    id, campaign_id, lead_id, status, message_id,
                    queued_at, sent_at, delivered_at, opened_at, clicked_at,
                    bounced_at, failed_reason, bounce_type
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (campaign_id, lead_id) DO NOTHING
            """, (
                r["id"], r["campaign_id"], r["lead_id"], r["status"], r["message_id"],
                r["queued_at"], r["sent_at"], r["delivered_at"], r["opened_at"], r["clicked_at"],
                r["bounced_at"], r["failed_reason"], r["bounce_type"]
            ))
        cur.execute("SELECT setval('campaign_sends_id_seq', COALESCE(MAX(id), 1)) FROM campaign_sends")

    if not dry_run:
        pg.commit()
        click.secho("\n✅  Migration complete!\n", fg="green")
    else:
        click.secho("\n🔍  Dry run complete — nothing written.\n", fg="cyan")

    sqlite.close()
    pg.close()


if __name__ == "__main__":
    migrate()
