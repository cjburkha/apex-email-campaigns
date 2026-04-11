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

# ── .env setup ────────────────────────────────────────────────────────────────
if [ -f ".env" ]; then
    echo "✔  .env already exists — skipping credential setup"
else
    echo ""
    echo "── Database & AWS credentials ──────────────────────────────────────────"
    echo "   (credentials are written to .env with chmod 600 — owner-read only)"
    echo ""

    read -p    "  DATABASE_URL : " DB_URL
    read -p    "  AWS_ACCESS_KEY_ID : " AWS_KEY
    read -s -p "  AWS_SECRET_ACCESS_KEY (hidden): " AWS_SECRET
    echo ""
    read -p    "  AWS_REGION [us-east-1]: " AWS_REGION
    AWS_REGION="${AWS_REGION:-us-east-1}"
    read -p    "  SES_CONFIG_SET [apex-campaigns]: " SES_CONFIG_SET
    SES_CONFIG_SET="${SES_CONFIG_SET:-apex-campaigns}"
    read -p    "  SES_EVENTS_QUEUE_URL : " SES_QUEUE

    cat > .env <<EOF
DATABASE_URL="${DB_URL}"
AWS_ACCESS_KEY_ID=${AWS_KEY}
AWS_SECRET_ACCESS_KEY=${AWS_SECRET}
AWS_REGION=${AWS_REGION}
SES_CONFIG_SET=${SES_CONFIG_SET}
SES_EVENTS_QUEUE_URL=${SES_QUEUE}
EOF

    # Restrict to owner read/write only — no other users can read credentials
    chmod 600 .env
    echo "✔  .env created (permissions: 600)"
fi

# ── Executable bits ───────────────────────────────────────────────────────────
chmod +x go 2>/dev/null || true

echo ""
echo "✅  Installation complete!"
echo ""
echo "   Test with:"
echo "   ./go window-inspection \"SELECT * FROM leads LIMIT 5\" --dry-run"
echo ""
