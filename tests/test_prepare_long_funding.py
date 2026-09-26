import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import pandas as pd

from scripts import prepare_long_funding as funding


def zipped(rows):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('funding.csv', 'instrument_name,funding_rate,funding_time\n' + '\n'.join(rows))
    return buffer.getvalue()


def row(date, rate='0.0001', instrument='BTC-USDT-SWAP'):
    return f'{instrument},{rate},{pd.Timestamp(date).value // 10**6}'


def frame(start, end):
    return pd.DataFrame({'date': pd.date_range(start, end, freq='8h', inclusive='left'), 'rate': .0001})


class FundingTests(unittest.TestCase):
    def test_wrong_coin_nonfinite_and_invalid_epoch(self):
        for content in [zipped([row('2026-03-01T00:00Z', instrument='ETH-USDT-SWAP')]),
                        zipped([row('2026-03-01T00:00Z', rate='nan')]),
                        zipped([row('2026-03-01T00:00Z', rate='inf')]),
                        zipped([row('2026-03-01T00:00Z', rate='not-a-number')]),
                        zipped(['BTC-USDT-SWAP,0.1,1772323200'])]:
            with self.assertRaises(ValueError):
                funding.parse_archive(content, 'BTC-USDT-SWAP')

    def test_duplicate_conflict_and_identical_deduplication(self):
        a = row('2026-03-01T00:00Z')
        self.assertEqual(len(funding.parse_archive(zipped([a, a]), 'BTC-USDT-SWAP')), 1)
        with self.assertRaisesRegex(ValueError, 'Conflicting duplicate'):
            funding.parse_archive(zipped([a, row('2026-03-01T00:00Z', '.0002')]), 'BTC-USDT-SWAP')

    def test_utc_boundaries_overlap_and_extra_settlement(self):
        archive = frame(funding.START - pd.Timedelta(hours=8), pd.Timestamp('2026-06-30T16:00Z'))
        rest = frame(funding.JOIN, funding.END + pd.Timedelta(hours=8))
        extra = pd.DataFrame({'date': [funding.START + pd.Timedelta(hours=4)], 'rate': [.0002]})
        result, overlap, missing = funding.merge_sources(pd.concat([archive, extra]), rest)
        self.assertEqual(result.date.min(), funding.START)
        self.assertEqual(result.date.max(), funding.END - pd.Timedelta(hours=8))
        self.assertIn(extra.date.iloc[0], set(result.date))
        self.assertFalse(missing)
        self.assertGreater(overlap['rows'], 0)
        rest.loc[0, 'rate'] = .0003
        with self.assertRaisesRegex(ValueError, 'Archive/REST funding conflict'):
            funding.merge_sources(archive, rest)

    def test_missing_standard_settlement(self):
        archive = frame(funding.START, pd.Timestamp('2026-06-30T16:00Z')).iloc[1:]
        rest = frame(funding.JOIN, funding.END)
        _, _, missing = funding.merge_sources(archive, rest)
        self.assertEqual(missing, [funding.START.isoformat()])

    def test_cache_reused_and_tampering_rejected(self):
        content = zipped([row('2026-03-01T00:00Z')])
        url = 'https://static.okx.com/test.zip'
        response = {'code': '0', 'data': {'details': [{'groupDetails': [{'url': url}]}]}}
        class Client:
            calls = 0
            def request(self, method, target, **kwargs):
                self.calls += 1
                class Response:
                    def json(self):
                        return response
                result = Response()
                result.content = content
                return result
        with tempfile.TemporaryDirectory() as directory:
            cache, client = Path(directory), Client()
            funding.load_month('BTC', 3, cache, client)
            funding.load_month('BTC', 3, cache, client)
            self.assertEqual(client.calls, 2)
            next(cache.glob('*.zip')).write_bytes(b'broken')
            with self.assertRaisesRegex(ValueError, 'hash/source mismatch'):
                funding.load_month('BTC', 3, cache, client)

    def test_failed_preparation_preserves_existing_file_and_source(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            output = base / 'okx_scalp_long'
            output.mkdir()
            existing = output / 'BTC-USDT-SWAP-funding.feather'
            existing.write_bytes(b'old validated result')
            rest_dir = base / 'okx_scalp_extended'
            rest_dir.mkdir()
            rest_file = rest_dir / existing.name
            frame(funding.JOIN, funding.END).to_feather(rest_file)
            original = rest_file.read_bytes()
            archive = frame(funding.START, pd.Timestamp('2026-06-30T16:00Z')).iloc[1:]
            with patch.object(funding, 'SYMBOLS', ['BTC']), patch.object(funding, 'load_month', return_value=(archive, [])):
                result = funding.prepare(base, base / 'cache')
            self.assertFalse(result['complete'])
            self.assertEqual(result['status'], 'failed')
            self.assertTrue(result['symbols'][0]['missing_standard_8h_settlements'])
            self.assertEqual(existing.read_bytes(), b'old validated result')
            self.assertEqual(rest_file.read_bytes(), original)
            self.assertFalse(json.loads((output / 'long-funding-manifest.json').read_text())['complete'])

    def test_url_requires_official_source_and_spacing(self):
        with self.assertRaises(ValueError):
            funding.archive_urls({'code': '0', 'data': {'details': [{'groupDetails': [{'url': 'https://evil.test/archive.zip'}]}]}})
        with self.assertRaises(ValueError):
            funding.OfficialClient(.1)


if __name__ == '__main__':
    unittest.main()
