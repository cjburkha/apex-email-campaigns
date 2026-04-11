@echo off
:: Apex Email Campaigns — Windows Setup
:: Double-click this file to install everything.
:: You will need your Username and Password from your admin.

powershell -NoProfile -ExecutionPolicy Bypass -Command "& {
    Write-Host ''
    Write-Host '╔══════════════════════════════════════════════╗'
    Write-Host '║   Apex Email Campaigns — Windows Setup       ║'
    Write-Host '╚══════════════════════════════════════════════╝'
    Write-Host ''

    # ── Install folder ────────────────────────────────────────────────────────
    $installDir = Join-Path $env:USERPROFILE 'apex-campaigns'
    if (-not (Test-Path $installDir)) {
        New-Item -ItemType Directory -Path $installDir | Out-Null
    }
    Set-Location $installDir

    # ── Python check ──────────────────────────────────────────────────────────
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        Write-Host ''
        Write-Host '❌  Python is not installed.'
        Write-Host '    1. Go to: https://www.python.org/downloads/'
        Write-Host '    2. Download and install Python 3.11 or newer'
        Write-Host '    3. IMPORTANT: check the box that says Add Python to PATH'
        Write-Host '    4. Re-run this setup after installing Python.'
        Write-Host ''
        pause
        Start-Process 'https://www.python.org/downloads/'
        exit 1
    }
    $pyVer = python -c 'import sys; print(f\"{sys.version_info.major}.{sys.version_info.minor}\")'
    Write-Host \"✔  Python $pyVer found\"

    # ── Download latest code from GitHub ──────────────────────────────────────
    Write-Host '→  Downloading latest version...'
    $zipUrl  = 'https://github.com/cjburkha/apex-email-campaigns/archive/refs/heads/master.zip'
    $zipPath = Join-Path $env:TEMP 'apex-campaigns.zip'
    $tmpDir  = Join-Path $env:TEMP 'apex-campaigns-extract'

    Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -UseBasicParsing
    if (Test-Path $tmpDir) { Remove-Item $tmpDir -Recurse -Force }
    Expand-Archive -Path $zipPath -DestinationPath $tmpDir -Force

    # Copy files (merge, keep existing .env and venv)
    $srcDir = Join-Path $tmpDir 'apex-email-campaigns-master'
    Get-ChildItem $srcDir | ForEach-Object {
        $dest = Join-Path $installDir $_.Name
        if ($_.Name -notin @('venv', '.env')) {
            Copy-Item $_.FullName $dest -Recurse -Force
        }
    }
    Remove-Item $zipPath -Force
    Remove-Item $tmpDir -Recurse -Force
    Write-Host '✔  Files downloaded'

    # ── Virtual environment ───────────────────────────────────────────────────
    if (-not (Test-Path 'venv')) {
        Write-Host '→  Setting up Python environment (one-time, takes ~30 seconds)...'
        python -m venv venv
    }

    # ── Dependencies ──────────────────────────────────────────────────────────
    Write-Host '→  Installing dependencies...'
    & '.\venv\Scripts\python.exe' -m pip install -q --upgrade pip
    & '.\venv\Scripts\pip.exe' install -q -r requirements.txt
    Write-Host '✔  Dependencies ready'

    # ── Credentials ───────────────────────────────────────────────────────────
    Write-Host ''
    Write-Host '── Login ───────────────────────────────────────────────────────────────'
    Write-Host '   Your credentials will be saved securely to Windows Credential Manager.'
    Write-Host '   They will NOT be stored in any file.'
    Write-Host ''

    $existing = & '.\venv\Scripts\python.exe' -c \"
import keyring
v = keyring.get_password('apex-campaigns', 'DATABASE_URL')
print(v if v else '')
\" 2>`$null

    if ($existing) {
        Write-Host '✔  Already logged in — credentials found in Windows Credential Manager'
    } else {
        $dbUser = Read-Host '  Username'
        $dbPassSecure = Read-Host '  Password' -AsSecureString
        $dbPass = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto(
                    [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($dbPassSecure))
        & '.\venv\Scripts\python.exe' -c \"
import keyring
keyring.set_password('apex-campaigns', 'DATABASE_URL', '${dbUser}|${dbPass}')
print('✔  Credentials saved to Windows Credential Manager')
\"
    }

    # ── Desktop shortcut ──────────────────────────────────────────────────────
    $shortcutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Apex Campaigns.lnk'
    if (-not (Test-Path $shortcutPath)) {
        $shell    = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut($shortcutPath)
        $shortcut.TargetPath       = 'cmd.exe'
        $shortcut.Arguments        = '/K cd /d \"' + $installDir + '\" && venv\Scripts\activate'
        $shortcut.WorkingDirectory = $installDir
        $shortcut.Description      = 'Apex Email Campaigns'
        $shortcut.Save()
        Write-Host '✔  Shortcut created on Desktop: Apex Campaigns'
    }

    Write-Host ''
    Write-Host '✅  Setup complete!'
    Write-Host ''
    Write-Host '   To send emails, open the Apex Campaigns shortcut on your Desktop'
    Write-Host '   and run a command like:'
    Write-Host ''
    Write-Host '   go.bat window-inspection \"SELECT * FROM leads LIMIT 5\" --dry-run'
    Write-Host ''
}
pause
