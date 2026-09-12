"""Local source launcher: one backend process serves the compiled frontend."""
import argparse
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

import uvicorn
from dashboard.api.main import create_app


def main():
    parser = argparse.ArgumentParser(description='AKI local workbench')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--data-dir', default=None)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('port must be between 1024 and 65535')
    url = f'http://127.0.0.1:{args.port}'
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', args.port))
        except OSError:
            print(f'Port {args.port} is occupied. Close the existing AKI server or choose --port.')
            return 1
    def open_when_ready():
        for _ in range(50):
            try:
                with urllib.request.urlopen(url + '/api/health', timeout=1) as response:
                    if response.status == 200:
                        webbrowser.open(url)
                        return
            except OSError:
                time.sleep(.2)
    if not args.no_browser:
        threading.Thread(target=open_when_ready, daemon=True).start()
    print('AKI local workbench: ' + url)
    print('Course project. Model is not configured. Press Ctrl+C to stop.')
    uvicorn.run(create_app(args.data_dir), host='127.0.0.1', port=args.port, access_log=False,
                loop='asyncio', http='h11', ws='none')
    return 0


if __name__ == '__main__':
    sys.exit(main())
