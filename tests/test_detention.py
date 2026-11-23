from app.modules.detention.rules import build_ledger, load_baseline, summarize, _largest_remainder
from app.config import ROOT
from decimal import Decimal
from copy import deepcopy
import pytest

@pytest.fixture
def baseline():
    shares_raw=(ROOT/'sample_data/detention_shares.csv').read_bytes()
    events_raw=(ROOT/'sample_data/detention_events.csv').read_bytes()
    shares,events=load_baseline(shares_raw,events_raw)
    policy={'free_days':7,'daily_rate':'150.00','basis_tolerance_hours':48,'settlement':'explainable'}
    return shares,events,policy

ROOTS=['M001','M002','M003','M004','M005','M006']

def box(entries,c): return next(e for e in entries if e['container']==c)

def test_baseline_sample_loads(baseline):
    shares,events,_=baseline
    assert {r['container'] for r in shares}>={'C001','C009'}
    assert len([r for r in events if r['event']=='RETURN'])==6

def test_carrier_basis_overdue_and_fee(baseline):
    shares,events,policy=baseline
    e=box(build_ledger(shares,events,ROOTS,policy),'C001')
    # 提空11-03到还箱11-12共9天，免7天，超2天，150/天=300
    assert e['entry_status']=='正式责任' and e['basis']=='carrier'
    assert e['carrier_days']==9 and e['port_days']==10
    assert e['overdue_days']==2 and e['container_fee']=='300.00' and e['sealable'] is True

def test_open_return_receipt_blocks_formal(baseline):
    shares,events,policy=baseline
    e=box(build_ledger(shares,events,ROOTS,policy),'C004')
    assert e['entry_status']=='争议暂记' and e['container_fee']=='0.00'
    assert 'RETURN_RECEIPT_OPEN' in e['block_codes'] and e['sealable'] is False
    assert all(b['bill_fee']=='0.00' for b in e['allocations'])

def test_unclosed_relation_blocks_formal(baseline):
    shares,events,policy=baseline
    e=box(build_ledger(shares,events,ROOTS,policy),'C009')
    assert 'RELATION_NOT_CLOSED' in e['block_codes'] and e['entry_status']=='争议暂记'

def test_basis_unreconciled_when_gap_exceeds_tolerance(baseline):
    from app.coerce import instant
    shares,events,policy=deepcopy(baseline[0]),deepcopy(baseline[1]),deepcopy(baseline[2])
    for r in events:
        if r['container']=='C005' and r['event']=='GATE_OUT':
            r['event_time']='2026-11-20T09:00:00+08:00'; r['time']=instant(r['event_time'])
    e=box(build_ledger(shares,events,ROOTS,policy),'C005')
    assert 'BASIS_UNRECONCILED' in e['block_codes'] and e['sealable'] is False

def test_fee_counted_once_and_largest_remainder(baseline):
    shares,events,policy=baseline
    entries=build_ledger(shares,events,ROOTS,policy)
    for e in entries:
        if e['entry_status']=='正式责任':
            assert sum(Decimal(b['bill_fee']) for b in e['allocations'])==Decimal(e['container_fee'])
    c2=box(entries,'C002')  # 150元按24:16分摊 -> 90/60
    assert [b['bill_fee'] for b in c2['allocations']]==['90.00','60.00']

def test_largest_remainder_exact_sum():
    splits=_largest_remainder(Decimal('100.00'),[1,1,1])
    assert sum(Decimal(v) for v in splits)==Decimal('100.00')
    splits=_largest_remainder(Decimal('0.05'),[1,1,1])
    assert sum(Decimal(v) for v in splits)==Decimal('0.05')

def test_free_period_zero_overdue(baseline):
    shares,events,policy=baseline
    e=box(build_ledger(shares,events,ROOTS,policy),'C006')
    assert e['overdue_days']==0 and e['container_fee']=='0.00' and e['sealable'] is True

def test_whole_container_waits_for_all(baseline):
    shares,events,policy=baseline
    policy['settlement']='whole_container'
    entries=build_ledger(shares,events,ROOTS,policy)
    assert summarize(entries)['formal']==0
    assert all('WAIT_WHOLE_CONTAINER' in e['block_codes'] for e in entries)
    assert all(e['container_fee']=='0.00' for e in entries)

def test_explainable_settles_known_boxes_only(baseline):
    shares,events,policy=baseline
    entries=build_ledger(shares,events,ROOTS,policy)
    s=summarize(entries)
    assert s['formal']==5 and s['disputed']==2 and s['formal_fee']=='1950.00'

def test_reversed_timeline_rejected(baseline):
    shares,events,policy=deepcopy(baseline[0]),deepcopy(baseline[1]),deepcopy(baseline[2])
    from app.coerce import instant
    for r in events:
        if r['container']=='C001' and r['event']=='GATE_OUT':
            r['event_time']='2026-10-30T09:00:00+08:00';r['time']=instant(r['event_time'])
    with pytest.raises(Exception): build_ledger(shares,events,ROOTS,policy)

def test_basis_detail_documents_both_clocks(baseline):
    shares,events,policy=baseline
    e=box(build_ledger(shares,events,ROOTS,policy),'C001')
    assert '提空回执' in e['basis_detail'] and '港口进场离场' in e['basis_detail']
