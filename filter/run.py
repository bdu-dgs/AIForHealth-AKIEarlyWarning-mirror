"""One command, two loopback-only services. No patient data or config written here."""
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT=Path(__file__).resolve().parent

def main():
    python=ROOT/'.venv'/'Scripts'/'python.exe' if os.name=='nt' else ROOT/'.venv'/'bin'/'python'
    if not python.exists(): python=Path(sys.executable)
    for port in (8000,8501):
        with socket.socket() as s:
            if s.connect_ex(('127.0.0.1',port))==0:
                raise SystemExit(f'Port {port} is in use. Stop the existing service first; no process was killed.')
    env=os.environ.copy(); env.update(AKI_API_TOKEN=secrets.token_urlsafe(32),STREAMLIT_BROWSER_GATHER_USAGE_STATS='false',PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1')
    children=[]
    try:
        children.append(subprocess.Popen([str(python),'-m','uvicorn','backend.main:app','--host','127.0.0.1','--port','8000','--no-access-log','--log-level','critical'],cwd=ROOT,env=env))
        children.append(subprocess.Popen([str(python),'-m','streamlit','run','frontend/app.py','--server.address','127.0.0.1','--server.port','8501','--browser.gatherUsageStats','false'],cwd=ROOT,env=env))
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(120):
            if any(p.poll() is not None for p in children): raise RuntimeError('A local service exited during startup.')
            try:
                with opener.open('http://127.0.0.1:8501/_stcore/health',timeout=1) as r: ready=r.status==200
                with opener.open('http://127.0.0.1:8000/health',timeout=1) as r: ready=ready and r.status==200
                if ready: break
            except OSError: pass
            time.sleep(.5)
        else: raise RuntimeError('Local services did not become ready.')
        print('AKI system ready: http://127.0.0.1:8501 — Ctrl+C stops both services.')
        if '--no-browser' not in sys.argv: webbrowser.open('http://127.0.0.1:8501')
        while all(p.poll() is None for p in children): time.sleep(.5)
    except KeyboardInterrupt: pass
    finally:
        for p in children:
            if p.poll() is None: p.terminate()
        for p in children:
            try: p.wait(timeout=5)
            except subprocess.TimeoutExpired: p.kill()

if __name__=='__main__': main()
