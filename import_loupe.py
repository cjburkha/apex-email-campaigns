#!/usr/bin/env python3
"""
import_loupe.py — Phase 1: stage the Loupe export into its own table.

Loads data/loupe_export_2026-05-12.csv into loupe_leads (raw, unparsed)
and registers a new row in the source table.

This is staging only — no rows are merged into leads here. Phase 2 will:
  • parse Customer Name → first_name / last_name (with "A & B" spouse style)
  • match against existing leads on normalized phone/email
  • insert only unmatched rows into leads

Usage:
    python import_loupe.py
    python import_loupe.py --reset    # drop and recreate loupe_leads first
"""

import csv
import os
import re

import click
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from db import _PgConn

load_dotenv()

CSV_PATH = "data/loupe_export_2016-2020.csv"
SOURCE_PATH = CSV_PATH          # what we store in source.path
SOURCE_KIND = "loupe"           # source.source

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS loupe_leads (
    id              SERIAL PRIMARY KEY,
    ticket_id       TEXT,
    ticket_name     TEXT,
    status          TEXT,
    store           TEXT,
    customer_name   TEXT,
    phone           TEXT,
    phone_norm      TEXT,
    email           TEXT,
    email_norm      TEXT,
    address         TEXT,
    address2        TEXT,
    city            TEXT,
    state           TEXT,
    zip             TEXT,
    sales_rep       TEXT,
    last_activity   DATE,
    customer_id     TEXT,
    location_id     TEXT,
    source_id       INTEGER REFERENCES source(id),
    matched_lead_id INTEGER REFERENCES leads(id),
    merged_at       TIMESTAMPTZ
)
"""

CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_loupe_phone_norm ON loupe_leads(phone_norm)",
    "CREATE INDEX IF NOT EXISTS idx_loupe_email_norm ON loupe_leads(email_norm)",
    "CREATE INDEX IF NOT EXISTS idx_loupe_ticket_id  ON loupe_leads(ticket_id)",
]


def normalize_phone(raw: str | None) -> str | None:
    if not raw:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else None


def normalize_email(raw: str | None) -> str | None:
    if not raw:
        return None
    e = raw.strip().lower()
    return e or None


def get_admin_conn() -> _PgConn:
    """Connect as DATABASE_ADMIN_USER (needs CREATE TABLE in public)."""
    user = os.environ["DATABASE_ADMIN_USER"]
    pw   = os.environ["DATABASE_ADMIN_PASSWORD"]
    host = os.environ["DATABASE_HOST"]
    name = os.environ["DATABASE_NAME"]
    url  = f"postgresql://{user}:{pw}@{host}:5432/{name}?sslmode=require"
    conn = psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)
    return _PgConn(conn)


def get_or_create_source(conn, path: str, kind: str) -> int:
    """Return source.id for (path, kind), inserting if missing."""
    cur = conn.execute("SELECT id FROM source WHERE path = %s", (path,))
    row = cur.fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO source (path, source) VALUES (%s, %s) RETURNING id",
        (path, kind),
    )
    return cur.fetchone()["id"]


@click.command()
@click.option("--reset", is_flag=True, help="Drop and recreate loupe_leads first")
def import_loupe(reset: bool):
    conn = get_admin_conn()

    if reset:
        conn.execute("DROP TABLE IF EXISTS loupe_leads")
        click.secho("  Dropped existing loupe_leads table", fg="yellow")

    conn.execute(CREATE_TABLE)
    for stmt in CREATE_INDEXES:
        conn.execute(stmt)
    conn.commit()

    source_id = get_or_create_source(conn, SOURCE_PATH, SOURCE_KIND)
    conn.commit()
    click.echo(f"  source_id = {source_id}  ({SOURCE_PATH})")

    inserted = 0
    with open(CSV_PATH, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            conn.execute(
                """
                INSERT INTO loupe_leads (
                    ticket_id, ticket_name, status, store, customer_name,
                    phone, phone_norm, email, email_norm,
                    address, address2, city, state, zip,
                    sales_rep, last_activity, customer_id, location_id,
                    source_id
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s
                )
                """,
                (
                    row["Ticket ID"] or None,
                    row["Ticket Name"] or None,
                    row["Status"] or None,
                    row["Store"] or None,
                    row["Customer Name"] or None,
                    row["Phone"] or None,
                    normalize_phone(row["Phone"]),
                    row["Email"] or None,
                    normalize_email(row["Email"]),
                    row["Address"] or None,
                    row["Address 2"] or None,
                    row["City"] or None,
                    row["State"] or None,
                    row["Zip"] or None,
                    row["Sales Rep"] or None,
                    row["Last Activity"] or None,
                    row["Customer ID"] or None,
                    row["Location ID"] or None,
                    source_id,
                ),
            )
            inserted += 1

    conn.commit()
    click.secho(f"\n✅  {inserted:,} rows inserted into loupe_leads\n", fg="green")
    conn.close()


if __name__ == "__main__":
    import_loupe()
