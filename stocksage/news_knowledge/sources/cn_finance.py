"""中文财经新闻源适配器。

2026-11 实测，中文财经站点的公开接口大多已经废弃：和讯 RSS 返回 502，
证券之星 / 中国证券网 / 第一财经 / 环球网 都是 404，雪球被 WAF 拦，财联社要签名。
**活下来且能给出发布时间**的只有下面四个端点，所以这里只实现这四个，不写死掉的。

四个端点覆盖三家发行人、三种数据结构：

| name | 发行人 | 形态 | 特点 |
| --- | --- | --- | --- |
| `eastmoney-rss` | 东方财富 | RSS 2.0 | 整篇文章，含 pubDate（+0800） |
| `eastmoney` | 东方财富 | JSON | 7x24 快讯，`showTime` 是北京时间 |
| `ths` | 同花顺 | JSON | 7x24 快讯，`ctime` 是 Unix 秒，带 stock 字段 |
| `sina-fin` | 新浪财经 | JSON | 滚动新闻，`ctime` 是 Unix 秒，偏美股/宏观 |

统一契约（与 sources/rss.py 等一致）：
只返回 ArticleStub，且 `published_at` **一律格式化为 ISO 8601 UTC**。
下游的时间窗过滤完全依赖这个约定，所以每个适配器都要自己把本地时间换算成 UTC，
不要让「+0800 的字符串」流到后面去。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from ..models import ArticleStub
from .base import http_get

#: 北京时间的时区偏移。东方财富的 showTime 是不带时区的本地时间，只能按 +08:00 解释。
CST = timezone(timedelta(hours=8))


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _from_unix(raw) -> str:
    """Unix 秒（可能是字符串）-> ISO 8601 UTC。"""
    try:
        return _iso(datetime.fromtimestamp(int(raw), tz=timezone.utc))
    except (TypeError, ValueError, OSError):
        return ""


def _from_cst_text(raw: str) -> str:
    """'2026-11-06 01:22:18'（北京时间，无时区标记）-> ISO 8601 UTC。"""
    if not raw:
        return ""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return _iso(datetime.strptime(raw.strip(), fmt).replace(tzinfo=CST))
        except ValueError:
            continue
    return ""


class EastmoneyRss:
    """东方财富 RSS：整篇文章，最适合做三元组抽取。"""

    name = "eastmoney-rss"
    license = "正文版权归东方财富网，仅课程研究使用"
    FEED = "http://rss.eastmoney.com/rss_partener.xml"

    def discover(self, query: str | None, limit: int, days: int) -> list[ArticleStub]:
        import feedparser

        resp = http_get(self.FEED)
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)

        stubs: list[ArticleStub] = []
        for entry in parsed.entries:
            title = (entry.get("title") or "").strip()
            if query and query.lower() not in title.lower():
                continue
            published = ""
            if entry.get("published_parsed"):
                import calendar

                ts = calendar.timegm(entry.published_parsed)
                published = _from_unix(ts)
            stubs.append(
                ArticleStub(
                    source=self.name,
                    url=(entry.get("link") or "").strip(),
                    title=title,
                    published_at=published,
                    lang="zh",
                    license=self.license,
                    extra={"feed": parsed.feed.get("title", "东方财富")},
                )
            )
            if len(stubs) >= limit:
                break
        return stubs


class EastmoneyNews:
    """东方财富 7x24 资讯（JSON）。

    这个接口要求带 `req_trace`，缺失时直接返回
    `{"code":0,"message":"Required String parameter 'req_trace' is not present"}`。
    它只是个防缓存用的随机串，任意 UUID 都行。
    """

    name = "eastmoney"
    license = "正文版权归东方财富网，仅课程研究使用"
    API = "https://np-listapi.eastmoney.com/comm/web/getNewsByColumns"

    def discover(self, query: str | None, limit: int, days: int) -> list[ArticleStub]:
        stubs: list[ArticleStub] = []
        page_size = min(max(limit, 10), 50)
        for page in range(1, 6):
            params = {
                "client": "web",
                "biz": "web_news_col",
                "column": "350",
                "order": "1",
                "needInteractData": "0",
                "page_index": page,
                "page_size": page_size,
                "req_trace": uuid.uuid4().hex,
            }
            resp = http_get(self.API, params=params)
            resp.raise_for_status()
            payload = resp.json()
            items = (payload.get("data") or {}).get("list") or []
            if not items:
                break

            for item in items:
                title = (item.get("title") or "").strip()
                url = (item.get("uniqueUrl") or item.get("url") or "").strip()
                if not url:
                    continue
                if query and query.lower() not in title.lower():
                    continue
                stubs.append(
                    ArticleStub(
                        source=self.name,
                        url=url,
                        title=title,
                        published_at=_from_cst_text(item.get("showTime", "")),
                        lang="zh",
                        license=self.license,
                        extra={
                            "summary": (item.get("summary") or "")[:200],
                            "media": item.get("mediaName", ""),
                        },
                    )
                )
                if len(stubs) >= limit:
                    return stubs
        return stubs


class ThsNews:
    """同花顺 7x24 快讯（JSON）。

    每条带 `stock` 字段（相关个股），是做「股票相关」过滤时最干净的信号。
    注意：`tag` 参数留空才返回数据，传 tag=1 会得到空列表。
    """

    name = "ths"
    license = "正文版权归同花顺，仅课程研究使用"
    API = "https://news.10jqka.com.cn/tapp/news/push/stock/"

    def discover(self, query: str | None, limit: int, days: int) -> list[ArticleStub]:
        stubs: list[ArticleStub] = []
        page_size = min(max(limit, 10), 50)
        for page in range(1, 6):
            params = {"page": page, "tag": "", "track": "website", "pagesize": page_size}
            resp = http_get(self.API, params=params)
            resp.raise_for_status()
            payload = resp.json()
            items = (payload.get("data") or {}).get("list") or []
            if not items:
                break

            for item in items:
                title = (item.get("title") or "").strip()
                url = (item.get("url") or "").strip()
                if not url:
                    continue
                if query and query.lower() not in title.lower():
                    continue
                stubs.append(
                    ArticleStub(
                        source=self.name,
                        url=url,
                        title=title,
                        published_at=_from_unix(item.get("ctime") or item.get("rtime")),
                        lang="zh",
                        license=self.license,
                        extra={
                            "digest": (item.get("digest") or "")[:200],
                            # stock 是 [{name, stockCode, stockMarket}, ...]，只在提到具体个股时才有
                            "stock": item.get("stock") or [],
                            "stock_codes": [
                                str(s.get("stockCode", ""))
                                for s in (item.get("stock") or [])
                                if isinstance(s, dict)
                            ],
                            "source": item.get("source", ""),
                        },
                    )
                )
                if len(stubs) >= limit:
                    return stubs
        return stubs


class SinaFinance:
    """新浪财经滚动新闻（JSON）。

    注意：该接口的 lid 分组已经变过，2516/2517/2509 现在返回同一份内容，
    且以美股、宏观为主。保留它是为了「多源」而不是「多 A 股」，做 A 股请优先用东方财富。
    """

    name = "sina-fin"
    license = "正文版权归新浪财经，仅课程研究使用"
    API = "https://feed.mix.sina.com.cn/api/roll/get"
    LIDS = [2516, 2510]

    def discover(self, query: str | None, limit: int, days: int) -> list[ArticleStub]:
        stubs: list[ArticleStub] = []
        seen: set[str] = set()
        for lid in self.LIDS:
            params = {"pageid": 153, "lid": lid, "num": min(max(limit, 10), 50), "page": 1}
            resp = http_get(self.API, params=params)
            resp.raise_for_status()
            items = (resp.json().get("result") or {}).get("data") or []
            for item in items:
                title = (item.get("title") or "").strip()
                url = (item.get("url") or "").strip()
                if not url or url in seen:
                    continue
                if query and query.lower() not in title.lower():
                    continue
                seen.add(url)
                stubs.append(
                    ArticleStub(
                        source=self.name,
                        url=url,
                        title=title,
                        published_at=_from_unix(item.get("ctime")),
                        lang="zh",
                        license=self.license,
                        extra={"summary": (item.get("summary") or "")[:200]},
                    )
                )
                if len(stubs) >= limit:
                    return stubs
        return stubs
