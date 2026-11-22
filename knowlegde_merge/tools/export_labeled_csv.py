"""把库里的三元组连同标签导成一张宽表（CSV），给下游直接用。

为什么单独做这个：`out/graph.json` 这类图文件只存了「谁-谓词-谁」，
实体类型、关系族、规范名、义项全在多张表里。下游如果不熟 SQL，
join 六张表容易出错；这张宽表把该带的一次带齐。

用法：
    python tools/export_labeled_csv.py                    # 默认 out/kg_labeled.csv
    python tools/export_labeled_csv.py --db out/kg.sqlite3 --out out/kg_labeled.csv

列说明见 README「交接内容」一节。一行 = 一条三元组。
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
from pathlib import Path

SQL = """
SELECT
    t.triple_id,
    t.subject,
    COALESCE(m1.canonical, t.subject)          AS subject_canonical,
    COALESCE(et1.etype, '')                    AS subject_type,
    t.predicate,
    COALESCE(pm.family, '')                    AS rel_family,
    t.object,
    COALESCE(m2.canonical, t.object)           AS object_canonical,
    COALESCE(et2.etype, '')                    AS object_type,
    t.published_at,
    t.source,
    t.article_id,
    COALESCE(es1.sense_id, '')                 AS subject_sense,
    COALESCE(es1.label, '')                    AS subject_sense_label,
    COALESCE(es2.sense_id, '')                 AS object_sense,
    COALESCE(es2.label, '')                    AS object_sense_label
FROM triples t
LEFT JOIN entity_merge  m1  ON m1.alias = t.subject
LEFT JOIN entity_merge  m2  ON m2.alias = t.object
LEFT JOIN entity_type   et1 ON et1.entity = t.subject
LEFT JOIN entity_type   et2 ON et2.entity = t.object
LEFT JOIN predicate_map pm  ON pm.predicate = t.predicate
LEFT JOIN triple_sense  ts1 ON ts1.triple_id = t.triple_id AND ts1.role = 'subject'
LEFT JOIN entity_sense  es1 ON es1.entity = ts1.entity AND es1.sense_id = ts1.sense_id
LEFT JOIN triple_sense  ts2 ON ts2.triple_id = t.triple_id AND ts2.role = 'object'
LEFT JOIN entity_sense  es2 ON es2.entity = ts2.entity AND es2.sense_id = ts2.sense_id
ORDER BY t.published_at, t.triple_id
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="导出一条三元组一行的带标签宽表")
    ap.add_argument("--db", default="out/kg.sqlite3")
    ap.add_argument("--out", default="out/kg_labeled.csv")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db, timeout=30)
    # 这个查询要排序 + 多表 join，SQLite 默认会写临时文件；某些环境下
    # （临时目录不可写 / 被占）会报 "unable to open database file"。
    # 一万多行的量放进内存绰绰有余，直接关掉临时文件。
    conn.execute("PRAGMA temp_store=MEMORY")
    cur = conn.execute(SQL)
    cols = [d[0] for d in cur.description]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    n = 0
    # utf-8-sig：Excel 直接双击打开不乱码
    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for row in cur:
            w.writerow(row)
            n += 1
    conn.close()
    print(f"[export] {out}  {n} 行 × {len(cols)} 列")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())