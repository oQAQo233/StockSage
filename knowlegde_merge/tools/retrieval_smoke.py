"""检索体检：拿一批查询跑「实体链接 + 双路召回」，看榜上有哪些文章。

用途是**改完检索参数后快速看结果**，不是评测——它不判断对错，只把名次摊开给你看。
真正的召回率要 gold 文章集，见 README §6。

用法：
    python tools/retrieval_smoke.py --db out/tech.sqlite3
    python tools/retrieval_smoke.py --db out/tech.sqlite3 --queries queries.txt --top 3
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, "src")

from newskg import retrieve  # noqa: E402
from newskg.store import connect  # noqa: E402

#: 默认这批查询覆盖三类：图上有的实体、只在正文里的说法、语料完全没有的主题。
#: 最后一类用来确认"没覆盖时返回空"，比命中更重要。
DEFAULT_QUERIES = [
    "华为和赛力斯在智能汽车上有什么合作",
    "岚图汽车的销量和新车规划怎么样",
    "小米机器人在具身智能上有什么进展",
    "攀枝花的微电网项目有什么特点",
    "Google Cloud API Gateway 的流式处理是做什么的",
    "问界新 M8 上市发布了什么",
    "鸿蒙智行框架下的合作模式是怎么调整的",
    "商务部对半导体出口管制是什么态度",
    "固态电池产业链最近有什么进展",
    "钠离子电池什么时候能量产",
]


def main() -> int:
    ap = argparse.ArgumentParser(description="批量看检索结果（不生成报告、不调模型）")
    ap.add_argument("--db", default="out/tech.sqlite3")
    ap.add_argument("--queries", help="每行一个查询的文本文件（默认用内置 10 条）")
    ap.add_argument("--top", type=int, default=3)
    args = ap.parse_args()

    queries = DEFAULT_QUERIES
    if args.queries:
        path = Path(args.queries)
        queries = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    conn = connect(args.db)
    covered = 0
    for i, query in enumerate(queries, start=1):
        pack = retrieve.retrieve(conn, query, top=args.top, verbose=False)
        seeds = "、".join(s["name"] for s in pack["seeds"]) or "—"
        if pack["candidates"]:
            covered += 1
        print(f"{i:>2}. {query}")
        print(f"    实体: {seeds} | 入选 {len(pack['candidates'])} 篇")
        for c in pack["candidates"]:
            mark = "图" if c["graph_hits"] else "文"
            print(f"      [{mark}] {c['title'][:46]}")
        if not pack["candidates"]:
            print("      （无结果：语料没覆盖，或查询里的名字图上没有）")

    print(f"\n{len(queries)} 个查询，{covered} 个有结果，{len(queries) - covered} 个返回空。")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())