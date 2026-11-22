"""Schema 四步的统一入口。

用法（每批跑完就退出，反复跑同一条命令 = 继续推进，不会重复付费）：

    python -m newskg.schema.cli --step type  --budget-min 8
    python -m newskg.schema.cli --step rel   --budget-min 8
    python -m newskg.schema.cli --step merge --budget-min 8
    python -m newskg.schema.cli --step resolve
    python -m newskg.schema.cli --step sense --budget-min 8
    python -m newskg.schema.cli --step report
"""

from __future__ import annotations

import argparse
import json
import sqlite3

from ..config import DB_PATH, OUT
from . import ddl, llm
from . import merge as merge_mod
from . import propagate as propagate_mod
from . import rel as rel_mod
from . import report as report_mod
from . import senses as senses_mod
from . import typing as typing_mod

DEFAULT_BATCH = {"rel": 30, "type": 50, "merge": 40, "sense": 20}
STEPS = [
    "rel", "type", "propagate", "merge", "resolve", "sense", "disamb", "report",
    "stats", "validate", "suspicious",
]


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="newskg.schema")
    ap.add_argument("--step", required=True, choices=STEPS)
    ap.add_argument("--batch", type=int, default=0, help="每批条数，0 用默认值")
    ap.add_argument("--budget-min", type=float, default=8.0, help="本次最多跑多少分钟")
    ap.add_argument("--limit", type=int, default=20, help="suspicious/sense 的候选个数")
    ap.add_argument("--min-degree", type=int, default=2, help="type 步骤只处理度数不低于此值的实体")
    ap.add_argument("--only", default="", help="sense 步骤只处理这些实体（逗号分隔）")
    ap.add_argument("--timeout", type=float, default=600.0, help="单次调用的读超时（秒）")
    ap.add_argument("--db", default=str(DB_PATH))
    args = ap.parse_args(argv)

    conn = connect(args.db)
    ddl.ensure_schema(conn)
    batch = args.batch or DEFAULT_BATCH.get(args.step, 30)

    if args.step == "rel":
        result = rel_mod.run(conn, batch=batch, budget_min=args.budget_min)
    elif args.step == "type":
        hits, total = typing_mod.seed_rules(conn)
        result = {"rule_hits": hits, "entities": total}
        result.update(
            typing_mod.run(
                conn,
                batch=batch,
                budget_min=args.budget_min,
                min_degree=args.min_degree,
                timeout=args.timeout,
            )
        )
    elif args.step == "propagate":
        result = propagate_mod.run(conn)
    elif args.step == "merge":
        result = merge_mod.run(conn, batch=batch, budget_min=args.budget_min)
    elif args.step == "resolve":
        result = merge_mod.resolve(conn)
    elif args.step == "sense":
        only = [x.strip() for x in args.only.split(",") if x.strip()] or None
        result = senses_mod.run(
            conn, limit=args.limit, budget_min=args.budget_min, only=only
        )
    elif args.step == "disamb":
        only = [x.strip() for x in args.only.split(",") if x.strip()] or None
        result = senses_mod.cluster_run(
            conn, limit=args.limit, budget_min=args.budget_min, only=only
        )
    elif args.step == "report":
        extra = {
            "关系族": f"{conn.execute('SELECT COUNT(*) FROM rel_family').fetchone()[0]} 个",
            "已映射谓词": f"{conn.execute('SELECT COUNT(*) FROM predicate_map').fetchone()[0]} 个",
            "已合并别名": f"{conn.execute('SELECT COUNT(*) FROM entity_merge').fetchone()[0]} 个",
            "已拆分义项": f"{conn.execute('SELECT COUNT(*) FROM entity_sense').fetchone()[0]} 个",
            "LLM 缓存命中记录": f"{conn.execute('SELECT COUNT(*) FROM llm_cache').fetchone()[0]} 条",
        }
        result = report_mod.write_all(conn, OUT / "labels", extra)
    elif args.step == "validate":
        result = {
            "rel": rel_mod.validate(conn),
            "type_coverage": dict(
                conn.execute(
                    "SELECT decided_by, COUNT(*) FROM entity_type GROUP BY decided_by"
                ).fetchall()
            ),
            "merge_clusters": conn.execute(
                "SELECT COUNT(DISTINCT canonical) FROM entity_merge"
            ).fetchone()[0],
            "progress": llm.progress(conn),
        }
    elif args.step == "suspicious":
        result = senses_mod.suspicious(conn, limit=args.limit)
    else:  # stats
        result = llm.progress(conn)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())