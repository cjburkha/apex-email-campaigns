# Apex Email Campaigns

This repo sends campaigns from PostgreSQL leads, tracks SES events, and now supports drip campaigns, unsubscribe handling, and AWS Pinpoint SMS.

## New features

- SES email send from `chris@windowsbyburkhardt.com`
- `List-Unsubscribe` headers with mailto + unsubscribe URL
- `unsubscribed_at` recorded in `leads`
- `drip.py` for scheduled drip campaigns
- SMS support via AWS Pinpoint
- Multi-channel contact: email + SMS when both are available, fallback to one channel if only one exists

## Setup

1. Copy `.env.example` to `.env`
2. Set AWS credentials and `DATABASE_URL`
3. Set `PINPOINT_APPLICATION_ID`, `SMS_SENDER_ID`, `SMS_ORIGINATING_NUMBER`, `UNSUBSCRIBE_SECRET`, and `UNSUBSCRIBE_BASE_URL`
4. Configure Pinpoint SMS replies and STOP/HELP keywords in the AWS Pinpoint console for your originating number.
5. Run `python setup_aws.py` once
6. Run `python db.py` or any command that calls `init_db()` to apply schema changes

## Sending

### One-shot email/SMS campaign

```bash
python send.py --campaign window-inspection --query "SELECT * FROM leads WHERE state='WI'"
```

### Drip campaigns

Each drip campaign can now include 4 distinct email templates and 4 distinct SMS templates in the campaign folder. The `window-inspection` campaign stores those templates in `campaigns/window-inspection/emails/` and `campaigns/window-inspection/sms/`.

Enroll leads:

```bash
python drip.py enroll --campaign window-inspection --query "SELECT * FROM leads WHERE state='WI'"
```

Run due drip steps:

```bash
python drip.py run --campaign window-inspection
```

The campaign is configured to send one email each week in succession, with optional SMS delivered on the same weekly step when a phone number is available.

## Unsubscribe

Recipients receive `List-Unsubscribe` headers and can unsubscribe via:

```
https://windowsbyburkhardt.com/unsubscribe?id=<lead_id>&t=<token>
```

The app records `unsubscribed_at` and excludes those leads from future sends.
