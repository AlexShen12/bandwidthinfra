"""Static acceptance fixture. Do not execute: this function reads a real S3 object."""

import boto3

s3 = boto3.client("s3")


def prepare_notifications(recipients, bucket, key):
    return [
        (recipient, s3.get_object(Bucket=bucket, Key=key)["Body"].read())
        for recipient in recipients
    ]
