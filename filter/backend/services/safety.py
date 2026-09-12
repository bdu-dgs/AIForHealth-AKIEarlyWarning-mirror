import ipaddress
import os
import socket
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
CLOUD = ('onedrive','dropbox','google drive','googledrive','icloud','box sync','box drive')

def safe_path(value):
    p = Path(value).expanduser()
    if not p.is_absolute() or str(p).startswith(('\\\\','//')):
        raise ValueError('Use an absolute local disk path, not a network share.')
    p = p.resolve()
    if any(x in str(p).lower() for x in CLOUD):
        raise ValueError('WARNING: cloud-synchronized data paths are blocked. Choose a non-synchronized local directory.')
    for env in ('OneDrive','OneDriveConsumer','OneDriveCommercial'):
        root = os.environ.get(env)
        if root and p.is_relative_to(Path(root).resolve()):
            raise ValueError('WARNING: data path belongs to a cloud sync root.')
    if p == PROJECT or p.is_relative_to(PROJECT):
        raise ValueError('Patient data cannot be stored in the code directory.')
    if os.name == 'nt':
        import ctypes
        if ctypes.windll.kernel32.GetDriveTypeW(p.anchor) == 4:
            raise ValueError('Network drives are prohibited.')
    return p

def validate_paths(c):
    if not c.local_storage_confirmed:
        raise ValueError('Confirm all data directories are local and not synchronized by any cloud client.')
    raw, work, out = [safe_path(v) for v in (c.raw_dir,c.workspace_dir,c.output_dir)]
    if not raw.is_dir():
        raise ValueError('Raw Data Directory does not exist.')
    for a,b in ((raw,work),(raw,out),(work,out)):
        if a.is_relative_to(b) or b.is_relative_to(a):
            raise ValueError('Raw, workspace and output must be separate, non-nested directories.')
    for p in (work,out):
        p.mkdir(parents=True,exist_ok=True)
        import tempfile
        try:
            with tempfile.TemporaryFile(dir=p):
                pass
        except OSError:
            raise ValueError('Workspace or output directory is not writable.') from None
    return raw,work,out

def install_network_guard():
    """Deny non-loopback Python socket connections, including library telemetry."""
    if getattr(socket, '_aki_guard', False):
        return
    original = socket.socket.connect
    original_ex = socket.socket.connect_ex
    def check(address):
        if isinstance(address,tuple):
            host = str(address[0])
            if host != 'localhost':
                try:
                    allowed = ipaddress.ip_address(host).is_loopback
                except ValueError:
                    allowed = False
                if not allowed:
                    raise PermissionError('External network access is disabled.')
    def connect(self,address):
        check(address)
        return original(self,address)
    def connect_ex(self,address):
        check(address)
        return original_ex(self,address)
    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket._aki_guard = True
