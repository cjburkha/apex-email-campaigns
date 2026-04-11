"""
db.py — PostgreSQL connection and schema helpers for apex-email-campaigns.

Tables:
  source_files   — lookup: one row per imported xlsx file path
  lead_statuses  — lookup: one row per distinct lead status string
  leads          — one row per lead; FK to source_files and lead_statuses
  campaigns      — one row per campaign run
  campaign_sends — one row per (campaign × lead): tracks send/delivery/open/click

SQL dialect (psycopg2 / PostgreSQL):
  • Use %s placeholders (not ?)
  • Use ON CONFLICT … DO NOTHING (not INSERT OR IGNORE)
  • Use NOW() (not datetime('now'))
"""

import os

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

_KEYRING_SERVICE = "apex-campaigns"
_KEYRING_KEY     = "DATABASE_URL"
_DB_HOST = "wbb-prod.c81qkua4c3e2.us-east-1.rds.amazonaws.com"
_DB_NAME = "apex"


def _build_url(username: str, password: str) -> str:
    return f"postgresql://{username}:{password}@{_DB_HOST}:5432/{_DB_NAME}?sslmode=require"


def _get_database_url() -> str:
    """
    Load DATABASE_URL using this priority order:
      1. Environment variable / .env file  (CI, docker, explicit override)
      2. OS keychain  (macOS Keychain / Windows Credential Manager)
      3. Interactive prompt → saved to keychain for next time

    Credentials stored in the OS keychain are encrypted by the OS and tied
    to your login session — they never touch the filesystem in plain text.
    """
    # 1. Environment / .env (full URL override)
    url = os.environ.get(_KEYRING_KEY)
    if url:
        return url

    # 2. OS keychain — stored as "username|password"
    try:
        import keyring
        stored = keyring.get_password(_KEYRING_SERVICE, _KEYRING_KEY)
        if stored and "|" in stored:
            username, _, password = stored.partition("|")
            return _build_url(username, password)
        elif stored:  # legacy: full URL stored directly
            return stored
    except Exception:
        pass

    # 3. Prompt for username + password and save to keychain
    import click
    click.echo("\n🔑  No credentials found. Ask your admin for your username and password.")
    username = click.prompt("    Username").strip()
    password = click.prompt("    Password", hide_input=True).strip()
    try:
        import keyring as kr
        kr.set_password(_KEYRING_SERVICE, _KEYRING_KEY, f"{username}|{password}")
        click.echo("    ✔  Saved to OS keychain — won't be asked again.\n")
    except Exception:
        click.echo("    ⚠️  Could not save to keychain. Add DATABASE_URL to .env manually.\n")
    return _build_url(username, password)


def get_conn():
    """Return a _PgConn backed by DATABASE_URL from the environment or OS keychain."""
    url = _get_database_url()
    conn = psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)
    return _PgConn(conn)


class _PgConn:
    """
    Thin wrapper around a psycopg2 connection that presents the same
    .execute() / .executescript() / .commit() / .close() interface the
    rest of the app expects, so call-sites need no structural changes.
    """

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        cur = self._conn.cursor()
        cur.execute(sql, params if params else None)
        return cur

    def executescript(self, sql: str):
        """Execute a semicolon-separated SQL script (used by init_db only)."""
        cur = self._conn.cursor()
        for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
            cur.execute(stmt)
        self._conn.commit()
        return cur

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def migrate_db() -> None:
    """No-op on PostgreSQL — schema is always created fresh via init_db()."""
    pass


def init_db() -> None:
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS source_files (
            id    SERIAL PRIMARY KEY,
            path  TEXT   NOT NULL UNIQUE
        );

        CREATE TABLE IF NOT EXISTS lead_statuses (
            id   SERIAL PRIMARY KEY,
            name TEXT   NOT NULL UNIQUE
        );

        CREATE TABLE IF NOT EXISTS leads (
            id               SERIAL PRIMARY KEY,
            source_id        TEXT,
            user_id          TEXT,
            lead_owner       TEXT,
            lead_active      TEXT,
            business_name    TEXT,
            import_batch_id  TEXT,
            first_name       TEXT,
            last_name        TEXT,
            email            TEXT,
            phone_primary    TEXT,
            phone_secondary  TEXT,
            appointment_time TEXT,
            street1          TEXT,
            street2          TEXT,
            city             TEXT,
            state            TEXT,
            postal_code      TEXT,
            latitude         DOUBLE PRECISION,
            longitude        DOUBLE PRECISION,
            status_id        INTEGER REFERENCES lead_statuses(id),
            note             TEXT,
            form_data        TEXT,
            updated_at       TEXT,
            inserted_at      TEXT,
            deleted          TEXT,
            source_file_id   INTEGER REFERENCES source_files(id),
            test_lead        INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS campaigns (
            id          TEXT        PRIMARY KEY,
            name        TEXT        NOT NULL,
            subject     TEXT        NOT NULL,
            from_email  TEXT        NOT NULL,
            from_name   TEXT        NOT NULL,
            created_at  TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS campaign_sends (
            id            SERIAL      PRIMARY KEY,
            campaign_id   TEXT        NOT NULL REFERENCES campaigns(id),
            lead_id       INTEGER     NOT NULL REFERENCES leads(id),
            status        TEXT        NOT NULL DEFAULT 'queued',
            message_id    TEXT,
            queued_at     TIMESTAMPTZ DEFAULT NOW(),
            sent_at       TIMESTAMPTZ,
            delivered_at  TIMESTAMPTZ,
            opened_at     TIMESTAMPTZ,
            clicked_at    TIMESTAMPTZ,
            bounced_at    TIMESTAMPTZ,
            failed_reason TEXT,
            bounce_type   TEXT,
            UNIQUE(campaign_id, lead_id)
        );

        CREATE INDEX IF NOT EXISTS idx_cs_message_id
            ON campaign_sends(message_id);
        CREATE INDEX IF NOT EXISTS idx_cs_campaign_status
            ON campaign_sends(campaign_id, status)
    """)
    conn.close()
