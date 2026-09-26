from argparse import Namespace
from pathlib import Path
import pandas as pd
import pytest
from scripts import run_cost_aware_research as study


def test_no_candidate_can_be_selected_without_passing_a():
    rows=[{'passed':False,'metrics':{'stress':{'pf':8},'regular':{'pf':10}}}]
    assert study.select_a(rows) is None


def test_empty_output_guard_precedes_dataset_access(tmp_path,monkeypatch):
    (tmp_path/'existing.txt').write_text('preserve')
    monkeypatch.setattr(study,'load_dataset',lambda *a:pytest.fail('unexpected data read'))
    with pytest.raises(ValueError,match='empty'):
        study.run(Namespace(output_dir=tmp_path,data_dir=Path('/no-data')))
    assert (tmp_path/'existing.txt').read_text()=='preserve'


def test_failed_development_never_evaluates_b_or_opens_reserved(tmp_path,monkeypatch):
    calls=[]
    monkeypatch.setattr(study,'load_dataset',lambda *a: ({}, {}, {}))
    monkeypatch.setattr(study,'event_set',lambda *a: [])
    monkeypatch.setattr(study,'period_events',lambda *a: [])
    monkeypatch.setattr(study,'label',lambda *a: pd.DataFrame())
    monkeypatch.setattr(study,'evaluate_events',lambda *a,**kw: pd.DataFrame())
    monkeypatch.setattr(study,'stats',lambda *a: {'n':0,'pf':None,'mean_net_return':None})
    def measurement(out,prefix,frames,funding,events,target,hold,begin,end):
        calls.append((begin,end));return {'passed':False,'regular':{'pf':None},'stress':{'pf':None}}
    monkeypatch.setattr(study,'measure_portfolio',measurement)
    study.run(Namespace(output_dir=tmp_path/'result',data_dir=Path('/dev-only')))
    assert len(calls)==6
    assert all(begin==study.SPLIT_A and end==study.SPLIT_B for begin,end in calls)
    import json
    manifest=json.loads((tmp_path/'result'/'manifest.json').read_text())
    assert manifest['status']=='no_profitable_development_candidate'
    assert manifest['heldout_prices_opened'] is False
