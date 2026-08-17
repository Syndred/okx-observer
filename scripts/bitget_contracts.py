"""Fetch and normalize Bitget's public USDT perpetual contract universe."""

from __future__ import annotations

import json
from urllib.request import Request, urlopen


BITGET_CONTRACTS_URL = (
    "https://api.bitget.com/api/v2/mix/market/contracts"
    "?productType=usdt-futures"
)
BITGET_USER_AGENT = "crypto-three-stage-screener/1.0"


def normalize_bitget_contracts(payload: dict[str, object]) -> set[str]:
    """Return symbols that are currently normal USDT perpetuals."""

    if str(payload.get("code", "")) != "00000":
        raise RuntimeError(
            f"Bitget contracts request failed: {payload.get('code')} {payload.get('msg', '')}"
        )
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise RuntimeError("Bitget contracts response has no data list")
    symbols: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        if (
            str(row.get("quoteCoin", "")).upper() == "USDT"
            and str(row.get("symbolType", "")).lower() == "perpetual"
            and str(row.get("symbolStatus", "")).lower() == "normal"
            and str(row.get("offTime", "")) == "-1"
        ):
            symbol = str(row.get("symbol", "")).strip().upper()
            if symbol:
                symbols.add(symbol)
    required_market_references = {"BTCUSDT", "ETHUSDT"}
    missing_market_references = sorted(required_market_references - symbols)
    if missing_market_references:
        raise RuntimeError(
            "Bitget contracts response missing required market references: "
            + ", ".join(missing_market_references)
        )
    return symbols


def fetch_bitget_contracts(timeout: int = 30) -> set[str]:
    """Fetch the official Bitget public contract list.

    Any transport, JSON, or API response failure is fatal to the caller's scan;
    the dashboard runner will then retain its previous published snapshot.
    """

    request = Request(
        BITGET_CONTRACTS_URL,
        headers={"User-Agent": BITGET_USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except Exception as error:
        raise RuntimeError(f"Bitget contracts request failed: {error}") from error
    if not isinstance(payload, dict):
        raise RuntimeError("Bitget contracts response is not an object")
    return normalize_bitget_contracts(payload)
