#!/usr/bin/env bash
# Deploy SES email forwarder: chris@windowsbyburkhardt.com → chris.burkhardt@live.com
set -euo pipefail

PROFILE="wbb-admin"
REGION="us-east-1"
ACCOUNT="669143131098"
ZONE_ID="Z0069181X6QIMOMW5RHO"

BUCKET="wbb-inbound-email"
LAMBDA_NAME="ses-email-forwarder"
ROLE_NAME="ses-email-forwarder-role"
RULE_SET="wbb-inbound"
RULE_NAME="forward-chris"
FORWARD_TO="chris.burkhardt@live.com"
FORWARD_FROM="chris@windowsbyburkhardt.com"
MAIL_PREFIX="inbound/"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAMBDA_DIR="$SCRIPT_DIR/email-forwarder"

echo "=== 1. S3 bucket for inbound mail ==="
if aws s3api head-bucket --bucket "$BUCKET" --profile "$PROFILE" 2>/dev/null; then
    echo "Bucket $BUCKET already exists"
else
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" --profile "$PROFILE"
    echo "Created bucket: $BUCKET"
fi

# Block public access
aws s3api put-public-access-block --bucket "$BUCKET" \
    --public-access-block-configuration "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true" \
    --profile "$PROFILE"

# Bucket policy: allow SES to write inbound mail
aws s3api put-bucket-policy --bucket "$BUCKET" --profile "$PROFILE" --policy "{
  \"Version\": \"2012-10-17\",
  \"Statement\": [{
    \"Sid\": \"AllowSESPuts\",
    \"Effect\": \"Allow\",
    \"Principal\": { \"Service\": \"ses.amazonaws.com\" },
    \"Action\": \"s3:PutObject\",
    \"Resource\": \"arn:aws:s3:::$BUCKET/$MAIL_PREFIX*\",
    \"Condition\": { \"StringEquals\": { \"aws:Referer\": \"$ACCOUNT\" } }
  }]
}"
echo "Bucket policy applied"

echo ""
echo "=== 2. IAM role for Lambda ==="
TRUST='{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "lambda.amazonaws.com" },
    "Action": "sts:AssumeRole"
  }]
}'

ROLE_ARN=$(aws iam get-role --role-name "$ROLE_NAME" --profile "$PROFILE" \
    --query "Role.Arn" --output text 2>/dev/null || true)

if [ -z "$ROLE_ARN" ]; then
    ROLE_ARN=$(aws iam create-role --role-name "$ROLE_NAME" \
        --assume-role-policy-document "$TRUST" \
        --profile "$PROFILE" --query "Role.Arn" --output text)
    echo "Created role: $ROLE_ARN"
else
    echo "Role already exists: $ROLE_ARN"
fi

# Attach managed policy for CloudWatch Logs
aws iam attach-role-policy --role-name "$ROLE_NAME" \
    --policy-arn "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole" \
    --profile "$PROFILE" 2>/dev/null || true

# Inline policy: S3 read + SES send
aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name "ses-forwarder-policy" \
    --profile "$PROFILE" --policy-document "{
  \"Version\": \"2012-10-17\",
  \"Statement\": [
    {
      \"Effect\": \"Allow\",
      \"Action\": [\"s3:GetObject\"],
      \"Resource\": \"arn:aws:s3:::$BUCKET/*\"
    },
    {
      \"Effect\": \"Allow\",
      \"Action\": [\"ses:SendRawEmail\"],
      \"Resource\": \"*\"
    }
  ]
}"
echo "IAM policy applied"

echo ""
echo "=== 3. Deploy Lambda ==="
cd "$LAMBDA_DIR"
zip -q lambda.zip lambda_function.py

# Wait for role to propagate
sleep 10

LAMBDA_ARN=$(aws lambda get-function --function-name "$LAMBDA_NAME" --profile "$PROFILE" \
    --region "$REGION" --query "Configuration.FunctionArn" --output text 2>/dev/null || true)

if [ -z "$LAMBDA_ARN" ]; then
    LAMBDA_ARN=$(aws lambda create-function \
        --function-name "$LAMBDA_NAME" \
        --runtime python3.11 \
        --role "$ROLE_ARN" \
        --handler lambda_function.lambda_handler \
        --zip-file fileb://lambda.zip \
        --timeout 30 \
        --environment "Variables={FORWARD_TO=$FORWARD_TO,MAIL_BUCKET=$BUCKET,MAIL_PREFIX=$MAIL_PREFIX}" \
        --region "$REGION" --profile "$PROFILE" \
        --query "FunctionArn" --output text)
    echo "Created Lambda: $LAMBDA_ARN"
else
    aws lambda update-function-code \
        --function-name "$LAMBDA_NAME" \
        --zip-file fileb://lambda.zip \
        --region "$REGION" --profile "$PROFILE" > /dev/null
    aws lambda update-function-configuration \
        --function-name "$LAMBDA_NAME" \
        --environment "Variables={FORWARD_TO=$FORWARD_TO,MAIL_BUCKET=$BUCKET,MAIL_PREFIX=$MAIL_PREFIX}" \
        --region "$REGION" --profile "$PROFILE" > /dev/null
    echo "Updated Lambda: $LAMBDA_ARN"
fi
rm lambda.zip
cd "$SCRIPT_DIR"

# Allow SES to invoke Lambda
aws lambda add-permission \
    --function-name "$LAMBDA_NAME" \
    --statement-id "ses-invoke" \
    --action "lambda:InvokeFunction" \
    --principal "ses.amazonaws.com" \
    --source-account "$ACCOUNT" \
    --region "$REGION" --profile "$PROFILE" 2>/dev/null || echo "SES invoke permission already set"

echo ""
echo "=== 4. SES Receipt Rule Set ==="
aws ses create-receipt-rule-set --rule-set-name "$RULE_SET" \
    --profile "$PROFILE" --region "$REGION" 2>/dev/null || echo "Rule set already exists"

aws ses set-active-receipt-rule-set --rule-set-name "$RULE_SET" \
    --profile "$PROFILE" --region "$REGION"
echo "Active rule set: $RULE_SET"

echo ""
echo "=== 5. SES Receipt Rule ==="
aws ses create-receipt-rule --rule-set-name "$RULE_SET" --profile "$PROFILE" --region "$REGION" \
    --rule "{
  \"Name\": \"$RULE_NAME\",
  \"Enabled\": true,
  \"Recipients\": [\"$FORWARD_FROM\"],
  \"Actions\": [
    {
      \"S3Action\": {
        \"BucketName\": \"$BUCKET\",
        \"ObjectKeyPrefix\": \"$MAIL_PREFIX\"
      }
    },
    {
      \"LambdaAction\": {
        \"FunctionArn\": \"arn:aws:lambda:$REGION:$ACCOUNT:function:$LAMBDA_NAME\",
        \"InvocationType\": \"Event\"
      }
    }
  ],
  \"ScanEnabled\": true
}" 2>/dev/null || echo "Receipt rule already exists"
echo "Receipt rule created: $RULE_NAME"

echo ""
echo "=== 6. Route53 MX Record ==="
aws route53 change-resource-record-sets --hosted-zone-id "$ZONE_ID" \
    --profile "$PROFILE" --change-batch '{
  "Changes": [{
    "Action": "UPSERT",
    "ResourceRecordSet": {
      "Name": "windowsbyburkhardt.com.",
      "Type": "MX",
      "TTL": 300,
      "ResourceRecords": [
        { "Value": "10 inbound-smtp.us-east-1.amazonaws.com" }
      ]
    }
  }]
}' | python3 -c "import sys,json; r=json.load(sys.stdin); print(f'MX record change: {r[\"ChangeInfo\"][\"Status\"]}')"

echo ""
echo "=== Done ==="
echo "Emails to $FORWARD_FROM will be forwarded to $FORWARD_TO"
echo "Subject prefix: [forwarded for chris@wbb]"
echo "Reply-To is preserved so replies go to original senders"
