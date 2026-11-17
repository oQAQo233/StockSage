"""报告生成：把证据包渲染成一份带引用的 Markdown 报告。

M2 的生产顺序刻意不是配置顺序：

    背景 → 关键主体 → 时间线 → 趋势推断 → **摘要**

摘要最后写，因为它要概括前面的正文。先写摘要再写正文，等于让模型在不知道
后面会写什么的情况下先下结论，两者必然打架。

三类内容用不同的生成方式，这是刻意的：

| 内容 | 谁写 | 理由 |
| --- | --- | --- |
| 摘要、背景、趋势 | 模型 | 需要归纳与行文 |
| 关键主体、时间线 | 模板 | 事实清单和日期，模板比模型更准，也便于核对 |

引用体系：全报告共用一套 `[n]`，编号对应来源表。"事实与推断"用节来分开——
趋势节不带引用，且在开头声明是推断。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from . import llm_client
from .config import CONFIGS

DEFAULT_CONFIG = CONFIGS / "report_sections.json"

_CITE = re.compile(r"\[(\d+)\]")
_ITEM = re.compile(r"^(?:- |\d+\. |### )")
#: 只回查带单位或足够长的数字："5 篇"这种不会因为单位缺失就误报。
_NUMBER = re.compile(r"\d+(?:\.\d+)?(?:%|万|亿|百万|千亿|bp)|(?<!\d)\d{4,}(?!\d)")


def load_config(path: Path | None = None) -> dict:
    p = Path(path) if path else DEFAULT_CONFIG
    if not p.exists():
        raise SystemExit(f"找不到报告配置：{p}")
    cfg = json.loads(p.read_text(encoding="utf-8"))
    cfg.setdefault("sections", [])
    return cfg


def _now() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")


# ---------------------------------------------------------------- 证据整理


def _sources(pack: dict) -> list[dict]:
    """来源表：按发布时间倒序，编号从 1 开始。"""
    arts = sorted(pack.get("candidates") or [], key=lambda a: a.get("published_at") or "", reverse=True)
    return [dict(art, n=i) for i, art in enumerate(arts, start=1)]


def _src_of(article_id: str, sources: list[dict]) -> int:
    for s in sources:
        if s["article_id"] == article_id:
            return s["n"]
    return 0


def facts_block(pack: dict, sources: list[dict], limit: int = 40) -> str:
    lines, seen = [], set()
    for fact in pack.get("facts") or []:
        key = (fact["subject"], fact["predicate"], fact["object"])
        if key in seen:
            continue
        seen.add(key)
        n = _src_of(fact["article_id"], sources)
        lines.append(f"- {fact['subject']} —[{fact['predicate']}]→ {fact['object']}" + (f" [{n}]" if n else ""))
        if len(lines) >= limit:
            break
    return "\n".join(lines)


def paragraphs_block(
    pack: dict, sources: list[dict], per_article: int = 2, width: int = 320, max_total: int = 12
) -> str:
    """原文摘录：按文章顺序取，段落去重（同一段常被不同文章抄）。"""
    chunks, seen = [], set()
    by_id = {s["article_id"]: s for s in sources}
    for article_id, paras in (pack.get("paragraphs") or {}).items():
        src = by_id.get(article_id)
        if not src:
            continue
        for para in paras[:per_article]:
            text = re.sub(r"\s+", " ", para["text"]).strip()[:width]
            key = text[:40]
            if text and key not in seen:
                seen.add(key)
                chunks.append(f"[{src['n']}] {src['title']}：{text}")
            if len(chunks) >= max_total:
                return "\n".join(chunks)
    return "\n".join(chunks)


def _seeds_text(pack: dict) -> str:
    seeds = pack.get("seeds") or []
    return "、".join(f"{s['name']}（{s['via']}）" for s in seeds) if seeds else "（未链接到图上实体）"


def _window_text(pack: dict) -> str:
    w = pack.get("time_window")
    if not w:
        return "不限"
    unit = {"days": "天", "weeks": "周", "months": "个月"}.get(w["unit"], w["unit"])
    return f"最近 {w['value']} {unit}"


def _events(pack: dict) -> list[dict]:
    return pack.get("events") or []


def _facts_by_subject(pack: dict, sources: list[dict]) -> list[tuple[str, list[str]]]:
    """事实按主体分组，组内保序，组间按条数降序。"""
    groups: dict[str, list[str]] = {}
    seen = set()
    for fact in pack.get("facts") or []:
        key = (fact["subject"], fact["predicate"], fact["object"])
        if key in seen:
            continue
        seen.add(key)
        n = _src_of(fact["article_id"], sources)
        line = f"- {fact['subject']} —[{fact['predicate']}]→ {fact['object']}" + (f" [{n}]" if n else "")
        groups.setdefault(fact["subject"], []).append(line)
    return sorted(groups.items(), key=lambda kv: len(kv[1]), reverse=True)


# ---------------------------------------------------------------- 各节


def _llm(system: str, user: str, max_tokens: int = 1600) -> str:
    return llm_client.chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=max_tokens,
        temperature=0.2,
    )


def section_background(pack: dict, sec: dict, sources: list[dict], use_llm: bool) -> str:
    """背景：用文本路的段落讲"现在是什么情况"。"""
    excerpts = paragraphs_block(pack, sources, per_article=2, max_total=10)
    if not excerpts:
        return "（没有可用于撰写背景的原文段落。）"
    if not (use_llm and llm_client.configured()):
        return "（未调用模型，背景节只列原文摘录。）\n\n" + excerpts

    system = (
        "你是科技行业研究员，正在写报告的「背景与现状」一节。"
        "只能使用给定的原文摘录，不得引入外部知识。每句话末尾用 [n] 标注引用编号。"
        "只写摘录里确实存在的信息：谁、做了什么、什么时间、什么数字。不要预测、不要评价。"
        "不要逐项罗列「哪些方面没有覆盖」——那是噪声。整节最多用一句话说明材料不足之处。"
        "如果摘录几乎不相关，就只写一句「现有报道不足以描述背景」，不要硬凑。"
    )
    user = (
        f"查询：{pack['query']}\n"
        f"原文摘录（[编号] 标题：正文）：\n{excerpts}\n\n"
        f"请写 {sec.get('words', 600)} 字以内的背景：把摘录里的事实按逻辑串成 2~3 段"
        f"连贯叙述，说明事情是怎么走到今天的。不要小标题，不要分点。"
    )
    return _llm(system, user, max_tokens=1800)


def section_players(pack: dict, sec: dict, sources: list[dict]) -> str:
    """关键主体：模板输出。事实和编号不该让模型复述一遍。"""
    groups = _facts_by_subject(pack, sources)
    if not groups:
        return "（图路没有抽到相关事实。）"
    limit = int(sec.get("max_subjects", 5))
    per = int(sec.get("facts_per_subject", 4))
    out = []
    for subject, lines in groups[:limit]:
        out.append(f"### {subject}")
        out.extend(lines[:per])
        if len(lines) > per:
            out.append(f"- （另有 {len(lines) - per} 条关系未展开）")
        out.append("")
    if len(groups) > limit:
        out.append(f"（另有 {len(groups) - limit} 个主体未展开。）")
    return "\n".join(out).strip()


def section_timeline(pack: dict, sec: dict, sources: list[dict]) -> str:
    """时间线：按事件簇排，多源事件只出现一次并列出全部来源。"""
    events = _events(pack)
    if not events:
        return "（没有可用于排时间线的事件。）"
    rows = []
    for e in sorted(events, key=lambda x: (x.get("first_seen") or "", -x["size"])):
        date = (e.get("first_seen") or "")[:10]
        nums = sorted({_src_of(a, sources) for a in e["article_ids"]} - {0})
        cites = "".join(f"[{n}]" for n in nums)
        multi = f"（{e['size']} 篇报道）" if e["size"] > 1 else ""
        rows.append(f"- **{date}** · {e['title']}{multi} {cites}".rstrip())
    return "\n".join(rows)


def section_outlook(pack: dict, sec: dict, sources: list[dict], use_llm: bool) -> str:
    """趋势与不确定性：唯一允许推断的节，明确声明且不带引用。"""
    head = "> 本节为推断，不是报道中的事实。依据是上文的事实与时间线，不构成投资建议。\n"
    if not (use_llm and llm_client.configured()):
        return head + "\n（未调用模型，推断节跳过。）"

    facts = facts_block(pack, sources, limit=25)
    events = "\n".join(f"- {e['title']}" for e in _events(pack)[:8])
    system = (
        "你是科技行业研究员。基于给定的事实清单与事件列表，写出趋势判断与不确定性。"
        "规则：① 不要写 [n] 这类引用编号，本节是推断；② 不得编造数字、时间、人名；"
        "③ 每一条判断都要能追溯到上面某条事实或事件；④ 明确写出哪些方面证据不足。"
        "输出中文，3~4 条要点，不要小标题。"
    )
    user = (
        f"查询：{pack['query']}\n\n事实清单：\n{facts or '（无）'}\n\n"
        f"事件：\n{events or '（无）'}\n\n"
        f"请写 {sec.get('words', 450)} 字左右：接下来可能发生什么、哪些信号值得跟踪、"
        "哪些结论目前证据不足。"
    )
    return head + "\n" + _llm(system, user, max_tokens=1400)


def section_summary(pack: dict, sec: dict, sources: list[dict], body: str, use_llm: bool) -> str:
    """摘要：最后写，读的是已经写好的正文。"""
    if not (use_llm and llm_client.configured()):
        return _fallback_summary(pack, sources)

    system = (
        "你是报告的责任编辑。基于已经写好的正文，提炼一段摘要。"
        "不得引入正文之外的信息，不得出现正文里没有的数字。"
        "每句话末尾用 [n] 标注引用，编号只能取自正文中出现过的来源编号。"
        "只写有证据的内容；不要罗列「未覆盖」的方面，也不要复述正文里的元叙述。"
        "输出中文，一段话或 3 条要点，不要标题。"
    )
    user = (
        f"查询：{pack['query']}\n"
        f"已写好的正文（截断）：\n{body[:6000]}\n\n"
        f"请写 {sec.get('words', 320)} 字左右的摘要：结论先行，点出最关键的两三件事。"
    )
    return _llm(system, user, max_tokens=1200)


def _fallback_summary(pack: dict, sources: list[dict]) -> str:
    if not sources:
        return "本次检索没有找到相关报道，无法生成摘要。"
    n_facts = len({(f["subject"], f["predicate"], f["object"]) for f in pack.get("facts") or []})
    lines = [
        f"共检索到 {len(sources)} 篇相关报道、{len(_events(pack))} 个事件，"
        f"图谱命中实体：{_seeds_text(pack)}，涉及 {n_facts} 条事实关系。"
    ]
    for fact in (pack.get("facts") or [])[:5]:
        n = _src_of(fact["article_id"], sources)
        lines.append(f"- {fact['subject']} —[{fact['predicate']}]→ {fact['object']}" + (f" [{n}]" if n else ""))
    lines.append("（当前为规则摘要：未配置模型或模型不可用。）")
    return "\n".join(lines)


def sources_section(sources: list[dict]) -> str:
    if not sources:
        return "（无）"
    return "\n".join(
        f"{s['n']}. {s['title']} · {s['source']} · {(s.get('published_at') or '')[:10]} · {s['url']}"
        for s in sources
    )


# ---------------------------------------------------------------- 校验


def audit(text: str, sources: list[dict], haystack: str) -> dict:
    """统计引用与数字：不做改写，只把风险摊出来。

    - cited：有效引用次数；out_of_range：超出来源表的编号
    - uncited：没带引用的句子数（只对声明要引用的节统计）
    - unsupported：正文里出现、但在证据原文里找不到的数字
    """
    total_n = len(sources)
    nums = [int(n) for n in _CITE.findall(text)]
    out_of_range = sorted({n for n in nums if not 1 <= n <= total_n})
    sentences = [s for s in re.split(r"(?<=[。！？!?])", text) if s.strip()]
    uncited = sum(1 for s in sentences if not _CITE.search(s))

    # 数字写法不统一：事实里是 "18 percent"，正文写 "18%"。先把证据侧归一，
    # 否则每次都会误报"证据里找不到"。
    normalized = haystack.replace(" percent", "%").replace(" percent", "%").replace(" ", "")
    unsupported = [
        tok for tok in set(_NUMBER.findall(text))
        if tok not in haystack and tok.replace(" ", "") not in normalized
    ]

    return {
        "cited": len([n for n in nums if 1 <= n <= total_n]),
        "uncited": uncited,
        "out_of_range": out_of_range,
        "unsupported": sorted(unsupported)[:6],
    }


def _tidy_lists(text: str) -> str:
    """列表/小标题前补空行，只在"正文 → 块"的过渡处补。"""
    out: list[str] = []
    prev_block = False
    for line in text.split("\n"):
        is_block = bool(_ITEM.match(line))
        if is_block and not prev_block and out and out[-1].strip():
            out.append("")
        out.append(line)
        prev_block = is_block
    return "\n".join(out)


# ---------------------------------------------------------------- 渲染


def render(
    pack: dict,
    cfg: dict | None = None,
    use_llm: bool = True,
    debug: bool = False,
) -> tuple[str, dict]:
    """渲染整份报告，返回 (markdown, 统计)。"""
    cfg = cfg or load_config()
    sources = _sources(pack)
    stats: dict = {"sections": {}}

    head = [
        f"# {pack['query'].strip()}",
        "",
        f"> 生成：{_now()}　｜　入选 {len(sources)} 篇 / {len(_events(pack))} 个事件　｜　"
        f"图谱事实 {len({(f['subject'], f['predicate'], f['object']) for f in pack.get('facts') or []})} 条",
    ]
    if pack.get("coverage") == "text":
        head.append("> 注意：图谱没有命中实体，以下内容仅来自全文关键词匹配，事实关系未经图校验。")
    head.append(f"> 查询解析：实体 {_seeds_text(pack)}　｜　时间窗 {_window_text(pack)}")
    head.append("")

    sections_cfg = [s for s in cfg.get("sections", []) if s.get("enabled")]
    produced: list[tuple[dict, str]] = []

    # 摘要以外的节先写，摘要最后生成（它要概括前面写好的正文）
    for sec in sections_cfg:
        if sec.get("selector") == "top_facts":
            continue
        produced.append((sec, _build(sec, pack, sources, use_llm)))

    body_text = "\n\n".join(b for _, b in produced)
    for sec in sections_cfg:
        if sec.get("selector") == "top_facts":
            produced.insert(0, (sec, section_summary(pack, sec, sources, body_text, use_llm)))

    haystack = body_text + "\n" + " ".join(
        p["text"] for paras in (pack.get("paragraphs") or {}).values() for p in paras
    )
    blocks = []
    for sec, body in produced:
        body = _tidy_lists(body)
        # 只校验模型写的内容：规则摘要和原文摘录本来就是清单/引文，
        # 拿"逐句带引用"去量它们只会造出一堆无意义的告警。
        if sec.get("cite") and use_llm and llm_client.configured():
            stats["sections"][sec["id"]] = audit(body, sources, haystack)
        blocks.append(f"## {sec['title']}\n\n{body}\n")

    if debug:
        blocks.append(_debug_section(pack, sources, stats))

    md = "\n".join(head) + "\n" + "\n".join(blocks)
    return md.rstrip() + "\n", stats


def _build(sec: dict, pack: dict, sources: list[dict], use_llm: bool) -> str:
    selector = sec.get("selector")
    if selector == "text_hits":
        return section_background(pack, sec, sources, use_llm)
    if selector == "graph_clusters":
        return section_players(pack, sec, sources)
    if selector == "chrono":
        return section_timeline(pack, sec, sources)
    if selector == "outlook":
        return section_outlook(pack, sec, sources, use_llm)
    if selector == "sources_all":
        return sources_section(sources)
    return f"（选择器 {selector} 尚未实现）"


def _debug_section(pack: dict, sources: list[dict], stats: dict) -> str:
    rows = ["| # | 文章 | 图路 | 文本 | 融合分 | 命中实体 |", "| --- | --- | --- | --- | --- | --- |"]
    for i, c in enumerate(sources, start=1):
        rows.append(
            f"| {i} | {c['title'][:26]} | {c.get('graph_rank') or '—'} | "
            f"{c.get('text_rank') or '—'} | {c.get('score')} | "
            f"{'、'.join(c.get('graph_hits') or []) or '—'} |"
        )
    idx = pack.get("index") or {}
    out = ["## 附录：检索与校验", "", "\n".join(rows), ""]

    if _events(pack):
        out.append("事件簇：")
        for e in _events(pack):
            nums = sorted({_src_of(a, sources) for a in e["article_ids"]} - {0})
            out.append(f"- {e['title'][:40]}（{e['size']} 篇，来源 {','.join(map(str, nums))}）")
        out.append("")

    if stats.get("sections"):
        out.append("引用与数字校验：")
        for sid, s in stats["sections"].items():
            flag = ""
            if s["out_of_range"]:
                flag += f"，越界引用 {s['out_of_range']}"
            if s["unsupported"]:
                flag += f"，证据里找不到的数字 {s['unsupported']}"
            out.append(f"- {sid}：引用 {s['cited']} 处，无引用句 {s['uncited']} 句{flag}")
        out.append("")

    out.append(f"索引：{idx.get('articles_indexed', 0)} 篇 / {idx.get('paragraphs', 0)} 段。")
    return "\n".join(out)


