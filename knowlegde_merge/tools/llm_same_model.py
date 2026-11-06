"""同模型对照：用 kggen 那个模型（claude-haiku-5-5）再跑一遍手写 prompt 的 llm 后端。

目的：把「工具差异」和「模型差异」分开。三方表里 llm 用 deepseek、kggen 用 haiku，
直接比会混淆；这里在同一模型下各跑一次，才能说清是 prompt 的功劳还是模型的功劳。

结果打印到终端，不覆盖 out/ 里已有的产物。
"""

import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")
sys.path.append(".kgvenv")

from newskg import evaluate as ev
from newskg.kg import build_extractor
from newskg.pipeline import build_graph
from newskg.store import connect

SRC, DST = Path("out/corpus.sqlite3"), Path("out/corpus_haiku.sqlite3")
if not DST.exists():
    shutil.copy(SRC, DST)

conn = connect(str(DST))
conn.execute("DELETE FROM triples WHERE extractor='llm'")
conn.execute("DELETE FROM runs WHERE extractor='llm'")
conn.commit()

gold = ev.load_gold(Path("data/gold/corpus.csv"))
extractor = build_extractor("llm")
print(f"后端={extractor.name}  模型={extractor.model}  文章数={len(gold)}")

t0 = time.time()
added = build_graph(conn, extractor, only_ids=set(gold))
elapsed = time.time() - t0

m = ev.evaluate_extractor(conn, gold, "llm")
print(f"\n耗时 {elapsed:.1f}s  写入 {added} 条")
print(
    f"strict  P={m['strict']['precision']:.2%}  R={m['strict']['recall']:.2%}  F1={m['strict']['f1']:.2%}\n"
    f"relaxed P={m['relaxed']['precision']:.2%}  R={m['relaxed']['recall']:.2%}  F1={m['relaxed']['f1']:.2%}"
)