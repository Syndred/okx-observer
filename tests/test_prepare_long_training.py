from argparse import Namespace
from pathlib import Path
import json
import pandas as pd
import pytest
from scripts import prepare_long_training as prep


def setup(tmp_path,monkeypatch):
    start=pd.Timestamp('2026-02-26',tz='UTC');cut=start+pd.Timedelta(minutes=10);end=cut+pd.Timedelta(minutes=10)
    for k,v in [('START',start),('CUT',cut),('END',end),('DEV_SYMBOLS',('X',))]:monkeypatch.setattr(prep,k,v)
    args=Namespace(prefix_dir=tmp_path/'prefix',base_dir=tmp_path/'base',output_dir=tmp_path/'out')
    for directory,name,begin in [(args.prefix_dir,'manifest.json',start),(args.base_dir,'extended-manifest.json',cut)]:
        directory.mkdir();f=pd.DataFrame({'date':pd.date_range(begin,periods=2,freq='5min'),'open':100.,'high':101.,'low':99.,'close':100.,'volume':10.,'quote_volume':1000.})
        p=directory/'X-5m.feather';f.to_feather(p)
        (directory/name).write_text(json.dumps({'complete':True,'symbols':[{'instrument':'X','sha256':prep.digest(p)}]}))
    args.output_dir.mkdir();fp=args.output_dir/'X-funding.feather';pd.DataFrame({'date':[start],'rate':[.0001]}).to_feather(fp)
    (args.output_dir/'long-funding-manifest.json').write_text(json.dumps({'complete':True,'symbols':[{'instrument':'X','sha256':prep.digest(fp)}]}))
    return args


def test_complete_merge_preserves_sources(tmp_path,monkeypatch):
    args=setup(tmp_path,monkeypatch);original=(args.base_dir/'X-5m.feather').read_bytes()
    prep.prepare(args)
    assert (args.base_dir/'X-5m.feather').read_bytes()==original
    m=json.loads((args.output_dir/'long-manifest.json').read_text())
    assert m['complete'] and m['symbols'][0]['rows']==4
    assert m['reserved_prices_read'] is False


def test_bad_hash_rejected_before_candle_write(tmp_path,monkeypatch):
    args=setup(tmp_path,monkeypatch);p=args.prefix_dir/'manifest.json';m=json.loads(p.read_text());m['symbols'][0]['sha256']='wrong';p.write_text(json.dumps(m))
    with pytest.raises(ValueError,match='hash'):prep.prepare(args)
    assert not (args.output_dir/'X-5m.feather').exists()


def test_incomplete_funding_rejected_before_candle_write(tmp_path,monkeypatch):
    args=setup(tmp_path,monkeypatch);p=args.output_dir/'long-funding-manifest.json';m=json.loads(p.read_text());m['complete']=False;p.write_text(json.dumps(m))
    with pytest.raises(ValueError,match='incomplete'):prep.prepare(args)
    assert not (args.output_dir/'X-5m.feather').exists()
