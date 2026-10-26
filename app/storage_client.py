"""Export writes for the bucket configured in infra/retention.tf."""

from uuid import uuid4

import boto3

REPORT_EXPORT_BUCKET = "bandwidth-static-demo-report-exports"


def store_report_export(account_id: str, report_json: bytes) -> str:
    key = f"exports/{account_id}/{uuid4().hex}.json"
    s3 = boto3.client("s3")
    s3.put_object(
        Bucket=REPORT_EXPORT_BUCKET,
        Key=key,
        Body=report_json,
        ContentType="application/json",
    )
    return key
