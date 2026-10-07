import asyncio
import csv
import io
import json
import os
import time
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

from .schemas import Batch, InputBatch, ModelHeartbeat, now, set_clock, stamp
from .demo import DemoPlayback
from .storage import Store
from .watcher import Watcher
from .exchange import pack, unpack, volumes
from .dataset_preview import load_preview
from pydantic import ValidationError


BASE = Path(__file__).resolve().parents[2]
Image.MAX_IMAGE_PIXELS = 20_000_000


MODEL_HEARTBEAT_SECONDS = 20


def create_app(data_root=None, watch=True, dataset_root=None, demo_batch=None, demo_start=None):
    """demo_batch (InputBatch dict) + demo_start (aware datetime) load demo playback into an empty data directory."""
    # No filesystem mutation at module import; tests and servers own their lifecycle.
    @asynccontextmanager
    async def lifespan(app):
        default_root = Path(os.environ.get('LOCALAPPDATA', str(BASE))) / 'AKIWorkbench' / 'data'
        app.state.store = Store(data_root or os.environ.get('AKI_DATA_DIR', str(default_root)))
        app.state.demo = DemoPlayback(app.state.store)
        if demo_batch is not None and not app.state.demo.active:
            app.state.demo.load(InputBatch(**demo_batch), demo_start)
        app.state.model = None
        app.state.watcher = Watcher(app.state.store)
        if watch:
            app.state.watcher.start()
        try:
            yield
        finally:
            if watch:
                app.state.watcher.close()
            set_clock(None)

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
                return JSONResponse({'detail': 'Write requests from other websites are not allowed'}, status_code=403)
            # Enforce actual bytes, including chunked requests without Content-Length.
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 17 * 1024 * 1024:
                    return JSONResponse({'detail': 'Request exceeds 17 MB; split it into smaller batches'}, status_code=413)
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
        return JSONResponse({'detail': 'Patient not found'}, status_code=404)

    def model_status():
        beat = app.state.model
        if beat is None:
            return 'not_configured'
        if time.monotonic() - beat['seen'] > MODEL_HEARTBEAT_SECONDS:
            return 'offline'
        return 'error' if beat['status'] == 'error' else 'running'

    def model_info():
        beat = app.state.model
        return None if beat is None else {k: v for k, v in beat.items() if k != 'seen'}

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'revision': app.state.store.revision, 'model_status': model_status(),
                'model': model_info(), 'clock': app.state.demo.status(),
                'watcher': app.state.watcher.status(), 'file_writes_pending': app.state.store.outbox_pending(),
                'server_time': now()}

    @app.post('/api/model/heartbeat')
    def model_heartbeat(beat: ModelHeartbeat):
        # Sent by the local model worker; wall-clock age (not the demo clock) decides whether it is alive.
        app.state.model = {**beat.model_dump(), 'seen': time.monotonic()}
        return {'model_status': model_status()}

    @app.get('/api/clock')
    def clock():
        return app.state.demo.status()

    @app.post('/api/demo/advance')
    async def demo_advance(hours: int = Query(1, ge=1, le=24)):
        if not app.state.demo.active:
            raise HTTPException(409, 'Demo playback is not active; start the website with --demo')
        result = await asyncio.to_thread(app.state.demo.advance, hours)
        app.state.watcher.wake.set()
        return result

    @app.get('/api/settings')
    def settings():
        return {'data_directory': str(app.state.store.root), 'input_directory': str(app.state.store.root / 'inbox/input'),
                'prediction_directory': str(app.state.store.root / 'inbox/prediction'),
                'model_status': model_status(), 'max_import_mb': 16,
                'research': {'cutoff_stability': 'not_evaluated', 'validation_threshold': 'not_selected'}}

    @app.get('/api/patients')
    def patients():
        return app.state.store.patients()

    @app.get('/api/datasets/icu-preview')
    def dataset_preview():
        try:
            return load_preview(dataset_root if dataset_root is not None else BASE / 'icu_pre_admission_data')
        except (OSError, UnicodeError, csv.Error):
            raise HTTPException(422, 'Data files cannot be read right now; check that the files are complete and refresh later') from None

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
            raise HTTPException(413, 'File exceeds 16 MB; split it into smaller files')
        try:
            batches = unpack(raw, file.filename or '')
        except ValidationError:
            raise ValueError('File structure does not match the data contract; check fields, times, and values')
        completed = 0; inserted = 0; changed = 0
        # Each batch is transactional; an interrupted ZIP can be retried idempotently.
        for batch in batches:
            try:
                result = await asyncio.to_thread(app.state.store.ingest, batch)
            except ValueError as error:
                raise ValueError(f'Imported {completed} batch(es); the current batch failed: {error}. After correcting it, re-import the whole file')
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
            raise ValueError('End time must be later than start time')
        return app.state.store.history(patient_id, start, end, known, kind, offset, limit)

    @app.get('/api/patients/{patient_id}/quality')
    def quality(patient_id: str, as_of: str | None = None):
        return app.state.store.quality(patient_id, min(stamp(as_of), now()) if as_of else now())

    @app.get('/api/patients/{patient_id}/snapshot')
    def snapshot(patient_id: str, as_of: str | None = None, cutoff: str | None = None):
        known = min(stamp(as_of), now()) if as_of else now()
        end = min(stamp(cutoff), known) if cutoff else known
        return {'input_fingerprint': app.state.store.input_snapshot(patient_id, known, end), 'as_of': known, 'data_cutoff': end}

    @app.get('/api/patients/{patient_id}/revisions')
    def revisions(patient_id: str):
        return app.state.store.revisions(patient_id)

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
            raise HTTPException(409, 'Data changed during export; regenerate the export list')
        parts = volumes(app.state.store.export(patient_id, kind))
        if part > len(parts):
            raise HTTPException(404, 'Export part not found')
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
            raise HTTPException(413, 'Photo cannot exceed 5 MB')
        try:
            with Image.open(io.BytesIO(raw)) as source:
                if source.format not in ('JPEG', 'PNG', 'WEBP'):
                    raise ValueError('Use a JPEG, PNG, or WebP photo')
                source.load()
                picture = source.convert('RGB'); picture.thumbnail((512, 512))
                name = uuid.uuid4().hex + '.jpg'
                picture.save(app.state.store.root / 'photos' / name, 'JPEG', quality=90)
        except (UnidentifiedImageError, Image.DecompressionBombError, OSError):
            raise ValueError('Cannot read the photo; use a valid image of reasonable size')
        app.state.store.set_photo(patient_id, name)
        return {'photo': name}

    @app.get('/api/patients/{patient_id}/photo')
    def photo(patient_id: str):
        item = app.state.store.patient(patient_id)
        if not item['photo']:
            raise HTTPException(404, 'No photo')
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
            raise HTTPException(404, 'Endpoint not found')
        if not (web / 'index.html').exists():
            raise HTTPException(503, 'Frontend has not been built; run Setup-AKI.cmd')
        if path == 'favicon.svg' and (web / path).exists():
            return FileResponse(web / path)
        return FileResponse(web / 'index.html')

    return app


app = create_app()
