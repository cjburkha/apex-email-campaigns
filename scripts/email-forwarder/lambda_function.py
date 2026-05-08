import email
import email.policy

import boto3

s3 = boto3.client("s3")
ses = boto3.client("ses", region_name="us-east-1")

FORWARD_TO = "chris.burkhardt@live.com"
MAIL_BUCKET = "wbb-inbound-email"
MAIL_PREFIX = "inbound/"
FORWARD_FROM = "chris@windowsbyburkhardt.com"


def lambda_handler(event, context):
    record = event["Records"][0]["ses"]
    message_id = record["mail"]["messageId"]
    s3_key = f"{MAIL_PREFIX}{message_id}"

    obj = s3.get_object(Bucket=MAIL_BUCKET, Key=s3_key)
    raw = obj["Body"].read()

    msg = email.message_from_bytes(raw, policy=email.policy.compat32)

    original_subject = msg.get("Subject", "(no subject)")
    original_from = msg.get("From", "")

    # Prefix subject so recipient knows which address this arrived at
    del msg["Subject"]
    msg["Subject"] = f"[forwarded for chris@wbb] {original_subject}"

    # Re-address: From must be our verified domain so SES accepts it;
    # Reply-To preserves the original sender so replies go back to them.
    del msg["From"]
    msg["From"] = FORWARD_FROM

    if not msg.get("Reply-To") and original_from:
        msg["Reply-To"] = original_from

    del msg["To"]
    msg["To"] = FORWARD_TO

    # Strip DKIM signatures — they'll fail after header rewrite
    for header in ("DKIM-Signature", "DomainKey-Signature"):
        while header in msg:
            del msg[header]

    ses.send_raw_email(
        Source=FORWARD_FROM,
        Destinations=[FORWARD_TO],
        RawMessage={"Data": msg.as_bytes()},
    )
    return {"statusCode": 200}
