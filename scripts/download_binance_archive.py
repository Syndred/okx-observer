#!/usr/bin/env python3
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zipfile import ZipFile

import pandas as pd


BASE_URL = "https://data.binance.vision/data/futures/um"
KLINE_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "count",
    "taker_buy_volume",
    "taker_buy_quote_volume",
    "ignore",
]
FUNDING_COLUMNS = ["calc_time", "funding_interval_hours", "last_funding_rate"]
FREQTRADE_COLUMNS = ["date", "open", "high", "low", "close", "volume"]


@dataclass(frozen=True)
class PairSpec:
    label: str
    archive_symbol: str
    freqtrade_pair: str


@dataclass(frozen=True)
class ArchiveRequest:
    dataset: str
    url: str


def _read_csv_from_zip(payload: bytes, columns: list[str]) -> pd.DataFrame:
    with ZipFile(BytesIO(payload)) as archive:
        names = archive.namelist()
        if len(names) != 1:
            raise ValueError(f"expected one CSV in archive, found {len(names)}")
        with archive.open(names[0]) as stream:
            first_line = stream.readline().decode("utf-8").strip().split(",")
        has_header = first_line[0] in {"open_time", "calc_time"}
        with archive.open(names[0]) as stream:
            return pd.read_csv(
                stream,
                header=0 if has_header else None,
                names=None if has_header else columns,
            )


def _milliseconds(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="raise")
    return numeric.where(numeric < 10**15, numeric / 1000)


def parse_kline_zip(payload: bytes) -> pd.DataFrame:
    frame = _read_csv_from_zip(payload, KLINE_COLUMNS)
    frame["date"] = pd.to_datetime(_milliseconds(frame["open_time"]), unit="ms", utc=True)
    for column in ["open", "high", "low", "close", "volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    return frame.loc[:, FREQTRADE_COLUMNS]


def parse_funding_zip(payload: bytes) -> pd.DataFrame:
    frame = _read_csv_from_zip(payload, FUNDING_COLUMNS)
    frame["date"] = pd.to_datetime(_milliseconds(frame["calc_time"]), unit="ms", utc=True).dt.floor(
        "h"
    )
    rate = pd.to_numeric(frame["last_funding_rate"], errors="raise")
    for column in ["open", "high", "low", "close"]:
        frame[column] = rate
    frame["volume"] = 0.0
    return frame.loc[:, FREQTRADE_COLUMNS]


def _next_month(day: date) -> date:
    return date(day.year + (day.month == 12), 1 if day.month == 12 else day.month + 1, 1)


def _archive_requests(symbol: str, start: date, end: date) -> list[ArchiveRequest]:
    requests: list[ArchiveRequest] = []
    month = date(start.year, start.month, 1)
    while _next_month(month) <= end:
        period = month.strftime("%Y-%m")
        requests.extend(
            [
                ArchiveRequest(
                    "klines",
                    f"{BASE_URL}/monthly/klines/{symbol}/1h/{symbol}-1h-{period}.zip",
                ),
                ArchiveRequest(
                    "mark",
                    f"{BASE_URL}/monthly/markPriceKlines/{symbol}/1h/{symbol}-1h-{period}.zip",
                ),
                ArchiveRequest(
                    "funding",
                    f"{BASE_URL}/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{period}.zip",
                ),
            ]
        )
        month = _next_month(month)

    partial_month_start = date(end.year, end.month, 1)
    day = max(start, partial_month_start)
    while day < end:
        period = day.isoformat()
        requests.extend(
            [
                ArchiveRequest(
                    "klines",
                    f"{BASE_URL}/daily/klines/{symbol}/1h/{symbol}-1h-{period}.zip",
                ),
                ArchiveRequest(
                    "mark",
                    f"{BASE_URL}/daily/markPriceKlines/{symbol}/1h/{symbol}-1h-{period}.zip",
                ),
            ]
        )
        day += timedelta(days=1)
    return requests


def _fetch(request: ArchiveRequest) -> tuple[str, bytes] | None:
    headers = {"User-Agent": "crypto-trading-system/1.0"}
    for attempt in range(3):
        try:
            with urlopen(Request(request.url, headers=headers), timeout=30) as response:
                payload = response.read()
            with urlopen(Request(request.url + ".CHECKSUM", headers=headers), timeout=30) as response:
                expected = response.read().decode("utf-8").split()[0]
            actual = sha256(payload).hexdigest()
            if actual != expected:
                raise ValueError(f"checksum mismatch for {request.url}")
            return request.dataset, payload
        except HTTPError as error:
            if error.code == 404:
                return None
            if attempt == 2:
                raise
        except (TimeoutError, URLError):
            if attempt == 2:
                raise
        time.sleep(0.5 * (attempt + 1))
    return None


def _concat(frames: list[pd.DataFrame], start: datetime, end: datetime) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame(columns=FREQTRADE_COLUMNS)
    result = pd.concat(frames, ignore_index=True)
    result = result.drop_duplicates(subset="date", keep="last").sort_values("date")
    return result[(result["date"] >= start) & (result["date"] < end)].reset_index(drop=True)


def _overlay_funding(contract: pd.DataFrame, actual: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    if contract.empty:
        return pd.DataFrame(columns=FREQTRADE_COLUMNS), 0
    schedule = pd.DataFrame(
        {
            "date": pd.date_range(
                contract["date"].iloc[0].floor("h"),
                contract["date"].iloc[-1].floor("h"),
                freq="1h",
                tz="UTC",
            )
        }
    )
    rates = actual.loc[:, ["date", "open"]].rename(columns={"open": "actual_rate"})
    combined = schedule.merge(rates, on="date", how="left")
    fallback_rows = int(combined["actual_rate"].isna().sum())
    combined["open"] = combined["actual_rate"].fillna(0.0)
    for column in ["high", "low", "close"]:
        combined[column] = combined["open"]
    combined["volume"] = 0.0
    return combined.loc[:, FREQTRADE_COLUMNS], fallback_rows


def _overlay_mark(contract: pd.DataFrame, mark: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    if contract.empty:
        return pd.DataFrame(columns=FREQTRADE_COLUMNS), 0
    combined = contract.merge(mark, on="date", how="left", suffixes=("_contract", ""))
    fallback_rows = int(combined["open"].isna().sum())
    for column in ["open", "high", "low", "close", "volume"]:
        combined[column] = combined[column].fillna(combined[f"{column}_contract"])
    return combined.loc[:, FREQTRADE_COLUMNS], fallback_rows


def _filename(pair: str, candle_type: str) -> str:
    pair_name = pair.replace("/", "_").replace(":", "_")
    return f"{pair_name}-1h-{candle_type}.feather"


def load_pairs(path: Path) -> list[PairSpec]:
    pairs: list[PairSpec] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        label, archive_symbol, freqtrade_pair = line.split()
        pairs.append(PairSpec(label, archive_symbol, freqtrade_pair))
    return pairs


def download_pair(
    spec: PairSpec,
    start: datetime,
    end: datetime,
    output_dir: Path,
    workers: int,
    funding_only: bool,
) -> dict[str, object]:
    requests = _archive_requests(spec.archive_symbol, start.date(), end.date())
    if funding_only:
        requests = [request for request in requests if request.dataset == "funding"]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        fetched = list(pool.map(_fetch, requests))

    grouped: dict[str, list[pd.DataFrame]] = {"klines": [], "mark": [], "funding": []}
    for item in fetched:
        if item is None:
            continue
        dataset, payload = item
        parser = parse_funding_zip if dataset == "funding" else parse_kline_zip
        grouped[dataset].append(parser(payload))

    if funding_only:
        contract = pd.read_feather(output_dir / _filename(spec.freqtrade_pair, "futures"))
        raw_mark = pd.read_feather(output_dir / _filename(spec.freqtrade_pair, "mark"))
    else:
        contract = _concat(grouped["klines"], start, end)
        raw_mark = _concat(grouped["mark"], start, end)
    raw_funding = _concat(grouped["funding"], start, end)
    mark, mark_fallback_rows = _overlay_mark(contract, raw_mark)
    funding, funding_fallback_rows = _overlay_funding(contract, raw_funding)

    output_dir.mkdir(parents=True, exist_ok=True)
    if not funding_only:
        contract.to_feather(output_dir / _filename(spec.freqtrade_pair, "futures"), compression="lz4")
        mark.to_feather(output_dir / _filename(spec.freqtrade_pair, "mark"), compression="lz4")
    funding.to_feather(output_dir / _filename(spec.freqtrade_pair, "funding_rate"), compression="lz4")

    print(
        f"{spec.label}: {len(contract)} candles, {len(raw_funding)} actual funding rows, "
        f"{funding_fallback_rows} zero-fallback funding rows"
    )
    return {
        "label": spec.label,
        "archive_symbol": spec.archive_symbol,
        "freqtrade_pair": spec.freqtrade_pair,
        "first_candle": contract["date"].iloc[0].isoformat() if not contract.empty else "",
        "last_candle": contract["date"].iloc[-1].isoformat() if not contract.empty else "",
        "candle_rows": len(contract),
        "actual_funding_rows": len(raw_funding),
        "funding_fallback_rows": funding_fallback_rows,
        "mark_fallback_rows": mark_fallback_rows,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download official Binance USD-M archives")
    parser.add_argument("--pairs-file", type=Path, required=True)
    parser.add_argument("--start", default="2021-01-01")
    parser.add_argument("--end", default="2026-08-10")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--funding-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)
    rows = [
        download_pair(spec, start, end, args.output_dir, args.workers, args.funding_only)
        for spec in load_pairs(args.pairs_file)
    ]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(args.report, index=False)


if __name__ == "__main__":
    main()
