# AWS Setup & Configuration Guide

## Overview
This document outlines the AWS resources needed for the apex-email-campaigns drip marketing system with SES email and Pinpoint SMS.

## Architecture
- **Email**: AWS SES (Simple Email Service) - `chris@windowsbyburkhardt.com`
- **SMS**: AWS Pinpoint - Phone: `+14145501960`
- **Database**: PostgreSQL on RDS - `wbb-prod.c81qkua4c3e2.us-east-1.rds.amazonaws.com`
- **Event Tracking**: SNS → SQS (bounces, complaints, opens, clicks)

---

## Phase 1: Database Schema (DBA)

### Action Required
Run the schema migration as the `wbbadmin` user:

```bash
psql -h wbb-prod.c81qkua4c3e2.us-east-1.rds.amazonaws.com \
     -U wbbadmin \
     -d apex \
     -f schema-migration.sql
```

### What It Does
- Adds `unsubscribed_at` to `leads` table
- Adds SMS & drip tracking columns to `campaign_sends`:
  - `drip_step` - current step in drip sequence (1-4)
  - `next_send_at` - when to send next step
  - `sms_message_id`, `sms_sent_at`, `sms_status` - SMS tracking
- Creates performance indexes for drip queries
- Grants necessary permissions to `cburkhardt` user

---

## Phase 2: SES Email Configuration

### Current Status
- Region: `us-east-1`
- Configuration Set: `apex-campaigns` (created by `setup_aws.py`)
- Email to verify: `chris@windowsbyburkhardt.com`

### Steps

**1. Verify Email Identity (via CLI)**
```bash
aws ses verify-email-identity \
  --email-address chris@windowsbyburkhardt.com \
  --region us-east-1
```

**Action:** Check inbox for AWS verification email with link - **MUST DO THIS**

**2. Set DKIM (via AWS Console or CLI)**
After email is verified, add DKIM signing for reputation:

```bash
aws ses verify-domain-dkim \
  --domain windowsbyburkhardt.com \
  --region us-east-1
```

Add the returned CNAME records to your DNS.

**3. Enable Event Publishing**
Already configured via `setup_aws.py`:
- Configuration Set: `apex-campaigns`
- Events → SNS topic → SQS queue
- Bounces, complaints, opens, clicks tracked automatically

### .env Configuration
```bash
SES_CONFIG_SET=apex-campaigns
# Already set in code
```

---

## Phase 3: Pinpoint SMS Configuration

### Current Status
- Region: `us-east-1`
- Phone for SMS: `+14145501960`
- Sender ID: `ApexEnergy`

### Steps

**1. Create/Identify Pinpoint Application**

List existing apps:
```bash
aws mobiletargeting get-apps --region us-east-1
```

If needed, create new app:
```bash
aws mobiletargeting create-app \
  --create-application-request Name=apex-email-campaigns \
  --region us-east-1
```

**2. Register SMS 10DLC Long Code**

This requires **AWS Console** (API doesn't support registration):
- Go to AWS Pinpoint Console → Phone Number Management
- Request a 10-digit long code: `414-550-1960`
- Note: Can take 1-2 business days for carrier approval

**3. Enable SMS Channel (via CLI)**

```bash
aws mobiletargeting update-sms-channel \
  --application-id <APP_ID> \
  --sms-channel-request Enabled=true,SenderId=ApexEnergy \
  --region us-east-1
```

**4. Configure Reply Handling (Optional)**

For processing inbound SMS:
```bash
aws mobiletargeting get-sms-channel \
  --application-id <APP_ID> \
  --region us-east-1
```

### .env Configuration
```bash
PINPOINT_APPLICATION_ID=<your-app-id>
SMS_ORIGINATING_NUMBER=+14145501960
SMS_SENDER_ID=ApexEnergy
```

---

## Phase 4: Application Configuration

### Update .env
```bash
# Copy from .env.example and fill in:
AWS_ACCESS_KEY_ID=<from-secrets>
AWS_SECRET_ACCESS_KEY=<from-secrets>
AWS_REGION=us-east-1

# After DBA schema migration
# (no change needed - just verify)

# After SES setup
# (already set by setup_aws.py)
SES_CONFIG_SET=apex-campaigns
SES_EVENTS_QUEUE_URL=<from-setup_aws.py-output>

# After Pinpoint setup
PINPOINT_APPLICATION_ID=<from-get-apps>
SMS_ORIGINATING_NUMBER=+14145501960
SMS_SENDER_ID=ApexEnergy

# Unsubscribe handling
UNSUBSCRIBE_SECRET=<generate-random-32-char-string>
UNSUBSCRIBE_BASE_URL=https://windowsbyburkhardt.com/unsubscribe
```

---

## Testing Checklist

### ✅ Email Sending
```bash
python3 send.py --campaign window-inspection \
  --query "SELECT * FROM leads WHERE email IS NOT NULL LIMIT 1" \
  --dry-run
```

### ✅ Drip Enrollment (After Schema)
```bash
python3 drip.py enroll --campaign window-inspection \
  --query "SELECT * FROM leads WHERE email IS NOT NULL LIMIT 5" \
  --dry-run
```

### ✅ Drip Execution (After Schema)
```bash
python3 drip.py run --campaign window-inspection --dry-run
```

### ✅ Unsubscribe Link
```bash
python3 -c "
from send import _make_unsubscribe_token, _unsubscribe_url
token = _make_unsubscribe_token(12345, 'test@example.com')
url = _unsubscribe_url(12345, token)
print(f'Unsubscribe URL: {url}')
"
```

---

## Dependencies

### AWS IAM Permissions Required
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "sesv2:SendEmail",
        "sesv2:GetConfiguration*",
        "ses:VerifyEmailIdentity",
        "ses:GetAccountSendingEnabled"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "mobiletargeting:SendMessages",
        "mobiletargeting:GetSmsChannel",
        "mobiletargeting:UpdateSmsChannel"
      ],
      "Resource": "arn:aws:mobiletargeting:*:*:apps/*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "sns:Publish",
        "sqs:ReceiveMessage",
        "sqs:GetQueueAttributes"
      ],
      "Resource": "*"
    }
  ]
}
```

---

## Timeline & Blockers

| Phase | Task | Owner | Timeline | Status |
|-------|------|-------|----------|--------|
| DB | Run schema migration | DBA (wbbadmin) | ASAP | 🔴 **PENDING** |
| Email | Verify `chris@windowsbyburkhardt.com` | Chris (inbox) | 5 mins | 🔴 **PENDING** |
| Email | Enable DKIM signing | Chris (DNS admin) | 1-2 hours | 🔴 **PENDING** |
| SMS | Register 10DLC long code | Chris (AWS console) | 1-2 business days | 🔴 **PENDING** |
| SMS | Enable Pinpoint SMS channel | Chris (AWS CLI) | 5 mins | 🔴 **PENDING** |
| App | Update .env with credentials | Chris | 5 mins | 🔴 **PENDING** |
| Test | Run dry-run tests | Chris | 10 mins | 🟡 **BLOCKED** (needs DB schema) |
| Deploy | Set up drip scheduler | DevOps | TBD | 🟡 **BLOCKED** (needs DB schema) |

---

## References

- [AWS SES Documentation](https://docs.aws.amazon.com/ses/)
- [AWS Pinpoint SMS](https://docs.aws.amazon.com/pinpoint/latest/developerguide/channels-sms.html)
- [Pinpoint 10DLC Registration](https://docs.aws.amazon.com/pinpoint/latest/developerguide/channels-sms-10dlc.html)
- [Project README](./README.md)
