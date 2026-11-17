"""段落索引：把每篇 Markdown 正文切成段落，建 FTS5 索引，供查询检索用。

三个设计选择，都是为了少引入依赖：

1. **切段落而不是切句子**：句子太短，检索时缺上下文，生成时也没法用。
   段落是检索与写作之间的最小公约数。

2. **用 FTS5 的 trigram 分词器**：SQLite 自带的 `unicode61` 不认识中文，
   一整段中文会被当成一个词，等于没建索引。trigram 把文本拆成三字符序列，
   中文可以直接做子串匹配，零依赖、零服务。
   代价是**两个字的词查不到**（trigram 至少要三个字符），所以两字词退回 LIKE。

3. **切词保守、打分统一**：切得粗糙只影响排序，切断实词才会漏召回，
   所以只切连接词/疑问词。候选来自 FTS 与 LIKE 两路，最后用同一套
   「词长 × 出现次数」打分，避免两路分数量纲不同没法比。

索引存在同一个 SQLite 文件里，跟着 `out/*.sqlite3` 一起删、一起建。
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from .config import ROOT

#: 段落合并的目标长度：太碎丢上下文，太长检索命中后不好裁剪。
PARA_TARGET = 420
PARA_MAX = 900

#: 只在**不可能是实词的一部分**的地方切开。
#:
#: 单字停用词是坑：切"和"会切断"和平"，切"为"会把"华为"切成"华"——
#: 后者直接让最常见的实体名消失。所以单字只保留语气词和连词，
#: "在/是/有/为/对/把/被/这/那/个/都/还/更"这些常见于词内部的全部去掉。
STOPWORDS = [
    "有什么", "有没有", "是什么", "怎么样", "为什么", "能不能", "可不可以",
    "最近", "近期", "目前", "现在", "当前", "关于", "相关", "有关", "一下", "请问",
    "哪些", "哪个", "如何", "怎么", "什么", "多少", "是否",
    "以及", "或者", "还有", "并且", "但是", "因为", "所以",
    "的", "和", "与", "及", "或", "吗", "呢", "吧", "啊", "么",
]

LONG_PIECE = 4   # 超过这个长度的片段还要切窗口（"智能汽车上"→"智能汽车"）
WINDOW = 4       # 窗口长度

_SPLIT = re.compile("|".join(re.escape(w) for w in sorted(STOPWORDS, key=len, reverse=True)))
_ASCII_WORD = re.compile(r"[A-Za-z][A-Za-z0-9+._-]{1,}")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")


@dataclass
class ParaHit:
    article_id: str
    idx: int
    text: str
    score: float


def paragraphs_of(body: str) -> list[str]:
    """按空行切段；过长段落再按句末标点合并到 PARA_TARGET 左右。"""
    raw = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    out: list[str] = []
    for para in raw:
        para = re.sub(r"\s+", " ", para)
        if len(para) <= PARA_MAX:
            out.append(para)
            continue
        sentences = [s for s in re.split(r"(?<=[。！？；!?;])", para) if s.strip()]
        buf = ""
        for sent in sentences:
            if buf and len(buf) + len(sent) > PARA_TARGET:
                out.append(buf.strip())
                buf = sent
            else:
                buf += sent
        if buf.strip():
            out.append(buf.strip())
    return out


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS paragraphs (
            article_id TEXT NOT NULL,
            idx        INTEGER NOT NULL,
            text       TEXT NOT NULL,
            PRIMARY KEY (article_id, idx)
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS paragraphs_fts
            USING fts5(text, article_id UNINDEXED, idx UNINDEXED, tokenize='trigram');
        """
    )


def index_pending(conn: sqlite3.Connection, force: bool = False, verbose: bool = True) -> int:
    """给还没进索引的文章建段落（force=True 时整表重建）。"""
    ensure_schema(conn)
    if force:
        conn.execute("DELETE FROM paragraphs")
        conn.execute("DELETE FROM paragraphs_fts")
        conn.commit()

    done = {r[0] for r in conn.execute("SELECT DISTINCT article_id FROM paragraphs")}
    rows = conn.execute(
        "SELECT article_id, title, markdown_path FROM articles "
        "WHERE (duplicate_of IS NULL OR duplicate_of = '') ORDER BY published_at DESC"
    ).fetchall()

    added = 0
    for row in rows:
        article_id = row[0]
        if article_id in done:
            continue
        md = ROOT / (row[2] or "")
        if not md.exists():
            continue
        body = _body(md.read_text(encoding="utf-8"))
        # 标题作为第 0 段入索引：不少查询只命中标题，正文里反而没有这几个字
        paras = [row[1] or ""] + paragraphs_of(body)
        for i, text in enumerate(paras):
            if not text:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO paragraphs (article_id, idx, text) VALUES (?,?,?)",
                (article_id, i, text),
            )
            conn.execute(
                "INSERT INTO paragraphs_fts (text, article_id, idx) VALUES (?,?,?)",
                (text, article_id, i),
            )
        added += 1
    conn.commit()
    if verbose and added:
        print(f"  [index] 新增 {added} 篇的段落索引（共 {_count(conn)} 段）", flush=True)
    return added


def _count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM paragraphs").fetchone()[0]


def _body(text: str) -> str:
    """去掉 YAML front matter，只留正文。"""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            return parts[2].strip()
    return text


def query_terms(query: str, max_terms: int = 20) -> list[str]:
    """切出检索词，长词在前（长词更具体，截断时优先保留）。"""
    terms: list[str] = []
    for word in _ASCII_WORD.findall(query):
        terms.append(word.lower())

    for run in _CJK_RUN.findall(query):
        for piece in _SPLIT.split(run):
            piece = piece.strip()
            if len(piece) < 2:
                continue
            if len(piece) <= LONG_PIECE:
                terms.append(piece)
                continue
            # 长片段按 3-gram 全滑窗 + 4-gram 隔位滑窗。
            # 只保 4-gram 会漏掉"商务部"这种三字词——长查询里三字词恰恰最常见。
            terms.append(piece)
            for i in range(0, len(piece) - 3 + 1):
                terms.append(piece[i : i + 3])
            for i in range(0, len(piece) - 4 + 1, 2):
                terms.append(piece[i : i + 4])

    seen, out = set(), []
    for term in sorted(terms, key=len, reverse=True):
        if term in seen:
            continue
        seen.add(term)
        out.append(term)
        if len(out) >= max_terms:
            break
    return out


def search(
    conn: sqlite3.Connection,
    query: str,
    limit: int = 400,
    terms: list[str] | None = None,
) -> list[ParaHit]:
    """返回与查询相关的段落，按分数降序。

    候选＝FTS（≥3 字）∪ LIKE（两字词），打分统一在 Python 里算：
    `词长 × 该词在段落里的出现次数`。长词命中一次胜过短词命中多次。
    """
    ensure_schema(conn)
    terms = terms or query_terms(query)
    if not terms:
        return []

    candidates: dict[tuple[str, int], str] = {}

    long_terms = [t for t in terms if len(t) >= 3]
    if long_terms:
        match = " OR ".join('"{}"'.format(t.replace('"', "")) for t in long_terms)
        try:
            rows = conn.execute(
                """
                SELECT p.article_id, p.idx, p.text
                FROM paragraphs_fts JOIN paragraphs p
                  ON p.article_id = paragraphs_fts.article_id AND p.idx = paragraphs_fts.idx
                WHERE paragraphs_fts MATCH ?
                LIMIT ?
                """,
                (match, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        for article_id, idx, text in rows:
            candidates[(article_id, idx)] = text

    for term in (t for t in terms if len(t) == 2):
        rows = conn.execute(
            "SELECT article_id, idx, text FROM paragraphs WHERE text LIKE ? LIMIT ?",
            (f"%{term}%", limit),
        ).fetchall()
        for article_id, idx, text in rows:
            candidates[(article_id, idx)] = text

    hits: list[ParaHit] = []
    for (article_id, idx), text in candidates.items():
        score = 0.0
        for term in terms:
            cnt = text.count(term)
            if cnt:
                # 2 字词权重 1，3 字 1.5，4 字 2……长词更具体，也更可信
                score += (len(term) / 2.0) * min(cnt, 3)
        if score:
            hits.append(ParaHit(article_id=article_id, idx=idx, text=text, score=round(score, 2)))

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]


def stats(conn: sqlite3.Connection) -> dict:
    ensure_schema(conn)
    return {
        "articles_indexed": conn.execute(
            "SELECT COUNT(DISTINCT article_id) FROM paragraphs"
        ).fetchone()[0],
        "paragraphs": _count(conn),
    }

