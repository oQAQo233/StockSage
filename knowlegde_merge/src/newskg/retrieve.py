"""查询检索：把一段自然语言查询变成"相关报道 + 相关事实"。

分三步，每步都只做一件事：

1. **实体链接**：查的是"固态电池产业链"，图里存的是 `固态电池`、`宁德时代`
   ——不先把查询里的名字对到图上的名字，后面全是空的。
2. **双路召回**：图路给"找对了"（有结构、有关系），文本路给"找全了"
   （图没抽到的背景、趋势、评论）。两路各出一个排名，用 RRF 融合。
3. **取证**：把命中文章的段落、三元组、时间线打包成"证据包"，
   交给 report.py 写报告。检索与写作在这里解耦。

M1 只做到这里：不做事件聚类、不做 LLM 精排。
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import events as events_mod
from . import textindex
from .dedup import simhash_hex
from .kg.entities import ALIAS_PATH

#: 图路扩展跳数。行业类查询要 2 跳才够（公司→供应商→上游材料），
#: 3 跳会把整张图吞进来，噪声压过信号。
DEFAULT_HOPS = 2

#: 太泛的实体名不参与链接：它们在正文里到处都是，只会稀释排名。
GENERIC_ENTITIES = {
    "AI", "ai", "API", "产品", "技术", "公司", "平台", "系统", "市场", "用户",
    "项目", "行业", "数据", "服务", "功能", "方案", "业务", "应用",
}

_TIME_PATTERNS = [
    (r"最近\s*(\d+)\s*天", "days"),
    (r"近\s*(\d+)\s*天", "days"),
    (r"最近\s*(\d+)\s*周", "weeks"),
    (r"最近\s*(\d+)\s*个?月", "months"),
    (r"近\s*(\d+)\s*个?月", "months"),
    (r"半年", "months:6"),
    (r"最近一年|近一年|过去一年", "months:12"),
]


@dataclass
class EntityHit:
    name: str
    via: str          # alias / substring / fuzzy
    weight: float


@dataclass
class Candidate:
    article_id: str
    title: str
    source: str
    published_at: str
    url: str
    markdown_path: str
    simhash: str = ""
    score: float = 0.0
    graph_hits: list[str] = field(default_factory=list)   # 命中的实体
    text_hits: int = 0                                    # 文本路命中的段落数
    graph_rank: int = 0
    text_rank: int = 0


def load_aliases() -> dict[str, list[str]]:
    """读别名表：canonical -> [别名...]。查表时 ASCII 不区分大小写。"""
    path = ALIAS_PATH
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    entities = data.get("entities") or data
    return {k: v for k, v in entities.items() if isinstance(v, list)}


def entity_table(conn) -> dict[str, int]:
    """图上出现过的实体和它们的度（出现次数）。"""
    rows = conn.execute(
        """
        SELECT name, COUNT(*) FROM (
            SELECT subject AS name FROM triples
            UNION ALL
            SELECT object AS name FROM triples
        ) GROUP BY name ORDER BY 2 DESC
        """
    ).fetchall()
    return {name: count for name, count in rows}


def parse_time_window(query: str) -> dict | None:
    """从查询里抠时间窗（只认几种常见说法，抠不到就返回 None）。"""
    for pattern, kind in _TIME_PATTERNS:
        m = re.search(pattern, query)
        if not m:
            continue
        if ":" in kind:
            return {"unit": "months", "value": int(kind.split(":")[1])}
        return {"unit": kind, "value": int(m.group(1))}
    return None


def link_entities(
    query: str,
    table: dict[str, int],
    aliases: dict[str, list[str]],
    limit: int = 6,
) -> list[EntityHit]:
    """把查询里的名字对到图上的实体。

    三层，前面命中就不再往下走：
      1. 别名表：`华为` → `Huawei`（图上存的是英文规范名）
      2. 子串：查询里出现了图上实体名本身（`宁德时代`）
      3. 模糊：编辑距离 ≥0.82 的兜底，处理错字/简称
    """
    q = query.lower()
    ascii_tokens = set(re.findall(r"[a-z0-9]+", q))
    hits: dict[str, EntityHit] = {}

    for canonical, names in aliases.items():
        for name in [canonical, *names]:
            if len(name) < 2 or name.lower() not in q:
                continue
            if name.lower() == q or name in GENERIC_ENTITIES:
                continue
            # 图上有这个规范名才算链接成功，否则只是"查询提到了一个我们不认识的词"
            if canonical in table:
                hits[canonical] = EntityHit(canonical, "alias", 1.0 + 0.02 * len(name))

    for name, degree in table.items():
        if len(name) < 2 or name in GENERIC_ENTITIES:
            continue
        if name.lower() in q:
            weight = 0.8 + 0.02 * len(name) + min(0.2, degree / 100)
            cur = hits.get(name)
            if cur is None or cur.weight < weight:
                hits[name] = EntityHit(name, "substring", round(weight, 3))

    # 反向包含：查询写简称，图上是全称（"赛力斯" 之于 "赛力斯集团"）。
    # 用前缀试探而不是整名匹配——查询里的片段往往被连词包着，
    # 直接拿 `token in name` 只能碰巧命中。前缀至少 3 字，否则"中国"能链上一切。
    for name in table:
        if name in hits or len(name) < 3 or name in GENERIC_ENTITIES:
            continue
        lowered = name.lower()
        if lowered.isascii():
            # 英文实体按"首词出现在查询的词表里"判断：Northwind Airlines 对
            # northwind 命中；同时不会因为 "sun" 是 "sunbelt" 的前缀而链上 Sunday。
            head = re.split(r"[^a-z0-9]+", lowered)[0]
            if len(head) >= 3 and head in ascii_tokens:
                hits[name] = EntityHit(name, "partial", 0.6)
            continue
        for length in range(min(len(lowered), 6), 2, -1):
            if lowered[:length] in q:
                hits[name] = EntityHit(name, "partial", round(0.5 + 0.02 * length, 3))
                break

    if not hits:
        # 只对长度≥2 的中英文片段做模糊匹配，避免拿整句话去比
        tokens = [t for t in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9.{}-]{1,}", query)]
        for name in table:
            if len(name) < 2 or name in GENERIC_ENTITIES:
                continue
            for token in tokens:
                ratio = difflib.SequenceMatcher(None, name.lower(), token.lower()).ratio()
                if ratio >= 0.82:
                    hits[name] = EntityHit(name, "fuzzy", round(0.5 * ratio, 3))
                    break

    ordered = sorted(hits.values(), key=lambda h: h.weight, reverse=True)
    return ordered[:limit]


def _article_entities(conn, article_ids: list[str]) -> dict[str, set[str]]:
    """每篇文章抽出来的主体/客体，用来判断"两篇报道是不是同一件事"。"""
    if not article_ids:
        return {}
    marks = ",".join("?" * len(article_ids))
    rows = conn.execute(
        f"SELECT article_id, subject, object FROM triples WHERE article_id IN ({marks})",
        article_ids,
    ).fetchall()
    out: dict[str, set[str]] = {aid: set() for aid in article_ids}
    for row in rows:
        out[row["article_id"]].update({row["subject"], row["object"]})
    return out


def _article_meta(conn, article_ids: list[str]) -> dict[str, dict]:
    if not article_ids:
        return {}
    marks = ",".join("?" * len(article_ids))
    rows = conn.execute(
        f"""
        SELECT article_id, title, source, published_at, url, markdown_path, simhash
        FROM articles WHERE article_id IN ({marks})
        """,
        article_ids,
    ).fetchall()
    return {
        r["article_id"]: {
            "article_id": r["article_id"],
            "title": r["title"] or "",
            "source": r["source"] or "",
            "published_at": r["published_at"] or "",
            "url": r["url"] or "",
            "markdown_path": r["markdown_path"] or "",
            # 统一成 hex：老库存的是 int / REAL，新库存 hex；下游按字符串比较
            "simhash": simhash_hex(r["simhash"]),
        }
        for r in rows
    }


def graph_recall(conn, seeds: list[str], hops: int = DEFAULT_HOPS, max_edges: int = 600):
    """从种子实体出发做 BFS，返回 (边列表, 文章分数, 每篇命中的种子实体)。

    边上带 distance：1 跳最可信，2 跳是"间接相关"，打分随之衰减。
    """
    if not seeds:
        return [], {}, {}
    edges: list[dict] = []
    seen_edges: set[tuple] = set()
    article_score: dict[str, float] = {}
    article_entities: dict[str, set[str]] = {}

    frontier = set(seeds)
    visited = set(seeds)
    for dist in range(1, hops + 1):
        if not frontier or len(edges) >= max_edges:
            break
        marks = ",".join("?" * len(frontier))
        rows = conn.execute(
            f"""
            SELECT subject, predicate, object, article_id, published_at, source
            FROM triples
            WHERE subject IN ({marks}) OR object IN ({marks})
            """,
            [*frontier, *frontier],
        ).fetchall()
        nxt = set()
        for row in rows:
            key = (row["subject"], row["predicate"], row["object"], row["article_id"])
            if key in seen_edges:
                continue
            seen_edges.add(key)
            edges.append(
                {
                    "subject": row["subject"],
                    "predicate": row["predicate"],
                    "object": row["object"],
                    "article_id": row["article_id"],
                    "published_at": row["published_at"],
                    "source": row["source"],
                    "distance": dist,
                }
            )
            article_score[row["article_id"]] = article_score.get(row["article_id"], 0.0) + 1.0 / dist
            article_entities.setdefault(row["article_id"], set()).update(
                {row["subject"], row["object"]} & set(seeds)
            )
            for node in (row["subject"], row["object"]):
                if node not in visited:
                    visited.add(node)
                    nxt.add(node)
        frontier = nxt

    return edges, article_score, article_entities


def text_recall(conn, query: str, per_article: int = 3):
    """文本路：段落级检索，聚合到文章。返回 (文章分数, 段落缓存)。"""
    hits = textindex.search(conn, query, limit=400)
    article_score: dict[str, float] = {}
    para_cache: dict[str, list[dict]] = {}
    for hit in hits:
        bucket = para_cache.setdefault(hit.article_id, [])
        if len(bucket) < per_article:
            bucket.append({"idx": hit.idx, "score": hit.score, "text": hit.text})
        article_score[hit.article_id] = article_score.get(hit.article_id, 0.0) + hit.score
    return article_score, para_cache


def fuse_rrf(rankings: list[list[str]], k: int = 60) -> dict[str, float]:
    """Reciprocal Rank Fusion：两路分数量纲不同，只比名次，最省事也最稳。"""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for pos, article_id in enumerate(ranking, start=1):
            scores[article_id] = scores.get(article_id, 0.0) + 1.0 / (k + pos)
    return scores


def retrieve(
    conn,
    query: str,
    top: int = 8,
    hops: int = DEFAULT_HOPS,
    verbose: bool = True,
) -> dict:
    """完整检索：实体链接 → 双路召回 → RRF 融合 → 返回证据包。"""
    table = entity_table(conn)
    aliases = load_aliases()
    seeds = link_entities(query, table, aliases)
    window = parse_time_window(query)

    if verbose:
        if seeds:
            print("  [link] " + "、".join(f"{h.name}({h.via})" for h in seeds))
        else:
            print("  [link] 没链上任何图上实体，只用文本路召回")

    g_edges, g_score, g_entities = graph_recall(conn, [h.name for h in seeds], hops=hops)
    t_score, t_paras = text_recall(conn, query)

    g_rank = sorted(g_score, key=lambda a: g_score[a], reverse=True)
    t_rank = sorted(t_score, key=lambda a: t_score[a], reverse=True)
    fused = fuse_rrf([g_rank, t_rank])

    g_pos = {a: i + 1 for i, a in enumerate(g_rank)}
    t_pos = {a: i + 1 for i, a in enumerate(t_rank)}

    # 相关性下限：纯文本召回里会混进"只因为命中一个泛词"的文章（如"合作""进展"）。
    # 图路有命中的留住；只有文本证据的，要拿到第一名 25% 以上的分数才留。
    ranked = sorted(fused, key=lambda a: fused[a], reverse=True)
    max_text = max(t_score.values(), default=0.0) or 1.0
    picked = [
        a for a in ranked
        if g_entities.get(a) or t_score.get(a, 0.0) >= 0.25 * max_text
    ][:top]
    if not picked:
        picked = ranked[:1]

    # 覆盖度守门：查询里最具体的两三个词如果全库都没出现过，就是"语料没覆盖"，
    # 这时靠"进展""产业链"这种泛词凑出来的文章是假召回，宁可如实说没有。
    # 判断放在"核心词是否存在于语料"上，而不是"命中了几篇"——后者在语料变大后
    # 会天然升高，前者才是真正的覆盖信号。
    covered = _corpus_covers(conn, query) if not seeds else True
    if not covered:
        picked = []
    meta = _article_meta(conn, picked)

    candidates: list[Candidate] = []
    for article_id in picked:
        m = meta.get(article_id)
        if not m:
            continue
        candidates.append(
            Candidate(
                **m,
                score=round(fused[article_id], 5),
                graph_hits=sorted(g_entities.get(article_id, set())),
                text_hits=len(t_paras.get(article_id, [])),
                graph_rank=g_pos.get(article_id, 0),
                text_rank=t_pos.get(article_id, 0),
            )
        )

    # 时间窗过滤：宁可少几篇，也别把去年的旧闻当"最近"
    if window:
        candidates = _apply_window(candidates, window)
        kept = {c.article_id for c in candidates}
        g_edges = [e for e in g_edges if e["article_id"] in kept]

    seeds_set = {h.name for h in seeds}
    facts = [
        e
        for e in g_edges
        if e["article_id"] in {c.article_id for c in candidates}
        and (e["subject"] in seeds_set or e["object"] in seeds_set or e["distance"] == 1)
    ]

    if verbose:
        print(f"  [graph] {len(g_edges)} 条边 / {len(g_score)} 篇｜[text] {len(t_score)} 篇")
        print(f"  [fuse] 入选 {len(candidates)} 篇")

    # 事件聚类：同一件事的多篇报道合成一簇，报告里只写一次、并列来源。
    art_entities = _article_entities(conn, [c.article_id for c in candidates])
    event_list = events_mod.cluster([c.__dict__ for c in candidates], art_entities)

    if verbose and event_list:
        multi = sum(1 for e in event_list if e["size"] > 1)
        print(f"  [events] {len(event_list)} 个事件（{multi} 个多源）")

    return {
        "query": query,
        "events": event_list,
        "seeds": [{"name": h.name, "via": h.via, "weight": h.weight} for h in seeds],
        "time_window": window,
        "hops": hops,
        "coverage": "graph" if seeds else ("text" if candidates else "none"),
        "candidates": [c.__dict__ for c in candidates],
        "facts": facts,
        "paragraphs": {
            c.article_id: t_paras.get(c.article_id, []) for c in candidates
        },
        "index": textindex.stats(conn),
    }


def _apply_window(candidates: list[Candidate], window: dict) -> list[Candidate]:
    from datetime import datetime, timedelta, timezone

    value = window["value"]
    unit = window["unit"]
    days = {"days": 1, "weeks": 7, "months": 30}[unit] * value
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    kept = [c for c in candidates if (c.published_at or "") >= cutoff]
    return kept or candidates  # 窗口内一篇都没有时不要假装"没有相关内容"，交给报告说明


def _corpus_covers(conn, query: str, probes: int = 3) -> bool:
    """查询里最具体的几个词，在语料里出现过吗？

    探测词这样挑：最长的那个 + 两三个三字词。三字词最接近中文的"词"，
    而 4-gram 往往是跨词窗口（"商务部对"），拿它探测会把有覆盖的查询误判成没覆盖。
    只要有一个探测词出现过就算覆盖："华为的机器人业务"命中华为就够。
    """
    terms = [t for t in textindex.query_terms(query) if len(t) >= 3]
    if not terms:
        return True
    by_len = sorted(terms, key=len, reverse=True)
    tri = [t for t in terms if len(t) == 3][: probes - 1]
    chosen = (by_len[:1] + tri)[:probes] or by_len[:probes]

    for term in chosen:
        hit = conn.execute(
            "SELECT 1 FROM paragraphs WHERE text LIKE ? LIMIT 1", (f"%{term}%",)
        ).fetchone()
        if hit:
            return True
    return False


def evidence_path(out_dir: Path, query: str) -> Path:
    slug = re.sub(r"[^\w\u4e00-\u9fff]+", "-", query.strip())[:48].strip("-") or "query"
    return out_dir / f"{slug}.evidence.json"


def save_evidence(pack: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = evidence_path(out_dir, pack["query"])
    path.write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


__all__ = [
    "Candidate",
    "EntityHit",
    "evidence_path",
    "load_aliases",
    "retrieve",
    "save_evidence",
]












