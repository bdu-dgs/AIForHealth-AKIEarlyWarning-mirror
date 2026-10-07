"""Local source launcher: one backend process serves the compiled frontend."""
import argparse
import importlib.util
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

import uvicorn
from dashboard.api.main import create_app


def is_workbench_ready(url):
    try:
        with urllib.request.urlopen(url + '/api/health', timeout=1) as response:
            health = json.load(response)
        with urllib.request.urlopen(url + '/openapi.json', timeout=1) as response:
            schema = json.load(response)
        return (health.get('status') == 'ok'
                and schema.get('info', {}).get('title') == 'AKI Local Workbench')
    except (OSError, ValueError, AttributeError):
        return False


def open_browser(url):
    try:
        if sys.platform == 'win32':
            os.startfile(url)  # Use the user's Windows default HTTP browser.
        elif not webbrowser.open(url):
            raise OSError('No browser accepted the URL')
    except OSError:
        print('Could not open a browser automatically. Open this address: ' + url, flush=True)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_HOURS_BEFORE_NOW = 72     # demo clock starts 72 h in the past, so it never runs ahead of real time


def demo_batch(path):
    """InputBatch for demo playback: a JSON file, or the built-in synthetic patients when no file is given."""
    if path:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    spec = importlib.util.spec_from_file_location('synthetic', REPO_ROOT / 'scripts' / 'make_synthetic_demo_patients.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    import numpy as np
    return module.build(module.SCENARIOS, np.random.default_rng(module.SEED))


def start_model_worker(url):
    """The model runs in its own process; the website keeps working (without risk results) if it cannot start."""
    try:
        return subprocess.Popen([sys.executable, '-m', 'dashboard.model_worker', '--api', url], cwd=REPO_ROOT)
    except OSError as error:
        print(f'Model worker could not start: {error}', flush=True)
        return None


def main():
    parser = argparse.ArgumentParser(description='AKI local workbench')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--data-dir', default=None)
    parser.add_argument('--demo', nargs='?', const='', default=None, metavar='FILE',
                        help='demo playback with a stepped clock; FILE is an InputBatch JSON (default: synthetic patients)')
    parser.add_argument('--no-model', action='store_true', help='do not start the model worker')
    args = parser.parse_args()
    demo, demo_start = None, None
    if args.demo is not None:
        demo = demo_batch(args.demo)
        demo_start = (datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
                      - timedelta(hours=DEMO_HOURS_BEFORE_NOW))
        if not args.data_dir:      # every demo run gets its own fresh data directory
            root = Path(os.environ.get('LOCALAPPDATA', str(REPO_ROOT))) / 'AKIWorkbench' / 'demo-runs'
            args.data_dir = str(root / datetime.now().strftime('%Y%m%d-%H%M%S'))
        print('Demo playback data directory: ' + args.data_dir, flush=True)
    if not 1024 <= args.port <= 65535:
        parser.error('port must be between 1024 and 65535')
    url = f'http://127.0.0.1:{args.port}'
    print('Website: ' + url + '/', flush=True)
    print('If the browser does not open, click or copy the address above.', flush=True)
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', args.port))
        except OSError:
            if not args.data_dir and is_workbench_ready(url):
                print('AKI is already running; opening the existing website.', flush=True)
                if not args.no_browser:
                    open_browser(url)
                return 0
            print(f'Port {args.port} is occupied. Close the existing server or choose --port.', flush=True)
            return 1
    def open_when_ready():
        for _ in range(50):
            if is_workbench_ready(url):
                open_browser(url)
                return
            time.sleep(.2)
        print('Automatic browser opening timed out. Once ready, open: ' + url, flush=True)
    if not args.no_browser:
        threading.Thread(target=open_when_ready, daemon=True).start()
    worker = None if args.no_model else start_model_worker(url)
    print('AKI local workbench: ' + url)
    print('Course project; not for clinical use. Press Ctrl+C to stop.')
    try:
        uvicorn.run(create_app(args.data_dir, demo_batch=demo, demo_start=demo_start), host='127.0.0.1',
                    port=args.port, access_log=False, loop='asyncio', http='h11', ws='none')
    finally:
        if worker is not None:
            worker.terminate()
    return 0


if __name__ == '__main__':
    sys.exit(main())
