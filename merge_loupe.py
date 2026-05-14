#!/usr/bin/env python3
"""
merge_loupe.py — Phase 3: insert new loupe customers into leads.

Defaults to a dry-run that prints the row count it WOULD insert.
Pass --commit to actually write to the leads table.

Eligibility per loupe_leads row:
  parse_status = 'ok'              (parser produced clean first/last names)
  matched_lead_id IS NULL          (this customer does not already exist in leads)

Customer-level dedupe: one row per customer_id (latest by last_activity).
After insert, merged_at is stamped on every loupe_leads row of the inserted
customers AND on every row that matched an existing lead — so anything still
NULL after the run is something we deliberately skipped (parse_status='manual'
on a customer with no existing record).

Usage:
    python merge_loupe.py            # dry-run, no writes
    python merge_loupe.py --commit   # do the inserts
"""

import json
import os

import click
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from db import _PgConn

load_dotenv()

LOUPE_SOURCE_ID = 34   # source.id for data/loupe_export_2016-2020.csv


def get_admin_conn() -> _PgConn:
    user = os.environ["DATABASE_ADMIN_USER"]
    pw   = os.environ["DATABASE_ADMIN_PASSWORD"]
    host = os.environ["DATABASE_HOST"]
    name = os.environ["DATABASE_NAME"]
    url  = f"postgresql://{user}:{pw}@{host}:5432/{name}?sslmode=require"
    return _PgConn(psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor))


CANONICAL_SQL = """
WITH ranked AS (
    SELECT *,
           ROW_NUMBER() OVER (
               PARTITION BY customer_id
               ORDER BY last_activity DESC NULLS LAST, id DESC
           ) AS rn
      FROM loupe_leads
     WHERE parse_status = 'ok'
       AND matched_lead_id IS NULL
       AND merged_at IS NULL
       AND customer_id NOT IN (
           SELECT customer_id
             FROM loupe_leads
            WHERE matched_lead_id IS NOT NULL
               OR merged_at IS NOT NULL
       )
)
SELECT *
  FROM ranked
 WHERE rn = 1
"""


@click.command()
@click.option("--commit", is_flag=True, help="Actually insert rows. Without this it's a dry-run.")
def main(commit: bool):
    conn = get_admin_conn()

    cur = conn.execute(CANONICAL_SQL)
    rows = cur.fetchall()
    click.echo(f"  canonical rows eligible for insert : {len(rows):,}")

    if not commit:
        cur = conn.execute(
            "SELECT COUNT(DISTINCT customer_id) AS n "
            "  FROM loupe_leads WHERE matched_lead_id IS NOT NULL"
        )
        matched = cur.fetchone()["n"]
        cur = conn.execute(
            "SELECT COUNT(DISTINCT customer_id) AS n "
            "  FROM loupe_leads WHERE parse_status <> 'ok'"
        )
        dropped = cur.fetchone()["n"]
        click.echo(f"  would skip (matched existing lead): {matched:,}")
        click.echo(f"  would drop (parse_status=manual)  : {dropped:,}")
        click.secho("\nDRY-RUN — pass --commit to write to leads.\n", fg="yellow")
        conn.close()
        return

    inserted = 0
    inserted_customer_ids: list[str] = []
    for r in rows:
        raw_jsonb = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()}
        ins = conn.execute(
            """
            INSERT INTO leads (
                source_id,
                loupe_id,
                first_name,
                last_name,
                email,
                phone_primary,
                street1,
                street2,
                city,
                state,
                postal_code,
                lead_owner,
                updated_at,
                status_id,
                test_lead,
                raw
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s::jsonb
            ) RETURNING id
            """,
            (
                LOUPE_SOURCE_ID,
                r["customer_id"],
                r["first_name"],
                r["last_name"],
                r["email"],
                r["phone"],
                r["address"],
                r["address2"],
                r["city"],
                r["state"],
                r["zip"],
                r["sales_rep"],
                (r["last_activity"].isoformat() if r["last_activity"] else None),
                r["target_status_id"],
                0,
                json.dumps(raw_jsonb),
            ),
        )
        ins.fetchone()
        inserted += 1
        inserted_customer_ids.append(r["customer_id"])

    # Stamp merged_at on ALL loupe_leads rows whose customer was inserted
    if inserted_customer_ids:
        conn.execute(
            "UPDATE loupe_leads SET merged_at = NOW() "
            "  WHERE customer_id = ANY(%s)",
            (inserted_customer_ids,),
        )

    # Also stamp merged_at on rows already matched to existing leads (so anything
    # still NULL after this run is a deliberately-dropped manual)
    conn.execute(
        "UPDATE loupe_leads SET merged_at = NOW() "
        "  WHERE matched_lead_id IS NOT NULL AND merged_at IS NULL"
    )

    conn.commit()
    conn.close()
    click.secho(f"\n✅  inserted {inserted:,} new leads (source_id={LOUPE_SOURCE_ID})\n", fg="green")


if __name__ == "__main__":
    main()
