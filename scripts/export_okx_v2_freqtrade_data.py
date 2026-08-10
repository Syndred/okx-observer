#!/usr/bin/env python3
"""Export the research cohort into Freqtrade's OKX futures Feather layout."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def freqtrade_stem(symbol: str) -> str:
    base = symbol.split("/", maxsplit=1)[0]
    return f"{base}_USDT_USDT"


def funding_ohlcv(candles: pd.DataFrame, funding: pd.DataFrame) -> pd.DataFrame:
    output = pd.DataFrame({"date": pd.to_datetime(candles["date"], utc=True)})
    rates = funding.copy()
    rates["date"] = pd.to_datetime(rates["date"], utc=True)
    rate_map = rates.drop_duplicates("date", keep="last").set_index("date")["rate"]
    output["close"] = output["date"].map(rate_map).fillna(0.0).astype(float)
    output["open"] = output["close"]
    output["high"] = output["close"]
    output["low"] = output["close"]
    output["volume"] = 0.0
    return output.loc[:, ["date", "open", "high", "low", "close", "volume"]]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cohort", type=Path, default=Path("user_data/okx_v2_research_cohort.json"))
    parser.add_argument("--snapshot", type=Path, default=Path("config/okx_v2_universe.json"))
    parser.add_argument("--source", type=Path, default=Path("user_data/data/okx_v2"))
    parser.add_argument("--output", type=Path, default=Path("user_data/data/okx/futures"))
    args = parser.parse_args()
    cohort = json.loads(args.cohort.read_text(encoding="utf-8"))
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    instrument_to_symbol = {
        row["instId"]: row["symbol"] for row in snapshot["instruments"]
    }
    args.output.mkdir(parents=True, exist_ok=True)
    exported = 0
    for instrument in cohort["download_instruments"]:
        symbol = instrument_to_symbol[instrument]
        candle_path = args.source / f"{instrument}-15m.feather"
        funding_path = args.source / f"{instrument}-funding.feather"
        if not candle_path.exists() or not funding_path.exists():
            continue
        candles = pd.read_feather(candle_path)
        candles["date"] = pd.to_datetime(candles["date"], utc=True)
        trade = candles.loc[:, ["date", "open", "high", "low", "close", "volume"]]
        mark = trade.copy()
        mark["volume"] = 0.0
        funding = funding_ohlcv(candles, pd.read_feather(funding_path))
        stem = freqtrade_stem(symbol)
        trade.to_feather(args.output / f"{stem}-15m-futures.feather")
        mark.to_feather(args.output / f"{stem}-15m-mark.feather")
        funding.to_feather(args.output / f"{stem}-15m-funding_rate.feather")
        exported += 1
    print(f"Exported {exported} OKX futures pairs to {args.output}")


if __name__ == "__main__":
    main()
