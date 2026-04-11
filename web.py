#!/usr/bin/env python3
"""
web.py — Local web interface for querying the leads database.

Usage:
    python web.py
    open http://localhost:5000
"""

import base64

from flask import Flask, render_template_string, request, Response
from db import get_conn, init_db

app = Flask(__name__)

PAGE_SIZE = 100

HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Apex Leads</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, sans-serif; font-size: 13px; background: #f5f5f5; }
  header { background: #1a1a2e; color: #fff; padding: 14px 20px; display: flex; align-items: center; gap: 16px; }
  header h1 { font-size: 16px; font-weight: 600; }
  .tag { background: #e94560; color: #fff; font-size: 11px; padding: 2px 8px; border-radius: 10px; }
  form { background: #fff; padding: 12px 20px; display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-end; border-bottom: 1px solid #ddd; }
  form label { display: flex; flex-direction: column; gap: 3px; font-weight: 500; color: #444; }
  input, select { padding: 6px 10px; border: 1px solid #ccc; border-radius: 4px; font-size: 13px; }
  input { width: 180px; }
  button { padding: 7px 18px; background: #1a1a2e; color: #fff; border: none; border-radius: 4px; cursor: pointer; font-size: 13px; }
  button:hover { background: #e94560; }
  .stats { padding: 10px 20px; color: #666; font-size: 12px; }
  .wrap { padding: 0 20px 20px; overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; background: #fff; border-radius: 6px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
  th { background: #1a1a2e; color: #fff; text-align: left; padding: 8px 10px; font-weight: 500; white-space: nowrap; }
  td { padding: 7px 10px; border-bottom: 1px solid #eee; white-space: nowrap; max-width: 220px; overflow: hidden; text-overflow: ellipsis; }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: #f0f4ff; }
  .status { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; }
  .s-sold { background:#d4edda; color:#155724; }
  .s-other { background:#fff3cd; color:#856404; }
  .s-canceled { background:#f8d7da; color:#721c24; }
  .pagination { padding: 10px 20px; display: flex; gap: 8px; align-items: center; }
  .pagination a { padding: 5px 12px; background:#fff; border:1px solid #ccc; border-radius:4px; text-decoration:none; color:#333; }
  .pagination a:hover { background:#1a1a2e; color:#fff; }
  .pagination .cur { padding: 5px 12px; background:#1a1a2e; color:#fff; border-radius:4px; }
</style>
</head>
<body>
<header>
  <h1>Apex Leads</h1>
  <span class="tag">{{ "{:,}".format(total_count) }} total</span>
</header>

<form method="get">
  <label>Search name / email
    <input name="q" value="{{ q }}" placeholder="name or email…">
  </label>
  <label>Status
    <select name="status">
      <option value="">All statuses</option>
      {% for s in statuses %}
      <option value="{{ s }}" {% if s == status %}selected{% endif %}>{{ s }}</option>
      {% endfor %}
    </select>
  </label>
  <label>State
    <select name="state">
      <option value="">All states</option>
      {% for st in states %}
      <option value="{{ st }}" {% if st == state %}selected{% endif %}>{{ st }}</option>
      {% endfor %}
    </select>
  </label>
  <label>City
    <input name="city" value="{{ city }}" placeholder="city…">
  </label>
  <button type="submit">Filter</button>
  <a href="/" style="padding:7px 14px;color:#666;text-decoration:none;">Clear</a>
</form>

<div class="stats">Showing {{ rows|length }} of {{ "{:,}".format(filtered_count) }} results (page {{ page }} of {{ total_pages }})</div>

<div class="wrap">
<table>
  <thead><tr>
    <th>#</th><th>First</th><th>Last</th><th>Email</th><th>Phone</th>
    <th>City</th><th>State</th><th>Postal</th><th>Status</th><th>Source</th>
  </tr></thead>
  <tbody>
  {% for r in rows %}
  {% set sc = 's-sold' if r.status == 'sold' else ('s-canceled' if 'Cancel' in (r.status or '') else 's-other') %}
  <tr>
    <td style="color:#999">{{ r.id }}</td>
    <td>{{ r.first_name or '' }}</td>
    <td>{{ r.last_name or '' }}</td>
    <td>{{ r.email or '' }}</td>
    <td>{{ r.phone_primary or '' }}</td>
    <td>{{ r.city or '' }}</td>
    <td>{{ r.state or '' }}</td>
    <td>{{ r.postal_code or '' }}</td>
    <td><span class="status {{ sc }}">{{ r.status or '' }}</span></td>
    <td style="color:#999;font-size:11px">{{ r.source_file.replace('data/','') if r.source_file else '' }}</td>
  </tr>
  {% else %}
  <tr><td colspan="10" style="text-align:center;padding:20px;color:#999">No results</td></tr>
  {% endfor %}
  </tbody>
</table>
</div>

{% if total_pages > 1 %}
<div class="pagination">
  {% if page > 1 %}<a href="?{{ pagination_qs }}&page={{ page-1 }}">← Prev</a>{% endif %}
  <span class="cur">{{ page }} / {{ total_pages }}</span>
  {% if page < total_pages %}<a href="?{{ pagination_qs }}&page={{ page+1 }}">Next →</a>{% endif %}
</div>
{% endif %}

</body>
</html>
"""


def status_class(s):
    if not s:
        return "s-other"
    if s == "sold":
        return "s-sold"
    if "Cancel" in s:
        return "s-canceled"
    return "s-other"


# 1x1 transparent GIF
_PIXEL = base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
)


@app.route("/t/o/<campaign_id>/<int:send_id>")
def track_open(campaign_id: str, send_id: int):
    conn = get_conn()
    conn.execute(
        "UPDATE campaign_sends SET opened_at = NOW() "
        "WHERE id = %s AND campaign_id = %s AND opened_at IS NULL",
        (send_id, campaign_id),
    )
    conn.commit()
    conn.close()
    return Response(_PIXEL, mimetype="image/gif",
                    headers={"Cache-Control": "no-store, no-cache, must-revalidate"})


@app.route("/")
def index():
    init_db()
    conn = get_conn()

    q      = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    state  = request.args.get("state", "").strip()
    city   = request.args.get("city", "").strip()
    page   = max(1, int(request.args.get("page", 1)))

    # Dropdown options
    statuses = [r[0] for r in conn.execute(
        "SELECT name FROM lead_statuses ORDER BY name"
    )]
    states = [r[0] for r in conn.execute(
        "SELECT DISTINCT state FROM leads WHERE state IS NOT NULL ORDER BY state"
    )]
    total_count = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]

    # JOIN clause resolves status and source_file text from lookup tables
    join_clause = """
        FROM leads l
        LEFT JOIN lead_statuses ls ON ls.id = l.status_id
        LEFT JOIN source_files  sf ON sf.id = l.source_file_id
    """

    # Build WHERE
    where, params = [], []
    if q:
        where.append("(l.first_name LIKE %s OR l.last_name LIKE %s OR l.email LIKE %s)")
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]
    if status:
        where.append("ls.name = %s")
        params.append(status)
    if state:
        where.append("l.state = %s")
        params.append(state)
    if city:
        where.append("l.city LIKE %s")
        params.append(f"%{city}%")

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    filtered_count = conn.execute(
        f"SELECT COUNT(*) {join_clause} {where_sql}", params
    ).fetchone()[0]

    total_pages = max(1, (filtered_count + PAGE_SIZE - 1) // PAGE_SIZE)
    page        = min(page, total_pages)
    offset      = (page - 1) * PAGE_SIZE

    rows = conn.execute(
        f"SELECT l.*, ls.name AS status, sf.path AS source_file "
        f"{join_clause} {where_sql} ORDER BY l.id LIMIT %s OFFSET %s",
        params + [PAGE_SIZE, offset]
    ).fetchall()

    # Build pagination query string without page param
    qs_parts = []
    for k, v in [("q", q), ("status", status), ("state", state), ("city", city)]:
        if v:
            qs_parts.append(f"{k}={v}")
    pagination_qs = "&".join(qs_parts)

    conn.close()
    return render_template_string(HTML,
        rows=rows, q=q, status=status, state=state, city=city,
        statuses=statuses, states=states,
        total_count=total_count, filtered_count=filtered_count,
        page=page, total_pages=total_pages, pagination_qs=pagination_qs,
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
