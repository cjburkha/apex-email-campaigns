#!/usr/bin/env python3
"""
send.py — Send a campaign to leads selected by a SQL query.

Usage:
    python send.py --campaign window-inspection --query "SELECT * FROM leads WHERE state='WI'"
    python send.py --campaign window-inspection --query "SELECT * FROM leads WHERE state='WI'" --dry-run
    python send.py --campaign window-inspection --query "SELECT * FROM leads WHERE state='WI'" --limit 5

The query must SELECT from the leads table (or any query that returns leads columns).
Required lead columns : first_name, email
Useful lead columns   : last_name, city  (used as template variables)

Each (campaign, lead) pair is tracked in campaign_sends. Running the same command
twice will skip leads already queued/sent — safe to re-run after a partial failure.
"""

import hashlib
import hmac
import json
import os
import re
import time
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import boto3
import click
from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader, Template

from db import get_conn, init_db

load_dotenv()


# ── helpers ───────────────────────────────────────────────────────────────────

# Path pattern for shortlink slugs (e.g. /spring2026-em-1). When a URL's path
# matches this, UTM tagging is skipped — the slug-redirect adds the right UTMs
# server-side, and inline tagging would bloat SMS char counts and double-encode
# attribution in HTML.
_SLUG_PATH_RE = re.compile(r'^/[a-z0-9]+(?:-[a-z0-9]+)+$')


def _add_utm(html: str, utm: dict) -> str:
    """Append UTM params to every http(s) href in the HTML.

    Slug-shaped paths (handled by the redirect server) are skipped so we don't
    double-tag attribution.
    """
    def _rewrite(match):
        url = match.group(1)
        parsed = urlparse(url)
        if _SLUG_PATH_RE.match(parsed.path):
            return f'href="{url}"'
        qs = parse_qs(parsed.query, keep_blank_values=True)
        for k, v in utm.items():
            qs[k] = [v]
        new_qs = urlencode({k: v[0] for k, v in qs.items()})
        return f'href="{urlunparse(parsed._replace(query=new_qs))}"'
    return re.sub(r'href="(https?://[^"]+)"', _rewrite, html)


def _add_utm_text(text: str, utm: dict) -> str:
    """Append UTM params to every bare http(s) URL in plain text / SMS.

    Slug-shaped paths are skipped (see _add_utm).
    """
    def _rewrite(match):
        url = match.group(0)
        # strip trailing punctuation likely not part of the URL
        trailing = ""
        while url and url[-1] in '.,;:!?)]}>"\'':
            trailing = url[-1] + trailing
            url = url[:-1]
        parsed = urlparse(url)
        if _SLUG_PATH_RE.match(parsed.path):
            return url + trailing
        qs = parse_qs(parsed.query, keep_blank_values=True)
        for k, v in utm.items():
            qs[k] = [v]
        new_qs = urlencode({k: v[0] for k, v in qs.items()})
        return urlunparse(parsed._replace(query=new_qs)) + trailing
    return re.sub(r'https?://[^\s<>"\']+', _rewrite, text)

def _normalize_phone(phone: str) -> str:
    if not phone:
        return ""
    phone = re.sub(r"[^0-9+]", "", phone)
    if phone.startswith("+"):
        return phone
    if len(phone) == 10:
        return f"+1{phone}"
    if len(phone) == 11 and phone.startswith("1"):
        return f"+{phone}"
    return phone


def _make_unsubscribe_token(lead_id: int, email: str) -> str:
    secret = os.getenv("UNSUBSCRIBE_SECRET", "change-me").encode("utf-8")
    return hmac.new(secret, f"{lead_id}:{email}".encode("utf-8"), hashlib.sha256).hexdigest()


def _make_pixel_token(campaign_id: str, lead_id: int, week: int) -> str:
    secret = os.getenv("PIXEL_SECRET", os.getenv("UNSUBSCRIBE_SECRET", "change-me")).encode("utf-8")
    msg = f"{campaign_id}:{lead_id}:{week}".encode("utf-8")
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()[:16]


def _pixel_url(campaign_id: str, lead_id: int, week: int) -> str:
    base = os.getenv("PIXEL_BASE_URL", "https://windowsbyburkhardt.com").rstrip("/")
    tok = _make_pixel_token(campaign_id, lead_id, week)
    return f"{base}/t/o/{campaign_id}/{lead_id}/{week}/{tok}"


def _pixel_html(campaign_id: str, lead_id: int, week: int) -> str:
    return (
        f'<img src="{_pixel_url(campaign_id, lead_id, week)}" '
        'width="1" height="1" style="display:none;border:0;" alt="" />'
    )


def _click_url(campaign_id: str, lead_id: int, week: int) -> str:
    """Per-lead email click URL. Same token scheme as the open pixel; the website
    /t/c route records who clicked and redirects to the campaign/week destination."""
    base = os.getenv("PIXEL_BASE_URL", "https://windowsbyburkhardt.com").rstrip("/")
    tok = _make_pixel_token(campaign_id, lead_id, week)
    return f"{base}/t/c/{campaign_id}/{lead_id}/{week}/{tok}"


# ── HTML → plain text fallback ───────────────────────────────────────────────
# Used to auto-generate a text/plain part for SES multipart/alternative when a
# campaign step ships only an .html template. Tuned for the table-based email
# layouts we use (header / hero p / <tr><td>✓ bullet</td></tr> / CTA <a>).
class _HtmlToText(HTMLParser):
    _BLOCK    = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "blockquote", "li"}
    _SKIP     = {"style", "script", "head", "title"}
    _INVISIBLE = {"img"}  # tracking pixel etc.

    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip_depth = 0
        self._href = None

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self.skip_depth += 1
            return
        if tag in self._INVISIBLE:
            # Surface the image's alt text (e.g. brand logo) so plaintext
            # readers and image-blocked clients still see the branding.
            # Tracking pixels use alt="" so they remain silent.
            alt = dict(attrs).get("alt", "").strip()
            if alt:
                self.parts.append(alt)
            return
        if tag == "br":
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("  • ")
        elif tag == "a":
            self._href = dict(attrs).get("href")

    def handle_endtag(self, tag):
        if tag in self._SKIP:
            self.skip_depth -= 1
            return
        if tag == "a" and self._href and self._href.startswith("http"):
            # Render <a>Schedule</a> → "Schedule (https://…)"
            self.parts.append(f" ({self._href})")
            self._href = None
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.skip_depth == 0:
            self.parts.append(data)


def _html_to_text(html: str) -> str:
    """Render HTML to a readable plain-text fallback. Stdlib only, no deps."""
    p = _HtmlToText()
    p.feed(html)
    text = unescape("".join(p.parts))
    # Replace ✓ glyph (visual checkmark in bullets) with a plain bullet
    text = text.replace("✓", "•")
    # The HTML formatting puts whitespace+newlines between <span>✓</span> and the
    # bullet text — collapse those so each bullet is on a single line.
    text = re.sub(r"•\s*\n\s*", "• ", text)
    # Per-line strip + intra-line whitespace collapse
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    # Collapse runs of blank lines to a single blank line
    out = []
    blank = False
    for ln in lines:
        if not ln:
            if not blank:
                out.append("")
            blank = True
        else:
            out.append(ln)
            blank = False
    return "\n".join(out).strip() + "\n"


def _unsubscribe_url(lead_id: int, token: str) -> str:
    base = os.getenv("UNSUBSCRIBE_BASE_URL", "https://windowsbyburkhardt.com/unsubscribe")
    return f"{base}?id={lead_id}&t={token}"


def _send_sms(phone_number: str, message: str):
    """Send an SMS via Mailchimp Transactional (Mandrill) or AWS Pinpoint
    depending on SMS_PROVIDER env var. Returns (message_id, status)."""
    provider = os.getenv("SMS_PROVIDER", "mailchimp").lower()

    if provider == "mailchimp":
        import requests  # noqa: PLC0415
        api_key = os.getenv("MAILCHIMP_TRANSACTIONAL_API_KEY")
        from_number = os.getenv("SMS_ORIGINATING_NUMBER")
        if not api_key or not from_number:
            raise RuntimeError("MAILCHIMP_TRANSACTIONAL_API_KEY and SMS_ORIGINATING_NUMBER must be set in .env")
        resp = requests.post(
            "https://mandrillapp.com/api/1.1/messages/send-sms",
            json={
                "key": api_key,
                "message": {
                    "sms": {
                        "text":    message,
                        "to":      phone_number,
                        "from":    from_number,
                        "consent": os.getenv("MAILCHIMP_SMS_CONSENT", "recurring"),
                    }
                },
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        # Mandrill returns a list of results on success, or an error dict on failure.
        if isinstance(data, dict) and data.get("status") == "error":
            raise RuntimeError(f"Mandrill error {data.get('name')}: {data.get('message')}")
        result = data[0] if isinstance(data, list) and data else {}
        return result.get("_id"), result.get("status")

    # provider == "pinpoint"
    application_id = os.getenv("PINPOINT_APPLICATION_ID")
    if not application_id:
        raise RuntimeError("PINPOINT_APPLICATION_ID is not set in .env")
    pinpoint = boto3.client("pinpoint", region_name=os.getenv("AWS_REGION", "us-east-1"))
    body = {"Body": message, "MessageType": "TRANSACTIONAL"}
    sender_id = os.getenv("SMS_SENDER_ID")
    if sender_id:
        body["SenderId"] = sender_id
    origination_number = os.getenv("SMS_ORIGINATING_NUMBER")
    if origination_number:
        body["OriginationNumber"] = origination_number
    response = pinpoint.send_messages(
        ApplicationId=application_id,
        MessageRequest={
            "Addresses": {phone_number: {"ChannelType": "SMS"}},
            "MessageConfiguration": {"SMSMessage": body},
        },
    )
    result = response["MessageResponse"]["Result"][phone_number]
    return result.get("MessageId"), result.get("Status")

# ── CLI ───────────────────────────────────────────────────────────────────────

@click.command()
@click.option("--campaign", required=True,
              help="Campaign directory name under campaigns/")
@click.option("--query", "sql_query", default=None,
              help="SQL SELECT query against the leads table (prompted if omitted)")
@click.option("--dry-run", is_flag=True,
              help="Render & preview every email without sending")
@click.option("--rate", default=14, type=float, show_default=True,
              help="Emails per second (14 = SES production max, 1 = sandbox)")
@click.option("--limit", default=None, type=int,
              help="Cap the number of leads processed (handy for test batches)")
def send(campaign: str, sql_query: str, dry_run: bool, rate: float, limit: int):
    """Send a campaign to leads selected by a SQL query."""
    init_db()

    # ── load campaign config ───────────────────────────────────────────────────
    campaign_dir = Path("campaigns") / campaign
    config_path  = campaign_dir / "config.json"
    if not config_path.exists():
        raise click.ClickException(f"config.json not found: {config_path}")

    config      = json.loads(config_path.read_text())
    campaign_id = config.get("id", campaign)

    # ── load Jinja2 templates ─────────────────────────────────────────────────
    env             = Environment(loader=FileSystemLoader(str(campaign_dir)))
    html_tmpl       = env.get_template("template.html")
    txt_tmpl        = env.get_template("template.txt")
    sms_template    = campaign_dir / "template.sms.txt"
    sms_tmpl        = env.get_template("template.sms.txt") if sms_template.exists() else None

    # ── prompt for query if not provided ─────────────────────────────────────
    if not sql_query:
        click.echo("Enter a SQL query to select leads (e.g. SELECT * FROM leads WHERE state='WI' LIMIT 50):")
        sql_query = click.prompt("Query")

    # ── run lead query ────────────────────────────────────────────────────────
    conn = get_conn()
    try:
        leads = conn.execute(sql_query).fetchall()
    except Exception as exc:
        raise click.ClickException(f"Query failed: {exc}")

    if not leads:
        click.secho("No leads returned by query.", fg="yellow")
        conn.close()
        return

    if limit:
        leads = leads[:limit]

    # Validate required columns
    lead_keys = leads[0].keys()
    missing = {"first_name", "email"} - set(lead_keys)
    if missing:
        raise click.ClickException(f"Query results missing required columns: {missing}")

    # ── upsert campaign into DB ───────────────────────────────────────────────
    conn.execute(
        "INSERT INTO campaigns "
        "(id, name, subject, from_email, from_name) VALUES (%s,%s,%s,%s,%s) "
        "ON CONFLICT (id) DO NOTHING",
        (campaign_id, config["name"], config["subject"],
         config["from_email"], config["from_name"])
    )

    # ── enroll leads into campaign_sends (idempotent) ────────────────────────
    new_count = 0
    for lead in leads:
        if lead.get("unsubscribed_at") is not None:
            continue
        result = conn.execute(
            "INSERT INTO campaign_sends (campaign_id, lead_id) VALUES (%s,%s) "
            "ON CONFLICT (campaign_id, lead_id) DO NOTHING",
            (campaign_id, lead["id"])
        )
        if result.rowcount:
            new_count += 1
    conn.commit()

    already = len(leads) - new_count
    if already:
        click.echo(f"  ℹ  {already} lead(s) already enrolled in this campaign — skipped")

    # ── fetch only queued sends ───────────────────────────────────────────────
    pending_rows = conn.execute(
        """
        SELECT cs.id as send_id, cs.lead_id,
               l.first_name, l.last_name, l.email, l.city,
               l.state, l.postal_code, l.phone_primary, l.phone_secondary
        FROM campaign_sends cs
        JOIN leads l ON l.id = cs.lead_id
        WHERE cs.campaign_id = %s AND cs.status = 'queued' AND l.unsubscribed_at IS NULL
              AND l.bounced_at IS NULL
        """,
        (campaign_id,)
    ).fetchall()

    total = len(pending_rows)
    click.echo(f"\n📧  {config['name']}")
    click.echo(f"    From  : {config['from_name']} <{config['from_email']}>")
    click.echo(f"    Query : {sql_query[:80]}{'…' if len(sql_query) > 80 else ''}")
    click.echo(f"    Queue : {total} recipient(s) to send")
    click.echo(f"    Mode  : {'⚠️  DRY RUN — nothing will be sent' if dry_run else '🚀 LIVE'}\n")

    if total == 0:
        click.secho("✅  Nothing to send — all matched leads already processed for this campaign.", fg="green")
        conn.close()
        return

    if not dry_run:
        click.confirm(f"Send {total} emails now?", abort=True)

    # ── SES client ────────────────────────────────────────────────────────────
    ses        = boto3.client("sesv2", region_name=os.getenv("AWS_REGION", "us-east-1"))
    config_set = os.getenv("SES_CONFIG_SET", "apex-campaigns")
    utm_base             = {
        "utm_source":   config.get("utm_source",   "email"),
        "utm_medium":   config.get("utm_medium",   "email"),
        "utm_campaign": config.get("utm_campaign", campaign_id),
    }
    interval = 1.0 / rate
    sent = failed = 0

    # ── send loop ─────────────────────────────────────────────────────────────
    for i, r in enumerate(pending_rows, 1):
        vars = dict(
            first_name=r["first_name"] or "",
            last_name=r["last_name"] or "",
            email=r["email"],
            city=r["city"] or "",
            state=r["state"] or "",
            postal_code=r["postal_code"] or "",
            phone=(r.get("phone_primary") or r.get("phone_secondary") or ""),
        )

        subject = Template(config["subject"]).render(**vars)
        html    = _add_utm(html_tmpl.render(**vars), utm_base)
        txt     = _add_utm_text(txt_tmpl.render(**vars), utm_base)
        phone   = _normalize_phone(vars["phone"])
        sms_body = _add_utm_text(sms_tmpl.render(**vars), utm_base) if sms_tmpl and phone else None
        unsubscribe_tok = _make_unsubscribe_token(r["lead_id"], vars["email"])
        unsubscribe_url = _unsubscribe_url(r["lead_id"], unsubscribe_tok)
        reply_to = config.get("reply_to", config["from_email"])

        if dry_run:
            channels = ["email" if vars["email"] else None, "sms" if sms_body else None]
            channels = ", ".join([c for c in channels if c]) or "none"
            click.echo(f"  [{i}/{total}] → {r.get('email') or phone:40s}  {subject}  ({channels})")
            continue

        headers = [
            {
                "Name": "List-Unsubscribe",
                "Value": f"<mailto:{reply_to}?subject=unsubscribe>, <{unsubscribe_url}>",
            },
            {
                "Name": "List-Unsubscribe-Post",
                "Value": "List-Unsubscribe=One-Click",
            },
        ]

        email_sent = False
        sms_sent = False
        sms_message_id = None
        sms_status = None
        errors = []
        message_id = None

        if vars["email"]:
            try:
                resp = ses.send_email(
                    FromEmailAddress=f"{config['from_name']} <{config['from_email']}>",
                    Destination={"ToAddresses": [vars["email"]]},
                    ReplyToAddresses=[reply_to],
                    Content={
                        "Simple": {
                            "Subject": {"Data": subject, "Charset": "UTF-8"},
                            "Body": {
                                "Text": {"Data": txt,  "Charset": "UTF-8"},
                                "Html": {"Data": html, "Charset": "UTF-8"},
                            },
                            "Headers": headers,
                        }
                    },
                    ConfigurationSetName=config_set,
                )
                email_sent = True
                message_id = resp["MessageId"]
            except Exception as exc:
                errors.append(f"email: {exc}")

        if sms_body:
            try:
                sms_message_id, sms_status = _send_sms(phone, sms_body)
                sms_sent = True
            except Exception as exc:
                errors.append(f"sms: {exc}")

        if not vars["email"] and not sms_body:
            errors.append("no email or SMS contact available")

        if not email_sent and not sms_sent:
            conn.execute(
                "UPDATE campaign_sends SET status='failed', failed_reason=%s WHERE id=%s",
                ("; ".join(errors), r["send_id"])
            )
            conn.commit()
            failed += 1
            click.secho(f"  [{i}/{total}] ✗  {r.get('email') or phone} — {'; '.join(errors)}", fg="red", err=True)
        else:
            update_sql = ["status='sent'", "sent_at=NOW()", "message_id=%s"]
            update_params = [message_id]
            if sms_message_id:
                update_sql.append("sms_message_id=%s")
                update_sql.append("sms_sent_at=NOW()")
                update_sql.append("sms_status=%s")
                update_params.extend([sms_message_id, sms_status])
            update_params.append(r["send_id"])
            conn.execute(
                f"UPDATE campaign_sends SET {', '.join(update_sql)} WHERE id=%s",
                tuple(update_params)
            )
            conn.commit()
            sent += 1
            contact_summary = ", ".join(c for c in ["email" if email_sent else None, "sms" if sms_sent else None] if c)
            click.secho(f"  [{i}/{total}] ✓  {r.get('email') or phone}  ({contact_summary})", fg="green")

        time.sleep(interval)

    conn.close()
    color = "green" if failed == 0 else "yellow"
    click.secho(
        f"\n{'✅' if not failed else '⚠️ '} Sent {sent}  |  Failed {failed}  |  Total {total}\n",
        fg=color
    )


if __name__ == "__main__":
    send()
