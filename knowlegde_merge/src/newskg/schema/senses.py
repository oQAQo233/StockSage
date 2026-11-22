"""Step 4：实体消歧（一个名字 → 多个义项）。

原方案用 embedding 给「邻居」聚类，本机没有模型，改成**用邻居的类型**分簇：
一个实体的邻居如果既有「公司/产品/人物」这类组织侧，又有「泛称角色/设施」这类
物体侧，它就很可能是同名异指（苹果 = 公司 or 水果）。

判定单位是 **mention**（每一条边），不是实体字符串——因为同一个名字在不同句子里
指不同东西。最后把边按 sense 分组，得到 `苹果#1[公司]` / `苹果#2[水果]`。
"""

from __future__ import annotations

import collections
import json
import sqlite3
import time
from pathlib import Path

from ..config import ROOT
from . import ddl
from .llm import ask_json, mark

#: 组织侧类型（"作为主体/机构存在"）
ORG_SIDE = ("公司企业", "机构组织", "产品型号", "人物")
#: 物体侧类型（"作为被操作的对象存在"）
THING_SIDE = ("泛称角色", "地点设施", "数值度量", "其他")

_PROMPT = """实体「{entity}」可能在不同语境里指不同对象。下面列出它在这个新闻图谱里参与的关系。

请做两件事：
1. 归纳它在这批数据里有几个不同含义（最多 3 个），每个给一个短标签；
2. 把每一条关系分配到对应含义。

【判定依据】谓词语义 + 另一端实体的类型 + 原文片段。
例：一端是「产品型号」且谓词是「发布」→ 公司；一端是「泛称角色」且谓词是「抓握」→ 实物。

【关系列表】
{edges}

【输出格式】（只输出 JSON，不要解释）
{{"senses":[{{"id":"1","label":"公司"}},{{"id":"2","label":"水果"}}],
 "assign":[{{"i":0,"sense":"1"}},{{"i":1,"sense":"2"}}]}}
"""


def _degrees(conn: sqlite3.Connection) -> collections.Counter:
    deg: collections.Counter = collections.Counter()
    for s, o in conn.execute("SELECT subject, object FROM triples"):
        deg[s] += 1
        deg[o] += 1
    return deg


def _types(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["entity"]: r["etype"] for r in conn.execute("SELECT entity, etype FROM entity_type")}


def _neighbors(conn: sqlite3.Connection) -> dict[str, set[str]]:
    nb: dict[str, set[str]] = collections.defaultdict(set)
    for s, o in conn.execute("SELECT subject, object FROM triples"):
        nb[s].add(o)
        nb[o].add(s)
    return nb


def suspicious(conn: sqlite3.Connection, limit: int = 20, min_degree: int = 6) -> list[dict]:
    """挑「疑似同名异指」的实体，交给 LLM 逐条边判义项。

    排序用**邻居类型多样性**：一个名字如果只有一个含义，它的邻居类型会很集中
    （公司名 → 全是机构/人物/产品）；出现同名异指时，邻居会跨到完全不同的类别
    （苹果：公司企业/产品型号/人物 + 泛称角色）。之前用「组织侧与物体侧是否均衡」
    排序，结果每个候选都恰好等于 0.5，等于没有排序。
    """
    deg = _degrees(conn)
    etype = _types(conn)
    nb = _neighbors(conn)
    out = []
    for name, d in deg.items():
        if d < min_degree or not (2 <= len(name) <= 6):
            continue
        counts = collections.Counter(etype.get(x, "其他") for x in nb.get(name, ()))
        org = sum(counts[t] for t in ORG_SIDE)
        thing = sum(counts[t] for t in ("泛称角色", "地点设施", "数值度量"))
        diversity = sum(
            1 for t, c in counts.items() if c > 0 and t not in ("其他", "未标注", "")
        )
        out.append(
            {
                "entity": name,
                "degree": d,
                "diversity": diversity,
                "org_side": org,
                "thing_side": thing,
                "dist": dict(counts.most_common()),
            }
        )
    out.sort(key=lambda r: (-r["diversity"], -r["degree"]))
    return out[:limit]


def _edges_for(conn: sqlite3.Connection, entity: str, cap: int = 20) -> list[dict]:
    """取该实体的边，按「另一端类型」轮转抽样。

    不能按 article_id 排序取前 N 条：苹果有 94 条边，按库里顺序取前 20 条
    很可能全是公司相关，那条「机器人手指 --抓握--> 苹果」永远进不来，
    消歧就失去了证据。轮转能保证每种邻居类型都有代表。
    """
    rows = conn.execute(
        "SELECT triple_id, subject, predicate, object, article_id FROM triples "
        "WHERE subject = ? OR object = ?",
        (entity, entity),
    ).fetchall()
    etype = _types(conn)
    items = [
        {
            "triple_id": r[0],
            "subject": r[1],
            "predicate": r[2],
            "object": r[3],
            "article_id": r[4],
            "other": r[3] if r[1] == entity else r[1],
            "other_type": etype.get(r[3] if r[1] == entity else r[1], ""),
        }
        for r in rows
    ]

    groups: dict[str, list[dict]] = collections.defaultdict(list)
    for it in items:
        groups[it["other_type"] or "未知"].append(it)

    # 同一类型里，全局越罕见的谓词越可疑，先取它们
    freq = {
        p: n
        for p, n in conn.execute("SELECT predicate, COUNT(*) FROM triples GROUP BY predicate")
    }
    for g in groups.values():
        g.sort(key=lambda it: freq.get(it["predicate"], 0))

    buckets = sorted(groups.values(), key=len)  # 稀有类型排在前面，优先被取

    out: list[dict] = []
    while len(out) < cap and buckets:
        for b in buckets:
            if b and len(out) < cap:
                out.append(b.pop(0))
        buckets = [b for b in buckets if b]
    return out


def _article_text(conn: sqlite3.Connection, article_id: str) -> str:
    """正文不在 articles 表里（只有 markdown_path），要读文件。

    先按原样试路径，再按项目根拼一次——库里的路径可能是相对路径。
    """
    row = conn.execute(
        "SELECT markdown_path FROM articles WHERE article_id = ?", (article_id,)
    ).fetchone()
    if not row or not row[0]:
        return ""
    raw = row[0]
    for cand in (Path(raw), ROOT / raw):
        try:
            if cand.exists():
                return cand.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
    return ""


def _snippets(conn: sqlite3.Connection, entity: str, edges: list[dict], n: int = 2) -> dict[int, str]:
    """给前 n 条边各配一段原文，帮助模型判语境。取不到就跳过。"""
    out: dict[int, str] = {}
    for i, e in enumerate(edges[:n]):
        body = _article_text(conn, e["article_id"])
        pos = body.find(entity)
        if pos == -1:
            continue
        out[i] = body[max(0, pos - 70) : pos + 90].replace("\n", " ")
    return out


def run(
    conn: sqlite3.Connection,
    *,
    limit: int = 20,
    budget_min: float = 8.0,
    only: list[str] | None = None,
) -> dict:
    ddl.ensure_schema(conn)
    started = time.time()
    cands = suspicious(conn, limit=limit)
    if only:
        # 指定名单时也允许反选：suspicious 里挑不到的名字，直接按 name 补进来
        known = {c["entity"] for c in cands}
        cands = [c for c in cands if c["entity"] in only]
        cands += [{"entity": e} for e in only if e not in known]
    done = {r["entity"] for r in conn.execute("SELECT DISTINCT entity FROM entity_sense")}
    todo = [c for c in cands if c["entity"] not in done]
    results = []

    for cand in todo:
        if (time.time() - started) / 60 > budget_min:
            break
        entity = cand["entity"]
        edges = _edges_for(conn, entity, cap=10)
        if len(edges) < 3:
            continue
        snip = _snippets(conn, entity, edges)

        lines = []
        for i, e in enumerate(edges):
            extra = f'  «原文: {snip[i]}»' if i in snip else ""
            lines.append(
                f"{i}. {e['subject']} --{e['predicate']}--> {e['object']}"
                f"  (另一端类型: {e['other_type'] or '未知'}){extra}"
            )
        prompt = _PROMPT.format(entity=entity, edges="\n".join(lines))
        obj = ask_json(conn, "sense", prompt, model=None, max_tokens=2000, timeout=120)
        # 模型经常把一整份结果包在只有一个元素的数组里
        if isinstance(obj, list) and len(obj) == 1 and isinstance(obj[0], dict):
            obj = obj[0]
        if not isinstance(obj, dict):
            mark(conn, "sense", entity, "failed", "llm/parse")
            continue

        senses = obj.get("senses") or []
        assign = obj.get("assign") or []
        if not senses or not assign:
            mark(conn, "sense", entity, "failed", "empty")
            continue

        sense_rows = []
        for s in senses[:3]:
            sid = str(s.get("id", "")).strip()
            label = str(s.get("label", "")).strip()
            if sid and label:
                sense_rows.append(
                    (entity, f"{entity}#{sid}", label, "", json.dumps(cand["dist"], ensure_ascii=False))
                )
        conn.executemany(
            "INSERT OR REPLACE INTO entity_sense(entity,sense_id,label,definition,evidence) "
            "VALUES (?,?,?,?,?)",
            sense_rows,
        )

        valid_ids = {r[1].split("#", 1)[1] for r in sense_rows}
        ts_rows = []
        for a in assign:
            if not isinstance(a, dict):
                continue
            try:
                i = int(a.get("i"))
            except (TypeError, ValueError):
                continue
            sid = str(a.get("sense", "")).strip()
            if i < 0 or i >= len(edges) or sid not in valid_ids:
                continue
            e = edges[i]
            role = "subject" if e["subject"] == entity else "object"
            ts_rows.append(
                (e["triple_id"], entity, role, f"{entity}#{sid}", "llm", e["predicate"])
            )
        conn.executemany(
            "INSERT OR REPLACE INTO triple_sense(triple_id,entity,role,sense_id,decided_by,evidence) "
            "VALUES (?,?,?,?,?,?)",
            ts_rows,
        )
        conn.commit()
        mark(conn, "sense", entity, "ok", f"{len(sense_rows)} senses / {len(ts_rows)} edges")
        results.append({"entity": entity, "senses": len(sense_rows), "edges": len(ts_rows)})
        print(
            f"  [sense] {entity}: {len(sense_rows)} 个义项 / {len(ts_rows)} 条边",
            flush=True,
        )

    return {"candidates": len(cands), "processed": len(results), "detail": results}


#: 组织侧类型：名字若是这些，正常邻居也应是这些。
_SIDE_TYPES = {
    "公司企业", "机构组织", "产品型号", "人物",
    "活动事件", "作品内容", "技术概念", "地点行政", "地点设施",
}
#: 物体侧类型：出现在「公司名」的邻居里就很可疑（苹果 --抓握-->）。
_OBJ_TYPES = {"泛称角色", "数值度量"}

_LABEL_PROMPT = """一个名字在新闻里可能指多个不同的东西。下面两组边都用了同一个名字「{entity}」。

【第一组】
{main}

【第二组】
{anom}

请给每组起一个 2~4 字的中文短名，说明它在那一组里指什么（例如「公司」「水果」「城市」「产品」「技术」）。
只输出 JSON，不要解释：{{"main":"公司","anom":"水果"}}
"""


def cluster_run(
    conn: sqlite3.Connection,
    *,
    limit: int = 40,
    min_degree: int = 6,
    budget_min: float = 6.0,
    only: list[str] | None = None,
) -> dict:
    """确定性分簇 + 极小的模型调用（只用来起名字）。

    分簇不靠模型：名字自己的类型是组织侧、而少数邻居落在物体侧（泛称角色/数值度量），
    这几条边就构成第二个义项。苹果那 93 条公司边和唯一一条「机器人手指 --抓握--> 苹果」
    正是这种形态。模型只负责给两组各起一个 2~4 字的短名，提示词只有几百字，
    在这个经常挂死的端点上才跑得动。
    """
    ddl.ensure_schema(conn)
    started = time.time()
    etype = _types(conn)
    deg = _degrees(conn)

    names = []
    for name, d in deg.items():
        if d < min_degree or not (2 <= len(name) <= 8):
            continue
        if only and name not in only:
            continue
        own = etype.get(name, "")
        if own not in _SIDE_TYPES:
            continue
        edges = _edges_for(conn, name, cap=400)
        anom = [e for e in edges if e["other_type"] in _OBJ_TYPES]
        if not anom or len(anom) > 0.35 * len(edges):
            continue
        names.append((name, own, len(anom), len(edges)))

    names.sort(key=lambda x: (-x[2], -x[3]))
    names = names[:limit]

    results = []
    try_llm = True
    for name, own, n_anom, n_all in names:
        if (time.time() - started) / 60 > budget_min:
            break
        edges = _edges_for(conn, name, cap=400)
        main = [e for e in edges if e["other_type"] not in _OBJ_TYPES]
        anom = [e for e in edges if e["other_type"] in _OBJ_TYPES]

        def fmt(group: list[dict]) -> str:
            return "\n".join(
                f'- {e["subject"]} --{e["predicate"]}--> {e["object"]}' for e in group[:4]
            )

        label_main, label_anom = own, "另一义项"
        if try_llm:
            obj = ask_json(
                conn,
                "sense_label",
                _LABEL_PROMPT.format(entity=name, main=fmt(main), anom=fmt(anom)),
                model=None,
                max_tokens=200,
                timeout=90,
            )
            if isinstance(obj, dict):
                label_main = str(obj.get("main") or own).strip()[:8] or own
                label_anom = str(obj.get("anom") or "另一义项").strip()[:8] or "另一义项"
            else:
                # 端点一旦连不上，后面每个实体都要白等好几分钟，所以第一次失败就整体降级
                try_llm = False
                print("  [sense] 起名调用失败，后续改用默认标签", flush=True)

        sense_main = f"{name}#1"
        sense_anom = f"{name}#2"
        conn.executemany(
            "INSERT OR REPLACE INTO entity_sense(entity,sense_id,label,definition,evidence) "
            "VALUES (?,?,?,?,?)",
            [
                (name, sense_main, label_main, f"邻居类型 {own} 为主", f"{len(main)} 条边"),
                (
                    name,
                    sense_anom,
                    label_anom,
                    "邻居落在泛称角色/数值度量侧",
                    "、".join(e["predicate"] for e in anom[:5]),
                ),
            ],
        )

        ts_rows = []
        for group, sid in ((main, sense_main), (anom, sense_anom)):
            for e in group:
                role = "subject" if e["subject"] == name else "object"
                ts_rows.append((e["triple_id"], name, role, sid, "rule", e["predicate"]))
        conn.executemany(
            "INSERT OR REPLACE INTO triple_sense(triple_id,entity,role,sense_id,decided_by,evidence) "
            "VALUES (?,?,?,?,?,?)",
            ts_rows,
        )
        conn.commit()
        results.append(
            {
                "entity": name,
                "labels": [label_main, label_anom],
                "edges": [len(main), len(anom)],
                "sample_anom": [f'{e["subject"]} --{e["predicate"]}--> {e["object"]}' for e in anom[:3]],
            }
        )
        print(
            f'  [sense] {name}: {label_main}（{len(main)} 边） / {label_anom}（{len(anom)} 边）',
            flush=True,
        )

    return {"candidates": len(names), "processed": len(results), "detail": results}