"""建图后端。

两个后端：
- `kggen`（默认）：KGGen，自带实体聚类，一篇两次大请求，质量最好但端点不稳时会挂住。
- `llm-direct`：一次小请求拿三元组，稳、快，没有聚类，靠 `kg/entities.py` 规则归一。

早期还有 `rule`（正则基线），抽取方案定下来后删掉了。
用 NEWSKG_BACKEND 或 cli 的 `--backend` 选后端；台账里记 extractor 名，两种产出可以分开统计。
"""

from __future__ import annotations

import os

from .direct import DirectExtractor
from .kggen import KGGenExtractor

__all__ = ["KGGenExtractor", "DirectExtractor", "build_extractor"]


def build_extractor(kind: str | None = None):
    """抽取器工厂。保留这层是为了让 cli / pipeline 只依赖工厂，不依赖具体类。"""
    choice = (kind or os.getenv("NEWSKG_BACKEND", "kggen")).strip().lower()
    if choice in ("direct", "llm-direct", "llm"):
        return DirectExtractor()
    return KGGenExtractor()