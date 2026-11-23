from fastapi.testclient import TestClient
from app import main

def client(tmp_path,monkeypatch):
    monkeypatch.setattr(main,'DB_PATH',str(tmp_path/'api.sqlite'))
    return TestClient(main.app)

def test_detention_page_and_api(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch)
    run=c.post('/api/runs',json={}).json();rid=run['manifest']['run_id']
    assert c.post('/api/detention',json={}).status_code==200
    page=c.get(f'/detention?run_id={rid}')
    assert page.status_code==200 and '超期用箱责任账' in page.text
    assert rid in page.text

def test_ledger_lifecycle_over_http(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch)
    rid=c.post('/api/runs',json={}).json()['manifest']['run_id']
    r=c.post(f'/api/detention/{rid}/ledger')
    assert r.status_code==200 and r.json()['generation']==1
    assert c.get(f'/api/detention/{rid}/ledger').json()['generation']==1
    receipt={'receipt_id':'RCP-C004-R','container':'C004','event':'RETURN','event_time':'2026-11-12T15:00:00+08:00','closed':True}
    first=c.post(f'/api/detention/{rid}/receipts',json=receipt).json()
    second=c.post(f'/api/detention/{rid}/receipts',json=receipt).json()
    assert first['generation']==2 and second['generation']==2
    c004=next(e for e in second['entries'] if e['container']=='C004')
    assert c004['entry_status']=='正式责任'
    bad=c.post(f'/api/detention/{rid}/receipts',json={'receipt_id':''})
    assert bad.status_code==422
    assert c.get(f'/api/detention/{rid}/ledger').json()['generation']==2

def test_share_and_recompute_endpoints(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch)
    rid=c.post('/api/runs',json={}).json()['manifest']['run_id']
    c.post(f'/api/detention/{rid}/ledger')
    r=c.post(f'/api/detention/{rid}/shares',json={'container':'C009','master_bill':'M001','pieces':10,'replace_master_bill':'M999'})
    assert r.status_code==200
    r=c.post(f'/api/detention/{rid}/recompute',json={'containers':['C002']})
    assert r.status_code==200 and r.json()['generation']==3
    assert c.post(f'/api/detention/{rid}/recompute',json={'containers':'C002'}).status_code==422
    gens=c.get(f'/api/detention/{rid}/generations').json()['generations']
    assert [g['generation'] for g in gens]==[1,2,3]

def test_export_contains_official_ledger(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch)
    rid=c.post('/api/runs',json={}).json()['manifest']['run_id']
    c.post(f'/api/detention/{rid}/ledger')
    z=c.get(f'/api/export/{rid}')
    assert z.status_code==200
    import zipfile,io,json
    names=zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert 'detention_ledger.json' in names and 'detention_ledger_official.json' in names and 'detention_evidence.json' in names
    bundle=json.loads(zipfile.ZipFile(io.BytesIO(z.content)).read('detention_ledger.json'))
    assert all(e['sealable'] for e in bundle['sealable_entries'])
    assert set(bundle['excluded_disputed'])=={'C004','C009'}

def test_whole_container_policy_via_overrides(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch)
    run=c.post('/api/runs',json={'overrides':{'detention':{'settlement':'whole_container'}}}).json()
    rid=run['manifest']['run_id']
    data=c.post(f'/api/detention/{rid}/ledger').json()
    assert data['summary']['formal']==0 and data['settlement']=='whole_container'
