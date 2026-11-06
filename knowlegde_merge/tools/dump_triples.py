import sys

sys.path.insert(0, "src")

from newskg.store import connect

conn = connect(sys.argv[1] if len(sys.argv) > 1 else "out/demo.sqlite3")
for row in conn.execute(
    """SELECT a.article_id, a.title, t.subject, t.predicate, t.object
       FROM triples t JOIN articles a USING(article_id)
       ORDER BY a.article_id, t.subject"""
):
    print(f"{row['article_id'][:8]} | {row['subject']:32s} | {row['predicate']:14s} | {row['object']}")