"""把 SQLite 里的三元组渲染成一个可交互的 HTML 图，用浏览器打开即可。

为什么用 pyvis 而不是 matplotlib：三元组天然是图结构，让节点可拖拽、悬停看边标签，
比静态图片有用得多。pyvis 把 networkx 的图转成 vis.js 的前端页面。

依赖：pyvis + networkx（随 kg-gen 装在 .kgvenv 里，不用另装）

用法：
    set PYTHONPATH=src;.kgvenv
    .venv\\Scripts\\python.exe tools\\view_graph.py --db out/corpus.sqlite3 --extractor llm
    # 然后双击 out/graph.html
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, "src")

from newskg.store import connect  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="把知识图谱渲染成交互式 HTML")
    ap.add_argument("--db", default="out/corpus.sqlite3", help="SQLite 库路径")
    ap.add_argument("--extractor", default="", help="只看某个抽取器（rule/llm/kggen），留空看全部")
    ap.add_argument("--out", default="out/graph.html", help="输出的 HTML 路径")
    ap.add_argument("--limit", type=int, default=300, help="最多画多少条边（太多会卡）")
    ap.add_argument("--min-degree", type=int, default=1, help="只画度数 >= N 的节点")
    args = ap.parse_args()

    try:
        from pyvis.network import Network
    except ImportError:
        print(
            "缺少 pyvis。它随 kg-gen 装在 .kgvenv 里，请把 .kgvenv 加进 PYTHONPATH：\n"
            "  set PYTHONPATH=src;.kgvenv",
            file=sys.stderr,
        )
        return 2

    conn = connect(args.db)
    sql = "SELECT subject, predicate, object, confidence FROM triples"
    where, params = [], ()
    if args.extractor:
        where.append("extractor = ?")
        params = (args.extractor,)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY confidence DESC LIMIT {int(args.limit)}"

    rows = conn.execute(sql, params).fetchall()
    if not rows:
        print(f"没有数据。检查 --db {args.db} 是否跑过 build，或 --extractor 是否写对。")
        return 1

    # 统计度数，用来决定节点大小和颜色
    degree: dict[str, int] = {}
    for r in rows:
        degree[r["subject"]] = degree.get(r["subject"], 0) + 1
        degree[r["object"]] = degree.get(r["object"], 0) + 1

    net = Network(
        height="820px", width="100%", directed=True,
        bgcolor="#ffffff", font_color="#222222",
        cdn_resources="in_line",      # 把 JS 内联进 HTML，断网也能打开
    )
    net.barnes_hut(gravity=-8000, central_gravity=0.3, spring_length=160)

    for name, deg in degree.items():
        if deg < args.min_degree:
            continue
        net.add_node(
            name,
            label=name,
            size=10 + min(deg, 20) * 1.6,
            color="#e8663d" if deg >= 5 else "#5b9bd5",
            title=f"{name}（度 {deg}）",
        )

    added = 0
    for r in rows:
        if r["subject"] not in [n["id"] for n in net.nodes] or r["object"] not in [n["id"] for n in net.nodes]:
            continue
        net.add_edge(r["subject"], r["object"], title=r["predicate"], label=r["predicate"])
        added += 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    net.write_html(str(out), notebook=False, open_browser=False)
    _inline_assets(out)

    print(f"节点 {len(net.nodes)} 个，边 {added} 条")
    print(f"已生成：{out.resolve()}")
    print("用浏览器打开它，可以拖拽节点、滚轮缩放、鼠标悬停看关系名。")
    return 0


def _inline_assets(path: Path) -> None:
    """把 vis.js / vis.css 内联进 HTML。

    pyvis 0.3 的 `cdn_resources="in_line"` 并不真的内联，而是写成
    `../node_modules/vis/dist/vis.js` 这种不存在的相对路径，打开会白屏。
    这里用 pyvis 包内自带的 vis-9.1.2 手工拼进去，得到一个断网也能看的单文件。
    """
    import re

    import pyvis

    lib = Path(pyvis.__file__).parent / "lib" / "vis-9.1.2"
    js_file, css_file = lib / "vis-network.min.js", lib / "vis-network.css"
    if not (js_file.exists() and css_file.exists()):
        return  # 没有自带资源就保持原样

    html = path.read_text(encoding="utf-8")
    # 用函数作为替换值，避免 JS 里的反斜杠被当成转义序列
    html = re.sub(
        r'<script[^>]*src="[^"]*vis[^"]*"[^>]*>\s*</script>',
        lambda m: "<script>" + js_file.read_text(encoding="utf-8", errors="ignore") + "</script>",
        html, flags=re.I,
    )
    html = re.sub(
        r'<link[^>]*href="[^"]*vis[^"]*"[^>]*>',
        lambda m: "<style>" + css_file.read_text(encoding="utf-8", errors="ignore") + "</style>",
        html, flags=re.I,
    )
    # bootstrap 只是 pyvis 模板的装饰，删掉不影响图，断网也不会报错
    html = re.sub(r'<script[^>]*src="https://cdn\.jsdelivr\.net[^"]*"[^>]*>\s*</script>', "", html, flags=re.I)
    html = re.sub(r'<link[^>]*href="https://cdn\.jsdelivr\.net[^"]*"[^>]*>', "", html, flags=re.I)
    path.write_text(html, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())