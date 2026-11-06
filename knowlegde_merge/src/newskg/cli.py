"""命令行入口。

    python -m newskg.cli demo
    python -m newskg.cli fetch --source gdelt --query "..." --limit 10
    python -m newskg.cli build --backend rule
    python -m newskg.cli export --format all
    python -m newskg.cli stats
    python -m newskg.cli eval
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import config, evaluate as evaluate_mod, store
from . import compare as compare_mod
from .kg import build_extractor
from .pipeline import build_graph, ingest, reset
from .sources import FilteredSource, build_source


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _open_db(path: str | None):
    return store.connect(Path(path) if path else config.DB_PATH)


def _print_stats(stats: dict) -> None:
    print()
    print("=" * 62)
    print(f"文章 {stats['articles']} 篇（判重丢弃 {stats['duplicates']}）"
          f" | 三元组 {stats['triples']} 条 | 去重后 {stats['distinct_triples']} 条"
          f" | 多源支持 {stats['multi_source_triples']} 条")
    print("-" * 62)
    for row in stats["top_subjects"][:8]:
        print(f"  {row['subject'][:44]:<46} {row['c']}")
    print("=" * 62)
    print()


def cmd_init(args) -> int:
    config.ensure_dirs()
    conn = _open_db(args.db)
    conn.close()
    print(f"目录已就绪：{config.ROOT}")
    print(f"数据库：{args.db or config.DB_PATH}")
    return 0


def cmd_fetch(args) -> int:
    config.ensure_dirs()
    source = build_source(args.source)
    # 两道过滤都放在发现层：先按发布时间卡窗口，再按主题筛股票相关。
    # 下游（抓取 → 转换 → 抽取 → 入库）拿到的仍是普通 ArticleStub，完全无感。
    source = FilteredSource(
        source,
        topic=args.topic,
        keep_undated=args.keep_undated,
    )
    conn = _open_db(args.db)
    ingest(source, args.query, args.limit, args.days, conn)
    conn.close()
    return 0


def cmd_build(args) -> int:
    config.ensure_dirs()
    extractor = build_extractor(args.backend)
    conn = _open_db(args.db)
    print(f"[build] 后端 = {extractor.name}")
    try:
        total = build_graph(conn, extractor, limit=args.limit)
    except Exception as exc:  # noqa: BLE001  额度/网络问题给一句人话，不吐 traceback
        print(f"[build] 失败：{exc}", file=sys.stderr)
        conn.close()
        return 2
    print(f"[build] 共写入 {total} 条三元组")
    conn.close()
    return 0


def cmd_export(args) -> int:
    conn = _open_db(args.db)
    out_dir = Path(args.out) if args.out else config.OUT
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    if args.format in ("all", "json"):
        written.append(store.export_json(conn, out_dir / "graph.json"))
    if args.format in ("all", "cypher"):
        written.append(store.export_cypher(conn, out_dir / "graph.cypher"))
    if args.format in ("all", "graphml"):
        written.append(store.export_graphml(conn, out_dir / "graph.graphml"))
    conn.close()
    for path in written:
        print(f"[export] {path}")
    return 0


def cmd_stats(args) -> int:
    conn = _open_db(args.db)
    stats = store.stats(conn)
    conn.close()
    if args.json:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        _print_stats(stats)
    return 0


def cmd_dump(args) -> int:
    conn = _open_db(args.db)
    rows = conn.execute(
        "SELECT subject, predicate, object, support FROM v_triples "
        "ORDER BY support DESC, subject LIMIT ?",
        (args.limit,),
    ).fetchall()
    conn.close()
    print(f"{'subject':<38} {'predicate':<14} {'object':<34} support")
    print("-" * 100)
    for r in rows:
        print(f"{r['subject'][:37]:<38} {r['predicate'][:13]:<14} {r['object'][:33]:<34} {r['support']}")
    print("-" * 100)
    print(f"共 {len(rows)} 条")
    return 0


def cmd_eval(args) -> int:
    conn = _open_db(args.db)
    gold_path = Path(args.gold) if args.gold else (config.GOLD / "fixtures.csv")
    if not gold_path.exists():
        print(f"找不到 gold 集：{gold_path}", file=sys.stderr)
        return 2
    gold = evaluate_mod.load_gold(gold_path)
    result = evaluate_mod.evaluate_extractor(conn, gold, args.backend)
    conn.close()
    payload = {k: v for k, v in result.items() if k != "per_article"}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_compare(args) -> int:
    config.ensure_dirs()
    conn = _open_db(args.db)
    gold_path = Path(args.gold) if args.gold else (config.GOLD / "corpus.csv")
    if not gold_path.exists():
        print(f"找不到 gold 集：{gold_path}", file=sys.stderr)
        print("先跑 export-corpus 导出语料清单，再用 gold 标注", file=sys.stderr)
        return 2

    backends = [b.strip() for b in args.backends.split(",") if b.strip()]
    results = compare_mod.run(conn, gold_path, backends, limit=args.limit)

    gold = evaluate_mod.load_gold(gold_path)
    sources = conn.execute("SELECT DISTINCT source FROM articles").fetchall()
    meta = {
        "corpus_articles": conn.execute("SELECT COUNT(*) c FROM articles").fetchone()["c"],
        "corpus_sources": ", ".join(r["source"] for r in sources),
        "gold_total": sum(len(v) for v in gold.values()),
        "gold_articles": len(gold),
        "generated_at": _now(),
    }
    md = compare_mod.write_markdown(results, config.OUT / "comparison.md", meta)
    csv_path = compare_mod.write_csv(results, config.OUT / "comparison.csv")
    conn.close()

    print()
    print(f"[compare] {md}")
    print(f"[compare] {csv_path}")
    return 0


def cmd_export_corpus(args) -> int:
    """把当前库里的文章清单导成 JSON，配合 data/md 就能离线重建语料。"""
    conn = _open_db(args.db)
    rows = conn.execute(
        "SELECT * FROM articles WHERE duplicate_of IS NULL OR duplicate_of = '' ORDER BY published_at DESC"
    ).fetchall()
    fields = [
        "article_id", "source", "url", "canonical_url", "title", "published_at",
        "lang", "license", "content_sha1", "simhash", "markdown_path", "word_count", "fetched_at",
    ]
    payload = [{k: row[k] for k in fields} for row in rows]
    conn.close()

    out = Path(args.out) if args.out else (config.DATA / "corpus" / "manifest.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[export-corpus] {len(payload)} 篇 -> {out}")
    return 0


def cmd_import_corpus(args) -> int:
    """从 manifest 重建文章表（不联网），配合已提交的 data/md 使用。"""
    manifest = Path(args.manifest) if args.manifest else (config.DATA / "corpus" / "manifest.json")
    if not manifest.exists():
        print(f"找不到 manifest：{manifest}", file=sys.stderr)
        return 2
    payload = json.loads(manifest.read_text(encoding="utf-8"))

    conn = _open_db(args.db)
    for row in payload:
        conn.execute(
            """INSERT OR REPLACE INTO articles
               (article_id, source, url, canonical_url, title, published_at, lang, license,
                content_sha1, simhash, raw_path, markdown_path, word_count, fetched_at, duplicate_of)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, '')""",
            (
                row["article_id"], row["source"], row.get("url", ""), row.get("canonical_url", ""),
                row.get("title", ""), row.get("published_at", ""), row.get("lang", "en"),
                row.get("license", ""), row.get("content_sha1", ""), str(row.get("simhash", 0)),
                row.get("raw_path", ""), row.get("markdown_path", ""),
                row.get("word_count", 0), row.get("fetched_at", ""),
            ),
        )
    conn.commit()
    conn.close()
    print(f"[import-corpus] 导入 {len(payload)} 篇")
    return 0


def cmd_reset(args) -> int:
    conn = _open_db(args.db)
    reset(conn, keep_articles=args.keep_articles)
    conn.close()
    print("[reset] 已清空" + ("（保留文章）" if args.keep_articles else "（文章与三元组）"))
    return 0


def cmd_demo(args) -> int:
    """一键跑通全链路：样例源 → 规则抽取 → 导出 → 统计 → 评估 → 报告。"""
    config.ensure_dirs()
    db_path = Path(args.db) if args.db else (config.OUT / "demo.sqlite3")
    conn = store.connect(db_path)

    print("=" * 62)
    print("离线演示：不联网、不需要 API key")
    print("=" * 62)

    reset(conn)
    ingest(build_source("fixtures"), None, 50, 365, conn)

    print()
    print("=" * 62)
    print(f"抽取后端：{args.backend}")
    print("=" * 62)
    total = build_graph(conn, build_extractor(args.backend))
    print(f"[build] 共写入 {total} 条三元组")

    stats = store.stats(conn)
    _print_stats(stats)

    config.OUT.mkdir(parents=True, exist_ok=True)
    store.export_json(conn, config.OUT / "graph.json")
    store.export_cypher(conn, config.OUT / "graph.cypher")
    store.export_graphml(conn, config.OUT / "graph.graphml")
    print(f"[export] {config.OUT / 'graph.json'}")
    print(f"[export] {config.OUT / 'graph.cypher'}")
    print(f"[export] {config.OUT / 'graph.graphml'}")

    gold_path = config.GOLD / "fixtures.csv"
    evaluation = None
    if gold_path.exists():
        evaluation = evaluate_mod.evaluate_extractor(
            conn, evaluate_mod.load_gold(gold_path), args.backend
        )
        print()
        print(
            f"[eval] strict P={evaluation['strict']['precision']:.2%} "
            f"R={evaluation['strict']['recall']:.2%} F1={evaluation['strict']['f1']:.2%}  "
            f"(gold={evaluation['gold_total']})"
        )

    report = evaluate_mod.write_report(
        stats,
        evaluation,
        config.OUT / "report.md",
        {"generated_at": _now(), "backend": args.backend},
    )
    print(f"[report] {report}")
    conn.close()
    return 0


def _add_db_option(parser: argparse.ArgumentParser) -> None:
    """让 --db 既能写在子命令前，也能写在子命令后。"""
    parser.add_argument("--db", default=argparse.SUPPRESS, help="SQLite 路径，默认 out/kg.sqlite3")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="newskg",
        description="新闻 → 统一 Markdown → 知识图谱（课程作业版）",
    )
    parser.add_argument("--db", help="SQLite 路径，默认 out/kg.sqlite3")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="建目录与空库")
    _add_db_option(p)
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("fetch", help="从某个源抓取文章")
    _add_db_option(p)
    p.add_argument(
        "--source",
        default="fixtures",
        help=(
            "新闻源（默认 fixtures，离线可跑）。可用：fixtures / rss / gdelt / guardian / "
            "eastmoney-rss / eastmoney / ths / sina-fin，"
            "逗号分隔可同时跑多个，预设 cn-finance（中文财经四源）或 all（全部网络源）"
        ),
    )
    p.add_argument("--query", help="检索词，GDELT/RSS 需要在标题里匹配")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument(
        "--days",
        type=float,
        default=2,
        help="时间窗：只保留距「现在」N 天以内发布的文章，可带小数（如 0.5 = 12 小时）",
    )
    p.add_argument(
        "--topic",
        default=None,
        help="主题过滤：stock（只保留与中国股票相关的新闻），留空不过滤",
    )
    p.add_argument(
        "--keep-undated",
        action="store_true",
        help="源没给发布时间时仍然保留（默认丢弃，因为无法判断是否在时间窗内）",
    )
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("build", help="抽三元组建图")
    _add_db_option(p)
    p.add_argument("--backend", default="rule", choices=["rule", "llm", "kggen"])
    p.add_argument("--limit", type=int, default=None, help="最多处理多少篇")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("export", help="导出图")
    _add_db_option(p)
    p.add_argument("--format", default="all", choices=["all", "json", "cypher", "graphml"])
    p.add_argument("--out", help="输出目录，默认 out/")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("stats", help="打印统计")
    _add_db_option(p)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("dump", help="打印三元组表")
    _add_db_option(p)
    p.add_argument("--limit", type=int, default=200)
    p.set_defaults(func=cmd_dump)

    p = sub.add_parser("eval", help="对照 gold 集评估")
    _add_db_option(p)
    p.add_argument("--backend", default="rule", choices=["rule", "llm", "kggen"])
    p.add_argument("--gold", help="gold CSV 路径，默认 data/gold/fixtures.csv")
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("compare", help="三方对照实验（同一语料 + 同一 gold）")
    _add_db_option(p)
    p.add_argument("--backends", default="rule,llm,kggen", help="逗号分隔，例如 rule,llm")
    p.add_argument("--gold", help="gold CSV 路径，默认 data/gold/corpus.csv")
    p.add_argument("--limit", type=int, default=None, help="每后端最多处理多少篇")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("export-corpus", help="导出语料清单（配合 data/md 离线重建）")
    _add_db_option(p)
    p.add_argument("--out", help="输出路径，默认 data/corpus/manifest.json")
    p.set_defaults(func=cmd_export_corpus)

    p = sub.add_parser("import-corpus", help="从语料清单重建文章表，不联网")
    _add_db_option(p)
    p.add_argument("--manifest", help="清单路径，默认 data/corpus/manifest.json")
    p.set_defaults(func=cmd_import_corpus)

    p = sub.add_parser("reset", help="清空数据")
    _add_db_option(p)
    p.add_argument("--keep-articles", action="store_true")
    p.set_defaults(func=cmd_reset)

    p = sub.add_parser("demo", help="离线一键演示")
    _add_db_option(p)
    p.add_argument("--backend", default="rule", choices=["rule", "llm", "kggen"])
    p.set_defaults(func=cmd_demo)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())