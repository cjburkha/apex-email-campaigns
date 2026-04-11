# install.ps1 — Windows installer for apex-email-campaigns
# Run once (as your normal user, NOT as Administrator):
#
#   Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
#   .\install.ps1

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host ""
Write-Host "╔══════════════════════════════════════════════╗"
Write-Host "║   Apex Email Campaigns — Windows Installer   ║"
Write-Host "╚══════════════════════════════════════════════╝"
Write-Host ""

# ── Python check ──────────────────────────────────────────────────────────────
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "❌  Python not found. Install from https://python.org (check 'Add to PATH')"
    exit 1
}
$pyVer = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
Write-Host "✔  Python $pyVer"

# ── Virtual environment ───────────────────────────────────────────────────────
if (-not (Test-Path "venv")) {
    Write-Host "→  Creating virtual environment..."
    python -m venv venv
}
& ".\venv\Scripts\Activate.ps1"

# ── Dependencies ──────────────────────────────────────────────────────────────
Write-Host "→  Installing dependencies..."
python -m pip install -q --upgrade pip
pip install -q -r requirements.txt
Write-Host "✔  Dependencies installed"

# ── .env setup ────────────────────────────────────────────────────────────────
if (Test-Path ".env") {
    Write-Host "✔  .env already exists — skipping credential setup"
} else {
    Write-Host ""
    Write-Host "── Database & AWS credentials ──────────────────────────────────────────"
    Write-Host "   (stored in .env, restricted to your Windows account only)"
    Write-Host ""

    $dbUrl    = Read-Host "  DATABASE_URL"
    $awsKey   = Read-Host "  AWS_ACCESS_KEY_ID"
    # Read-Host -AsSecureString hides the input — password never echoed to screen
    $awsSecretSecure = Read-Host "  AWS_SECRET_ACCESS_KEY" -AsSecureString
    $awsSecret = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto(
                    [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($awsSecretSecure))
    $awsRegion = Read-Host "  AWS_REGION [us-east-1]"
    if (-not $awsRegion) { $awsRegion = "us-east-1" }
    $sesConfig = Read-Host "  SES_CONFIG_SET [apex-campaigns]"
    if (-not $sesConfig) { $sesConfig = "apex-campaigns" }
    $sesQueue  = Read-Host "  SES_EVENTS_QUEUE_URL"

    $envContent = @"
DATABASE_URL="$dbUrl"
AWS_ACCESS_KEY_ID=$awsKey
AWS_SECRET_ACCESS_KEY=$awsSecret
AWS_REGION=$awsRegion
SES_CONFIG_SET=$sesConfig
SES_EVENTS_QUEUE_URL=$sesQueue
"@
    Set-Content -Path ".env" -Value $envContent

    # Restrict .env to current user only (remove inherited permissions, grant only owner)
    $acl = Get-Acl ".env"
    $acl.SetAccessRuleProtection($true, $false)   # break inheritance, remove inherited
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
        $env:USERNAME, "FullControl", "Allow"
    )
    $acl.SetAccessRule($rule)
    Set-Acl ".env" $acl
    Write-Host "✔  .env created (restricted to $env:USERNAME only)"
}

Write-Host ""
Write-Host "✅  Installation complete!"
Write-Host ""
Write-Host "   Test with:"
Write-Host '   .\go.bat window-inspection "SELECT * FROM leads LIMIT 5" --dry-run'
Write-Host ""
