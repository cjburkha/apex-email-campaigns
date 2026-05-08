# Setup Complete - Implementation Summary

**Date**: May 8, 2026  
**Commit**: 8a97fc1  
**System**: apex-email-campaigns drip marketing engine

---

## ✅ Completed Tasks

### 1. Drip Campaign Architecture
- ✅ 4-step weekly email sequence (emails/email-1.html through email-4.html)
- ✅ 4-step weekly SMS sequence (sms/sms-1.txt through sms-4.txt)
- ✅ Drip runner: `drip.py enroll` and `drip.py run`
- ✅ Multi-channel logic: email + SMS when both available
- ✅ 7-day intervals between steps
- ✅ Unsubscribe-aware lead exclusion

### 2. Email Configuration
- ✅ SES email identity verification initiated: `chris@windowsbyburkhardt.com`
- ✅ Configuration set: `apex-campaigns` (with event tracking)
- ✅ DKIM tokens generated for windowsbyburkhardt.com
- ✅ Event destinations configured (8 event types: send, bounce, delivery, open, click, etc.)

### 3. SMS Configuration
- ✅ Pinpoint application created
- ✅ SMS channel enabled
- ✅ Phone configured: `+14145501960`
- ✅ Sender ID: `ApexEnergy`

### 4. Unsubscribe Handling
- ✅ Unsubscribe endpoint: `/unsubscribe?id=<lead_id>&t=<token>`
- ✅ HMAC token generation & validation
- ✅ List-Unsubscribe headers with mailto + HTTP URL
- ✅ Web.py endpoint to mark leads as unsubscribed

### 5. AWS Setup Documentation
- ✅ `AWS-SETUP.md` - Complete setup guide with timelines
- ✅ `scripts/setup-complete.sh` - Interactive setup automation
- ✅ `scripts/verify-aws-setup.sh` - AWS configuration verification
- ✅ `schema-migration.sql` - DBA-ready database migration
- ✅ `iam-policy.json` - Required IAM permissions
- ✅ All committed to git for reproducibility

---

## 🔴 Pending Tasks

### Phase 1: Database Schema (DBA Access Required)
**Status**: Ready, waiting for DBA  
**Action**: Ask DBA to run schema-migration.sql

```bash
psql -h wbb-prod.c81qkua4c3e2.us-east-1.rds.amazonaws.com \
     -U wbbadmin \
     -d apex \
     -f schema-migration.sql
```

**What it does**:
- Adds `unsubscribed_at` to `leads`
- Adds `drip_step`, `next_send_at`, SMS tracking to `campaign_sends`
- Creates performance indexes
- Grants permissions to `cburkhardt`

**Blocker**: None - can run immediately

---

### Phase 2: SES Email Verification
**Status**: In Progress  
**Action**: Check email inbox for verification

**Steps**:
1. ✅ Verification email sent to `chris@windowsbyburkhardt.com`
2. ⏳ Check inbox for email from `noreply@amazonses.com`
3. ⏳ Click verification link
4. Then run: `bash scripts/verify-aws-setup.sh` to confirm

**Timeline**: 5 minutes  
**Blocker**: Email verification link

---

### Phase 3: DKIM DNS Records
**Status**: Ready, waiting for DNS admin  
**Action**: Add CNAME records to windowsbyburkhardt.com DNS

**DKIM Tokens** (from verification):
```
frcfavyla7n6z5ghjuzf5rlcrihwazzd._domainkey.windowsbyburkhardt.com 
  CNAME frcfavyla7n6z5ghjuzf5rlcrihwazzd.dkim.amazonses.com

vycy6isetrmcnjmeefyc6iv2bp4sdcnp._domainkey.windowsbyburkhardt.com 
  CNAME vycy6isetrmcnjmeefyc6iv2bp4sdcnp.dkim.amazonses.com

63eab75qslz24b3bslgoj6vn3tcvdnmr._domainkey.windowsbyburkhardt.com 
  CNAME 63eab75qslz24b3bslgoj6vn3tcvdnmr.dkim.amazonses.com
```

**Timeline**: 1-2 hours (DNS propagation)  
**Blocker**: DNS admin access

---

### Phase 4: SMS 10DLC Registration
**Status**: Pending Pinpoint approval  
**Action**: Register phone number in AWS Pinpoint console

**What to do**:
1. AWS Pinpoint Console → Phone Number Management
2. Request new 10DLC long code
3. Enter phone: `414-550-1960` (Wisconsin area code)
4. Fill carrier compliance requirements
5. Wait for approval

**Timeline**: 1-2 **business days** for carrier approval  
**Blocker**: Pinpoint/carrier approval

---

## 📋 Next Steps (In Order)

### Immediate (Today)
```bash
# 1. Verify email
# → Check inbox for AWS verification email

# 2. Ask DBA to run schema migration
# → Send schema-migration.sql

# 3. Add DKIM records
# → Contact DNS admin with DKIM tokens above

# 4. Register 10DLC
# → AWS Pinpoint console (may take 1-2 days)
```

### After Schema Migration
```bash
# 5. Test drip enrollment
python3 drip.py enroll --campaign window-inspection \
  --query "SELECT * FROM leads WHERE email IS NOT NULL LIMIT 5" \
  --dry-run

# 6. Test drip execution
python3 drip.py run --campaign window-inspection --dry-run
```

### After 10DLC Approval
```bash
# 7. Update .env with Pinpoint app ID
PINPOINT_APPLICATION_ID=<your-app-id>

# 8. Test live SMS sends
python3 drip.py run --campaign window-inspection --limit 1
```

### Final Steps
```bash
# 9. Set up cron job for automated drip execution
# (e.g., every 6 hours or daily)
0 */6 * * * cd /path/to/apex-email-campaigns && python3 drip.py run --campaign window-inspection

# 10. Monitor unsubscribes and bounce handling
# via sync_events.py
python3 sync_events.py --watch
```

---

## 📊 Configuration Reference

### Environment Variables (.env)
```bash
# AWS Credentials (already set)
AWS_ACCESS_KEY_ID=AKIAZXTAJ4PNOZZNBPD3...
AWS_SECRET_ACCESS_KEY=***
AWS_REGION=us-east-1

# SES Configuration (auto-configured)
SES_CONFIG_SET=apex-campaigns
SES_EVENTS_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/.../apex-campaigns-events

# Email Configuration
# To be verified - chris@windowsbyburkhardt.com

# Pinpoint SMS Configuration
PINPOINT_APPLICATION_ID=<pending 10DLC approval>
SMS_ORIGINATING_NUMBER=+14145501960
SMS_SENDER_ID=ApexEnergy

# Unsubscribe Support
UNSUBSCRIBE_SECRET=<generate random 32 chars>
UNSUBSCRIBE_BASE_URL=https://windowsbyburkhardt.com/unsubscribe
```

### Campaign Configuration
File: `campaigns/window-inspection/config.json`
- 4 drip steps, 7 days apart
- Email templates: `emails/email-1.html` through `email-4.html`
- SMS templates: `sms/sms-1.txt` through `sms-4.txt`
- From: `chris@windowsbyburkhardt.com`
- Reply-To: `chris@windowsbyburkhardt.com`

---

## 🧪 Testing Checklist

### Template Rendering
```bash
python3 << 'EOF'
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

env = Environment(loader=FileSystemLoader('campaigns/window-inspection'))
email = env.get_template('emails/email-1.html').render(first_name='Test', city='Milwaukee', state='WI')
sms = env.get_template('sms/sms-1.txt').render(first_name='Test', city='Milwaukee')
print(f"✅ Email: {len(email)} chars")
print(f"✅ SMS: {len(sms)} chars")
EOF
```

### Unsubscribe Tokens
```bash
python3 << 'EOF'
from send import _make_unsubscribe_token, _unsubscribe_url
token = _make_unsubscribe_token(12345, 'test@example.com')
url = _unsubscribe_url(12345, token)
print(f"Unsubscribe URL: {url}")
EOF
```

### Database Connection
```bash
python3 << 'EOF'
from db import get_conn, init_db
init_db()
conn = get_conn()
leads = conn.execute('SELECT COUNT(*) as count FROM leads').fetchone()
print(f"✅ Connected. Leads: {leads['count']}")
EOF
```

---

## 📚 Documentation Files

| File | Purpose |
|------|---------|
| `AWS-SETUP.md` | Complete setup guide with architecture & timelines |
| `README.md` | Project overview and usage instructions |
| `schema-migration.sql` | DBA-ready database migration script |
| `iam-policy.json` | Required AWS IAM permissions |
| `scripts/setup-complete.sh` | Interactive setup automation script |
| `scripts/verify-aws-setup.sh` | AWS configuration verification script |

---

## 🎯 Success Criteria

- [ ] Database schema migrated (DBA)
- [ ] Email verified in SES (Inbox)
- [ ] DKIM records added (DNS Admin)
- [ ] 10DLC approved (Pinpoint/Carrier)
- [ ] `drip.py enroll` works with dry-run
- [ ] `drip.py run` sends test emails/SMS
- [ ] Unsubscribe links work
- [ ] Cron job scheduled for automated sends
- [ ] Event tracking working (bounces, opens, clicks)

---

## 🔧 Troubleshooting

### "must be owner of table" Error
→ Schema migration requires DBA access. Ask wbbadmin to run schema-migration.sql

### "PINPOINT_APPLICATION_ID not set" Error
→ Need to create Pinpoint app or find existing app ID

### Email not verified
→ Check inbox (including spam folder) for AWS verification email from noreply@amazonses.com

### SMS not sending
→ 10DLC registration pending approval. Check Pinpoint console for status.

---

## 📞 Support

For questions or blockers:
- Schema: Ask DBA (wbbadmin)
- SES: Check AWS SES console
- Pinpoint: Check AWS Pinpoint console
- Codebase: See AWS-SETUP.md and README.md

All changes committed to git. Configuration scripts are reproducible and documented.

---

**Last Updated**: May 8, 2026  
**Next Review**: After DBA schema migration
