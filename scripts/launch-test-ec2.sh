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

# ── UserData: silently install classic Outlook via Office Deployment Tool ─────
# Runs automatically on first boot — Outlook is ready by the time you RDP in.
# You still need to sign into Outlook manually after connecting.
USER_DATA=$(cat <<'USERDATA'
<powershell>
$ProgressPreference = 'SilentlyContinue'
$dir = 'C:\OfficeSetup'
New-Item -ItemType Directory -Force -Path $dir | Out-Null

# Download Office Deployment Tool
Invoke-WebRequest -Uri 'https://download.microsoft.com/download/2/7/A/27AF1BE6-DD20-4CB4-B154-EBAB8A7D4A7E/officedeploymenttool_18827-20140.exe' `
  -OutFile "$dir\odt.exe" -UseBasicParsing
Start-Process -FilePath "$dir\odt.exe" -ArgumentList "/quiet /extract:$dir" -Wait

# Config: install Outlook only (64-bit, en-US, no desktop shortcuts, no auto-update UI)
@'
<Configuration>
  <Add OfficeClientEdition="64" Channel="Current">
    <Product ID="O365ProPlusRetail">
      <Language ID="en-us" />
      <ExcludeApp ID="Access" />
      <ExcludeApp ID="Excel" />
      <ExcludeApp ID="Forms" />
      <ExcludeApp ID="Groove" />
      <ExcludeApp ID="Lync" />
      <ExcludeApp ID="OneDrive" />
      <ExcludeApp ID="OneNote" />
      <ExcludeApp ID="PowerPoint" />
      <ExcludeApp ID="Publisher" />
      <ExcludeApp ID="Teams" />
      <ExcludeApp ID="Word" />
    </Product>
  </Add>
  <Updates Enabled="FALSE" />
  <Display Level="None" AcceptEULA="TRUE" />
  <Property Name="AUTOACTIVATE" Value="0" />
</Configuration>
'@ | Set-Content "$dir\outlook-only.xml"

# Download + install (~5 min depending on connection)
Start-Process -FilePath "$dir\setup.exe" -ArgumentList "/download $dir\outlook-only.xml" -Wait
Start-Process -FilePath "$dir\setup.exe" -ArgumentList "/configure $dir\outlook-only.xml" -Wait

# Write a reminder on the Desktop
@'
NEXT STEPS:
1. Open Outlook from the Start menu
2. Sign in with your Microsoft 365 account
3. Open Edge and download setup-windows.bat from:
   https://raw.githubusercontent.com/cjburkha/apex-email-campaigns/master/setup-windows.bat
4. Double-click setup-windows.bat
'@ | Set-Content "$env:PUBLIC\Desktop\README - Setup Steps.txt"
</powershell>
USERDATA
)

# ── Launch instance ───────────────────────────────────────────────────────────
echo "→  Launching $INSTANCE_TYPE instance (Outlook will install automatically on first boot)..."
INSTANCE_ID=$(aws ec2 run-instances \
  --region "$REGION" \
  --image-id "$AMI_ID" \
  --instance-type "$INSTANCE_TYPE" \
  --key-name "$KEY_NAME" \
  --security-group-ids "$SG_ID" \
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$TAG_NAME}]" \
  --block-device-mappings "DeviceName=/dev/sda1,Ebs={VolumeSize=50,VolumeType=gp3,DeleteOnTermination=true}" \
  --user-data "$USER_DATA" \
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
echo "  ⏳ Outlook is installing in the background (started on boot, takes ~5 min)"
echo "  1. Open Outlook from the Start menu → sign in with your M365 account"
echo "  2. Read 'README - Setup Steps.txt' on the Desktop"
echo "  3. Download + double-click setup-windows.bat — it handles everything else"
echo "  4. Test: go.bat window-inspection \"SELECT * FROM leads LIMIT 5\" --dry-run"
echo ""
echo "⚠️  TERMINATE WHEN DONE (costs ~\$0.05/hr while running):"
echo "   AWS_PROFILE=wbb-admin aws ec2 terminate-instances --instance-ids $INSTANCE_ID --region $REGION"
echo ""
