"""
db.py — SQLite schema and helpers for apex-email-campaigns.

Tables:
  campaigns   — one row per campaign run
  recipients  — one row per (campaign, email) with full lifecycle status
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "db" / "campaigns.db"


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS campaigns (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            subject     TEXT NOT NULL,
            from_email  TEXT NOT NULL,
            from_name   TEXT NOT NULL,
            created_at  TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS recipients (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id   TEXT    NOT NULL REFERENCES campaigns(id),
            first_name    TEXT    NOT NULL,
            last_name     TEXT,
            email         TEXT    NOT NULL,
            city          TEXT,
            extra_vars    TEXT,                    -- JSON blob for additional template vars
            status        TEXT    NOT NULL DEFAULT 'queued',
                                                   -- queued → sent → delivered
                                                   -- or: failed / bounced / complained
            message_id    TEXT,                    -- SES MessageId, used to match events
            sent_at       TEXT,
            delivered_at  TEXT,
            opened_at     TEXT,                    -- first open timestamp
            clicked_at    TEXT,                    -- first click timestamp
            bounced_at    TEXT,
            failed_reason TEXT,
            bounce_type   TEXT,                    -- Permanent / Transient
            UNIQUE(campaign_id, email)
        );

        CREATE INDEX IF NOT EXISTS idx_message_id
            ON recipients(message_id);
        CREATE INDEX IF NOT EXISTS idx_campaign_status
            ON recipients(campaign_id, status);
    """)
    conn.commit()
    conn.close()
