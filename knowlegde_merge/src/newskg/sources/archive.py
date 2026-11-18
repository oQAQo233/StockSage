"""按日期抓归档：RSS 只保留最近两三天，回溯指定日期只能走站点的日期页。

为什么需要它：11 个源的 RSS 实测最早只到三天前（2026-11-18 抓，最早 11-16），
想覆盖「11-04 到 11-18」这 15 天，必须按天去站点拿列表页。

目前只接了中新网的「即时新闻」日页：
    https://www.chinanews.com.cn/scroll-news/2026/1104/news.shtml
它是少数能按天拿全站列表、并且每条都带分钟的站点，正好补上 RSS 抓不到的日期。
其它源要接，加一个 `_SPECS` 条目即可（列表页 → 文章链接正则 → 时间正则）。
"""

from __future__ import annotations

import html
import os
import re
from datetime import datetime, timedelta, timezone

from ..models import ArticleStub
from .base import http_get

#: 站点时间是北京时；库里统一存 UTC（末尾 Z），否则 events 里算时间差会错 8 小时
BJT = timezone(timedelta(hours=8))

#: 形如 <li>...<a href="/cj/2026/11-04/10726333.shtml">标题</a>...<div class="dd_time">11-4 23:45</div>
_ITEM = re.compile(
    r'<li>\s*<div class="dd_lm">\[<a[^>]*>([^<]*)</a>\]</div>\s*'
    r'<div class="dd_bt"><a href="([^"]+\.shtml)">([^<]{2,120})</a></div>\s*'
    r'<div class="dd_time">(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})</div>',
    re.S,
)


class ChinanewsArchive:
    """中新网按天归档。日期来自 `--day`（优先级最高）或 NEWSKG_ARCHIVE_DATE。"""

    license = "中新网版权，正文版权归原站点"
    name = "chinanews_day"

    def __init__(self, day: str = ""):
        self.day = (day or os.getenv("NEWSKG_ARCHIVE_DATE", "")).strip()

    def _target(self) -> datetime:
        if not self.day:
            raise SystemExit(
                "按日期抓取需要指定日期：--day 2026-11-04 或设置 NEWSKG_ARCHIVE_DATE"
            )
        try:
            return datetime.strptime(self.day, "%Y-%m-%d").replace(tzinfo=BJT)
        except ValueError:
            raise SystemExit(f"日期格式应为 YYYY-MM-DD，收到 {self.day!r}") from None

    def discover(self, query: str | None, limit: int, days: int) -> list[ArticleStub]:
        day = self._target()
        url = f"https://www.chinanews.com.cn/scroll-news/{day:%Y}/{day:%m%d}/news.shtml"
        resp = http_get(url)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding
        page = resp.text

        stubs: list[ArticleStub] = []
        seen: set[str] = set()
        for channel, href, title, _m, _d, hh, mm in _ITEM.findall(page):
            if href.startswith("//"):  # 协议相对链接，日页里视频稿常见
                link = "https:" + href
            elif href.startswith("http"):
                link = href
            else:
                link = "https://www.chinanews.com.cn" + href
            if link in seen:
                continue
            # 日页里会混进图片/专题页，正文抽不出东西，直接排除
            if "/tp/" in link or "/photo" in link:
                continue
            if query and query.lower() not in title.lower():
                continue
            seen.add(link)
            published = day.replace(hour=int(hh), minute=int(mm))
            stubs.append(
                ArticleStub(
                    source="chinanews_day",
                    url=link,
                    title=html.unescape(title).strip(),
                    published_at=published.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    lang="zh",
                    license=self.license,
                    extra={"archive": "chinanews", "channel": channel},
                )
            )
            if len(stubs) >= limit:
                break
        print(f"  [{self.name}] {day:%Y-%m-%d} 归档页：{len(stubs)} 条")
        return stubs


#: 爱范儿日页：一天 12 条左右，`<time datetime="2026-11-04T23:12:46+08:00">` 自带时区
_IFANR_ITEM = re.compile(
    r'<div class="js-pager-item">\s*<div class="article-item[^"]*"[^>]*data-post-id="(\d+)".*?'
    r'<h3><a href="([^"]+)"[^>]*>(.*?)</a></h3>.*?'
    r'<time datetime="([^"]+)"',
    re.S,
)


class IfanrDate:
    """爱范儿按天归档：https://www.ifanr.com/date/2026/11/04（一天一页，无分页）。"""

    license = "爱范儿版权，正文版权归原站点"
    name = "ifanr_day"

    def __init__(self, day: str = ""):
        self.day = (day or os.getenv("NEWSKG_ARCHIVE_DATE", "")).strip()

    def _target(self) -> datetime:
        if not self.day:
            raise SystemExit("按日期抓取需要指定日期：--day 2026-11-04")
        try:
            return datetime.strptime(self.day, "%Y-%m-%d")
        except ValueError:
            raise SystemExit(f"日期格式应为 YYYY-MM-DD，收到 {self.day!r}") from None

    def discover(self, query: str | None, limit: int, days: int) -> list[ArticleStub]:
        day = self._target()
        url = f"https://www.ifanr.com/date/{day:%Y/%m/%d}"
        resp = http_get(url)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding

        stubs: list[ArticleStub] = []
        seen: set[str] = set()
        for _pid, href, title, when in _IFANR_ITEM.findall(resp.text):
            if href in seen:
                continue
            if query and query.lower() not in title.lower():
                continue
            seen.add(href)
            try:
                published = datetime.fromisoformat(when).astimezone(timezone.utc)
            except ValueError:
                published = day.replace(tzinfo=bjt_of(when))
            stubs.append(
                ArticleStub(
                    source="ifanr_day",
                    url=href,
                    title=html.unescape(re.sub(r"<[^>]+>", "", title)).strip(),
                    published_at=published.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    lang="zh",
                    license=self.license,
                    extra={"archive": "ifanr"},
                )
            )
            if len(stubs) >= limit:
                break
        print(f"  [{self.name}] {day:%Y-%m-%d} 归档页：{len(stubs)} 条")
        return stubs


def bjt_of(when: str):
    """从 ISO 串里取时区偏移；取不到就按北京时间。"""
    for tail in ("+08:00", "+0800"):
        if when.endswith(tail):
            return BJT
    return BJT


__all__ = ["ChinanewsArchive", "IfanrDate"]