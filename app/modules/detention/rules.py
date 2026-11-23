import csv, io
from decimal import Decimal
from app.config import ROOT
from app.coerce import instant, number, money
from app.errors import BusinessError

SHARE_FIELDS=['container','master_bill','pieces','note']
EVENT_FIELDS=['container','event','event_time','receipt_id','actor','closed']
EVENTS=('GATE_IN','GATE_OUT','PICKUP','RETURN')
CENT=Decimal('0.01')

def _rows(raw, fields, source):
    text=raw.decode('utf-8-sig')
    stream=csv.reader(io.StringIO(text,newline=''),strict=True)
    header=[v.strip() for v in next(stream)]
    if header!=fields: raise BusinessError('BAD_HEADER',f'{source}字段或顺序不匹配')
    out=[]
    for cells in stream:
        if not cells or not any(v.strip() for v in cells): continue
        if len(cells)!=len(fields): raise BusinessError('COLUMN_COUNT',f'{source}列数不一致')
        out.append(dict(zip(fields,[v.strip() for v in cells])))
    return out

def load_baseline(shares_raw, events_raw):
    shares,events=_rows(shares_raw,SHARE_FIELDS,'拆并共用清册'),_rows(events_raw,EVENT_FIELDS,'用箱节点清册')
    for row in shares: row['pieces']=int(number(row['pieces']))
    for row in events:
        if row['event'] not in EVENTS: raise BusinessError('BAD_EVENT',f"未知节点{row['event']}")
        row['time']=instant(row['event_time']); row['is_closed']=row['closed'].lower() in ('yes','y','true','1','闭合')
    return shares,events

def _span_days(start,end):
    if start is None or end is None: return None
    seconds=(end-start).total_seconds()
    if seconds<0: raise BusinessError('BAD_TIME','离场/还箱早于进场/提空')
    return int(seconds//86400)

def _timeline(ev):
    def one(code):
        rows=[r for r in ev if r['event']==code]
        if len(rows)>1: raise BusinessError('DUP_EVENT',f'{code}节点重复')
        return rows[0] if rows else None
    gi,go=one('GATE_IN'),one('GATE_OUT');pu,rt=one('PICKUP'),one('RETURN')
    port=_span_days(gi['time'] if gi else None,go['time'] if go else None)
    carrier=_span_days(pu['time'] if pu else None,rt['time'] if rt else None)
    return {'gate_in':gi,'gate_out':go,'pickup':pu,'return':rt,'port_days':port,'carrier_days':carrier}

def _block_codes(shares, roots, tl, tolerance_hours):
    codes=[]
    if not shares: codes.append('NO_SHARE_RELATION')
    if any(s['master_bill'] not in roots for s in shares): codes.append('RELATION_NOT_CLOSED')
    if any(s['pieces']<=0 for s in shares): codes.append('BAD_SHARE_PIECES')
    if tl['gate_in'] is None or tl['gate_out'] is None: codes.append('PORT_TIMELINE_INCOMPLETE')
    if tl['pickup'] is None: codes.append('PICKUP_RECEIPT_MISSING')
    if tl['return'] is None: codes.append('RETURN_RECEIPT_MISSING')
    elif not tl['return']['is_closed']: codes.append('RETURN_RECEIPT_OPEN')
    if not tl['pickup'] or not tl['pickup'].get('is_closed', True): codes.append('PICKUP_RECEIPT_OPEN')
    if tl['port_days'] is not None and tl['carrier_days'] is not None:
        if abs(tl['port_days']-tl['carrier_days'])*24>tolerance_hours: codes.append('BASIS_UNRECONCILED')
    return codes

def _largest_remainder(total, weights):
    # 费用只能计一次：分摊额合计严格等于箱总额，余数按最大余数法配给。
    raw=[total*Decimal(w)/Decimal(sum(weights)) for w in weights]
    floors=[r.quantize(CENT,rounding='ROUND_FLOOR') for r in raw]
    remainder=(total-sum(floors,Decimal(0)))/CENT
    order=sorted(range(len(raw)),key=lambda i:(raw[i]-floors[i],-i),reverse=True)
    for i in order[:int(remainder)]: floors[i]+=CENT
    return [money(v) for v in floors]

def build_ledger(shares, events, roots, policy):
    free_days=int(policy['free_days']);rate=Decimal(str(policy['daily_rate']));tol=int(policy['basis_tolerance_hours'])
    settlement=policy.get('settlement','explainable')
    containers=sorted({r['container'] for r in shares}|{r['container'] for r in events})
    by_box_share={c:[r for r in shares if r['container']==c] for c in containers}
    by_box_event={c:[r for r in events if r['container']==c] for c in containers}
    entries=[]
    for c in containers:
        sh=by_box_share[c];tl=_timeline(by_box_event[c]);codes=_block_codes(sh,set(roots),tl,tol)
        formal=not codes
        port,car=tl['port_days'],tl['carrier_days']
        if formal:
            # 承运人按提空/还箱回执计时，作为正式起算依据；港口口径保留用于交叉解释。
            basis='carrier';overdue=max(0,car-free_days);fee=money(rate*overdue)
            detail=(f"提空回执{tl['pickup']['event_time']}起算，还箱回执{tl['return']['event_time']}止，"
                    f"承运人计时{car}天；港口进场离场{port}天，两口径差异{abs(car-port)}天在{tol//24}日容差内可互相解释")
            weights=[s['pieces'] for s in sh];splits=_largest_remainder(Decimal(fee),weights)
            bills=[{'master_bill':s['master_bill'],'share_pieces':s['pieces'],'bill_fee':splits[i]} for i,s in enumerate(sh)]
            split_sum=money(sum((Decimal(b['bill_fee']) for b in bills),Decimal(0)))
            assert split_sum==fee
        else:
            basis='pending';overdue=None;fee=money(0)
            detail='；'.join(codes)+'，承运人回执或上游拆并关系未闭合，暂不写成正式责任'
            bills=[{'master_bill':s['master_bill'],'share_pieces':s['pieces'],'bill_fee':money(0)} for s in sh]
        entries.append({'container':c,'entry_status':'正式责任' if formal else '争议暂记','block_codes':codes,
            'basis':basis,'basis_detail':detail,'port_days':port,'carrier_days':car,'free_days':free_days,
            'overdue_days':overdue,'container_fee':fee,'sealable':formal,'allocations':bills,
            'receipts':[r['receipt_id'] for r in by_box_event[c] if r['receipt_id']],
            'return_closed':bool(tl['return'] and tl['return']['is_closed'])})
    if settlement=='whole_container':
        disputed={e['container'] for e in entries if e['block_codes']}
        if disputed:
            for e in entries:
                if e['entry_status']=='正式责任':
                    e['entry_status']='争议暂记';e['sealable']=False;e['container_fee']=money(0)
                    e['basis']='pending';e['block_codes']=sorted(set(e['block_codes']+['WAIT_WHOLE_CONTAINER']))
                    e['basis_detail']='同批次存在未闭合争议箱，按整柜等待策略暂不定责；'+e['basis_detail']
                    for b in e['allocations']: b['bill_fee']=money(0)
                elif 'WAIT_WHOLE_CONTAINER' not in e['block_codes']:
                    e['block_codes']=sorted(set(e['block_codes']+['WAIT_WHOLE_CONTAINER']))
    return entries

def summarize(entries):
    formal=[e for e in entries if e['entry_status']=='正式责任']
    return {'containers':len(entries),'formal':len(formal),'disputed':len(entries)-len(formal),
            'formal_fee':money(sum((Decimal(e['container_fee']) for e in formal),Decimal(0))),
            'sealable_boxes':sum(1 for e in entries if e['sealable'])}

def apply(state):
    cfg=state['context']['detention']
    if cfg.get('settlement','explainable') not in ('explainable','whole_container'):
        raise BusinessError('BAD_SETTLEMENT','结算策略仅支持 explainable/whole_container')
    shares_raw=state.pop('detention_shares_raw',None) or (ROOT/'sample_data'/cfg['shares_file']).read_bytes()
    events_raw=state.pop('detention_events_raw',None) or (ROOT/'sample_data'/cfg['events_file']).read_bytes()
    shares,events=load_baseline(shares_raw,events_raw)
    entries=build_ledger(shares,events,state['graph']['roots'],cfg)
    state['detention_baseline']={'shares_raw_b64':_b64(shares_raw),'events_raw_b64':_b64(events_raw),
        'shares':shares,'events':[{k:v for k,v in r.items() if k!='time'} for r in events]}
    state['detention']={'entries':entries,'summary':summarize(entries),'settlement':cfg.get('settlement','explainable')}
    state['views']['detention']={'title':'超期用箱责任账','rows':entries,'metrics':{
        '箱数':len(entries),'正式责任':state['detention']['summary']['formal'],
        '争议暂记':state['detention']['summary']['disputed'],'正式金额':state['detention']['summary']['formal_fee']}}

def _b64(raw):
    import base64
    return base64.b64encode(raw).decode()
