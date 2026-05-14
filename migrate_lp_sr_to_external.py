#!/usr/bin/env python3
"""
migrate_lp_sr_to_external.py — one-shot migration, run 2026-05-13.

1. UPDATE leads.status_id WHERE NULL using lp_dispo (priority) or sr_status.
2. CREATE TABLE lead_external_status (lead_id PK FK -> leads.id, plus 8 cols).
3. Copy rows from leads → lead_external_status where any of the 8 fields is set.
4. ALTER TABLE leads DROP COLUMN for the 8 migrated columns.

All in one transaction. Pass --commit to actually apply; default is dry-run
(rolls back at the end and prints what would happen).

Backup taken first: leads_backup_2026-05-13.sql (pg_dump --data-only)
"""

import os
import sys

import click
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from db import _PgConn

load_dotenv()


def get_admin_conn() -> _PgConn:
    user = os.environ["DATABASE_ADMIN_USER"]
    pw   = os.environ["DATABASE_ADMIN_PASSWORD"]
    host = os.environ["DATABASE_HOST"]
    name = os.environ["DATABASE_NAME"]
    url  = f"postgresql://{user}:{pw}@{host}:5432/{name}?sslmode=require"
    return _PgConn(psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor))


# lead_statuses ids confirmed 2026-05-13:
#   1 Canceled Appointment   2 Followup   3 Sales Appointment
#   4 No Sale                7 Sale Pending  10 sold  12 Job Cancelled
UPDATE_STATUS_SQL = """
UPDATE leads SET status_id = CASE
    WHEN lp_dispo IN ('PMPrice','PM1Leg','NPTime','DNC','NPProduct') THEN 4
    WHEN lp_dispo IN ('CXL','CTC','CRS')                              THEN 1
    WHEN lp_dispo = 'Sale (WIP)'                                      THEN 7
    WHEN lp_dispo = 'Sale (Cmp)'                                      THEN 10
    WHEN lp_dispo = 'Sale (Cxl)'                                      THEN 12
    WHEN lp_dispo IN ('NH','ReschedIP')                               THEN 2
    WHEN lp_dispo IN ('Set','Issue','Resch')                          THEN 3
    -- lp_dispo Data/OVB or any unrecognized value: leave NULL
    WHEN lp_dispo IS NULL AND sr_status = 'Appointment Set'           THEN 3
    ELSE NULL
END
WHERE status_id IS NULL
"""

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS lead_external_status (
    lead_id              INTEGER PRIMARY KEY REFERENCES leads(id) ON DELETE CASCADE,
    sr_status            TEXT,
    lp_custid            INTEGER,
    lp_dispo             TEXT,
    lp_appointment       TEXT,
    lp_rep               TEXT,
    lp_no_record         BOOLEAN NOT NULL DEFAULT FALSE,
    lp_duplicate_record  BOOLEAN NOT NULL DEFAULT FALSE,
    lp_enriched_at       TIMESTAMPTZ
)
"""

COPY_SQL = """
INSERT INTO lead_external_status (
    lead_id, sr_status, lp_custid, lp_dispo, lp_appointment,
    lp_rep, lp_no_record, lp_duplicate_record, lp_enriched_at
)
SELECT
    id, sr_status, lp_custid, lp_dispo, lp_appointment,
    lp_rep, lp_no_record, lp_duplicate_record, lp_enriched_at
FROM leads
WHERE sr_status            IS NOT NULL
   OR lp_custid            IS NOT NULL
   OR lp_dispo             IS NOT NULL
   OR lp_appointment       IS NOT NULL
   OR lp_rep               IS NOT NULL
   OR lp_no_record         = TRUE
   OR lp_duplicate_record  = TRUE
   OR lp_enriched_at       IS NOT NULL
"""

DROP_COLUMNS_SQL = """
ALTER TABLE leads
    DROP COLUMN sr_status,
    DROP COLUMN lp_custid,
    DROP COLUMN lp_dispo,
    DROP COLUMN lp_appointment,
    DROP COLUMN lp_rep,
    DROP COLUMN lp_no_record,
    DROP COLUMN lp_duplicate_record,
    DROP COLUMN lp_enriched_at
"""


@click.command()
@click.option("--commit", is_flag=True, help="Actually commit. Without this it's a dry-run that rolls back.")
def main(commit: bool):
    conn = get_admin_conn()
    raw = conn._conn   # psycopg2 connection for direct rollback control
    raw.autocommit = False

    try:
        # ----- before snapshot -----
        c = conn.execute("SELECT COUNT(*) AS n FROM leads WHERE status_id IS NULL")
        before_null = c.fetchone()["n"]
        click.echo(f"  before: status_id IS NULL = {before_null:,}")

        # ----- 1. update status_id -----
        c = conn.execute(UPDATE_STATUS_SQL)
        click.echo(f"  step 1: UPDATE leads.status_id  -> {c.rowcount:,} rows touched")

        c = conn.execute("SELECT COUNT(*) AS n FROM leads WHERE status_id IS NULL")
        click.echo(f"          remaining NULL status_id = {c.fetchone()['n']:,}")

        # ----- 2. create table -----
        conn.execute(CREATE_TABLE_SQL)
        click.echo("  step 2: CREATE TABLE lead_external_status")

        # ----- 3. copy rows -----
        c = conn.execute(COPY_SQL)
        click.echo(f"  step 3: COPY rows               -> {c.rowcount:,} rows inserted into lead_external_status")

        # sanity: spot-check a couple of rows
        c = conn.execute("""
            SELECT l.id, l.lp_dispo, l.sr_status, l.lp_custid, e.lp_dispo AS e_disp, e.sr_status AS e_sr, e.lp_custid AS e_cust
              FROM leads l
              JOIN lead_external_status e ON e.lead_id = l.id
             WHERE l.lp_dispo IS NOT NULL
             LIMIT 3
        """)
        for r in c.fetchall():
            ok = (r["lp_dispo"] == r["e_disp"] and r["sr_status"] == r["e_sr"] and r["lp_custid"] == r["e_cust"])
            click.echo(f"          spot-check id={r['id']} lp_dispo={r['lp_dispo']!r}  ok={ok}")

        # ----- 4. drop columns -----
        conn.execute(DROP_COLUMNS_SQL)
        click.echo("  step 4: DROP 8 columns from leads")

        c = conn.execute("""SELECT column_name FROM information_schema.columns
                              WHERE table_name='leads'
                                AND column_name IN
                              ('sr_status','lp_custid','lp_dispo','lp_appointment','lp_rep',
                               'lp_no_record','lp_duplicate_record','lp_enriched_at')""")
        leftover = [r["column_name"] for r in c.fetchall()]
        if leftover:
            raise RuntimeError(f"columns still present after drop: {leftover}")
        click.echo("          confirmed: 0 of 8 columns remain on leads")

        if commit:
            raw.commit()
            click.secho("\n✅  COMMITTED\n", fg="green")
        else:
            raw.rollback()
            click.secho("\n🟡  DRY-RUN — rolled back. Pass --commit to apply.\n", fg="yellow")

    except Exception:
        raw.rollback()
        click.secho("\n❌  ROLLED BACK due to error\n", fg="red")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
