from fastapi import APIRouter, Request, Body
from app.engine import evaluate
from app.web import render
from app import detention_store
from app.errors import BusinessError

router=APIRouter()

def _db():
    from app.main import DB_PATH
    return DB_PATH

@router.get('/detention')
def page(request:Request):
    return render(request,'detention')

@router.post('/api/detention')
def calculate(payload:dict=Body(default={})):
    # 纯计算视图：按当前输入快照给出责任账草案，不落正式账。
    return evaluate(payload,until=8)

@router.post('/api/detention/{run_id}/ledger')
def create_ledger(run_id:str):
    return detention_store.initialize(_db(),run_id)

@router.get('/api/detention/{run_id}/ledger')
def get_ledger(run_id:str):
    return detention_store.ledger(_db(),run_id)

@router.get('/api/detention/{run_id}/generations')
def get_generations(run_id:str):
    return {'run_id':run_id,'generations':detention_store.history(_db(),run_id)}

@router.post('/api/detention/{run_id}/receipts')
def add_receipt(run_id:str,payload:dict=Body(...)):
    return detention_store.add_receipt(_db(),run_id,payload)

@router.post('/api/detention/{run_id}/shares')
def change_share(run_id:str,payload:dict=Body(...)):
    return detention_store.change_share(_db(),run_id,payload)

@router.post('/api/detention/{run_id}/recompute')
def recompute(run_id:str,payload:dict=Body(default={})):
    containers=payload.get('containers')
    if containers is not None and not isinstance(containers,list):
        raise BusinessError('BAD_CONTAINERS','containers须为箱号数组或省略')
    return detention_store.recompute(_db(),run_id,containers)
