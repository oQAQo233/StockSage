"""GDELT DOC 2.0 API 适配器。

选它做默认源的理由：
- 免费、不需要 key；
- 15 分钟更新，覆盖 65 个语种；
- 单次最多返回 250 条文章 URL，足够做课程作业规模；
- 只给 URL 和元数据，正文由我们自己抓，版权边界清楚。

限制：search window 只覆盖最近 3 个月，且查询语法是 GDELT 自己的一套。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from ..models import ArticleStub
from .base import http_get

API = "https://api.gdeltproject.org/api/v2/doc/doc"


def _parse_seendate(raw: str) -> str:
    """GDELT 的 seendate 形如 20261105T120000Z。"""
    if not raw:
        return ""
    try:
        dt = datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return ""


class GdeltSource:
    name = "gdelt"
    license = "元数据与 URL 免费；正文版权归原站点"

    def __init__(self, max_records: int = 250, sort: str = "datedesc"):
        self.max_records = min(max_records, 250)
        self.sort = sort

    def discover(self, query: str | None, limit: int, days: int) -> list[ArticleStub]:
        if not query:
            raise SystemExit(
                "GDELT 需要一个查询词，例如：--query \"semiconductor export controls\""
            )
        params = {
            "query": query,
            "mode": "artlist",
            "format": "json",
            "maxrecords": min(limit, self.max_records),
            "timespan": f"{max(days, 1)}d",
            "sort": self.sort,
        }
        resp = http_get(API, params=params)
        if resp.status_code == 429:
            raise SystemExit(
                "GDELT 限流了（HTTP 429）。它按出口 IP 限速，大约 5 秒一次请求；"
                "等几分钟再试，或先用 --source rss / --source fixtures。"
            )
        resp.raise_for_status()
        text = resp.text.strip()
        if not text.startswith("{"):
            # GDELT 在无结果或语法错误时返回纯文本提示，而不是 4xx
            raise SystemExit(f"GDELT 返回了非 JSON 内容：{text[:200]}")

        payload = json.loads(text)
        stubs: list[ArticleStub] = []
        for item in payload.get("articles", []):
            url = item.get("url") or item.get("url_mobile")
            if not url:
                continue
            stubs.append(
                ArticleStub(
                    source=self.name,
                    url=url,
                    title=(item.get("title") or "").strip(),
                    published_at=_parse_seendate(item.get("seendate", "")),
                    lang=(item.get("language") or "en").lower(),
                    license=self.license,
                    extra={
                        "domain": item.get("domain", ""),
                        "sourcecountry": item.get("sourcecountry", ""),
                    },
                )
            )
        return stubs[:limit]