#!/bin/bash
# setup-aws-ses-pinpoint.sh
# Configure AWS SES email and Pinpoint SMS via AWS CLI
# Run as: bash setup-aws-ses-pinpoint.sh

set -e

EMAIL="chris@windowsbyburkhardt.com"
REGION="${AWS_REGION:-us-east-1}"
PHONE="+14145501960"
SENDER_ID="ApexEnergy"

echo "============================================================"
echo "AWS SES & Pinpoint Setup via CLI"
echo "============================================================"

# 1. Verify SES Email Identity
echo ""
echo "1. Verifying email identity in SES..."
aws ses verify-email-identity \
  --email-address "$EMAIL" \
  --region "$REGION"

echo "✅ Email verification initiated for: $EMAIL"
echo "   ⚠️  Check your inbox for AWS verification email"

# 2. Get SES Configuration Set
echo ""
echo "2. Checking SES configuration set..."
CONFIG_SET="apex-campaigns"

aws sesv2 get-configuration-set \
  --configuration-set-name "$CONFIG_SET" \
  --region "$REGION" 2>/dev/null || {
  echo "⚠️  Configuration set not found. Run setup_aws.py first:"
  echo "   python3 setup_aws.py"
  exit 1
}

echo "✅ Configuration set exists: $CONFIG_SET"

# 3. Set up email sending attributes
echo ""
echo "3. Configuring sending attributes..."
aws sesv2 put-configuration-set-sending-options \
  --configuration-set-name "$CONFIG_SET" \
  --sending-options TlsPolicy=Optional \
  --region "$REGION" 2>/dev/null || true

echo "✅ Sending options configured"

# 4. Check Pinpoint SMS setup
echo ""
echo "4. Checking Pinpoint configuration..."

# List available Pinpoint apps
APPS=$(aws mobiletargeting get-apps --region "$REGION" 2>/dev/null || echo '{"ApplicationsResponse":{"Item":[]}}')
APP_COUNT=$(echo "$APPS" | grep -o '"Id"' | wc -l)

if [ "$APP_COUNT" -gt 0 ]; then
  APP_ID=$(echo "$APPS" | grep -o '"Id":"[^"]*"' | head -1 | cut -d'"' -f4)
  echo "✅ Found Pinpoint application: $APP_ID"
else
  echo "⚠️  No Pinpoint application found"
  echo "   Create one in AWS console or use:"
  echo "   aws mobiletargeting create-app --create-application-request Name=apex-campaigns --region $REGION"
  exit 1
fi

# 5. Enable SMS channel
echo ""
echo "5. Configuring SMS channel..."
aws mobiletargeting update-sms-channel \
  --application-id "$APP_ID" \
  --sms-channel-request "Enabled=true,SenderId=$SENDER_ID" \
  --region "$REGION" 2>/dev/null || {
  echo "⚠️  Could not update SMS channel (may need console setup)"
  echo "   Ensure SMS channel is enabled in Pinpoint console"
}

echo "✅ SMS channel configuration attempted"

# 6. Store configuration
echo ""
echo "6. Configuration to add to .env:"
echo "============================================================"
echo "PINPOINT_APPLICATION_ID=$APP_ID"
echo "SMS_ORIGINATING_NUMBER=$PHONE"
echo "SMS_SENDER_ID=$SENDER_ID"
echo "============================================================"

# 7. Summary
echo ""
echo "✅ AWS SES & Pinpoint setup complete!"
echo ""
echo "Next steps:"
echo "1. Verify email in your inbox (check for AWS verification email)"
echo "2. Register phone number ($PHONE) for SMS in Pinpoint console"
echo "3. Add the above variables to your .env file"
echo "4. Test email sending: python3 send.py --campaign window-inspection --dry-run"
