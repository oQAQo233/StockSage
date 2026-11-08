"""源适配器的公共接口与 HTTP 工具。"""

from __future__ import annotations

import time
from typing import Protocol

import requests

from ..config import USER_AGENT, RUN
from ..models import ArticleStub


class Source(Protocol):
    name: str
    license: str

    def discover(self, query: str | None, limit: int, days: int) -> list[ArticleStub]:
        ...


def http_get(
    url: str,
    params: dict | None = None,
    timeout: int | None = None,
    retries: int = 3,
    backoff: float = 5.0,
) -> requests.Response:
    """统一的 GET：带 UA、带超时、对 429/5xx 做指数退避重试。

    GDELT 对同一出口 IP 限流很严，碰上 429 直接抛异常会让整轮抓取白跑；
    这里退避重试三次，仍失败则把最后一次响应交给调用方判断。
    """
    last: requests.Response | None = None
    for attempt in range(retries):
        resp = requests.get(
            url,
            params=params,
            timeout=timeout or RUN.request_timeout,
            headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
        )
        if resp.status_code == 429 or resp.status_code >= 500:
            last = resp
            if attempt < retries - 1:
                wait = backoff * (attempt + 1)
                print(f"      [{resp.status_code}] 限流，{wait:.0f} 秒后重试（{attempt + 1}/{retries}）")
                time.sleep(wait)
            continue
        return resp
    return last  # type: ignore[return-value]


class RateLimiter:
    """按域名限速，最小间隔由 config.POLITE_DELAY 控制。"""

    def __init__(self, delay: float):
        self.delay = delay
        self._last: dict[str, float] = {}

    def wait(self, url: str) -> None:
        from urllib.parse import urlparse

        host = urlparse(url).netloc
        last = self._last.get(host)
        if last is not None:
            remain = self.delay - (time.time() - last)
            if remain > 0:
                time.sleep(remain)
        self._last[host] = time.time()