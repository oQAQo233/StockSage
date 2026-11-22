"""Schema 层：关系归一、实体类型、实体合并、实体消歧。

四步都只写新表，不动 `triples` / `articles`；每步都能单独重跑、单独核对。
见 PLAN_FOUR_STEPS.md。
"""

from . import ddl, llm

__all__ = ["ddl", "llm"]