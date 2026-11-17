"""事件聚类：把"同一件事的多个报道"并成一簇。

为什么需要它：报告里最刺眼的问题是同一件事被写五遍。语料里一个发布会
常有 3~5 家媒体同时报，不聚类就会重复，而且"多源支持"N 条事实会被当成
N 件不同的事。

聚类信号按可信度排：

| 信号 | 权重 | 说明 |
| --- | --- | --- |
| 近重复（SimHash 汉明距离 ≤6） | 3 | 同一篇稿子的转载/改写，最硬 |
| 标题词集 Jaccard ≥0.4 | 2 | 同一事件的不同报道，标题会高度重合 |
| 共享实体 ≥2 个 | 2 | 两家公司一起出现，基本是同一件事 |
| 发布时间相差 ≤48h | 1 | 只是"可能相关"，单靠它不合并 |

总分 ≥3 才合并。只用并查集，不引入聚类库——文章数在报告规模下最多几十篇，
两两比较是 O(n²)，足够。
"""

from __future__ import annotations

import re
from datetime import datetime

#: 这些实体太泛，参与"共享实体"判断只会把无关文章粘在一起。
GENERIC = {
    "AI", "ai", "API", "产品", "技术", "公司", "平台", "系统", "市场", "用户",
    "项目", "行业", "数据", "服务", "功能", "方案", "业务", "应用", "中国", "美国",
}

_TITLE_NOISE = re.compile(r"[^\w\u4e00-\u9fff]+")


def _title_tokens(title: str) -> set[str]:
    """标题切成 2 字片段，用于算 Jaccard。中文不需要分词，二元组够用。"""
    out = set()
    for run in re.findall(r"[\u4e00-\u9fff]+", title or ""):
        for i in range(len(run) - 1):
            out.add(run[i : i + 2])
    for word in re.findall(r"[A-Za-z][A-Za-z0-9]{1,}", title or ""):
        out.add(word.lower())
    return out


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _hamming(simhash_a: str, simhash_b: str) -> int:
    try:
        return bin(int(simhash_a, 16) ^ int(simhash_b, 16)).count("1")
    except (TypeError, ValueError):
        return 64


def _hours_apart(a: str, b: str) -> float | None:
    try:
        ta = datetime.strptime((a or "")[:20], "%Y-%m-%dT%H:%M:%SZ")
        tb = datetime.strptime((b or "")[:20], "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return abs((ta - tb).total_seconds()) / 3600.0


class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def cluster(candidates: list[dict], entities: dict[str, set[str]], threshold: int = 3) -> list[dict]:
    """把候选文章聚类，返回事件列表（按最早发布时间排序）。

    candidates: 每篇含 article_id / title / published_at / simhash
    entities:   article_id → 提及的实体集合
    """
    n = len(candidates)
    uf = _UnionFind(n)
    tokens = [_title_tokens(c.get("title", "")) for c in candidates]

    for i in range(n):
        for j in range(i + 1, n):
            ci, cj = candidates[i], candidates[j]
            score = 0
            reasons = []

            if ci.get("simhash") and cj.get("simhash"):
                if _hamming(ci["simhash"], cj["simhash"]) <= 6:
                    score += 3
                    reasons.append("近重复")

            jac = _jaccard(tokens[i], tokens[j])
            if jac >= 0.4:
                score += 2
                reasons.append(f"标题重合 {jac:.0%}")

            shared = (entities.get(ci["article_id"], set()) & entities.get(cj["article_id"], set())) - GENERIC
            if len(shared) >= 2:
                score += 2
                reasons.append("共享 " + "、".join(sorted(shared)[:3]))

            gap = _hours_apart(ci.get("published_at", ""), cj.get("published_at", ""))
            if gap is not None and gap <= 48:
                score += 1

            if score >= threshold:
                uf.union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(uf.find(i), []).append(i)

    events = []
    for members in groups.values():
        arts = [candidates[i] for i in members]
        ents: set[str] = set()
        for a in arts:
            ents |= entities.get(a["article_id"], set()) - GENERIC
        dates = sorted(a.get("published_at", "") for a in arts if a.get("published_at"))
        # 事件名取最长标题的核心部分：多篇里最长的那条通常信息最全
        lead = max(arts, key=lambda a: len(a.get("title") or ""))
        events.append(
            {
                "title": lead.get("title", ""),
                "article_ids": [a["article_id"] for a in arts],
                "size": len(arts),
                "entities": sorted(ents)[:6],
                "first_seen": dates[0] if dates else "",
                "last_seen": dates[-1] if dates else "",
            }
        )

    events.sort(key=lambda e: e["first_seen"], reverse=True)
    for i, e in enumerate(events, start=1):
        e["event_id"] = f"e{i}"
    return events