import unittest
import numpy as np
import pandas as pd
from user_data.strategy_lib.scalp_signal_engine import ScalpParameters, scan_scalp, signal_events

class ScalpSignalTests(unittest.TestCase):
    def source(self):
        rng=np.random.default_rng(3); c=100+np.cumsum(rng.normal(.015,.14,500))
        return pd.DataFrame(dict(date=pd.date_range('2026-01-01',periods=500,freq='5min',tz='UTC'),open=c-.03,high=c+.1,low=c-.13,close=c,volume=10.,quote_volume=1000.))
    def test_future_changes_cannot_change_past_signals(self):
        f=self.source(); altered=f.copy(); altered.loc[300:,['open','high','low','close']]*=100
        a=scan_scalp(f,ScalpParameters());b=scan_scalp(altered,ScalpParameters())
        pd.testing.assert_frame_equal(a.iloc[:300],b.iloc[:300])
    def test_closed_higher_timeframe_only(self):
        f=self.source();a=scan_scalp(f,ScalpParameters())
        # row300 opens01:00? regardless last15m is only available at decision instant
        valid=a.available.notna();self.assertTrue((a.loc[valid,'available']<=a.loc[valid,'decision_at']).all())
    def test_entry_time_is_signal_close(self):
        f=scan_scalp(self.source(),ScalpParameters(compression_atr=2,entry_mode='pullback'))
        events=signal_events('A',f,3,cost_multiple=0)
        self.assertGreater(len(events),0)
        for e in events:
            row=f.loc[f.decision_at==e.date].iloc[0]
            self.assertEqual(e.date,row.date+pd.Timedelta(minutes=5))
        diffs=pd.Series([e.date for e in events]).diff().dropna()
        self.assertTrue((diffs>=pd.Timedelta(minutes=60)).all())
    def test_gap_blocks_new_signals_during_warmup(self):
        f=self.source().drop(index=250); a=scan_scalp(f,ScalpParameters(compression_atr=2,entry_mode='pullback'))
        self.assertTrue((a.iloc[250:369].side=='').all())
    def test_cost_headroom_filters_without_future_prices(self):
        f=scan_scalp(self.source(),ScalpParameters(compression_atr=2,entry_mode='pullback'))
        self.assertEqual(signal_events('A',f,.01,cost_multiple=100),[])
    def test_invalid_parameters(self):
        with self.assertRaises(ValueError):scan_scalp(self.source(),ScalpParameters(fast=21,slow=9))

if __name__=='__main__':unittest.main()
