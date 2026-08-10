"""Parse confirmed OKX candles and derive aligned informative timeframes."""

from __future__ import annotations

import pandas as pd


OKX_CANDLE_COLUMNS = (
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "contract_volume",
    "volume",
    "quote_volume",
    "confirm",
)


def confirmed_candles(rows: list[list[str]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(
            columns=["date", "open", "high", "low", "close", "volume", "quote_volume"]
        )
    width = min(len(row) for row in rows)
    if width < len(OKX_CANDLE_COLUMNS):
        raise ValueError("OKX candle row has fewer than 9 fields")
    frame = pd.DataFrame(
        [row[: len(OKX_CANDLE_COLUMNS)] for row in rows], columns=OKX_CANDLE_COLUMNS
    )
    frame = frame.loc[frame["confirm"].astype(str) == "1"].copy()
    frame["date"] = pd.to_datetime(
        pd.to_numeric(frame["timestamp"], errors="raise"), unit="ms", utc=True
    )
    for column in ("open", "high", "low", "close", "volume", "quote_volume"):
        frame[column] = pd.to_numeric(frame[column], errors="raise").astype(float)
    frame = frame.sort_values("date").drop_duplicates("date", keep="last")
    return frame.loc[
        :, ["date", "open", "high", "low", "close", "volume", "quote_volume"]
    ].reset_index(drop=True)


def mark_candles(rows: list[list[str]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(
            columns=["date", "open", "high", "low", "close", "volume", "quote_volume"]
        )
    if min(len(row) for row in rows) < 6:
        raise ValueError("OKX mark candle row has fewer than 6 fields")
    frame = pd.DataFrame(
        [row[:6] for row in rows],
        columns=["timestamp", "open", "high", "low", "close", "confirm"],
    )
    frame = frame.loc[frame["confirm"].astype(str) == "1"].copy()
    frame["date"] = pd.to_datetime(
        pd.to_numeric(frame["timestamp"], errors="raise"), unit="ms", utc=True
    )
    for column in ("open", "high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="raise").astype(float)
    frame["volume"] = 0.0
    frame["quote_volume"] = 0.0
    frame = frame.sort_values("date").drop_duplicates("date", keep="last")
    return frame.loc[
        :, ["date", "open", "high", "low", "close", "volume", "quote_volume"]
    ].reset_index(drop=True)


def funding_events(rows: list[dict[str, str]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["date", "rate"])
    parsed = []
    for row in rows:
        realized = row.get("realizedRate")
        rate = realized if realized not in (None, "") else row.get("fundingRate")
        if rate in (None, ""):
            continue
        parsed.append(
            {
                "date": pd.to_datetime(int(row["fundingTime"]), unit="ms", utc=True),
                "rate": float(rate),
            }
        )
    return (
        pd.DataFrame(parsed, columns=["date", "rate"])
        .sort_values("date")
        .drop_duplicates("date", keep="last")
        .reset_index(drop=True)
    )


def resample_confirmed(frame: pd.DataFrame, rule: str) -> pd.DataFrame:
    normalized = rule.lower()
    expected = {"1h": 4, "4h": 16}.get(normalized)
    if expected is None:
        raise ValueError("rule must be '1h' or '4h'")
    if frame.empty:
        return frame.copy()
    source = frame.sort_values("date").drop_duplicates("date", keep="last").copy()
    source["row_count"] = 1
    groups = source.set_index("date").resample(
        normalized, label="left", closed="left", origin="epoch"
    )
    output = groups.agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
            "quote_volume": "sum",
            "row_count": "sum",
        }
    )
    output = output.loc[output["row_count"] == expected].drop(columns="row_count")
    return output.reset_index()
