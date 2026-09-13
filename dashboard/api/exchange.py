"""Bounded ZIP exchange for histories too large for a single JSON batch."""
import io
import json
import zipfile
from .storage import encode
from .watcher import ADAPTER, MAX_FILE_BYTES


def volumes(value):
    """Each volume stays within both the import byte limit and schema record limit."""
    key = 'observations' if value['kind'] == 'input' else 'predictions'
    base = {k: v for k, v in value.items() if k != key}
    parts = []; chunk = []; size = len(encode(base).encode())
    for item in value[key]:
        item_size = len(encode(item).encode()) + 1
        if chunk and (len(chunk) >= 10000 or size + item_size > 8 * 1024 * 1024):
            parts.append({**base, key: chunk}); chunk = []; size = len(encode(base).encode())
        chunk.append(item); size += item_size
    if chunk or not parts:
        parts.append({**base, key: chunk})
    return parts


def pack(value):
    key = 'observations' if value['kind'] == 'input' else 'predictions'
    base = {k: v for k, v in value.items() if k != key}
    files = []
    chunk = []
    size = len(encode(base).encode())
    for item in value[key]:
        item_size = len(encode(item).encode()) + 1
        if chunk and (len(chunk) >= 1000 or size + item_size > 14 * 1024 * 1024):
            files.append(encode({**base, key: chunk}).encode()); chunk = []; size = len(encode(base).encode())
        chunk.append(item); size += item_size
    if chunk or not files:
        files.append(encode({**base, key: chunk}).encode())
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        names = [f'batch-{i:06}.json' for i in range(len(files))]
        archive.writestr('manifest.json', encode({'format': 'aki-batches-v1', 'files': names}))
        for name, content in zip(names, files):
            archive.writestr(name, content)
    result = buffer.getvalue()
    if len(files) > 300 or sum(map(len, files)) > 64 * 1024 * 1024 or len(result) > MAX_FILE_BYTES:
        raise ValueError('数据超过单卷限制，请使用导出分卷接口')
    return result


def unpack(raw, filename):
    if filename.lower().endswith('.json'):
        return [ADAPTER.validate_json(raw)]
    if not filename.lower().endswith('.zip'):
        raise ValueError('请使用 JSON 或本网站导出的 ZIP 文件')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries) > 301 or sum(x.file_size for x in entries) > 64 * 1024 * 1024:
                raise ValueError('交换包超过 300 个批次或解压后 64 MB，请拆分')
            if any(x.file_size > MAX_FILE_BYTES for x in entries):
                raise ValueError('交换包单个批次超过 16 MB')
            manifest = json.loads(archive.read('manifest.json'))
            names = manifest.get('files')
            if manifest.get('format') != 'aki-batches-v1' or not isinstance(names, list) or not names:
                raise ValueError('交换包清单格式不正确')
            if len(set(names)) != len(names):
                raise ValueError('交换包清单存在重复批次')
            return [ADAPTER.validate_json(archive.read(name)) for name in names]
    except (zipfile.BadZipFile, KeyError, TypeError):
        raise ValueError('无法读取交换包或清单引用的批次')
