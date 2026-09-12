import asyncio
import csv
import io
import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from PIL import Image, UnidentifiedImageError

from .schemas import Batch, now, stamp
from .storage import Store
from .watcher import Watcher
from .exchange import pack, unpack, volumes
from .dataset_preview import load_preview
from pydantic import ValidationError


BASE = Path(__file__).resolve().parents[2]
Image.MAX_IMAGE_PIXELS = 20_000_000


def create_app(data_root=None, watch=True, dataset_root=None):
    # No filesystem mutation at module import; tests and servers own their lifecycle.
    @asynccontextmanager
    async def lifespan(app):
        default_root = Path(os.environ.get('LOCALAPPDATA', str(BASE))) / 'AKIWorkbench' / 'data'
        app.state.store = Store(data_root or os.environ.get('AKI_DATA_DIR', str(default_root)))
        app.state.watcher = Watcher(app.state.store)
        if watch:
            app.state.watcher.start()
        try:
            yield
        finally:
            if watch:
                app.state.watcher.close()

    app = FastAPI(title='AKI Local Workbench', version='0.1.0', lifespan=lifespan,
                  docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])

    @app.middleware('http')
    async def local_requests(request: Request, call_next):
        # Prevent another website from sending writes to the local service.
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('origin')
            same_origin = str(request.base_url).rstrip('/')
            if origin and origin not in (same_origin, 'http://127.0.0.1:5173', 'http://localhost:5173'):
                return JSONResponse({'detail': '不允许来自其他网站的写入请求'}, status_code=403)
            # Enforce actual bytes, including chunked requests without Content-Length.
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 17 * 1024 * 1024:
                    return JSONResponse({'detail': '请求超过 17 MB；请拆分批次'}, status_code=413)
            request._body = bytes(body)
        response = await call_next(request)
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        errors = [{'field': '.'.join(str(x) for x in e['loc']), 'message': e['msg']} for e in exc.errors()]
        return JSONResponse({'detail': errors}, status_code=422)

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=422)

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({'detail': '患者不存在'}, status_code=404)

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'revision': app.state.store.revision, 'model_status': 'not_configured',
                'watcher': app.state.watcher.status(), 'file_writes_pending': app.state.store.outbox_pending(),
                'server_time': now()}

    @app.get('/api/settings')
    def settings():
        return {'data_directory': str(app.state.store.root), 'input_directory': str(app.state.store.root / 'inbox/input'),
                'prediction_directory': str(app.state.store.root / 'inbox/prediction'),
                'model_status': 'not_configured', 'max_import_mb': 16,
                'research': {'cutoff_stability': 'not_evaluated', 'validation_threshold': 'not_selected'}}

    @app.get('/api/patients')
    def patients():
        return app.state.store.patients()

    @app.get('/api/datasets/icu-preview')
    def dataset_preview():
        try:
            return load_preview(dataset_root if dataset_root is not None else BASE / 'icu_pre_admission_data')
        except (OSError, UnicodeError, csv.Error):
            raise HTTPException(422, '数据文件暂时无法读取，请检查文件是否完整并稍后刷新') from None

    @app.get('/api/patients/{patient_id}')
    def patient(patient_id: str):
        return app.state.store.patient(patient_id)

    @app.post('/api/import')
    def ingest(batch: Batch):
        result = app.state.store.ingest(batch)
        app.state.watcher.wake.set()
        return result

    @app.post('/api/import-file')
    async def import_file(file: UploadFile = File()):
        raw = await file.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
            raise HTTPException(413, '文件超过 16 MB，请拆分')
        try:
            batches = unpack(raw, file.filename or '')
        except ValidationError:
            raise ValueError('文件结构不符合数据契约；请检查字段、时间和数值')
        completed = 0; inserted = 0; changed = 0
        # Each batch is transactional; an interrupted ZIP can be retried idempotently.
        for batch in batches:
            try:
                result = await asyncio.to_thread(app.state.store.ingest, batch)
            except ValueError as error:
                raise ValueError(f'已成功导入 {completed} 个批次；当前批次失败：{error}。更正后可重新导入整个文件。')
            completed += 1; inserted += result['inserted']; changed += result['patients_changed']
        return {'inserted': inserted, 'patients_changed': changed, 'batches': completed}

    @app.get('/api/patients/{patient_id}/history')
    def history(patient_id: str, start: str = '1970-01-01T00:00:00+00:00', end: str | None = None,
                as_of: str | None = None, kind: Literal['observation', 'prediction'] = 'observation',
                offset: int = Query(0, ge=0), limit: int = Query(3000, ge=1, le=10000)):
        moment = now()
        known = min(stamp(as_of), moment) if as_of else moment
        start = stamp(start); end = stamp(end) if end else known
        if end < start:
            raise ValueError('结束时间必须晚于开始时间')
        return app.state.store.history(patient_id, start, end, known, kind, offset, limit)

    @app.get('/api/patients/{patient_id}/quality')
    def quality(patient_id: str, as_of: str | None = None):
        return app.state.store.quality(patient_id, min(stamp(as_of), now()) if as_of else now())

    @app.get('/api/patients/{patient_id}/snapshot')
    def snapshot(patient_id: str, as_of: str | None = None, cutoff: str | None = None):
        known = min(stamp(as_of), now()) if as_of else now()
        end = min(stamp(cutoff), known) if cutoff else known
        return {'input_fingerprint': app.state.store.input_snapshot(patient_id, known, end), 'as_of': known, 'data_cutoff': end}

    @app.get('/api/patients/{patient_id}/current-predictions')
    def current_predictions(patient_id: str, as_of: str | None = None):
        known = min(stamp(as_of), now()) if as_of else now()
        return app.state.store.current_predictions(patient_id, known)

    @app.get('/api/patients/{patient_id}/export-plan/{kind}')
    def export_plan(patient_id: str, kind: Literal['input', 'prediction']):
        revision = app.state.store.revision
        parts = volumes(app.state.store.export(patient_id, kind))
        return {'parts': len(parts), 'revision': revision}

    @app.get('/api/patients/{patient_id}/export/{kind}')
    def export(patient_id: str, kind: Literal['input', 'prediction'], part: int = Query(1, ge=1), revision: int | None = None):
        if revision is not None and revision != app.state.store.revision:
            raise HTTPException(409, '导出期间数据发生变化，请重新生成导出列表')
        parts = volumes(app.state.store.export(patient_id, kind))
        if part > len(parts):
            raise HTTPException(404, '导出分卷不存在')
        value = parts[part - 1]
        suffix = f'-part-{part}-of-{len(parts)}'
        key = 'observations' if kind == 'input' else 'predictions'
        if len(value[key]) > 10000 or len(json.dumps(value).encode()) > 14 * 1024 * 1024:
            return Response(pack(value), media_type='application/zip',
                            headers={'Content-Disposition': f'attachment; filename="{patient_id}-{kind}{suffix}.zip"'})
        # ID was looked up before forming this header; accepted IDs contain only safe characters.
        return JSONResponse(value, headers={'Content-Disposition': f'attachment; filename="{patient_id}-{kind}{suffix}.json"',
                                            'X-AKI-Export-Parts': str(len(parts))})

    @app.post('/api/patients/{patient_id}/photo')
    async def upload_photo(patient_id: str, photo: UploadFile = File()):
        app.state.store.patient(patient_id)
        raw = await photo.read(5 * 1024 * 1024 + 1)
        if len(raw) > 5 * 1024 * 1024:
            raise HTTPException(413, '照片不能超过 5 MB')
        try:
            with Image.open(io.BytesIO(raw)) as source:
                if source.format not in ('JPEG', 'PNG', 'WEBP'):
                    raise ValueError('请使用 JPEG、PNG 或 WebP 照片')
                source.load()
                picture = source.convert('RGB'); picture.thumbnail((512, 512))
                name = uuid.uuid4().hex + '.jpg'
                picture.save(app.state.store.root / 'photos' / name, 'JPEG', quality=90)
        except (UnidentifiedImageError, Image.DecompressionBombError, OSError):
            raise ValueError('无法读取照片，请使用有效且尺寸适当的图片')
        app.state.store.set_photo(patient_id, name)
        return {'photo': name}

    @app.get('/api/patients/{patient_id}/photo')
    def photo(patient_id: str):
        item = app.state.store.patient(patient_id)
        if not item['photo']:
            raise HTTPException(404, '没有照片')
        return FileResponse(app.state.store.root / 'photos' / item['photo'])

    @app.get('/api/events')
    async def events(request: Request):
        async def stream():
            last = -1
            tick = 0
            while not await request.is_disconnected():
                revision = await asyncio.to_thread(lambda: app.state.store.revision)
                if revision != last:
                    yield f'id: {revision}\ndata: {json.dumps({"revision": revision})}\n\n'
                    last = revision
                elif tick % 30 == 0:
                    yield ': heartbeat\n\n'
                tick += 1
                await asyncio.sleep(0.5)
        return StreamingResponse(stream(), media_type='text/event-stream', headers={'X-Accel-Buffering': 'no'})

    web = BASE / 'dashboard/web/dist'
    if (web / 'assets').exists():
        app.mount('/assets', StaticFiles(directory=web / 'assets'), name='assets')

    @app.get('/{path:path}')
    def spa(path: str):
        if path.startswith('api/'):
            raise HTTPException(404, '接口不存在')
        if not (web / 'index.html').exists():
            raise HTTPException(503, '前端尚未构建，请运行 Setup-AKI.cmd')
        if path == 'favicon.svg' and (web / path).exists():
            return FileResponse(web / path)
        return FileResponse(web / 'index.html')

    return app


app = create_app()
