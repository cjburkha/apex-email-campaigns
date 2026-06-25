"""
warm_leads.py — build a phone follow-up list ranked by recent email engagement.

Pulls leads who clicked OR opened a drip email within the last N days, scores
them, and prints a call list with name + phone. Clickers rank first (a click is
a deliberate, high-intent action); people who only opened are included too,
ranked lower. Excludes unsubscribed and bounced leads and anyone with no phone.

Two signals, two tables (both written by the live windowsbyburkhardt.com site):
  - clicks → campaign_click_events  (high intent, reliable)
  - opens  → campaign_open_events   (soft signal; inflated by Apple Mail Privacy
             Protection / inbox prefetch, so we score on distinct weeks opened
             rather than raw open count)
Note: campaign_sends.opened_at / clicked_at are dead columns — nothing writes
them; all engagement lives in the two *_events tables above.

Usage:
    python3 warm_leads.py                  # last 14 days, top 100
    python3 warm_leads.py --days 30        # wider window
    python3 warm_leads.py --limit 50       # cap the list
    python3 warm_leads.py --clicked-only   # only people who clicked
    python3 warm_leads.py --csv warm.csv   # also write a CSV
"""

import argparse
import csv as csvmod
import os
import sys

# Self-heal the common "ModuleNotFoundError: No module named 'psycopg2'" error:
# if we're running under an interpreter that lacks the project's deps but the
# venv next to this file has them, re-exec ourselves with the venv's python.
def _ensure_venv():
    try:
        import psycopg2  # noqa: F401
        return
    except ModuleNotFoundError:
        pass
    # Re-exec with the project venv's python. Use an env sentinel (not a path
    # comparison) to prevent an infinite loop: realpath() collapses the venv
    # symlink onto its base interpreter, so comparing paths wrongly skips the
    # re-exec when your shell python is the same base the venv was built from.
    if os.environ.get("_WARM_LEADS_REEXEC") != "1":
        venv_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "venv", "bin", "python3")
        if os.path.exists(venv_py):
            os.environ["_WARM_LEADS_REEXEC"] = "1"
            os.execv(venv_py, [venv_py, os.path.abspath(__file__), *sys.argv[1:]])
    sys.exit("psycopg2 is not installed in the venv. Recreate it:\n"
             "  python3 -m venv venv && venv/bin/pip install -r requirements.txt")

_ensure_venv()

from db import get_conn


def fetch_warm_leads(days: int, limit: int, clicked_only: bool,
                     min_opens: int = 0, min_days: int = 0):
    conn = get_conn()

    sql = """
        WITH open_days AS (
            -- distinct calendar days a lead opened on. We only have two stamps
            -- per weekly email (first open = opened_at, latest = last_open_at),
            -- so this is a lower bound, but it's enough to tell a one-shot
            -- scanner/prefetch (1 day) from someone who came back another day.
            SELECT lead_id, COUNT(DISTINCT d) AS open_days
            FROM campaign_open_events o
            CROSS JOIN LATERAL (VALUES
                (o.opened_at::date),
                (COALESCE(o.last_open_at, o.opened_at)::date)
            ) v(d)
            GROUP BY lead_id
        ),
        opens AS (
            SELECT o.lead_id,
                   SUM(o.hits)                                AS open_hits,
                   COUNT(DISTINCT o.week)                     AS open_weeks,
                   d.open_days                                AS open_days,
                   MAX(COALESCE(o.last_open_at, o.opened_at)) AS last_open_at
            FROM campaign_open_events o
            JOIN open_days d ON d.lead_id = o.lead_id
            GROUP BY o.lead_id, d.open_days
        ),
        clicks AS (
            SELECT lead_id,
                   SUM(hits)                                AS click_hits,
                   MAX(COALESCE(last_click_at, clicked_at)) AS last_click_at
            FROM campaign_click_events
            GROUP BY lead_id
        ),
        eng AS (
            SELECT
                COALESCE(c.lead_id, o.lead_id)              AS lead_id,
                COALESCE(c.click_hits, 0)                   AS click_hits,
                COALESCE(o.open_hits, 0)                    AS open_hits,
                COALESCE(o.open_weeks, 0)                   AS open_weeks,
                COALESCE(o.open_days, 0)                    AS open_days,
                GREATEST(
                    COALESCE(c.last_click_at, 'epoch'::timestamptz),
                    COALESCE(o.last_open_at,  'epoch'::timestamptz)
                )                                           AS last_activity_at
            FROM clicks c
            FULL OUTER JOIN opens o ON o.lead_id = c.lead_id
        )
        SELECT
            l.id,
            l.first_name,
            l.last_name,
            l.business_name,
            l.email,
            l.phone_primary,
            l.phone_secondary,
            e.click_hits,
            e.open_hits,
            e.open_weeks,
            e.open_days,
            e.last_activity_at,
            -- clicks dominate; opens (by distinct day opened) are a soft
            -- tiebreaker; recent activity gets a small bonus inside the window.
            (e.click_hits * 10
             + e.open_days * 2
             + GREATEST(0, %(days)s - EXTRACT(DAY FROM NOW() - e.last_activity_at))
            ) AS score
        FROM eng e
        JOIN leads l ON l.id = e.lead_id
        WHERE l.unsubscribed_at IS NULL
          AND l.bounced_at IS NULL
          AND COALESCE(l.phone_primary, l.phone_secondary) IS NOT NULL
          AND e.last_activity_at >= NOW() - (%(days)s || ' days')::interval
    """
    if clicked_only:
        sql += "          AND e.click_hits > 0\n"
    else:
        # Quality bar for open-only leads. Clickers always pass (high intent);
        # open-only leads must clear both thresholds. min=0 is a no-op.
        sql += ("          AND (e.click_hits > 0\n"
                "               OR (e.open_hits >= %(min_opens)s "
                "AND e.open_days >= %(min_days)s))\n")
    sql += """
        ORDER BY score DESC, e.last_activity_at DESC
        LIMIT %(limit)s
    """
    cur = conn.execute(sql, {"days": days, "limit": limit,
                             "min_opens": min_opens, "min_days": min_days})
    rows = cur.fetchall()
    conn.close()
    return rows


def fmt_phone(p):
    if not p:
        return ""
    d = "".join(ch for ch in p if ch.isdigit())
    if len(d) == 10:
        return f"{d[0:3]}-{d[3:6]}-{d[6:]}"
    return p


def main():
    ap = argparse.ArgumentParser(description="Warm phone follow-up list by email engagement.")
    ap.add_argument("--days", type=int, default=14, help="Lookback window in days (default 14).")
    ap.add_argument("--limit", type=int, default=100, help="Max leads to return (default 100).")
    ap.add_argument("--clicked-only", action="store_true", help="Only leads who clicked (hide open-only).")
    ap.add_argument("--min-opens", type=int, default=0,
                    help="Open-only leads must have at least this many total opens (clickers always pass).")
    ap.add_argument("--min-days", type=int, default=0,
                    help="Open-only leads must have opened on at least this many distinct days (clickers always pass).")
    ap.add_argument("--csv", help="Optional path to also write the list as CSV.")
    args = ap.parse_args()

    rows = fetch_warm_leads(args.days, args.limit, args.clicked_only,
                            args.min_opens, args.min_days)

    if not rows:
        # Not an error — just an empty window. Report how far back the most
        # recent engagement is so the user knows what --days to try.
        conn = get_conn()
        cur = conn.execute("""
            SELECT MAX(ts) AS last_activity,
                   EXTRACT(DAY FROM NOW() - MAX(ts))::int AS days_ago
            FROM (
                SELECT COALESCE(last_click_at, clicked_at) AS ts FROM campaign_click_events
                UNION ALL
                SELECT COALESCE(last_open_at,  opened_at)  AS ts FROM campaign_open_events
            ) t
        """)
        info = cur.fetchone()
        conn.close()
        kind = "clicks" if args.clicked_only else "engagement"
        print(f"\nNo {kind} in the last {args.days} days — nothing to call yet. "
              "(This is normal, not an error.)")
        if info and info["last_activity"]:
            d = info["days_ago"]
            print(f"Most recent activity was {info['last_activity'].strftime('%Y-%m-%d')} "
                  f"(~{d} days ago).")
            print(f"Try a wider window, e.g.:  python3 warm_leads.py --days {max(d + 1, 14)}")
        return

    scope = "clicked" if args.clicked_only else "clicked or opened"
    print(f"\n{len(rows)} warm leads ({scope}, last {args.days} days), best first:\n")
    print(f"{'#':>3}  {'Name':<24} {'Phone':<14} {'Signal':<10} {'Clk':>3} {'Opn':>3} {'Days':>4} "
          f"{'Last activity':<16} Email")
    print("-" * 114)
    for i, r in enumerate(rows, 1):
        name = " ".join(x for x in [r["first_name"], r["last_name"]] if x) or (r["business_name"] or "—")
        phone = fmt_phone(r["phone_primary"] or r["phone_secondary"])
        signal = "CLICKED" if r["click_hits"] else "open-only"
        last = r["last_activity_at"].strftime("%Y-%m-%d %H:%M") if r["last_activity_at"] else ""
        print(f"{i:>3}  {name[:24]:<24} {phone:<14} {signal:<10} "
              f"{r['click_hits']:>3} {r['open_hits']:>3} {r['open_days']:>4} {last:<16} {r['email'] or ''}")

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csvmod.writer(f)
            w.writerow(["rank", "first_name", "last_name", "business_name", "phone", "email",
                        "signal", "click_hits", "open_hits", "open_weeks", "open_days",
                        "last_activity_at", "score"])
            for i, r in enumerate(rows, 1):
                w.writerow([i, r["first_name"], r["last_name"], r["business_name"],
                            fmt_phone(r["phone_primary"] or r["phone_secondary"]), r["email"],
                            "clicked" if r["click_hits"] else "open-only",
                            r["click_hits"], r["open_hits"], r["open_weeks"], r["open_days"],
                            r["last_activity_at"], r["score"]])
        print(f"\nWrote CSV → {args.csv}")


if __name__ == "__main__":
    main()
