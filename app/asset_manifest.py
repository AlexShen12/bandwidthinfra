import boto3


def build_manifest(recipients: list[str], bucket: str, key: str) -> list[dict]:
    s3 = boto3.client("s3")
    asset_size = s3.head_object(Bucket=bucket, Key=key)["ContentLength"]
    return [{"recipient": recipient, "asset_size": asset_size} for recipient in recipients]
