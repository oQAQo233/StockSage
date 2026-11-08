"""The Guardian Open Platform 适配器。

开发者 key 免费，限额约 1 次/秒、500 次/天，仅限非商业用途
（课程作业正好落在许可范围内）。返回字段里带 bodyText，可以省掉一次正文抓取。
"""

from __future__ import annotations

import os

from ..models import ArticleStub
from .base import http_get

API = "https://content.guardianapis.com/search"


class GuardianSource:
    name = "guardian"
    license = "Guardian Open Platform 开发者条款，仅限非商业用途"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("GUARDIAN_API_KEY", "")

    def discover(self, query: str | None, limit: int, days: int) -> list[ArticleStub]:
        if not self.api_key:
            raise SystemExit(
                "Guardian 源需要 API key：到 https://open-platform.theguardian.com/access/ "
                "免费申请后设置环境变量 GUARDIAN_API_KEY，或改用 --source gdelt / fixtures。"
            )
        params = {
            "api-key": self.api_key,
            "page-size": min(limit, 50),
            "order-by": "newest",
            "show-fields": "bodyText,byline,headline,firstPublicationDate",
        }
        if query:
            params["q"] = query

        resp = http_get(API, params=params)
        resp.raise_for_status()
        results = resp.json().get("response", {}).get("results", [])

        stubs: list[ArticleStub] = []
        for item in results:
            fields = item.get("fields", {})
            stubs.append(
                ArticleStub(
                    source=self.name,
                    url=item.get("webUrl", ""),
                    title=(item.get("webTitle") or fields.get("headline") or "").strip(),
                    published_at=item.get("webPublicationDate", ""),
                    lang="en",
                    license=self.license,
                    extra={
                        "section": item.get("sectionName", ""),
                        "byline": fields.get("byline", ""),
                        # Guardian 直接给正文，抓取层会优先用它，避免再请求一次
                        "body_text": fields.get("bodyText", ""),
                    },
                )
            )
        return stubs[:limit]