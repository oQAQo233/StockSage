"""建图后端。"""

from __future__ import annotations

from .kggen import KGGenExtractor
from .llm import LLMExtractor
from .rule import RuleExtractor

__all__ = ["KGGenExtractor", "LLMExtractor", "RuleExtractor", "build_extractor"]


def build_extractor(name: str):
    table = {
        "rule": RuleExtractor,
        "llm": LLMExtractor,
        "kggen": KGGenExtractor,
        "kg-gen": KGGenExtractor,
    }
    try:
        cls = table[name.lower()]
    except KeyError:
        raise SystemExit(f"未知抽取后端 {name!r}，可选：rule / llm / kggen") from None
    return cls()