import json
import threading
import unittest
import urllib.error
import urllib.request
import server


class CleaningTests(unittest.TestCase):
    def setUp(self):
        self.source = server.parse_csv(b'ID,AGE,VALUE\n001,20, NA \n002,17,4\n001,20,8\n003,30,5\n', 'utf-8-sig', ',')

    def test_filter_dedupe_fill_and_leading_zero(self):
        result = server.clean(self.source, {'trim': True, 'rules': [{'column': 'AGE', 'op': 'ge', 'value': '18'}], 'dedupe': ['ID'], 'fills': {'VALUE': '0'}, 'columns': ['ID', 'VALUE']})
        self.assertEqual(result['rows'], [['001', '0'], ['003', '5']])
        self.assertEqual(result['stats']['filtered_rows'], 1)
        self.assertEqual(result['stats']['duplicates_removed'], 1)
        self.assertEqual(result['stats']['filled_cells'], 1)
        self.assertEqual(self.source['rows'][0][2], ' NA ')

    def test_missing_before_fill(self):
        result = server.clean(self.source, {'trim': True, 'drop_missing': ['VALUE'], 'fills': {'VALUE': '0'}})
        self.assertEqual(result['stats']['missing_removed'], 1)
        self.assertEqual(result['stats']['filled_cells'], 0)

    def test_or_empty_and_invalid_numeric(self):
        result = server.clean(self.source, {'trim': True, 'match': 'any', 'rules': [{'column': 'VALUE', 'op': 'empty'}, {'column': 'AGE', 'op': 'lt', 'value': '18'}]})
        self.assertEqual(len(result['rows']), 2)
        with self.assertRaises(ValueError):
            server.clean(self.source, {'rules': [{'column': 'AGE', 'op': 'gt', 'value': 'oops'}]})

    def test_csv_roundtrip(self):
        raw = 'ID,NOTE\r\n001,"a,b\n中文"\r\n'.encode('utf-8-sig')
        source = server.parse_csv(raw, 'utf-8-sig', ',')
        self.assertEqual(server.parse_csv(server.export_csv(source), 'utf-8-sig', ','), source)

    def test_bad_schema_and_empty_output(self):
        for raw in (b'A,A\n1,2', b'A,B\n1', b',B\n1,2'):
            with self.assertRaises(ValueError): server.parse_csv(raw, 'utf-8-sig', ',')
        result = server.clean(self.source, {'rules': [{'column': 'AGE', 'op': 'gt', 'value': '100'}]})
        self.assertEqual(result['rows'], [])
        self.assertIn(b'ID,AGE,VALUE', server.export_csv(result))


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.worker = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.worker.start()
        cls.base = f'http://127.0.0.1:{cls.http.server_port}'
        cls.client = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.worker.join()

    def request(self, path, data=None, headers=None):
        return self.client.open(urllib.request.Request(self.base + path, data=data, headers=headers or {}))

    def test_upload_process_download_clear(self):
        with self.request('/api/session') as response:
            token = json.load(response)['token']
        headers = {'X-Local-Token': token}
        with self.request('/api/upload', b'ID,AGE\n001,18\n002,10\n', headers) as response:
            self.assertEqual(json.load(response)['total'], 2)
        config = {'rules': [{'column': 'AGE', 'op': 'ge', 'value': '18'}]}
        with self.request('/api/process', json.dumps(config).encode(), headers) as response:
            self.assertEqual(json.load(response)['rows'], [['001', '18']])
        with self.request('/api/download', headers=headers) as response:
            self.assertEqual(response.read().decode('utf-8-sig'), 'ID,AGE\r\n001,18\r\n')
        with self.request('/api/clear', b'', headers): pass
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/api/download', headers=headers)
        self.assertEqual(error.exception.code, 404)

    def test_local_security_and_static(self):
        for path in ('/', '/app.js', '/style.css'):
            with self.request(path) as response:
                self.assertEqual(response.status, 200)
                self.assertIn("connect-src 'self'", response.headers['Content-Security-Policy'])
        for headers in ({'Host': 'evil.example'}, {'Origin': 'https://evil.example'}, {}):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request('/api/clear', b'', headers)
            self.assertEqual(error.exception.code, 403)


if __name__ == '__main__': unittest.main()
