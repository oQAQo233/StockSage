"""Step 2：实体类型标注。

流水线：
1. 规则层（免费，覆盖 ~10%）—— 后缀/形态特征；
2. LLM 层（只对度数 >=2 的实体）—— 每个实体带最多 3 条邻居边，靠语义而不是字面；
3. 度数 =1 的实体不花预算，标 `unset`。

为什么必须给邻居：只给「苹果」两个字，模型会犹豫；给出
「苹果 --发布--> iPhone Duo」它才会答"公司企业"。
"""

from __future__ import annotations

import collections
import re
import sqlite3
import time

from . import ddl
from .llm import TYPE_MODEL, ask_json, mark

#: 12 类闭集。顺序即提示词里的顺序。
ETYPES = [
    ("公司企业", "营利性企业及其品牌"),
    ("机构组织", "政府机关、协会、院所、媒体、国际组织"),
    ("人物", "自然人（含以其身份出现的人名）"),
    ("地点行政", "国家/省/市/县/区/镇/乡等行政区划"),
    ("地点设施", "场馆、园区、车站、基地、工程等具体设施"),
    ("产品型号", "具体的产品、型号、车型、设备"),
    ("技术概念", "技术、标准、算法、框架、协议"),
    ("活动事件", "会议、展会、论坛、比赛、发布会"),
    ("作品内容", "影视、游戏、报告、白皮书、标准文件"),
    ("数值度量", "纯数字、金额、比例、日期、计数"),
    ("泛称角色", "类指词，无具体指称（机器人、医生、用户、企业）"),
    ("其他", "无法判定"),
]
ETYPE_NAMES = [n for n, _ in ETYPES]
_ETYPE_SET = set(ETYPE_NAMES)

#: 这些缩写长得像数字，其实是技术概念，别被 \d 误伤
_TECH_TOKEN = {
    "3g", "4g", "5g", "6g", "ai", "ar", "vr", "mr", "wifi", "llm", "agi",
    "gpu", "cpu", "npu", "api", "sdk", "iot", "saas", "paas", "iaas", "rpa",
}

_NUMERIC_RE = re.compile(
    r"^[0-9]+(\.[0-9]+)?\s*(%|万亿|亿|万|台|个|家|人|次|年|月|日|号|号线|小时|分钟|倍|元|美元)?$"
)
_ACTIVITY_RE = re.compile(
    r"(论坛|峰会|大会|博览会|展览会|发布会|赛事|座谈会|研讨会|联委会|对接会|交流会|路演|沙龙|挑战赛|大赛|评选|开幕式)$"
)
_ORG_RE = re.compile(
    r"(公司|集团|研究院|研究所|大学|学院|委员会|协会|学会|基金会|实验室|中心|银行|医院|法院|检察院|"
    r"委|部|局|署|厅|院|所|社|台)$"
)
_PLACE_RE = re.compile(r"(省|市|县|区|镇|乡|村|州|旗|盟)$")


def rule_type(name: str) -> str | None:
    """纯字面规则。返回 None 表示规则判不了，交给 LLM。"""
    n = name.strip()
    low = n.lower()
    if not n:
        return "其他"
    if low in _TECH_TOKEN:
        return "技术概念"
    # 活动优先：很多活动名里含「中心/委员会」等组织后缀
    if _ACTIVITY_RE.search(n):
        return "活动事件"
    if _NUMERIC_RE.fullmatch(low):
        return "数值度量"
    if _ORG_RE.search(n):
        return "机构组织"
    if _PLACE_RE.search(n) and len(n) <= 8:
        return "地点行政"
    return None


def _entity_degrees(conn: sqlite3.Connection) -> collections.Counter:
    deg: collections.Counter = collections.Counter()
    for s, o in conn.execute("SELECT subject, object FROM triples"):
        deg[s] += 1
        deg[o] += 1
    return deg


def _edge_index(conn: sqlite3.Connection, keep: int = 3) -> dict[str, list[str]]:
    """每个实体最多保留 keep 条邻居边，格式化成一句话。一次扫描搞定。"""
    idx: dict[str, list[str]] = collections.defaultdict(list)
    for s, p, o in conn.execute("SELECT subject, predicate, object FROM triples"):
        if len(idx[s]) < keep:
            idx[s].append(f"{s} --{p}--> {o}")
        if len(idx[o]) < keep:
            idx[o].append(f"{s} --{p}--> {o}")
    return idx


def seed_rules(conn: sqlite3.Connection) -> tuple[int, int]:
    """对所有实体先跑一遍规则层。返回（规则命中数, 总数）。"""
    deg = _entity_degrees(conn)
    existing = {r["entity"] for r in conn.execute("SELECT entity FROM entity_type")}
    hits = 0
    rows = []
    for name in deg:
        if name in existing:
            continue
        et = rule_type(name)
        if et:
            hits += 1
            rows.append((name, et, 1.0, "rule", "后缀规则"))
        else:
            rows.append((name, "未标注", 0.0, "unset", ""))
    conn.executemany(
        "INSERT OR REPLACE INTO entity_type(entity,etype,confidence,decided_by,evidence) "
        "VALUES (?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return hits, len(deg)


def pending(conn: sqlite3.Connection, min_degree: int = 2) -> list[str]:
    """还没被 LLM/规则定下来、且度数够高的实体。"""
    deg = _entity_degrees(conn)
    decided = {
        r["entity"]
        for r in conn.execute("SELECT entity FROM entity_type WHERE decided_by IN ('llm','rule')")
    }
    out = [e for e, d in deg.items() if d >= min_degree and e not in decided]
    out.sort(key=lambda e: -deg[e])
    return out


_PROMPT_HEAD = """你是中文科技新闻知识图谱的实体类型标注器。给每个实体从 12 类中选 1 个类型。

【类型清单】
{types}

【判定规则】
- 依据是「邻居边」的语义，不是名字字面。例：
  "苹果 --发布--> iPhone Duo" → 公司企业
  "机器人手指 --抓握--> 苹果" 只是说明这条边里的苹果是物体，实体整体仍按多数边判
- 名字是泛称（机器人/医生/企业/用户/记者）→ 泛称角色
- 纯数字/金额/比例/日期/计数 → 数值度量
- 活动名（论坛/峰会/大会/博览会）优先判活动事件，即使里面含「中心/委员会」
- 只输出 JSON 数组，不要解释、不要 Markdown

【待标注】
"""


def _prompt_for(items: list[tuple[str, list[str]]]) -> str:
    types = "\n".join(f"{i+1}. {n}：{d}" for i, (n, d) in enumerate(ETYPES))
    body = "\n".join(
        f'{{"entity":"{e}","edges":[{",".join(repr(x) for x in edges)}]}}' for e, edges in items
    )
    return (
        _PROMPT_HEAD.format(types=types)
        + body
        + '\n\n【输出格式】[["苹果","公司企业"],["比亚迪","公司企业"]]\n'
    )


def run(
    conn: sqlite3.Connection,
    *,
    batch: int = 50,
    budget_min: float = 8.0,
    min_degree: int = 2,
    timeout: float = 600.0,
) -> dict:
    """跑若干批，直到预算用完或没有待办。每批独立提交，随时可中断。

    timeout 是**读**超时：端点偶尔整个连接不返回，600 秒配上 3 次重试意味着一批
    能卡半小时。批量小的时候（≤100 条）把超时压到 240 秒更划算——正常批次只要
    2~3 分钟，卡住的话 12 分钟内就能失败重来。
    """
    ddl.ensure_schema(conn)
    started = time.time()
    idx = _edge_index(conn)
    deg = _entity_degrees(conn)

    todo = pending(conn, min_degree=min_degree)
    total = len(todo)
    done_batches = 0
    failed = 0

    while todo and (time.time() - started) / 60 < budget_min:
        chunk = todo[:batch]
        items = [(e, idx.get(e, [])) for e in chunk]
        obj = ask_json(
            conn, "type", _prompt_for(items), model=TYPE_MODEL, max_tokens=9000, timeout=timeout
        )
        if obj is None:
            failed += 1
            new_batch = max(25, batch // 2)
            print(f"  [type] 批失败（{chunk[0][:10]}…），批量 {batch} -> {new_batch}", flush=True)
            mark(conn, "type", f"batch-{chunk[0][:8]}", "failed", "llm/parse")
            batch = new_batch
            continue  # 不前进 todo，下一轮用更小的批量重试

        rows = []
        for rec in obj:
            # 兼容紧凑数组和最开始的 dict 两种形态
            if isinstance(rec, dict):
                name = str(rec.get("entity", "")).strip()
                et = str(rec.get("etype", "")).strip()
                conf = 0.8
            elif isinstance(rec, (list, tuple)) and len(rec) >= 2:
                name = str(rec[0]).strip()
                et = str(rec[1]).strip()
                conf = 0.8
            else:
                continue
            if name not in deg or et not in _ETYPE_SET:
                continue
            edges = idx.get(name, [])
            rows.append((name, et, conf, "llm", " | ".join(edges[:3])))

        conn.executemany(
            "INSERT OR REPLACE INTO entity_type(entity,etype,confidence,decided_by,evidence) "
            "VALUES (?,?,?,?,?)",
            rows,
        )
        conn.commit()
        done_batches += 1
        todo = todo[batch:]
        print(f"  [type] 批 {done_batches}: 写 {len(rows)}/{len(chunk)} 条，剩余 {len(todo)}", flush=True)

    return {
        "total_pending": total,
        "batches": done_batches,
        "failed": failed,
        "remaining": len(todo),
        "elapsed_s": round(time.time() - started, 1),
    }