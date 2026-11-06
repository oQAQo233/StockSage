"""SQLite 存储与三种导出格式。

为什么用 SQLite 而不是一上来就 Neo4j：
课程作业的数据量在几百到几千条，SQLite 零部署、可提交、可 diff；
真要上图数据库时，`export cypher` 生成的语句可以直接灌进 Neo4j。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from .config import DB_PATH, OUT
from .models import Article, Triple

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    article_id     TEXT PRIMARY KEY,
    source         TEXT NOT NULL,
    url            TEXT,
    canonical_url  TEXT,
    title          TEXT,
    published_at   TEXT,
    lang           TEXT,
    license        TEXT,
    content_sha1   TEXT,
    simhash        INTEGER,
    raw_path       TEXT,
    markdown_path  TEXT,
    word_count     INTEGER,
    fetched_at     TEXT,
    duplicate_of   TEXT
);

CREATE TABLE IF NOT EXISTS triples (
    triple_id    TEXT PRIMARY KEY,
    subject      TEXT NOT NULL,
    predicate    TEXT NOT NULL,
    object       TEXT NOT NULL,
    article_id   TEXT NOT NULL,
    published_at TEXT,
    source       TEXT,
    extractor    TEXT,
    confidence   REAL,
    FOREIGN KEY (article_id) REFERENCES articles(article_id)
);

CREATE INDEX IF NOT EXISTS idx_triples_subject ON triples(subject);
CREATE INDEX IF NOT EXISTS idx_triples_object  ON triples(object);
CREATE INDEX IF NOT EXISTS idx_triples_article ON triples(article_id);

-- 抽取台账：记下「哪个抽取器处理过哪篇文章」，哪怕一条也没抽出来。
-- 没有这张表，0 产出的文章会被反复重跑——用 LLM 后端时等于重复付费。
CREATE TABLE IF NOT EXISTS runs (
    article_id TEXT NOT NULL,
    extractor  TEXT NOT NULL,
    n_triples  INTEGER NOT NULL,
    created_at TEXT,
    PRIMARY KEY (article_id, extractor)
);

-- 视图每次启动都重建：CREATE VIEW IF NOT EXISTS 不会更新已存在的视图定义，
-- 改了 SQL 却看不到效果。视图不存数据，删了重建没有代价。
DROP VIEW IF EXISTS v_triples;

CREATE VIEW v_triples AS
SELECT
    t.subject, t.predicate, t.object,
    -- 必须 COUNT(DISTINCT article_id)：三个后端跑同一篇文会各写一行，
    -- 用 COUNT(*) 会把「同一篇文章被抽了三遍」误算成「三篇文章互相印证」。
    COUNT(DISTINCT t.article_id)   AS support,
    MIN(t.published_at)            AS first_seen,
    MAX(t.published_at)            AS last_seen,
    GROUP_CONCAT(DISTINCT t.source) AS sources
FROM triples t
JOIN articles a ON a.article_id = t.article_id AND (a.duplicate_of IS NULL OR a.duplicate_of = '')
GROUP BY t.subject, t.predicate, t.object;
"""


def entity_id(name: str) -> str:
    return hashlib.sha1(name.strip().lower().encode("utf-8")).hexdigest()[:16]


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """老库补列：CREATE TABLE IF NOT EXISTS 不会改已存在的表。"""
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(articles)")}
    for column, ddl in (("raw_path", "ALTER TABLE articles ADD COLUMN raw_path TEXT"),):
        if column not in existing:
            conn.execute(ddl)
    conn.commit()


def upsert_article(conn: sqlite3.Connection, article: Article, duplicate_of: str = "") -> None:
    conn.execute(
        """
        INSERT INTO articles (article_id, source, url, canonical_url, title, published_at,
                              lang, license, content_sha1, simhash, raw_path, markdown_path,
                              word_count, fetched_at, duplicate_of)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(article_id) DO UPDATE SET
            title=excluded.title,
            published_at=excluded.published_at,
            raw_path=excluded.raw_path,
            markdown_path=excluded.markdown_path,
            word_count=excluded.word_count,
            duplicate_of=excluded.duplicate_of
        """,
        (
            article.article_id,
            article.source,
            article.url,
            article.canonical_url,
            article.title,
            article.published_at,
            article.lang,
            article.license,
            article.content_sha1,
            str(article.simhash),
            article.raw_path,
            article.markdown_path,
            article.word_count,
            article.fetched_at,
            duplicate_of,
        ),
    )


def upsert_triples(conn: sqlite3.Connection, triples: list[Triple]) -> int:
    rows = []
    for t in triples:
        # 主键必须含 extractor：三个后端会被要求抽同一篇文章，
        # 若只按 (s,p,o,article_id) 去重，后跑的后端会把先跑的记录覆盖成自己的，
        # 对照实验就失真了（实测 rule 的 3 条被 llm 抢走 1 条，F1 从 0.8% 掉到 0）。
        tid = hashlib.sha1(
            f"{t.subject}|{t.predicate}|{t.object}|{t.article_id}|{t.extractor}".encode("utf-8")
        ).hexdigest()[:20]
        rows.append(
            (tid, t.subject, t.predicate, t.object, t.article_id,
             t.published_at, t.source, t.extractor, t.confidence)
        )
    conn.executemany(
        """INSERT OR REPLACE INTO triples
           (triple_id, subject, predicate, object, article_id, published_at,
            source, extractor, confidence)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    return len(rows)


# --------------------------------------------------------------------------- #
# 统计
# --------------------------------------------------------------------------- #

def stats(conn: sqlite3.Connection) -> dict:
    q = lambda sql, *args: conn.execute(sql, args).fetchall()  # noqa: E731

    articles = q("SELECT COUNT(*) c FROM articles")[0]["c"]
    dups = q("SELECT COUNT(*) c FROM articles WHERE duplicate_of != ''")[0]["c"]
    triples = q("SELECT COUNT(*) c FROM triples")[0]["c"]
    uniq = q("SELECT COUNT(*) c FROM v_triples")[0]["c"]
    sources = q("SELECT source, COUNT(*) c FROM articles GROUP BY source ORDER BY c DESC")
    top_subj = q("SELECT subject, COUNT(*) c FROM triples GROUP BY LOWER(subject) ORDER BY c DESC LIMIT 10")
    top_pred = q("SELECT predicate, COUNT(*) c FROM triples GROUP BY predicate ORDER BY c DESC LIMIT 10")
    multi = q("SELECT COUNT(*) c FROM v_triples WHERE support >= 2")[0]["c"]

    return {
        "articles": articles,
        "duplicates": dups,
        "distinct_sources": len(sources),
        "sources": [dict(r) for r in sources],
        "triples": triples,
        "distinct_triples": uniq,
        "multi_source_triples": multi,
        "top_subjects": [dict(r) for r in top_subj],
        "top_predicates": [dict(r) for r in top_pred],
    }


# --------------------------------------------------------------------------- #
# 导出
# --------------------------------------------------------------------------- #

def export_json(conn: sqlite3.Connection, path: Path | None = None) -> Path:
    path = path or (OUT / "graph.json")
    nodes, edges = {}, []

    for row in conn.execute("SELECT subject, object, predicate, confidence FROM triples"):
        for name in (row["subject"], row["object"]):
            nid = entity_id(name)
            nodes.setdefault(nid, {"id": nid, "name": name})
        edges.append(
            {
                "source": entity_id(row["subject"]),
                "target": entity_id(row["object"]),
                "predicate": row["predicate"],
                "confidence": row["confidence"],
            }
        )

    payload = {"nodes": list(nodes.values()), "edges": edges}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def export_cypher(conn: sqlite3.Connection, path: Path | None = None) -> Path:
    """生成可直接在 Neo4j Browser 粘贴执行的 Cypher。"""
    path = path or (OUT / "graph.cypher")
    lines = ["// 由 newskg 自动生成：先建节点再建边", ""]

    seen: set[str] = set()
    for row in conn.execute("SELECT subject, object FROM triples"):
        for name in (row["subject"], row["object"]):
            nid = entity_id(name)
            if nid in seen:
                continue
            seen.add(nid)
            safe = name.replace("'", "\\'")
            lines.append(f"MERGE (n{ nid }:Entity {{id:'{nid}', name:'{safe}'}});")

    lines.append("")
    for row in conn.execute("SELECT subject, object, predicate, confidence FROM triples"):
        s, o = entity_id(row["subject"]), entity_id(row["object"])
        pred = row["predicate"].upper().replace(" ", "_").replace("-", "_")
        lines.append(
            f"MATCH (a:Entity {{id:'{s}'}}), (b:Entity {{id:'{o}'}}) "
            f"MERGE (a)-[:{pred}]->(b);"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def export_graphml(conn: sqlite3.Connection, path: Path | None = None) -> Path:
    path = path or (OUT / "graph.graphml")
    import networkx as nx

    g = nx.DiGraph()
    for row in conn.execute("SELECT subject, object, predicate, confidence, published_at FROM triples"):
        s, o = entity_id(row["subject"]), entity_id(row["object"])
        g.add_node(s, label=row["subject"])
        g.add_node(o, label=row["object"])
        g.add_edge(s, o, predicate=row["predicate"], confidence=row["confidence"],
                   published_at=row["published_at"] or "")
    nx.write_graphml(g, str(path))
    return path