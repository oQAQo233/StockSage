"""政府/科研机构新闻列表的按日回溯：服务端分页 + 列表里带日期。

为什么补这一类源：新闻门户的频道列表几乎全部改成前端 JS 翻页（见 HANDOFF §9.4），
而**部委和科研机构的新闻列表大多还是老式服务端分页**（`index.html` / `index_1.html` …），
并且列表项上直接印着日期。这意味着「翻到第几页」真的能拿到更早的文章，可以按天回溯。

对科技题材来说这类源还有额外好处：科技部、发改委发布的是科技政策、重大专项、
产业规划，实体密度比综合新闻更高。

结构（两类站点略有差异，但都能用同一套 <li> 解析）：
- 科技部 `most.gov.cn/kjbgz/`：`<li><span class="date">2026-11-13</span><a href="./202611/t....html" title="标题">`
- 发改委 `ndrc.gov.cn/xwdt/xwfb/`：`<li><a href="./202611/t....html" title="标题">标题</a><span>2026/11/17</span></li>`
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

from ..models import ArticleStub
from .base import http_get

BJT = timezone(timedelta(hours=8))
PUBLISH_HOUR = 9

# 注意 `\b`：少了它 `<li[^>]*>` 会把 `<link href=...>` 也当成列表项吞进去
_LI = re.compile(r"<li\b[^>]*>(.*?)</li>", re.S | re.I)
_DATE = re.compile(r"(20\d{2})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})")
_LINK = re.compile(r'<a[^>]+href="([^"]+)"', re.I)
_TITLE_ATTR = re.compile(r'title="([^"]*)"', re.I)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _plain(raw: str) -> str:
    text = _TAG.sub(" ", raw or "")
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"')):
        text = text.replace(a, b)
    return _WS.sub(" ", text).strip()


def _decode(resp) -> str:
    raw = resp.content
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


class _GovListBase:
    """子类给出频道首页 URL 与分页模板，其余流程共用。"""

    name = ""
    sitename = ""
    license = "新闻内容版权归原机构"
    #: 频道首页
    INDEX = ""
    #: 第 n 页（n 从 2 起。老式 CMS 普遍是 index_1.html = 第 2 页）
    PAGE = ""
    #: 最多往回翻多少页（防止目标日太老时无限翻）
    MAX_PAGES = 12
    #: 主列表容器的 class（只在这个 <ul> 里解析条目）
    LIST_UL = ""

    def _container(self):
        return re.compile(
            r'<ul[^>]*class="[^"]*\b' + re.escape(self.LIST_UL) + r'\b[^"]*"[^>]*>(.*?)</ul>',
            re.S | re.I,
        )

    def __init__(self, day: str = ""):
        self.day = (day or os.getenv("NEWSKG_ARCHIVE_DATE", "")).strip()

    def _target(self) -> datetime:
        if not self.day:
            raise SystemExit(f"[{self.name}] 按日期抓取需要指定日期：--day 2026-11-04")
        try:
            return datetime.strptime(self.day, "%Y-%m-%d")
        except ValueError:
            raise SystemExit(f"[{self.name}] 日期格式应为 YYYY-MM-DD，收到 {self.day!r}") from None

    def _page_url(self, n: int) -> str:
        return self.INDEX if n == 1 else self.PAGE.format(n=n - 1)

    def _parse(self, page: str, page_url: str) -> list[tuple[str, str, datetime]]:
        # 只解析主列表容器：整页里还混着侧栏/页脚的旧链接，
        # 一起解析会让「本页覆盖哪几天」失真，翻页判断随之失效。
        block_match = self._container().search(page)
        if block_match:
            page = block_match.group(1)
        rows = []
        for block in _LI.findall(page):
            m_date = _DATE.search(block)
            m_link = _LINK.search(block)
            if not (m_date and m_link):
                continue
            try:
                day = datetime(int(m_date.group(1)), int(m_date.group(2)), int(m_date.group(3)))
            except ValueError:
                continue
            url = urljoin(page_url, m_link.group(1))
            m_title = _TITLE_ATTR.search(block)
            title = _plain(m_title.group(1)) if m_title else _plain(block)
            rows.append((url, title, day))
        return rows

    def discover(self, query: str | None, limit: int, days: int) -> list[ArticleStub]:
        target = self._target()
        published = target.replace(hour=PUBLISH_HOUR, tzinfo=BJT).astimezone(timezone.utc)
        published_str = published.strftime("%Y-%m-%dT%H:%M:%SZ")

        stubs: list[ArticleStub] = []
        seen: set[str] = set()
        pages_read = 0

        for n in range(1, self.MAX_PAGES + 1):
            page_url = self._page_url(n)
            try:
                page = _decode(http_get(page_url))
            except Exception as exc:  # noqa: BLE001
                print(f"  [{self.name}] 第 {n} 页取不到：{type(exc).__name__}")
                break
            pages_read += 1
            rows = self._parse(page, page_url)
            if not rows:
                break

            for url, title, day in rows:
                if day != target or url in seen:
                    continue
                if query and query.lower() not in title.lower():
                    continue
                seen.add(url)
                stubs.append(
                    ArticleStub(
                        source=self.name,
                        url=url,
                        title=title,
                        published_at=published_str,
                        lang="zh",
                        license=self.license,
                        extra={"org": self.sitename, "page": page_url},
                    )
                )
                if len(stubs) >= limit:
                    break

            # 判断是否该停：只看整页的最新日期。
            # 不用「某条比目标日更早」来停，因为页脚/侧栏常挂着几年前的旧链，会误判。
            if len(stubs) >= limit or max(day for _, _, day in rows) < target:
                break

        print(f"  [{self.name}] {target:%Y-%m-%d}：{len(stubs)} 条（翻了 {pages_read} 页）")
        return stubs


class MostDay(_GovListBase):
    """科技部 · 科技部工作。"""

    name = "most_day"
    sitename = "科技部"
    INDEX = "https://www.most.gov.cn/kjbgz/index.html"
    LIST_UL = "info_list2"
    PAGE = "https://www.most.gov.cn/kjbgz/index_{n}.html"


class NdrcDay(_GovListBase):
    """国家发改委 · 新闻发布。"""

    name = "ndrc_day"
    sitename = "国家发改委"
    INDEX = "https://www.ndrc.gov.cn/xwdt/xwfb/index.html"
    LIST_UL = "u-list"
    PAGE = "https://www.ndrc.gov.cn/xwdt/xwfb/index_{n}.html"


__all__ = ["MostDay", "NdrcDay"]
