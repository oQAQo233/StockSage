"""新闻源适配器。

每个源只需要实现 discover()，返回 ArticleStub 列表；
「怎么抓正文」「怎么转格式」由上层统一处理，源之间不重复实现。

题材已从「中国股票」换成「科技」，语种收敛为**纯中文**（2026-11 起英文源全部移除）：
默认集合是 12 个中文科技源，其中 4 个是通讯社/报社（中新网、经济观察报、界面），
8 个是科技垂媒。中文财经四源已移除（历史实现见 git 记录）。

`--source` 支持三种写法：
- 单个源：`--source ithome`
- 逗号分隔：`--source ithome,qbitai`
- 预设：`--source tech`（全部 12 个）/ `tech-off`（官方 4 个）/ `tech-media`（垂媒 8 个）
  / `all`（连国际综合源一起）
"""

from __future__ import annotations

from .base import Source
from .filters import FilteredSource, keyword_score, within_window
from .fixtures import FixtureSource
from .gdelt import GdeltSource
from .guardian import GuardianSource
from .rss import RssSource
from .tech import (  # noqa: F401
    EN_NAMES,
    FEEDS,
    PRESETS,
    ZH_NAMES,
    TechFeed,
    make_default_preset,
    make_preset,
    make_single,
)

__all__ = [
    "Source",
    "FixtureSource",
    "GdeltSource",
    "GuardianSource",
    "RssSource",
    "TechFeed",
    "MultiSource",
    "FilteredSource",
    "build_source",
    "keyword_score",
    "within_window",
    "FEEDS",
]

#: 科技预设。`tech` 把 12 个 feed 放进**同一个适配器实例**里，
#: 这样日志与去重都只发生一次，而不是先合并 12 个源再合并一遍。
_REGISTRY: dict[str, object] = {
    # 国际综合（仍然是英文新闻，可用于对照实验）
    "gdelt": GdeltSource,
    "guardian": GuardianSource,
    "rss": RssSource,
    # 科技单源
    **{name: make_single(name) for name in FEEDS},
    # 科技预设
    "tech": make_default_preset(),
    "tech-off": make_preset("tech-off", PRESETS["tech-off"]),
    "tech-media": make_preset("tech-media", PRESETS["tech-media"]),
    # 离线
    "fixtures": FixtureSource,
    "fixture": FixtureSource,
    "offline": FixtureSource,
}

#: 预设展开 `all` 时包含哪些（科技合集 + 国际综合）
ALL_PARTS = ["gdelt", "guardian", "rss", "tech"]


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
        parts = list(ALL_PARTS)

    def make(key: str):
        try:
            factory = _REGISTRY[key]
        except KeyError:
            raise SystemExit(
                f"未知新闻源 {key!r}，可选：{', '.join(sorted(_REGISTRY))}"
                "，或预设 tech / tech-en / tech-zh / all"
            ) from None
        return factory(**kwargs) if kwargs else factory()  # type: ignore[operator]

    if len(parts) == 1:
        return make(parts[0])
    return MultiSource([make(key) for key in parts], label=",".join(parts))