from argparse import Namespace
import pandas as pd
import pytest
from scripts.prepare_extended_training import stitch_candles,prepare


def frames():
    start=pd.Timestamp('2026-06-28',tz='UTC');cut=start+pd.Timedelta(minutes=10);end=cut+pd.Timedelta(minutes=10)
    def part(begin):return pd.DataFrame({'date':pd.date_range(begin,periods=2,freq='5min'),'open':100.,'high':101.,'low':99.,'close':100.,'volume':10.,'quote_volume':1000.})
    return part(start),part(cut),start,cut,end


def test_stitch_preserves_original_rows():
    prefix,base,start,cut,end=frames();before=base.copy(deep=True)
    result=stitch_candles(prefix,base,start,cut,end)
    assert len(result)==4 and result.date.is_monotonic_increasing
    pd.testing.assert_frame_equal(base,before)
    pd.testing.assert_frame_equal(result.iloc[2:].reset_index(drop=True),base)


@pytest.mark.parametrize('case',['missing','duplicate','outside','nan','ohlc','negative_volume'])
def test_corrupt_input_is_rejected(case):
    prefix,base,start,cut,end=frames()
    if case=='missing':prefix=prefix.iloc[:1]
    if case=='duplicate':prefix=pd.concat([prefix,prefix.iloc[:1]],ignore_index=True)
    if case=='outside':prefix.loc[0,'date']=start-pd.Timedelta(minutes=5)
    if case=='nan':prefix.loc[0,'close']=float('nan')
    if case=='ohlc':prefix.loc[0,'high']=98.
    if case=='negative_volume':prefix.loc[0,'volume']=-1
    with pytest.raises(ValueError):stitch_candles(prefix,base,start,cut,end)


def test_output_cannot_overwrite_source(tmp_path):
    with pytest.raises(ValueError,match='overwrite'):
        prepare(Namespace(output_dir=tmp_path,prefix_dir=tmp_path,base_dir=tmp_path/'base'))
