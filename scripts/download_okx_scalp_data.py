#!/usr/bin/env python3
"""Download closed public OKX 5m swap candles, with durable page checkpoints."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path
import sys
import threading
import time

# Permit both direct script execution and package import.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.download_okx_v2_data import request_json, utc_ms
from user_data.strategy_lib.okx_candles import confirmed_candles
import pandas as pd
import numpy as np

BAR_MS = 300_000
SYMBOLS = tuple(f"{coin}-USDT-SWAP" for coin in ("BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "LINK", "AVAX"))
_lock = threading.Lock()
_last_request = 0.0


def swap_symbol(value):
    """Accept only a plain OKX USDT swap identifier, never a path."""
    if not isinstance(value, str) or re.fullmatch(r'[A-Z0-9]{1,20}-USDT-SWAP', value) is None:
        raise argparse.ArgumentTypeError('symbol must match [A-Z0-9]{1,20}-USDT-SWAP')
    return value


def limited_request(endpoint, params):
    global _last_request
    with _lock:
        time.sleep(max(0, 0.3 - (time.monotonic() - _last_request)))
        _last_request = time.monotonic()
    return request_json(endpoint, params)


def iso(timestamp_ms):
    return datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).isoformat()


def atomic_json(path, payload):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    temp.replace(path)


def download_symbol(symbol, start_ms, end_ms, output_dir, server_ms, requester=limited_request):
    """Resume backward paging; cache key includes the exact requested UTC range."""
    symbol = swap_symbol(symbol)
    output_dir = Path(output_dir)
    cache_dir = output_dir / '.cache'
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / f'{symbol}-{start_ms}-{end_ms}.json'
    state = json.loads(cache.read_text()) if cache.exists() else {'cursor': end_ms, 'rows': [], 'done': False}
    while not state['done']:
        cursor = int(state['cursor'])
        page = requester('/api/v5/market/history-candles', {'instId': symbol, 'bar': '5m', 'limit': 300, 'after': cursor}).get('data', [])
        if not page:
            state['done'] = True
        else:
            timestamps = [int(row[0]) for row in page]
            oldest = min(timestamps)
            if oldest >= cursor:
                raise RuntimeError(f'Pagination stopped advancing for {symbol}: {oldest} >= {cursor}')
            state['rows'].extend(row for row, ts in zip(page, timestamps) if start_ms <= ts < end_ms)
            state['cursor'] = oldest
            # Do not infer exhaustion from short pages: OKX can return fewer rows.
            state['done'] = oldest <= start_ms
        atomic_json(cache, state)
    frame = confirmed_candles(state['rows'])
    if not frame.empty:
        frame = frame.loc[(frame.date >= pd.to_datetime(start_ms, unit='ms', utc=True)) &
                          (frame.date < pd.to_datetime(end_ms, unit='ms', utc=True)) &
                          (frame.date <= pd.to_datetime(server_ms - BAR_MS, unit='ms', utc=True))].reset_index(drop=True)
    path = output_dir / f'{symbol}-5m.feather'
    temporary = path.with_suffix('.feather.tmp')
    frame.to_feather(temporary)
    temporary.replace(path)
    expected = pd.date_range(pd.to_datetime(start_ms, unit='ms', utc=True), pd.to_datetime(end_ms, unit='ms', utc=True), freq='5min', inclusive='left')
    available_expected = expected[expected <= pd.to_datetime(server_ms - BAR_MS, unit='ms', utc=True)]
    missing = available_expected.difference(pd.DatetimeIndex(frame.date))
    numeric = frame[['open', 'high', 'low', 'close', 'volume', 'quote_volume']].astype(float)
    invalid_ohlc = ((frame.high < frame[['open', 'close', 'low']].max(axis=1)) |
                    (frame.low > frame[['open', 'close', 'high']].min(axis=1)) |
                    (numeric[['open', 'high', 'low', 'close']] <= 0).any(axis=1))
    quality = {'nan_cells': int(frame.isna().sum().sum()),
               'nonfinite_numeric_cells': int((~np.isfinite(numeric)).sum().sum()),
               'invalid_ohlc_rows': int(invalid_ohlc.sum()),
               'negative_volume_rows': int((numeric[['volume', 'quote_volume']] < 0).any(axis=1).sum()),
               'duplicate_dates': int(frame.date.duplicated().sum()),
               'off_grid_dates': int((frame.date.astype('int64') // 1_000_000 % BAR_MS != 0).sum())}
    return {'instrument': symbol, 'file': path.name, 'rows': len(frame), 'expected_requested_rows': len(expected),
            'expected_closed_rows_at_server_time': len(available_expected), 'missing_closed_bars': len(missing),
            'missing_closed_timestamps': [value.isoformat() for value in missing],
            'first_candle': frame.date.min().isoformat() if len(frame) else None,
            'last_candle': frame.date.max().isoformat() if len(frame) else None,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'quality': quality, 'quality_passed': all(value == 0 for value in quality.values()),
            'complete_requested_range': len(frame) == len(expected) and len(missing) == 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', default='2026-07-28T00:00:00Z')
    parser.add_argument('--end', default='2026-09-26T00:00:00Z')
    parser.add_argument('--symbols', nargs='+', type=swap_symbol, default=list(SYMBOLS))
    parser.add_argument('--output-dir', type=Path, default=Path('user_data/data/okx_scalp'))
    parser.add_argument('--workers', type=int, choices=(1, 2, 3), default=3)
    args = parser.parse_args()
    start, end = utc_ms(args.start), utc_ms(args.end)
    if start >= end or start % BAR_MS or end % BAR_MS:
        parser.error('start/end must be ascending and aligned to 5 minute UTC boundaries')
    clock = limited_request('/api/v5/public/time', {})
    server_ms = int(clock['data'][0]['ts'])
    manifest = {'source': 'OKX public history-candles', 'bar': '5m', 'confirmed_only': True,
                'requested_start_inclusive': iso(start), 'requested_end_exclusive': iso(end),
                'server_time_at_start': iso(server_ms), 'local_time_at_start': datetime.now(timezone.utc).isoformat(), 'symbols': []}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_dir / 'manifest.json'
    atomic_json(manifest_path, manifest)
    print(f'OKX clock: {iso(server_ms)}; requested {iso(start)} to {iso(end)} exclusive', flush=True)
    def run(symbol):
        result = download_symbol(symbol, start, end, args.output_dir, server_ms)
        print(f"{symbol}: {result['rows']} rows, {result['missing_closed_bars']} missing; {result['first_candle']} to {result['last_candle']}", flush=True)
        return result
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for summary in pool.map(run, args.symbols):
            manifest['symbols'].append(summary)
            atomic_json(manifest_path, manifest)
    manifest['server_time_at_completion'] = iso(int(limited_request('/api/v5/public/time', {})['data'][0]['ts']))
    manifest['complete'] = all(row['complete_requested_range'] and row['quality_passed'] for row in manifest['symbols'])
    atomic_json(manifest_path, manifest)
    print(f'Manifest: {manifest_path}; complete={manifest["complete"]}', flush=True)


if __name__ == '__main__':
    main()
