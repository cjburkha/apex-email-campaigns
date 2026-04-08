#!/usr/bin/env python3
"""
send_outlook.py — Send a campaign via the local Outlook desktop app (AppleScript).

Drives the already-signed-in Outlook app on macOS — no credentials, app
passwords, or IT permissions required. Outlook must be running.

Usage:
    python send_outlook.py --campaign window-inspection --query "SELECT * FROM leads WHERE test_lead = 1"
    python send_outlook.py --campaign window-inspection --query "SELECT * FROM leads WHERE state='WI'" --dry-run
    python send_outlook.py --campaign window-inspection --query "SELECT * FROM leads WHERE state='WI'" --limit 50

Batching:
    --limit N sends up to N *valid, not-yet-sent* leads. Re-running the same
    command with the same query will automatically skip already-sent leads and
    pick up the next batch of unsent ones.
"""

import json
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import click
from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader, Template

from db import get_conn, init_db

load_dotenv()


# ── email validation ──────────────────────────────────────────────────────────

# Known typo corrections: bad domain → corrected domain
_DOMAIN_FIXES = {
    "gmail.comm":   "gmail.com",
    "gamil.com":    "gmail.com",
    "gmal.com":     "gmail.com",
    "gmial.com":    "gmail.com",
    "yahoo.comm":   "yahoo.com",
    "yaho.com":     "yahoo.com",
    "hotmail.comm": "hotmail.com",
    "outloook.com": "outlook.com",
}

# Domains/patterns that indicate a fake or placeholder email
_FAKE_PATTERNS = [
    r"noemail\.",
    r"unknownemail\.",
    r"unknown\w*email\.",
    r"@noemail",
    r"@unknown",
    r"@test\.",
    r"@example\.",
    r"@fake\.",
    r"\.comm$",          # catches any .comm not fixed above
]

_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
)


def _clean_email(email: str) -> str:
    """Normalize and fix common typos. Returns the cleaned email."""
    if not email:
        return ""
    email = email.strip().lower()
    local, _, domain = email.partition("@")
    if not domain:
        return email
    fixed_domain = _DOMAIN_FIXES.get(domain, domain)
    return f"{local}@{fixed_domain}"


def _is_valid_email(email: str) -> bool:
    """Return True if the email looks real and is correctly formatted."""
    if not email or len(email) < 6:
        return False
    if not _EMAIL_RE.match(email):
        return False
    # Must have something reasonable on both sides of @
    local, _, domain = email.partition("@")
    domain_name = domain.rsplit(".", 1)[0]  # strip TLD
    if len(local) < 2 or len(domain_name) < 2:
        return False
    # Block obvious fakes
    for pattern in _FAKE_PATTERNS:
        if re.search(pattern, email):
            return False
    return True


# ── helpers ───────────────────────────────────────────────────────────────────

def _add_utm(html: str, utm: dict) -> str:
    """Append UTM params to every http(s) href in the HTML."""
    def _rewrite(match):
        url = match.group(1)
        parsed = urlparse(url)
        qs = parse_qs(parsed.query, keep_blank_values=True)
        for k, v in utm.items():
            qs[k] = [v]
        new_qs = urlencode({k: v[0] for k, v in qs.items()})
        return f'href="{urlunparse(parsed._replace(query=new_qs))}"'
    return re.sub(r'href="(https?://[^"]+)"', _rewrite, html)



TRACKING_BASE = 'https://windowsbyburkhardt.com'


def _inject_pixel(html: str, campaign_id: str, send_id: int) -> str:
    pixel = (
        f'<img src="{TRACKING_BASE}/t/o/{campaign_id}/{send_id}" '
        'width="1" height="1" style="display:none;border:0;" alt="" />'
    )
    if '</body>' in html:
        return html.replace('</body>', pixel + '</body>', 1)
    return html + pixel

def _escape_applescript(s: str) -> str:
    """Escape a string for safe embedding in an AppleScript quoted string."""
    # Strip HTML comments — AppleScript treats -- as a comment delimiter
    s = re.sub(r'<!--.*?-->', '', s, flags=re.DOTALL)
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "").replace("\n", "").replace("\t", " ")


def send_via_applescript(to_email: str, to_name: str, subject: str, html_body: str) -> None:
    """Send an HTML email via the Outlook desktop app using AppleScript."""
    safe_to      = _escape_applescript(to_email)
    safe_name    = _escape_applescript(to_name)
    safe_subject = _escape_applescript(subject)
    safe_html    = _escape_applescript(html_body)

    script = f'''
tell application "Microsoft Outlook"
    set newMessage to make new outgoing message
    set subject of newMessage to "{safe_subject}"
    set content of newMessage to "{safe_html}"
    tell newMessage
        make new to recipient with properties {{email address:{{address:"{safe_to}", name:"{safe_name}"}}}}
    end tell
    send newMessage
end tell
'''
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())


# ── CLI ───────────────────────────────────────────────────────────────────────

@click.command()
@click.option("--campaign", required=True,
              help="Campaign directory name under campaigns/")
@click.option("--query", "sql_query", default=None,
              help="SQL SELECT query against the leads table (prompted if omitted)")
@click.option("--dry-run", is_flag=True,
              help="Render & preview every email without sending")
@click.option("--limit", default=None, type=int,
              help="Max valid unsent leads to process in this batch")
def send(campaign: str, sql_query: str, dry_run: bool, limit: int):
    """Send a campaign via the local Outlook desktop app (AppleScript)."""
    init_db()

    # ── load campaign config ───────────────────────────────────────────────────
    campaign_dir = Path("campaigns") / campaign
    config_path  = campaign_dir / "config.json"
    if not config_path.exists():
        raise click.ClickException(f"config.json not found: {config_path}")

    config      = json.loads(config_path.read_text())
    campaign_id = config.get("id", campaign)

    # ── load Jinja2 templates ─────────────────────────────────────────────────
    env       = Environment(loader=FileSystemLoader(str(campaign_dir)))
    html_tmpl = env.get_template("template.html")

    # ── prompt for query if not provided ─────────────────────────────────────
    if not sql_query:
        click.echo("Enter a SQL query to select leads (e.g. SELECT * FROM leads WHERE state='WI'):")
        sql_query = click.prompt("Query")

    # ── run lead query ────────────────────────────────────────────────────────
    # Strip any LIMIT/OFFSET from the SQL — we control batching via --limit
    clean_query = re.sub(r'\s+limit\s+\d+(\s+offset\s+\d+)?$', '', sql_query.strip(), flags=re.IGNORECASE)

    conn = get_conn()
    try:
        all_leads = conn.execute(clean_query).fetchall()
    except Exception as exc:
        raise click.ClickException(f"Query failed: {exc}")

    if not all_leads:
        click.secho("No leads returned by query.", fg="yellow")
        conn.close()
        return

    # Validate required columns
    if {"first_name", "email"} - set(all_leads[0].keys()):
        raise click.ClickException("Query results missing required columns: first_name, email")

    # ── upsert campaign into DB ───────────────────────────────────────────────
    conn.execute(
        "INSERT INTO campaigns "
        "(id, name, subject, from_email, from_name) VALUES (%s,%s,%s,%s,%s) "
        "ON CONFLICT (id) DO NOTHING",
        (campaign_id, config["name"], config["subject"],
         config["from_email"], config["from_name"])
    )
    conn.commit()

    # ── bulk-fetch already-processed sends for this campaign ─────────────────
    all_lead_ids = [lead["id"] for lead in all_leads]
    done_ids = set()
    if all_lead_ids:
        rows = conn.execute(
            "SELECT lead_id FROM campaign_sends "
            "WHERE campaign_id=%s AND lead_id = ANY(%s) AND status IN ('sent','failed')",
            (campaign_id, all_lead_ids)
        ).fetchall()
        done_ids = {r["lead_id"] for r in rows}

    # ── validate emails and classify leads ────────────────────────────────────
    invalid_leads  = []   # bad/fake email — skip entirely
    fixed_emails   = {}   # lead_id → corrected email
    already_done   = []   # valid email but already sent/failed
    to_send        = []   # valid + unsent — candidates for this batch

    for lead in all_leads:
        raw   = (lead["email"] or "").strip()
        clean = _clean_email(raw)

        if not _is_valid_email(clean):
            invalid_leads.append((lead, raw))
            continue

        if clean != raw.lower():
            fixed_emails[lead["id"]] = (raw, clean)

        if lead["id"] in done_ids:
            already_done.append(lead)
        else:
            to_send.append((lead, clean))

    # ── apply batch limit to valid unsent leads ───────────────────────────────
    if limit:
        to_send = to_send[:limit]

    # ── enroll this batch into campaign_sends ─────────────────────────────────
    for lead, _ in to_send:
        conn.execute(
            "INSERT INTO campaign_sends (campaign_id, lead_id) VALUES (%s,%s) "
            "ON CONFLICT (campaign_id, lead_id) DO NOTHING",
            (campaign_id, lead["id"])
        )
    conn.commit()

    # ── fetch queued rows with full lead data ─────────────────────────────────
    if not to_send:
        pending_rows = []
    else:
        lead_id_list = [lead["id"] for lead, _ in to_send]
        pending_rows = conn.execute(
            """
            SELECT cs.id as send_id, cs.lead_id,
                   l.first_name, l.last_name, l.email, l.city,
                   l.state, l.postal_code, l.phone_primary
            FROM campaign_sends cs
            JOIN leads l ON l.id = cs.lead_id
            WHERE cs.campaign_id = %s AND cs.status = 'queued'
              AND l.id = ANY(%s)
            """,
            (campaign_id, lead_id_list)
        ).fetchall()

    total = len(pending_rows)

    # ── print summary ─────────────────────────────────────────────────────────
    click.echo(f"\n📧  {config['name']}")
    click.echo(f"    From  : {config['from_name']} <{config['from_email']}>")
    click.echo(f"    Via   : Outlook (AppleScript)")
    click.echo(f"    Query : {sql_query[:80]}{'…' if len(sql_query) > 80 else ''}")
    click.echo(f"    Pool  : {len(all_leads)} lead(s) from query")
    if invalid_leads:
        click.secho(f"    Skip  : {len(invalid_leads)} invalid/fake email(s)", fg="yellow")
        for lead, raw in invalid_leads:
            click.secho(f"            ✗ {raw}", fg="yellow")
    if fixed_emails:
        click.secho(f"    Fixed : {len(fixed_emails)} email typo(s)", fg="cyan")
        for _, (raw, clean) in fixed_emails.items():
            click.secho(f"            {raw} → {clean}", fg="cyan")
    if already_done:
        click.echo(f"    Done  : {len(already_done)} already sent/failed — skipped")
    click.echo(f"    Queue : {total} recipient(s) to send{f'  (batch limit: {limit})' if limit else ''}")
    click.echo(f"    Mode  : {'⚠️  DRY RUN — nothing will be sent' if dry_run else '🚀 LIVE'}\n")

    if total == 0:
        click.secho("✅  Nothing to send.", fg="green")
        conn.close()
        return

    if not dry_run:
        click.confirm(f"Send {total} emails now?", abort=True)

    utm_base = {
        "utm_source":   config.get("utm_source",   "email"),
        "utm_medium":   config.get("utm_medium",   "email"),
        "utm_campaign": config.get("utm_campaign", campaign_id),
    }
    sent = failed = 0

    # ── send loop ─────────────────────────────────────────────────────────────
    for i, r in enumerate(pending_rows, 1):
        # Use corrected email if we fixed a typo
        email = fixed_emails.get(r["lead_id"], (None, r["email"]))[1] if r["lead_id"] in fixed_emails else r["email"]

        vars = dict(
            first_name=r["first_name"] or "",
            last_name=r["last_name"] or "",
            email=email,
            city=r["city"] or "",
            state=r["state"] or "",
            postal_code=r["postal_code"] or "",
            phone=r["phone_primary"] or "",
        )

        subject = Template(config["subject"]).render(**vars)
        html    = _add_utm(html_tmpl.render(**vars), utm_base)
        if not dry_run:
            html = _inject_pixel(html, campaign_id, r['send_id'])
        to_name = f"{vars['first_name']} {vars['last_name']}".strip()

        if dry_run:
            click.echo(f"  [{i}/{total}] → {email:40s}  {subject}")
            continue

        try:
            send_via_applescript(
                to_email=email,
                to_name=to_name,
                subject=subject,
                html_body=html,
            )
            conn.execute(
                "UPDATE campaign_sends "
                "SET status='sent', sent_at=NOW() "
                "WHERE id=%s",
                (r["send_id"],)
            )
            conn.commit()
            sent += 1
            click.secho(f"  [{i}/{total}] ✓  {email}", fg="green")

        except Exception as exc:
            conn.execute(
                "UPDATE campaign_sends SET status='failed', failed_reason=%s WHERE id=%s",
                (str(exc), r["send_id"])
            )
            conn.commit()
            failed += 1
            click.secho(f"  [{i}/{total}] ✗  {email} — {exc}", fg="red", err=True)

        time.sleep(1)

    conn.close()
    color = "green" if failed == 0 else "yellow"
    click.secho(
        f"\n{'✅' if not failed else '⚠️ '} Sent {sent}  |  Failed {failed}  |  Total {total}\n",
        fg=color
    )


if __name__ == "__main__":
    send()
