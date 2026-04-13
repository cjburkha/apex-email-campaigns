#!/usr/bin/env bash
# scripts/launch-test-ec2.sh
#
# Spins up a Windows Server 2022 EC2 instance for testing setup-windows.bat.
# Run with an IAM profile that has EC2 permissions (e.g. wbb-admin).
#
# Usage:
#   AWS_PROFILE=wbb-admin bash scripts/launch-test-ec2.sh
#
# When done testing, terminate with:
#   AWS_PROFILE=wbb-admin aws ec2 terminate-instances --instance-ids <id> --region us-east-1
set -euo pipefail

REGION="us-east-1"
INSTANCE_TYPE="t3.medium"         # 2 vCPU / 4 GB RAM — comfortable for Outlook + Python
KEY_NAME="apex-test-key"          # will be created if it doesn't exist
SG_NAME="apex-test-rdp"
TAG_NAME="apex-campaigns-test"

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║   Apex Campaigns — Windows Test EC2 Launcher    ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# ── Latest Windows Server 2022 Full AMI ──────────────────────────────────────
echo "→  Looking up latest Windows Server 2022 AMI..."
AMI_ID=$(aws ec2 describe-images \
  --region "$REGION" \
  --owners amazon \
  --filters \
    "Name=name,Values=Windows_Server-2022-English-Full-Base-*" \
    "Name=state,Values=available" \
  --query "sort_by(Images, &CreationDate)[-1].ImageId" \
  --output text)
echo "✔  AMI: $AMI_ID"

# ── Key pair ──────────────────────────────────────────────────────────────────
KEY_FILE="$HOME/.ssh/${KEY_NAME}.pem"
if ! aws ec2 describe-key-pairs --region "$REGION" --key-names "$KEY_NAME" &>/dev/null; then
  echo "→  Creating key pair: $KEY_NAME"
  aws ec2 create-key-pair \
    --region "$REGION" \
    --key-name "$KEY_NAME" \
    --query "KeyMaterial" \
    --output text > "$KEY_FILE"
  chmod 600 "$KEY_FILE"
  echo "✔  Key saved to: $KEY_FILE"
else
  echo "✔  Key pair already exists: $KEY_NAME"
fi

# ── Security group: RDP from your IP only ────────────────────────────────────
MY_IP=$(curl -s https://checkip.amazonaws.com)/32
SG_ID=$(aws ec2 describe-security-groups \
  --region "$REGION" \
  --filters "Name=group-name,Values=$SG_NAME" \
  --query "SecurityGroups[0].GroupId" \
  --output text 2>/dev/null || echo "None")

if [ "$SG_ID" = "None" ] || [ -z "$SG_ID" ]; then
  echo "→  Creating security group: $SG_NAME"
  SG_ID=$(aws ec2 create-security-group \
    --region "$REGION" \
    --group-name "$SG_NAME" \
    --description "RDP access for apex-campaigns Windows test" \
    --query "GroupId" \
    --output text)
  aws ec2 authorize-security-group-ingress \
    --region "$REGION" \
    --group-id "$SG_ID" \
    --protocol tcp --port 3389 --cidr "$MY_IP"
  echo "✔  Security group $SG_ID — RDP open for $MY_IP"
else
  echo "✔  Security group already exists: $SG_ID"
  # Update RDP rule to current IP (in case it changed)
  aws ec2 revoke-security-group-ingress \
    --region "$REGION" \
    --group-id "$SG_ID" \
    --protocol tcp --port 3389 --cidr 0.0.0.0/0 2>/dev/null || true
  aws ec2 authorize-security-group-ingress \
    --region "$REGION" \
    --group-id "$SG_ID" \
    --protocol tcp --port 3389 --cidr "$MY_IP" 2>/dev/null || true
  echo "✔  RDP rule updated for $MY_IP"
fi

# ── Launch instance ───────────────────────────────────────────────────────────
echo "→  Launching $INSTANCE_TYPE instance..."
INSTANCE_ID=$(aws ec2 run-instances \
  --region "$REGION" \
  --image-id "$AMI_ID" \
  --instance-type "$INSTANCE_TYPE" \
  --key-name "$KEY_NAME" \
  --security-group-ids "$SG_ID" \
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$TAG_NAME}]" \
  --block-device-mappings "DeviceName=/dev/sda1,Ebs={VolumeSize=50,VolumeType=gp3,DeleteOnTermination=true}" \
  --query "Instances[0].InstanceId" \
  --output text)
echo "✔  Instance: $INSTANCE_ID"

# ── Wait for running + status ok ─────────────────────────────────────────────
echo "→  Waiting for instance to start (usually ~2 min)..."
aws ec2 wait instance-running --region "$REGION" --instance-ids "$INSTANCE_ID"

PUBLIC_IP=$(aws ec2 describe-instances \
  --region "$REGION" \
  --instance-ids "$INSTANCE_ID" \
  --query "Reservations[0].Instances[0].PublicIpAddress" \
  --output text)

echo ""
echo "✔  Instance running: $PUBLIC_IP"
echo ""
echo "→  Waiting for Windows password to be available (~4 min after launch)..."
aws ec2 wait password-data-available --region "$REGION" --instance-ids "$INSTANCE_ID"

# ── Get admin password ────────────────────────────────────────────────────────
ENCRYPTED_PW=$(aws ec2 get-password-data \
  --region "$REGION" \
  --instance-id "$INSTANCE_ID" \
  --query "PasswordData" \
  --output text)

ADMIN_PW=$(echo "$ENCRYPTED_PW" | base64 -d | openssl rsautl -decrypt -inkey "$KEY_FILE" 2>/dev/null \
  || echo "$ENCRYPTED_PW" | base64 --decode | openssl pkeyutl -decrypt -inkey "$KEY_FILE")

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  RDP CREDENTIALS                                             ║"
echo "╠══════════════════════════════════════════════════════════════╣"
printf  "║  Host     : %-48s║\n" "$PUBLIC_IP"
printf  "║  Username : %-48s║\n" "Administrator"
printf  "║  Password : %-48s║\n" "$ADMIN_PW"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Connect: open 'Microsoft Remote Desktop', add PC above.    ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "Once connected:"
echo "  1. Open Edge → sign into microsoft365.com with your M365 account"
echo "  2. Install Office → open Outlook → sign in"
echo "  3. Open Edge → download setup-windows.bat from:"
echo "     https://raw.githubusercontent.com/cjburkha/apex-email-campaigns/master/setup-windows.bat"
echo "  4. Double-click setup-windows.bat"
echo "  5. Test: go.bat window-inspection \"SELECT * FROM leads LIMIT 5\" --dry-run"
echo ""
echo "⚠️  TERMINATE WHEN DONE (costs ~\$0.05/hr while running):"
echo "   AWS_PROFILE=wbb-admin aws ec2 terminate-instances --instance-ids $INSTANCE_ID --region $REGION"
echo ""
