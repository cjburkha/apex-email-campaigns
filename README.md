# apex-email-campaigns

**A production Revenue Operations stack — CRM data layer, multi-channel lifecycle engine, attribution, and deliverability — built solo for a regional window & home-improvement company.**

> 📊 **Portfolio case study:** https://cjburkha.github.io
> Companion analytics repo (funnel & sold-dollar analysis): `apex-sales-intel`

This repo is the **activation and data-hygiene half** of a go-to-market stack. It takes leads out of the CRM warehouse, runs cohort-based email + SMS lifecycle campaigns against them, tracks per-lead engagement, and keeps the contact database clean and compliant — the kind of system a RevOps team owns.

---

## What it does (RevOps view)

| Capability | What it means | Where |
|---|---|---|
| **Lifecycle / campaign ops** | Cohort drip engine — 4-week email + SMS sequences enrolled straight from a SQL query, weekly stepped sends, channel fallback when only one contact method exists. | `drip.py`, `campaigns/` |
| **Deliverability & compliance** | Bounces & complaints drain from SQS back to the lead record; one-click unsubscribe, `List-Unsubscribe` headers, and SMS STOP all write one opt-out ledger every send filters against. | `sync_events.py`, `web.py`, `send.py` |
| **Attribution & tracking** | Open-pixel and click-tracking routes write to dedicated event tables; branded shortlinks tie referral/campaign clicks to the individual lead. | `web.py` |
| **Data integration & hygiene** | Import pipelines from CRM/tooling exports (LeadPerfection, Loupe) into Postgres; enrichment normalized into a 1:1 sidecar table. | `import_leads.py`, `import_loupe.py`, `merge_loupe.py` |
| **Demand gen** | A $500 referral program end to end — Mailchimp send → branded shortlink → conversion-oriented landing flow. | `gen_referral_csv.py` |

## Architecture

```
Sources → Ingest/ETL → Postgres warehouse → Activate + Track → Feedback loop
(CRM,      (Python       (leads, event       (SES/Pinpoint     (SQS drains bounces,
 tooling,   import         tables, opt-out     drips, open        complaints, opt-outs
 web)       scripts)       ledger)             pixel, clicks)     back to warehouse)
```

Every send filters against suppression state, so the pipeline gets cleaner on each run rather than dirtier.

## Stack

Python · PostgreSQL · AWS SES · AWS Pinpoint (SMS) · AWS SQS · AWS App Runner · CloudFront

## Send paths

```bash
# One-shot email/SMS campaign from a SQL segment
python send.py --campaign window-inspection --query "SELECT * FROM leads WHERE state='WI'"

# Enroll a segment into a multi-week drip cohort
python drip.py enroll --campaign window-inspection --query "SELECT * FROM leads WHERE state='WI'"

# Run all due drip steps (SES email + Pinpoint SMS)
python drip.py run --campaign window-inspection
```

## Setup

1. Copy `.env.example` → `.env`; set AWS credentials, `DATABASE_URL`, and the Pinpoint/unsubscribe vars.
2. `python setup_aws.py` once, then `python db.py` to apply schema.

## A note on data

**No customer data lives in this repo.** All PII (lead lists, sold tickets, exports) stays in the Postgres database and gitignored local files — never committed. Only code, schema, and templates are tracked.
