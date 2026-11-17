"""实体归一化：同一实体不同写法合并成一个规范名。

为什么要单独一层：抽取器（尤其 LLM）每篇独立跑，同一实体在不同文章里会写成
`Capcom` / `卡普空`、`GTA VI` / `Grand Theft Auto VI`、`NVIDIA` / `英伟达`。
不做归一，图里就是一个实体几个节点，度数被切碎、跨文章共现永远统计不出来。

归一化分三步，**顺序不能颠倒**：
1. 字符层：NFKC 全半角、去首尾空白、去包裹的书名号/引号、压缩内部空白；
2. 别名层：查 `data/aliases.json`（作者维护）→ 规范名；
3. 过滤层：丢掉宾语是泛化类别的边（`X uses AI`），以及归一后退化成自环的边。

别名表故意做成**外置 JSON** 而不是代码里的常量：它是知识不是逻辑，改它不该动代码，
也不该触发一次重新测试。
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from ..models import Triple

ALIAS_PATH = Path(__file__).resolve().parents[3] / "data" / "aliases.json"

#: 宾语是这些词时整条边丢掉：`X uses AI` / `X 提供 技术` 不是可用于查询的关系，
#: 留着只会让 `AI` 变成度数最高的节点。刻意做得很短——宁可漏杀也不要错杀。
GENERIC_OBJECTS = {
    "ai", "artificial intelligence", "technology", "tech", "product", "products",
    "software", "hardware", "service", "services", "platform", "solution", "solutions",
    "人工智能", "技术", "产品", "软件", "硬件", "服务", "平台", "解决方案", "系统",
}

_WRAP = "\"'“”‘’《》「」【】()（）[]"
#: 实体名里永远不该出现的引号/括号：只剥这些，不动 ASCII 撇号（O'Brien 会被拆坏）。
_NOISE = re.compile(r"[“”‘’《》「」【】]")

_cache: dict[str, str] | None = None


def _alias_table() -> dict[str, str]:
    """读别名表；文件不存在就返回空表（功能可选，不阻塞流水线）。"""
    global _cache
    if _cache is not None:
        return _cache
    table: dict[str, str] = {}
    try:
        raw = json.loads(ALIAS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    for canonical, aliases in (raw.get("entities") or {}).items():
        table[_key(canonical)] = canonical
        for alias in aliases or []:
            table[_key(alias)] = canonical
    _cache = table
    return table


def _key(name: str) -> str:
    """查表用的键：ASCII 不区分大小写，中文原样。"""
    return name.casefold() if name.isascii() else name


def normalize_name(name: str) -> str:
    """字符层 + 别名层。不做任何语义推理（那是作者在别名表里的事）。"""
    if not name:
        return ""
    s = unicodedata.normalize("NFKC", name)
    s = s.strip().strip(_WRAP).strip()
    # 成对引号常夹在名字中间（`“梦想”号大洋钻探船`）：只剥首尾会留下半截引号，
    # 反而造出一个新实体。ASCII 撇号不在此列，否则 O'Brien 会被拆坏。
    s = _NOISE.sub("", s)
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[，,、;；。.]+$", "", s).strip()
    if not s:
        return ""
    return _alias_table().get(_key(s), s)


def is_generic(name: str) -> bool:
    return _key(normalize_name(name)) in GENERIC_OBJECTS


def canonicalize(triples: list[Triple]) -> tuple[list[Triple], dict[str, int]]:
    """返回（归一化后的三元组，被合并的别名计数）。

    只改 subject/object，谓词不动——谓词归一化是 `kg/base.py` 的职责，两件事分开放，
    出问题时能分别定位。
    """
    merged: dict[str, int] = {}
    out: list[Triple] = []
    for t in triples:
        subj = normalize_name(t.subject)
        obj = normalize_name(t.object)
        if not subj or not obj or subj == obj:
            continue
        if is_generic(obj):
            continue
        if subj != t.subject:
            merged[f"{t.subject} → {subj}"] = merged.get(f"{t.subject} → {subj}", 0) + 1
        if obj != t.object:
            merged[f"{t.object} → {obj}"] = merged.get(f"{t.object} → {obj}", 0) + 1
        out.append(
            Triple(
                subject=subj,
                predicate=t.predicate,
                object=obj,
                article_id=t.article_id,
                published_at=t.published_at,
                source=t.source,
                extractor=t.extractor,
                confidence=t.confidence,
            )
        )
    return out, merged


def reset_cache() -> None:
    """测试/热更新用：别名表改了之后不必重启进程。"""
    global _cache
    _cache = None