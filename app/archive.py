import io, csv, json, zipfile, hashlib
from app.errors import BusinessError

def serialize(result, ledger=None):
    if not result.get('manifest') or not result.get('views'):
        raise BusinessError('INCOMPLETE_RUN','批次尚未完成')
    evidence={'counts':result['counts'],'views':result['views'],'quarantine':result['quarantine']}
    files={}
    if result.get('detention'):
        # 只带可封存的正式责任箱；争议暂记不进封存件。
        formal=[e for e in result['detention']['entries'] if e['sealable']]
        files['detention_ledger.json']=json.dumps(
            {'settlement':result['detention'].get('settlement'),'summary':result['detention']['summary'],
             'sealable_entries':formal,'excluded_disputed':[e['container'] for e in result['detention']['entries'] if not e['sealable']]},
            ensure_ascii=False,indent=2)
    if ledger is not None:
        files['detention_ledger_official.json']=json.dumps(
            {k:v for k,v in ledger.items() if k!='evidence'},ensure_ascii=False,indent=2)
        files['detention_evidence.json']=json.dumps(ledger.get('evidence',[]),ensure_ascii=False,indent=2)
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json',json.dumps({**result['manifest'],'revision':result.get('revision',1),'status':result.get('status','待复核')},ensure_ascii=False,indent=2))
        archive.writestr('evidence.json',json.dumps(evidence,ensure_ascii=False,indent=2))
        for name,content in files.items(): archive.writestr(name,content)
    return buffer.getvalue()
