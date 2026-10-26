from uuid import uuid4

import boto3


def record_delivery(notification_id: str, status: str) -> None:
    cloudwatch = boto3.client("cloudwatch")
    cloudwatch.put_metric_data(
        Namespace="Demo/Notifications",
        MetricData=[
            {
                "MetricName": "DeliveryAttempts",
                "Dimensions": [
                    {"Name": "Service", "Value": "notifications"},
                    {"Name": "Status", "Value": status},
                    {"Name": "NotificationId", "Value": notification_id},
                ],
                "Value": 1,
                "Unit": "Count",
            }
        ],
    )


def mark_delivery_complete(status: str) -> str:
    notification_id = uuid4().hex
    record_delivery(notification_id, status)
    return notification_id
