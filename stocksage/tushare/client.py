"""Tushare Pro HTTP 协议、限速、重试与可校验的原始响应缓存。"""
import hashlib
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from .models import QueryResult, digest, instant, json_text, now


class TushareError(RuntimeError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise TushareError("Tushare 返回重定向，拒绝转发凭据")


def environment_token():
    token = os.environ.get("TUSHARE_TOKEN")
    if token:
        return token.strip()
    path = Path(__file__).resolve().parents[2] / ".env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            key, separator, value = line.strip().partition("=")
            if separator and key.strip() == "TUSHARE_TOKEN":
                return value.strip().strip("\"'")
    return ""


class TushareClient:
    """凭据只发往 HTTPS 官方端点，不写入缓存、日志或知识库。"""

    def __init__(self, token=None, *, cache_dir="data/knowledge_base/tushare_raw", resume=False,
                 min_interval=1.3, timeout=30, retries=2, transport=None):
        self._token = token if token is not None else environment_token()
        if not isinstance(self._token, str) or not self._token.strip():
            raise ValueError("请在环境变量或项目 .env 中配置 TUSHARE_TOKEN")
        self._token = self._token.strip()
        if min_interval < 0 or timeout <= 0 or not 0 <= retries <= 8:
            raise ValueError("限速、超时或重试参数无效")
        self.cache_dir = Path(cache_dir).resolve()
        self.resume, self.min_interval = resume, min_interval
        self.timeout, self.retries = timeout, retries
        self._transport = transport or self._http
        self._last_call = None
        self._opener = build_opener(_NoRedirect())

    def _http(self, payload):
        request = Request("https://api.tushare.pro", data=json_text(payload).encode("utf-8"),
                          headers={"Content-Type": "application/json", "User-Agent": "StockSage/0.1"})
        with self._opener.open(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def safe_message(self, error):
        return str(error).replace(self._token, "[REDACTED]")[:400]

    @staticmethod
    def _rows(response):
        if not isinstance(response, dict) or response.get("code") != 0:
            raise TushareError("缓存或接口响应缺少成功 code")
        data = response.get("data")
        if not isinstance(data, dict):
            raise TushareError("接口返回 data 结构错误")
        fields, items = data.get("fields"), data.get("items")
        if not isinstance(fields, list) or not all(isinstance(f, str) for f in fields) or len(set(fields)) != len(fields):
            raise TushareError("接口返回 fields 无效")
        if not isinstance(items, list) or any(not isinstance(r, list) or len(r) != len(fields) for r in items):
            raise TushareError("接口返回 items 与 fields 不匹配")
        return [dict(zip(fields, item)) for item in items]

    def query(self, api_name, params=None, fields=""):
        params = dict(params or {})
        if "token" in params:
            raise ValueError("token 不得放入查询参数")
        query = dict(api_name=api_name, params=params, fields=fields)
        directory = self.cache_dir / digest(query)
        if self.resume and directory.exists():
            files = sorted(directory.glob("*.json"))
            if files:
                path = files[-1]
                raw = path.read_bytes()
                envelope = json.loads(raw)
                payload_text = json_text(envelope["response"])
                if envelope["query"] != query or envelope["response_sha256"] != hashlib.sha256(payload_text.encode()).hexdigest():
                    raise TushareError("原始缓存校验失败")
                instant(envelope["observed_at"])
                return QueryResult(envelope["request_id"], api_name, params, self._rows(envelope["response"]),
                                   envelope["observed_at"], str(path), hashlib.sha256(raw).hexdigest())

        payload = {**query, "token": self._token}
        for attempt in range(self.retries + 1):
            if self._last_call is not None:
                time.sleep(max(0, self.min_interval - (time.monotonic() - self._last_call)))
            self._last_call = time.monotonic()
            retryable = False
            try:
                response = self._transport(payload)
                if not isinstance(response, dict) or "code" not in response:
                    raise TushareError("Tushare 响应缺少 code")
                if response["code"] != 0:
                    message = self.safe_message(response.get("msg", "未知错误"))
                    retryable = any(word in message.lower() for word in ("频率", "每分钟", "rate limit", "too many"))
                    raise TushareError(f"{api_name} 返回 {response['code']}：{message}")
                rows = self._rows(response)
                break
            except HTTPError as error:
                retryable = error.code in {429, 500, 502, 503, 504}
                failure = TushareError(f"{api_name} HTTP {error.code}")
            except (URLError, TimeoutError, ConnectionError, OSError) as error:
                retryable = True
                failure = TushareError(f"{api_name} 连接失败：{self.safe_message(error)}")
            except TushareError as error:
                failure = TushareError(self.safe_message(error))
            if not retryable or attempt == self.retries:
                raise failure from None
            time.sleep(min(60, max(self.min_interval, 2 ** attempt)))
        # 不保存携带 token 的请求体；拒绝异常服务回显凭据。
        text = json_text(response)
        if self._token in text:
            raise TushareError("响应包含凭据回显，拒绝落盘")
        observed_at, request_id = now(), uuid4().hex
        envelope = dict(query=query, request_id=request_id, observed_at=observed_at, response=response,
                        response_sha256=hashlib.sha256(text.encode()).hexdigest())
        raw = json_text(envelope).encode("utf-8")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{observed_at.replace(':', '-')}_{request_id}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(raw)
        temporary.replace(path)
        return QueryResult(request_id, api_name, params, rows, observed_at, str(path), hashlib.sha256(raw).hexdigest())

    def pages(self, api_name, params=None, fields="", *, page_size=1000):
        """limit/offset 分页；忽略分页的上游会显式失败，避免静默截断。"""
        if not 1 <= page_size <= 1000:
            raise ValueError("page_size 须在 1—1000 之间")
        seen = set()
        for offset in range(0, 10_000_000, page_size):
            result = self.query(api_name, {**(params or {}), "limit": page_size, "offset": offset}, fields)
            fingerprint = digest(sorted(json_text(r) for r in result.rows))
            if result.rows and fingerprint in seen:
                raise TushareError(f"{api_name} 重复返回同一页，无法确认数据完整性")
            if len(result.rows) > page_size:
                raise TushareError(f"{api_name} 未遵守 limit，无法确认分页完整性")
            seen.add(fingerprint)
            yield result
            if len(result.rows) < page_size:
                return
        raise TushareError(f"{api_name} 超过最大分页范围")
