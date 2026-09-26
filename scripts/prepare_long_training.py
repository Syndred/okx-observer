#!/usr/bin/env python3
"""Assemble the frozen long-history dataset after candle and funding jobs finish."""
from __future__ import annotations
import argparse,json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import pandas as pd
from scripts.prepare_extended_training import stitch_candles,digest
from scripts.run_jev_profit_research import DEV_SYMBOLS
from scripts.run_okx_v2_research import write_json
START,CUT,END=[pd.Timestamp(v,tz='UTC') for v in ['2026-02-26','2026-06-28','2026-09-26']]


def prepare(args):
    if args.output_dir.resolve() in [args.prefix_dir.resolve(),args.base_dir.resolve()]:raise ValueError('output must not overwrite original data')
    pm=json.loads((args.prefix_dir/'manifest.json').read_text())
    bm=json.loads((args.base_dir/'extended-manifest.json').read_text())
    fm=json.loads((args.output_dir/'long-funding-manifest.json').read_text())
    if not all(m.get('complete') for m in [pm,bm,fm]):raise ValueError('prerequisite data incomplete')
    known=[{r['instrument']:r for r in m['symbols']} for m in [pm,bm,fm]]
    pending={};records=[]
    for symbol in DEV_SYMBOLS:
        pp=args.prefix_dir/f'{symbol}-5m.feather';bp=args.base_dir/f'{symbol}-5m.feather';fp=args.output_dir/f'{symbol}-funding.feather'
        hashes=[digest(p) for p in [pp,bp,fp]]
        if any(h!=m[symbol]['sha256'] for h,m in zip(hashes,known)):raise ValueError('source hash mismatch')
        pending[symbol]=stitch_candles(pd.read_feather(pp),pd.read_feather(bp),START,CUT,END)
        fund=pd.read_feather(fp);fund['date']=pd.to_datetime(fund.date,utc=True)
        if fund.date.duplicated().any() or len(pd.date_range(START,END,freq='8h',inclusive='left').difference(pd.DatetimeIndex(fund.date))):raise ValueError('funding coverage failed')
        records.append({'instrument':symbol,'rows':len(pending[symbol]),'prefix_sha256':hashes[0],
                        'base_sha256':hashes[1],'funding_sha256':hashes[2],'funding_rows':len(fund)})
    manifest={'complete':False,'status':'writing','start':START.isoformat(),'end':END.isoformat(),'reserved_prices_read':False,'symbols':records}
    write_json(args.output_dir/'long-manifest.json',manifest)
    for r in records:
        path=args.output_dir/f"{r['instrument']}-5m.feather";tmp=path.with_suffix('.feather.tmp')
        pending[r['instrument']].to_feather(tmp);tmp.replace(path);r['sha256']=digest(path)
    manifest.update(complete=True,status='complete');write_json(args.output_dir/'long-manifest.json',manifest)
    print('Verified long dataset:',sum(r['rows'] for r in records),'candles;',sum(r['funding_rows'] for r in records),'funding rows',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix-dir',type=Path,default=ROOT/'user_data/data/okx_scalp_prefix_20260226')
    parser.add_argument('--base-dir',type=Path,default=ROOT/'user_data/data/okx_scalp_extended')
    parser.add_argument('--output-dir',type=Path,default=ROOT/'user_data/data/okx_scalp_long')
    prepare(parser.parse_args())
if __name__=='__main__':main()
