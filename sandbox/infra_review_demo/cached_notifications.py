"""Static acceptance control. Do not execute: the helper reads a real S3 object."""

from functools import lru_cache

import boto3

s3 = boto3.client("s3")


@lru_cache(maxsize=32)
def read_attachment(bucket, key):
    return s3.get_object(Bucket=bucket, Key=key)["Body"].read()


def prepare_notifications(recipients, bucket, key):
    attachment = read_attachment(bucket, key)
    return [(recipient, attachment) for recipient in recipients]
