"""Schema 层的表结构定义（幂等）。

设计原则与主库一致：
- 不改 `triples` / `articles` 原表，一切都写新表，随时可删重建；
- 每张表都能单独查、单独核对，出问题只删自己那一张。
"""

from __future__ import annotations

import sqlite3

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS rel_family (
    family      TEXT PRIMARY KEY,
    definition  TEXT NOT NULL,
    examples    TEXT
);

CREATE TABLE IF NOT EXISTS predicate_map (
    predicate   TEXT PRIMARY KEY,
    family      TEXT,
    direction   TEXT,
    decided_by  TEXT,
    confidence  REAL,
    evidence    TEXT,
    note        TEXT
);

CREATE TABLE IF NOT EXISTS entity_type (
    entity      TEXT PRIMARY KEY,
    etype       TEXT,
    confidence  REAL,
    decided_by  TEXT,
    evidence    TEXT
);
CREATE INDEX IF NOT EXISTS idx_entity_type_etype ON entity_type(etype);

CREATE TABLE IF NOT EXISTS entity_merge (
    alias       TEXT PRIMARY KEY,
    canonical   TEXT NOT NULL,
    method      TEXT,
    score       REAL,
    decided_by  TEXT,
    evidence    TEXT
);
CREATE INDEX IF NOT EXISTS idx_entity_merge_canonical ON entity_merge(canonical);

CREATE TABLE IF NOT EXISTS entity_sense (
    entity      TEXT NOT NULL,
    sense_id    TEXT NOT NULL,
    label       TEXT NOT NULL,
    definition  TEXT,
    evidence    TEXT,
    PRIMARY KEY (entity, sense_id)
);

CREATE TABLE IF NOT EXISTS triple_sense (
    triple_id   TEXT NOT NULL,
    entity      TEXT NOT NULL,
    role        TEXT NOT NULL,
    sense_id    TEXT NOT NULL,
    decided_by  TEXT,
    evidence    TEXT,
    PRIMARY KEY (triple_id, entity, role)
);
CREATE INDEX IF NOT EXISTS idx_triple_sense_entity ON triple_sense(entity);

CREATE TABLE IF NOT EXISTS llm_cache (
    cache_key   TEXT PRIMARY KEY,
    step        TEXT,
    response    TEXT,
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS schema_runs (
    step        TEXT,
    item        TEXT,
    status      TEXT,
    detail      TEXT,
    created_at  TEXT,
    PRIMARY KEY (step, item)
);
"""

#: (族, 定义, 典型谓词)——定义要能让 LLM 判边界，不是只给个名字。
FAMILIES: list[tuple[str, str, str]] = [
    ("任职", "自然人在组织中担任职务或受雇", "任职于、就职于、供职于、担任、理事长、CEO"),
    ("归属", "A 属于/隶属于 B；含 is-a「A 是 B 的一种」；含反方向的「拥有/持有」，用 direction 区分",
     "属于、隶属于、归入、是、拥有"),
    ("包含", "A 包含/涵盖/覆盖 B（与「归属」方向相反）", "包含、包括、涵盖、涉及、覆盖"),
    ("位于", "实体在空间上处于某地", "位于、地处、坐落在"),
    ("活动地点", "活动/会议在哪里举办", "举办地、举办于、举办、举办地点"),
    ("发布推出", "主体发布/推出/上市产品", "发布、推出、亮相、上市、发售、展出、展示"),
    ("研发", "研发/研制/开发/技术攻关", "研发、研制、开发、攻关"),
    ("生产制造", "生产/制造/代工/投产", "生产、制造、代工生产、投产"),
    ("使用应用", "使用/采用/搭载/配备/应用于", "使用、采用、搭载、配备、应用于、用于"),
    ("技术支持", "技术或能力对主体的支撑关系", "支持、支撑、赋能、依托、基于、接入、提供、服务"),
    ("合作签约", "合作/签约/供应/采购", "合作、签订、供应、采购、联合研发"),
    ("投资交易", "投资/收购/股权变动/资本运作", "投资、收购、并购、增资、控股、参股、增持、减持、回购、中标、上市"),
    ("组织设立", "设立/成立/组建新组织", "设立、成立、组建、创办"),
    ("参与活动", "参加/出席/参展/主办/承办活动", "参与、参加、出席、参展、主办、承办"),
    ("人物言论", "人物表态、介绍、认为", "表示、指出、认为、强调、介绍、称"),
    ("媒体报道", "媒体或机构报道、披露、统计", "报道、披露、公布、统计"),
    ("因果影响", "推动/促进/带动/提升/影响", "推动、促进、带动、提升、影响、赋能"),
    ("属性参数", "实体的规格、参数、能力、状态", "支持格式、生成图像分辨率、时长、重量、亏损、盈利"),
    ("命名别名", "简称/全称/别名/又名", "简称、全称、别名、又称、又名、缩写"),
    ("比较评价", "高于/低于/领先/超过", "高于、低于、领先、超过、优于"),
    ("法规监管", "处罚/立案/起诉/批准/整治", "处罚、立案、起诉、问询、禁止、批准、整治"),
    ("医疗", "诊断/检查/治疗/患病", "检查出、诊断、治疗、患"),
    ("时间", "事件发生/上线/发布的时间", "发生于、上线于、发布于、将于、首发于"),
    ("竞争替代", "竞争/对标/取代/替代", "竞争、对标、取代、替代"),
    ("其他", "无法归入以上任何族（兜底，保留原谓词）", ""),
]

#: 高频谓词的确定性映射：能直配就不花 LLM 钱。
#: 键是谓词（小写），值是族名。
FAMILY_BY_PREDICATE: dict[str, str] = {}
for _fam, _defn, _ex in FAMILIES:
    for _p in _ex.split("、"):
        _p = _p.strip()
        if _p:
            FAMILY_BY_PREDICATE.setdefault(_p.lower(), _fam)

#: 中文规范谓词（来自 kg/base.py 的 PREDICATES_ZH）到族的映射。
#: 这些是「细粒度关系」，族是粗类，两者不是一回事。
CANON_TO_FAMILY = {
    "收购": "投资交易", "并购": "投资交易", "增持": "投资交易", "减持": "投资交易",
    "回购": "投资交易", "中标": "投资交易", "签订": "合作签约", "投资": "投资交易",
    "增资": "投资交易", "定增": "投资交易", "控股": "投资交易", "参股": "投资交易",
    "上市": "投资交易", "退市": "投资交易", "停牌": "投资交易", "复牌": "投资交易",
    "分红": "投资交易", "质押": "投资交易", "解押": "投资交易", "解禁": "投资交易",
    "破产": "投资交易", "重整": "投资交易", "重组": "投资交易",
    "处罚": "法规监管", "立案": "法规监管", "问询": "法规监管", "起诉": "法规监管",
    "整治": "法规监管",
    "辞职": "任职", "任命": "任职", "担任": "任职",
    "合作": "合作签约", "供应": "合作签约", "采购": "合作签约",
    "设立": "组织设立", "更名": "命名别名",
    "澄清": "人物言论", "否认": "人物言论",
    "披露": "媒体报道",
    "推动": "因果影响", "布局": "因果影响",
    "攻关": "研发", "聚焦": "其他", "融合": "其他", "转化": "其他",
    "亏损": "属性参数", "盈利": "属性参数",
    # 英文规范谓词
    "acquired": "投资交易", "merged_with": "投资交易", "raised": "投资交易",
    "cut": "投资交易", "invested_in": "投资交易", "sanctioned": "法规监管",
    "banned": "法规监管",
    "appointed": "任职", "leads": "任职", "employed_by": "任职",
    "said": "人物言论", "warned": "人物言论",
    "announced": "发布推出", "reported": "媒体报道", "located_in": "位于",
    "signed_with": "合作签约", "partnered_with": "合作签约", "agreed_with": "合作签约",
    "co_founded": "组织设立",
}
FAMILY_BY_PREDICATE.update({k.lower(): v for k, v in CANON_TO_FAMILY.items()})


def ensure_schema(conn: sqlite3.Connection) -> None:
    """建表 + 灌入族目录。可反复调用。"""
    conn.executescript(SCHEMA_SQL)
    conn.executemany(
        "INSERT OR REPLACE INTO rel_family(family, definition, examples) VALUES (?,?,?)",
        FAMILIES,
    )
    conn.commit()