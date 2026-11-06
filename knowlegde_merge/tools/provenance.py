"""溯源：给定一个实体或一条三元组，查出它来自哪些文章、哪一句原文。

三层溯源：
    三元组行 → articles 行 → 原文（URL + 本地 HTML 快照 + Markdown 快照 + 具体句子）

前两层靠数据库外键，第三层靠读 data/md 的正文现场定位句子。

用法：
    python tools/provenance.py --db out/cn_gold.sqlite3 --subject 超聚变
    python tools/provenance.py --db out/cn_gold.sqlite3 --s 侨源股份 --p 签订
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, "src")

from newskg.config import ROOT  # noqa: E402
from newskg.store import connect  # noqa: E402


def _body(md_path: str) -> str:
    p = ROOT / md_path
    if not p.exists():
        return ""
    text = p.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            return parts[2].strip()
    return text


def _sentences(body: str) -> list[str]:
    return [s.strip() for s in re.split(r"[。！？；\n]+", body) if s.strip()]


def _find_sentence(body: str, *needles: str) -> str:
    """找出同时包含所有关键词的那句话。"""
    for s in _sentences(body):
        if all(n and n in s for n in needles):
            return s
    for s in _sentences(body):
        if any(n and n in s for n in needles):
            return s
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(description="查三元组/实体的原始出处")
    ap.add_argument("--db", default="out/cn_gold.sqlite3")
    ap.add_argument("--subject", help="只按实体名查（主宾都算）")
    ap.add_argument("--s", help="主语")
    ap.add_argument("--p", help="谓词")
    ap.add_argument("--o", help="宾语")
    ap.add_argument("--extractor", default="", help="只看某个抽取器")
    args = ap.parse_args()

    where, params = [], []
    if args.subject:
        where.append("(t.subject = ? OR t.object = ?)")
        params += [args.subject, args.subject]
    if args.s:
        where.append("t.subject = ?"); params.append(args.s)
    if args.p:
        where.append("t.predicate = ?"); params.append(args.p)
    if args.o:
        where.append("t.object = ?"); params.append(args.o)
    if args.extractor:
        where.append("t.extractor = ?"); params.append(args.extractor)
    if not where:
        print("至少给一个条件：--subject / --s / --p / --o", file=sys.stderr)
        return 2

    conn = connect(args.db)
    rows = conn.execute(
        f"""
        SELECT t.subject, t.predicate, t.object, t.extractor, t.confidence,
               a.article_id, a.title, a.source, a.published_at, a.url,
               a.markdown_path, a.raw_path
        FROM triples t JOIN articles a ON a.article_id = t.article_id
        WHERE {' AND '.join(where)}
        ORDER BY a.published_at DESC
        """,
        tuple(params),
    ).fetchall()

    if not rows:
        print("没有匹配的三元组。检查 --db 和查询条件。")
        return 1

    arts = {r["article_id"] for r in rows}
    print(f"共 {len(rows)} 条三元组，来自 {len(arts)} 篇文章\n")

    for r in rows:
        print("─" * 72)
        print(f"关系    ：{r['subject']}  —[{r['predicate']}]→  {r['object']}")
        print(f"抽取器  ：{r['extractor']}（置信度 {r['confidence']}）")
        print(f"文章    ：{r['title']}")
        print(f"来源    ：{r['source']}    发布：{r['published_at']}")
        print(f"原文链接：{r['url']}")
        print(f"本地快照：{r['markdown_path']}   （原始 HTML：{r['raw_path']}）")
        sentence = _find_sentence(_body(r["markdown_path"]), r["subject"], r["object"])
        if sentence:
            print(f"原文句子：{sentence[:160]}")
        else:
            print("原文句子：（在快照里没找到同时含主宾的句子，可能是抽取时的实体改写）")
    print("─" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())