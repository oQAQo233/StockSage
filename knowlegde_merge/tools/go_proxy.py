"""本地中转：把 litellm 的请求转发到 OpenCode Go，并补上 x-opencode-session。

litellm 只会发标准的 OpenAI 头，而 Go 网关要求 x-opencode-session（UUID，用于路由）。
与其改 litellm 或 kg-gen，不如在前面放一个 40 行的转发器——下游完全无感。

用法：
    python tools/go_proxy.py            # 监听 127.0.0.1:8766
然后把抽取器的 base_url 指向 http://127.0.0.1:8766/v1
"""

from __future__ import annotations

import http.server
import json
import os
import socketserver
import uuid

import requests

UPSTREAM = os.getenv("GO_UPSTREAM", "https://opencode.ai/inference/go/openai/v1")
PORT = int(os.getenv("GO_PROXY_PORT", "8766"))
SESSION = os.getenv("GO_SESSION") or str(uuid.uuid4())


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 安静点，别刷屏
        pass

    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        raw = self.rfile.read(length)

        headers = {"Content-Type": "application/json", "x-opencode-session": SESSION}
        auth = self.headers.get("authorization")
        if auth:
            headers["Authorization"] = auth

        try:
            resp = requests.post(
                f"{UPSTREAM}/chat/completions", data=raw, headers=headers, timeout=300
            )
            body, status = resp.content, resp.status_code
        except Exception as exc:  # noqa: BLE001
            body = json.dumps({"error": {"message": f"proxy failed: {exc}"}}).encode()
            status = 502

        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            body = json.dumps({"object": "list", "data": [{"id": "deepseek-v4.1-flash"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    print(f"中转已启动 http://127.0.0.1:{PORT}/v1  ->  {UPSTREAM}")
    Server(("127.0.0.1", PORT), Handler).serve_forever()