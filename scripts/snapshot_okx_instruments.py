#!/usr/bin/env python3
"""Snapshot OKX USDT perpetual metadata for reproducible V2 research."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DAY_MS = 86_400_000
OKX_INSTRUMENTS_URL = "https://www.okx.com/api/v5/public/instruments?instType=SWAP"
REFERENCE_BASES = {"BTC", "ETH"}


def normalize_instruments(
    payload: dict[str, Any], now_ms: int, min_age_days: int = 30
) -> list[dict[str, Any]]:
    if payload.get("code") != "0":
        raise ValueError(f"OKX returned error code {payload.get('code')}: {payload.get('msg', '')}")
    minimum_age_ms = min_age_days * DAY_MS
    normalized: list[dict[str, Any]] = []
    for item in payload.get("data", []):
        if item.get("instType") != "SWAP" or item.get("settleCcy") != "USDT":
            continue
        instrument_id = str(item["instId"])
        base = instrument_id.removesuffix("-USDT-SWAP")
        list_time = int(item.get("listTime") or 0)
        category = str(item.get("instCategory") or "")
        state = str(item.get("state") or "")
        age_ms = max(0, now_ms - list_time)
        if state != "live":
            reason = "not_live"
        elif not category:
            reason = "unknown_category"
        elif age_ms < minimum_age_ms:
            reason = f"listed_less_than_{min_age_days}_days"
        else:
            reason = "eligible"
        normalized.append(
            {
                "instId": instrument_id,
                "symbol": f"{base}/USDT:USDT",
                "base": base,
                "instCategory": category,
                "listTime": list_time,
                "listTimeUtc": datetime.fromtimestamp(
                    list_time / 1000, tz=timezone.utc
                ).isoformat(),
                "ageDays": age_ms // DAY_MS,
                "state": state,
                "role": "reference" if base in REFERENCE_BASES else "trade",
                "eligible": reason == "eligible",
                "eligibility_reason": reason,
                "ctVal": item.get("ctVal", ""),
                "ctValCcy": item.get("ctValCcy", ""),
                "tickSz": item.get("tickSz", ""),
                "lotSz": item.get("lotSz", ""),
                "minSz": item.get("minSz", ""),
                "lever": item.get("lever", ""),
            }
        )
    return sorted(normalized, key=lambda row: row["instId"])


def fetch_instruments(retries: int = 5, timeout: int = 30) -> dict[str, Any]:
    request = Request(
        OKX_INSTRUMENTS_URL,
        headers={"User-Agent": "crypto-trading-system-research/2.0"},
    )
    for attempt in range(retries):
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code != 429 and error.code < 500:
                raise
        except URLError:
            if attempt == retries - 1:
                raise
        if attempt == retries - 1:
            raise RuntimeError("OKX instrument request exhausted retries")
        time.sleep(min(2**attempt, 8))
    raise RuntimeError("unreachable")


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", type=Path, default=Path("config/okx_v2_universe.json")
    )
    parser.add_argument("--min-age-days", type=int, default=30)
    args = parser.parse_args()
    retrieved = datetime.now(timezone.utc)
    payload = fetch_instruments()
    rows = normalize_instruments(
        payload, now_ms=int(retrieved.timestamp() * 1000), min_age_days=args.min_age_days
    )
    snapshot = {
        "source": OKX_INSTRUMENTS_URL,
        "retrievedAt": retrieved.isoformat(),
        "minAgeDays": args.min_age_days,
        "totalUsdtSwaps": len(rows),
        "eligibleTradeCount": sum(
            row["eligible"] and row["role"] == "trade" for row in rows
        ),
        "referenceCount": sum(row["eligible"] and row["role"] == "reference" for row in rows),
        "instruments": rows,
    }
    write_json_atomic(args.output, snapshot)
    print(
        f"Saved {len(rows)} USDT swaps; "
        f"{snapshot['eligibleTradeCount']} trade candidates to {args.output}"
    )


if __name__ == "__main__":
    main()
