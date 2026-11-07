from functools import partial
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest import TestCase

from focus_web import Handler


class QuietHandler(Handler):
    def log_message(self, *args):
        pass


class WebTests(TestCase):
    def setUp(self):
        self.scratch = TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        directory = Path(self.scratch.name)
        public = directory/'reports'
        public.mkdir()
        self.private = directory/'private'
        self.private.mkdir()
        self.key = 'test-'+('x'*32)
        (self.private/'admin.key').write_text(self.key)
        (public/'index.html').write_text('<h1>Sample report</h1>')
        (public/'internal.sqlite3').write_text('private state')
        (public/'intelligence.json').write_text('{"events":[]}')
        self.server = ThreadingHTTPServer(('127.0.0.1',0),partial(QuietHandler,directory=str(public)))
        self.server.settings_path = self.private
        self.thread = Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.port = self.server.server_port
        self.origin = f'http://127.0.0.1:{self.port}'

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def call(self, method, route, data=None, headers=None):
        conn = HTTPConnection('127.0.0.1',self.port,timeout=3)
        try:
            conn.request(method,route,body=data,headers=headers or {})
            response = conn.getresponse()
            return response.status,response.read()
        finally:
            conn.close()

    def test_reports_are_public_but_storage_files_are_not(self):
        self.assertEqual(self.call('GET','/')[0],200)
        self.assertEqual(self.call('GET','/intelligence.json')[0],200)
        for path in ('/internal.sqlite3','/html/','/%2e%2e/private/admin.key','/private/admin.key'):
            with self.subTest(path=path):
                self.assertEqual(self.call('GET',path)[0],404)

    def test_same_origin_authenticated_write_and_revision_conflict(self):
        data = json.dumps({'revision':0,'keywords':['示例市地铁'],'regions':['样本区'],'discover':False})
        headers = {'Content-Type':'application/json','Origin':self.origin,'Authorization':'Bearer '+self.key}
        self.assertEqual(self.call('PUT','/api/focus',data,{**headers,'Authorization':'Bearer invalid'})[0],401)
        self.assertEqual(self.call('PUT','/api/focus',data,{**headers,'Origin':'https://example.test'})[0],403)
        status, response = self.call('PUT','/api/focus',data,headers)
        self.assertEqual(status,200)
        self.assertEqual(json.loads(response)['revision'],1)
        self.assertEqual(json.loads(self.call('GET','/api/focus')[1])['keywords'],['示例市地铁'])
        self.assertEqual(self.call('PUT','/api/focus',data,headers)[0],409)
