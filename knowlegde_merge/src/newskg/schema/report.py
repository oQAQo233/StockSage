"""生成 3 份交付清单：关系族、实体类型、总览 Schema。"""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from . import ddl


def _pct(a: int, b: int) -> str:
    return f"{a / b:.1%}" if b else "0.0%"


def _degrees(conn: sqlite3.Connection) -> dict[str, int]:
    d: dict[str, int] = {}
    for s, o in conn.execute("SELECT subject, object FROM triples"):
        d[s] = d.get(s, 0) + 1
        d[o] = d.get(o, 0) + 1
    return d


def write_rel_families(conn: sqlite3.Connection, path: Path) -> int:
    total_edges = conn.execute("SELECT COUNT(*) FROM triples").fetchone()[0]
    rows = conn.execute(
        "SELECT p.family, COUNT(*) edges FROM triples t "
        "JOIN predicate_map p ON p.predicate = t.predicate "
        "GROUP BY p.family ORDER BY edges DESC"
    ).fetchall()
    lines = [
        "# 关系族清单",
        "",
        f"原始谓词 **{conn.execute('SELECT COUNT(DISTINCT predicate) FROM triples').fetchone()[0]}** 个，"
        f"边 **{total_edges}** 条，归入 **{len(rows)}** 个关系族（没有任何一条边被丢弃）。",
        "",
        "| 族 | 定义 | 边数 | 占比 | 覆盖谓词数 |",
        "|---|---|---|---|---|",
    ]
    for fam, edges in rows:
        defn = conn.execute("SELECT definition FROM rel_family WHERE family=?", (fam,)).fetchone()
        npred = conn.execute(
            "SELECT COUNT(*) FROM predicate_map WHERE family=?", (fam,)
        ).fetchone()[0]
        lines.append(
            f"| {fam} | {(defn[0] if defn else '')[:40]} | {edges} | {_pct(edges, total_edges)} | {npred} |"
        )

    lines += ["", "---", "", "## 各族下的原始谓词", ""]
    for fam, edges in rows:
        preds = conn.execute(
            "SELECT p.predicate, COUNT(*) c FROM triples t "
            "JOIN predicate_map p ON p.predicate = t.predicate "
            "WHERE p.family = ? GROUP BY p.predicate ORDER BY c DESC",
            (fam,),
        ).fetchall()
        by = conn.execute(
            "SELECT decided_by, COUNT(*) FROM predicate_map WHERE family=? GROUP BY decided_by",
            (fam,),
        ).fetchall()
        how = "、".join(f"{k} {v}" for k, v in by)
        lines.append(f"### {fam}（{edges} 条边 / {len(preds)} 个谓词；判定来源：{how}）")
        lines.append("")
        lines.append("、".join(f"`{p}`({c})" for p, c in preds[:60]))
        if len(preds) > 60:
            lines.append("")
            lines.append(f"（其余 {len(preds) - 60} 个谓词略，见 `predicate_map` 表）")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return len(rows)


def write_entity_types(conn: sqlite3.Connection, path: Path) -> int:
    deg = _degrees(conn)
    rows = conn.execute(
        "SELECT etype, COUNT(*) c FROM entity_type WHERE decided_by != 'unset' "
        "GROUP BY etype ORDER BY c DESC"
    ).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM entity_type").fetchone()[0]
    typed = sum(c for _t, c in rows)
    unset = total - typed

    lines = [
        "# 实体类型清单",
        "",
        f"实体 **{total}** 个，其中 **{typed}** 个有确定类型（{_pct(typed, total)}）；"
        f"**{unset}** 个为 `未标注`（模型无法从已有边判断，宁可留空也不硬塞「其他」）。",
        "",
        "| 类型 | 定义 | 实体数 | 占有类型实体的比例 | 代表实体 |",
        "|---|---|---|---|---|",
    ]
    defs = dict((n, d) for n, d in
                [("公司企业", "营利性企业及其品牌"), ("机构组织", "政府/协会/院所/媒体"),
                 ("人物", "自然人"), ("地点行政", "行政区划"), ("地点设施", "场馆/园区/车站/基地"),
                 ("产品型号", "具体产品与型号"), ("技术概念", "技术/标准/算法"),
                 ("活动事件", "会议/展会/论坛/比赛"), ("作品内容", "影视/游戏/报告"),
                 ("数值度量", "数字/金额/比例/日期"), ("泛称角色", "类指词"),
                 ("其他", "无法判定")])
    for et, c in rows:
        members = [
            r[0]
            for r in conn.execute(
                "SELECT entity FROM entity_type WHERE etype=? LIMIT 400", (et,)
            )
        ]
        members.sort(key=lambda x: -deg.get(x, 0))
        sample = "、".join(members[:8])
        lines.append(f"| {et} | {defs.get(et, '')} | {c} | {_pct(c, typed)} | {sample} |")

    lines += ["", f"另有 **{unset}** 个实体为 `未标注`（模型未能从已有边判断）。"]

    lines += ["", "---", "", "## 数值度量类实体（建议后续过滤）", ""]
    nums = [r[0] for r in conn.execute("SELECT entity FROM entity_type WHERE etype='数值度量'")]
    lines.append("、".join(f"`{n}`" for n in nums[:80]) or "（无）")
    path.write_text("\n".join(lines), encoding="utf-8")
    return len(rows)


def write_schema(conn: sqlite3.Connection, path: Path, extra: dict) -> None:
    total_edges = conn.execute("SELECT COUNT(*) FROM triples").fetchone()[0]
    total_ent = len(_degrees(conn))

    fam_rows = conn.execute(
        "SELECT p.family, COUNT(*) e, COUNT(DISTINCT t.predicate) np FROM triples t "
        "JOIN predicate_map p ON p.predicate = t.predicate GROUP BY p.family ORDER BY e DESC"
    ).fetchall()
    type_rows = conn.execute(
        "SELECT etype, COUNT(*) c FROM entity_type WHERE decided_by != 'unset' "
        "GROUP BY etype ORDER BY c DESC"
    ).fetchall()
    unset_n = conn.execute(
        "SELECT COUNT(*) FROM entity_type WHERE decided_by = 'unset'"
    ).fetchone()[0]
    typed_n = sum(c for _t, c in type_rows)

    merged_n = conn.execute("SELECT COUNT(*) FROM entity_merge").fetchone()[0]
    raw_ent = conn.execute("SELECT COUNT(*) FROM entity_type").fetchone()[0]

    lines = [
        "# 知识图谱 Schema 总览",
        "",
        f"- 实体：**{raw_ent}** 个原始字符串，把别名并进来之后是 **{raw_ent - merged_n}** 个"
        f"（合并了 {merged_n} 个别名）",
        f"- 实体标签：**{len(type_rows)}** 类，已标注 **{typed_n}** 个，"
        f"`未标注` {unset_n} 个",
        f"- 关系：**{conn.execute('SELECT COUNT(DISTINCT predicate) FROM triples').fetchone()[0]}** 个原始谓词、"
        f"**{total_edges}** 条边，归入 **{len(fam_rows)}** 个关系族",
        "",
    ]
    if extra:
        lines += ["### 本次处理结果", ""]
        for k, v in extra.items():
            lines.append(f"- {k}：{v}")
        lines.append("")

    lines += ["## 实体标签", "", "| 标签 | 实体数 | 占有类型实体的比例 |", "|---|---|---|"]
    for et, c in type_rows:
        lines.append(f"| {et} | {c} | {_pct(c, typed_n)} |")
    lines.append(f"| （未标注） | {unset_n} | — |")

    lines += ["", "## 关系标签", "", "| 族 | 边数 | 占比 | 谓词数 |", "|---|---|---|---|"]
    for fam, e, npred in fam_rows:
        lines.append(f"| {fam} | {e} | {_pct(e, total_edges)} | {npred} |")

    lines += ["", "详见 `rel_families.md`、`entity_types.md`、`merge_report.csv`、`sense_report.csv`。"]
    path.write_text("\n".join(lines), encoding="utf-8")


def write_csvs(conn: sqlite3.Connection, out_dir: Path) -> dict:
    merge_path = out_dir / "merge_report.csv"
    rows = conn.execute(
        "SELECT alias, canonical, method, score, decided_by, evidence FROM entity_merge "
        "ORDER BY canonical, alias"
    ).fetchall()
    with merge_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["alias", "canonical", "method", "score", "decided_by", "evidence"])
        for r in rows:
            w.writerow([r[0], r[1], r[2], r[3], r[4], (r[5] or "")[:120]])

    sense_path = out_dir / "sense_report.csv"
    srows = conn.execute(
        "SELECT es.entity, es.sense_id, es.label, es.definition, "
        "(SELECT COUNT(*) FROM triple_sense ts WHERE ts.sense_id = es.sense_id) n "
        "FROM entity_sense es ORDER BY es.entity, es.sense_id"
    ).fetchall()
    with sense_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["entity", "sense_id", "label", "definition", "edges"])
        for r in srows:
            w.writerow([r[0], r[1], r[2], r[3] or "", r[4]])

    edge_path = out_dir / "sense_edges.csv"
    erows = conn.execute(
        "SELECT ts.sense_id, t.subject, t.predicate, t.object, t.article_id "
        "FROM triple_sense ts JOIN triples t ON t.triple_id = ts.triple_id "
        "ORDER BY ts.sense_id LIMIT 20000"
    ).fetchall()
    with edge_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["sense_id", "subject", "predicate", "object", "article_id"])
        for r in erows:
            w.writerow(list(r))

    return {"merge": len(rows), "sense": len(srows), "sense_edges": len(erows)}


def write_all(conn: sqlite3.Connection, out_dir: Path, extra: dict | None = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    n_fam = write_rel_families(conn, out_dir / "rel_families.md")
    n_type = write_entity_types(conn, out_dir / "entity_types.md")
    csvs = write_csvs(conn, out_dir)
    write_schema(conn, out_dir / "SCHEMA.md", extra or {})
    return {"families": n_fam, "types": n_type, **csvs}