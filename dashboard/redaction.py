"""Small, dependency-free redaction helpers for dashboard-facing text."""

from __future__ import annotations

import re


_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|secret|token|passphrase|password|credential|authorization)",
    re.IGNORECASE,
)
_SENSITIVE_KEY_NAME = (
    r"(?:[A-Za-z_][\w.-]*(?:api[_-]?key|secret|token|passphrase|"
    r"password|credential|authorization)[A-Za-z0-9_.-]*|"
    r"api[_-]?key|secret|token|passphrase|password|credential|authorization)"
)
_SENSITIVE_ASSIGNMENT = re.compile(
    rf"(?P<prefix>"
    rf"[\"']?(?P<key>{_SENSITIVE_KEY_NAME})[\"']?\s*[:=]\s*)"
    rf"(?:"
    rf"(?P<value_quote>[\"'])(?P<quoted_value>.*?)(?P=value_quote)"
    rf"|(?P<bare_value>[^\r\n]*)"
    rf")",
    re.IGNORECASE,
)


def _redact_sensitive_assignment(match: re.Match[str]) -> str:
    prefix = match.group("prefix")
    quote = match.group("value_quote")
    if quote is not None:
        return f"{prefix}{quote}<redacted>{quote}"
    return f"{prefix}<redacted>"


def redact_sensitive_text(value: object) -> object:
    """Redact credential-like assignment values while keeping safe context."""

    if isinstance(value, dict):
        result: dict[str, object] = {}
        for key, item in value.items():
            normalized_key = str(key)
            result[normalized_key] = (
                "<redacted>"
                if _SENSITIVE_KEY.search(normalized_key)
                else redact_sensitive_text(item)
            )
        return result
    if isinstance(value, (list, tuple)):
        return [redact_sensitive_text(item) for item in value]
    if not isinstance(value, str):
        return value

    return _SENSITIVE_ASSIGNMENT.sub(_redact_sensitive_assignment, value)
