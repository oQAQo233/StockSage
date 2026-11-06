"""挑事件型文章（公告/中标/回购/增持/上会/签订），排除行情播报与评论。"""

import sys

sys.path.insert(0, "src")

from newskg.store import connect

EVENT_HINTS = ("公告", "中标", "签订", "签署", "回购", "增持", "减持", "收购", "并购",
               "上会", "过会", "定增", "增资", "设立", "重组", "分拆", "投资", "中标")
SKIP_HINTS = ("午评", "早评", "收评", "快讯：", "异动", "盘中", "涨停潮", "跌停",
              "指数", "成交额", "研判", "观点", "午盘", "开盘", "低开", "高开")

conn = connect(sys.argv[1] if len(sys.argv) > 1 else "out/cn_gold.sqlite3")
rows = conn.execute(
    "SELECT article_id, title, word_count FROM articles "
    "WHERE (duplicate_of IS NULL OR duplicate_of='') ORDER BY word_count DESC"
).fetchall()

print("=== 事件型（推荐标 gold）===")
for r in rows:
    t = r["title"] or ""
    if any(k in t for k in EVENT_HINTS) and not any(k in t for k in SKIP_HINTS):
        print(f"{r['article_id'][:8]}  {r['word_count']:>5} 字  {t[:52]}")

print()
print("=== 前 12 长文（含不推荐的）===")
for r in rows[:12]:
    print(f"{r['article_id'][:8]}  {r['word_count']:>5} 字  {(r['title'] or '')[:52]}")