"""按「正文短的先跑」补抽 KGGen：今晚时间有限，先把能出成果的做完。

为什么不直接用 `newskg.cli build`：
build 的排序是 `published_at DESC`，会从最新那篇开始，而最新那天的稿子里有 3000 字长文，
单篇要 6~8 分钟，一晚上只能出几篇。这里改成按 word_count 升序，
同样时间里能多完成几篇；剩下的长文以后继续跑（台账是幂等的，重跑不会丢）。

用法（必须由前台调用守着跑，进程一旦被放养就会卡住）：
    $env:PYTHONPATH="src;.kgvenv"; $env:LITELLM_LOCAL_MODEL_COST_MAP="true"
    .\\.venv\\Scripts\\python.exe -u .tmp\\run_short_first.py <最多跑几篇>
"""

import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, "src")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "true")

from newskg.config import ROOT  # noqa: E402
from newskg.kg.bylines import drop_noise  # noqa: E402
from newskg.kg.entities import canonicalize  # noqa: E402
from newskg.kg.kggen import KGGenExtractor  # noqa: E402
from newskg.models import Article  # noqa: E402
from newskg.pipeline import _strip_front_matter  # noqa: E402
from newskg.quality import assess  # noqa: E402
from newskg.store import connect, upsert_triples  # noqa: E402

BUDGET = float(os.getenv("NEWSKG_BUDGET_SEC", "999999"))
CAP = int(os.getenv("NEWSKG_CAP", "999999"))


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> None:
    conn = connect()
    rows = conn.execute(
        """
        SELECT a.* FROM articles a
        WHERE (a.duplicate_of IS NULL OR a.duplicate_of = '')
          AND NOT EXISTS (SELECT 1 FROM runs r WHERE r.article_id = a.article_id)
        ORDER BY a.word_count ASC
        """
    ).fetchall()

    print(f"[runner] 待抽 {len(rows)} 篇，按字数升序；预算 {BUDGET:.0f} 秒", flush=True)
    ext = KGGenExtractor()
    t_start = time.time()
    done = 0
    total_tri = 0

    for row in rows:
        if time.time() - t_start > BUDGET or done >= CAP:
            print(f"[runner] 到达预算，本轮收工", flush=True)
            break
        md = ROOT / row["markdown_path"]
        if not md.exists():
            continue
        body = _strip_front_matter(md.read_text(encoding="utf-8"))
        verdict = assess(body, row["title"] or "")
        if not verdict.ok:
            conn.execute(
                "INSERT OR REPLACE INTO runs (article_id, extractor, n_triples, created_at) VALUES (?,?,?,?)",
                (row["article_id"], ext.name, 0, now()),
            )
            conn.commit()
            continue

        art = Article(
            article_id=row["article_id"],
            source=row["source"],
            url=row["url"] or "",
            canonical_url=row["canonical_url"] or "",
            title=row["title"] or "",
            published_at=row["published_at"] or "",
            lang=row["lang"] or "zh",
            license=row["license"] or "",
            authors=[],
            body=verdict.body,
            content_sha1=row["content_sha1"] or "",
            simhash=0,
            raw_path="",
            markdown_path=row["markdown_path"],
        )

        t0 = time.time()
        try:
            triples = ext.extract(art)
        except Exception as exc:  # noqa: BLE001
            print(f"  ❌ {row['article_id'][:8]} {type(exc).__name__}: {str(exc)[:60]}", flush=True)
            continue
        dt = time.time() - t0

        triples, _ = drop_noise(triples, art.body)
        triples, _ = canonicalize(triples)
        total_tri += upsert_triples(conn, triples)
        conn.execute(
            "INSERT OR REPLACE INTO runs (article_id, extractor, n_triples, created_at) VALUES (?,?,?,?)",
            (row["article_id"], ext.name, len(triples), now()),
        )
        conn.commit()
        done += 1
        print(
            f"  ✅ {dt:5.0f}s  {row['word_count']:>5d}字  {len(triples):>3d}条  "
            f"{(row['title'] or '')[:34]}  [本轮第 {done} 篇]",
            flush=True,
        )

    print(f"[runner] 本轮完成 {done} 篇，新增三元组 {total_tri} 条", flush=True)
    conn.close()


if __name__ == "__main__":
    main()