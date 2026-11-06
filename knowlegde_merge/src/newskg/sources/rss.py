"""RSS / Atom 适配器。

RSS 是「发现层」里最省事的一类：无需 key、结构稳定、天然分频道。
缺点是不给正文，需要再抓一次文章页（本管线照抓）。
"""

from __future__ import annotations

import os

from ..models import ArticleStub
from .base import http_get

#: 默认订阅几个以「快讯 + 全文开放」著称的源；可在环境变量里覆盖成逗号分隔的 URL
DEFAULT_FEEDS = [
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://www.aljazeera.com/xml/rss/all.xml",
    "https://feeds.npr.org/1001/rss.xml",
]


class RssSource:
    name = "rss"
    license = "各站点 RSS 条款，正文版权归原站点"

    def __init__(self, feeds: list[str] | None = None):
        raw = os.getenv("NEWSKG_RSS_FEEDS", "")
        env_feeds = [u.strip() for u in raw.split(",") if u.strip()]
        self.feeds = feeds or env_feeds or DEFAULT_FEEDS

    def discover(self, query: str | None, limit: int, days: int) -> list[ArticleStub]:
        import feedparser

        stubs: list[ArticleStub] = []
        for feed_url in self.feeds:
            if len(stubs) >= limit:
                break
            try:
                resp = http_get(feed_url)
                resp.raise_for_status()
            except Exception as exc:  # 单个 feed 挂了不影响其它源
                print(f"  [rss] 跳过 {feed_url}: {exc}")
                continue

            parsed = feedparser.parse(resp.content)
            for entry in parsed.entries:
                if len(stubs) >= limit:
                    break
                title = (entry.get("title") or "").strip()
                if query and query.lower() not in title.lower():
                    continue
                published = ""
                if entry.get("published_parsed"):
                    from time import strftime

                    published = strftime("%Y-%m-%dT%H:%M:%SZ", entry.published_parsed)
                stubs.append(
                    ArticleStub(
                        source=self.name,
                        url=entry.get("link", ""),
                        title=title,
                        published_at=published,
                        lang=parsed.feed.get("language", "en")[:2].lower() or "en",
                        license=self.license,
                        extra={"feed": parsed.feed.get("title", feed_url)},
                    )
                )
        return stubs[:limit]