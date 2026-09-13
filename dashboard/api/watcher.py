import hashlib
import json
import threading
import time
from pathlib import Path

from pydantic import TypeAdapter, ValidationError
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .schemas import Batch


ADAPTER = TypeAdapter(Batch)
MAX_FILE_BYTES = 16 * 1024 * 1024


class Watcher(FileSystemEventHandler):
    """Native events wake one ingestion worker; periodic scans recover missed events."""
    def __init__(self, store):
        self.store = store
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.observer = Observer()
        self.thread = threading.Thread(target=self.run, daemon=True, name='aki-file-ingestion')
        self.seen = {}
        self.failed = {}
        self.errors = []
        self.status_lock = threading.Lock()

    def on_any_event(self, event):
        if not event.is_directory:
            self.wake.set()

    def start(self):
        self.observer.schedule(self, str(self.store.root / 'inbox'), recursive=True)
        self.observer.start()
        self.thread.start()

    def close(self):
        self.stop.set(); self.wake.set()
        self.observer.stop(); self.observer.join(timeout=5); self.thread.join(timeout=5)

    def scan(self):
        errors = []
        for folder in ('input', 'prediction'):
            for path in (self.store.root / 'inbox' / folder).glob('*.json'):
                try:
                    stat = path.stat()
                    if stat.st_size > MAX_FILE_BYTES:
                        raise ValueError('文件超过 16 MB；请拆分批次')
                    signature = (stat.st_mtime_ns, stat.st_size)
                    # Wait for a stable file. Producers should always rename .tmp -> .json atomically.
                    if time.time() - stat.st_mtime < 0.25:
                        continue
                    raw = path.read_bytes()
                    if (path.stat().st_mtime_ns, path.stat().st_size) != signature:
                        continue
                    digest = hashlib.sha256(raw).hexdigest()
                    if self.seen.get(str(path)) == digest:
                        continue
                    value = ADAPTER.validate_json(raw)
                    if value.kind != folder:
                        raise ValueError('文件 kind 与输入／输出目录不匹配')
                    self.store.ingest(value, source='file')
                    self.seen[str(path)] = digest
                    self.failed.pop(str(path), None)
                except (ValueError, OSError, ValidationError) as error:
                    # Never echo raw field values, patient data or filesystem paths into logs/status.
                    message = 'JSON 结构或字段不符合契约' if isinstance(error, ValidationError) else str(error) if isinstance(error, ValueError) else '文件暂不可读'
                    errors.append({'file': path.name, 'message': message})
        with self.status_lock:
            self.errors = errors[:20]
        self.store.flush_outbox()

    def run(self):
        while not self.stop.is_set():
            try:
                self.scan()
            except Exception:
                with self.status_lock:
                    self.errors = [{'file': '', 'message': '导入服务遇到错误，将自动重试；请检查磁盘空间与本地文件权限'}]
            self.wake.wait(1.0)
            self.wake.clear()
            # Short debounce coalesces bursts and catches files still settling.
            self.stop.wait(0.3)

    def status(self):
        with self.status_lock:
            return {'running': self.thread.is_alive(), 'errors': list(self.errors)}
