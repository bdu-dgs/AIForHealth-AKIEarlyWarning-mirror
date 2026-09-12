"""Local CSV cleaner. Standard library only; patient data stays in memory."""
import csv
import io
import json
import math
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
TOKEN = secrets.token_urlsafe(32)
LIMIT = 50 * 1024 * 1024
MAX_ROWS = 200000
STATE = {'source': None, 'result': None}
LOCK = threading.Lock()
csv.field_size_limit(2 * 1024 * 1024)

def parse_csv(raw, encoding, delimiter):
    if encoding not in ('utf-8-sig', 'gb18030', 'utf-16') or delimiter not in (',', '\t', ';', '|'):
        raise ValueError('不支持的编码或分隔符。')
    try:
        reader = csv.reader(io.StringIO(raw.decode(encoding), newline=''), delimiter=delimiter, strict=True)
        header = next(reader, None)
        if not header or any(not h.strip() for h in header) or len(set(header)) != len(header):
            raise ValueError('列名不能为空或重复，请先修正表头。')
        if len(header) > 1000:
            raise ValueError('最多支持 1000 列。')
        rows = []
        for line, row in enumerate(reader, 2):
            if not row:
                continue
            if len(row) != len(header):
                raise ValueError(f'第 {line} 条记录的列数与表头不同，请检查分隔符。')
            rows.append(row)
            if len(rows) > MAX_ROWS:
                raise ValueError('简易版最多支持 200,000 行，请先分割文件。')
        return {'columns': header, 'rows': rows}
    except UnicodeError:
        raise ValueError('文件编码不匹配，请切换编码后重新上传。') from None
    except csv.Error:
        raise ValueError('CSV 格式有误，或单个字段超过 2 MB。') from None

def number(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None

def clean(source, config):
    cols = source['columns']
    selected = config.get('columns', cols)
    if not isinstance(selected, list) or not selected or len(set(selected)) != len(selected) or any(c not in cols for c in selected):
        raise ValueError('请至少选择一列有效的输出列。')
    mode = config.get('match', 'all')
    if mode not in ('all', 'any'):
        raise ValueError('筛选组合方式无效。')
    missing = set(config.get('missing_tokens', ['', 'NA', 'N/A', 'NULL', 'null', 'NaN']))
    if any(not isinstance(v, str) for v in missing):
        raise ValueError('空值标记必须是文本。')
    missing.add('')
    trim = bool(config.get('trim', False))
    rules = config.get('rules', [])
    prepared = []
    ops = ('eq', 'ne', 'contains', 'not_contains', 'gt', 'ge', 'lt', 'le', 'empty', 'not_empty')
    for rule in rules:
        col, op, value = rule.get('column'), rule.get('op'), rule.get('value', '')
        if col not in cols or op not in ops or not isinstance(value, str):
            raise ValueError('筛选条件无效。')
        if op in ('gt', 'ge', 'lt', 'le') and number(value) is None:
            raise ValueError('数值比较需要填写有效数字。')
        prepared.append((cols.index(col), op, value))
    def matches(row, rule):
        i, op, value = rule
        x = row[i]
        empty = x in missing
        if op == 'empty': return empty
        if op == 'not_empty': return not empty
        if empty: return False
        if op == 'eq': return x == value
        if op == 'ne': return x != value
        if op == 'contains': return value in x
        if op == 'not_contains': return value not in x
        a, b = number(x), number(value)
        if a is None: return False
        return {'gt': a > b, 'ge': a >= b, 'lt': a < b, 'le': a <= b}[op]
    drop = config.get('drop_missing', [])
    dedupe = config.get('dedupe', [])
    fills = config.get('fills', {})
    if any(c not in cols for c in drop + dedupe) or any(c not in cols or not isinstance(v, str) for c, v in fills.items()):
        raise ValueError('空值处理或去重列无效。')
    drop_i, dup_i, out_i = ([cols.index(c) for c in names] for names in (drop, dedupe, selected))
    fill_i = {cols.index(c): v for c, v in fills.items()}
    stats = {'input_rows': len(source['rows']), 'filtered_rows': 0, 'missing_removed': 0, 'duplicates_removed': 0, 'filled_cells': 0}
    seen, output = set(), []
    for original in source['rows']:
        row = [v.strip() for v in original] if trim else original.copy()
        if prepared and not (all if mode == 'all' else any)(matches(row, r) for r in prepared):
            stats['filtered_rows'] += 1
            continue
        if any(row[i] in missing for i in drop_i):
            stats['missing_removed'] += 1
            continue
        if dup_i:
            key = tuple(None if row[i] in missing else row[i] for i in dup_i)
            if key in seen:
                stats['duplicates_removed'] += 1
                continue
            seen.add(key)
        for i, value in fill_i.items():
            if row[i] in missing:
                row[i] = value
                stats['filled_cells'] += 1
        output.append([row[i] for i in out_i])
    stats['output_rows'] = len(output)
    return {'columns': selected, 'rows': output, 'stats': stats}

def preview(data):
    return {'columns': data['columns'], 'rows': data['rows'][:100], 'total': len(data['rows']), 'stats': data.get('stats')}

def export_csv(data):
    out = io.StringIO(newline='')
    writer = csv.writer(out)
    writer.writerow(data['columns'])
    writer.writerows(data['rows'])
    return out.getvalue().encode('utf-8-sig')

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Never log uploaded content, filenames or request payloads.

    def send(self, status, body, mime='application/json; charset=utf-8', attachment=None):
        if isinstance(body, dict): body = json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'")
        if attachment: self.send_header('Content-Disposition', f'attachment; filename="{attachment}"')
        self.end_headers()
        self.wfile.write(body)

    def allowed(self, auth=False):
        host = self.headers.get('Host', '')
        port = self.server.server_port
        if host not in (f'127.0.0.1:{port}', f'localhost:{port}'):
            return False
        origin = self.headers.get('Origin')
        if origin and origin not in (f'http://127.0.0.1:{port}', f'http://localhost:{port}'):
            return False
        return not auth or secrets.compare_digest(self.headers.get('X-Local-Token', ''), TOKEN)

    def do_GET(self):
        if not self.allowed(): return self.send(403, {'error': '仅允许本地访问。'})
        path = urlsplit(self.path).path
        files = {'/i18n.js': ('i18n.js', 'text/javascript; charset=utf-8'), '/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}
        if path in files:
            name, mime = files[path]
            return self.send(200, (ROOT / name).read_bytes(), mime)
        if path == '/api/session': return self.send(200, {'token': TOKEN})
        if not self.allowed(True): return self.send(403, {'error': '本地会话验证失败，请刷新。'})
        with LOCK:
            if path == '/api/download' and STATE['result'] is not None:
                return self.send(200, export_csv(STATE['result']), 'text/csv; charset=utf-8', 'cleaned.csv')
        return self.send(404, {'error': '尚无结果。'})

    def do_POST(self):
        if not self.allowed(True): return self.send(403, {'error': '本地会话验证失败，请刷新。'})
        try:
            size = int(self.headers.get('Content-Length', '-1'))
            if size < 0 or size > LIMIT:
                return self.send(413, {'error': '文件最多 50 MB。'})
            body = self.rfile.read(size)
            path = urlsplit(self.path).path
            with LOCK:
                if path == '/api/upload':
                    data = parse_csv(body, self.headers.get('X-Encoding', 'utf-8-sig'), {'comma': ',', 'tab': '\t', 'semicolon': ';', 'pipe': '|'}.get(self.headers.get('X-Delimiter', 'comma'), 'invalid'))
                    STATE.update(source=data, result=None)
                    return self.send(200, preview(data))
                if path == '/api/process':
                    if STATE['source'] is None: raise ValueError('请先上传文件。')
                    config = json.loads(body)
                    if not isinstance(config, dict): raise ValueError('处理配置格式无效。')
                    result = clean(STATE['source'], config)
                    STATE['result'] = result
                    return self.send(200, preview(result))
                if path == '/api/clear':
                    STATE.update(source=None, result=None)
                    return self.send(200, {'ok': True})
            self.send(404, {'error': '未知请求。'})
        except (ValueError, TypeError, KeyError, AttributeError):
            import sys
            exc = sys.exception()
            message = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else '配置格式无效。'
            self.send(400, {'error': message})
        except MemoryError:
            self.send(400, {'error': '内存不足，请减小文件。'})

def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    url = f'http://127.0.0.1:{server.server_port}'
    print('Local CSV Filter: ' + url + '\nCtrl+C to stop. Data is held in memory only.', flush=True)
    webbrowser.open(url)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        server.server_close()
        STATE.update(source=None, result=None)

if __name__ == '__main__': main()
