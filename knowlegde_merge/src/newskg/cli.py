"""命令行入口。

    python -m newskg.cli demo
    python -m newskg.cli fetch --source tech --limit 20
    python -m newskg.cli build
    python -m newskg.cli export --format all
    python -m newskg.cli stats
"""

from __future__ import annotations

import argparse
import os
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import config, store
from .kg import build_extractor
from .kg.entities import canonicalize
from .models import Triple
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
    if getattr(args, "day", None):
        os.environ["NEWSKG_ARCHIVE_DATE"] = args.day
    source = build_source(args.source)
    # 两道过滤都放在发现层：先按发布时间卡窗口，再用 LLM 给科技相关性打分并卡阈值。
    # 下游（抓取 → 转换 → 抽取 → 入库）拿到的仍是普通 ArticleStub，完全无感。
    source = FilteredSource(
        source,
        topic=args.topic,
        keep_undated=args.keep_undated,
        threshold=args.topic_threshold,
        mode=args.topic_mode,
        desc=args.topic_desc,
    )
    conn = _open_db(args.db)
    ingest(source, args.query, args.limit, args.days, conn)
    conn.close()
    return 0


def cmd_build(args) -> int:
    config.ensure_dirs()
    extractor = build_extractor(getattr(args, "backend", None))
    conn = _open_db(args.db)
    print(f"[build] 后端 = {extractor.name}")
    try:
        total = build_graph(conn, extractor, limit=args.limit, workers=args.workers, shard=args.shard or None)
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


def cmd_renormalize(args) -> int:
    """按当前别名表重写库里的实体名，不重新调用抽取器。

    别名表是知识，改它是常事；重跑 LLM 既贵又慢，而且同样的输入未必得到同样的输出
    （温度不为 0 时连结果都会漂）。这个命令只做字符串层的重算。
    """
    conn = store.connect(Path(args.db) if args.db else (config.OUT / "demo.sqlite3"))
    rows = conn.execute(
        "SELECT subject, predicate, object, article_id, published_at, source,"
        " extractor, confidence FROM triples"
    ).fetchall()
    if not rows:
        print("[renormalize] 库里没有三元组")
        return 0

    triples = [
        Triple(
            subject=r["subject"], predicate=r["predicate"], object=r["object"],
            article_id=r["article_id"], published_at=r["published_at"] or "",
            source=r["source"] or "", extractor=r["extractor"],
            confidence=float(r["confidence"] or 1.0),
        )
        for r in rows
    ]
    fixed, merged = canonicalize(triples)
    conn.execute("DELETE FROM triples")
    written = store.upsert_triples(conn, fixed)
    conn.commit()
    conn.close()

    print(f"[renormalize] {len(triples)} 条 → 写入 {written} 条"
          f"（过滤 {len(triples) - len(fixed)} 条泛化/自环，合并 {len(merged)} 处别名）")
    for pair in list(merged)[:10]:
        print(f"    {pair}")
    return 0


def cmd_reset(args) -> int:
    conn = _open_db(args.db)
    reset(conn, keep_articles=args.keep_articles)
    conn.close()
    print("[reset] 已清空" + ("（保留文章）" if args.keep_articles else "（文章与三元组）"))
    return 0


def cmd_demo(args) -> int:
    """端到端演示：样例源 → KGGen 抽取 → 统计 → 导出。

    样例是本地合成新闻（`data/fixtures`），不需要联网抓取；但抽取仍要调用模型端点，
    所以这个命令**不是离线命令**——没有 API key 时会在 build 阶段报错退出。
    """
    config.ensure_dirs()
    db_path = Path(args.db) if args.db else (config.OUT / "demo.sqlite3")
    conn = store.connect(db_path)

    print("=" * 62)
    print("演示语料：data/fixtures（8 篇合成新闻，不联网抓取）")
    print("=" * 62)

    reset(conn)
    ingest(build_source("fixtures"), None, 50, 365, conn)

    print()
    print("=" * 62)
    print("抽取后端：KGGen")
    print("=" * 62)
    total = build_graph(conn, build_extractor())
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
    conn.close()
    return 0


def _add_db_option(parser: argparse.ArgumentParser) -> None:
    """让 --db 既能写在子命令前，也能写在子命令后。"""
    parser.add_argument("--db", default=argparse.SUPPRESS, help="SQLite 路径，默认 out/kg.sqlite3")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="newskg",
        description="新闻 → 统一 Markdown → 知识图谱",
    )
    parser.add_argument("--db", help="SQLite 路径，默认 out/kg.sqlite3")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="建目录与空库")
    _add_db_option(p)
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("fetch", help="从某个源抓取文章（正文不合格的会被拒收）")
    _add_db_option(p)
    p.add_argument(
        "--source",
        default="fixtures",
        help=(
            "新闻源（默认 fixtures 样例）。科技源有 xinhua_tech / stdaily / chinanews_fin / chinanews_top / "
            "jiemian / eeo / tmtpost / leiphone / infoq / ifanr，"
            "按日归档有 chinanews_day / ifanr_day / most_day / ndrc_day，"
            "逗号分隔可同时跑多个，预设 tech（全部）/ tech-off（权威 6 个）/ tech-media（垂媒 4 个）/ all"
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
        "--day",
        default="",
        help="按天抓归档（仅 chinanews_day 之类源支持），格式 2026-11-04；RSS 源忽略此项",
    )
    p.add_argument(
        "--topic",
        default="tech",
        help="题材过滤：tech（LLM 打分筛科技新闻，默认）/ none（不过滤）",
    )
    p.add_argument(
        "--topic-threshold",
        type=float,
        default=None,
        help="科技相关性阈值，0-10，默认取 NEWSKG_TOPIC_THRESHOLD（6）",
    )
    p.add_argument(
        "--topic-mode",
        default="auto",
        choices=["auto", "llm", "keyword"],
        help="auto=有 key 用模型、没有则关键词降级；llm=强制模型（缺配置报错）；keyword=强制关键词",
    )
    p.add_argument(
        "--topic-desc",
        default="",
        help="自定义领域描述，追加进打分的提示词（默认内置「科技」定义）",
    )
    p.add_argument(
        "--keep-undated",
        action="store_true",
        help="源没给发布时间时仍然保留（默认丢弃，因为无法判断是否在时间窗内）",
    )
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("build", help="用 KGGen（或直连 LLM）抽三元组建图")
    _add_db_option(p)
    p.add_argument("--limit", type=int, default=None, help="最多处理多少篇")
    p.add_argument(
        "--backend",
        default=os.getenv("NEWSKG_BACKEND", ""),
        help="抽取后端：kggen（默认，质量最好但一篇两次大请求）/ direct（一次小请求，快且稳）",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=int(os.getenv("NEWSKG_BUILD_WORKERS", "1")),
        help="并发抽取的线程数，默认 1（串行）。抽取 95%% 时间在等模型，4 路约快 3 倍",
    )
    p.add_argument(
        "--shard",
        default=os.getenv("NEWSKG_BUILD_SHARD", ""),
        help='分片，形如 "0/4"：把待抽文章按 id 哈希分成 4 份，本进程只做第 0 份',
    )
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

    p = sub.add_parser("renormalize", help="按当前别名表重写实体名（不重跑抽取）")
    _add_db_option(p)
    p.set_defaults(func=cmd_renormalize)

    p = sub.add_parser("reset", help="清空数据")
    _add_db_option(p)
    p.add_argument("--keep-articles", action="store_true")
    p.set_defaults(func=cmd_reset)

    p = sub.add_parser("demo", help="端到端演示（样例语料 + KGGen，需要模型端点）")
    _add_db_option(p)
    p.set_defaults(func=cmd_demo)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())




