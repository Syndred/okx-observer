from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path

import pandas as pd
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy, stoploss_from_absolute

from user_data.strategy_lib.risk_model import collateral_for_risk
from user_data.strategy_lib.v2_portfolio import TradeState, exit_decision, risk_budget
from user_data.strategy_lib.v2_signal_engine import (
    V2Parameters,
    add_v2_indicators,
    scan_v2_setups,
)


def encode_v2_entry_tag(side: str, stop_price: float, risk_pct: float) -> str:
    return f"v2_{side}|stop={stop_price:.10f}|risk={risk_pct:.6f}"


def parse_v2_entry_tag(entry_tag: str | None) -> tuple[str, float, float] | None:
    if not entry_tag or not entry_tag.startswith("v2_"):
        return None
    try:
        side_part, stop_part, risk_part = entry_tag.split("|")
        side = side_part.removeprefix("v2_")
        if side not in {"long", "short"}:
            return None
        return side, float(stop_part.removeprefix("stop=")), float(
            risk_part.removeprefix("risk=")
        )
    except (TypeError, ValueError):
        return None


class OKX_Shortline_V2(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "15m"
    can_short = True
    startup_candle_count = 480
    process_only_new_candles = True

    minimal_roi = {"0": 100.0}
    stoploss = -0.99
    trailing_stop = False
    use_exit_signal = False
    use_custom_stoploss = True
    position_adjustment_enable = True
    max_entry_position_adjustment = 0

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._equity_peak = 0.0
        self._data_dir = Path(os.getenv("OKX_V2_DATA_DIR", "user_data/data/okx_v2"))
        self._universe_path = Path(
            os.getenv("OKX_V2_UNIVERSE", str(self._data_dir / "universe_top30.feather"))
        )

    @staticmethod
    def _instrument(pair: str) -> str:
        base = pair.split("/", maxsplit=1)[0]
        return f"{base}-USDT-SWAP"

    def _load_frame(self, pair: str, timeframe: str) -> DataFrame:
        path = self._data_dir / f"{self._instrument(pair)}-{timeframe}.feather"
        frame = pd.read_feather(path)
        frame["date"] = pd.to_datetime(frame["date"], utc=True)
        return frame

    def _category(self, pair: str) -> str:
        snapshot_path = Path("config/okx_v2_universe.json")
        if not snapshot_path.exists():
            return "1"
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
        instrument = self._instrument(pair)
        for row in payload.get("instruments", []):
            if row.get("instId") == instrument:
                return str(row.get("instCategory", ""))
        return ""

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        base = add_v2_indicators(dataframe)
        pair = metadata.get("pair", "")
        try:
            universe = pd.read_feather(self._universe_path)
            universe["date"] = pd.to_datetime(universe["date"], utc=True)
            return scan_v2_setups(
                pair=pair,
                inst_category=self._category(pair),
                candles15m=base,
                candles1h=self._load_frame(pair, "1h"),
                candles4h=self._load_frame(pair, "4h"),
                btc4h=self._load_frame("BTC/USDT:USDT", "4h"),
                eth4h=self._load_frame("ETH/USDT:USDT", "4h"),
                universe_mask=universe,
                params=V2Parameters(),
            )
        except (FileNotFoundError, KeyError, ValueError):
            base["enter_long"] = 0
            base["enter_short"] = 0
            base["initial_stop_price"] = float("nan")
            return base

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        result = dataframe.copy()
        result["enter_tag"] = None
        result.loc[result["volume"] <= 0, ["enter_long", "enter_short"]] = 0
        long_rows = result["enter_long"] == 1
        short_rows = result["enter_short"] == 1
        result.loc[long_rows, "enter_tag"] = result.loc[
            long_rows, "initial_stop_price"
        ].map(lambda stop: encode_v2_entry_tag("long", float(stop), 0.0075))
        result.loc[short_rows, "enter_tag"] = result.loc[
            short_rows, "initial_stop_price"
        ].map(lambda stop: encode_v2_entry_tag("short", float(stop), 0.0075))
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
        return min(3.0, max_leverage)

    def _current_universe_allows(self, pair: str, current_time: datetime) -> bool:
        try:
            universe = pd.read_feather(self._universe_path)
        except (FileNotFoundError, ValueError):
            return False
        hour = pd.Timestamp(current_time)
        hour = hour.tz_localize("UTC") if hour.tz is None else hour.tz_convert("UTC")
        dates = pd.to_datetime(universe["date"], utc=True).dt.floor("h")
        matches = universe.loc[(universe["pair"] == pair) & (dates == hour.floor("h"))]
        return not matches.empty and bool(matches["eligible"].fillna(False).any())

    def confirm_trade_entry(
        self,
        pair: str,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        current_time: datetime,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> bool:
        return (
            parse_v2_entry_tag(entry_tag) is not None
            and self._current_universe_allows(pair, current_time)
        )

    def _portfolio_snapshot(self) -> tuple[list[float], list[str], float]:
        try:
            open_trades = Trade.get_open_trades()
        except Exception:
            open_trades = []
        risks: list[float] = []
        sides: list[str] = []
        for trade in open_trades:
            parsed = parse_v2_entry_tag(trade.enter_tag)
            risks.append(parsed[2] if parsed else 0.0075)
            sides.append("short" if trade.is_short else "long")
        equity = float(self.wallets.get_total_stake_amount())
        self._equity_peak = max(self._equity_peak, equity)
        drawdown = 0.0 if self._equity_peak <= 0 else 1 - equity / self._equity_peak
        return risks, sides, max(0.0, drawdown)

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
        parsed = parse_v2_entry_tag(entry_tag)
        if parsed is None or current_rate <= 0:
            return 0.0
        _, stop_price, _ = parsed
        open_risks, open_sides, drawdown = self._portfolio_snapshot()
        budget = risk_budget(open_risks, open_sides, side, drawdown)
        if budget <= 0:
            return 0.0
        try:
            stake = collateral_for_risk(
                equity=float(self.wallets.get_total_stake_amount()),
                risk_pct=budget,
                stop_distance_ratio=abs(current_rate - stop_price) / current_rate,
                leverage=leverage,
                max_collateral=max_stake,
            )
        except ValueError:
            return 0.0
        if min_stake is not None and stake < min_stake:
            return 0.0
        return stake

    @staticmethod
    def _r_multiple(trade: Trade, rate: float) -> float:
        parsed = parse_v2_entry_tag(trade.enter_tag)
        if parsed is None:
            return 0.0
        _, stop, _ = parsed
        initial_risk = abs(trade.open_rate - stop)
        if initial_risk <= 0:
            return 0.0
        move = trade.open_rate - rate if trade.is_short else rate - trade.open_rate
        return move / initial_risk

    def _latest_ema20(self, pair: str, fallback: float) -> float:
        try:
            frame, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            return float(frame.iloc[-1]["ema20"])
        except (AttributeError, IndexError, KeyError, TypeError):
            return fallback

    def adjust_trade_position(
        self,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        min_stake: float | None,
        max_stake: float,
        current_entry_rate: float,
        current_exit_rate: float,
        current_entry_profit: float,
        current_exit_profit: float,
        **kwargs,
    ) -> float | None:
        if getattr(trade, "nr_of_successful_exits", 0) == 0 and self._r_multiple(trade, current_rate) >= 2:
            return -float(trade.stake_amount) * 0.40
        return None

    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ) -> str | None:
        age_hours = (current_time - trade.open_date_utc).total_seconds() / 3600
        state = TradeState(
            age_hours=age_hours,
            r_multiple=self._r_multiple(trade, current_rate),
            partial_taken=getattr(trade, "nr_of_successful_exits", 0) > 0,
            rate=current_rate,
            entry=trade.open_rate,
            ema20=self._latest_ema20(pair, current_rate),
            fees_ratio=float(trade.fee_open or 0) + float(trade.fee_close or 0),
            is_short=trade.is_short,
        )
        return exit_decision(state).full_exit_reason

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
        parsed = parse_v2_entry_tag(trade.enter_tag)
        if parsed is None:
            return None
        _, initial_stop, _ = parsed
        stop = initial_stop
        if getattr(trade, "nr_of_successful_exits", 0) > 0:
            fee_ratio = float(trade.fee_open or 0) + float(trade.fee_close or 0)
            break_even = trade.open_rate * (1 - fee_ratio if trade.is_short else 1 + fee_ratio)
            ema = self._latest_ema20(pair, break_even)
            stop = min(break_even, ema) if trade.is_short else max(break_even, ema)
            if trade.is_short:
                stop = max(stop, current_rate * 1.0001)
            else:
                stop = min(stop, current_rate * 0.9999)
        return stoploss_from_absolute(
            stop,
            current_rate=current_rate,
            is_short=trade.is_short,
            leverage=trade.leverage,
        )
