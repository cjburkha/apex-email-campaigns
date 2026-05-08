#!/bin/bash
# scripts/verify-aws-setup.sh
# Verify AWS SES and Pinpoint are properly configured
# Usage: bash scripts/verify-aws-setup.sh

set -e

REGION="${AWS_REGION:-us-east-1}"
EMAIL="chris@windowsbyburkhardt.com"

echo "=========================================================================="
echo "AWS Setup Verification"
echo "=========================================================================="
echo ""

# 1. Check SES
echo "1. SES Email Verification Status"
echo "  Email: $EMAIL"

# Use sesv2 instead of legacy ses
VERIFIED=$(aws sesv2 get-email-identity \
  --email-address "$EMAIL" \
  --region "$REGION" \
  --query 'VerificationStatus' \
  --output text 2>/dev/null || echo "NOT_VERIFIED")

if [ "$VERIFIED" = "SUCCESS" ]; then
  echo "  ✅ Email is verified in SES"
elif [ "$VERIFIED" != "NOT_VERIFIED" ]; then
  echo "  ℹ️  Email status: $VERIFIED"
else
  echo "  ❌ Email NOT verified in SES"
  echo "  Run: aws ses verify-email-identity --email-address $EMAIL --region $REGION"
  echo "  Then check your inbox for verification link"
fi

echo ""

# 2. Check DKIM
echo "2. Domain DKIM Status"
DKIM_ATTRS=$(aws ses get-identity-dkim-attributes \
  --identities windowsbyburkhardt.com \
  --region "$REGION" \
  --query 'DkimAttributes.windowsbyburkhardt.com' \
  --output json 2>/dev/null || echo '{}')

DKIM_ENABLED=$(echo "$DKIM_ATTRS" | grep -o '"DkimEnabled"[^,}]*' | cut -d':' -f2 | tr -d ' ')

if [ "$DKIM_ENABLED" = "true" ]; then
  echo "  ✅ DKIM is enabled for windowsbyburkhardt.com"
else
  echo "  ⚠️  DKIM not enabled yet"
  echo "  DKIM tokens:"
  TOKENS=$(aws ses verify-domain-dkim --domain windowsbyburkhardt.com --region "$REGION" --query 'DkimTokens[]' --output text 2>/dev/null)
  echo "$TOKENS" | tr ' ' '\n' | while read token; do
    echo "    $token._domainkey.windowsbyburkhardt.com CNAME $token.dkim.amazonses.com"
  done
fi

echo ""

# 3. Check Configuration Set
echo "3. SES Configuration Set"
CONFIG_SET="apex-campaigns"

CONFIG_STATUS=$(aws sesv2 get-configuration-set \
  --configuration-set-name "$CONFIG_SET" \
  --region "$REGION" \
  --query 'ConfigurationSetName' \
  --output text 2>/dev/null || echo "NOT FOUND")

if [ "$CONFIG_STATUS" = "$CONFIG_SET" ]; then
  echo "  ✅ Configuration set '$CONFIG_SET' exists"
  
  # Check event destinations
  DESTINATIONS=$(aws sesv2 get-configuration-set-event-destinations \
    --configuration-set-name "$CONFIG_SET" \
    --region "$REGION" \
    --query 'EventDestinations[0].MatchingEventTypes[]' \
    --output text 2>/dev/null | wc -w)
  
  if [ "$DESTINATIONS" -gt 0 ]; then
    echo "  ✅ Event tracking configured ($DESTINATIONS event types)"
  else
    echo "  ⚠️  No event destinations configured"
  fi
else
  echo "  ❌ Configuration set not found"
  echo "  Run: python3 setup_aws.py"
fi

echo ""

# 4. Check Pinpoint
echo "4. Pinpoint SMS Configuration"

APPS=$(aws mobiletargeting get-apps --region "$REGION" --query 'ApplicationsResponse.Item[*].[Id,Name]' --output text 2>/dev/null || echo "")

if [ -z "$APPS" ]; then
  echo "  ❌ No Pinpoint applications found"
  echo "  Create one: aws mobiletargeting create-app --create-application-request Name=apex-email-campaigns"
else
  FIRST_APP=$(echo "$APPS" | head -1 | awk '{print $1}')
  echo "  ✅ Pinpoint application found: $FIRST_APP"
  
  # Check SMS channel
  SMS_CHANNEL=$(aws mobiletargeting get-sms-channel \
    --application-id "$FIRST_APP" \
    --region "$REGION" \
    --query 'SMSChannelResponse.Enabled' \
    --output text 2>/dev/null || echo "false")
  
  if [ "$SMS_CHANNEL" = "True" ] || [ "$SMS_CHANNEL" = "true" ]; then
    echo "  ✅ SMS channel is enabled"
  else
    echo "  ⚠️  SMS channel is disabled"
    echo "  Enable: aws mobiletargeting update-sms-channel --application-id $FIRST_APP --sms-channel-request Enabled=true,SenderId=ApexEnergy"
  fi
fi

echo ""

# 5. Summary
echo "=========================================================================="
echo "Summary"
echo "=========================================================================="
echo ""
echo "✅ Setup items to complete:"
echo ""
echo "Immediate:"
echo "  [ ] Verify email in inbox (if not done)"
echo "  [ ] Add DKIM records to DNS (if not done)"
echo ""
echo "Within 1-2 business days:"
echo "  [ ] Register 10DLC long code (+14145501960) in Pinpoint console"
echo "  [ ] Approve carrier requirements"
echo ""
echo "See AWS-SETUP.md for detailed documentation"
