"""Serve existing report files and a same-origin settings API on the LAN."""
import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import re
from pathlib import Path
from secrets import compare_digest
from urllib.parse import unquote, urlsplit

from focus_watch import STORE, Conflict, load_settings, save_settings

LIMIT = 8192


class Handler(SimpleHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(8)
        super().setup()

    def api_path(self):
        try:
            return urlsplit(self.path).path == '/api/focus'
        except ValueError:
            return False

    def reply(self, status, payload):
        body = json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.end_headers()
        if self.command!='HEAD':
            self.wfile.write(body)

    def read_settings(self):
        try:
            self.reply(200,load_settings(self.server.settings_path))
        except (OSError,ValueError):
            self.reply(503,{'error':'NAS 关注设置暂时不可读'})

    def do_GET(self):
        if self.api_path():
            return self.read_settings()
        return super().do_GET()

    def do_HEAD(self):
        if self.api_path():
            return self.read_settings()
        return super().do_HEAD()

    def do_PUT(self):
        if not self.api_path():
            return self.reply(404,{'error':'接口不存在'})
        origin = self.headers.get('Origin')
        host = self.headers.get('Host')
        if not host or origin not in (f'http://{host}',f'https://{host}'):
            return self.reply(403,{'error':'请从当前新闻雷达页面提交'})
        try:
            key = (self.server.settings_path/'admin.key').read_bytes().strip()
        except OSError:
            key = b''
        auth = self.headers.get('Authorization','').encode()
        if len(key)<32 or not compare_digest(auth,b'Bearer '+key):
            return self.reply(401,{'error':'管理口令不正确'})
        if self.headers.get('Transfer-Encoding') or self.headers.get_content_type()!='application/json':
            return self.reply(415,{'error':'需要 JSON 格式'})
        lengths = self.headers.get_all('Content-Length') or []
        if len(lengths)!=1 or not lengths[0].isascii() or not lengths[0].isdigit() or len(lengths[0])>5:
            return self.reply(400,{'error':'请求长度无效'})
        size = int(lengths[0])
        if not 1<=size<=LIMIT:
            return self.reply(413,{'error':'设置内容过长'})
        try:
            body = json.loads(self.rfile.read(size).decode('utf-8'))
            if not isinstance(body,dict):
                raise ValueError('设置格式无效')
            revision = body.pop('revision',None)
            saved = save_settings(self.server.settings_path,revision,body)
        except Conflict as error:
            return self.reply(409,{'error':str(error)})
        except (json.JSONDecodeError,UnicodeError,RecursionError,TimeoutError):
            return self.reply(400,{'error':'设置格式无效'})
        except ValueError as error:
            return self.reply(400,{'error':str(error)})
        except OSError:
            return self.reply(503,{'error':'NAS 关注设置暂时无法保存'})
        return self.reply(200,saved)

    def send_head(self):
        try:
            route = unquote(urlsplit(self.path).path)
            if route not in ('/','/index.html','/intelligence.json') and not re.fullmatch(
                r'/html/(?:latest/[a-z_]+|\d{4}-\d{2}-\d{2}/\d{2}-\d{2})\.html',route
            ):
                raise ValueError
            file = Path(self.directory)/'index.html' if route=='/' else Path(self.translate_path(self.path))
            file.resolve().relative_to(Path(self.directory).resolve())
            if not file.is_file():
                raise ValueError
        except (OSError,ValueError,RuntimeError):
            self.send_error(404)
            return None
        return super().send_head()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('port',type=int)
    parser.add_argument('--bind',default='0.0.0.0')
    parser.add_argument('--store',type=Path,default=STORE)
    args = parser.parse_args()
    with ThreadingHTTPServer((args.bind,args.port),Handler) as server:
        server.settings_path = args.store
        server.serve_forever()


if __name__=='__main__':
    main()
