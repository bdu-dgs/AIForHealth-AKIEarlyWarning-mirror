"""Local source launcher: one backend process serves the compiled frontend."""
import argparse
import json
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

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


def main():
    parser = argparse.ArgumentParser(description='AKI local workbench')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--data-dir', default=None)
    args = parser.parse_args()
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
    print('AKI local workbench: ' + url)
    print('Course project. Model is not configured. Press Ctrl+C to stop.')
    uvicorn.run(create_app(args.data_dir), host='127.0.0.1', port=args.port, access_log=False,
                loop='asyncio', http='h11', ws='none')
    return 0


if __name__ == '__main__':
    sys.exit(main())
