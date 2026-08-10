from __future__ import annotations

from datetime import datetime
import os

from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import CategoricalParameter, IStrategy, stoploss_from_absolute

from user_data.strategies.risk_model import collateral_for_risk
from user_data.strategies.v1_signal_engine import (
    V1Parameters,
    add_six_averages,
    scan_v1_setups,
)


def encode_entry_tag(side: str, stop_price: float) -> str:
    return f"v1_{side}|stop={stop_price:.10f}"


def parse_stop_from_tag(entry_tag: str | None) -> float | None:
    if not entry_tag or "|stop=" not in entry_tag:
        return None
    try:
        return float(entry_tag.split("|stop=", maxsplit=1)[1])
    except (TypeError, ValueError):
        return None


class MA_EMA_Trend_Strategy(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "1h"
    can_short = True
    startup_candle_count = 120
    process_only_new_candles = True

    minimal_roi = {"0": 100.0}
    stoploss = -0.99
    trailing_stop = False
    use_exit_signal = False
    use_custom_roi = True
    use_custom_stoploss = True

    buy_compression_threshold = CategoricalParameter(
        [0.0025, 0.005, 0.0075, 0.01, 0.015, 0.02], default=0.01, space="buy"
    )
    buy_breakout_threshold = CategoricalParameter(
        [0.001, 0.0025, 0.005, 0.01], default=0.0025, space="buy"
    )
    buy_pullback_tolerance = CategoricalParameter(
        [0.0025, 0.005, 0.01, 0.02], default=0.005, space="buy"
    )
    buy_pullback_max_candles = CategoricalParameter(
        [3, 6, 12, 24], default=12, space="buy"
    )
    buy_leverage = CategoricalParameter([3, 5, 10], default=5, space="buy", optimize=False)
    buy_risk_pct = CategoricalParameter(
        [0.005, 0.01, 0.02, 0.05], default=0.01, space="buy", optimize=False
    )
    sell_reward_risk = CategoricalParameter([3, 5, 8, 10], default=5, space="sell")

    def configured_leverage(self) -> float:
        return float(os.getenv("V1_LEVERAGE", str(self.buy_leverage.value)))

    def configured_risk_pct(self) -> float:
        return float(os.getenv("V1_RISK_PCT", str(self.buy_risk_pct.value)))

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return add_six_averages(dataframe)

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        params = V1Parameters(
            compression_threshold=float(self.buy_compression_threshold.value),
            breakout_threshold=float(self.buy_breakout_threshold.value),
            pullback_tolerance=float(self.buy_pullback_tolerance.value),
            pullback_max_candles=int(self.buy_pullback_max_candles.value),
        )
        result = scan_v1_setups(dataframe, params)
        result.loc[result["volume"] <= 0, ["enter_long", "enter_short"]] = 0
        result["enter_tag"] = None
        long_rows = result["enter_long"] == 1
        short_rows = result["enter_short"] == 1
        result.loc[long_rows, "enter_tag"] = result.loc[
            long_rows, "initial_stop_price"
        ].map(lambda stop: encode_entry_tag("long", float(stop)))
        result.loc[short_rows, "enter_tag"] = result.loc[
            short_rows, "initial_stop_price"
        ].map(lambda stop: encode_entry_tag("short", float(stop)))
        return result

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0
        return dataframe

    def leverage(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_leverage: float,
        max_leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        return min(self.configured_leverage(), max_leverage)

    def custom_stake_amount(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_stake: float,
        min_stake: float | None,
        max_stake: float,
        leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        stop_price = parse_stop_from_tag(entry_tag)
        if stop_price is None or current_rate <= 0:
            return 0.0
        stop_distance_ratio = abs(current_rate - stop_price) / current_rate
        try:
            return collateral_for_risk(
                equity=float(self.wallets.get_total_stake_amount()),
                risk_pct=self.configured_risk_pct(),
                stop_distance_ratio=stop_distance_ratio,
                leverage=leverage,
                max_collateral=max_stake,
            )
        except ValueError:
            return 0.0

    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs,
    ) -> float | None:
        stop_price = parse_stop_from_tag(trade.enter_tag)
        if stop_price is None:
            return None
        return stoploss_from_absolute(
            stop_price,
            current_rate=current_rate,
            is_short=trade.is_short,
            leverage=trade.leverage,
        )

    def custom_roi(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        trade_duration: int,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float | None:
        stop_price = parse_stop_from_tag(trade.enter_tag)
        if stop_price is None or trade.open_rate <= 0:
            return None
        unleveraged_risk = abs(trade.open_rate - stop_price) / trade.open_rate
        return (
            unleveraged_risk
            * float(trade.leverage)
            * float(self.sell_reward_risk.value)
        )
