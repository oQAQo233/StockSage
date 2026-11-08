"""将主项目报告、主项目数据库或原始三元组数据库渲染为离线交互图。

依赖：pyvis（无需安装 KGGen）。在 StockSage 根目录运行：
    python -m stocksage.visualization --report output/fresh_energy_20261108/report.json --out output/fresh_energy_20261108/graph_interactive.html
    # 可加 --symbol 300750.SZ 筛选证券；也支持 --db 路径。
    # 原始 newskg 三元组数据库仍支持 --db out/corpus.sqlite3 --extractor llm。
    # 生成的 HTML 内嵌脚本和样式，可以直接双击打开。
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import sqlite3
from contextlib import closing
from pathlib import Path


def report_graph(report, symbol=None):
    """读取主项目已完成时间过滤的图，不从原始新闻重建未来关系。"""
    reports = report.get("reports", {})
    if symbol and symbol not in reports:
        raise ValueError(f"报告中没有证券 {symbol}")
    selected = {symbol: reports[symbol]} if symbol else reports
    nodes, edges = {}, {}
    for analysis in selected.values():
        graph = analysis["graph"]
        for node in graph["nodes"]:
            nodes[node["id"]] = node
        for edge in graph["edges"]:
            key = (edge["source"], edge["target"], edge["relation"], edge.get("evidence_id"), edge.get("triple_id"))
            edges[key] = edge
    dataset = report.get("dataset", {})
    return dict(nodes=list(nodes.values()), edges=list(edges.values()), dataset_id=dataset.get("dataset_id", ""),
                label=dataset.get("label", "StockSage"), symbol=symbol or "全部证券",
                extracted_relations=sum(bool(e.get("triple_id")) for e in edges.values()))


def load_graph(report_path=None, db_path=None, symbol=None, extractor=""):
    if report_path:
        return report_graph(json.loads(Path(report_path).read_text(encoding="utf-8-sig")), symbol)
    path = Path(db_path or "output/stocksage.sqlite3").resolve()
    if not path.is_file():
        raise ValueError(f"数据库不存在：{path}")
    with closing(sqlite3.connect(path.as_uri()+"?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "snapshots" in tables:
            row = conn.execute("SELECT report_json FROM snapshots WHERE id=1").fetchone()
            if not row:
                raise ValueError("主项目数据库没有报告快照")
            return report_graph(json.loads(row[0]), symbol)
        if "triples" not in tables:
            raise ValueError("数据库没有 triples 或 snapshots 表")
        sql = "SELECT subject,predicate,object,confidence,article_id,extractor FROM triples"
        rows = conn.execute(sql + (" WHERE extractor=?" if extractor else ""), (extractor,) if extractor else ()).fetchall()
        nodes, edges = {}, []
        for row in rows:
            ids = []
            for name in (row["subject"],row["object"]):
                identifier = "entity:"+hashlib.sha256(name.casefold().encode()).hexdigest()[:16]
                nodes[identifier] = dict(id=identifier,label=name,kind="entity")
                ids.append(identifier)
            edges.append(dict(source=ids[0],target=ids[1],relation=row["predicate"],evidence_id=row["article_id"],
                              backend=row["extractor"],confidence=row["confidence"],triple_id=f"legacy:{len(edges)}"))
        return dict(nodes=list(nodes.values()),edges=edges,label="newskg 三元组图谱",dataset_id="",symbol="全部实体",extracted_relations=len(edges))


def select_graph(graph, limit=300, min_degree=1, extractor=""):
    if limit < 1 or min_degree < 0:
        raise ValueError("limit须为正整数，min-degree须非负")
    candidates = [e for e in graph["edges"] if not extractor or not e.get("triple_id") or e.get("backend")==extractor][:limit]
    degree = {}
    for e in candidates:
        for identifier in (e["source"],e["target"]):
            degree[identifier] = degree.get(identifier,0)+1
    nodes = [n for n in graph["nodes"] if degree.get(n["id"],0)>=min_degree]
    ids = {n["id"] for n in nodes}
    edges = [e for e in candidates if e["source"] in ids and e["target"] in ids]
    return {**graph,"nodes":nodes,"edges":edges,"degree":degree,
            "extracted_relations":sum(bool(e.get("triple_id")) for e in edges)}


def render_graph(graph, out):
    from pyvis.network import Network
    colors = dict(stock="#264f6c",company="#5c9e8c",industry="#a2c5a3",news="#77a4ba",event="#c7a065",source="#a29ab4",entity="#db8b5b")
    net = Network(height="760px",width="100%",directed=True,bgcolor="#f6f9f7",font_color="#243f46",cdn_resources="in_line")
    net.barnes_hut(gravity=-6500, central_gravity=.25,spring_length=160)
    for n in graph["nodes"]:
        kind = n.get("kind","entity")
        label = n["label"]
        details = [label,"类型："+kind]
        for key in ("evidence_id","published_at","available_at","url"):
            if n.get(key): details.append(f"{key}：{n[key]}")
        net.add_node(n["id"],label=label[:24]+("…" if len(label)>24 else ""),group=kind,
                     size=12+min(graph["degree"].get(n["id"],0),20)*1.4,color=colors.get(kind,"#77a4ba"),
                     shape="box" if kind=="news" else "dot",title="<br>".join(html.escape(d) for d in details))
    for index,e in enumerate(graph["edges"]):
        details=[e["relation"],"证据："+str(e.get("evidence_id") or "证券主数据")]
        if e.get("evidence_text"): details.append(e["evidence_text"])
        net.add_edge(e["source"],e["target"],id=f"edge-{index}",label=e["relation"],
                     title="<br>".join(html.escape(d) for d in details),color="#c29550" if e.get("triple_id") else "#a8bdb8")
    out=Path(out)
    out.parent.mkdir(parents=True,exist_ok=True)
    # PyVis write_html 在 Windows 使用系统默认编码，内联 JS 可能导致 GBK 编码失败。
    out.write_text(net.generate_html(notebook=False),encoding="utf-8")
    _inline_assets(out)
    # 在单文件中直接呈现数据集身份与零抽取状态，避免把静态边当作抽取成果。
    text=out.read_text(encoding="utf-8")
    banner=(f'<header style="padding:18px 24px;font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#163e42;color:white">'
            f'<strong>StockSage · 交互知识图谱</strong><div style="font-size:12px;margin-top:7px">'
            f'{html.escape(graph["label"])}<br>{html.escape(graph["dataset_id"])} · {html.escape(graph["symbol"])}<br>'
            f'{len(graph["nodes"])} 个节点 · {len(graph["edges"])} 条边 · 抽取候选关系 {graph["extracted_relations"]} 条。'
            '其余关系为证券主数据、新闻提及/报道/来源；未分类事件沿用占位标签。可拖拽、缩放、悬停查看证据。</div></header>')
    text=text.replace("<body>","<body>"+banner,1)
    out.write_text(text,encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="把知识图谱渲染成交互式 HTML")
    inputs = ap.add_mutually_exclusive_group()
    inputs.add_argument("--db", help="原 newskg 或主项目 SQLite 数据库")
    inputs.add_argument("--report", type=Path, help="主项目 report.json（含已对齐的股票图谱）")
    ap.add_argument("--symbol", help="主项目证券代码，留空合并全部股票")
    ap.add_argument("--extractor", default="", help="只看某个抽取器（rule/llm/kggen），留空看全部")
    ap.add_argument("--out", default="output/graph.html", help="输出的 HTML 路径")
    ap.add_argument("--limit", type=int, default=300, help="最多画多少条边（太多会卡）")
    ap.add_argument("--min-degree", type=int, default=1, help="只画度数 >= N 的节点")
    args = ap.parse_args()

    try:
        graph = select_graph(load_graph(args.report,args.db,args.symbol,args.extractor),args.limit,args.min_degree,args.extractor)
        if not graph["nodes"]:
            raise ValueError("没有可显示的节点，请检查输入和筛选条件")
        out = render_graph(graph, args.out)
    except (ValueError,OSError,sqlite3.Error,ImportError) as error:
        ap.exit(2,f"图谱生成失败：{error}；PyVis依赖安装命令：python -m pip install pyvis\n")
    print(f"节点 {len(graph['nodes'])} 个，边 {len(graph['edges'])} 条；抽取候选关系 {graph['extracted_relations']} 条")
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
