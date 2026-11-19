"""把导出的知识图谱渲染成一张预览图（给报告和答辩用）。

只画「度数最高的若干实体」构成的子图：全图 4576 个节点画出来是一团毛线，
抽取核心实体才能看出结构。节点大小按度数，颜色按社区（模块度聚类）。

用 Anaconda 的解释器跑（.venv 里没装 matplotlib）：
    C:\\JRTAT\\Environment\\Anaconda\\python.exe .tmp\\render_graph.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import networkx as nx  # noqa: E402
from matplotlib import font_manager  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GRAPH = ROOT / "out" / "graph.json"
OUT = ROOT / "out" / "graph_preview.png"
TOP_N = 70

# 中文字体：Windows 自带黑体。注意 networkx 画标签时要显式传 font_family，
# 只改 rcParams 不够（它内部会退回 DejaVu Sans，中文全变方框）。
FONT_NAME = "DejaVu Sans"
for path in ("C:/Windows/Fonts/simhei.ttf", "C:/Windows/Fonts/msyh.ttc"):
    if Path(path).exists():
        font_manager.fontManager.addfont(path)
        FONT_NAME = font_manager.FontProperties(fname=path).get_name()
        plt.rcParams["font.family"] = "sans-serif"
        plt.rcParams["font.sans-serif"] = [FONT_NAME, "SimHei", "DejaVu Sans"]
        break
plt.rcParams["axes.unicode_minus"] = False


def main() -> None:
    data = json.loads(GRAPH.read_text(encoding="utf-8"))
    nodes = {n["id"]: n.get("name") or n["id"] for n in data["nodes"]}
    edges = data.get("edges") or data.get("links") or []

    G = nx.MultiDiGraph()
    for e in edges:
        G.add_edge(e["source"], e["target"], predicate=e.get("predicate", ""))

    degree = Counter()
    for u, v in G.edges():
        degree[u] += 1
        degree[v] += 1

    keep = [n for n, _ in degree.most_common(TOP_N)]
    keep_set = set(keep)
    H = G.subgraph(keep_set).copy()
    H.remove_edges_from([(u, v) for u, v in H.edges() if u == v])

    # 社区：把多重有向图转成简单无向图再做模块度聚类
    und = nx.Graph()
    und.add_nodes_from(H.nodes())
    und.add_edges_from((u, v) for u, v in H.edges())
    communities = list(nx.community.greedy_modularity_communities(und))
    communities.sort(key=len, reverse=True)
    color_of = {}
    for i, block in enumerate(communities):
        # 只给最大的 6 个社区上色，其余统一灰掉：70 个节点切成 40 多个色块反而看不清结构
        shade = i if i < 6 else 6
        for n in block:
            color_of[n] = shade

    pos = nx.spring_layout(und, k=1.5 / (len(und) ** 0.5), iterations=300, seed=42)

    fig, ax = plt.subplots(figsize=(15, 10.5), dpi=140)
    ax.set_axis_off()

    nx.draw_networkx_edges(
        H, pos, ax=ax, width=0.6, alpha=0.35,
        edge_color="#8899aa", arrows=False,
    )
    sizes = [140 + 130 * (degree[n] ** 0.9) for n in H.nodes()]
    palette = ["#2f6fb3", "#e6862a", "#3a9e5f", "#b5453c", "#7b5ea7", "#c9a227", "#b8bec6"]
    colors = [palette[color_of.get(n, 6)] for n in H.nodes()]
    nx.draw_networkx_nodes(
        H, pos, ax=ax, node_size=sizes, node_color=colors,
        linewidths=0.8, edgecolors="white", alpha=0.92,
    )
    labels = {n: (nodes.get(n, n)[:14]) for n in H.nodes()}
    nx.draw_networkx_labels(
        H, pos, labels=labels, ax=ax, font_family=FONT_NAME,
        font_size=9.5, font_color="#1a1a1a",
        bbox={"boxstyle": "round,pad=0.15", "fc": "white", "ec": "none", "alpha": 0.8},
    )

    ax.set_title(
        f"科技新闻知识图谱（2026-11-04 ~ 11-11）· 度数最高的 {H.number_of_nodes()} 个实体 · "
        f"{H.number_of_edges()} 条关系",
        fontsize=15, pad=14,
    )
    fig.tight_layout()
    fig.savefig(OUT, bbox_inches="tight", facecolor="white")
    print(f"[render] {OUT}  {OUT.stat().st_size // 1024} KB")
    print(f"  节点 {H.number_of_nodes()}  边 {H.number_of_edges()}  社区 {len(communities)}")


if __name__ == "__main__":
    main()