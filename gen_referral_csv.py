"""
gen_referral_csv.py — build a Mailchimp-ready SMS CSV for the $500 referral
campaign, targeting sold customers (lead_statuses.name = 'sold', id 10).

Each row gets a unique /r/<code> referral link (decoded by the website at
/r/:code, which credits the right customer their $500 when a referral converts).
Phones are normalized to E.164 (+1XXXXXXXXXX, required by Mailchimp SMS); rows
with no valid mobile are dropped, and duplicate phones are de-duped so nobody is
texted twice.

Usage:
    python3 gen_referral_csv.py                       # sold leads -> mailchimp_referral_sms.csv
    python3 gen_referral_csv.py --status 10 --out x.csv
    python3 gen_referral_csv.py --https               # use https:// links instead of bare domain
"""

import argparse
import csv as csvmod
import os
import re
import sys

# Self-heal "ModuleNotFoundError: No module named 'psycopg2'": if the current
# interpreter lacks the project deps but the venv next to this file has them,
# re-exec with the venv's python. Mirrors warm_leads.py.
def _ensure_venv():
    try:
        import psycopg2  # noqa: F401
        return
    except ModuleNotFoundError:
        pass
    # Re-exec with the project venv's python. Env sentinel (not a path compare)
    # prevents an infinite loop: realpath() collapses the venv symlink onto its
    # base interpreter, wrongly skipping the re-exec when your shell python is
    # the same base the venv was built from.
    if os.environ.get("_GEN_REFERRAL_REEXEC") != "1":
        venv_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "venv", "bin", "python3")
        if os.path.exists(venv_py):
            os.environ["_GEN_REFERRAL_REEXEC"] = "1"
            os.execv(venv_py, [venv_py, os.path.abspath(__file__), *sys.argv[1:]])
    sys.exit("psycopg2 is not installed in the venv. Recreate it:\n"
             "  python3 -m venv venv && venv/bin/pip install -r requirements.txt")

_ensure_venv()

from db import get_conn
from drip import _referral_code

HOST = "windowsbyburkhardt.com"  # bare domain (http->https + HSTS verified)


def to_e164(*phones):
    """First phone that is a valid 10-digit NANP number -> +1XXXXXXXXXX, else None."""
    for p in phones:
        if not p:
            continue
        d = re.sub(r"\D", "", p)
        if len(d) == 11 and d.startswith("1"):
            d = d[1:]
        if len(d) == 10 and d[0] in "23456789":
            return "+1" + d
    return None


def main():
    ap = argparse.ArgumentParser(description="Mailchimp SMS CSV for the referral campaign.")
    ap.add_argument("--status", type=int, default=10, help="lead_statuses.id to target (default 10 = sold).")
    ap.add_argument("--out", default="mailchimp_referral_sms.csv", help="Output CSV path.")
    ap.add_argument("--https", action="store_true", help="Emit https:// links instead of bare domain.")
    args = ap.parse_args()

    conn = get_conn()
    rows = conn.execute("""
        SELECT id, first_name, last_name, email, phone_primary, phone_secondary
        FROM leads
        WHERE status_id = %s
          AND unsubscribed_at IS NULL
          AND bounced_at IS NULL
        ORDER BY id
    """, (args.status,)).fetchall()
    conn.close()

    seen_phone = set()
    written = no_phone = invalid = dupe = 0
    with open(args.out, "w", newline="") as f:
        w = csvmod.writer(f)
        w.writerow(["Email Address", "First Name", "Last Name", "Phone Number", "REFURL"])
        for r in rows:
            phone = to_e164(r["phone_primary"], r["phone_secondary"])
            if phone is None:
                if r["phone_primary"] or r["phone_secondary"]:
                    invalid += 1
                else:
                    no_phone += 1
                continue
            if phone in seen_phone:
                dupe += 1
                continue
            seen_phone.add(phone)
            link = f"{HOST}/r/{_referral_code(r['id'])}"
            if args.https:
                link = "https://" + link
            w.writerow([r["email"] or "", r["first_name"] or "", r["last_name"] or "", phone, link])
            written += 1

    print(f"sold leads (sendable):           {len(rows)}")
    print(f"  written (valid, unique phone): {written}")
    print(f"  dropped - no phone:            {no_phone}")
    print(f"  dropped - invalid phone:       {invalid}")
    print(f"  dropped - duplicate phone:     {dupe}")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
