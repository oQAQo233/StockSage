"""建图后端。"""

from __future__ import annotations

__all__ = ["KGGenExtractor", "LLMExtractor", "RuleExtractor", "build_extractor"]


def __getattr__(name):
    # 规则后端不应因包初始化而强制安装 requests / KGGen。
    from importlib import import_module
    modules = {"RuleExtractor": ".rule", "LLMExtractor": ".llm", "KGGenExtractor": ".kggen"}
    if name not in modules:
        raise AttributeError(name)
    value = getattr(import_module(modules[name], __name__), name)
    globals()[name] = value
    return value


def build_extractor(name: str):
    from importlib import import_module
    table = {
        "rule": (".rule", "RuleExtractor"),
        "llm": (".llm", "LLMExtractor"),
        "kggen": (".kggen", "KGGenExtractor"),
        "kg-gen": (".kggen", "KGGenExtractor"),
    }
    try:
        module, cls = table[name.lower()]
    except KeyError:
        raise SystemExit(f"未知抽取后端 {name!r}，可选：rule / llm / kggen") from None
    return getattr(import_module(module, __name__), cls)()
