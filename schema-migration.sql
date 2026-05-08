-- Schema migration for drip campaigns and SMS support
-- Run as: psql -h wbb-prod.c81qkua4c3e2.us-east-1.rds.amazonaws.com -U wbbadmin -d apex -f schema-migration.sql
-- Contact: chris@windowsbyburkhardt.com

BEGIN TRANSACTION;

-- Add unsubscribe column to leads table
ALTER TABLE leads 
ADD COLUMN IF NOT EXISTS unsubscribed_at TIMESTAMPTZ;

-- Add SMS and drip columns to campaign_sends table
ALTER TABLE campaign_sends 
ADD COLUMN IF NOT EXISTS sms_message_id TEXT,
ADD COLUMN IF NOT EXISTS sms_sent_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS sms_delivered_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS sms_status TEXT,
ADD COLUMN IF NOT EXISTS drip_step INTEGER NOT NULL DEFAULT 1,
ADD COLUMN IF NOT EXISTS next_send_at TIMESTAMPTZ;

-- Create indexes for drip campaign queries
CREATE INDEX IF NOT EXISTS idx_campaign_sends_drip_status 
ON campaign_sends(campaign_id, status, next_send_at) 
WHERE status != 'completed';

CREATE INDEX IF NOT EXISTS idx_campaign_sends_next_send 
ON campaign_sends(next_send_at, status) 
WHERE status IN ('queued', 'sent');

-- Grant permissions to cburkhardt
GRANT SELECT, UPDATE ON leads TO cburkhardt;
GRANT SELECT, INSERT, UPDATE ON campaigns TO cburkhardt;
GRANT SELECT, INSERT, UPDATE ON campaign_sends TO cburkhardt;

COMMIT;

-- Verify columns were added
SELECT 'Schema migration complete!' as status;
SELECT COUNT(*) as campaign_sends_drip_steps FROM campaign_sends WHERE drip_step IS NOT NULL;
SELECT COUNT(*) as leads_unsubscribed FROM leads WHERE unsubscribed_at IS NOT NULL;
