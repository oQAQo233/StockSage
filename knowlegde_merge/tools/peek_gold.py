import collections
import csv
import sys

rows = list(csv.DictReader(open("data/gold/corpus.csv", encoding="utf-8")))
counts = collections.Counter(r["article_id"] for r in rows)
print("总条数:", len(rows), "| 每篇:", sorted(counts.values()))

want = sys.argv[1] if len(sys.argv) > 1 else None
for r in rows:
    if want and not r["article_id"].startswith(want):
        continue
    print(f"{r['article_id'][:8]}  {r['subject']} | {r['predicate']} | {r['object']}")