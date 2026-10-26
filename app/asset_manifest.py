import boto3


def build_manifest(recipients: list[str], bucket: str, key: str) -> list[dict]:
    s3 = boto3.client("s3")
    return [
        {
            "recipient": recipient,
            "asset_size": s3.head_object(Bucket=bucket, Key=key)["ContentLength"],
        }
        for recipient in recipients
    ]
