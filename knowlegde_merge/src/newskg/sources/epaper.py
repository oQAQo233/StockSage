"""电子报（方正 CMS）按天归档：URL 里带日期，天然支持回溯到任意一天。

为什么走电子报而不是频道列表：本项目的目标窗口是「11-04 起 8 天」，而中文新闻站的
频道列表页**几乎全是前端 JS 翻页**（实测：澎湃、IT之家、新浪科技滚动的 page 参数
对服务端 HTML 无效，返回的始终是同一页；人民网、证券日报首页的 sitemap 只留今天和
昨天）。也就是说「翻页回溯」这条路在多数站点上根本不存在。

但报纸的**电子版**是另一种结构：编辑按日出版，版面 URL 里直接写着日期，天然可以
定位到某一天，而且内容权威、正文完整、不设反爬。因此这里按电子报的版面结构取数：

- 证券日报   `epaper.zqrb.cn/html/YYYY-MM/DD/node_N.htm`
- 经济日报   `paper.ce.cn/pc/layout/YYYYMM/DD/node_NN.html`（当天有索引页列出全部版面）
- 上海证券报 `paper.cnstock.com/html/YYYY-MM/DD/node_N.htm`（版面号不连续，靠固定首页导航）

时间口径：版面页只告诉我们「哪一天的报纸」，没有钟点。这里统一记成**当天 09:00
（北京时间）**，换成 UTC 就是同日 01:00Z——这样按 UTC 统计「每天篇数」时，文章仍然
落在同一个日历日，不会像 `html_list.py` 那样用 00:00 BJT 而整体前移一天。
"""

from __future__ import annotations

import html
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit, urlunsplit

from ..models import ArticleStub
from .base import http_get

BJT = timezone(timedelta(hours=8))

#: 报纸出版时点（北京时间）。用带钟点的时间而不是 00:00，见模块说明。
PUBLISH_HOUR = 9

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _plain(raw: str) -> str:
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


def _strip_query(url: str) -> str:
    """去掉 `?div=-1` 这类跟踪参数，同一篇文章只留一种写法。"""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _decode(resp) -> str:
    """方正系的站点 utf-8 / gb18030 混用，按顺序试。"""
    raw = resp.content
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


class _EpaperBase:
    """电子报基类：子类只需给出「怎么取某一天的版面链接」和「怎么从版面里抠文章」。"""

    license = "版面与正文版权归原报社"
    sitename = ""
    #: 每次 discover 最多请求多少个版面页（防止版面号猜错时无限请求）
    MAX_PAGES = 16
    #: 版面页不给标题时，是否回抓文章页的 <title> 来补标题（见 _title_of）
    BACKFILL_TITLES = True
    #: 回填标题最多抓几篇，防止某天版面特别多时无限请求
    BACKFILL_MAX = 40

    def __init__(self, day: str = ""):
        self.day = (day or os.getenv("NEWSKG_ARCHIVE_DATE", "")).strip()

    def _target(self):
        if not self.day:
            raise SystemExit(f"[{self.name}] 按日期抓取需要指定日期：--day 2026-11-04")
        try:
            return datetime.strptime(self.day, "%Y-%m-%d")
        except ValueError:
            raise SystemExit(f"[{self.name}] 日期格式应为 YYYY-MM-DD，收到 {self.day!r}") from None

    # --- 子类实现 -------------------------------------------------------
    def _page_urls(self, day: datetime) -> list[str]:
        """返回这一天的所有版面页 URL（顺序尽量按版面先后）。"""
        raise NotImplementedError

    def _items(self, page: str, page_url: str) -> list[tuple[str, str]]:
        """从版面页里返回 [(文章绝对URL, 标题或空串)]。"""
        raise NotImplementedError

    # --- 公共流程 -------------------------------------------------------
    def _published(self, day: datetime) -> str:
        dt = day.replace(hour=PUBLISH_HOUR, tzinfo=BJT).astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    def _title_of(self, url: str) -> str:
        """版面页只有图片热区、不给标题，只能去文章页的 <title> 里取。

        代价是每篇多一次请求；换来的是**主题粗筛能按标题判分**——否则标题为空，
        关键词分永远是 0，整版金融稿会被原样收进来，把「科技」主题冲淡。
        """
        try:
            page = _decode(http_get(url))
        except Exception:  # noqa: BLE001
            return ""
        m = re.search(r"<title>(.*?)</title>", page, re.S | re.I)
        if not m:
            return ""
        title = _plain(html.unescape(m.group(1)))
        # "<title>" 里常带站点前后缀：'证券日报网-标题' / '标题--证券日报' / '标题 | 上海证券报'
        title = re.sub(r"^[\u4e00-\u9fa5A-Za-z]{2,12}(?:网|报|社|日报|证券报)[\-－—_|]\s*", "", title)
        for sep in ("--", "——", "_", "|"):
            if sep in title:
                title = title.split(sep)[0]
        return title.strip()

    def discover(self, query: str | None, limit: int, days: int) -> list[ArticleStub]:
        day = self._target()
        published = self._published(day)
        stubs: list[ArticleStub] = []
        seen: set[str] = set()

        try:
            pages = self._page_urls(day)
        except Exception as exc:  # noqa: BLE001
            print(f"  [{self.name}] 取版面列表失败：{type(exc).__name__}: {str(exc)[:70]}")
            return []

        for page_url in pages[: self.MAX_PAGES]:
            try:
                page = _decode(http_get(page_url))
            except Exception as exc:  # noqa: BLE001
                print(f"  [{self.name}] 跳过版面 {page_url[-24:]}: {type(exc).__name__}")
                continue

            for raw_url, raw_title in self._items(page, page_url):
                url = _strip_query(urljoin(page_url, raw_url))
                if url in seen:
                    continue
                title = _plain(raw_title)
                if query and query.lower() not in title.lower():
                    continue
                seen.add(url)
                stubs.append(
                    ArticleStub(
                        source=self.name,
                        url=url,
                        # 版面页多数只给图片热区、不给标题；空标题会在入库时
                        # 由文章页的 <title>/<h1> 回填（见 pipeline.ingest）
                        title=title,
                        published_at=published,
                        lang="zh",
                        license=self.license,
                        extra={"epaper": self.sitename, "page": page_url},
                    )
                )
                if len(stubs) >= limit:
                    break
            if len(stubs) >= limit:
                break

        print(f"  [{self.name}] {day:%Y-%m-%d} 电子报：{len(stubs)} 条（{min(len(pages), self.MAX_PAGES)} 个版面）")

        if self.BACKFILL_TITLES:
            need = [s for s in stubs if not s.title][: self.BACKFILL_MAX]
            if need:
                got = 0
                for stub in need:
                    title = self._title_of(stub.url)
                    if title:
                        stub.title = title
                        got += 1
                print(f"  [{self.name}] 回填标题：{got}/{len(need)} 篇（供主题筛判分）")
                stubs = [s for s in stubs if s.title]

        # 回填后标题为空说明文章页也取不到标题，这种稿件正文多半是版面图，
        # 主题筛和正文闸都过不去，直接丢掉，省一次必失败的抓取。
        return stubs


# --------------------------------------------------------------------------
# 证券日报：版面号从 1 起连续；任一版面页的侧栏都含当天全部文章链接，
# 所以取到第一页通常就够了，多取几页只是为了兜底。
# --------------------------------------------------------------------------
# 版面页的正文链接都写在图片热区 <area href> 里，没有标题（标题靠文章页回填）。
# 不要用 `<a ...>(.*?)</a>` 配 re.S 去抓标题：版面页里 <a> 的跨度不一致，
# 会把大段 HTML 当成标题吞进去（实测抓出 "1 1 -->" 这种碎片）。
_ZQRB_ITEM = re.compile(r'<area[^>]+href="(content_\d+\.htm[^"]*)"', re.I | re.S)


class ZqrbDay(_EpaperBase):
    name = "zqrb_day"
    sitename = "证券日报"
    MAX_PAGES = 3

    def _page_urls(self, day: datetime) -> list[str]:
        base = f"http://epaper.zqrb.cn/html/{day:%Y-%m}/{day:%d}/"
        return [f"{base}node_{n}.htm" for n in range(1, 4)]

    def _items(self, page: str, page_url: str):
        return [(u, "") for u in _ZQRB_ITEM.findall(page)]


# --------------------------------------------------------------------------
# 经济日报：当天有索引页，直接给出 12 个版面；版面页里只有图片热区，
# 文章链接写在 <Area href>，标题靠文章页回填。
# --------------------------------------------------------------------------
_CE_NODE = re.compile(r'href="(node_\d+\.html)"', re.I)
_CE_ITEM = re.compile(r'<area[^>]+href="([^"]*content_\d+\.html)"', re.I | re.S)


class CeDay(_EpaperBase):
    name = "ce_day"
    sitename = "经济日报"
    MAX_PAGES = 16

    def _page_urls(self, day: datetime) -> list[str]:
        index = f"http://paper.ce.cn/pc/layout/{day:%Y%m}/{day:%d}/"
        page = _decode(http_get(index))
        nodes = list(dict.fromkeys(_CE_NODE.findall(page)))
        if not nodes:  # 索引页没有就退化成猜版面号
            nodes = [f"node_{n:02d}.html" for n in range(1, 13)]
        return [urljoin(index, n) for n in nodes]

    def _items(self, page: str, page_url: str):
        items = _CE_ITEM.findall(page)
        if items:
            return [(u, "") for u in items]
        # 有的版面改成普通 <a> 列表
        return [(u, t) for u, t in re.findall(r'<a[^>]+href="([^"]*content_\d+\.html)"[^>]*>(.*?)</a>', page, re.S | re.I)]


# --------------------------------------------------------------------------
# 上海证券报：版面号不连续（node_700 = 1 版封面），但任意版面页都带完整导航，
# 所以固定先从 node_700 进，再从导航里拿全部版面。
# --------------------------------------------------------------------------
_CNSTOCK_NAV = re.compile(r'href="(node_\d+\.htm)"', re.I)
_CNSTOCK_ITEM = re.compile(r'<h3>\s*<a[^>]+href="(content_\d+\.htm)"[^>]*>(.*?)</a>', re.S | re.I)


class CnstockDay(_EpaperBase):
    name = "cnstock_day"
    sitename = "上海证券报"
    MAX_PAGES = 14

    def _page_urls(self, day: datetime) -> list[str]:
        base = f"https://paper.cnstock.com/html/{day:%Y-%m}/{day:%d}/"
        first = f"{base}node_700.htm"
        try:
            page = _decode(http_get(first))
        except Exception:  # noqa: BLE001
            return [first]
        nodes = list(dict.fromkeys(_CNSTOCK_NAV.findall(page)))
        urls = [first] + [urljoin(base, n) for n in nodes]
        # 去重但保持顺序
        return list(dict.fromkeys(urls))

    def _items(self, page: str, page_url: str):
        return _CNSTOCK_ITEM.findall(page)


__all__ = ["ZqrbDay", "CeDay", "CnstockDay"]