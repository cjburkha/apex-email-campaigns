#!/bin/bash
# scripts/setup-complete.sh
# Complete setup automation for apex-email-campaigns
# Usage: bash scripts/setup-complete.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=========================================================================="
echo "apex-email-campaigns: Complete Setup Script"
echo "=========================================================================="
echo ""

# Check for required tools
echo "Checking dependencies..."
for cmd in aws python3 psql; do
  if ! command -v $cmd &> /dev/null; then
    echo "❌ $cmd is not installed"
    exit 1
  fi
done
echo "✅ All dependencies found"
echo ""

# 1. Database Schema
echo "=========================================================================="
echo "Phase 1: Database Schema Migration"
echo "=========================================================================="
echo ""
echo "This requires DBA access (wbbadmin user)"
echo ""
echo "Ask your DBA to run:"
echo ""
echo "  psql -h wbb-prod.c81qkua4c3e2.us-east-1.rds.amazonaws.com \\"
echo "       -U wbbadmin \\"
echo "       -d apex \\"
echo "       -f schema-migration.sql"
echo ""
echo "File location: $ROOT_DIR/schema-migration.sql"
echo ""
read -p "Press Enter when DBA has completed schema migration, or Ctrl+C to exit..."
echo ""

# 2. SES Configuration
echo "=========================================================================="
echo "Phase 2: AWS SES Email Configuration"
echo "=========================================================================="
echo ""

EMAIL="chris@windowsbyburkhardt.com"
REGION="${AWS_REGION:-us-east-1}"

echo "Verifying email identity: $EMAIL"
aws ses verify-email-identity \
  --email-address "$EMAIL" \
  --region "$REGION"

echo "✅ Verification initiated"
echo ""
echo "⚠️  ACTION REQUIRED: Check your email inbox for AWS verification link"
echo "    Sender: noreply@amazonses.com"
echo "    Subject: Amazon SES Verification Email"
echo ""
read -p "Press Enter after verifying your email, or Ctrl+C to skip..."
echo ""

# 3. DKIM Setup
echo "Setting up DKIM for domain reputation..."
DKIM_TOKENS=$(aws ses verify-domain-dkim \
  --domain windowsbyburkhardt.com \
  --region "$REGION" \
  --query 'DkimTokens' \
  --output text)

echo "✅ DKIM tokens generated:"
echo "$DKIM_TOKENS" | tr '\t' '\n' | while read token; do
  echo "  $token._domainkey.windowsbyburkhardt.com CNAME $token.dkim.amazonses.com"
done
echo ""
echo "⚠️  ACTION REQUIRED: Add the above CNAME records to your DNS"
echo ""

# 4. Pinpoint Configuration
echo "=========================================================================="
echo "Phase 3: AWS Pinpoint SMS Configuration"
echo "=========================================================================="
echo ""

PHONE="+14145501960"
SENDER_ID="ApexEnergy"

echo "Checking Pinpoint applications..."
APPS=$(aws mobiletargeting get-apps --region "$REGION" 2>/dev/null || echo '{}')
APP_COUNT=$(echo "$APPS" | grep -o '"Id"' | wc -l)

if [ "$APP_COUNT" -gt 0 ]; then
  APP_ID=$(echo "$APPS" | grep -o '"Id":"[^"]*"' | head -1 | cut -d'"' -f4)
  echo "✅ Found Pinpoint application: $APP_ID"
else
  echo "Creating new Pinpoint application..."
  CREATE_RESPONSE=$(aws mobiletargeting create-app \
    --create-application-request Name=apex-email-campaigns \
    --region "$REGION")
  APP_ID=$(echo "$CREATE_RESPONSE" | grep -o '"Id":"[^"]*"' | head -1 | cut -d'"' -f4)
  echo "✅ Created Pinpoint application: $APP_ID"
fi

echo ""
echo "Enabling SMS channel..."
aws mobiletargeting update-sms-channel \
  --application-id "$APP_ID" \
  --sms-channel-request "Enabled=true,SenderId=$SENDER_ID" \
  --region "$REGION" 2>/dev/null || {
  echo "⚠️  Could not update SMS channel"
  echo "   Ensure SMS is enabled in Pinpoint console"
}

echo "✅ SMS channel configuration complete"
echo ""
echo "⚠️  ACTION REQUIRED:"
echo "    1. Register 10DLC long code: $PHONE"
echo "       AWS Pinpoint Console → Phone Number Management"
echo "    2. Note: Takes 1-2 business days for carrier approval"
echo ""

# 5. Update .env
echo "=========================================================================="
echo "Phase 4: Application Configuration"
echo "=========================================================================="
echo ""

echo "Generating configuration..."
cat > "$ROOT_DIR/.env.local" << EOF
# AWS Configuration
AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID:-}
AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY:-}
AWS_REGION=${REGION}

# SES Configuration (auto-configured)
SES_CONFIG_SET=apex-campaigns
SES_EVENTS_QUEUE_URL=

# Pinpoint SMS Configuration
PINPOINT_APPLICATION_ID=${APP_ID}
SMS_ORIGINATING_NUMBER=${PHONE}
SMS_SENDER_ID=${SENDER_ID}

# Unsubscribe Support
UNSUBSCRIBE_SECRET=$(openssl rand -hex 16)
UNSUBSCRIBE_BASE_URL=https://windowsbyburkhardt.com/unsubscribe

# Database (from keychain)
DATABASE_URL=postgresql://cburkhardt@wbb-prod.c81qkua4c3e2.us-east-1.rds.amazonaws.com:5432/apex?sslmode=require
EOF

echo "✅ Configuration saved to: $ROOT_DIR/.env.local"
echo ""
echo "Next steps:"
echo "1. Review $ROOT_DIR/.env.local"
echo "2. Add SES_EVENTS_QUEUE_URL after running: python3 setup_aws.py"
echo "3. Copy values to your actual .env (or use .env.local)"
echo "4. Once 10DLC is approved, test drip campaigns"
echo ""

# 6. Test Configuration
echo "=========================================================================="
echo "Phase 5: Testing"
echo "=========================================================================="
echo ""

read -p "Run tests now? (y/n) " -n 1 -r
echo ""

if [[ $REPLY =~ ^[Yy]$ ]]; then
  echo "Testing database connectivity..."
  python3 << 'EOF'
from db import get_conn, init_db
try:
  init_db()
  conn = get_conn()
  leads = conn.execute('SELECT COUNT(*) as count FROM leads').fetchone()
  print(f"✅ Database connected. Leads in system: {leads['count']}")
  conn.close()
except Exception as e:
  print(f"❌ Database error: {e}")
EOF
  
  echo ""
  echo "Testing template rendering..."
  python3 << 'EOF'
import json
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

try:
  campaign_dir = Path('campaigns/window-inspection')
  config = json.loads((campaign_dir / 'config.json').read_text())
  env = Environment(loader=FileSystemLoader(str(campaign_dir)))
  
  # Test email template
  email = env.get_template('emails/email-1.html').render(
    first_name='Test', city='Milwaukee', state='WI'
  )
  
  # Test SMS template
  sms = env.get_template('sms/sms-1.txt').render(
    first_name='Test', city='Milwaukee'
  )
  
  print(f"✅ Email template: {len(email)} chars")
  print(f"✅ SMS template: {len(sms)} chars")
except Exception as e:
  print(f"❌ Template error: {e}")
EOF
fi

echo ""
echo "=========================================================================="
echo "✅ Setup Complete!"
echo "=========================================================================="
echo ""
echo "Summary:"
echo "  Email:     chris@windowsbyburkhardt.com (SES)"
echo "  SMS:       +14145501960 (Pinpoint, pending 10DLC approval)"
echo "  Database:  wbb-prod.c81qkua4c3e2.us-east-1.rds.amazonaws.com/apex"
echo ""
echo "See AWS-SETUP.md for detailed documentation"
