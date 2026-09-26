import tempfile
import unittest
from pathlib import Path

import pandas as pd

from scripts.download_okx_scalp_data import BAR_MS, download_symbol


def candle(ts, confirmed='1'):
    return [str(ts), '100', '101', '99', '100.5', '20', '2', '200', confirmed]


class ScalpDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def test_download_short_pages_resume_and_missing_closed_bars(self):
        tmp_path = self.directory
        calls = []
        def request(endpoint, params):
            calls.append(params['after'])
            return {'data': {4 * BAR_MS: [candle(3 * BAR_MS), candle(2 * BAR_MS)],
                             2 * BAR_MS: [candle(BAR_MS, '0'), candle(0)]}[params['after']]}
        result = download_symbol('BTC-USDT-SWAP', 0, 4 * BAR_MS, tmp_path, 5 * BAR_MS, request)
        assert calls == [4 * BAR_MS, 2 * BAR_MS]
        assert result['rows'] == 3
        assert result['missing_closed_bars'] == 1
        assert result['complete_requested_range'] is False
        def no_request(*args):
            raise AssertionError('completed historical cache should be reused')
        resumed = download_symbol('BTC-USDT-SWAP', 0, 4 * BAR_MS, tmp_path, 5 * BAR_MS, no_request)
        assert result == resumed
        frame = pd.read_feather(tmp_path / 'BTC-USDT-SWAP-5m.feather')
        assert list(frame.columns) == ['date', 'open', 'high', 'low', 'close', 'volume', 'quote_volume']


    def test_nonadvancing_page_fails(self):
        tmp_path = self.directory
        with self.assertRaisesRegex(RuntimeError, 'stopped advancing'):
            download_symbol('BTC-USDT-SWAP', 0, BAR_MS, tmp_path, 2 * BAR_MS,
                            lambda *args: {'data': [candle(BAR_MS)]})


    def test_page_checkpoint_survives_interruption(self):
        tmp_path = self.directory
        def request(endpoint, params):
            if params['after'] == 2 * BAR_MS:
                return {'data': [candle(BAR_MS)]}
            raise ConnectionError('offline')
        with self.assertRaises(ConnectionError):
            download_symbol('BTC-USDT-SWAP', 0, 2 * BAR_MS, tmp_path, 3 * BAR_MS, request)
        calls = []
        def resumed_request(endpoint, params):
            calls.append(params['after'])
            return {'data': [candle(0)]}
        result = download_symbol('BTC-USDT-SWAP', 0, 2 * BAR_MS, tmp_path, 3 * BAR_MS, resumed_request)
        assert calls == [BAR_MS]
        assert result['complete_requested_range']


    def test_future_candles_never_exported(self):
        tmp_path = self.directory
        result = download_symbol('BTC-USDT-SWAP', 0, 3 * BAR_MS, tmp_path, BAR_MS + 1,
                                 lambda *args: {'data': [candle(2 * BAR_MS), candle(BAR_MS), candle(0)]})
        assert result['rows'] == 1
        assert result['expected_requested_rows'] == 3
        assert result['expected_closed_rows_at_server_time'] == 1
        assert result['complete_requested_range'] is False
