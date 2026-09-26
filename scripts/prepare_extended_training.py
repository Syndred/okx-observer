#!/usr/bin/env python3
"""Validate and stitch fixed historical development data; never read reserved prices."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import numpy as np
import pandas as pd
from scripts import download_profit_funding as funding_loader
from scripts.run_jev_profit_research import DEV_SYMBOLS
from scripts.run_okx_v2_research import write_json
START,CUT,END=[pd.Timestamp(v,tz='UTC') for v in ['2026-06-28','2026-07-28','2026-09-26']]


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def stitch_candles(prefix,base,start=START,cut=CUT,end=END):
    pieces=[]
    for frame,begin,finish in [(prefix,start,cut),(base,cut,end)]:
        frame=frame.copy();frame['date']=pd.to_datetime(frame.date,utc=True)
        frame=frame.sort_values('date').reset_index(drop=True)
        expected=pd.date_range(begin,finish,freq='5min',inclusive='left')
        if not pd.DatetimeIndex(frame.date).equals(expected):raise ValueError('source dates incomplete, duplicated or outside fixed range')
        values=frame[['open','high','low','close','volume','quote_volume']].to_numpy(float)
        if not np.isfinite(values).all() or (values[:,:4]<=0).any() or (values[:,4:]<0).any():raise ValueError('invalid numeric data')
        if (frame.high<frame[['open','low','close']].max(axis=1)).any() or (frame.low>frame[['open','high','close']].min(axis=1)).any():raise ValueError('invalid OHLC')
        pieces.append(frame)
    return pd.concat(pieces,ignore_index=True)


def prepare(args):
    if args.output_dir.resolve() in [args.prefix_dir.resolve(),args.base_dir.resolve()]:raise ValueError('output must not overwrite original data')
    prefix_meta=json.loads((args.prefix_dir/'manifest.json').read_text())
    base_meta=json.loads((args.base_dir/'manifest.json').read_text())
    if not prefix_meta.get('complete') or not base_meta.get('complete'):raise ValueError('incomplete source manifests')
    known_prefix={r['instrument']:r for r in prefix_meta['symbols']};known_base={r['instrument']:r for r in base_meta['symbols']}
    # Validate every input before any network download or output mutation.
    merged={};hashes={}
    for symbol in DEV_SYMBOLS:
        pp=args.prefix_dir/f'{symbol}-5m.feather';bp=args.base_dir/f'{symbol}-5m.feather'
        ph,bh=digest(pp),digest(bp)
        if ph!=known_prefix[symbol]['sha256'] or bh!=known_base[symbol]['sha256']:raise ValueError('source hash mismatch')
        merged[symbol]=stitch_candles(pd.read_feather(pp),pd.read_feather(bp));hashes[symbol]={'prefix':ph,'original':bh}
    args.output_dir.mkdir(parents=True,exist_ok=True)
    manifest={'status':'preparing','complete':False,'reserved_prices_read':False,'start':START.isoformat(),'end':END.isoformat(),
              'source_candle_hashes':hashes,'symbols':[]}
    write_json(args.output_dir/'extended-manifest.json',manifest)
    # Reuse the public funding downloader with a fixed separate destination.
    funding_loader.GROUPS['extended-development']=(args.output_dir.name,'2026-06-28','BTC ETH SOL XRP DOGE ADA LINK AVAX')
    funding_manifest=funding_loader.download_group('extended-development',args.output_dir.parent)
    if funding_manifest['errors']:raise ValueError('funding download failed')
    for symbol in DEV_SYMBOLS:
        frame=merged[symbol];path=args.output_dir/f'{symbol}-5m.feather'
        temp=path.with_suffix('.feather.tmp');frame.to_feather(temp);temp.replace(path)
        fp=args.output_dir/f'{symbol}-funding.feather';fund=pd.read_feather(fp);dates=pd.DatetimeIndex(pd.to_datetime(fund.date,utc=True))
        expected=pd.date_range(START,END,freq='8h',inclusive='left')
        missing=expected.difference(dates)
        if not np.isfinite(fund.rate.to_numpy(float)).all() or dates.has_duplicates:raise ValueError('invalid funding values or duplicate dates')
        manifest['symbols'].append({'instrument':symbol,'rows':len(frame),'sha256':digest(path),
            'funding_rows':len(fund),'funding_sha256':digest(fp),'missing_standard_8h_settlements':len(missing)})
        write_json(args.output_dir/'extended-manifest.json',manifest)
    manifest['complete']=all(r['missing_standard_8h_settlements']==0 for r in manifest['symbols'])
    manifest['status']='complete' if manifest['complete'] else 'funding_coverage_incomplete'
    write_json(args.output_dir/'extended-manifest.json',manifest)
    if not manifest['complete']:raise ValueError('missing historical funding; do not claim complete data')
    print('Complete:',sum(r['rows'] for r in manifest['symbols']),'candles;',sum(r['funding_rows'] for r in manifest['symbols']),'funding records',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix-dir',type=Path,default=ROOT/'user_data/data/okx_scalp_prefix_20260628')
    parser.add_argument('--base-dir',type=Path,default=ROOT/'user_data/data/okx_scalp')
    parser.add_argument('--output-dir',type=Path,default=ROOT/'user_data/data/okx_scalp_extended')
    prepare(parser.parse_args())
if __name__=='__main__':main()
