import sys

sys.path.insert(0, "src")

from newskg.store import connect

conn = connect(sys.argv[1] if len(sys.argv) > 1 else "out/corpus.sqlite3")
for row in conn.execute("SELECT extractor, COUNT(*) n, SUM(n_triples) t FROM runs GROUP BY extractor"):
    print(f"{row['extractor']:8s} 处理 {row['n']:3d} 篇, 产出 {row['t']:4d} 条")
print("triples 表:", conn.execute("SELECT COUNT(*) c FROM triples").fetchone()["c"])