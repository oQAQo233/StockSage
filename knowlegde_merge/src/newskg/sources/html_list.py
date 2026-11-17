"""HTML 列表页源：站点已撤掉 RSS，但列表页仍是服务端渲染的。

为什么需要这一层：科技日报、新华网这类最权威的中文科技媒体，RSS 端点不是 404 就是
不给 `pubDate`（详见 README §5.1），但它们的频道首页把「标题 + 链接」直接写在 HTML
里，而链接路径自带发布日期（`/tech/20261117/<id>/c.html`、`/web/2026-11/17/content_x.html`）。

于是抽取规则只有一条：**能从 URL 里读出日期**，标题从 `<a>` 文字取。日期不依赖页面上
那个 `class="time"` 的节点，站点改模板也不受影响；读不出日期的链接一律丢弃——宁可少一条，
也不要一条时间未知、会把 `--days` 时间窗变成摆设的文章。

这一层只做「列表页 → ArticleStub」，正文抓取仍由上层统一处理，和 RSS 源共用同一条流水线。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin

from ..models import ArticleStub
from .base import http_get

#: 单次列表页最多解析多少条（多源合并时由上层再截断）
PER_PAGE_CAP = 60

#: URL 里的两种日期写法：`/20261117/`、`/2026-11/17/`
_DATE_COMPACT = re.compile(r"/(\d{4})(\d{2})(\d{2})/")
_DATE_SLASHED = re.compile(r"/(\d{4})-(\d{2})/(\d{2})/")

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _plain(raw: str) -> str:
    """去标签、解常见实体、压空白。"""
    text = _TAG.sub(" ", raw or "")
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    return _WS.sub(" ", text).strip()


def date_from_url(url: str) -> str:
    """从文章 URL 推出发布日期，返回 `YYYY-MM-DD`；读不出则返回空串。"""
    for pattern in (_DATE_COMPACT, _DATE_SLASHED):
        m = pattern.search(url)
        if not m:
            continue
        year, month, day = m.group(1), m.group(2), m.group(3)
        if not (2000 <= int(year) <= 2100 and 1 <= int(month) <= 12 and 1 <= int(day) <= 31):
            continue
        return f"{year}-{month}-{day}"
    return ""


def _utc_from_day(day: str) -> str:
    """`YYYY-MM-DD`（北京时间）-> ISO 8601 UTC 字符串，与 RSS 源口径一致。"""
    from datetime import datetime, timedelta, timezone

    dt = datetime.fromisoformat(f"{day}T00:00:00+08:00").astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class HtmlSpec:
    """一个列表页的抓取规格。

    `item` 必须有两个分组：`(href, title)`；href 可以是相对路径。
    """

    list_url: str
    sitename: str
    item: re.Pattern[str]
    lang: str = "zh"


#: key -> 规格。新增站点只需加一条，不用再写类。
HTML_SPECS: dict[str, HtmlSpec] = {
    "xinhua_tech": HtmlSpec(
        list_url="http://www.news.cn/tech/",
        sitename="新华网·科技",
        item=re.compile(
            r'<div class="tit"><a href="(/tech/\d{8}/[0-9a-f]+/c\.html)"[^>]*>(.*?)</a>',
            re.S,
        ),
    ),
    "stdaily": HtmlSpec(
        list_url="https://www.stdaily.com/",
        sitename="科技日报",
        item=re.compile(
            r'<a href="(https://www\.stdaily\.com/web/[a-z]+/\d{4}-\d{2}/\d{2}/content_\d+\.html)"[^>]*>(.*?)</a>',
            re.S,
        ),
    ),
}


def _decode(resp) -> str:
    """科技日报/新华网都是 UTF-8，但历史页面偶有 GB18030，兜一层。"""
    raw = resp.content
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


class HtmlListFeed:
    """把一个列表页适配成 Source。"""

    def __init__(self, name: str, keys: list[str]):
        self.name = name
        self.keys = [k for k in keys if k in HTML_SPECS]
        self.license = "各站点页面版权归原站点"

    def discover(self, query: str | None, limit: int, days: int) -> list[ArticleStub]:
        cap = min(PER_PAGE_CAP, max(3, limit))
        stubs: list[ArticleStub] = []

        for key in self.keys:
            spec = HTML_SPECS[key]
            try:
                html = _decode(http_get(spec.list_url))
            except Exception as exc:  # noqa: BLE001  单页失败不影响其它源
                print(f"  [{self.name}] 跳过 {key}: {type(exc).__name__}: {str(exc)[:70]}")
                continue

            seen: set[str] = set()
            bucket: list[ArticleStub] = []
            for href, raw_title in spec.item.findall(html):
                if len(bucket) >= cap:
                    break
                url = urljoin(spec.list_url, href)
                if url in seen:
                    continue
                title = _plain(raw_title)
                if not title or (query and query.lower() not in title.lower()):
                    continue
                day = date_from_url(url)
                if not day:
                    continue  # 没有日期就不要：时间窗会静默失效
                seen.add(url)
                bucket.append(
                    ArticleStub(
                        source=key,
                        url=url,
                        title=title,
                        # 列表页只到「天」：按北京时间当天 00:00 记，再统一换成 UTC Z，
                        # 否则和 RSS 源的 "…Z" 混在一起按字符串排序会错位。
                        published_at=_utc_from_day(day),
                        lang=spec.lang,
                        license=self.license,
                        extra={"feed": spec.sitename, "summary": ""},
                    )
                )

            if not bucket:
                print(f"  [{self.name}] 跳过 {key}: 页面里没有带日期的条目（模板可能变了）")
                continue
            print(f"  [{self.name}] {key}: {len(bucket)} 条")
            stubs.extend(bucket)

        # 同一列表页里越靠前越新，但跨站点要按时间统一排序
        stubs.sort(key=lambda s: s.published_at or "", reverse=True)
        return stubs