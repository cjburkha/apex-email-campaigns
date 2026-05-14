#!/usr/bin/env python3
"""
parse_loupe.py — Phase 2 (dry-run): parse names, map statuses, match to leads.

What this does (idempotent, no inserts into leads):
  1. Schema: ALTER leads ADD loupe_id; ALTER loupe_leads ADD parsed-name + match cols
  2. Inserts new lead_status 'Job Cancelled' if missing
  3. Parses loupe_leads.customer_name → first_name / last_name / parse_status / parse_note
  4. Maps loupe_leads.status → leads target status_id (stored in loupe_leads.target_status_id)
  5. Matches each loupe row to existing leads on phone_norm, then email_norm
     → writes loupe_leads.matched_lead_id (NULL = unmatched, will be a new lead)
  6. Reports counts + samples; merge into leads happens in a separate script

Usage:
    python parse_loupe.py
"""

import os
import re
from collections import defaultdict

import click
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from db import _PgConn

load_dotenv()


# --------------------------------------------------------------------------- #
# DB
# --------------------------------------------------------------------------- #
def get_admin_conn() -> _PgConn:
    user = os.environ["DATABASE_ADMIN_USER"]
    pw   = os.environ["DATABASE_ADMIN_PASSWORD"]
    host = os.environ["DATABASE_HOST"]
    name = os.environ["DATABASE_NAME"]
    url  = f"postgresql://{user}:{pw}@{host}:5432/{name}?sslmode=require"
    return _PgConn(psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor))


SCHEMA_STMTS = [
    "ALTER TABLE leads        ADD COLUMN IF NOT EXISTS loupe_id          TEXT",
    "CREATE INDEX IF NOT EXISTS idx_leads_loupe_id ON leads(loupe_id)",
    "ALTER TABLE loupe_leads  ADD COLUMN IF NOT EXISTS first_name        TEXT",
    "ALTER TABLE loupe_leads  ADD COLUMN IF NOT EXISTS last_name         TEXT",
    "ALTER TABLE loupe_leads  ADD COLUMN IF NOT EXISTS parse_status      TEXT",
    "ALTER TABLE loupe_leads  ADD COLUMN IF NOT EXISTS parse_note        TEXT",
    "ALTER TABLE loupe_leads  ADD COLUMN IF NOT EXISTS target_status_id  INTEGER REFERENCES lead_statuses(id)",
]


# --------------------------------------------------------------------------- #
# Status map (loupe status string → leads.lead_statuses name)
# --------------------------------------------------------------------------- #
STATUS_MAP = {
    "Appt Cancelled":       "Canceled Appointment",
    "Install Done":         "sold",
    "Not Sold":             "No Sale",
    "Followup":             "Followup",
    "Followup Cancelled":   "Canceled Appointment",
    "Sales Appt":           "Sales Appointment",
    "Contract Cancelled":   "Job Cancelled",   # new status
    "Installed":            "sold",
    "Rework Request":       "sold",
    "Job Canceled":         "Job Cancelled",   # new status
    "Sched Service":        "sold",
    "Ordered":              "Sale Pending",
    "Sched Install":        "sold",
    "Install Appt":         "sold",
    "Mfg Service":          "sold",
    "Sold":                 "sold",
    "Apex Service":         "sold",
    "Review Proposal":      "Sales Appointment",
    "Admin Install Review": "sold",
    "Collections":          "sold",
    "Create Proposal":      "Sales Appointment",
    "Sale Pending":         "Sale Pending",
    "Service Appt":         "sold",
    "Admin Survey Review":  "sold",
    "Edit Order":           "sold",
    "Waiting on Mfg":       "sold",
    "Measure Appt":         "sold",
    "Build Packet":         "sold",
}


# --------------------------------------------------------------------------- #
# Name parser
# --------------------------------------------------------------------------- #
# Compound-surname particles. If the word before the last surname is one of
# these (case-insensitive), the surname is taken as two words ("De Leon").
SURNAME_PARTICLES = {
    "de", "del", "dela", "di", "da", "du", "van", "von",
    "vander", "vanden", "mc", "mac", "st", "st.", "saint",
    "le", "la", "los", "las", "el",
}

BUSINESS_HINTS = re.compile(
    r"\b(LLC|Inc|Inc\.|Corp|Corp\.|Co\.|Group|Center|Garden|Trust|Estate|Properties|"
    r"Holdings|Partners|Realty|School|Foundation|Association)\b",
    re.IGNORECASE,
)

# Trailing placeholder "surnames" that mean "no surname known".
# Matches: "Noname", "No name", "(No name)", "N/A", "N/a", "H/O", "New H/O".
PLACEHOLDER_SURNAME = re.compile(
    r"\s*\(?\s*(?:no\s*name|noname|n[/.]?a\.?|(?:new\s+)?h[/.]?o\.?)\s*\)?\s*$",
    re.IGNORECASE,
)


def _split_surname(seg_tokens: list[str]) -> tuple[str, str]:
    """Given a list of tokens that ends with a surname, return (first_part, last_name).

    Handles compound surnames via SURNAME_PARTICLES — if the second-to-last
    token is a particle, both tokens are taken as the surname.
    """
    if len(seg_tokens) >= 2 and seg_tokens[-2].lower().rstrip(".") in SURNAME_PARTICLES:
        return " ".join(seg_tokens[:-2]), " ".join(seg_tokens[-2:])
    return " ".join(seg_tokens[:-1]), seg_tokens[-1]


def parse_customer_name(raw: str) -> tuple[str | None, str | None, str, str]:
    """Parse a Loupe Customer Name into (first_name, last_name, status, note).

    status ∈ {ok, manual, business}.
    """
    if not raw or not raw.strip():
        return None, None, "manual", "empty"

    s = raw.strip()

    # Strip trailing placeholder surnames; remember we did so.
    no_surname = False
    stripped = PLACEHOLDER_SURNAME.sub("", s).strip()
    if stripped != s:
        no_surname = True
        s = stripped
        if not s:
            return None, None, "manual", "only placeholder"

    if BUSINESS_HINTS.search(s):
        return None, None, "business", "business keyword"

    # Track whether original had "/" (signals two-surname couple)
    had_slash = "/" in s

    # Normalize couple connectors → " & "
    s = s.replace("/", " & ")
    s = re.sub(r"\s*,\s*", " & ", s)                       # comma as connector
    s = re.sub(r"\s+(?:and|And|AND|abd)\s+", " & ", s)
    s = re.sub(r"\s*&\s*", " & ", s)
    s = re.sub(r"\s+", " ", s).strip()

    segments = [seg.strip() for seg in s.split(" & ") if seg.strip()]

    # If trailing placeholder was stripped, what remains is all first names.
    if no_surname:
        return " & ".join(segments), None, "ok", "no surname"

    # ---- single person ----
    if len(segments) == 1:
        toks = segments[0].split()
        if len(toks) == 1:
            return toks[0], None, "manual", "single token"
        first, last = _split_surname(toks)
        return first, last, "ok", "single"

    # ---- couple / multi ----
    last_seg_toks = segments[-1].split()

    # Two-surname pattern: last segment is a single word AND prior segments end with
    # a multi-word block (the firsts + first surname). Also fires when "/" was in raw.
    middle_has_extra = any(len(seg.split()) >= 2 for seg in segments[:-1])
    if (had_slash and len(last_seg_toks) >= 1) or (
        len(last_seg_toks) == 1 and middle_has_extra
    ):
        # firsts = leading single-word segments + all but the last word of the multi-word seg
        # surnames = last word of that multi-word seg + each trailing single-word seg
        firsts: list[str] = []
        surnames: list[str] = []
        # Walk: leading single-word segments are pure firsts.
        i = 0
        while i < len(segments) and len(segments[i].split()) == 1:
            firsts.append(segments[i])
            i += 1
        if i < len(segments):
            multi = segments[i].split()
            firsts.append(" ".join(multi[:-1]) if len(multi) > 1 else multi[0])
            surnames.append(multi[-1] if len(multi) > 1 else multi[0])
            i += 1
        # remaining segments are surnames
        for seg in segments[i:]:
            surnames.append(seg)
        if firsts and surnames and len(firsts) == len(surnames):
            return " & ".join(firsts), " & ".join(surnames), "ok", "two-surname couple"
        # fallthrough → manual

    # Shared-surname couple: last segment carries the shared surname.
    if len(last_seg_toks) >= 2:
        first_part_of_last, surname = _split_surname(last_seg_toks)
        firsts = [seg for seg in segments[:-1]]
        if first_part_of_last:
            firsts.append(first_part_of_last)
        # Guardrail: if first_part_of_last has more than 2 words, looks like a business
        if first_part_of_last and len(first_part_of_last.split()) > 2:
            return None, None, "manual", "last segment too long"
        return " & ".join(firsts), surname, "ok", "shared surname"

    # Fallback: 2+ segments, all single-word → couple with no surname (e.g. "Karen & Ken")
    if all(len(seg.split()) == 1 for seg in segments):
        return " & ".join(segments), None, "ok", "couple no surname"

    return None, None, "manual", "unrecognized pattern"


# --------------------------------------------------------------------------- #
# Phone / email normalization (also used at load time)
# --------------------------------------------------------------------------- #
def normalize_phone(raw):
    if not raw: return None
    d = re.sub(r"\D", "", str(raw))
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return d if len(d) == 10 else None


def normalize_email(raw):
    if not raw: return None
    e = raw.strip().lower()
    return e or None


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
@click.command()
def main():
    conn = get_admin_conn()

    # 1. Schema
    for stmt in SCHEMA_STMTS:
        conn.execute(stmt)
    conn.commit()
    click.echo("✔  schema in place")

    # 2. Ensure 'Job Cancelled' lead_status exists
    conn.execute(
        "INSERT INTO lead_statuses (name) VALUES (%s) ON CONFLICT (name) DO NOTHING",
        ("Job Cancelled",),
    )
    conn.commit()

    # Load name→id map for lead_statuses
    cur = conn.execute("SELECT id, name FROM lead_statuses")
    status_name_to_id = {r["name"]: r["id"] for r in cur.fetchall()}
    click.echo(f"✔  {len(status_name_to_id)} lead_statuses in DB")

    # Resolve STATUS_MAP → loupe_status → status_id
    unmapped = [v for v in STATUS_MAP.values() if v not in status_name_to_id]
    if unmapped:
        raise RuntimeError(f"Status map references missing statuses: {unmapped}")
    loupe_to_status_id = {k: status_name_to_id[v] for k, v in STATUS_MAP.items()}

    # 3. Build phone/email index over existing leads
    cur = conn.execute(
        "SELECT id, email, phone_primary, phone_secondary FROM leads"
    )
    phone_idx: dict[str, int] = {}
    email_idx: dict[str, int] = {}
    for L in cur.fetchall():
        for p in (L["phone_primary"], L["phone_secondary"]):
            np = normalize_phone(p)
            if np and np not in phone_idx:
                phone_idx[np] = L["id"]
        ne = normalize_email(L["email"])
        if ne and ne not in email_idx:
            email_idx[ne] = L["id"]
    click.echo(f"✔  indexed {len(phone_idx):,} phone keys, {len(email_idx):,} email keys")

    # 4. Walk loupe_leads → parse + map + match
    cur = conn.execute(
        "SELECT id, customer_name, status, phone_norm, email_norm FROM loupe_leads"
    )
    rows = cur.fetchall()

    parse_counts = defaultdict(int)
    status_unmapped = defaultdict(int)
    match_counts = defaultdict(int)
    manual_samples: list[tuple[int, str, str]] = []

    for r in rows:
        first, last, ps, note = parse_customer_name(r["customer_name"])
        parse_counts[ps] += 1
        if ps != "ok" and len(manual_samples) < 30:
            manual_samples.append((r["id"], r["customer_name"], f"{ps}: {note}"))

        target_status_id = loupe_to_status_id.get(r["status"])
        if target_status_id is None and r["status"]:
            status_unmapped[r["status"]] += 1

        matched_lead_id = None
        if r["phone_norm"] and r["phone_norm"] in phone_idx:
            matched_lead_id = phone_idx[r["phone_norm"]]
            match_counts["phone"] += 1
        elif r["email_norm"] and r["email_norm"] in email_idx:
            matched_lead_id = email_idx[r["email_norm"]]
            match_counts["email"] += 1
        else:
            match_counts["none"] += 1

        conn.execute(
            """
            UPDATE loupe_leads
               SET first_name        = %s,
                   last_name         = %s,
                   parse_status      = %s,
                   parse_note        = %s,
                   target_status_id  = %s,
                   matched_lead_id   = %s
             WHERE id = %s
            """,
            (first, last, ps, note, target_status_id, matched_lead_id, r["id"]),
        )

    conn.commit()
    conn.close()

    # 5. Report
    click.echo("\n────── Parse ──────")
    for k, n in sorted(parse_counts.items(), key=lambda x: -x[1]):
        click.echo(f"  {k:10s} {n:>5,}")

    click.echo("\n────── Status mapping ──────")
    if status_unmapped:
        click.secho("  UNMAPPED loupe statuses:", fg="red")
        for k, n in status_unmapped.items():
            click.echo(f"    {k}: {n}")
    else:
        click.echo("  all loupe statuses mapped ✔")

    click.echo("\n────── Lead match ──────")
    for k, n in match_counts.items():
        click.echo(f"  {k:6s} {n:>5,}")

    click.echo("\n────── Parse flags (sample) ──────")
    for rid, name, note in manual_samples:
        click.echo(f"  #{rid:>5} [{note}]  {name}")


if __name__ == "__main__":
    main()
