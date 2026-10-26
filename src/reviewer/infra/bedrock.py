"""Bedrock Converse adapter and deterministic offline client."""

import json
import os
import time
from dataclasses import dataclass, field

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

from reviewer.infra.config import Settings
from reviewer.infra.models import CATEGORIES


class ModelError(RuntimeError):
    pass


@dataclass
class Completion:
    text: str
    model: str
    seconds: float = 0
    usage: dict = field(default_factory=dict)
    stop_reason: str = "end_turn"
    simulated: bool = False


class BedrockClient:
    simulated = False

    def __init__(self, settings: Settings, sdk_client=None):
        self.settings = settings
        if sdk_client is not None:
            self.client = sdk_client
            return
        if settings.auth_mode == "api-key":
            if not os.environ.get("AWS_BEARER_TOKEN_BEDROCK"):
                raise ModelError(
                    "No Bedrock API key is configured. Set the GitHub secret AWS_BEARER_TOKEN_BEDROCK to a fresh short-term key."
                )
            signature, scheme = "bearer", "httpBearerAuth"
        else:
            # Explicit IAM mode never lets a stale environment bearer override a
            # fresh role session. This CLI runs one authentication mode per process.
            os.environ.pop("AWS_BEARER_TOKEN_BEDROCK", None)
            signature, scheme = "v4", "sigv4"
        try:
            self.client = boto3.client(
                "bedrock-runtime",
                region_name=settings.region,
                config=Config(
                    signature_version=signature,
                    auth_scheme_preference=scheme,
                    connect_timeout=10,
                    read_timeout=180,
                    retries={"mode": "standard", "total_max_attempts": 2},
                ),
            )
        except (BotoCoreError, NoCredentialsError) as exc:
            raise ModelError(
                "AWS credentials are unavailable. Configure the selected Bedrock authentication mode."
            ) from exc

    def complete(self, system, user, max_tokens):
        started = time.monotonic()
        try:
            response = self.client.converse(
                modelId=self.settings.model_id,
                system=[{"text": system}],
                messages=[{"role": "user", "content": [{"text": user}]}],
                inferenceConfig={"maxTokens": max_tokens, "temperature": 0},
            )
        except ClientError as exc:
            error = exc.response.get("Error", {})
            code, message = error.get("Code", ""), error.get("Message", "").lower()
            if (
                code
                in {
                    "ExpiredTokenException",
                    "ExpiredToken",
                    "InvalidClientTokenId",
                    "UnrecognizedClientException",
                }
                or "expired" in message
            ):
                raise ModelError(
                    "Bedrock credentials expired or are invalid. Replace the short-term API key, or rerun with a fresh OIDC role session."
                ) from exc
            if code in {
                "ThrottlingException",
                "ServiceQuotaExceededException",
                "TooManyRequestsException",
            }:
                raise ModelError(
                    "Bedrock throttled this review. Retry later or ask the AWS administrator to check the model quota."
                ) from exc
            if code in {"AccessDeniedException", "UnauthorizedException"}:
                raise ModelError(
                    "Bedrock denied model access. Check the selected account, region, model, and IAM permissions; this is not proof that the key expired."
                ) from exc
            if code in {"ResourceNotFoundException", "ValidationException"}:
                raise ModelError(
                    "Bedrock rejected the model/request configuration. Check the model ID and region with the AWS administrator."
                ) from exc
            raise ModelError(
                f"Bedrock failed ({code or 'service error'}). Retry later and share the run ID."
            ) from exc
        except (BotoCoreError, NoCredentialsError) as exc:
            raise ModelError(
                "Bedrock could not complete the request. Check credentials/network access and retry."
            ) from exc
        text = "".join(
            block["text"]
            for block in response.get("output", {}).get("message", {}).get("content", [])
            if "text" in block
        )
        return Completion(
            text,
            self.settings.model_id,
            round(time.monotonic() - started, 3),
            response.get("usage", {}),
            response.get("stopReason", "unknown"),
        )


class FakeModelClient:
    """Exercises the pipeline only. Never claims to measure model precision."""

    simulated = True

    def __init__(self, responses=None):
        self.responses = iter(responses) if responses is not None else None
        self.calls = []

    def complete(self, system, user, max_tokens):
        self.calls.append((system, user, max_tokens))
        if self.responses is not None:
            text = next(self.responses)
        else:
            context = json.loads(user)
            text = json.dumps(
                {
                    "schema_version": "infra-review/v1",
                    "reviewed_head_sha": context["review_metadata"]["head_sha"],
                    "findings": [],
                    "coverage": [
                        {
                            "category": category,
                            "status": "limited",
                            "limitations": [
                                "Simulated client: no semantic cost review was performed."
                            ],
                        }
                        for category in CATEGORIES
                    ],
                }
            )
        return Completion(text, "fake-offline", simulated=True)
