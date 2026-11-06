"""离线样例源。

它不是「假数据兜底」那么简单：课程作业要在答辩现场跑，
现场网络不能保证，这个源保证整条链路（抽取 → 转换 → 建图 → 导出 → 评估）
在没有网络、没有 API key 的情况下依然完整可演示。

文章正文全部是**合成的**，用于演示管线，切勿当作真实新闻引用。
"""

from __future__ import annotations

import json

from ..config import FIXTURES
from ..models import ArticleStub
from .base import Source  # noqa: F401  (供类型检查阅读)


class FixtureSource:
    name = "fixtures"
    license = "合成样例文本，仅用于演示"

    def __init__(self, index_path=None):
        self.index_path = index_path or (FIXTURES / "index.json")

    def discover(self, query: str | None, limit: int, days: int) -> list[ArticleStub]:
        if not self.index_path.exists():
            raise SystemExit(
                f"找不到离线样例索引 {self.index_path}，请先在项目根目录运行 `python -m newskg.cli init`。"
            )
        entries = json.loads(self.index_path.read_text(encoding="utf-8"))

        stubs: list[ArticleStub] = []
        for item in entries:
            if query and query.lower() not in (item.get("title", "") + item.get("file", "")).lower():
                continue
            stubs.append(
                ArticleStub(
                    source=self.name,
                    url=item.get("url", ""),
                    title=item.get("title", ""),
                    published_at=item.get("published_at", ""),
                    lang=item.get("lang", "en"),
                    license=self.license,
                    extra={"file": item.get("file", "")},
                )
            )
        return stubs[:limit]