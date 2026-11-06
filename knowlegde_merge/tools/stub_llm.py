"""本地假 OpenAI 端点：验证 llm 后端的请求/解析/入库链路，不产生任何真实结果。

用法：
    python tools\\stub_llm.py
    set NEWSKG_LLM_BASE_URL=http://127.0.0.1:8765/v1
    .\\run.cmd --db out\\test.sqlite3 build --backend llm --limit 3

它固定返回四条候选关系，其中两条是故意做坏的（自环、超长实体），
用来确认抽取器的过滤逻辑真的在生效。
"""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer

CANNED = {
    "entities": [{"name": "Stub Corp", "type": "ORG"}, {"name": "Stub Target", "type": "ORG"}],
    "relations": [
        {"subject": "Stub Corp", "predicate": "acquired", "object": "Stub Target"},
        {"subject": "Stub Corp", "predicate": "based in", "object": "Stub City"},
        {"subject": "same", "predicate": "equals", "object": "same"},
        {"subject": "x" * 200, "predicate": "too_long", "object": "y"},
    ],
}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        body = json.dumps({"choices": [{"message": {"content": json.dumps(CANNED)}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print("stub LLM listening on http://127.0.0.1:8765/v1")
    HTTPServer(("127.0.0.1", 8765), Handler).serve_forever()