"""科技题材新闻源，**只保留中文源**。

题材从「中国股票」换成「科技」后，语种也从中英双语收敛到纯中文：需求是「权威、
带时间标签的中文科技新闻」，英文源在 2026-11 全部移除。

源分两种接法：
- **RSS/Atom**（`FEEDS`）：`feedparser` 解析，要求条目带 `pubDate`；
- **列表页**（`html_list.HTML_SPECS`）：新华网、科技日报这类最权威的站点 RSS 已下线，
  但频道首页仍是服务端渲染、且文章 URL 自带日期，于是从 HTML 里取「标题 + 链接」，
  日期从 URL 推出来（`/20261117/`、`/2026-11/17/`）。

**每个源都是 2026-11 实测过的**，判据是三条，不是一条：
1. 能解析出条目（feed）或条目（列表页）；
2. 条目带时间（feed 的 `pubDate`，或 URL 里的日期）——没有时间，`--days` 时间窗
   会静默失效，等于把时间过滤关掉；
3. 首条正文能被 trafilatura 抽出 >400 字符。

只过第 1 条会在抓取层全军覆没；只过 1+2 会拿到一堆抓不出正文的链接。实测被淘汰的
（详见 README §5.1）：人民网（有日期但停在 2024/2025）、中国政府网（正文仅 56 字符）、
中国科学报 / 央视网 / 光明网 / 第一财经（RSS 404）、新浪/搜狐（停在 2016–2018）。

一个适配器类管所有 feed，不为每个站点写一份：RSS 的解析细节归 feedparser，列表页的
解析细节归 html_list，这里只负责分额、去重、合并，以及每源失败互不牵连。
"""

from __future__ import annotations

import calendar
import io
import os
import re

from ..models import ArticleStub
from .base import http_get

#: 单个 feed 最多取多少条（多源合并时由上层再截断）
PER_FEED_CAP = 40

#: name -> (feed_url, lang, 站点名)
FEEDS: dict[str, tuple[str, str, str]] = {
    # ---- 权威/官方：通讯社与报社（都带 pubDate，时间窗过滤才生效）----
    "chinanews_fin": ("https://www.chinanews.com.cn/rss/finance.xml", "zh", "中新网·财经"),
    "chinanews_top": ("https://www.chinanews.com.cn/rss/scroll-news.xml", "zh", "中新网·要闻"),
    "eeo": ("http://www.eeo.com.cn/rss.xml", "zh", "经济观察报"),
    "jiemian": ("https://a.jiemian.com/index.php?m=article&a=rss", "zh", "界面新闻"),
    "zqrb": ("http://www.zqrb.cn/rss.xml", "zh", "证券日报"),
    # ---- 科技垂媒（保留专业向，剔除科普/社区向）----
    "infoq": ("https://www.infoq.cn/feed", "zh", "InfoQ 中文"),
    "ifanr": ("https://www.ifanr.com/feed", "zh", "爱范儿"),
    "tmtpost": ("https://www.tmtpost.com/rss.xml", "zh", "钛媒体"),
    "leiphone": ("https://www.leiphone.com/feed", "zh", "雷锋网"),
}

ZH_NAMES = [n for n, (_, lang, _) in FEEDS.items() if lang == "zh"]
EN_NAMES = [n for n, (_, lang, _) in FEEDS.items() if lang == "en"]

#: 没有 RSS、但有服务端渲染列表页的源（键与 FEEDS 不重名）
from .html_list import HTML_SPECS, HtmlListFeed  # noqa: E402  （放在 FEEDS 之后，避免循环导入）

HTML_NAMES = list(HTML_SPECS)

#: 全部可用源键（RSS + 列表页），顺序即分额顺序
ALL_KEYS = list(FEEDS) + HTML_NAMES

#: 抓摘要时保留的字符数：只用来给相关性打分，不需要全文
SUMMARY_CHARS = 400


def _iso_from_struct(struct_time) -> str:
    """feedparser 的 *_parsed（UTC struct_time）-> ISO 8601 UTC。"""
    if not struct_time:
        return ""
    try:
        from datetime import datetime, timezone

        ts = calendar.timegm(struct_time)  # struct_time 本身就是 UTC
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:  # noqa: BLE001
        return ""


#: 时间串里出现这些标记，说明原文自带时区，feedparser 的解释可信
_TZ_MARK = re.compile(r"(?:GMT|UTC|Z\b|[+-]\d{2}:?\d{2})", re.I)


def _iso_from_entry(entry, lang: str) -> str:
    """entry -> ISO 8601 UTC，并修正「无时区的北京本地时间」。

    中文站点的 `pubDate` 经常写成 `2026-11-17 09:24:01`（北京时间但省略偏移）。
    feedparser 按 UTC 解释，结果整体快 8 小时；`within_window` 里超过
    `FUTURE_TOLERANCE`（6 小时）的未来时间戳会被判成解析错误丢掉——表现为整源
    静默归零（证券日报就是这么被漏掉的）。所以：原文无时区标记时，中文源按 +08:00 折算。
    """
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return ""
    ts = calendar.timegm(parsed)
    raw = entry.get("published") or entry.get("updated") or ""
    if lang == "zh" and raw and not _TZ_MARK.search(raw):
        ts -= 8 * 3600
    try:
        from datetime import datetime, timezone

        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:  # noqa: BLE001
        return ""


def _clean_summary(raw: str) -> str:
    """去 HTML 标签、压空白、截断。"""
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    text = re.sub(r"\s+", " ", text).strip()
    return text[:SUMMARY_CHARS]


class TechFeed:
    """一个实例可以带多个 feed；按 URL 去重后统一返回 ArticleStub。

    `name` 只用于日志与预设标识；真正写入 `articles.source` 的是具体 feed 名，
    这样 `--source tech` 跑完之后，库里仍能按发行人分别统计。
    """

    license = "各站点 RSS 条款，正文版权归原站点"

    def __init__(self, name: str, feed_keys: list[str]):
        self.name = name
        # RSS 与列表页两种源可以混在一个实例里：前者查 FEEDS，后者查 HTML_SPECS
        self.feed_keys = [k for k in feed_keys if k in FEEDS or k in HTML_SPECS]

    def _from_rss(self, key: str, cap: int, query: str | None, seen: set[str]) -> list[ArticleStub]:
        """一个 RSS/Atom 源 -> ArticleStub 列表。"""
        import feedparser

        url, lang, sitename = FEEDS[key]
        try:
            resp = http_get(url)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001  单个 feed 挂掉不影响其它
            print(f"  [{self.name}] 跳过 {key}: {type(exc).__name__}: {str(exc)[:70]}")
            return []

        parsed = feedparser.parse(io.BytesIO(resp.content))
        entries = parsed.entries
        if not entries:
            # 200 但解析不出条目，说明拿到的是 HTML 而不是 feed
            print(f"  [{self.name}] 跳过 {key}: 不是有效 feed（{len(resp.content)} 字节）")
            return []

        bucket: list[ArticleStub] = []
        for entry in entries:
            if len(bucket) >= cap:
                break
            link = (entry.get("link") or "").strip()
            if not link or link in seen:
                continue
            title = (entry.get("title") or "").strip()
            if query and query.lower() not in title.lower():
                continue
            seen.add(link)
            bucket.append(
                ArticleStub(
                    # 用 feed 名而不是预设名：入库后按源统计才有意义
                    source=key,
                    url=link,
                    title=title,
                    published_at=_iso_from_entry(entry, lang),
                    lang=lang,
                    license=self.license,
                    extra={
                        "feed": sitename,
                        "summary": _clean_summary(
                            entry.get("summary") or entry.get("description") or ""
                        ),
                    },
                )
            )
        return bucket

    def discover(self, query: str | None, limit: int, days: int) -> list[ArticleStub]:
        # 每个 feed 分到多少条：按 feed 数摊平，再留 2 条余量给同源去重与过滤损耗。
        # 平均分配是为了让「每个源各出一个头条」而不是「第一个源出全部」。
        per_feed = min(PER_FEED_CAP, max(3, -(-limit // max(len(self.feed_keys), 1)) + 2))

        buckets: list[list[ArticleStub]] = []
        seen: set[str] = set()

        # 列表页源单独成组：它们不走 feedparser，但合并规则与 RSS 完全一致
        html_keys = [k for k in self.feed_keys if k in HTML_SPECS]
        if html_keys:
            page = HtmlListFeed(self.name, html_keys).discover(query, per_feed, days)
            buckets.append(page[:per_feed])
            seen.update(stub.url for stub in page)

        for key in self.feed_keys:
            if key in HTML_SPECS:
                continue
            bucket = self._from_rss(key, per_feed, query, seen)
            print(f"  [{self.name}] {key}: {len(bucket)} 条")
            buckets.append(bucket)

        # 合并：先保证「每个源各出 1 条」，再用剩余名额按时间倒序补满。
        # 只按时间排序会让高产源吃满额度——实测多源 80 条候选里，单日高产的那个站
        # 能把其它源整体挤出前 N 名，多源就退化成单源了。
        for bucket in buckets:
            bucket.sort(key=lambda s: s.published_at or "", reverse=True)

        head = [bucket[0] for bucket in buckets if bucket]
        head.sort(key=lambda s: s.published_at or "", reverse=True)
        taken = {id(stub) for stub in head}

        rest = [stub for bucket in buckets for stub in bucket if id(stub) not in taken]
        rest.sort(key=lambda s: s.published_at or "", reverse=True)

        return (head + rest)[:limit]


# --------------------------------------------------------------------------- #
# 工厂：注册表里放「怎么造」，而不是「造好的」
# --------------------------------------------------------------------------- #

def make_single(key: str):
    return lambda: TechFeed(key, [key])


def make_preset(name: str, keys: list[str]):
    return lambda: TechFeed(name, keys)


#: 预设名 -> 该预设包含哪些源（RSS 与列表页混排，顺序即分额顺序）
PRESETS: dict[str, list[str]] = {
    "tech": list(ALL_KEYS),  # 全部 11 个（9 个 RSS + 2 个列表页）
    # 官方/报社：通讯社、党报集团、行业报
    "tech-off": [
        "xinhua_tech",
        "chinanews_fin",
        "chinanews_top",
        "stdaily",
        "eeo",
        "jiemian",
        "zqrb",
    ],
    # 科技垂媒：专业向，已剔除科普/社区向（少数派、Solidot、IT之家、量子位）
    "tech-media": ["tmtpost", "leiphone", "infoq", "ifanr"],
}


def default_feeds() -> list[str]:
    """`NEWSKG_TECH_FEEDS` 可覆盖默认集合（逗号分隔的注册名）。"""
    raw = os.getenv("NEWSKG_TECH_FEEDS", "").strip()
    picked = [x.strip().lower() for x in raw.split(",") if x.strip()]
    return [x for x in picked if x in FEEDS or x in HTML_SPECS] or list(ALL_KEYS)


def make_default_preset():
    """`--source tech` 的工厂：每次构造时读一次环境变量，便于不改代码换源集合。"""
    return lambda: TechFeed("tech", default_feeds())

