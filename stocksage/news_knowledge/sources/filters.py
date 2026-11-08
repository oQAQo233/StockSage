"""发现层的两道过滤：时间窗、股票相关性。

为什么单独成模块而不是塞进各个适配器：
过滤逻辑与「怎么拿到 URL」无关，放在适配器里会导致每个源各写一遍、行为不一致。
这里做成**装饰器式包装**（FilteredSource），任何源套上去就同时获得两个能力，
下游的抓取/转换/抽取/入库完全不需要知道过滤发生过。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from ..models import ArticleStub

# --------------------------------------------------------------------------- #
# 时间窗
# --------------------------------------------------------------------------- #

#: 允许的「未来偏差」：部分源的时区标注有误，可能比本地时钟快几分钟。
FUTURE_TOLERANCE = timedelta(hours=6)


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
# 股票相关性
# --------------------------------------------------------------------------- #

#: 强信号：只在中国股市语境里出现的词。命中任一即判定相关。
#: 这一层是精度主力——像「美股恐慌指数飙升」这种标题里没有强信号词，会被正确排除。
STRONG_KEYWORDS = (
    "A股", "B股", "H股", "沪指", "上证指数", "上证综指", "深证成指", "深成指", "沪深300",
    "创业板", "科创板", "北交所", "主板", "中小板", "新三板",
    "涨停", "跌停", "一字板", "封板", "炸板",
    "证监会", "上交所", "深交所", "北向资金", "南向资金", "融资融券", "龙虎榜",
    "定增", "增发", "配股", "可转债", "回购注销", "增持计划", "减持计划", "举牌",
    "解禁", "限售股", "限售解禁", "股权激励", "员工持股",
    "业绩预告", "业绩快报", "业绩说明会", "上市公司", "控股股东", "实控人",
    "董事会决议", "重大资产重组", "并购重组", "借壳上市", "分拆上市",
    "券商", "市盈率", "市净率", "市值管理", "退市风险", "戴帽", "ST股",
    "分红派息", "高送转", "机构调研", "游资", "主力资金", "换手率", "题材股", "概念股",
    "上会", "过会", "上市辅导", "招股说明书", "询价发行",
)

#: 弱信号：金融新闻里到处都有，单独命中不足以说明「与中国股票相关」。
#: 保留这份清单是为了在日志里区分「因为弱词被丢弃」和「完全无关」。
WEAK_KEYWORDS = (
    "股票", "股价", "股份", "基金", "私募", "公募", "IPO", "市值", "估值",
    "净利润", "营收", "季报", "年报", "股东大会", "上市公司公告",
)

#: A 股代码：沪市 60x/68x、深市 00x/30x、北交所 43x/8xx/92x。
#: 前后加 (?!\d)/(?<!\d) 是为了避免把 8 位日期或 6 位随机数误判成股票代码。
STOCK_CODE_RE = re.compile(
    r"(?<!\d)(?:60[0135]\d{3}|68[89]\d{3}|00[0123]\d{3}|30[01]\d{3}|[48][3-9]\d{4}|92\d{4})(?!\d)"
)


def _stub_text(stub: ArticleStub) -> str:
    """把 stub 里所有可用于判断的文本拼起来（标题 + 摘要 + 自带标签）。"""
    extra = stub.extra or {}
    parts = [
        stub.title or "",
        str(extra.get("summary") or ""),
        str(extra.get("digest") or ""),
    ]
    codes = extra.get("stock_codes") or []
    if codes:
        parts.append(" ".join(str(c) for c in codes))
    return " ".join(parts)


def stock_match_reason(stub: ArticleStub) -> str:
    """返回命中的理由（`code` / `strong` / `weak` / `""`），便于排查与统计。"""
    codes = (stub.extra or {}).get("stock_codes") or []
    if any(STOCK_CODE_RE.fullmatch(str(c).strip()) for c in codes):
        return "code"

    text = _stub_text(stub)
    if STOCK_CODE_RE.search(text):
        return "code"
    lowered = text.lower()
    if any(kw.lower() in lowered for kw in STRONG_KEYWORDS):
        return "strong"
    if any(kw.lower() in lowered for kw in WEAK_KEYWORDS):
        return "weak"
    return ""


def is_stock_related(stub: ArticleStub) -> bool:
    """判断一条 stub 是否与**中国股票**相关。

    只认三种证据：A 股代码、中国股市专有词汇、源站标注的 A 股个股代码。
    像「美股恐慌指数飙升」「明星基金净值下跌」这类只命中泛金融词的新闻会被排除，
    否则一堆美股/宏观稿会混进来，破坏「中国股票」这个前提。
    """
    return stock_match_reason(stub) in ("code", "strong")


# --------------------------------------------------------------------------- #
# 包装器
# --------------------------------------------------------------------------- #


class FilteredSource:
    """给任意源套上「时间窗 + 股票相关」两道过滤。

    为了过滤后仍能凑够 limit 条，会按 over_scan 倍数向底层源多要一些候选；
    抓取发生在过滤之后，所以多要的只是元数据，不会多抓正文。
    """

    def __init__(self, inner, topic: str | None = None, keep_undated: bool = False,
                 over_scan: int = 8):
        self.inner = inner
        self.name = getattr(inner, "name", "filtered")
        self.license = getattr(inner, "license", "")
        self.topic = (topic or "").lower() or None
        self.keep_undated = keep_undated
        self.over_scan = max(over_scan, 1)

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

        kept = kept_time
        if self.topic in ("stock", "stocks", "cn-stock"):
            reasons = [stock_match_reason(s) for s in kept_time]
            kept = [s for s, r in zip(kept_time, reasons) if r in ("code", "strong")]
            weak_only = sum(1 for r in reasons if r == "weak")
            print(
                f"[filter] 股票相关：保留 {len(kept)} 篇"
                f"（丢弃 {len(kept_time) - len(kept)} 篇，其中 {weak_only} 篇只命中泛金融词）"
            )

        return kept[:limit]
