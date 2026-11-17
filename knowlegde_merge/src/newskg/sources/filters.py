"""发现层的两道过滤：时间窗 + 题材相关性。

第一道（时间窗）与题材无关，纯元数据判断。
第二道（题材相关性）默认交给 **LLM 打分**：把「什么叫科技新闻」写成提示词，
让模型给每条候选打 0-10 分，再用阈值截断。原来那套关键词打分只保留为
**降级路径**（没有配 key、或模型整批失败时），不再是主机制。

为什么过滤要单独成模块：过滤逻辑与「怎么拿到 URL」无关，塞进各适配器会导致
每个源各写一遍、行为不一致。这里做成装饰器式包装，任何源套上就获得两个能力，
下游抓取/转换/抽取/入库完全不需要知道过滤发生过。

为什么打分放在**抓正文之前**：一条候选的标题+摘要就足够判断"是不是科技新闻"，
先打分能直接省掉对无关文章的整篇抓取（既省时间，也少给对方站点添麻烦）。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from ..config import TOPIC
from ..models import ArticleStub
from .topic_llm import TopicScore, make_scorer

# --------------------------------------------------------------------------- #
# 第一道：时间窗
# --------------------------------------------------------------------------- #

#: 允许的「未来偏差」：部分源的时区标注有误，可能比本地时钟快几分钟。
FUTURE_TOLERANCE = timedelta(hours=6)

#: 最多给多少条候选打分。多要候选能提高"过阈值数量"的稳定性，但每条都要花 token，
#: 60 条 / 阈值 6 的配置在实测里足够凑满 limit=10~30。
MAX_SCORED = 60

#: 单源配额：任一来源最多占 limit 的这个比例（软限制，凑不满会放宽）
SRC_CAP_RATIO = 0.35


def parse_iso(raw: str) -> datetime | None:
    """解析 ISO 8601（含 Z 结尾）；失败返回 None。"""
    if not raw:
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def within_window(published_at: str, days: float, now: datetime | None = None) -> bool | None:
    """发布时间是否落在「距现在 days 天以内」。

    返回 True/False；无法解析时间时返回 None，由调用方决定保留还是丢弃
    （不同源的元数据质量差别很大，把选择权留给调用方比默默猜更安全）。
    """
    pub = parse_iso(published_at)
    if pub is None:
        return None
    now = now or datetime.now(timezone.utc)
    age = now - pub
    if age < -FUTURE_TOLERANCE:
        return False  # 明显来自未来的时间戳，通常是解析错误
    return age <= timedelta(days=days)


# --------------------------------------------------------------------------- #
# 第二道（降级路径）：关键词粗筛
# --------------------------------------------------------------------------- #

#: 强信号：单独出现就基本能确定是科技新闻
TECH_STRONG = (
    "芯片", "半导体", "光刻", "晶圆", "制程", "算力", "大模型", "人工智能",
    "处理器", "显卡", "GPU", "CPU", "NPU", "服务器", "数据中心", "云计算",
    "操作系统", "开源", "算法", "量子计算", "机器人", "自动驾驶", "无人机",
    "网络安全", "漏洞", "数据泄露", "加密", "卫星", "航天", "传感器",
    "半导体设备", "AI", "LLM", "chip", "semiconductor", "GPU", "LLM",
    "artificial intelligence", "quantum", "cybersecurity", "vulnerability",
    "open source", "data center", "processor", "robot", "autonomous",
)

#: 泛科技词：科技新闻常见，但商业/社会新闻里也常见，权重低
TECH_WEAK = (
    "手机", "平板", "笔记本", "耳机", "智能家居", "智能汽车", "车机",
    "应用", "软件", "互联网", "平台", "数字化", "智能化", "技术", "研发",
    "科技", "创新", "专利", "标准", "监管", "隐私", "电池", "光伏",
    "mobile", "app", "software", "internet", "platform", "technology",
    "startup", "innovation", "privacy", "battery", "electric vehicle",
)


def _ascii_word_pattern(words: tuple[str, ...]) -> re.Pattern | None:
    """英文关键词按词边界匹配：否则 "AI" 会命中 "chair"、"said"。"""
    ascii_only = [w for w in words if w.isascii() and len(w) > 1]
    if not ascii_only:
        return None
    joined = "|".join(re.escape(w) for w in sorted(ascii_only, key=len, reverse=True))
    return re.compile(rf"(?<![A-Za-z])(?:{joined})(?![A-Za-z])", re.IGNORECASE)


_STRONG_ASCII = _ascii_word_pattern(TECH_STRONG)
_WEAK_ASCII = _ascii_word_pattern(TECH_WEAK)


def stub_text(stub: ArticleStub) -> str:
    """把 stub 里所有可用于判断的文本拼起来（标题 + 摘要 + 自带标签）。"""
    extra = stub.extra or {}
    return " ".join(
        [
            stub.title or "",
            str(extra.get("summary") or ""),
            str(extra.get("digest") or ""),
        ]
    )


def keyword_score(stub: ArticleStub) -> TopicScore:
    """粗略打分：只用于 LLM 不可用时的降级，不追求准确。

    规则：强信号 3 分、泛科技词 1 分，封顶 10；零命中为 0。
    这样阈值 6 大致等价于「命中两个强信号词」或「一个强信号 + 三个泛词」。
    """
    text = stub_text(stub)
    points = 0
    hits: list[str] = []

    for word in TECH_STRONG:
        if word.isascii():
            continue
        if word in text:
            points += 3
            hits.append(word)
    for word in TECH_WEAK:
        if word.isascii():
            continue
        if word in text:
            points += 1
            hits.append(word)
    if _STRONG_ASCII:
        for match in set(m.group(0).lower() for m in _STRONG_ASCII.finditer(text)):
            points += 3
            hits.append(match)
    if _WEAK_ASCII:
        for match in set(m.group(0).lower() for m in _WEAK_ASCII.finditer(text)):
            points += 1
            hits.append(match)

    return TopicScore(score=min(10, points), reason="关键词：" + "/".join(hits[:3]) if hits else "")


# --------------------------------------------------------------------------- #
# 包装器
# --------------------------------------------------------------------------- #


class FilteredSource:
    """给任意源套上「时间窗 + 题材相关性」两道过滤。

    为了过滤后仍能凑够 limit 条，会按 over_scan 倍数向底层源多要一些候选；
    抓取发生在过滤之后，所以多要的只是元数据，不会多抓正文。
    """

    def __init__(
        self,
        inner,
        topic: str | None = "tech",
        keep_undated: bool = False,
        over_scan: int = 4,
        threshold: float | None = None,
        mode: str = "auto",
        desc: str = "",
    ):
        self.inner = inner
        self.name = getattr(inner, "name", "filtered")
        self.license = getattr(inner, "license", "")
        self.topic = (topic or "").lower() or None
        self.keep_undated = keep_undated
        self.over_scan = max(over_scan, 1)
        self.threshold = TOPIC.threshold if threshold is None else threshold
        self.mode = (mode or "auto").lower()
        self.desc = desc

    # ------------------------------------------------------------------ 入口
    def discover(self, query: str | None, limit: int, days: int) -> list[ArticleStub]:
        raw = self.inner.discover(query, limit * self.over_scan, days)

        kept_time, dropped_time, undated = [], 0, 0
        for stub in raw:
            ok = within_window(stub.published_at, days)
            if ok is None:
                undated += 1
                if self.keep_undated:
                    kept_time.append(stub)
                continue
            if ok:
                kept_time.append(stub)
            else:
                dropped_time += 1

        print(
            f"[filter] 时间窗 ≤ {days:g} 天：候选 {len(raw)} 篇 → 保留 {len(kept_time)} 篇"
            f"（超窗丢弃 {dropped_time}，无发布时间 {undated}"
            f"{'，已保留' if self.keep_undated else '，已丢弃'}）"
        )

        if not self.topic:
            return kept_time[:limit]
        return self._apply_topic(kept_time, limit)

    # ------------------------------------------------------------------ 题材
    def _apply_topic(self, candidates: list[ArticleStub], limit: int) -> list[ArticleStub]:
        if not candidates:
            return []

        pool = candidates[:MAX_SCORED]
        if len(candidates) > MAX_SCORED:
            print(f"[topic] 候选 {len(candidates)} 条，只给前 {MAX_SCORED} 条打分（控制成本）")

        scores = self._score(pool)

        kept: list[ArticleStub] = []
        dropped: list[tuple[ArticleStub, TopicScore]] = []
        buckets = {"0-2": 0, "3-4": 0, "5-6": 0, "7-8": 0, "9-10": 0}
        for stub, score in zip(pool, scores):
            bucket = "0-2" if score.score <= 2 else "3-4" if score.score <= 4 else "5-6" if score.score <= 6 else "7-8" if score.score <= 8 else "9-10"
            buckets[bucket] += 1
            stub.extra = {**(stub.extra or {}), "topic_score": score.score, "topic_reason": score.reason}
            if score.score >= self.threshold:
                kept.append(stub)
            else:
                dropped.append((stub, score))

        dist = "  ".join(f"{k}:{v}" for k, v in buckets.items())
        print(f"[topic] 分数分布 {dist}")
        print(
            f"[topic] 阈值 ≥{self.threshold:g}：保留 {len(kept)} 篇，丢弃 {len(dropped)} 篇"
            f"（候选池 {len(pool)} 篇）"
        )
        self._print_examples("保留", [(s, sc) for s, sc in zip(pool, scores) if sc.score >= self.threshold])
        self._print_examples("丢弃", dropped)
        return self._apply_source_cap(kept, limit)

    @staticmethod
    def _apply_source_cap(kept: list[ArticleStub], limit: int = 0) -> list[ArticleStub]:
        """单源配额：任一来源不得超过 SRC_CAP_RATIO × limit。

        只按分数与时间选，高产源会吃满额度——实测 11 个科技源里，IT之家一天几十条，
        前 20 篇里曾占 8 篇，多源等于白找。配额是软限制：配额内凑不满 limit 时，
        再从被压下的条目里按原顺序补齐，所以不会因为配额少给结果。
        """
        cap = max(1, int(round(limit * SRC_CAP_RATIO))) if limit else 0
        if not cap:
            return kept

        # 纯顺序（不设配额）会取哪些：用来算「到底有几篇是被配额挤掉的」。
        # 直接拿 len(kept)-len(picked) 会把「limit 截断」也算成让位，措辞就错了。
        natural = kept[:limit]

        picked: list[ArticleStub] = []
        overflow: list[ArticleStub] = []
        counts: dict[str, int] = {}
        for stub in kept:
            src = stub.source or ""
            if len(picked) < limit and counts.get(src, 0) < cap:
                picked.append(stub)
                counts[src] = counts.get(src, 0) + 1
            else:
                overflow.append(stub)

        for stub in overflow:
            if len(picked) >= limit:
                break
            picked.append(stub)

        picked_ids = {id(stub) for stub in picked}
        displaced = [stub for stub in natural if id(stub) not in picked_ids]
        if displaced:
            srcs = {stub.source for stub in natural} - {stub.source for stub in picked}
            print(
                f"[topic] 单源配额 {cap} 篇：{len(displaced)} 篇高分同源文章让位"
                f"（{'/'.join(sorted(srcs))}）"
            )
        return picked

    def _score(self, pool: list[ArticleStub]) -> list[TopicScore]:
        if self.mode == "keyword":
            print("[topic] 使用关键词粗筛（--topic-mode keyword）")
            return [keyword_score(s) for s in pool]

        scorer = make_scorer(domain_desc=self.desc)
        if not scorer.configured:
            if self.mode == "llm":
                raise RuntimeError(
                    "科技相关性打分需要 LLM，但没配置。请设置 NEWSKG_TOPIC_API_KEY / "
                    "NEWSKG_TOPIC_BASE_URL / NEWSKG_TOPIC_MODEL（见 .env.example），"
                    "或改用 --topic-mode keyword 走关键词降级。"
                )
            print("[topic] 未配置 LLM，降级为关键词粗筛（设置 NEWSKG_TOPIC_* 可启用模型打分）")
            return [keyword_score(s) for s in pool]

        print(f"[topic] LLM 打分：{scorer.model} @ {scorer.base_url}")
        items = [
            (
                s.title or "",
                str((s.extra or {}).get("summary") or ""),
                str((s.extra or {}).get("feed") or s.source or ""),
            )
            for s in pool
        ]
        results = scorer.score_many(items)
        scorer.close()

        if scorer.usage.get("total_tokens"):
            print(f"[topic] 本轮 token 用量：{scorer.usage['total_tokens']}")

        out: list[TopicScore] = []
        fallback = 0
        for stub, result in zip(pool, results):
            if result is None:
                fallback += 1
                out.append(keyword_score(stub))
            else:
                out.append(result)
        if fallback:
            print(f"[topic] {fallback} 条模型未返回分数，已用关键词粗筛补齐")
        return out

    @staticmethod
    def _print_examples(label: str, pairs: list[tuple[ArticleStub, TopicScore]], n: int = 3) -> None:
        if not pairs:
            return
        ordered = sorted(pairs, key=lambda p: p[1].score)
        sample = ordered[:n] if label == "丢弃" else sorted(pairs, key=lambda p: -p[1].score)[:n]
        for stub, score in sample:
            reason = f"｜{score.reason}" if score.reason else ""
            title = (stub.title or "")[:42]
            print(f"[topic]   {label} {score.score:2d} 分：{title}{reason}")