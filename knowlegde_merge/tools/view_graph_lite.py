"""零依赖图谱查看器：读 SQLite，产出**单文件** HTML（内联 JS，无 CDN、无第三方包）。

和 `tools/view_graph.py`（pyvis，需要 .kgvenv + pyvis 资源内联）的分工：
- 这个脚本不依赖任何包，主环境直接跑，生成的 HTML 断网可开、可发给别人。
- 布局在 Python 里**确定性**算好：先按连通分量做径向展开，再把分量排成货架式网格。
  用物理模拟反而会更乱——新闻抽出来的三元组大量是互不相连的小团，斥力会把它们
  推成一盘散沙，缩到全景就只剩零星像素。
- 交互：悬停高亮邻居、滚轮缩放、拖拽平移、拖节点。

用法：
    python tools/view_graph_lite.py --db out/tech.sqlite3 --out out/tech_graph.html
    python tools/view_graph_lite.py --db out/tech.sqlite3 --extractor llm --min-degree 2
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import defaultdict, deque
from pathlib import Path

HTML = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>__TITLE__</title>
<style>
  html,body{margin:0;height:100%;background:#0f1116;color:#e6e6e6;
    font:13px/1.5 "Segoe UI","Microsoft YaHei",sans-serif;overflow:hidden}
  #wrap{position:relative;width:100vw;height:100vh}
  canvas{position:absolute;inset:0;display:block;width:100%;height:100%;cursor:grab}
  canvas:active{cursor:grabbing}
  #hud{position:absolute;left:14px;top:12px;pointer-events:none;
    background:rgba(15,17,22,.78);padding:10px 12px;border-radius:8px;border:1px solid #262a35}
  #hud b{color:#7cc4ff}
  #hud .k{color:#8b93a7}
  #legend{position:absolute;right:14px;bottom:12px;background:rgba(15,17,22,.78);
    padding:8px 12px;border-radius:8px;border:1px solid #262a35;font-size:12px;color:#8b93a7}
  .dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px}
</style></head>
<body><div id="wrap">
<canvas id="c"></canvas>
<div id="hud"></div>
<div id="legend">
  <span class="dot" style="background:#7cc4ff"></span>中文实体
  <span class="dot" style="background:#ffb86b;margin-left:12px"></span>英文实体
  <span style="margin-left:12px">点大小 = 度数</span>
</div>
</div>
<script>
const DATA = __DATA__;
const cv = document.getElementById('c'), ctx = cv.getContext('2d');
const hud = document.getElementById('hud');
const HUD_DEFAULT = __HUD_JSON__;
let W = 0, H = 0, DPR = window.devicePixelRatio || 1;
let zoom = 1, panX = 0, panY = 0, hover = null, dragging = null;

const nodes = DATA.nodes.map(n => ({...n}));
const byId = new Map(nodes.map(n => [n.id, n]));
const links = DATA.links.map(l => ({...l, s: byId.get(l.source), t: byId.get(l.target)}))
  .filter(l => l.s && l.t);
const adj = new Map(nodes.map(n => [n.id, new Set()]));
for (const l of links) { adj.get(l.source).add(l.target); adj.get(l.target).add(l.source); }

function resize() {
  W = cv.clientWidth; H = cv.clientHeight;
  cv.width = W * DPR; cv.height = H * DPR;
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  fitView(); draw();
}
window.addEventListener('resize', resize);

function bounds() {
  let minX = 1e9, minY = 1e9, maxX = -1e9, maxY = -1e9;
  for (const n of nodes) {
    minX = Math.min(minX, n.x - n.r); maxX = Math.max(maxX, n.x + n.r);
    minY = Math.min(minY, n.y - n.r - 18); maxY = Math.max(maxY, n.y + n.r);
  }
  return {minX, minY, maxX, maxY};
}
function fitView() {
  const b = bounds();
  const w = Math.max(1, b.maxX - b.minX), h = Math.max(1, b.maxY - b.minY);
  zoom = Math.min(W / (w * 1.08), H / (h * 1.08), 2.6);
  zoom = Math.max(zoom, 0.05);
  panX = -((b.minX + b.maxX) / 2) * zoom;
  panY = -((b.minY + b.maxY) / 2) * zoom;
}

function draw() {
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  ctx.clearRect(0, 0, W, H);
  ctx.save();
  ctx.translate(W / 2 + panX, H / 2 + panY);
  ctx.scale(zoom, zoom);

  // 字号/线宽用 1/zoom 反算，保证屏幕上恒定 12px（不设上限：设了上限就等于
// 一缩小字也跟着缩成几个像素）。标签按缩放级别分级显示，缩太小就只留高度数的。
  const fs = 12 / zoom, gap = 4 / zoom, minR = 2.4 / zoom;
  const labelMinDeg = zoom < 0.35 ? 3 : (zoom < 0.7 ? 2 : 1);
  const near = hover ? new Set([hover.id, ...adj.get(hover.id)]) : null;

  for (const l of links) {
    const hot = near && near.has(l.source) && near.has(l.target);
    ctx.strokeStyle = hot ? 'rgba(124,196,255,.95)' : 'rgba(150,160,185,.30)';
    ctx.lineWidth = (hot ? 1.7 : 0.9) / zoom;
    ctx.beginPath(); ctx.moveTo(l.s.x, l.s.y); ctx.lineTo(l.t.x, l.t.y); ctx.stroke();
  }
  for (const n of nodes) {
    const dim = near && !near.has(n.id);
    const r = Math.max(n.r, minR);
    ctx.globalAlpha = dim ? 0.22 : 1;
    ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
    ctx.fillStyle = n.lang === 'zh' ? '#7cc4ff' : '#ffb86b';
    ctx.fill();
    if (n.degree >= labelMinDeg || (near && near.has(n.id))) {
      ctx.font = `${fs}px "Segoe UI","Microsoft YaHei",sans-serif`;
      ctx.textAlign = 'center';
      // 先描一圈底色再填字：标签压在连线上也读得清
      ctx.lineWidth = 3 / zoom;
      ctx.strokeStyle = 'rgba(15,17,22,.92)';
      ctx.strokeText(n.label, n.x, n.y - r - gap);
      ctx.fillStyle = '#e8edf7';
      ctx.fillText(n.label, n.x, n.y - r - gap);
    }
    ctx.globalAlpha = 1;
  }
  if (hover) {   // 悬停时把关系写在边上
    ctx.font = `${11 / zoom}px "Segoe UI","Microsoft YaHei",sans-serif`;
    ctx.fillStyle = 'rgba(205,220,245,.95)';
    ctx.textAlign = 'center';
    for (const l of links) {
      if (l.source !== hover.id && l.target !== hover.id) continue;
      ctx.fillText(l.label, (l.s.x + l.t.x) / 2, (l.s.y + l.t.y) / 2 - 3 / zoom);
    }
  }
  ctx.restore();
}

function toWorld(ev) {
  const r = cv.getBoundingClientRect();
  return {
    x: (ev.clientX - r.left - W / 2 - panX) / zoom,
    y: (ev.clientY - r.top - H / 2 - panY) / zoom
  };
}
function needle(x, y) {
  let best = null, bd = 1e9;
  for (const n of nodes) {
    const d = (n.x - x) ** 2 + (n.y - y) ** 2;
    const rr = Math.max(n.r, 2.4 / zoom) + 6 / zoom;
    if (d < bd && d < rr * rr) { bd = d; best = n; }
  }
  return best;
}
function esc(s) { return String(s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }

let panning = false, last = null;
cv.addEventListener('mousedown', ev => {
  const p = toWorld(ev);
  dragging = needle(p.x, p.y);
  if (!dragging) { panning = true; last = [ev.clientX, ev.clientY]; }
});
cv.addEventListener('mousemove', ev => {
  const p = toWorld(ev);
  if (dragging) { dragging.x = p.x; dragging.y = p.y; draw(); return; }
  if (panning && last) {
    panX += ev.clientX - last[0]; panY += ev.clientY - last[1];
    last = [ev.clientX, ev.clientY]; draw(); return;
  }
  const n = needle(p.x, p.y);
  if (n !== hover) {
    hover = n; draw();
    hud.innerHTML = n
      ? `<b>${esc(n.full || n.label)}</b><br><span class="k">度数</span> ${n.degree} ｜ `
        + `<span class="k">语言</span> ${n.lang}`
      : HUD_DEFAULT;
  }
});
window.addEventListener('mouseup', () => { dragging = null; panning = false; last = null; });
cv.addEventListener('wheel', ev => {
  ev.preventDefault();
  const before = toWorld(ev);
  zoom = Math.max(0.05, Math.min(6, zoom * (ev.deltaY < 0 ? 1.15 : 0.87)));
  const after = toWorld(ev);
  panX += (after.x - before.x) * zoom;
  panY += (after.y - before.y) * zoom;
  draw();
}, {passive: false});

hud.innerHTML = HUD_DEFAULT;
resize();
</script></body></html>
"""


def load(db: str, extractor: str = "", limit: int = 300, min_degree: int = 1):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    sql = "SELECT subject, predicate, object, support FROM v_triples"
    args: list[object] = []
    if extractor:
        sql += " WHERE extractor = ?"
        args.append(extractor)
    sql += " ORDER BY support DESC, subject LIMIT ?"
    args.append(limit)
    rows = con.execute(sql, args).fetchall()

    degree: dict[str, int] = defaultdict(int)
    edges = []
    for r in rows:
        s, o = r["subject"], r["object"]
        if not s or not o or s == o:
            continue
        edges.append((s, r["predicate"], o))
        degree[s] += 1
        degree[o] += 1

    edges = [(s, p, o) for s, p, o in edges
             if degree[s] >= min_degree and degree[o] >= min_degree]
    names = {n for s, _, o in edges for n in (s, o)}

    def short(name: str) -> str:
        # 图上只放短名：中文机构名能长到 20 字，全画出来必然互相压字。
        # 完整名在悬停的 HUD 里给。
        return name if len(name) <= 14 else name[:13] + "…"

    return (
        {n: {"id": n, "label": short(n), "full": n, "degree": degree[n],
             "lang": "zh" if any("\u4e00" <= ch <= "\u9fff" for ch in n) else "en"} for n in names},
        edges,
    )


def _components(nodes: dict, edges: list) -> list[list[str]]:
    """并查集找连通分量，按规模降序。"""
    parent = {n: n for n in nodes}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for s, _, o in edges:
        a, b = find(s), find(o)
        if a != b:
            parent[a] = b

    groups: dict[str, list[str]] = defaultdict(list)
    for n in nodes:
        groups[find(n)].append(n)
    return sorted(groups.values(), key=len, reverse=True)


def _radial(names: list[str], edges: list) -> tuple[dict[str, tuple[float, float]], float]:
    """分量内布局：度数最大的节点当圆心，邻居按 BFS 层放到同心环上。

    尺寸按「屏幕像素」设计，不是随便取的世界坐标：每层的环半径至少
    `18 × 该层节点数`，这样标签（约 12px 字、宽 60~110px）沿圆周排开不会重叠。
    代价是图大时会超出视口，由 fitView 统一缩放。
    """
    if len(names) == 1:
        return {names[0]: (0.0, 0.0)}, 30.0

    adj: dict[str, list[str]] = defaultdict(list)
    for s, _, o in edges:
        adj[s].append(o)
        adj[o].append(s)

    deg = {n: len(adj[n]) for n in names}
    root = max(names, key=lambda n: (deg[n], n))
    pos: dict[str, tuple[float, float]] = {root: (0.0, 0.0)}
    depth = {root: 0}
    order = [root]
    q = deque([root])
    while q:
        cur = q.popleft()
        for nb in adj[cur]:
            if nb not in pos:
                pos[nb] = (0.0, 0.0)
                depth[nb] = depth[cur] + 1
                order.append(nb)
                q.append(nb)

    rings: dict[int, list[str]] = defaultdict(list)
    for n in order:
        rings[depth[n]].append(n)

    # 椭圆环：中文标签偏宽，横向给多一点半径
    r_prev = 0.0
    for level in sorted(rings):
        if level == 0:
            continue
        k = len(rings[level])
        rx = max(r_prev + 90.0, 19.0 * k)
        ry = max(r_prev * 0.72 + 60.0, 13.0 * k)
        for i, n in enumerate(rings[level]):
            a = 2 * math.pi * i / max(1, k)
            pos[n] = (rx * math.cos(a), ry * math.sin(a))
        r_prev = rx

    return pos, r_prev + 46.0


def _pack(parts: list, width: float, pad: float = 34.0):
    """货架式排布：返回每个分量的左上角坐标、实际宽度、实际高度。"""
    ox = oy = row_w = row_h = 0.0
    origins: list[tuple[float, float]] = []
    for _pos, size in parts:
        if row_w > 0 and row_w + size > width:
            ox = 0.0
            oy += row_h + pad
            row_w = row_h = 0.0
        origins.append((ox, oy))
        ox += size + pad
        row_w += size + pad
        row_h = max(row_h, size)
    return origins, row_w, oy + row_h


def layout(nodes: dict, edges: list) -> dict[str, tuple[float, float]]:
    """分量各自展开，再按「货架」排布：从左上往右放，一行放不下就换行。

    货架宽度不是拍脑袋定的：对候选宽度逐个试排，把整体尺寸按 1280×560 的视口
    折算成缩放比，取缩放比最大的那个。写死宽度会让行数堆高，fitView 于是被高度
    限死，标签挤成一团。
    """
    comps = _components(nodes, edges)
    parts = []
    for names in comps:
        members = set(names)
        sub = [(s, p, o) for s, p, o in edges if s in members and o in members]
        pos, reach = _radial(names, sub)
        parts.append((pos, reach * 2.0))

    view_w, view_h = 1280.0, 560.0
    best_width, best_zoom = 1240.0, 0.0
    for cand in range(600, 3001, 50):
        _, w, h = _pack(parts, float(cand))
        if w <= 0 or h <= 0:
            continue
        z = min(view_w * 0.94 / w, view_h * 0.94 / h)
        if z > best_zoom:
            best_zoom, best_width = z, float(cand)

    origins, _, _ = _pack(parts, best_width)
    placed: dict[str, tuple[float, float]] = {}
    for (pos, size), (ox, oy) in zip(parts, origins):
        cx, cy = ox + size / 2, oy + size / 2
        for name, (px, py) in pos.items():
            placed[name] = (cx + px, cy + py)
    return placed


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="out/tech.sqlite3")
    ap.add_argument("--extractor", default="", help="只看某个抽取器（rule/llm/kggen）")
    ap.add_argument("--out", default="", help="输出 HTML（默认 out/graph_lite.html）")
    ap.add_argument("--limit", type=int, default=300, help="最多多少条边")
    ap.add_argument("--min-degree", type=int, default=1, help="只画度数 >= N 的节点")
    args = ap.parse_args()

    nodes, edges = load(args.db, args.extractor, args.limit, args.min_degree)
    if not nodes:
        raise SystemExit(f"{args.db} 里没有可画的边（先跑 build）")

    xy = layout(nodes, edges)
    for name, (x, y) in xy.items():
        nodes[name]["x"], nodes[name]["y"] = round(x, 1), round(y, 1)
    # 半径只由度数决定（渲染时再按屏幕下限兜底）
    for n in nodes.values():
        n["r"] = round(4 + min(11, math.sqrt(n["degree"]) * 2.6), 1)

    payload = {
        "nodes": list(nodes.values()),
        "links": [{"source": s, "target": o, "label": p} for s, p, o in edges],
    }

    out = Path(args.out or "out/graph_lite.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    hud = (f"<b>{Path(args.db).name}</b><br>"
           f"<span class='k'>节点</span> {len(nodes)} ｜ <span class='k'>边</span> {len(edges)} ｜ "
           f"<span class='k'>分量</span> {len(_components(nodes, edges))}<br>"
           f"<span class='k'>悬停看关系，滚轮缩放，拖拽平移/拖节点</span>")
    html = (HTML.replace("__DATA__", json.dumps(payload, ensure_ascii=False))
                .replace("__TITLE__", Path(args.db).name)
                .replace("__HUD_JSON__", json.dumps(hud, ensure_ascii=False)))
    out.write_text(html, encoding="utf-8")
    print(f"[graph] {len(nodes)} 个节点 / {len(edges)} 条边 → {out.resolve()}")


if __name__ == "__main__":
    main()