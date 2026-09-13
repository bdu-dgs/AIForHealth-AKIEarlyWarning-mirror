import os
import secrets
import threading
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from backend.models.config import Config
from backend.services.safety import install_network_guard,validate_paths
from backend.services.io import TABLES,find_table,metadata,inspect,small_table
from backend.services.pipeline import run

install_network_guard()
app=FastAPI(title='AKI local processing',docs_url=None,redoc_url=None,openapi_url=None)
TOKEN=os.environ.get('AKI_API_TOKEN') or secrets.token_urlsafe(32)
state={'status':'idle','progress':{},'report':None,'error':None}
lock=threading.Lock()

@app.middleware('http')
async def local_only(request:Request,call_next):
    host=request.headers.get('host','').split(':')[0]
    origin=request.headers.get('origin')
    if host not in ('127.0.0.1','localhost','testserver') or request.client.host not in ('127.0.0.1','::1','testclient'):
        return JSONResponse({'detail':'Local access only'},403)
    if origin and origin not in ('http://127.0.0.1:8501','http://localhost:8501'):
        return JSONResponse({'detail':'Origin rejected'},403)
    if request.url.path!='/health' and not secrets.compare_digest(request.headers.get('x-aki-token',''),TOKEN):
        return JSONResponse({'detail':'Authentication required'},403)
    try: return await call_next(request)
    except Exception: return JSONResponse({'detail':'Processing error; inspect configuration and source schema locally.'},500)

@app.exception_handler(ValueError)
async def validation_error(request,exc):
    return JSONResponse({'detail':str(exc)},400)

@app.exception_handler(MemoryError)
async def memory_error(request,exc):
    return JSONResponse({'detail':'Memory limit reached; reduce chunk size or selected variables.'},400)

@app.get('/health')
def health(): return {'status':'ok','version':'1.0.0'}

@app.post('/setup')
def setup(c:Config):
    raw,_,_=validate_paths(c); result=[]
    for name in TABLES:
        p=find_table(raw,name,True)
        result.append({'table':name,'found':p is not None,**({'bytes':p.stat().st_size,**metadata(p)} if p else {})})
    return result

@app.post('/inspect/{table}')
def inspection(table:str,c:Config):
    if table not in TABLES: raise HTTPException(400,'Unknown table')
    raw,_,_=validate_paths(c)
    return inspect(find_table(raw,table),c.chunk_size)

@app.post('/variables')
def variables(c:Config):
    raw,_,_=validate_paths(c); result=[]
    for source,table in [('CHARTEVENTS','D_ITEMS'),('LABEVENTS','D_LABITEMS')]:
        d=small_table(raw,table,c.chunk_size)
        for row in d.to_dict('records'):
            result.append({'ITEMID':int(row['ITEMID']),'label':str(row.get('LABEL','')),'source':source,'unit':str(row.get('UNITNAME','') or '')})
    return result

@app.post('/pipeline')
def start(c:Config):
    validate_paths(c)
    with lock:
        if state['status']=='running': raise HTTPException(409,'A pipeline is already running.')
        state.update(status='running',progress={},report=None,error=None)
    def worker():
        try:
            report=run(c,lambda p:state.update(progress=p))
            state.update(status='complete',report=report)
        except ValueError as exc:
            state.update(status='failed',error=str(exc))
        except MemoryError:
            state.update(status='failed',error='Memory limit reached. Reduce selection or chunk size.')
        except Exception as exc:
            state.update(status='failed',error='Processing failed: '+type(exc).__name__+'. Check required columns, permissions and local file formats.')
    threading.Thread(target=worker,daemon=True).start()
    return {'status':'started'}

@app.get('/status')
def status(): return state
