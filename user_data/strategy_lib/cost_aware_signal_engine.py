"""Causal volatility/body gates and wider stops over frozen entry engines.

The underlying engines retain their 12-bar cooldown, including signals rejected
by these gates. All filtering and stop prices use the decision candle only.
"""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd

from user_data.strategy_lib.profit_signal_engine import ProfitParameters, _higher, scan_profit
from user_data.strategy_lib.scalp_signal_engine import ScalpParameters, scan_scalp


@dataclass(frozen=True)
class CostAwareParameters:
    family: str = 'retest'
    stop_atr: float = 1.5
    min_atr_pct: float = 0.0
    min_body_atr: float = 0.0
    fast: int = 20
    slow: int = 60


def scan_cost_aware(frame: pd.DataFrame, params: CostAwareParameters) -> pd.DataFrame:
    if params.family not in {'retest', 'trend_pullback', 'range_reversion', 'breakout'}:
        raise ValueError('invalid cost-aware family')
    try:
        valid = (np.isfinite(params.stop_atr) and params.stop_atr > 0
                 and np.isfinite(params.min_atr_pct) and params.min_atr_pct >= 0
                 and np.isfinite(params.min_body_atr) and params.min_body_atr >= 0
                 and type(params.fast) is int and type(params.slow) is int
                 and 0 < params.fast < params.slow)
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError('stop must be positive, EMA periods increasing, and ATR/body floors nonnegative and finite')
    if params.family in {'retest', 'trend_pullback', 'range_reversion'}:
        family='breakout_retest' if params.family=='retest' else params.family
        f = scan_profit(frame, ProfitParameters(family=family, fast=params.fast, slow=params.slow,
                                              volume_multiple=1., stop_buffer_atr=.3,
                                              cooldown_bars=12))
    else:
        f = scan_scalp(frame, ScalpParameters(fast=9, slow=21, entry_mode='breakout',
                                            compression_atr=.5, stop_atr=1.5,
                                            cooldown_bars=12))
        # Reuse the sorted/deduplicated candles from the source engine. Higher
        # timeframe features become visible only after their complete close.
        raw = f[['date', 'open', 'high', 'low', 'close', 'volume', 'quote_volume']]
        for minutes in (15, 60):
            f = pd.merge_asof(f, _higher(raw, minutes), left_on='decision_at',
                              right_on=f'available{minutes}', direction='backward',
                              tolerance=pd.Timedelta(minutes=minutes - 5))
    long = f.side.eq('long')
    short = f.side.eq('short')
    body = f.close - f.open
    valid = (np.isfinite(f.close) & f.close.gt(0) & np.isfinite(f.atr14) & f.atr14.gt(0)
             & f.atr14.div(f.close).ge(params.min_atr_pct))
    valid &= ((long & body.ge(params.min_body_atr * f.atr14))
              | (short & (-body).ge(params.min_body_atr * f.atr14)))
    f.loc[~valid, 'side'] = ''
    f.loc[~valid, 'initial_stop_price'] = np.nan
    long &= valid
    short &= valid
    f.loc[long, 'initial_stop_price'] = np.minimum(
        f.loc[long, 'initial_stop_price'], f.loc[long, 'close'] - params.stop_atr * f.loc[long, 'atr14'])
    f.loc[short, 'initial_stop_price'] = np.maximum(
        f.loc[short, 'initial_stop_price'], f.loc[short, 'close'] + params.stop_atr * f.loc[short, 'atr14'])
    return f
