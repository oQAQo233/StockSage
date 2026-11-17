"""feedparser 条目 -> ISO 8601 UTC，并修正错标的时区。

**为什么单独一个模块**：这个转换有三处会被踩，而且三处的症状完全不同——
- `sources/tech.py`（科技池）：整源静默归零、或排序错位；
- `sources/rss.py`（通用 `--source rss`）：同样会错，但一直没人发现；
- `tools/probe_sources.py`（体检器）：如果自己写一套，就会出现「体检通过、入库失败」。

所以口径只写一份，谁要用谁 import。排序是**字符串字典序**，因此所有生产者必须输出
`...Z` 的 UTC 串，绝不能出现 `+08:00`（字典序会把 `+08:00` 排到 `Z` 之后）。
"""

from __future__ import annotations

import calendar
import re
import time
from datetime import datetime, timezone

#: 时间串里出现这些标记，说明原文自带时区，feedparser 的解释可信
TZ_MARK = re.compile(r"(?:GMT|UTC|Z\b|[+-]\d{2}:?\d{2})", re.I)

#: 比「现在」超前多少秒以内算正常（时钟漂移、预发布），超过就认为是时区标错了
FUTURE_GRACE = 3600

#: 北京时间相对 UTC 的偏移（秒）
BEIJING_OFFSET = 8 * 3600

_ISO = "%Y-%m-%dT%H:%M:%SZ"


def _fmt(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(_ISO)


def iso_from_struct(struct_time) -> str:
    """feedparser 的 `*_parsed`（按定义是 UTC struct_time）-> ISO 串。"""
    if not struct_time:
        return ""
    try:
        return _fmt(calendar.timegm(struct_time))
    except Exception:  # noqa: BLE001
        return ""


def iso_from_entry(entry, lang: str = "") -> str:
    """feedparser 条目 -> ISO 8601 UTC，修正两类错标的时区。

    **第一种：无时区的本地时间。** 中文站点的 `pubDate` 常写成
    `2026-11-17 09:24:01`（北京时间但省略偏移）。feedparser 按 UTC 解释，整体快 8 小时；
    `within_window` 里超过 `FUTURE_TOLERANCE`（6 小时）的未来时间戳会被判成解析错误丢掉
    ——表现为整源静默归零（证券日报就是这么被漏掉的）。原文无时区标记时按 +08:00 折算。

    **第二种：北京时间被标成 GMT。** InfoQ 中文的 `pubDate` 是
    `Tue, 17 Nov 2026 18:53:07 GMT`，而那一刻北京时间才 18:53——格式对、时区错。
    这种错误从字符串本身看不出来，只能靠「新闻不会来自未来」反推：折算后仍明显超前，
    就再按 +08:00 纠一次。

    两种修正都只对「中文源 + 无时区标记」和「任何源 + 仍超前」两件事生效，所以把
    英文源原样传进来是安全的。
    """
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return ""
    ts = calendar.timegm(parsed)
    raw = entry.get("published") or entry.get("updated") or ""
    if lang == "zh" and raw and not TZ_MARK.search(raw):
        ts -= BEIJING_OFFSET
    if ts - time.time() > FUTURE_GRACE:
        ts -= BEIJING_OFFSET
    try:
        return _fmt(ts)
    except Exception:  # noqa: BLE001
        return ""


def has_clock(iso: str) -> bool:
    """时间串是否带「钟点」——`...T00:00:00Z` 视为只有日期。

    这个判断是必要的：页面正文里抽出来的 `date` 元数据十有八九只到天，
    `_normalize_date` 会补成 `T00:00:00Z`，看起来像正常时间戳但是假的。
    """
    return len(iso) >= 19 and iso[11:19] != "00:00:00"


def pick_published(from_feed: str, from_page: str) -> str:
    """两个时间源挑一个：优先要「带钟点」的那个，其次才按来源顺序。

    实测（2026-11）：trafilatura 从正文页抽出的 `date` 常常只有日期，而 RSS 的
    `pubDate` 精确到分钟。原先的 `ext.published_at or stub.published_at` 让页面
    元数据优先，结果**整库 52 篇新闻的发布时间全是 `T00:00:00Z`**——时间窗过滤在
    入库前就做完了，所以没人发现；但按时间排序、按天统计、看「最新」全是错的。
    """
    if has_clock(from_feed):
        return from_feed
    if has_clock(from_page):
        return from_page
    return from_feed or from_page or ""