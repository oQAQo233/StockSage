"""科技题材新闻源（RSS / Atom），**只保留中文源**。

题材从「中国股票」换成「科技」后，语种也从中英双语收敛到纯中文：需求是「权威、
带时间标签的中文科技新闻」，英文源在 2026-11 全部移除。

**这里的每个源都是 2026-11 实测过的**，判据是三条，不是一条：
1. `feedparser` 能解析出条目；
2. 条目带 `pubDate`（没有时间标签，`--days` 时间窗会静默失效，等于把过滤关掉）；
3. 首条正文能被 trafilatura 抽出 >400 字符。

只过第 1 条会在抓取层全军覆没；只过 1+2 会拿到一堆抓不出正文的链接。实测被淘汰的
（详见 README §5）：新华网（300 条但零时间戳）、人民网（有日期但停在 2024/2025）、
中国政府网（正文仅 56 字符）、科技日报 / 中国科学报 / 央视网 / 光明网（RSS 已下线）。

一个适配器类管所有 feed，不为每个站点写一份：RSS 的解析细节归 feedparser，
这里只负责「把 entry 归一成 ArticleStub」以及每源失败互不牵连。
"""

from __future__ import annotations

import calendar
import io
import os

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
    # ---- 科技垂媒 ----
    "qbitai": ("https://www.qbitai.com/feed", "zh", "量子位"),
    "solidot": ("https://www.solidot.org/index.rss", "zh", "Solidot"),
    "ithome": ("https://www.ithome.com/rss/", "zh", "IT之家"),
    "ifanr": ("https://www.ifanr.com/feed", "zh", "爱范儿"),
    "infoq": ("https://www.infoq.cn/feed", "zh", "InfoQ 中文"),
    "sspai": ("https://sspai.com/feed", "zh", "少数派"),
    "tmtpost": ("https://www.tmtpost.com/rss.xml", "zh", "钛媒体"),
    "leiphone": ("https://www.leiphone.com/feed", "zh", "雷锋网"),
}

ZH_NAMES = [n for n, (_, lang, _) in FEEDS.items() if lang == "zh"]
EN_NAMES = [n for n, (_, lang, _) in FEEDS.items() if lang == "en"]

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


def _clean_summary(raw: str) -> str:
    """去 HTML 标签、压空白、截断。"""
    import re

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
        self.feed_keys = [k for k in feed_keys if k in FEEDS]

    def discover(self, query: str | None, limit: int, days: int) -> list[ArticleStub]:
        import feedparser

        # 每个 feed 分到多少条：按 feed 数摊平，再留 2 条余量给同源去重与过滤损耗。
        # 平均分配是为了让「11 个源各出一个头条」而不是「第一个源出 11 条」。
        per_feed = min(PER_FEED_CAP, max(3, -(-limit // max(len(self.feed_keys), 1)) + 2))

        buckets: list[list[ArticleStub]] = []
        seen: set[str] = set()

        for key in self.feed_keys:
            url, lang, sitename = FEEDS[key]
            try:
                resp = http_get(url)
                resp.raise_for_status()
            except Exception as exc:  # noqa: BLE001  单个 feed 挂掉不影响其它
                print(f"  [{self.name}] 跳过 {key}: {type(exc).__name__}: {str(exc)[:70]}")
                continue

            parsed = feedparser.parse(io.BytesIO(resp.content))
            entries = parsed.entries
            if not entries:
                # 200 但解析不出条目，说明拿到的是 HTML 而不是 feed
                print(f"  [{self.name}] 跳过 {key}: 不是有效 feed（{len(resp.content)} 字节）")
                continue

            bucket: list[ArticleStub] = []
            for entry in entries:
                if len(bucket) >= per_feed:
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
                        # （`tech` 预设下 11 家发行人仍各自可区分）
                        source=key,
                        url=link,
                        title=title,
                        published_at=_iso_from_struct(
                            entry.get("published_parsed") or entry.get("updated_parsed")
                        ),
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
            print(f"  [{self.name}] {key}: {len(bucket)} 条")
            buckets.append(bucket)

        # 合并：先保证「每个源各出 1 条」，再用剩余名额按时间倒序补满。
        # 只按时间排序会让高产源吃满额度——实测 11 源 80 条候选里，IT之家一天几十条
        # 能把 MIT/WIRED/IEEE/量子位 整体挤出前 20 名，多源就退化成单源了。
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


#: 预设名 -> 该预设包含哪些 feed
PRESETS: dict[str, list[str]] = {
    "tech": list(FEEDS),          # 全部 12 个（都是中文）
    "tech-off": ["chinanews_fin", "chinanews_top", "eeo", "jiemian"],  # 官方/报社
    "tech-media": ["qbitai", "solidot", "ithome", "ifanr", "infoq", "sspai", "tmtpost", "leiphone"],
}


def default_feeds() -> list[str]:
    """`NEWSKG_TECH_FEEDS` 可覆盖默认集合（逗号分隔的注册名）。"""
    raw = os.getenv("NEWSKG_TECH_FEEDS", "").strip()
    picked = [x.strip().lower() for x in raw.split(",") if x.strip()]
    return [x for x in picked if x in FEEDS] or list(FEEDS)


def make_default_preset():
    """`--source tech` 的工厂：每次构造时读一次环境变量，便于不改代码换源集合。"""
    return lambda: TechFeed("tech", default_feeds())