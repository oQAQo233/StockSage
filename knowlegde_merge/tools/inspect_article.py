import csv
import sys
from pathlib import Path

sys.path.insert(0, "src")

from newskg.evaluate import load_gold, norm
from newskg.kg.base import normalize_predicate
from newskg.store import connect

aid = sys.argv[1]
conn = connect("out/corpus.sqlite3")
gold = load_gold(Path("data/gold/corpus.csv")).get(aid, set())

pred = {
    (norm(r["subject"]), normalize_predicate(r["predicate"]), norm(r["object"]))
    for r in conn.execute(
        "SELECT subject, predicate, object FROM triples WHERE article_id=? AND extractor='llm'", (aid,)
    )
}

print(f"gold {len(gold)} 条 / llm {len(pred)} 条 / 命中 {len(gold & pred)} 条\n")
print("== 命中 gold ==")
for s, p, o in sorted(gold & pred):
    print(f"  {s} | {p} | {o}")
print("\n== 不在 gold 里的（需要人工判对错）==")
for s, p, o in sorted(pred - gold):
    print(f"  {s} | {p} | {o}")