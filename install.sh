#!/bin/bash
# install.sh — macOS/Linux installer for apex-email-campaigns
# Run once: bash install.sh
set -e

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║   Apex Email Campaigns — macOS Installer     ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

# ── Python check ──────────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "❌  Python 3 not found. Install from https://python.org"
    exit 1
fi
PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "✔  Python $PY_VER"

# ── Virtual environment ───────────────────────────────────────────────────────
if [ ! -d "venv" ]; then
    echo "→  Creating virtual environment..."
    python3 -m venv venv
fi
source venv/bin/activate

# ── Dependencies ──────────────────────────────────────────────────────────────
echo "→  Installing dependencies..."
pip install -q --upgrade pip
pip install -q -r requirements.txt
echo "✔  Dependencies installed"

# ── Credential setup ─────────────────────────────────────────────────────────
echo ""
echo "── Database credentials ────────────────────────────────────────────────"
echo "   Credentials are saved to the macOS Keychain — never written to disk."
echo "   Ask your admin for your personal DATABASE_URL."
echo ""

# Check if already saved in keyring
EXISTING=$(python3 -c "
import keyring, sys
url = keyring.get_password('apex-campaigns', 'DATABASE_URL')
if url: print(url)
" 2>/dev/null)

if [ -n "$EXISTING" ]; then
    echo "✔  DATABASE_URL already saved in macOS Keychain — skipping"
else
    read -s -p "  DATABASE_URL (hidden): " DB_URL
    echo ""
    python3 -c "
import keyring
keyring.set_password('apex-campaigns', 'DATABASE_URL', '$DB_URL')
print('✔  DATABASE_URL saved to macOS Keychain')
"
fi

# Write non-sensitive config to .env (no secrets here)
if [ ! -f ".env" ]; then
    read -p    "  AWS_ACCESS_KEY_ID : " AWS_KEY
    read -s -p "  AWS_SECRET_ACCESS_KEY (hidden): " AWS_SECRET
    echo ""
    read -p    "  AWS_REGION [us-east-1]: " AWS_REGION
    AWS_REGION="${AWS_REGION:-us-east-1}"
    read -p    "  SES_CONFIG_SET [apex-campaigns]: " SES_CONFIG_SET
    SES_CONFIG_SET="${SES_CONFIG_SET:-apex-campaigns}"
    read -p    "  SES_EVENTS_QUEUE_URL : " SES_QUEUE

    cat > .env <<EOF
AWS_ACCESS_KEY_ID=${AWS_KEY}
AWS_SECRET_ACCESS_KEY=${AWS_SECRET}
AWS_REGION=${AWS_REGION}
SES_CONFIG_SET=${SES_CONFIG_SET}
SES_EVENTS_QUEUE_URL=${SES_QUEUE}
EOF
    chmod 600 .env
    echo "✔  .env created (permissions: 600, no DB credentials stored)"
else
    echo "✔  .env already exists — skipping"
fi

# ── Executable bits ───────────────────────────────────────────────────────────
chmod +x go 2>/dev/null || true

echo ""
echo "✅  Installation complete!"
echo ""
echo "   Test with:"
echo "   ./go window-inspection \"SELECT * FROM leads LIMIT 5\" --dry-run"
echo ""
