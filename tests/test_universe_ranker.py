from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from user_data.strategy_lib.universe_ranker import (
    PairFeatures,
    UniverseRules,
    build_universe_mask,
    eligible_trade_symbols,
    rank_hour,
)


class UniverseRankerTests(unittest.TestCase):
    def test_reference_contracts_are_not_ranked_as_trade_candidates(self) -> None:
        rows = [
            {"instId": "BTC-USDT-SWAP", "symbol": "BTC/USDT:USDT", "eligible": True, "role": "reference"},
            {"instId": "ALT-USDT-SWAP", "symbol": "ALT/USDT:USDT", "eligible": True, "role": "trade"},
        ]

        self.assertEqual(eligible_trade_symbols(rows), {"ALT-USDT-SWAP": "ALT/USDT:USDT"})

    def test_rank_rejects_missing_illiquid_and_extreme_pairs(self) -> None:
        rules = UniverseRules(min_notional_24h=1_000_000, max_atr_ratio=0.15)
        features = {
            "LIQUID": PairFeatures(20_000_000, 1.0, 0.03),
            "BALANCED": PairFeatures(5_000_000, 1.0, 0.04),
            "EXTREME": PairFeatures(50_000_000, 1.0, 0.25),
            "ILLIQUID": PairFeatures(100_000, 1.0, 0.04),
            "MISSING": PairFeatures(10_000_000, 0.80, 0.04),
        }

        result = rank_hour(features, limit=2, rules=rules)

        self.assertEqual(result, ["LIQUID", "BALANCED"])

    def test_equal_scores_break_ties_by_pair_name(self) -> None:
        features = {
            "ZZZ": PairFeatures(5_000_000, 1.0, 0.04),
            "AAA": PairFeatures(5_000_000, 1.0, 0.04),
        }

        self.assertEqual(rank_hour(features, limit=2), ["AAA", "ZZZ"])

    def test_appending_future_rows_does_not_change_old_ranks(self) -> None:
        old = {
            "AAA": hourly_frame(60, quote_volume=100_000, atr_move=1.0),
            "BBB": hourly_frame(60, quote_volume=80_000, atr_move=1.5),
        }
        extended = {
            pair: pd.concat(
                [frame, future_frame(frame, 12, quote_volume=10_000_000 if pair == "BBB" else 1)]
            ).reset_index(drop=True)
            for pair, frame in old.items()
        }

        old_mask = build_universe_mask(old, limit=1)
        new_mask = build_universe_mask(extended, limit=1)
        cutoff = old_mask["date"].max()

        pd.testing.assert_frame_equal(
            old_mask.reset_index(drop=True),
            new_mask.loc[new_mask["date"] <= cutoff].reset_index(drop=True),
        )

    def test_current_hour_volume_is_not_used_for_current_rank(self) -> None:
        frames = {
            "AAA": hourly_frame(30, quote_volume=100_000, atr_move=1.0),
            "BBB": hourly_frame(30, quote_volume=90_000, atr_move=1.0),
        }
        frames["BBB"].loc[29, "quote_volume"] = 100_000_000

        mask = build_universe_mask(frames, limit=1)
        latest = mask.loc[mask["date"] == mask["date"].max()]

        self.assertEqual(latest.iloc[0]["pair"], "AAA")


def hourly_frame(length: int, quote_volume: float, atr_move: float) -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=length, freq="1h", tz="UTC")
    steps = np.arange(length, dtype=float) * atr_move
    close = 100 + steps
    return pd.DataFrame(
        {
            "date": dates,
            "open": close - atr_move / 2,
            "high": close + atr_move,
            "low": close - atr_move,
            "close": close,
            "volume": 1_000.0,
            "quote_volume": quote_volume,
        }
    )


def future_frame(
    previous: pd.DataFrame, length: int, quote_volume: float
) -> pd.DataFrame:
    start = previous["date"].max() + pd.Timedelta(hours=1)
    dates = pd.date_range(start, periods=length, freq="1h", tz="UTC")
    close = np.linspace(float(previous["close"].iloc[-1]), 101, length)
    return pd.DataFrame(
        {
            "date": dates,
            "open": close,
            "high": close + 0.02,
            "low": close - 0.02,
            "close": close,
            "volume": 1_000.0,
            "quote_volume": quote_volume,
        }
    )


if __name__ == "__main__":
    unittest.main()
