"""Redact credentials and keep repository/model text inert when displayed."""

import os
import re

SECRET_ASSIGNMENT = re.compile(
    r"""(?im)(\b(?:[A-Z_]*(?:API_KEY|API_TOKEN|ACCESS_TOKEN|SECRET_KEY|SECRET_ACCESS_KEY|SESSION_TOKEN|BEARER_TOKEN_BEDROCK|PASSWORD|PRIVATE_KEY|CLIENT_SECRET|WEBHOOK_SECRET))\b\s*[:=]\s*)(["'])([^\r\n"']+)\2"""
)


def redact(text: str) -> str:
    for name in (
        "GITHUB_TOKEN",
        "AWS_BEARER_TOKEN_BEDROCK",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
    ):
        value = os.environ.get(name)
        if value and len(value) >= 8:
            text = text.replace(value, "[REDACTED CREDENTIAL]")
    text = re.sub(
        r"-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?-----END [^-]*PRIVATE KEY-----",
        lambda match: "[REDACTED PRIVATE KEY]" + "\n" * match[0].count("\n"),
        text,
    )
    text = SECRET_ASSIGNMENT.sub(lambda match: f"{match[1]}{match[2]}[REDACTED]{match[2]}", text)
    text = re.sub(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b", "[REDACTED AWS KEY]", text)
    text = re.sub(
        r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,})\b",
        "[REDACTED TOKEN]",
        text,
    )
    return re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", "Bearer [REDACTED]", text)


def display(text: str, limit: int = 6000) -> str:
    text = redact(str(text))[:limit]
    text = re.sub(r"<[^>]*>", "", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"@(?=[A-Za-z0-9])", "@\u200b", text)
    return text.replace("\x00", "").replace("\r", "")


def inline(text: str) -> str:
    return display(text, 500).replace("`", "'").replace("\n", " ").replace("|", "\\|")
