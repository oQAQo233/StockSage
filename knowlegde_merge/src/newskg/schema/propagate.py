"""实体类型：邻居传播层。

LLM 只标注了度数 ≥3 的实体，还有 7000 多个「未标注」。它们大多只出现一两次，
但出现一两次也常常能定类型：

    AI工具运营商 --运营--> AI工具          → 左边的「运营商」是机构/公司
    2026年6G发展大会 --举办于--> 武汉      → 左边是活动，右边是地点
    约翰·特努斯 --任职于--> 苹果           → 左边是人，右边是机构

所以这里用**谓词规则 + 另一端类型**给它们补标签。为了不制造噪音：
只要有一条边参与投票，但必须「投票集中度 ≥ 60%」，且另一端类型要符合白名单
（比如「任职于」的主语只有当宾语确实是机构时，才判成人物）。

产出标 `decided_by='propagate'`、置信度 0.6，与 LLM 判的（0.7+）和规则判的（1.0）分开，
报告里能一眼看出哪些是「推出来的」。
"""

from __future__ import annotations

import collections
import sqlite3

from . import ddl

#: (谓词, 目标站在哪一侧, 判定类型, 另一端类型白名单 / None 表示不限制)
RULES: list[tuple[str, str, str, set[str] | None]] = [
    # ---- 地点 ----
    ("位于", "o", "地点行政", None),
    ("坐落于", "o", "地点行政", None),
    ("总部位于", "o", "地点行政", None),
    ("举办于", "o", "地点行政", None),
    ("举办地", "o", "地点行政", None),
    ("举办地点", "o", "地点行政", None),
    ("发布地", "o", "地点行政", None),
    ("签署地点", "o", "地点行政", None),
    # ---- 活动 ----
    ("举办", "s", "活动事件", None),
    ("举办于", "s", "活动事件", None),
    ("举办地", "s", "活动事件", None),
    ("举办地点", "s", "活动事件", None),
    ("主办", "s", "活动事件", None),
    ("承办", "s", "活动事件", None),
    ("参展", "s", "活动事件", None),
    ("亮相于", "s", "活动事件", None),
    ("获奖于", "s", "活动事件", None),
    # ---- 人物 ----
    ("任职于", "s", "人物", {"机构组织", "公司企业"}),
    ("就职于", "s", "人物", {"机构组织", "公司企业"}),
    ("担任", "s", "人物", {"机构组织", "公司企业"}),
    ("担任", "s", "人物", {"活动事件"}),
    ("出席", "s", "人物", {"活动事件", "机构组织"}),
    ("毕业于", "s", "人物", None),
    # ---- 组织 / 公司 ----
    ("任职于", "o", "机构组织", {"人物"}),
    ("就职于", "o", "机构组织", {"人物"}),
    ("担任", "o", "机构组织", {"人物"}),
    ("收购", "o", "公司企业", None),
    ("收购", "s", "公司企业", None),
    ("投资", "s", "公司企业", None),
    ("领投", "s", "公司企业", None),
    ("入股", "s", "公司企业", None),
    ("推出", "s", "公司企业", {"产品型号", "技术概念", "作品内容"}),
    ("发布", "s", "公司企业", {"产品型号", "技术概念", "作品内容"}),
    ("研发", "s", "公司企业", {"产品型号", "技术概念"}),
    ("生产", "s", "公司企业", {"产品型号"}),
    ("制造", "s", "公司企业", {"产品型号"}),
    # ---- 产品 ----
    ("发布", "o", "产品型号", {"公司企业", "机构组织"}),
    ("推出", "o", "产品型号", {"公司企业", "机构组织"}),
    ("研发", "o", "产品型号", None),
    ("生产", "o", "产品型号", None),
    ("搭载", "o", "产品型号", None),
    ("配备", "o", "产品型号", None),
    # ---- 技术 ----
    ("应用于", "s", "技术概念", None),
    ("用于", "s", "技术概念", None),
]

_BY_PRED: dict[str, list[tuple[str, str, set[str] | None]]] = collections.defaultdict(list)
for _p, _side, _t, _allow in RULES:
    _BY_PRED[_p].append((_side, _t, _allow))


def run(conn: sqlite3.Connection, only_unset: bool = True) -> dict:
    ddl.ensure_schema(conn)
    etype = {r[0]: r[1] for r in conn.execute("SELECT entity, etype FROM entity_type")}
    who = {r[0]: r[1] for r in conn.execute("SELECT entity, decided_by FROM entity_type")}

    votes: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    deg: collections.Counter = collections.Counter()

    for s, p, o in conn.execute("SELECT subject, predicate, object FROM triples"):
        deg[s] += 1
        deg[o] += 1
        for side, target, allow in _BY_PRED.get(p, ()):
            me, other = (s, o) if side == "s" else (o, s)
            if only_unset and who.get(me) != "unset":
                continue
            ot = etype.get(other, "")
            if allow is not None and ot not in allow:
                continue
            votes[me][target] += 1

    rows = []
    for ent, cnt in votes.items():
        total = deg[ent]
        target, n = cnt.most_common(1)[0]
        # 集中度：只认压倒性的那一类，避免「同一实体既像人又像机构」被硬塞
        if total and n / total >= 0.6:
            rows.append((ent, target, round(0.6 * n / total + 0.2, 2), "propagate", "邻居类型投票"))

    if rows:
        conn.executemany(
            "INSERT OR REPLACE INTO entity_type(entity,etype,confidence,decided_by,evidence) "
            "VALUES (?,?,?,?,?)",
            rows,
        )
        conn.commit()

    dist = collections.Counter(r[1] for r in rows)
    return {
        "assigned": len(rows),
        "by_type": dict(dist.most_common()),
        "still_unset": conn.execute(
            "SELECT COUNT(*) FROM entity_type WHERE decided_by='unset'"
        ).fetchone()[0],
    }