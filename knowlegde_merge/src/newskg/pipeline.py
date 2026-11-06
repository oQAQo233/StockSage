"""端到端流程：发现 → 抓取 → 抽取 → 规范化 → 去重 → 入库 → 建图。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import FIXTURES, MD, RAW, ROOT
from .convert import Extracted, build_markdown_document, extract_article, fetch_html
from .dedup import canonicalize_url, content_fingerprint, find_duplicates, simhash
from .models import Article, ArticleStub, Triple, sha1_of
from .sources.base import RateLimiter
from .store import upsert_article, upsert_triples, connect
from . import config


def _rel(path: Path) -> str:
    """库里存相对路径，工程整体拷走也不会失效。"""
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def process_stub(stub: ArticleStub, limiter: RateLimiter | None = None) -> Article:
    """把一条 stub 变成一篇可入库的 Article。"""
    canonical = canonicalize_url(stub.url)
    article_id = sha1_of(canonical)[:16]

    raw_html = ""
    ext = Extracted()

    if stub.source == "fixtures":
        fixture_file = stub.extra.get("file", "")
        html_path = FIXTURES / fixture_file
        if not html_path.exists():
            raise FileNotFoundError(f"样例文件不存在：{html_path}")
        raw_html = html_path.read_text(encoding="utf-8")
        ext = extract_article(raw_html, url=stub.url or "https://example.org/fixture")
    elif stub.extra.get("body_text"):
        # Guardian 已经在 API 里给了正文，省一次请求
        text = stub.extra["body_text"]
        ext.title = stub.title
        ext.body_text = text
        ext.body_markdown = "\n\n".join(
            p.strip() for p in text.split("\n\n") if p.strip()
        )
        ext.published_at = stub.published_at
        ext.engine = "guardian-api"
        raw_html = f"<html><body><article><p>{text}</p></article></body></html>"
    else:
        if limiter:
            limiter.wait(stub.url)
        raw_html = fetch_html(stub.url)
        ext = extract_article(raw_html, url=stub.url)

    if not ext.body_text and not ext.body_markdown:
        raise ValueError("正文抽取结果为空（可能是付费墙、反爬页或纯 JS 渲染）")

    title = (ext.title or stub.title or "").strip() or "(untitled)"
    published = ext.published_at or stub.published_at or ""
    body_text = ext.body_text or ext.body_markdown

    article = Article(
        article_id=article_id,
        source=stub.source,
        url=stub.url,
        canonical_url=canonical,
        title=title,
        published_at=published,
        lang=stub.lang,
        license=stub.license,
        authors=ext.authors,
        body=body_text,
        content_sha1=content_fingerprint(body_text),
        simhash=simhash(body_text),
        raw_path="",
        markdown_path="",
    )

    # 原文留档（可复现、可回溯）
    RAW.mkdir(parents=True, exist_ok=True)
    MD.mkdir(parents=True, exist_ok=True)
    raw_path = RAW / f"{article_id}.html"
    raw_path.write_text(raw_html, encoding="utf-8")

    md_path = MD / f"{article_id}.md"
    md_path.write_text(
        build_markdown_document(
            {
                "article_id": article_id,
                "source": stub.source,
                "url": stub.url,
                "canonical_url": canonical,
                "title": title,
                "published_at": published,
                "lang": stub.lang,
                "license": stub.license,
                "authors": ext.authors,
                "content_sha1": article.content_sha1,
                "word_count": article.word_count,
                "extractor": ext.engine,
            },
            ext.body_markdown,
        ),
        encoding="utf-8",
    )

    article.raw_path = _rel(raw_path)
    article.markdown_path = _rel(md_path)
    return article


def ingest(source, query: str | None, limit: int, days: int, conn: sqlite3.Connection) -> list[Article]:
    """跑一个源：发现 → 逐篇处理 → 去重 → 入库。"""
    print(f"[1/3] 发现文章：source={source.name} query={query!r} limit={limit}")
    stubs = source.discover(query, limit, days)
    print(f"      拿到 {len(stubs)} 条候选")

    limiter = RateLimiter(config.POLITE_DELAY)
    articles: list[Article] = []
    failed = 0

    print("[2/3] 抓取 + 正文抽取 + 转 Markdown")
    for i, stub in enumerate(stubs, 1):
        try:
            article = process_stub(stub, limiter)
        except Exception as exc:
            failed += 1
            print(f"      [{i}/{len(stubs)}] 跳过 {stub.url[:70]} -> {type(exc).__name__}: {exc}")
            continue
        articles.append(article)
        print(
            f"      [{i}/{len(stubs)}] {article.word_count:>5} 词  "
            f"{article.source:<8} {article.title[:56]}"
        )

    print(f"      成功 {len(articles)} 篇，失败 {failed} 篇")

    print("[3/3] 去重 + 入库")
    dup_map = find_duplicates(
        articles,
        threshold=config.RUN.dedup_hamming,
        min_jaccard=config.RUN.dedup_jaccard,
    )
    for article in articles:
        upsert_article(conn, article, dup_map.get(article.article_id, ""))
    conn.commit()
    print(f"      入库 {len(articles)} 篇，判定重复 {len(dup_map)} 篇")
    return articles


def build_graph(conn: sqlite3.Connection, extractor, limit: int | None = None,
                verbose: bool = True, only_ids: set[str] | None = None) -> int:
    """对库里尚未被**这个抽取器**处理过的文章跑抽取。

    按 extractor 去重，而不是按文章去重——否则第二个后端会因为没有「未处理文章」而一条都跑不出来，
    三方对照就无从谈起。

    only_ids 用来把范围限制在「有 gold 的文章」：没标注的文章跑了也不进指标，
    对按 token 计费的后端就是纯浪费。
    """
    sql = """
        SELECT * FROM articles a
        WHERE (a.duplicate_of IS NULL OR a.duplicate_of = '')
          AND NOT EXISTS (
              SELECT 1 FROM runs r
              WHERE r.article_id = a.article_id AND r.extractor = ?
          )
        ORDER BY a.published_at DESC
    """
    rows = conn.execute(sql, (extractor.name,)).fetchall()
    if only_ids:
        rows = [r for r in rows if r["article_id"] in only_ids]
    if limit:
        rows = rows[:limit]

    total = 0
    for row in rows:
        md_path = ROOT / row["markdown_path"]
        if not md_path.exists():
            continue
        body = _strip_front_matter(md_path.read_text(encoding="utf-8"))
        article = Article(
            article_id=row["article_id"],
            source=row["source"],
            url=row["url"] or "",
            canonical_url=row["canonical_url"] or "",
            title=row["title"] or "",
            published_at=row["published_at"] or "",
            lang=row["lang"] or "en",
            license=row["license"] or "",
            authors=[],
            body=body,
            content_sha1=row["content_sha1"] or "",
            simhash=int(row["simhash"] or 0),
            raw_path="",
            markdown_path=row["markdown_path"],
        )
        triples: list[Triple] = extractor.extract(article)
        total += upsert_triples(conn, triples)
        # 台账无条件记录：抽到 0 条也算处理过，否则每次都会重跑（LLM 就是重付费）
        conn.execute(
            "INSERT OR REPLACE INTO runs (article_id, extractor, n_triples, created_at) VALUES (?,?,?,?)",
            (article.article_id, extractor.name, len(triples), _now()),
        )
        # 每篇单独提交：LLM 可能跑几十分钟，中途崩掉不该把已付费的结果一起丢掉。
        conn.commit()
        if verbose:
            print(
                f"      {article.article_id[:8]}  {len(triples):>3} 条三元组  {article.title[:50]}",
                flush=True,
            )

    conn.commit()
    if rows and total == 0 and verbose:
        print(
            "      注意：这批文章一条三元组也没抽出来。规则后端只认少数几种动词模板，"
            "真实新闻的叙述体很容易全军覆没——这正是要换 --backend llm 的理由。"
        )
    return total


def _strip_front_matter(text: str) -> str:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            return parts[2].strip()
    return text


def reset(conn: sqlite3.Connection, keep_articles: bool = False) -> None:
    conn.execute("DELETE FROM triples")
    conn.execute("DELETE FROM runs")
    if not keep_articles:
        conn.execute("DELETE FROM articles")
    conn.commit()