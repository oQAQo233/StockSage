"""对照人工标注的 gold 集做评估。

两个口径，缺一不可：

- **strict**：`(主语, 谓词, 宾语)` 完全一致。衡量「连关系词都抽对了」。
- **relaxed**：只看 `(主语, 宾语)`。衡量「至少把这对实体连起来了」。

为什么要两个：规则后端的谓词来自封闭模板，LLM 的谓词是自由生成的，
只比 strict 会把「说对了同一件事、用词不同」判成错，对 LLM 不公平。
两个口径的差值本身就是一条结论——「结构化召回」和「语义召回」的差距。
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from .kg.base import normalize_predicate
from .store import connect  # noqa: F401  (部分调用方会复用连接工厂)


def norm(text: str) -> str:
    text = str(text).lower().strip()
    text = re.sub(r"^(the|a|an)\s+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .,;:'\"()[]")


def load_gold(path: Path) -> dict[str, set[tuple[str, str, str]]]:
    """读取逐篇标注：article_id,subject,predicate,object。"""
    gold: dict[str, set[tuple[str, str, str]]] = {}
    with path.open(encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            aid = (row.get("article_id") or "").strip()
            s = norm(row.get("subject", ""))
            p = normalize_predicate(row.get("predicate", ""))
            o = norm(row.get("object", ""))
            if aid and s and p and o:
                gold.setdefault(aid, set()).add((s, p, o))
    return gold


def _predicted(conn, article_id: str, extractor: str) -> set[tuple[str, str, str]]:
    rows = conn.execute(
        "SELECT subject, predicate, object FROM triples WHERE article_id = ? AND extractor = ?",
        (article_id, extractor),
    )
    return {(norm(r["subject"]), normalize_predicate(r["predicate"]), norm(r["object"])) for r in rows}


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f


def evaluate_extractor(conn, gold: dict[str, set[tuple[str, str, str]]], extractor: str) -> dict:
    """对某一个抽取器的全部产出算 micro / macro P/R/F1。"""
    per_article = []
    tp_s = fp_s = fn_s = 0
    tp_r = fp_r = fn_r = 0

    for aid in sorted(gold):
        g = gold[aid]
        pred = _predicted(conn, aid, extractor)

        g_relaxed = {(s, o) for s, p, o in g}
        p_relaxed = {(s, o) for s, p, o in pred}

        a_tp = len(g & pred)
        a_fp = len(pred - g)
        a_fn = len(g - pred)

        r_tp = len(g_relaxed & p_relaxed)
        r_fp = len(p_relaxed - g_relaxed)
        r_fn = len(g_relaxed - p_relaxed)

        tp_s += a_tp; fp_s += a_fp; fn_s += a_fn
        tp_r += r_tp; fp_r += r_fp; fn_r += r_fn

        pa = _prf(a_tp, a_fp, a_fn)
        per_article.append(
            {
                "article_id": aid,
                "gold": len(g),
                "predicted": len(pred),
                "tp": a_tp,
                "precision": round(pa[0], 4),
                "recall": round(pa[1], 4),
                "f1": round(pa[2], 4),
            }
        )

    ps, rs, fs = _prf(tp_s, fp_s, fn_s)
    pr, rr, fr = _prf(tp_r, fp_r, fn_r)

    macro_p = sum(a["precision"] for a in per_article) / len(per_article) if per_article else 0.0
    macro_r = sum(a["recall"] for a in per_article) / len(per_article) if per_article else 0.0
    macro_f = sum(a["f1"] for a in per_article) / len(per_article) if per_article else 0.0

    return {
        "extractor": extractor,
        "articles": len(per_article),
        "gold_total": tp_s + fn_s,
        "predicted_total": tp_s + fp_s,
        "strict": {
            "tp": tp_s, "fp": fp_s, "fn": fn_s,
            "precision": round(ps, 4), "recall": round(rs, 4), "f1": round(fs, 4),
        },
        "relaxed": {
            "tp": tp_r, "fp": fp_r, "fn": fn_r,
            "precision": round(pr, 4), "recall": round(rr, 4), "f1": round(fr, 4),
        },
        "macro": {
            "precision": round(macro_p, 4),
            "recall": round(macro_r, 4),
            "f1": round(macro_f, 4),
        },
        "per_article": per_article,
    }


def write_report(stats: dict, evaluation: dict | None, out_path: Path, meta: dict) -> Path:
    lines = [
        "# 新闻知识图谱构建报告",
        "",
        f"- 生成时间：{meta.get('generated_at', '')}",
        f"- 抽取后端：`{meta.get('backend', '')}`",
        f"- 数据源：{', '.join(s['source'] for s in stats['sources']) or '（无）'}",
        "",
        "## 1. 规模",
        "",
        "| 指标 | 数值 |",
        "| --- | --- |",
        f"| 文章数 | {stats['articles']} |",
        f"| 其中判重丢弃 | {stats['duplicates']} |",
        f"| 三元组实例数（含出处） | {stats['triples']} |",
        f"| 去重后不同三元组 | {stats['distinct_triples']} |",
        f"| 被两篇以上文章支持的三元组 | {stats['multi_source_triples']} |",
        "",
        "## 2. 来源分布",
        "",
        "| 来源 | 文章数 |",
        "| --- | --- |",
    ]
    lines += [f"| {s['source']} | {s['c']} |" for s in stats["sources"]] or ["| （无） | 0 |"]

    lines += ["", "## 3. 高频主体", "", "| 实体 | 出现次数 |", "| --- | --- |"]
    lines += [f"| {r['subject']} | {r['c']} |" for r in stats["top_subjects"]] or ["| （无） | 0 |"]

    lines += ["", "## 4. 关系类型", "", "| 谓词 | 次数 |", "| --- | --- |"]
    lines += [f"| {r['predicate']} | {r['c']} |" for r in stats["top_predicates"]] or ["| （无） | 0 |"]

    if evaluation and isinstance(evaluation, dict) and "strict" in evaluation:
        st, rl = evaluation["strict"], evaluation["relaxed"]
        lines += [
            "",
            "## 5. 对照人工标注",
            "",
            f"- 参与评估的文章：{evaluation['articles']} 篇，gold {evaluation['gold_total']} 条",
            f"- 抽取产出：{evaluation['predicted_total']} 条",
            "",
            "| 口径 | Precision | Recall | F1 |",
            "| --- | --- | --- | --- |",
            f"| strict（S,P,O 全对） | {st['precision']:.2%} | {st['recall']:.2%} | {st['f1']:.2%} |",
            f"| relaxed（只看 S,O） | {rl['precision']:.2%} | {rl['recall']:.2%} | {rl['f1']:.2%} |",
            "",
            "## 6. 已知局限",
            "",
            "1. gold 由单人标注，谓词用词有主观性——这是 relaxed 口径存在的原因。",
            "2. 实体消歧只做了小写归并，没有做别名词典与上下文消歧。",
            "3. 边上的时间来自文章发布时间，不是事件发生时间。",
            "4. 新闻正文版权归原站点，本报告只用于课程演示。",
            "",
        ]

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path