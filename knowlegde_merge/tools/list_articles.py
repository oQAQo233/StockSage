import sys

sys.path.insert(0, "src")

from newskg.store import connect

conn = connect(sys.argv[1] if len(sys.argv) > 1 else "out/cn_stock.sqlite3")
for r in conn.execute(
    "SELECT article_id, title, word_count, markdown_path, source FROM articles "
    "ORDER BY word_count DESC"
):
    print(f"{r['article_id']}  {r['word_count']:>5} 字  {r['source']:<14} {r['title'][:40]}")
    print(f"          {r['markdown_path']}")