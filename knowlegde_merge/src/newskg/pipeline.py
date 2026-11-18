"""端到端流程：发现 → 抓取 → 抽取 → 规范化 → 去重 → 入库 → 建图。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import FIXTURES, MD, RAW, ROOT
from .convert import Extracted, build_markdown_document, extract_article, fetch_html
from .dedup import canonicalize_url, content_fingerprint, find_duplicates, simhash, simhash_int
from .models import Article, ArticleStub, Triple, sha1_of
from .kg.entities import canonicalize
from .quality import assess
from .sources.base import RateLimiter
from .sources.timestamps import pick_published
from .store import upsert_article, upsert_triples, connect
from . import config


class BodyRejected(ValueError):
    """正文质量不达标（几乎只剩页脚，或短到没有信息量）。"""

    def __init__(self, reason: str, title: str, url: str):
        super().__init__(reason)
        self.reason = reason
        self.title = title
        self.url = url


def _rel(path: Path) -> str:
    """库里存相对路径，工程整体拷走也不会失效。"""
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log_rejected(reason: str, title: str, url: str) -> None:
    """拒收也要留痕：跑 2000 篇时，`out/rejected.jsonl` 是唯一能回查"少了什么"的地方。"""
    try:
        path = ROOT / "out" / "rejected.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"ts": _now(), "reason": reason, "title": title, "url": url}
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass  # 日志写不进去不该让抓取失败


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

    # 标题优先用源站 RSS/API 给的那条：它是编辑写好的完整标题。
    # 反例就在 Solidot——页面 <title> 只有「奇客Solidot」这个站点名，
    # 用 trafilatura 的元数据会把它当标题，两篇不同文章于是同名，入库后无法区分。
    title = (stub.title or ext.title or "").strip() or "(untitled)"
    # 页面元数据常常只有日期，feed 的 pubDate 才精确到分钟；挑带钟点的那个（见 timestamps.py）
    published = pick_published(stub.published_at, ext.published_at)
    body_text = ext.body_text or ext.body_markdown

    # 质量闸：正文几乎只剩页脚（zqrb 那类）或短到没有信息量的，不进库、不写 md，
    # 更不会在 build 时花钱抽成垃圾三元组。判据见 quality.py。
    verdict = assess(body_text, title)
    if not verdict.ok:
        raise BodyRejected(verdict.reason, title, stub.url)
    body_text = verdict.body

    article = Article(
        article_id=article_id,
        source=stub.source,
        url=stub.url,
        canonical_url=canonical,
        title=title,
        published_at=published,
        lang=verdict.lang,
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
    rejected: list[str] = []

    print("[2/3] 抓取 + 正文抽取 + 转 Markdown")
    for i, stub in enumerate(stubs, 1):
        try:
            article = process_stub(stub, limiter)
        except BodyRejected as exc:
            rejected.append(exc.reason)
            _log_rejected(exc.reason, exc.title, exc.url)
            print(f"      [{i}/{len(stubs)}] 拒收 {exc.reason}｜{exc.title[:40]}")
            continue
        except Exception as exc:
            failed += 1
            print(f"      [{i}/{len(stubs)}] 跳过 {stub.url[:70]} -> {type(exc).__name__}: {exc}")
            continue
        articles.append(article)
        print(
            f"      [{i}/{len(stubs)}] {article.word_count:>5} 词  "
            f"{article.source:<8} {article.title[:56]}"
        )

    print(f"      成功 {len(articles)} 篇，失败 {failed} 篇，正文不达标 {len(rejected)} 篇")
    if rejected:
        # 按原因归类，跑 2000 篇时一眼能看出闸门拦的是什么
        kinds: dict[str, int] = {}
        for reason in rejected:
            head = reason.split("（")[0]
            kinds[head] = kinds.get(head, 0) + 1
        for head, n in sorted(kinds.items(), key=lambda kv: -kv[1]):
            print(f"        拒收原因 {head}: {n} 篇")

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

    「未被处理过」按 `runs` 台账算，不按 `triples` 有没有行算：抽到 0 条也是一次
    已经付过费的处理，必须记下来，否则下次重跑还要再付一次。
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
    ok = 0
    failed: list[tuple[str, str]] = []
    for row in rows:
        md_path = ROOT / row["markdown_path"]
        if not md_path.exists():
            continue
        body = _strip_front_matter(md_path.read_text(encoding="utf-8"))
        # 老库里可能还留着历史废稿：抽之前再判一次，不合格就记台账跳过，
        # 免得 2000 篇跑完才发现有几篇只抽出一堆电话和许可证号。
        verdict = assess(body, row["title"] or "")
        if not verdict.ok:
            conn.execute(
                "INSERT OR REPLACE INTO runs (article_id, extractor, n_triples, created_at) VALUES (?,?,?,?)",
                (row["article_id"], extractor.name, 0, _now()),
            )
            conn.commit()
            if verbose:
                print(f"      {row['article_id'][:8]}  跳过：{verdict.reason}", flush=True)
            continue
        body = verdict.body
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
            simhash=simhash_int(row["simhash"]),
            raw_path="",
            markdown_path=row["markdown_path"],
        )
        try:
            triples: list[Triple] = extractor.extract(article)
        except Exception as exc:
            # 单篇失败不能拖垮整批：2000 篇要跑几十小时，中途一次超时或限流
            # 就让整轮白跑是不可接受的。不记台账，下次重跑会再试这一篇。
            failed.append((row["article_id"], f"{type(exc).__name__}: {exc}"))
            print(
                f"      {row['article_id'][:8]}  抽取失败 -> {type(exc).__name__}: {exc}",
                flush=True,
            )
            continue
        ok += 1
        # 实体归一化放在抽取之后、入库之前：同一个实体在不同文章里写成
        # `Capcom` / `卡普空` 时，不合并就是把度数切碎，跨文章共现永远统计不出来。
        triples, merged = canonicalize(triples)
        if merged and verbose:
            sample = "、".join(list(merged)[:3])
            print(f"      实体归一：合并 {len(merged)} 处（如 {sample}）", flush=True)
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
    if rows and verbose:
        print(
            f"      本轮：成功 {ok} 篇，失败 {len(failed)} 篇，新增三元组 {total} 条",
            flush=True,
        )
    if failed:
        print(f"      失败的文章没有记账，下次 build 会自动重试；本次失败 {len(failed)} 篇：", flush=True)
        for aid, err in failed[:5]:
            print(f"        {aid[:8]}  {err[:90]}", flush=True)
    if rows and total == 0 and verbose:
        print(
            "      注意：这批文章一条三元组也没抽出来。常见原因是模型端点不可用、"
            "或 KGGen 的 JSON 输出被截断（可调 NEWSKG_KGGEN_MAX_TOKENS）。"
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


