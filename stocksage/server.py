"""本地只读展示服务；静态资源白名单，无任意文件访问接口。"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

WEB = Path(__file__).parent / "web"
ASSETS = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8")}


def make_handler(report):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def send(self, status, body, content_type="application/json; charset=utf-8"):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urlsplit(self.path)
            if url.path in ASSETS:
                filename, mime = ASSETS[url.path]
                return self.send(200, (WEB / filename).read_bytes(), mime)
            if url.path == "/api/health":
                return self.send(200, dict(status="ok", synthetic=report["dataset"]["synthetic"]))
            if url.path == "/api/stocks":
                return self.send(200, dict(stocks=report["stocks"], dataset=report["dataset"], quality=report["quality"], agents=report["agents"], limitations=report["limitations"]))
            if url.path == "/api/dashboard":
                symbol = parse_qs(url.query).get("symbol", [report["stocks"][0]["symbol"]])[0]
                if symbol not in report["reports"]:
                    return self.send(404, dict(error="未知股票代码"))
                return self.send(200, report["reports"][symbol])
            return self.send(404, dict(error="资源不存在"))
    return Handler


def serve(report, host="127.0.0.1", port=8765):
    with ThreadingHTTPServer((host, port), make_handler(report)) as server:
        print(f"打开 http://{host}:{port}  查看演示，按 Ctrl+C 停止。", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nStockSage 已停止。", flush=True)
