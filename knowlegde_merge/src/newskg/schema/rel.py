"""Step 1：关系归一（谓词 → 关系族）。

关键立场（上一轮被用户否掉的方案就在这里修正）：
**不删任何谓词、不按频次筛。** 只出现 1 次的 1123 个谓词里，
`理事长 / 代工生产 / 非法获取 / 检查出 / is_spokesperson_for` 全是高信息量关系。
频次只决定「要不要花钱复核」，决定不了「要不要归一」。

三层：
1. 字典直配（约 200~400 个，零成本）；
2. LLM 批量归类（其余）——**按位置作答**：提示词里编号，模型只回一个族名数组。
   输出 token 从每条约 15 个降到约 4 个，在慢端点上这是几倍的差别；
3. 一致性校验（覆盖率、方向冲突）。
"""

from __future__ import annotations

import sqlite3
import time

from . import ddl
from .llm import ask_json, mark

#: 只有「归属」族需要区分方向（属于 vs 拥有），其余按正向记。
_REVERSE_HINTS = ("拥有", "持有", "掌握", "具备")

_PROMPT_HEAD = """你是中文科技新闻知识图谱的关系本体专家。把下面每一个关系谓词归入给定的关系族。

【关系族清单】
{families}

【判定规则】
- 必须从上面 {n} 个族里选，不许自创
- 判断依据是「样例三元组」的语义，不是谓词字面
  （例：谓词写「理事长」，样例是「谢少锋 --理事长--> 开放原子开源基金会」→ 任职）
- 确实无法归类的填「其他」
- **按编号顺序作答，数量必须与题目一致，不要漏、不要合并、不要解释**

【待归类】（共 {count} 个）
"""


def _family_block() -> str:
    return "\n".join(f"{i+1}. {f}：{d}" for i, (f, d, _ex) in enumerate(ddl.FAMILIES))


def _direction(pred: str) -> str:
    return "reverse" if any(k in pred for k in _REVERSE_HINTS) else "forward"


def _prompt_for(items: list[dict]) -> str:
    """每题只用「谓词 + 截断的样例两端」，不带标题。

    实测该端点的主要瓶颈是输入 token：带完整样例和 40 字标题时，
    200 个谓词要 16 分钟；压到这一种形态后同样的量只要几分钟。
    """
    lines = []
    for i, it in enumerate(items, 1):
        subj = (it["subject"] or "")[:12]
        obj = (it["object"] or "")[:12]
        lines.append(f'{i}.「{it["predicate"]}」  {subj}→{obj}')
    return (
        _PROMPT_HEAD.format(
            families=_family_block(), n=len(ddl.FAMILIES), count=len(items)
        )
        + "\n".join(lines)
        + f'\n\n【输出】长度为 {len(items)} 的 JSON 数组，每项是族名，顺序与题目一致：\n'
        + '["任职","投资交易","其他",...]\n'
    )


def collect(conn: sqlite3.Connection) -> list[dict]:
    """每个谓词一条：条数 + 一条样例（含原文标题）。"""
    stats: dict[str, dict] = {}
    for pred, s, o, title in conn.execute(
        "SELECT t.predicate, t.subject, t.object, a.title "
        "FROM triples t LEFT JOIN articles a ON a.article_id = t.article_id"
    ):
        rec = stats.get(pred)
        if rec is None:
            stats[pred] = {
                "predicate": pred,
                "n": 1,
                "subject": s,
                "object": o,
                "title": title or "",
            }
        else:
            rec["n"] += 1
    return list(stats.values())


def seed_dict(conn: sqlite3.Connection, items: list[dict]) -> int:
    """字典层：能直配的立即写，返回命中数。"""
    rows = []
    for it in items:
        pred = it["predicate"]
        fam = ddl.FAMILY_BY_PREDICATE.get(pred.strip().lower())
        if not fam:
            continue
        rows.append(
            (
                pred,
                fam,
                _direction(pred),
                "dict",
                1.0,
                f'{it["subject"]} --{pred}--> {it["object"]}',
                "",
            )
        )
    conn.executemany(
        "INSERT OR REPLACE INTO predicate_map"
        "(predicate,family,direction,decided_by,confidence,evidence,note) VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return len(rows)


def pending(conn: sqlite3.Connection, items: list[dict]) -> list[dict]:
    decided = {r["predicate"] for r in conn.execute("SELECT predicate FROM predicate_map")}
    todo = [it for it in items if it["predicate"] not in decided]
    todo.sort(key=lambda it: -it["n"])
    return todo


def _parse(obj, chunk: list[dict], families: set[str]) -> list[tuple]:
    """把模型输出落成 (predicate, family, direction, ...) 行。

    优先按「位置数组」解释；长度对不上再退回 dict/紧凑对 形态。
    """
    pairs: list[tuple[str, str]] = []

    if (
        isinstance(obj, list)
        and len(obj) == len(chunk)
        and all(isinstance(x, str) for x in obj)
    ):
        pairs = [(it["predicate"], fam.strip()) for it, fam in zip(chunk, obj)]
    else:
        for rec in obj or []:
            if isinstance(rec, dict):
                pairs.append(
                    (str(rec.get("predicate", "")).strip(), str(rec.get("family", "")).strip())
                )
            elif isinstance(rec, (list, tuple)) and len(rec) >= 2:
                pairs.append((str(rec[0]).strip(), str(rec[1]).strip()))

    by_pred = {it["predicate"]: it for it in chunk}
    rows = []
    for pred, fam in pairs:
        if fam not in families or pred not in by_pred:
            continue
        it = by_pred[pred]
        rows.append(
            (
                pred,
                fam,
                _direction(pred),
                "llm",
                0.7,
                f'{it["subject"]} --{pred}--> {it["object"]}',
                "",
            )
        )
    return rows


def run(conn: sqlite3.Connection, *, batch: int = 30, budget_min: float = 8.0) -> dict:
    ddl.ensure_schema(conn)
    started = time.time()

    items = collect(conn)
    hit = seed_dict(conn, items)
    todo = pending(conn, items)
    total = len(todo)
    batches = 0
    failed = 0
    families = {f for f, _d, _e in ddl.FAMILIES}

    while todo and (time.time() - started) / 60 < budget_min:
        chunk = todo[:batch]
        obj = ask_json(conn, "rel", _prompt_for(chunk), model=None, max_tokens=4000)
        if obj is None:
            failed += 1
            new_batch = max(20, batch // 2)
            print(
                f"  [rel] 批失败（{chunk[0]['predicate'][:10]}…），批量 {batch} -> {new_batch}",
                flush=True,
            )
            mark(conn, "rel", f"batch-{chunk[0]['predicate'][:8]}", "failed", "llm/parse")
            batch = new_batch
            continue  # 不前进 todo，用更小批量重试

        rows = _parse(obj, chunk, families)
        conn.executemany(
            "INSERT OR REPLACE INTO predicate_map"
            "(predicate,family,direction,decided_by,confidence,evidence,note) VALUES (?,?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
        batches += 1
        todo = todo[batch:]
        print(f"  [rel] 批 {batches}: 写 {len(rows)}/{len(chunk)}，剩余 {len(todo)}", flush=True)

    return {
        "predicates_total": len(items),
        "dict_hit": hit,
        "pending_at_start": total,
        "batches": batches,
        "failed": failed,
        "remaining": len(todo),
        "elapsed_s": round(time.time() - started, 1),
    }


def validate(conn: sqlite3.Connection) -> dict:
    """覆盖率 + 「其他」占比 + 方向冲突。"""
    total = conn.execute("SELECT COUNT(DISTINCT predicate) FROM triples").fetchone()[0]
    mapped = conn.execute("SELECT COUNT(*) FROM predicate_map").fetchone()[0]
    missing = conn.execute(
        "SELECT predicate FROM triples EXCEPT SELECT predicate FROM predicate_map"
    ).fetchall()
    other = conn.execute(
        "SELECT COUNT(*) FROM triples t JOIN predicate_map p ON p.predicate=t.predicate "
        "WHERE p.family='其他'"
    ).fetchone()[0]
    edges = conn.execute("SELECT COUNT(*) FROM triples").fetchone()[0]
    by_family = conn.execute(
        "SELECT family, COUNT(*) c FROM predicate_map GROUP BY family ORDER BY c DESC"
    ).fetchall()
    return {
        "predicates_total": total,
        "mapped": mapped,
        "missing": len(missing),
        "missing_sample": [r[0] for r in missing[:20]],
        "other_edges": other,
        "other_ratio": round(other / edges, 4) if edges else 0,
        "families_used": len(by_family),
    }