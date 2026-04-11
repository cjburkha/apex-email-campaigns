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

# ── Credential setup ─────────────────────────────────────────────────────────
Write-Host ""
Write-Host "── Database credentials ────────────────────────────────────────────────"
Write-Host "   Credentials are saved to Windows Credential Manager — never written to disk."
Write-Host "   Ask your admin for your personal DATABASE_URL."
Write-Host ""

$existingUrl = python -c "
import keyring, sys
url = keyring.get_password('apex-campaigns', 'DATABASE_URL')
if url: print(url)
" 2>$null

if ($existingUrl) {
    Write-Host "✔  Credentials already saved in Windows Credential Manager — skipping"
} else {
    $dbUser = Read-Host "  Username"
    $dbPassSecure = Read-Host "  Password" -AsSecureString
    $dbPass = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto(
                [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($dbPassSecure))
    python -c "import keyring; keyring.set_password('apex-campaigns', 'DATABASE_URL', '${dbUser}|${dbPass}'); print('✔  Credentials saved to Windows Credential Manager')"
}

# Write non-sensitive config to .env (no secrets here)
if (-not (Test-Path ".env")) {
    $awsKey   = Read-Host "  AWS_ACCESS_KEY_ID"
    $awsSecretSecure = Read-Host "  AWS_SECRET_ACCESS_KEY" -AsSecureString
    $awsSecret = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto(
                    [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($awsSecretSecure))
    $awsRegion = Read-Host "  AWS_REGION [us-east-1]"
    if (-not $awsRegion) { $awsRegion = "us-east-1" }
    $sesConfig = Read-Host "  SES_CONFIG_SET [apex-campaigns]"
    if (-not $sesConfig) { $sesConfig = "apex-campaigns" }
    $sesQueue  = Read-Host "  SES_EVENTS_QUEUE_URL"

    $envContent = @"
AWS_ACCESS_KEY_ID=$awsKey
AWS_SECRET_ACCESS_KEY=$awsSecret
AWS_REGION=$awsRegion
SES_CONFIG_SET=$sesConfig
SES_EVENTS_QUEUE_URL=$sesQueue
"@
    Set-Content -Path ".env" -Value $envContent

    $acl = Get-Acl ".env"
    $acl.SetAccessRuleProtection($true, $false)
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
        $env:USERNAME, "FullControl", "Allow"
    )
    $acl.SetAccessRule($rule)
    Set-Acl ".env" $acl
    Write-Host "✔  .env created (no DB credentials stored — those are in Credential Manager)"
} else {
    Write-Host "✔  .env already exists — skipping"
}

Write-Host ""
Write-Host "✅  Installation complete!"
Write-Host ""
Write-Host "   Test with:"
Write-Host '   .\go.bat window-inspection "SELECT * FROM leads LIMIT 5" --dry-run'
Write-Host ""
