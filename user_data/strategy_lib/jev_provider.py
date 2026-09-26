"""Strict Jev System One client for historical trade probability evaluation.

Wire format follows JevPlay and the official typesafe-sdk 0.7.1. Requests
is used because this project runs Python 3.9 and the SDK requires 3.10+.
The returned probability is a model estimate, not an observed win rate.
"""

from __future__ import annotations

import json
import math
import os
from typing import Any

import requests


JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
PROMPT_VERSION = "ma-net-profit-v2"
_INSTRUCTIONS = (
    "Estimate whether this proposed moving-average trade will finish with "
    "strictly positive net profit. Read state.strategy for the exact signal method. "
    "Follow state.execution_rules exactly for entry, "
    "stop loss, take profit, maximum holding period and all fees, slippage and "
    "funding costs; do not invent or change the execution rules. Use only the "
    "information in state, which is available at the decision time. Do not assume "
    "knowledge of later prices or infer a result from the date or instrument name. "
    "Choose win for strictly positive net profit and loss for zero or negative net "
    "profit. Return the probability distribution over these two outcomes."
)


class JevProviderError(RuntimeError):
    """A sanitized provider failure, with a stable machine-readable reason."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__("Jev evaluation unavailable: " + reason)


def _is_probability(value: Any) -> bool:
    return (
        type(value) in (int, float)
        and math.isfinite(value)
        and 0 <= value <= 1
    )


def _parse_response(body: Any) -> dict:
    if not isinstance(body, dict):
        raise JevProviderError("invalid_response")
    model = body.get("model")
    answers = body.get("answers")
    if not isinstance(model, str) or not model.strip() or not isinstance(answers, dict):
        raise JevProviderError("invalid_response")
    answer = answers.get("trade_outcome")
    if not isinstance(answer, dict) or answer.get("type") != "choice":
        raise JevProviderError("invalid_response")
    probabilities = answer.get("probabilities")
    if (
        answer.get("choice") not in ("win", "loss")
        or not isinstance(probabilities, dict)
        or set(probabilities) != {"win", "loss"}
        or not all(_is_probability(value) for value in probabilities.values())
        or not math.isclose(sum(probabilities.values()), 1.0, abs_tol=0.001)
    ):
        raise JevProviderError("invalid_response")
    confidence = answer.get("confidence")
    if confidence is not None and not _is_probability(confidence):
        raise JevProviderError("invalid_response")
    return {
        "model": model.strip(),
        "probability": float(probabilities["win"]),
        "confidence": None if confidence is None else float(confidence),
    }


class JevClient:
    """No retries, cached values or heuristic fallback are hidden in this client."""

    def __init__(self, api_key=None, model="jev-1.13.0", timeout=30):
        if api_key is None:
            api_key = os.getenv("TYPESAFE_API_KEY", "").strip() or os.getenv("JEV_API_KEY", "").strip()
        if not isinstance(api_key, str) or not api_key.strip():
            raise JevProviderError("missing_key")
        api_key = api_key.strip()
        if not api_key.isascii() or any(ord(char) <= 32 or ord(char) == 127 for char in api_key):
            raise JevProviderError("invalid_key")
        if not isinstance(model, str) or not model.strip():
            raise JevProviderError("invalid_model")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise JevProviderError("invalid_timeout")
        self._api_key = api_key
        self.model = model.strip()
        self.timeout = timeout

    def evaluate(self, state: dict) -> dict:
        """Evaluate an as-of state. Caller owns temporal filtering and caching."""
        if not isinstance(state, dict) or not isinstance(state.get("execution_rules"), dict) or not state["execution_rules"]:
            raise JevProviderError("invalid_state")
        try:
            json.dumps(state, allow_nan=False)
        except (ValueError, TypeError, OverflowError):
            raise JevProviderError("invalid_state") from None
        payload = {
            "model": self.model,
            "state": state,
            "questions": {
                "trade_outcome": {
                    "type": "choice",
                    "instructions": _INSTRUCTIONS,
                    "criteria": {
                        "win": "Net profit is strictly positive under state.execution_rules.",
                        "loss": "Net profit is zero or negative under state.execution_rules.",
                    },
                }
            },
        }
        try:
            response = requests.post(
                JEV_ENDPOINT,
                headers={"Authorization": "Bearer " + self._api_key, "Accept": "application/json"},
                json=payload,
                timeout=self.timeout,
                allow_redirects=False,
            )
        except requests.Timeout:
            raise JevProviderError("timeout") from None
        except requests.RequestException:
            raise JevProviderError("network_error") from None
        try:
            if not 200 <= response.status_code < 300:
                reason = "rate_limited" if response.status_code == 429 else "http_error"
                raise JevProviderError(reason)
            try:
                body = response.json()
            except ValueError:
                raise JevProviderError("invalid_response") from None
            return _parse_response(body)
        finally:
            response.close()
