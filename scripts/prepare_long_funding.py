#!/usr/bin/env python3
"""Prepare frozen original-eight funding history from official OKX archives and REST."""
from __future__ import annotations

import argparse
import calendar
import hashlib
import io
import json
from pathlib import Path
import time
from urllib.parse import urlparse
import zipfile

import numpy as np
import pandas as pd
import requests

SYMBOLS = 'BTC ETH SOL XRP DOGE ADA LINK AVAX'.split()
START = pd.Timestamp('2026-02-26', tz='UTC')
JOIN = pd.Timestamp('2026-06-28', tz='UTC')
END = pd.Timestamp('2026-09-26', tz='UTC')
ENDPOINT = 'https://www.okx.com/priapi/v5/broker/public/trade-data/download-link'


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def atomic_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_bytes(data)
    temporary.replace(path)


def write_json(path, data):
    atomic_bytes(path, (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode())


def monthly_request(symbol, month):
    begin = pd.Timestamp(f'2026-{month:02d}-01', tz='UTC')
    end = pd.Timestamp(f'2026-{month:02d}-{calendar.monthrange(2026, month)[1]}', tz='UTC')
    return {'module': '3', 'instType': 'SWAP',
            'instQueryParam': {'instFamilyList': [symbol + '-USDT']},
            'dateQuery': {'dateAggrType': 'monthly', 'begin': str(begin.value // 10**6),
                          'end': str(end.value // 10**6)}}


def validate_frame(frame):
    frame = frame[['date', 'rate']].copy()
    frame['date'] = pd.to_datetime(frame.date, utc=True, errors='raise')
    frame['rate'] = pd.to_numeric(frame.rate, errors='raise')
    if frame.date.isna().any() or not np.isfinite(frame.rate.to_numpy()).all():
        raise ValueError('Invalid date or nonfinite funding rate')
    for stamp, values in frame.groupby('date').rate:
        if values.max() - values.min() > 1e-12:
            raise ValueError(f'Conflicting duplicate funding settlement: {stamp}')
    return frame.drop_duplicates('date').sort_values('date').reset_index(drop=True)


def parse_archive(data, instrument):
    frames = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name in archive.namelist():
            if not name.lower().endswith('.csv'):
                continue
            with archive.open(name) as source:
                frame = pd.read_csv(source)
            if not {'instrument_name', 'funding_rate', 'funding_time'} <= set(frame.columns):
                raise ValueError('Archive CSV missing funding columns')
            if frame.empty or not frame.instrument_name.eq(instrument).all():
                raise ValueError(f'Archive contains wrong instrument; expected {instrument}')
            stamps = pd.to_numeric(frame.funding_time, errors='raise')
            if (not np.isfinite(stamps).all() or not (stamps % 1 == 0).all()
                    or not stamps.between(10**12, 10**13 - 1).all()):
                raise ValueError('funding_time must be integer UTC epoch milliseconds')
            frames.append(pd.DataFrame({'date': pd.to_datetime(stamps, unit='ms', utc=True),
                                        'rate': frame.funding_rate}))
    if not frames:
        raise ValueError('Archive has no funding CSV')
    return validate_frame(pd.concat(frames, ignore_index=True))


def missing_standard(frame, start=START, end=END):
    expected = pd.date_range(start, end, freq='8h', inclusive='left')
    return [stamp.isoformat() for stamp in expected.difference(frame.date)]


def merge_sources(archive, rest):
    archive, rest = validate_frame(archive), validate_frame(rest)
    rest = rest[(rest.date >= JOIN) & (rest.date < END)]
    overlap = archive.merge(rest, on='date', suffixes=('_archive', '_rest'))
    if overlap.empty:
        raise ValueError('Archive and REST have no overlap to verify')
    if ((overlap.rate_archive - overlap.rate_rest).abs() > 1e-12).any():
        raise ValueError('Archive/REST funding conflict exceeds 1e-12')
    overlap_end = min(archive.date.max(), rest.date.max())
    expected = rest[rest.date <= overlap_end]
    if not set(expected.date) <= set(overlap.date):
        raise ValueError('Archive missing settlements within REST overlap')
    combined = validate_frame(pd.concat([archive[(archive.date >= START) & (archive.date < JOIN)], rest]))
    missing = missing_standard(combined)
    return combined, {'rows': len(overlap), 'first': overlap.date.min().isoformat(),
                      'last': overlap.date.max().isoformat(), 'max_absolute_difference': float(
                          (overlap.rate_archive - overlap.rate_rest).abs().max())}, missing


class OfficialClient:
    def __init__(self, pause=.6, timeout=60, session=None):
        if pause < .6:
            raise ValueError('Request interval must be >= 0.6 seconds')
        self.pause, self.timeout = pause, timeout
        self.session = session or requests.Session()
        self.last_request = 0.0

    def request(self, method, url, **kwargs):
        time.sleep(max(0, self.pause - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()
        response = self.session.request(method, url, timeout=self.timeout, **kwargs)
        response.raise_for_status()
        if method == 'GET' and urlparse(response.url).hostname != 'static.okx.com':
            raise ValueError('Archive redirected outside official static.okx.com')
        return response


def archive_urls(response):
    if str(response.get('code')) != '0':
        raise ValueError(f'Official archive endpoint failed: {response}')
    urls = [group['url'] for detail in response.get('data', {}).get('details', [])
            for group in detail.get('groupDetails', []) if group.get('url')]
    if not urls or any(urlparse(url).scheme != 'https' or urlparse(url).hostname != 'static.okx.com' for url in urls):
        raise ValueError('No valid official static.okx.com archive URL')
    return list(dict.fromkeys(urls))


def load_month(symbol, month, cache, client):
    request = monthly_request(symbol, month)
    response_path = cache / f'{symbol}-2026-{month:02d}-response.json'
    if response_path.exists():
        record = json.loads(response_path.read_text())
        if record['request'] != request:
            raise ValueError('Cached request does not match frozen month')
    else:
        record = {'request': request, 'response': client.request('POST', ENDPOINT, json=request).json()}
        archive_urls(record['response'])
        write_json(response_path, record)
    frames, sources = [], []
    for url in archive_urls(record['response']):
        path = cache / (sha256(url.encode()) + '.zip')
        metadata_path = path.with_suffix('.json')
        if path.exists():
            content = path.read_bytes()
            metadata = json.loads(metadata_path.read_text())
            if metadata.get('url') != url or metadata.get('sha256') != sha256(content):
                raise ValueError('Cached archive hash/source mismatch')
        else:
            content = client.request('GET', url).content
            parse_archive(content, symbol + '-USDT-SWAP')
            atomic_bytes(path, content)
            write_json(metadata_path, {'url': url, 'sha256': sha256(content)})
        frame = parse_archive(content, symbol + '-USDT-SWAP')
        frames.append(frame)
        sources.append({'month': f'2026-{month:02d}', 'url': url, 'sha256': sha256(content),
                        'cache_file': str(path), 'response_file': str(response_path),
                        'response_sha256': sha256(response_path.read_bytes()), 'rows': len(frame),
                        'first': frame.date.min().isoformat(), 'last': frame.date.max().isoformat()})
    return validate_frame(pd.concat(frames)), sources


def prepare(base_dir, cache, client=None):
    output = base_dir / 'okx_scalp_long'
    output.mkdir(parents=True, exist_ok=True)
    client = client or OfficialClient()
    manifest = {'complete': False, 'status': 'running', 'created_at': pd.Timestamp.now(tz='UTC').isoformat(),
                'requested_start_inclusive': START.isoformat(), 'requested_end_exclusive': END.isoformat(),
                'archive_rest_join': JOIN.isoformat(), 'official_endpoint': ENDPOINT,
                'symbols': [], 'errors': []}
    manifest_path = output / 'long-funding-manifest.json'
    write_json(manifest_path, manifest)
    for symbol in SYMBOLS:
        instrument = symbol + '-USDT-SWAP'
        entry = {'instrument': instrument, 'rows': 0, 'sha256': None,
                 'missing_standard_8h_settlements': [], 'sources': []}
        manifest['symbols'].append(entry)
        try:
            archives = []
            for month in range(2, 7):
                frame, sources = load_month(symbol, month, cache, client)
                archives.append(frame)
                entry['sources'].extend(sources)
                print(f'{instrument} archive 2026-{month:02d}: {len(frame)} rows', flush=True)
            rest_path = base_dir / 'okx_scalp_extended' / f'{instrument}-funding.feather'
            rest_bytes = rest_path.read_bytes()
            rest = pd.read_feather(io.BytesIO(rest_bytes))
            entry['rest_source'] = {'file': str(rest_path), 'sha256': sha256(rest_bytes)}
            frame, overlap, missing = merge_sources(pd.concat(archives), rest)
            entry.update(rows=len(frame), overlap_validation=overlap,
                         missing_standard_8h_settlements=missing,
                         first_settlement=frame.date.min().isoformat(), last_settlement=frame.date.max().isoformat())
            if missing:
                raise ValueError(f'Missing {len(missing)} standard UTC 00/08/16 settlements')
            path = output / f'{instrument}-funding.feather'
            # Existing files survive every validation failure; replace only complete frames.
            buffer = io.BytesIO()
            frame.to_feather(buffer)
            atomic_bytes(path, buffer.getvalue())
            entry.update(file=str(path), sha256=sha256(path.read_bytes()), complete=True)
        except Exception as error:
            entry.update(complete=False, error=f'{type(error).__name__}: {error}')
            manifest['errors'].append({'instrument': instrument, 'error': entry['error']})
        write_json(manifest_path, manifest)
    manifest.update(complete=not manifest['errors'], status='complete' if not manifest['errors'] else 'failed')
    write_json(manifest_path, manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-dir', type=Path, default=Path('user_data/data'))
    parser.add_argument('--cache', type=Path, default=Path('work/okx-long-funding-cache'))
    parser.add_argument('--pause', type=float, default=.6)
    parser.add_argument('--timeout', type=float, default=60)
    args = parser.parse_args()
    manifest = prepare(args.base_dir, args.cache, OfficialClient(args.pause, args.timeout))
    if not manifest['complete']:
        raise SystemExit('Funding preparation failed; inspect long-funding-manifest.json')


if __name__ == '__main__':
    main()
