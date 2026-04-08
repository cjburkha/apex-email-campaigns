#!/bin/bash
# Shortcut: ./go <campaign> <query> [--dry-run] [--limit N]
# Example:  ./go window-inspection "SELECT * FROM leads WHERE test_lead = 1"
# Example:  ./go window-inspection "SELECT * FROM leads WHERE test_lead = 1" --dry-run
cd "$(dirname "$0")"
source venv/bin/activate
python send_outlook.py --campaign "$1" --query "$2" "${@:3}"
