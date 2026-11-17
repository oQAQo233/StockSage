"""建图后端：只用 KGGen。

早期版本同时带 `rule`（正则基线）和 `llm`（手写 prompt）两个后端，用来做三方对照。
抽取方案定下来之后，这两个后端连同它们的对照实验一起删掉了——留下的只有结论：
手写 prompt 的产出量被输出预算卡在每篇 25 条上下，而 KGGen 自带实体聚类、不设这个上限。
"""

from __future__ import annotations

from .kggen import KGGenExtractor

__all__ = ["KGGenExtractor"]


def build_extractor() -> KGGenExtractor:
    """抽取器工厂。保留这层是为了让 cli / pipeline 只依赖工厂，不依赖具体类。"""
    return KGGenExtractor()