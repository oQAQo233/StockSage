"""看中文 gold 与 llm 抽取的命中详情，判断分数是否可信。"""

import sys
from pathlib import Path

sys.path.insert(0, "src")

from newskg.evaluate import load_gold, norm
from newskg.kg.base import normalize_predicate
from newskg.store import connect

aid = sys.argv[1] if len(sys.argv) > 1 else "aa05dffe5497eb86"
conn = connect("out/cn_gold.sqlite3")
gold = load_gold(Path("data/gold/corpus_zh.csv")).get(aid, set())
pred = {
    (norm(r["subject"]), normalize_predicate(r["predicate"]), norm(r["object"]))
    for r in conn.execute(
        "SELECT subject, predicate, object FROM triples WHERE article_id=? AND extractor='llm'",
        (aid,),
    )
}

print(f"gold {len(gold)} / llm {len(pred)} / 命中 {len(gold & pred)}\n")
print("== 命中 ==")
for s, p, o in sorted(gold & pred):
    print(f"  {s} | {p} | {o}")
print("\n== gold 漏抽 ==")
for s, p, o in sorted(gold - pred):
    print(f"  {s} | {p} | {o}")
print("\n== llm 多抽（前 12）==")
for s, p, o in sorted(pred - gold)[:12]:
    print(f"  {s} | {p} | {o}")