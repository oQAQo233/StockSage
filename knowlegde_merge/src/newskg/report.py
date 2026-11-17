"""报告生成：把证据包渲染成 Markdown 报告。

M1 只落两节（摘要 + 来源），其余节的配置已经写在 `configs/report_sections.json`
里但 `enabled=false`。这样做的原因：分节方式要等看到真实报告再定，先把
"证据 → 一节"的通路跑通，扩节时只改配置和加选择器，不动主流程。

三个刻意的约束：
1. **只写证据里有的事**：提示词要求每句话带 [n]，找不到就明说"报道未覆盖"。
2. **引用编号统一分配**：各节共用同一套 [n]，合并时不会串号。
3. **生成结果可复现**：证据包先落盘（retrieve.save_evidence），换模型重生成不影响检索。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from . import llm_client
from .config import CONFIGS

DEFAULT_CONFIG = CONFIGS / "report_sections.json"

_CITE = re.compile(r"\[(\d+)\]")


def load_config(path: Path | None = None) -> dict:
    p = Path(path) if path else DEFAULT_CONFIG
    if not p.exists():
        raise SystemExit(f"找不到报告配置：{p}")
    cfg = json.loads(p.read_text(encoding="utf-8"))
    cfg.setdefault("sections", [])
    return cfg


def _now() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")


def _sources(pack: dict) -> list[dict]:
    """来源表：按发布时间倒序，编号从 1 开始，全报告共用。"""
    arts = sorted(pack.get("candidates") or [], key=lambda a: a.get("published_at") or "", reverse=True)
    return [dict(art, n=i) for i, art in enumerate(arts, start=1)]


def _src_of(article_id: str, sources: list[dict]) -> int:
    for s in sources:
        if s["article_id"] == article_id:
            return s["n"]
    return 0


def facts_block(pack: dict, sources: list[dict], limit: int = 40) -> str:
    """事实清单：每条带来源编号，供模型引用。"""
    lines = []
    seen = set()
    for fact in pack.get("facts") or []:
        key = (fact["subject"], fact["predicate"], fact["object"])
        if key in seen:
            continue
        seen.add(key)
        n = _src_of(fact["article_id"], sources)
        tag = f" [{n}]" if n else ""
        lines.append(f"- {fact['subject']} —[{fact['predicate']}]→ {fact['object']}{tag}")
        if len(lines) >= limit:
            break
    return "\n".join(lines)


def paragraphs_block(pack: dict, sources: list[dict], per_article: int = 2, width: int = 320) -> str:
    """原文摘录：给模型上下文，避免它只对着三元组编。"""
    chunks = []
    by_id = {s["article_id"]: s for s in sources}
    for article_id, paras in (pack.get("paragraphs") or {}).items():
        src = by_id.get(article_id)
        if not src:
            continue
        for para in paras[:per_article]:
            text = re.sub(r"\s+", " ", para["text"]).strip()[:width]
            if text:
                chunks.append(f"[{src['n']}] {src['title']}：{text}")
    return "\n".join(chunks)


def _seeds_text(pack: dict) -> str:
    seeds = pack.get("seeds") or []
    if not seeds:
        return "（未链接到图上实体）"
    return "、".join(f"{s['name']}（{s['via']}）" for s in seeds)


def _window_text(pack: dict) -> str:
    w = pack.get("time_window")
    if not w:
        return "不限"
    unit = {"days": "天", "weeks": "周", "months": "个月"}.get(w["unit"], w["unit"])
    return f"最近 {w['value']} {unit}"


def summary_of(pack: dict, cfg: dict, sources: list[dict], use_llm: bool = True) -> tuple[str, dict]:
    """生成摘要节。返回 (正文, 统计信息)。"""
    facts = facts_block(pack, sources)
    excerpts = paragraphs_block(pack, sources)
    stat = {"cited": 0, "uncited": 0, "fallback": False}

    if use_llm and llm_client.configured() and (facts or excerpts):
        system = (
            "你是科技行业研究员，正在为一份内部报告写摘要。"
            "只能使用用户给出的「事实清单」和「原文摘录」，不得引入任何外部知识，"
            "不得推测数字、时间、机构立场。"
            "每句话末尾必须用 [n] 标注来源编号，n 取自事实清单或摘录前的方括号编号。"
            "如果证据不足以支撑某一点，直接写“现有报道未覆盖”，不要补全。"
            "输出中文，不要标题、不要 Markdown 列表标记以外的格式。"
        )
        user = (
            f"查询：{pack['query']}\n"
            f"图谱命中实体：{_seeds_text(pack)}\n"
            f"时间范围：{_window_text(pack)}\n"
            f"入选报道：{len(sources)} 篇\n\n"
            f"事实清单（主体—关系→客体 [来源编号]）：\n{facts or '（无）'}\n\n"
            f"原文摘录：\n{excerpts or '（无）'}\n\n"
            f"请写 {cfg.get('words', 320)} 字左右的摘要：先用两三句给出结论，"
            f"再分点列出最关键的 2~4 条事实（每条一句，带引用）。"
        )
        try:
            text = llm_client.chat(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                max_tokens=1200,
                temperature=0.2,
            )
            if text:
                text, stat = _audit_citations(text, sources)
                return text, stat
        except llm_client.LLMError as exc:
            print(f"  [report] 模型调用失败，退回规则摘要：{exc}")

    stat["fallback"] = True
    return _fallback_summary(pack, sources), stat


def _audit_citations(text: str, sources: list[dict]) -> tuple[str, dict]:
    """检查引用编号是否越界、句子有没有引用。不做改写，只统计。"""
    cited = 0
    bad = []
    total_n = len(sources)
    for n in _CITE.findall(text):
        n = int(n)
        if 1 <= n <= total_n:
            cited += 1
        else:
            bad.append(n)
    sentences = [s for s in re.split(r"(?<=[。！？!?])", text) if s.strip()]
    uncited = sum(1 for s in sentences if not _CITE.search(s))
    if bad:
        text += f"\n\n> 注意：出现了越界引用 {sorted(set(bad))}，来源表只有 {total_n} 条。"
    return text, {"cited": cited, "uncited": uncited, "fallback": False}


def _fallback_summary(pack: dict, sources: list[dict]) -> str:
    """没有模型时的确定性摘要：把命中的东西如实列出来，不假装会写作。"""
    if not sources:
        return "本次检索没有找到相关报道，无法生成摘要。可以换更具体的说法，或扩大时间范围。"
    seeds = _seeds_text(pack)
    n_facts = len({(f["subject"], f["predicate"], f["object"]) for f in pack.get("facts") or []})
    lines = [
        f"共检索到 {len(sources)} 篇相关报道，图谱命中实体：{seeds}，"
        f"涉及 {n_facts} 条事实关系。"
    ]
    for fact in (pack.get("facts") or [])[:5]:
        n = _src_of(fact["article_id"], sources)
        tag = f" [{n}]" if n else ""
        lines.append(f"- {fact['subject']} —[{fact['predicate']}]→ {fact['object']}{tag}")
    lines.append("（当前为规则摘要：未配置模型或模型不可用，只做事实罗列，未做归纳。）")
    return "\n".join(lines)


def sources_section(sources: list[dict]) -> str:
    if not sources:
        return "（无）"
    lines = []
    for s in sources:
        date = (s.get("published_at") or "")[:10]
        lines.append(f"{s['n']}. {s['title']} · {s['source']} · {date} · {s['url']}")
    return "\n".join(lines)


def render(
    pack: dict,
    cfg: dict | None = None,
    use_llm: bool = True,
    debug: bool = False,
) -> tuple[str, dict]:
    """渲染整份报告，返回 (markdown, 统计)。"""
    cfg = cfg or load_config()
    sources = _sources(pack)
    sections: list[str] = []
    stats: dict = {}

    title = pack["query"].strip()
    head = [
        f"# {title}",
        "",
        f"> 生成：{_now()}　｜　检索：图路 + 文本路（RRF 融合）　｜　"
        f"入选 {len(sources)} 篇　｜　图谱事实 "
        f"{len({(f['subject'], f['predicate'], f['object']) for f in pack.get('facts') or []})} 条",
    ]
    if pack.get("coverage") == "text":
        head.append(
            "> 注意：图谱没有命中实体，以下内容仅来自全文关键词匹配，"
            "事实关系未经图校验，引用请回原文核对。"
        )
    head.append(f"> 查询解析：实体 {_seeds_text(pack)}　｜　时间窗 {_window_text(pack)}")
    head.append("")

    for sec in cfg.get("sections", []):
        if not sec.get("enabled"):
            continue
        selector = sec.get("selector")
        body = ""
        if selector == "top_facts":
            body, stats["summary"] = summary_of(pack, sec, sources, use_llm=use_llm)
        elif selector == "sources_all":
            body = sources_section(sources)
        else:
            body = f"（选择器 {selector} 尚未实现）"
        sections.append(f"## {sec['title']}\n\n{body}\n")

    if debug:
        sections.append(_debug_section(pack))

    sections = [_tidy_lists(sec) for sec in sections]

    md = "\n".join(head) + "\n" + "\n".join(sections)
    return md.rstrip() + "\n", stats


def _tidy_lists(text: str) -> str:
    """列表前补空行，但只在"正文 → 列表"的过渡处补，不在列表项之间补。"""
    out: list[str] = []
    prev_item = False
    for line in text.split("\n"):
        is_item = bool(re.match(r"^(?:- |\d+\. )", line))
        if is_item and not prev_item and out and out[-1].strip():
            out.append("")
        out.append(line)
        prev_item = is_item
    return "\n".join(out)


def _debug_section(pack: dict) -> str:
    rows = ["| # | 文章 | 图路名次 | 文本名次 | 融合分 | 命中实体 |", "| --- | --- | --- | --- | --- | --- |"]
    for i, c in enumerate(pack.get("candidates") or [], start=1):
        rows.append(
            f"| {i} | {c['title'][:28]} | {c.get('graph_rank') or '—'} | "
            f"{c.get('text_rank') or '—'} | {c.get('score')} | "
            f"{'、'.join(c.get('graph_hits') or []) or '—'} |"
        )
    idx = pack.get("index") or {}
    return (
        "## 附录：检索过程\n\n"
        + "\n".join(rows)
        + f"\n\n索引：{idx.get('articles_indexed', 0)} 篇 / {idx.get('paragraphs', 0)} 段。"
        + "\n"
    )




