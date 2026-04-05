#!/usr/bin/env python3
"""
setup_aws.py — One-time AWS infrastructure setup for campaign tracking.

Creates:
  1. SES v2 configuration set  (apex-campaigns)
  2. SNS topic                  (apex-campaigns-events)
  3. SQS queue                  (apex-campaigns-events)
  4. SQS policy allowing SNS to publish
  5. SNS → SQS subscription
  6. SES event destination → SNS  (all event types: send/bounce/open/click/…)

Run once.  Prints the SES_EVENTS_QUEUE_URL to add to your .env.

Usage:
    python setup_aws.py
    python setup_aws.py --region us-east-1 --config-set apex-campaigns
"""

import json
import os

import boto3
import click
from dotenv import load_dotenv

load_dotenv()


@click.command()
@click.option("--region",      default=lambda: os.getenv("AWS_REGION", "us-east-1"), show_default=True)
@click.option("--config-set",  default="apex-campaigns",        show_default=True)
@click.option("--topic-name",  default="apex-campaigns-events", show_default=True)
@click.option("--queue-name",  default="apex-campaigns-events", show_default=True)
def setup(region, config_set, topic_name, queue_name):
    """Create SES / SNS / SQS infrastructure for event tracking."""

    sts        = boto3.client("sts",   region_name=region)
    account_id = sts.get_caller_identity()["Account"]
    click.echo(f"\n🔧  Setting up in {region}  (account {account_id})\n")

    # ── 1. SES configuration set ──────────────────────────────────────────────
    ses = boto3.client("sesv2", region_name=region)
    try:
        ses.create_configuration_set(ConfigurationSetName=config_set)
        click.echo(f"  ✓  Created SES configuration set: {config_set}")
    except ses.exceptions.AlreadyExistsException:
        click.echo(f"  –  SES configuration set already exists: {config_set}")

    # ── 2. SNS topic ──────────────────────────────────────────────────────────
    sns       = boto3.client("sns", region_name=region)
    topic_arn = sns.create_topic(Name=topic_name)["TopicArn"]
    click.echo(f"  ✓  SNS topic: {topic_arn}")

    # ── 3. SQS queue ──────────────────────────────────────────────────────────
    sqs       = boto3.client("sqs", region_name=region)
    queue_url = sqs.create_queue(
        QueueName=queue_name,
        Attributes={"MessageRetentionPeriod": "1209600"}   # 14 days
    )["QueueUrl"]
    queue_arn = sqs.get_queue_attributes(
        QueueUrl=queue_url, AttributeNames=["QueueArn"]
    )["Attributes"]["QueueArn"]
    click.echo(f"  ✓  SQS queue: {queue_url}")

    # ── 4. SQS policy — allow SNS to publish ──────────────────────────────────
    policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Sid":       "AllowSNSPublish",
            "Effect":    "Allow",
            "Principal": {"Service": "sns.amazonaws.com"},
            "Action":    "sqs:SendMessage",
            "Resource":  queue_arn,
            "Condition": {"ArnEquals": {"aws:SourceArn": topic_arn}},
        }]
    }
    sqs.set_queue_attributes(
        QueueUrl=queue_url,
        Attributes={"Policy": json.dumps(policy)}
    )
    click.echo("  ✓  SQS policy set (allows SNS)")

    # ── 5. Subscribe SQS to SNS (raw delivery = no envelope to unwrap) ────────
    sns.subscribe(
        TopicArn=topic_arn,
        Protocol="sqs",
        Endpoint=queue_arn,
        Attributes={"RawMessageDelivery": "true"},
    )
    click.echo("  ✓  Subscribed SQS to SNS (raw delivery)")

    # ── 6. SES event destination → SNS ───────────────────────────────────────
    try:
        ses.create_configuration_set_event_destination(
            ConfigurationSetName=config_set,
            EventDestinationName=f"{config_set}-sns",
            EventDestination={
                "Enabled": True,
                "MatchingEventTypes": [
                    "SEND", "REJECT", "BOUNCE", "COMPLAINT",
                    "DELIVERY", "OPEN", "CLICK", "RENDERING_FAILURE",
                ],
                "SnsDestination": {"TopicArn": topic_arn},
            },
        )
        click.echo("  ✓  SES event destination → SNS")
    except ses.exceptions.AlreadyExistsException:
        click.echo("  –  SES event destination already exists")

    # ── Done ──────────────────────────────────────────────────────────────────
    click.secho(f"""
✅  Setup complete!

Add these to your .env:

    SES_CONFIG_SET={config_set}
    SES_EVENTS_QUEUE_URL={queue_url}
""", fg="green")


if __name__ == "__main__":
    setup()
