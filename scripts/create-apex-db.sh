#!/usr/bin/env bash
# Creates the 'apex' database on the existing wbb-prod RDS instance.
# Run this once before running scripts/migrate-to-pg.py.
#
# Usage: bash scripts/create-apex-db.sh
set -e

export AWS_PROFILE="wbb-admin"

REGION="us-east-1"
DB_IDENTIFIER="wbb-prod"
DB_USER="wbbadmin"
DB_PORT=5432

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║   Apex Email Campaigns — DB Setup            ║"
echo "║   Target: wbb-prod RDS (new 'apex' database) ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

# ── Fetch RDS endpoint ────────────────────────────────────────────────────────
ENDPOINT=$(aws rds describe-db-instances \
  --region "$REGION" \
  --db-instance-identifier "$DB_IDENTIFIER" \
  --query "DBInstances[0].Endpoint.Address" \
  --output text)

if [ -z "$ENDPOINT" ] || [ "$ENDPOINT" = "None" ]; then
  echo "❌  Could not find '$DB_IDENTIFIER' RDS instance in $REGION."
  exit 1
fi
echo "✔  RDS endpoint: $ENDPOINT"

# ── Prompt for DB password ────────────────────────────────────────────────────
read -s -p "Enter password for '$DB_USER' on $DB_IDENTIFIER: " DB_PASSWORD
echo ""

# ── Ensure your current IP has inbound access ─────────────────────────────────
MY_IP=$(curl -s https://checkip.amazonaws.com)/32
SG_ID=$(aws rds describe-db-instances \
  --region "$REGION" \
  --db-instance-identifier "$DB_IDENTIFIER" \
  --query "DBInstances[0].VpcSecurityGroups[0].VpcSecurityGroupId" \
  --output text)
echo "    Ensuring port 5432 open for your IP ($MY_IP) on $SG_ID..."
aws ec2 authorize-security-group-ingress \
  --region "$REGION" \
  --group-id "$SG_ID" \
  --protocol tcp \
  --port "$DB_PORT" \
  --cidr "$MY_IP" 2>/dev/null && echo "✔  Inbound rule added" || echo "–  Rule already exists"

# ── Create the 'apex' database ────────────────────────────────────────────────
echo ""
echo "── Creating 'apex' database ──────────────────────"
PGPASSWORD="$DB_PASSWORD" psql \
  -h "$ENDPOINT" -U "$DB_USER" -d wbb -p "$DB_PORT" \
  -c "CREATE DATABASE apex;" 2>/dev/null \
  && echo "✔  Database 'apex' created" \
  || echo "–  Database 'apex' already exists — skipping"

# ── Construct DATABASE_URL ────────────────────────────────────────────────────
DATABASE_URL="postgresql://${DB_USER}:${DB_PASSWORD}@${ENDPOINT}:${DB_PORT}/apex?sslmode=require"

# Save password to temp file for migrate-to-pg.py
echo -n "$DATABASE_URL" > /tmp/apex-database-url

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║  DONE — add this to your .env:              ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
echo "DATABASE_URL=$DATABASE_URL"
echo ""
echo "Next steps:"
echo "  1. Add DATABASE_URL to your .env file"
echo "  2. python -c 'from db import init_db; init_db()'   — creates schema"
echo "  3. python scripts/migrate-to-pg.py                 — copies SQLite data"
echo ""
