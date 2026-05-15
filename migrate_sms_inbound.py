#!/usr/bin/env python3
"""Create sms_inbound_events table for Mandrill SMS webhook ingestion.

Run as the admin user (DATABASE_ADMIN_*); regular runtime users only need
SELECT/INSERT/UPDATE which are GRANTed at the end.
"""
import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

SCHEMA = """
CREATE TABLE IF NOT EXISTS sms_inbound_events (
    id            SERIAL      PRIMARY KEY,
    received_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    mandrill_id   TEXT        UNIQUE,
    from_phone    TEXT        NOT NULL,
    to_phone      TEXT,
    message_text  TEXT,
    is_stop       BOOLEAN     NOT NULL DEFAULT FALSE,
    lead_id       INTEGER     REFERENCES leads(id),
    raw_payload   JSONB
);
CREATE INDEX IF NOT EXISTS idx_sms_inbound_from_phone  ON sms_inbound_events(from_phone);
CREATE INDEX IF NOT EXISTS idx_sms_inbound_received_at ON sms_inbound_events(received_at);
CREATE INDEX IF NOT EXISTS idx_sms_inbound_is_stop     ON sms_inbound_events(is_stop) WHERE is_stop;
"""

GRANTS = """
GRANT SELECT, INSERT, UPDATE ON sms_inbound_events TO {runtime_user};
GRANT USAGE, SELECT ON SEQUENCE sms_inbound_events_id_seq TO {runtime_user};
"""


def main():
    conn = psycopg2.connect(
        host=os.environ["DATABASE_HOST"],
        dbname=os.environ["DATABASE_NAME"],
        user=os.environ["DATABASE_ADMIN_USER"],
        password=os.environ["DATABASE_ADMIN_PASSWORD"],
        sslmode="require",
    )
    conn.autocommit = False
    cur = conn.cursor()
    try:
        print("Creating sms_inbound_events …")
        cur.execute(SCHEMA)

        for runtime_user in ("cburkhardt", "apex_user"):
            print(f"Granting on sms_inbound_events to {runtime_user} …")
            cur.execute(GRANTS.format(runtime_user=runtime_user))

        conn.commit()
        print("✅ sms_inbound_events ready")
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
