import sqlite3, json, base64
from datetime import datetime, timezone
from pathlib import Path
from app.errors import BusinessError
from app.modules.detention.rules import load_baseline, build_ledger, summarize
from app.coerce import instant

SCHEMA='''
CREATE TABLE IF NOT EXISTS detention_generations(
  run_id TEXT NOT NULL, generation INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1,
  settlement TEXT NOT NULL, created_at TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
  PRIMARY KEY(run_id,generation));
CREATE TABLE IF NOT EXISTS detention_entries(
  run_id TEXT NOT NULL, generation INTEGER NOT NULL, container TEXT NOT NULL,
  entry_status TEXT NOT NULL, basis TEXT NOT NULL, basis_detail TEXT NOT NULL,
  port_days INTEGER, carrier_days INTEGER, free_days INTEGER NOT NULL,
  overdue_days INTEGER, container_fee TEXT NOT NULL, sealable INTEGER NOT NULL,
  block_codes TEXT NOT NULL, allocations TEXT NOT NULL, receipts TEXT NOT NULL,
  return_closed INTEGER NOT NULL, PRIMARY KEY(run_id,generation,container));
CREATE TABLE IF NOT EXISTS detention_receipts(
  run_id TEXT NOT NULL, receipt_id TEXT NOT NULL, container TEXT NOT NULL,
  event TEXT NOT NULL, event_time TEXT NOT NULL, actor TEXT NOT NULL, closed INTEGER NOT NULL,
  updated_at TEXT NOT NULL, PRIMARY KEY(run_id,receipt_id));
CREATE TABLE IF NOT EXISTS detention_share_changes(
  run_id TEXT NOT NULL, container TEXT NOT NULL, master_bill TEXT NOT NULL, pieces INTEGER NOT NULL,
  note TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL, PRIMARY KEY(run_id,container,master_bill));
CREATE TABLE IF NOT EXISTS detention_evidence(
  run_id TEXT NOT NULL, seq INTEGER NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
  created_at TEXT NOT NULL, PRIMARY KEY(run_id,seq));
'''

def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con=sqlite3.connect(path,timeout=15)
    con.row_factory=sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.executescript(SCHEMA)
    return con

def _row_to_entry(r):
    d=dict(r)
    d.pop('run_id',None);d.pop('generation',None)
    for k in ('block_codes','allocations','receipts'): d[k]=json.loads(d[k])
    d['sealable']=bool(d['sealable']);d['return_closed']=bool(d['return_closed'])
    return d

def _entry_payload(e):
    return (e['entry_status'],e['basis'],e['basis_detail'],e['port_days'],e['carrier_days'],e['free_days'],
            e['overdue_days'],e['container_fee'],int(e['sealable']),json.dumps(e['block_codes'],ensure_ascii=False),
            json.dumps(e['allocations'],ensure_ascii=False),json.dumps(e['receipts'],ensure_ascii=False),int(e['return_closed']))

def _current(con,run_id):
    g=con.execute('SELECT MAX(generation) FROM detention_generations WHERE run_id=?',(run_id,)).fetchone()[0]
    if g is None: return None
    rows=[_row_to_entry(r) for r in con.execute('SELECT * FROM detention_entries WHERE run_id=? AND generation=? ORDER BY container',(run_id,g))]
    gen=dict(con.execute('SELECT * FROM detention_generations WHERE run_id=? AND generation=?',(run_id,g)).fetchone())
    return {'generation':g,'settlement':gen['settlement'],'note':gen['note'],'created_at':gen['created_at'],
            'entries':rows,'summary':summarize(rows)}

def _next_seq(con,run_id):
    v=con.execute('SELECT MAX(seq) FROM detention_evidence WHERE run_id=?',(run_id,)).fetchone()[0]
    return 1 if v is None else v+1

def _baseline(con,run_id):
    row=con.execute('SELECT result FROM runs WHERE id=?',(run_id,)).fetchone()
    if not row: raise BusinessError('NOT_FOUND','批次不存在',404)
    result=json.loads(row['result']);base=result.get('detention_baseline')
    if not base: raise BusinessError('BASELINE_MISSING','该批次缺少用箱基线，请先运行超期用箱模块')
    return base,result['graph']['roots'],result['context']['detention']

def _merged_events(con,run_id,base_events):
    # 补证回执覆盖基线同编号事件；同编号重试只更新闭合状态，绝不产生第二条费用来源。
    events=[dict(r) for r in base_events]
    idx={r.get('receipt_id'):i for i,r in enumerate(events) if r.get('receipt_id')}
    for r in con.execute('SELECT * FROM detention_receipts WHERE run_id=? ORDER BY receipt_id',(run_id,)):
        patch={'container':r['container'],'event':r['event'],'event_time':r['event_time'],
               'receipt_id':r['receipt_id'],'actor':r['actor'],'closed':'yes' if r['closed'] else 'no'}
        if r['receipt_id'] in idx: events[idx[r['receipt_id']]]=patch
        else: events.append(patch)
    return events

def _merged_shares(con,run_id,base_shares):
    # 上游拆并关系变化按 (箱,主单) 覆盖件数；零件数为闭合墓碑，不参与分摊且不触发坏件数块码。补证不删除历史键。
    changes={(r['container'],r['master_bill']):r for r in con.execute('SELECT * FROM detention_share_changes WHERE run_id=?',(run_id,))}
    shares=[dict(r) for r in base_shares if (r['container'],r['master_bill']) not in changes]
    for r in changes.values():
        if r['pieces']>0:
            shares.append({'container':r['container'],'master_bill':r['master_bill'],'pieces':r['pieces'],'note':r['note']})
    return shares

def _build(con,run_id):
    base,roots,policy=_baseline(con,run_id)
    shares_raw=base64.b64decode(base['shares_raw_b64']);events_raw=base64.b64decode(base['events_raw_b64'])
    base_shares,base_events=load_baseline(shares_raw,events_raw)
    shares=_merged_shares(con,run_id,base_shares)
    events=_merged_events(con,run_id,base_events)
    for r in events:
        r['time']=instant(r['event_time'])
        r['is_closed']=str(r['closed']).lower() in ('yes','y','true','1','闭合')
    return build_ledger(shares,events,roots,policy),policy

def _persist(con,run_id,generation,entries,settlement,note):
    con.execute('UPDATE detention_generations SET active=0 WHERE run_id=?',(run_id,))
    con.execute('INSERT INTO detention_generations(run_id,generation,active,settlement,created_at,note) VALUES(?,?,1,?,?,?)',
                (run_id,generation,settlement,datetime.now(timezone.utc).isoformat(),note))
    con.executemany('''INSERT INTO detention_entries(run_id,generation,container,entry_status,basis,basis_detail,
        port_days,carrier_days,free_days,overdue_days,container_fee,sealable,block_codes,allocations,receipts,return_closed)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    [(run_id,generation,e['container'])+_entry_payload(e) for e in entries])

def _evidence(con,run_id,kind,payload):
    con.execute('INSERT INTO detention_evidence(run_id,seq,kind,payload,created_at) VALUES(?,?,?,?,?)',
                (run_id,_next_seq(con,run_id),kind,json.dumps(payload,ensure_ascii=False),datetime.now(timezone.utc).isoformat()))

def initialize(path,run_id,note='按舱单批次生成首版责任账'):
    with connect(path) as con:
        if con.execute('SELECT 1 FROM detention_generations WHERE run_id=?',(run_id,)).fetchone():
            return ledger(path,run_id)
        _baseline(con,run_id)
        try:
            entries,policy=_build(con,run_id)
            _persist(con,run_id,1,entries,policy.get('settlement','explainable'),note)
            _evidence(con,run_id,'GENERATE',{'generation':1,'containers':[e['container'] for e in entries]})
        except Exception:
            con.rollback();raise
    return ledger(path,run_id)

def ledger(path,run_id):
    with connect(path) as con:
        if not con.execute('SELECT 1 FROM runs WHERE id=?',(run_id,)).fetchone():
            raise BusinessError('NOT_FOUND','批次不存在',404)
        cur=_current(con,run_id)
        evidence=[dict(r) for r in con.execute('SELECT seq,kind,payload,created_at FROM detention_evidence WHERE run_id=? ORDER BY seq',(run_id,))]
        receipts=[dict(r) for r in con.execute('SELECT receipt_id,container,event,event_time,closed,updated_at FROM detention_receipts WHERE run_id=? ORDER BY receipt_id',(run_id,))]
        shares=[dict(r) for r in con.execute('SELECT container,master_bill,pieces,note,updated_at FROM detention_share_changes WHERE run_id=? ORDER BY container,master_bill',(run_id,))]
    if cur is None:
        return {'run_id':run_id,'generation':0,'entries':[],'summary':summarize([]),'evidence':evidence,'receipts':receipts,'share_changes':shares}
    cur['run_id']=run_id;cur['evidence']=evidence;cur['receipts']=receipts;cur['share_changes']=shares
    return cur

def add_receipt(path,run_id,payload,note='补证回执闭合/拆并关系变化，增量重算受影响箱'):
    required=('receipt_id','container','event','event_time','closed')
    if any(not str(payload.get(k,'')).strip() for k in required):
        raise BusinessError('BAD_RECEIPT','回执缺字段：receipt_id/container/event/event_time/closed')
    if payload['event'] not in ('GATE_IN','GATE_OUT','PICKUP','RETURN'):
        raise BusinessError('BAD_EVENT','回执节点类型无效')
    closed=payload['closed'] if isinstance(payload['closed'],bool) else str(payload['closed']).lower() in ('yes','y','true','1','闭合')
    rid=str(payload['receipt_id']).strip();now=datetime.now(timezone.utc).isoformat()
    event_time=str(payload['event_time']);container=str(payload['container']);event=str(payload['event']);actor=str(payload.get('actor','补证'))
    with connect(path) as con:
        cur=_current(con,run_id)
        if cur is None: raise BusinessError('LEDGER_NOT_INITIALIZED','请先生成责任账')
        existing=con.execute('SELECT * FROM detention_receipts WHERE run_id=? AND receipt_id=?',(run_id,rid)).fetchone()
        patch=(container,event,event_time,actor,int(closed))
        if existing and existing['container']==container and existing['event']==event and existing['event_time']==event_time and bool(existing['closed'])==closed:
            return ledger(path,run_id)  # 同一回执重试：幂等，不新增版次、不新增费用
        if existing is None:
            base,_,_=_baseline(con,run_id)
            base_hit=next((r for r in base['events'] if r.get('receipt_id')==rid),None)
            if base_hit and base_hit['container']==container and base_hit['event']==event and base_hit['event_time']==event_time:
                base_closed=str(base_hit['closed']).lower() in ('yes','y','true','1','闭合')
                if base_closed==closed:
                    return ledger(path,run_id)  # 与基线还箱回执完全相同的重试：不新增费用
        affected={existing['container']} if existing else {container}
        try:
            if existing:
                con.execute('UPDATE detention_receipts SET container=?,event=?,event_time=?,actor=?,closed=?,updated_at=? WHERE run_id=? AND receipt_id=?',
                            (*patch,now,run_id,rid));action='RECEIPT_UPDATE';affected.add(container)
            else:
                con.execute('INSERT INTO detention_receipts VALUES(?,?,?,?,?,?,?,?)',(run_id,rid,*patch,now));action='RECEIPT_ADD'
            _refresh(con,run_id,cur,affected,action,{'receipt_id':rid},note)
        except Exception:
            con.rollback();raise  # 生成失败：旧版责任账保持active，补证不落库
    return ledger(path,run_id)

def change_share(path,run_id,payload,note='上游拆并关系变化，只重算受影响箱'):
    required=('container','master_bill','pieces')
    if any(not str(payload.get(k,'')).strip() for k in required):
        raise BusinessError('BAD_SHARE','拆并关系缺字段：container/master_bill/pieces')
    pieces=int(payload['pieces'])
    if pieces<=0: raise BusinessError('BAD_SHARE','拆并件数须为正整数')
    now=datetime.now(timezone.utc).isoformat()
    with connect(path) as con:
        cur=_current(con,run_id)
        if cur is None: raise BusinessError('LEDGER_NOT_INITIALIZED','请先生成责任账')
        try:
            old_bill=str(payload.get('replace_master_bill','')).strip()
            if old_bill:
                # 补证：旧拆并关系闭合失效，以零件数墓碑保留记录，不物理删除证据。
                con.execute('INSERT INTO detention_share_changes VALUES(?,?,?,?,?,?) ON CONFLICT(run_id,container,master_bill) DO UPDATE SET pieces=excluded.pieces,note=excluded.note,updated_at=excluded.updated_at',
                            (run_id,payload['container'],old_bill,0,f"被{payload['master_bill']}替换闭合",now))
            con.execute('INSERT INTO detention_share_changes VALUES(?,?,?,?,?,?) ON CONFLICT(run_id,container,master_bill) DO UPDATE SET pieces=excluded.pieces,note=excluded.note,updated_at=excluded.updated_at',
                        (run_id,payload['container'],payload['master_bill'],pieces,str(payload.get('note','补证')),now))
            _refresh(con,run_id,cur,{payload['container']},'SHARE_UPDATE',
                     {'container':payload['container'],'master_bill':payload['master_bill'],'pieces':pieces,
                      'replaced':old_bill or None},note)
        except Exception:
            con.rollback();raise
    return ledger(path,run_id)

def _canon(entry):
    return json.dumps(entry,sort_keys=True,ensure_ascii=False,default=str)

def _refresh(con,run_id,cur,affected,action,extra,note):
    new_entries,policy=_build(con,run_id)
    old={e['container']:e for e in cur['entries']};new={n['container']:n for n in new_entries}
    targets=set(affected)
    for c in set(old)&set(new):
        if _canon(old[c])!=_canon(new[c]): targets.add(c)
    targets={c for c in targets if c in old or c in new}
    kept=[e for c,e in old.items() if c not in targets]
    rebuilt=[n for c,n in new.items() if c in targets]
    generation=cur['generation']+1
    _persist(con,run_id,generation,kept+rebuilt,policy.get('settlement','explainable'),note)
    _evidence(con,run_id,action,{**extra,'generation':generation,'affected':sorted(targets),
                                 'kept':sorted(e['container'] for e in kept)})

def recompute(path,run_id,containers=None,note='箱期或上游拆并关系变化，只重算受影响箱'):
    with connect(path) as con:
        cur=_current(con,run_id)
        if cur is None: raise BusinessError('LEDGER_NOT_INITIALIZED','请先生成责任账')
        new_entries,policy=_build(con,run_id)
        old={e['container']:e for e in cur['entries']};new={n['container']:n for n in new_entries}
        targets=set(old)|set(new) if containers is None else set(containers)
        unknown=targets-set(old)-set(new)
        if unknown: raise BusinessError('UNKNOWN_CONTAINER',f'箱号不存在：{sorted(unknown)}')
        kept=[e for c,e in old.items() if c not in targets]
        rebuilt=[n for c,n in new.items() if c in targets]
        changed=sorted(c for c in targets if c in old and c in new and _canon(old[c])!=_canon(new[c]))
        try:
            _persist(con,run_id,cur['generation']+1,kept+rebuilt,policy.get('settlement','explainable'),note)
            _evidence(con,run_id,'RECOMPUTE',{'generation':cur['generation']+1,'requested':sorted(targets),
                                              'changed':changed,'kept':sorted(e['container'] for e in kept)})
        except Exception:
            con.rollback();raise
    return ledger(path,run_id)

def history(path,run_id):
    with connect(path) as con:
        return [dict(r) for r in con.execute('SELECT generation,active,settlement,created_at,note FROM detention_generations WHERE run_id=? ORDER BY generation',(run_id,))]
