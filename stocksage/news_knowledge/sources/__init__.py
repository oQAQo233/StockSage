"""新闻源适配器。

每个源只需要实现 discover()，返回 ArticleStub 列表；
「怎么抓正文」「怎么转格式」由上层统一处理，源之间不重复实现。

`--source` 支持三种写法：
- 单个源：`--source eastmoney`
- 逗号分隔：`--source eastmoney,ths`
- 预设：`--source cn-finance`（中文财经四源合集）/ `--source all`（全部网络源）
"""

from __future__ import annotations

from .base import Source
from .cn_finance import EastmoneyNews, EastmoneyRss, SinaFinance, ThsNews
from .filters import FilteredSource, is_stock_related, within_window
from .fixtures import FixtureSource
from .gdelt import GdeltSource
from .guardian import GuardianSource
from .rss import RssSource

__all__ = [
    "Source",
    "FixtureSource",
    "GdeltSource",
    "GuardianSource",
    "RssSource",
    "EastmoneyRss",
    "EastmoneyNews",
    "ThsNews",
    "SinaFinance",
    "MultiSource",
    "FilteredSource",
    "build_source",
    "is_stock_related",
    "within_window",
]

#: 中文财经四源。按「全文文章优先」排序：东方财富 RSS 给整篇文章，
#: 后三个给快讯/短讯，正文短，抽三元组的效果差一截。
CN_FINANCE = ["eastmoney-rss", "eastmoney", "ths", "sina-fin"]

_REGISTRY = {
    # 国际
    "gdelt": GdeltSource,
    "guardian": GuardianSource,
    "rss": RssSource,
    # 中文财经
    "eastmoney-rss": EastmoneyRss,
    "eastmoney": EastmoneyNews,
    "ths": ThsNews,
    "sina-fin": SinaFinance,
    # 离线
    "fixtures": FixtureSource,
    "fixture": FixtureSource,
    "offline": FixtureSource,
}


class MultiSource:
    """把多个源合并成一个：分别发现，按 URL 去重，再按发布时间倒序取前 N 条。

    这是「多源」的落地方式——一次运行同时覆盖多家发行人，
    入库时 `articles.source` 仍记录真实来源，所以事后能按源统计。
    """

    license = "各源版权归原站点"

    def __init__(self, sources: list, label: str = "multi"):
        self.sources = sources
        self.name = label

    def discover(self, query: str | None, limit: int, days: int):
        from ..dedup import canonicalize_url

        # 每个源先取「平分额度」但至少 5 条，避免某个源把额度吃光
        per = max(limit // max(len(self.sources), 1), 5)
        stubs = []
        seen: set[str] = set()

        for src in self.sources:
            try:
                got = src.discover(query, per, days)
            except SystemExit as exc:  # 缺 key 之类
                print(f"  [multi] {src.name} 跳过：{exc}")
                continue
            except Exception as exc:  # noqa: BLE001  单个源挂掉不影响整体
                print(f"  [multi] {src.name} 失败：{type(exc).__name__}: {str(exc)[:80]}")
                continue
            print(f"  [multi] {src.name}: {len(got)} 条")
            for stub in got:
                key = canonicalize_url(stub.url) or stub.url
                if key in seen:
                    continue
                seen.add(key)
                stubs.append(stub)

        # ISO 8601 UTC 字符串可直接按字典序比较；没有时间的排最后
        stubs.sort(key=lambda s: s.published_at or "", reverse=True)
        return stubs[:limit]


def build_source(name: str, **kwargs):
    """按名字构造源；支持逗号分隔与预设名，名字大小写不敏感。"""
    raw = (name or "").strip()
    if not raw:
        raise SystemExit("--source 不能为空")

    parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    if parts == ["all"]:
        parts = ["gdelt", "guardian", "rss", *CN_FINANCE]
    elif parts == ["cn-finance"] or parts == ["cn"]:
        parts = list(CN_FINANCE)

    if len(parts) == 1:
        key = parts[0]
        try:
            cls = _REGISTRY[key]
        except KeyError:
            raise SystemExit(
                f"未知新闻源 {key!r}，可选：{', '.join(sorted(_REGISTRY))}"
                "，或预设 cn-finance / all"
            ) from None
        return cls(**kwargs)

    built = []
    for key in parts:
        try:
            cls = _REGISTRY[key]
        except KeyError:
            raise SystemExit(
                f"未知新闻源 {key!r}，可选：{', '.join(sorted(_REGISTRY))}"
            ) from None
        built.append(cls(**kwargs))
    return MultiSource(built, label=",".join(parts))
