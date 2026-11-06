"""把 data/gold/parts/*.json 合并成 data/gold/corpus.csv。"""

import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from newskg.kg.base import normalize_predicate  # noqa: E402

PARTS = ROOT / "data" / "gold" / "parts"
OUT = ROOT / "data" / "gold" / "corpus.csv"

# 允许指定目录与输出，便于维护中文 gold（parts_zh -> corpus_zh.csv）
if "--parts" in sys.argv:
    PARTS = Path(sys.argv[sys.argv.index("--parts") + 1])
if "--out" in sys.argv:
    OUT = Path(sys.argv[sys.argv.index("--out") + 1])


def norm(text: str) -> str:
    text = str(text).lower().strip()
    text = re.sub(r"^(the|a|an)\s+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .,;:'\"()[]")


rows = []
per_article = {}
bad = []

for path in sorted(PARTS.glob("*.json")):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        bad.append((path.name, str(exc)))
        continue
    aid = (data.get("article_id") or path.stem).strip()
    seen = set()
    for t in data.get("triples", []):
        s, p, o = norm(t.get("subject", "")), normalize_predicate(t.get("predicate", "")), norm(t.get("object", ""))
        if not (s and p and o) or s == o:
            continue
        if (s, p, o) in seen:
            continue
        seen.add((s, p, o))
        rows.append({"article_id": aid, "subject": s, "predicate": p, "object": o})
    per_article[aid] = len(seen)

print(f"parts 文件 {len(list(PARTS.glob('*.json')))} 个，解析失败 {len(bad)} 个")
for name, err in bad:
    print(f"  BAD {name}: {err}")

print(f"三元组总数 {len(rows)}，涉及文章 {len(per_article)} 篇")
if per_article:
    counts = sorted(per_article.values())
    print(f"每篇 {counts[0]}-{counts[-1]} 条，中位数 {counts[len(counts) // 2]}")

verbose = "--write" in sys.argv
if verbose:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["article_id", "subject", "predicate", "object"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"已写入 {OUT}")
else:
    from collections import Counter

    print("谓词分布:", Counter(r["predicate"] for r in rows).most_common(12))