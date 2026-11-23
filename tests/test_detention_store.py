import json, pytest
from app.engine import evaluate
from app import store, detention_store
from app.errors import BusinessError

CLOSED_C004={'receipt_id':'RCP-C004-R','container':'C004','event':'RETURN','event_time':'2026-11-12T15:00:00+08:00','closed':True}

@pytest.fixture
def led(tmp_path):
    p=str(tmp_path/'detention.sqlite')
    run=store.save(p,evaluate())
    return p,run['manifest']['run_id']

def box(data,c): return next(e for e in data['entries'] if e['container']==c)

def test_initialize_creates_v1(led):
    p,rid=led
    data=detention_store.initialize(p,rid)
    assert data['generation']==1 and data['summary']['containers']==7
    again=detention_store.initialize(p,rid)
    assert again['generation']==1  # 重复初始化不建新版

def test_idempotent_same_receipt_no_new_fee(led):
    p,rid=led
    detention_store.initialize(p,rid)
    before=detention_store.ledger(p,rid)
    retry={'receipt_id':'RCP-C001-R','container':'C001','event':'RETURN','event_time':'2026-11-12T10:00:00+08:00','closed':True}
    after=detention_store.add_receipt(p,rid,retry)
    assert after['generation']==before['generation']
    assert box(after,'C001')['container_fee']==box(before,'C001')['container_fee']

def test_receipt_close_only_recomputes_affected_box(led):
    p,rid=led
    detention_store.initialize(p,rid)
    data=detention_store.add_receipt(p,rid,dict(CLOSED_C004))
    payload=json.loads(data['evidence'][-1]['payload'])
    assert payload['affected']==['C004']
    assert sorted(payload['kept'])==['C001','C002','C003','C005','C006','C009']
    assert box(data,'C004')['entry_status']=='正式责任'
    assert box(data,'C004')['container_fee']=='450.00'
    assert box(data,'C001')['container_fee']=='300.00'  # 未受影响箱原样保留

def test_retry_closed_receipt_still_idempotent(led):
    p,rid=led
    detention_store.initialize(p,rid)
    first=detention_store.add_receipt(p,rid,dict(CLOSED_C004))
    second=detention_store.add_receipt(p,rid,dict(CLOSED_C004))
    assert second['generation']==first['generation']

def test_old_generation_deactivated_but_kept(led):
    p,rid=led
    detention_store.initialize(p,rid)
    detention_store.add_receipt(p,rid,dict(CLOSED_C004))
    gens=detention_store.history(p,rid)
    assert [g['generation'] for g in gens]==[1,2]
    assert [g['active'] for g in gens]==[0,1]

def test_bad_receipt_rolls_back_keeping_old_version(led):
    p,rid=led
    detention_store.initialize(p,rid)
    with pytest.raises(BusinessError):
        detention_store.add_receipt(p,rid,{'receipt_id':'','container':'C001'})
    cur=detention_store.ledger(p,rid)
    assert cur['generation']==1 and cur['receipts']==[]

def test_build_failure_restores_previous_ledger(led,monkeypatch):
    p,rid=led
    detention_store.initialize(p,rid)
    monkeypatch.setattr(detention_store,'_build',lambda *a:(_ for _ in ()).throw(RuntimeError('boom')))
    with pytest.raises(RuntimeError):
        detention_store.add_receipt(p,rid,{'receipt_id':'RCP-X','container':'C001','event':'PICKUP','event_time':'2026-11-03T09:00:00+08:00','closed':True})
    monkeypatch.undo()
    cur=detention_store.ledger(p,rid)
    assert cur['generation']==1
    assert all(r['receipt_id']!='RCP-X' for r in cur['receipts'])

def test_evidence_append_only(led):
    p,rid=led
    detention_store.initialize(p,rid)
    detention_store.add_receipt(p,rid,dict(CLOSED_C004))
    detention_store.recompute(p,rid,['C002'])
    data=detention_store.ledger(p,rid)
    kinds=[e['kind'] for e in data['evidence']]
    assert kinds==['GENERATE','RECEIPT_ADD','RECOMPUTE']

def test_share_relation_repair_uses_tombstone(led):
    p,rid=led
    detention_store.initialize(p,rid)
    data=detention_store.change_share(p,rid,{'container':'C009','master_bill':'M001','pieces':10,'replace_master_bill':'M999'})
    c9=box(data,'C009')
    assert 'RELATION_NOT_CLOSED' not in c9['block_codes']
    assert {r['master_bill']:r['pieces'] for r in data['share_changes']}=={'M001':10,'M999':0}

def test_targeted_recompute_marks_changed(led):
    p,rid=led
    detention_store.initialize(p,rid)
    data=detention_store.recompute(p,rid,['C002'])
    payload=json.loads(data['evidence'][-1]['payload'])
    assert payload['requested']==['C002'] and payload['changed']==[]  # 输入未变则结果不变

def test_unknown_container_rejected(led):
    p,rid=led
    detention_store.initialize(p,rid)
    with pytest.raises(BusinessError): detention_store.recompute(p,rid,['NOPE'])

def test_ledger_requires_baseline(tmp_path):
    p=str(tmp_path/'nobase.sqlite')
    run=store.save(p,evaluate({'dataset':'clean'}))
    # clean 批次同样跑过第八模块，含基线；构造缺失基线场景
    import sqlite3,json
    with sqlite3.connect(p) as con:
        row=con.execute('SELECT result FROM runs WHERE id=?',(run['manifest']['run_id'],)).fetchone()
        result=json.loads(row[0]);result.pop('detention_baseline',None)
        con.execute('UPDATE runs SET result=? WHERE id=?',(json.dumps(result,ensure_ascii=False),run['manifest']['run_id']))
    with pytest.raises(BusinessError): detention_store.initialize(p,run['manifest']['run_id'])

def test_sealable_only_formal_entries(led):
    p,rid=led
    data=detention_store.initialize(p,rid)
    for e in data['entries']:
        assert e['sealable']==(e['entry_status']=='正式责任')
