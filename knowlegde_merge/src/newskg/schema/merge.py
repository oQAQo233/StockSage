"""Step 3：实体合并（同一实体的不同写法 → 一个规范名）。

四层候选 + 一道硬约束（etype 必须一致）：
1. 种子层：图里已有的 184 条「简称/全称/别名/又称/又名/缩写」边，零成本、最高置信；
2. 归一 key：去掉「第X届」「（…）」「有限公司/集团」后完全相同的；
3. 子串包含：A ⊂ B 且长度差 ≤4；
4. 编辑距离：同首字、长度差 ≤2、difflib 相似度 ≥0.80。

为什么 2/3/4 层都要类型约束：实测 9009 对子串候选里绝大多数是
「中国 ⊂ 中国移动 / 中国新闻网 / 中国科学院」这类，不加约束会把国家和公司合掉。
"""

from __future__ import annotations

import collections
import difflib
import re
import sqlite3
import time
import unicodedata

from . import ddl
from .llm import ask_json, mark

ALIAS_PREDICATES = ("简称", "全称", "别名", "又称", "又名", "缩写")

#: 方向明确的两类：简称的主体是全称，全称的客体是全称。
_REVERSE_CANON = {"全称"}

#: 明显不是专名的简写，不能当别名（否则会把一份文件并成一个普通词）。
#: 故意不用「出现频次」判断：`进博会` 也会出现在第四/五/九届的名字里，
#: 按频次筛会把合法的简称误杀。
_GENERIC_ALIASES = {
    "意见", "一张图", "通知", "方案", "报告", "计划", "标准", "平台", "系统",
    "服务", "产品", "技术", "项目", "活动", "数据", "模型", "政策", "规定",
    "办法", "指南", "白皮书", "路线图", "生态", "场景", "行业", "产业",
}


def _generic(alias: str) -> bool:
    return len(alias) < 2 or alias in _GENERIC_ALIASES


def _norm(name: str) -> str:
    """归一 key：只做高置信的形态归一，不动「市/区」这类会产生歧义的后缀。"""
    s = unicodedata.normalize("NFKC", name).strip()
    # 故意不剥「第X届」：剥了会把「第四届进博会」和「第九届进博会」并成一个，
    # 那是两届不同的展会。届次差异交给 LLM 判，规则层不碰。
    s = re.sub(r"[（(][^）)]*[）)]", "", s)
    s = re.sub(r"(股份有限公司|有限公司|集团|公司|股份)$", "", s)
    s = re.sub(r"\s+", "", s)
    return s


def _degrees(conn: sqlite3.Connection) -> collections.Counter:
    deg: collections.Counter = collections.Counter()
    for s, o in conn.execute("SELECT subject, object FROM triples"):
        deg[s] += 1
        deg[o] += 1
    return deg


def _types(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["entity"]: r["etype"] for r in conn.execute("SELECT entity, etype FROM entity_type")}


def _neighbors(conn: sqlite3.Connection, keep: int = 3) -> dict[str, set[str]]:
    nb: dict[str, set[str]] = collections.defaultdict(set)
    for s, o in conn.execute("SELECT subject, object FROM triples"):
        if len(nb[s]) < keep:
            nb[s].add(o)
        if len(nb[o]) < keep:
            nb[o].add(s)
    return nb


def _type_ok(a: str, b: str, ta: str, tb: str, strict: bool) -> bool:
    """类型一致才允许合并。

    strict=True（子串/编辑距离层）：两边都必须是已知类型且相同，防止「中国 ⊂ 中国移动」。
    strict=False（种子/归一 key 层）：证据本身够强，允许一边未知。
    """
    known = {"其他", "未标注", "", None, "unset"}
    if ta in known or tb in known:
        return not strict
    return ta == tb


def candidates(conn: sqlite3.Connection) -> tuple[list[dict], set[tuple[str, str]]]:
    """返回（需要 LLM 判的候选, 直接采信的 pair 集合）。"""
    deg = _degrees(conn)
    etype = _types(conn)
    names = set(deg)
    auto: set[tuple[str, str]] = set()
    auto_rows: list[tuple[str, str, str, float, str, str]] = []

    # ---------- 层 1：别名种子 ----------
    for s, o, pred in conn.execute(
        "SELECT subject, object, predicate FROM triples WHERE predicate IN (%s)"
        % ",".join("?" * len(ALIAS_PREDICATES)),
        ALIAS_PREDICATES,
    ):
        # 方向：简称边是「全称 --简称--> 简写」，全称边是「简写 --全称--> 全称」
        if pred == "简称":
            canon, alias = s, o
        elif pred == "全称":
            canon, alias = o, s
        else:
            canon, alias = (s, o) if deg[s] >= deg[o] else (o, s)
        if not canon or not alias or canon == alias:
            continue
        if _generic(alias):
            continue  # 简写那一侧是泛称（意见/一张图/Balance）就不能当别名
        key = tuple(sorted((alias, canon)))
        if key in auto:
            continue
        auto.add(key)
        auto_rows.append((alias, canon, "seed_alias", 1.0, "seed", f"--{pred}-->"))

    # ---------- 层 2：归一 key 相同 ----------
    groups: dict[str, list[str]] = collections.defaultdict(list)
    for n in names:
        k = _norm(n)
        if k:
            groups[k].append(n)
    for k, members in groups.items():
        if len(members) < 2 or len(members) > 8:
            continue
        members.sort(key=lambda x: (-deg[x], -len(x)))
        canon = members[0]
        for other in members[1:]:
            pair = tuple(sorted((canon, other)))
            if pair in auto:
                continue
            auto.add(pair)
            auto_rows.append((other, canon, "norm_key", 0.98, "rule", f"归一 key={k}"))

    # ---------- 层 3：子串包含（严格类型） ----------
    sub_pairs: list[tuple[str, str]] = []
    sub_seen: set[tuple[str, str]] = set()
    for b in names:
        if not (3 <= len(b) <= 18):
            continue
        for cut in range(1, 5):
            L = len(b) - cut
            if L < 2:
                break
            for i in range(0, len(b) - L + 1):
                a = b[i : i + L]
                if a == b or a not in names:
                    continue
                pair = tuple(sorted((a, b)))
                if pair in auto:
                    continue
                if not _type_ok(a, b, etype.get(a), etype.get(b), strict=True):
                    continue
                if pair not in sub_seen:
                    sub_seen.add(pair)
                    sub_pairs.append(pair)

    # ---------- 层 4：编辑距离（严格类型） ----------
    edit_pairs: list[tuple[str, str]] = []
    by_first: dict[str, list[str]] = collections.defaultdict(list)
    for n in names:
        if 2 <= len(n) <= 20:
            by_first[n[0]].append(n)
    for first, lst in by_first.items():
        lst.sort(key=len)
        for i, a in enumerate(lst):
            for b in lst[i + 1 :]:
                if len(b) - len(a) > 2:
                    break
                if not _type_ok(a, b, etype.get(a), etype.get(b), strict=True):
                    continue
                if difflib.SequenceMatcher(None, a, b).ratio() >= 0.80:
                    pair = tuple(sorted((a, b)))
                    if pair not in auto:
                        edit_pairs.append(pair)

    # 先落自动层
    if auto_rows:
        conn.executemany(
            "INSERT OR REPLACE INTO entity_merge(alias,canonical,method,score,decided_by,evidence) "
            "VALUES (?,?,?,?,?,?)",
            auto_rows,
        )
        conn.commit()

    # 候选去重
    seen: set[tuple[str, str]] = set()
    judged: list[dict] = []
    nb = _neighbors(conn)
    for a, b in sub_pairs + edit_pairs:
        if a in seen:
            continue
        seen.add((a, b))
        shared = sorted(nb.get(a, set()) & nb.get(b, set()))
        judged.append(
            {
                "a": a,
                "b": b,
                "type_a": etype.get(a, ""),
                "type_b": etype.get(b, ""),
                "deg_a": deg[a],
                "deg_b": deg[b],
                "shared": shared[:3],
            }
        )
    return judged, auto


_PROMPT_HEAD = """判断下面每一对实体，是否指同一个真实世界对象。

【判定标准】
- yes：同一对象的不同写法——全称/简称、带不带「市」、中英对照、新旧名
- no：兄弟产品（iPhone 17 / iPhone 18）、包含关系（中国 / 中国移动）、
  近似但不同的人名（马欣 / 马佳欣）、不同组织恰好名字像
- **上下位不是同一对象，判 no**：智能手机 / 手机、系统 / 操作系统、
  园区 / 产业园区、教育部 / 尼日利亚教育部、医疗 / 医疗健康
- 证据优先级：共享邻居 > 别名语义 > 字面相似
- 拿不准填 unsure
- **按编号顺序作答，数量必须与题目一致，不要漏、不要合并、不要解释**

【待判】（共 {count} 对）
"""


def _prompt_for(items: list[dict]) -> str:
    lines = []
    for i, it in enumerate(items, 1):
        shared = "、".join(it["shared"]) if it["shared"] else "无"
        lines.append(
            f'{i}. {it["a"]} | {it["b"]} | 类型 {it["type_a"] or "?"}/{it["type_b"] or "?"}'
            f' | 度数 {it["deg_a"]}/{it["deg_b"]} | 共享邻居 {shared}'
        )
    return (
        _PROMPT_HEAD.format(count=len(items))
        + "\n".join(lines)
        + f'\n\n【输出】长度为 {len(items)} 的 JSON 数组，每项只能是 "yes" / "no" / "unsure"：\n'
        + '["yes","no","unsure",...]\n'
    )


def run(conn: sqlite3.Connection, *, batch: int = 40, budget_min: float = 8.0) -> dict:
    ddl.ensure_schema(conn)
    started = time.time()
    judged, auto = candidates(conn)
    total = len(judged)
    batches = failed = 0
    accepted = 0
    deg = _degrees(conn)
    todo = judged

    while todo and (time.time() - started) / 60 < budget_min:
        chunk = todo[:batch]
        obj = ask_json(conn, "merge", _prompt_for(chunk), model=None, max_tokens=2500)
        if obj is None:
            failed += 1
            new_batch = max(10, batch // 2)
            print(f"  [merge] 批失败（{chunk[0]['a'][:10]}…），批量 {batch} -> {new_batch}", flush=True)
            mark(conn, "merge", f"batch-{chunk[0]['a'][:8]}", "failed", "llm/parse")
            batch = new_batch
            continue  # 不前进 todo，用更小批量重试

        # 优先按「位置数组」解释；长度对不上再退回 dict 形态
        if (
            isinstance(obj, list)
            and len(obj) == len(chunk)
            and all(isinstance(x, str) for x in obj)
        ):
            answers = [x.strip().lower() for x in obj]
        else:
            answers = []
            for rec in obj:
                if isinstance(rec, dict):
                    answers.append(str(rec.get("answer", "")).strip().lower())
                elif isinstance(rec, (list, tuple)) and rec:
                    answers.append(str(rec[0]).strip().lower())
                else:
                    answers.append("")

        rows = []
        for item, ans in zip(chunk, answers):
            if not ans.startswith("yes"):
                continue
            a, b = item["a"], item["b"]
            if a not in deg or b not in deg:
                continue
            # 规范名取度数更高的那个；同分取更长的
            canon = a if (deg[a], len(a)) >= (deg[b], len(b)) else b
            alias = b if canon == a else a
            rows.append(
                (
                    alias,
                    canon,
                    "llm",
                    0.85,
                    "llm",
                    f'{item["type_a"] or "?"}/{item["type_b"] or "?"}',
                )
            )

        conn.executemany(
            "INSERT OR REPLACE INTO entity_merge(alias,canonical,method,score,decided_by,evidence) "
            "VALUES (?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
        accepted += len(rows)
        batches += 1
        todo = todo[batch:]
        print(
            f"  [merge] 批 {batches}: 判定 {len(chunk)}，接受 {len(rows)}，剩余 {len(todo)}",
            flush=True,
        )

    return {
        "auto_pairs": len(auto),
        "llm_candidates": total,
        "batches": batches,
        "failed": failed,
        "accepted_by_llm": accepted,
        "remaining": len(todo),
        "elapsed_s": round(time.time() - started, 1),
    }


def resolve(conn: sqlite3.Connection) -> dict:
    """把 entity_merge 的 pair 做传递闭包，重选规范名，写回最终结果。"""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    pairs = list(
        conn.execute(
            "SELECT alias, canonical, method, score, decided_by, evidence FROM entity_merge"
        )
    )
    # 保留每对原来的判定来源：闭包只补「传递得来的」那些边，
    # 不能把种子别名/归一 key/编辑距离/LLM 判定统统改写成 closure，否则报告就没法审计了。
    old: dict[str, tuple] = {r[0]: (r[2], r[3], r[4], r[5]) for r in pairs}
    for alias, canon in ((r[0], r[1]) for r in pairs):
        union(alias, canon)

    deg = _degrees(conn)
    clusters: dict[str, list[str]] = collections.defaultdict(list)
    for node in list(parent):
        clusters[find(node)].append(node)

    rows = []
    for root, members in clusters.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda x: (-deg[x], -len(x), x))
        canon = members[0]
        for other in members[1:]:
            prov = old.get(other)
            if prov:
                rows.append((other, canon, prov[0], prov[1], prov[2], prov[3]))
            else:
                rows.append((other, canon, "closure", 0.6, "rule", "并查集传递闭包"))

    conn.execute("DELETE FROM entity_merge")
    conn.executemany(
        "INSERT OR REPLACE INTO entity_merge(alias,canonical,method,score,decided_by,evidence) "
        "VALUES (?,?,?,?,?,?)",
        rows,
    )
    conn.commit()

    total_entities = len(deg)
    merged = len(rows)
    return {
        "entities_before": total_entities,
        "aliases_merged": merged,
        "entities_after": total_entities - merged,
        "clusters": sum(1 for m in clusters.values() if len(m) > 1),
    }