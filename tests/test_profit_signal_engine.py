import unittest
import numpy as np
import pandas as pd

from user_data.strategy_lib.profit_signal_engine import (
    FAMILIES, ProfitParameters, scan_profit, profit_events,
)


class ProfitSignalTests(unittest.TestCase):
    def source(self, count=420):
        c = 100 + np.arange(count) * .001
        return pd.DataFrame(dict(date=pd.date_range('2026-01-01', periods=count, freq='5min', tz='UTC'),
                                 open=c-.01, high=c+.1, low=c-.1, close=c,
                                 volume=10., quote_volume=1000.))

    def retest(self, short=False):
        f = self.source()
        boundary = f.high.iloc[264:270].max()
        f.loc[270, ['open', 'high', 'low', 'close']] = [boundary-.1, boundary+.65, boundary-.11, boundary+.6]
        f.loc[271, ['open', 'high', 'low', 'close']] = [boundary+.2, boundary+.3, boundary-.04, boundary+.1]
        if short:
            for col in ['open', 'high', 'low', 'close']:
                f[col] = 200-f[col]
            f[['high', 'low']] = f[['low', 'high']].to_numpy()
        return f, boundary

    def test_future_cannot_change_history_all_families(self):
        f = self.source()
        altered = f.copy()
        altered.loc[310:, ['open', 'high', 'low', 'close', 'volume']] *= 10
        for family in FAMILIES:
            p = ProfitParameters(family=family)
            pd.testing.assert_frame_equal(scan_profit(f, p).iloc[:310], scan_profit(altered, p).iloc[:310])

    def test_retest_first_touch_long_and_short_not_breakout_bar(self):
        for short in (False, True):
            source, _ = self.retest(short)
            f = scan_profit(source, ProfitParameters())
            self.assertEqual(f.side.iloc[270], '')
            self.assertEqual(f.side.iloc[271], 'short' if short else 'long')
            self.assertTrue(f.side.iloc[272:283].eq('').all())
            if short:
                self.assertGreater(f.initial_stop_price.iloc[271], f.high.iloc[271])
            else:
                self.assertLess(f.initial_stop_price.iloc[271], f.low.iloc[271])

    def test_failed_first_touch_cancels_and_later_recovery_cannot_enter(self):
        f, boundary = self.retest()
        f.loc[271, 'close'] = boundary-.02
        f.loc[272, ['open', 'high', 'low', 'close']] = [boundary+.05, boundary+.2, boundary-.01, boundary+.1]
        out = scan_profit(f, ProfitParameters())
        self.assertTrue(out.side.iloc[270:273].eq('').all())

    def test_pending_expires_after_six_bars(self):
        f, boundary = self.retest()
        for i in range(271, 277):
            f.loc[i, ['open', 'high', 'low', 'close']] = [boundary+.2, boundary+.3, boundary+.1, boundary+.2]
        f.loc[277, ['open', 'high', 'low', 'close']] = [boundary+.1, boundary+.3, boundary-.01, boundary+.2]
        self.assertTrue(scan_profit(f, ProfitParameters()).side.iloc[270:278].eq('').all())

    def test_missing_bar_clears_pending_and_blocks_warmup(self):
        f, _ = self.retest()
        out = scan_profit(f.drop(index=271), ProfitParameters())
        self.assertTrue(out.side.iloc[270:389].eq('').all())

    def test_partial_higher_candle_not_visible(self):
        f = self.source()
        a = scan_profit(f.iloc[:272], ProfitParameters())
        # 272nd base bar closes 22:40; 22:00 hour is still partial.
        self.assertEqual(a.available60.iloc[-1], pd.Timestamp('2026-01-01 22:00', tz='UTC'))
        self.assertEqual(a.available15.iloc[-1], pd.Timestamp('2026-01-01 22:30', tz='UTC'))
        altered = f.iloc[:272].copy()
        altered.loc[264:, ['open', 'high', 'low', 'close']] *= 2
        b = scan_profit(altered, ProfitParameters())
        self.assertEqual(a.bias60.iloc[-1], b.bias60.iloc[-1])
        self.assertTrue(a.side.iloc[:120].eq('').all())

    def test_incomplete_higher_bucket_not_forward_filled_as_fresh(self):
        f = self.source().drop(index=260)
        out = scan_profit(f, ProfitParameters())
        at = out.loc[out.decision_at.eq(pd.Timestamp('2026-01-01 22:00', tz='UTC'))].iloc[0]
        self.assertTrue(pd.isna(at.bias60))

    def test_events_use_close_costs_and_ignore_next_open(self):
        f, _ = self.retest()
        a = scan_profit(f, ProfitParameters())
        b = f.copy()
        b.loc[272, 'open'] = 10000
        modified = scan_profit(b, ProfitParameters())
        ea = profit_events('A', a.iloc[:272], 3)
        eb = profit_events('A', modified.iloc[:272], 3)
        self.assertEqual(ea, eb)
        self.assertEqual(len(ea), 1)
        self.assertEqual(ea[0].date, f.date.iloc[272])
        self.assertEqual(profit_events('A', a, .001), [])

    def test_event_cost_boundary_and_direction(self):
        f = pd.DataFrame([dict(side='long', close=100., initial_stop_price=99., decision_at=pd.Timestamp('2026-01-01', tz='UTC'))])
        self.assertEqual(len(profit_events('A', f, .4)), 1)
        self.assertEqual(profit_events('A', f, .399), [])
        f['initial_stop_price'] = 101.
        self.assertEqual(profit_events('A', f, 2), [])

    def test_other_families_generate_both_directions(self):
        for family in ('trend_pullback', 'sweep_reclaim', 'range_reversion'):
            for short in (False, True):
                f = self.source()
                if family == 'trend_pullback':
                    f.loc[270, ['open', 'high', 'low', 'close']] = [100.25, 100.28, 100.10, 100.27]
                elif family == 'sweep_reclaim':
                    f.loc[270, ['open', 'high', 'low', 'close']] = [100.2, 100.31, 100.10, 100.30]
                else:
                    f.loc[269, ['open', 'high', 'low', 'close']] = [100.2, 100.3, 99.1, 99.2]
                    f.loc[270, ['open', 'high', 'low', 'close']] = [99.5, 100.02, 99.4, 100.0]
                if short:
                    for col in ('open', 'high', 'low', 'close'):
                        f[col] = 200-f[col]
                    f[['high', 'low']] = f[['low', 'high']].to_numpy()
                out = scan_profit(f, ProfitParameters(family=family))
                self.assertEqual(out.side.iloc[270], 'short' if short else 'long', family)

    def test_invalid_configuration(self):
        for params in [ProfitParameters(family='x'), ProfitParameters(fast=21, slow=9),
                       ProfitParameters(volume_multiple=0), ProfitParameters(cooldown_bars=1)]:
            with self.assertRaises(ValueError):
                scan_profit(self.source(), params)


if __name__ == '__main__':
    unittest.main()
