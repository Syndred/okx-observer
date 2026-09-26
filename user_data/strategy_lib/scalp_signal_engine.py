"""Causal five-minute dual EMA compression entries, using existing ATR indicators."""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
from user_data.strategy_lib.v2_signal_engine import add_v2_indicators
from user_data.strategy_lib.v2_backtester import EntryEvent

@dataclass(frozen=True)
class ScalpParameters:
    fast: int = 9
    slow: int = 21
    entry_mode: str = 'breakout'
    compression_atr: float = .5
    stop_atr: float = 1.5
    cooldown_bars: int = 12


def scan_scalp(frame: pd.DataFrame, params: ScalpParameters) -> pd.DataFrame:
    if not 0 < params.fast < params.slow or params.entry_mode not in {'breakout','pullback'}:
        raise ValueError('invalid moving averages or entry mode')
    if not np.isfinite(params.compression_atr) or params.compression_atr <= 0 or not np.isfinite(params.stop_atr) or params.stop_atr <= 0 or params.cooldown_bars < 1:
        raise ValueError('invalid compression, stop or cooldown')
    raw = frame[['date','open','high','low','close','volume','quote_volume']].copy()
    raw['date'] = pd.to_datetime(raw['date'], utc=True).dt.as_unit('ns')
    raw = raw.sort_values('date').drop_duplicates('date').reset_index(drop=True)
    f = add_v2_indicators(raw)
    f['fast'] = f.close.ewm(span=params.fast,adjust=False,min_periods=params.fast).mean()
    f['slow'] = f.close.ewm(span=params.slow,adjust=False,min_periods=params.slow).mean()
    f['compression'] = (f.fast-f.slow).abs()/f.atr14
    # Aggregate only full, contiguous 15m bars; available at their close.
    group=raw.set_index('date').resample('15min',label='left',closed='left')
    high=group.agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum','quote_volume':'sum'})
    high=high.loc[group.size()==3].reset_index()
    high['bias'] = high.close.ewm(span=9,adjust=False,min_periods=9).mean()-high.close.ewm(span=21,adjust=False,min_periods=21).mean()
    high['available'] = high.date + pd.Timedelta(minutes=15)
    f['decision_at'] = f.date + pd.Timedelta(minutes=5)
    f=pd.merge_asof(f, high[['available','bias']],left_on='decision_at',right_on='available',direction='backward')
    compressed = f.compression <= params.compression_atr
    # At least three compressed bars in a five-bar window; strictly prior to signal.
    coil = (compressed.rolling(5,min_periods=5).sum()>=3).shift(1,fill_value=False)
    recent = coil.rolling(12,min_periods=1).max().fillna(0).astype(bool)
    liquid = f.volume >= f.volume.rolling(20,min_periods=20).median()
    long_trend = (f.fast>f.slow)&(f.bias>0)
    short_trend = (f.fast<f.slow)&(f.bias<0)
    if params.entry_mode == 'breakout':
        upper=f.high.rolling(6,min_periods=6).max().shift(1)
        lower=f.low.rolling(6,min_periods=6).min().shift(1)
        long_signal=recent&long_trend&liquid&(f.close>upper+.05*f.atr14)
        short_signal=recent&short_trend&liquid&(f.close<lower-.05*f.atr14)
    else:
        long_signal=recent&long_trend&liquid&(f.low<=f.fast)&(f.close>f.fast)&(f.close>f.open)
        short_signal=recent&short_trend&liquid&(f.high>=f.fast)&(f.close<f.fast)&(f.close<f.open)
    # Require continuous historical warmup; never let a gap masquerade as 5m data.
    continuous = f.date.diff().eq(pd.Timedelta(minutes=5)).rolling(120,min_periods=120).sum().eq(120)
    f['side']=''; f['initial_stop_price']=np.nan
    next_index=0
    for i in np.flatnonzero(((long_signal|short_signal)&continuous).to_numpy()):
        if i<next_index: continue
        side='long' if long_signal.iloc[i] else 'short'
        f.at[i,'side']=side
        f.at[i,'initial_stop_price']=float(f.close.iloc[i])+params.stop_atr*float(f.atr14.iloc[i])*(-1 if side=='long' else 1)
        next_index=i+params.cooldown_bars
    return f


def signal_events(pair: str, signals: pd.DataFrame, target_r: float,
                  cost_multiple: float = 1.5, round_trip_cost: float = .002) -> list[EntryEvent]:
    """Filter target headroom on known close, never future entry price or outcomes."""
    events=[]
    for row in signals.loc[signals.side!=''].itertuples():
        risk=abs(float(row.close)-float(row.initial_stop_price))/float(row.close)
        if risk*target_r < cost_multiple*round_trip_cost: continue
        events.append(EntryEvent(pd.Timestamp(row.decision_at),pair,str(row.side),float(row.initial_stop_price),1))
    return events
