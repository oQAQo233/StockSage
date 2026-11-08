"""三元组抽取后端的公共接口。

三个后端不是互相替代关系，而是三种成本/质量档位：

  rule   —— 不用模型、不用网络、可复现，用来把管线跑通、做基线；
  llm    —— 任意 OpenAI 兼容端点，质量最好，按 token 计费；
  kggen  —— 别人训练/封装好的抽取器，省去自己写 prompt，但引入依赖。
"""

from __future__ import annotations

import re
from typing import Protocol

from ..models import Article, Triple


class Extractor(Protocol):
    name: str

    def extract(self, article: Article) -> list[Triple]:
        ...


# 键统一写成「空格分隔的小写短语」，normalize_predicate 会先把下划线/连字符换成空格再查表。
# 这张表同时作用于抽取结果和 gold 集——否则 gold 写 based_in、抽取器写 located_in，
# 明明说的是同一件事，评分却算错，三个后端的差距会被这层噪声淹没。
PREDICATE_ALIASES = {
    "acquired": "acquired",
    "acquires": "acquired",
    "bought": "acquired",
    "purchased": "acquired",
    "took over": "acquired",
    "merged with": "merged_with",
    "merges with": "merged_with",
    "appointed": "appointed",
    "named": "appointed",
    "hired": "appointed",
    "is ceo of": "leads",
    "leads": "leads",
    "runs": "leads",
    "heads": "leads",
    "said": "said",
    "says": "said",
    "warned": "warned",
    "announced": "announced",
    "reported": "reported",
    "based in": "located_in",
    "headquartered in": "located_in",
    "situated in": "located_in",
    "located in": "located_in",
    "worked at": "employed_by",
    "works at": "employed_by",
    "employed by": "employed_by",
    "signed contract with": "signed_with",
    "signed contracts with": "signed_with",
    "signed": "signed_with",
    "signed with": "signed_with",
    "raised": "raised",
    "cut": "cut",
    "cuts": "cut",
    "sanctioned": "sanctioned",
    "banned": "banned",
    "invested in": "invested_in",
    "partnered with": "partnered_with",
    "agreed with": "agreed_with",
    "co founded": "co_founded",
    "cofounded": "co_founded",
}


def normalize_predicate(pred: str) -> str:
    """把任意写法收敛成规范形式。

    分两条路：
    - 含中文 → 走 `_normalize_zh`，收敛到中文字典里的规范谓词；
    - 纯英文/拉丁 → 走原来的 snake_case 逻辑（行为与加中文之前完全一致）。

    为什么要分：中英文的「规范形态」根本不同（`收购` vs `acquired`），
    硬塞进一张表会让两边互相污染。
    """
    raw = str(pred).strip()
    if _has_cjk(raw):
        return _normalize_zh(raw)

    key = re.sub(r"[\s_\-]+", " ", raw.lower()).strip()
    if key in PREDICATE_ALIASES:
        return PREDICATE_ALIASES[key]
    return key.replace(" ", "_")


# --------------------------------------------------------------------------- #
# 中文谓词
# --------------------------------------------------------------------------- #
#: 中文规范谓词表：规范词 -> 可接受的同义写法。
#: 取值原则：
#: 1) 只收「动作类」关系（收购、中标、增持），不收价格状态（涨停、下跌）——
#:    后者是行情不是关系，放进三元组会让图变成时间序列；
#: 2) 语义相近但不相同的**不合**（`收购` 与 `并购`、`增资` 与 `定增` 各自独立），
#:    否则 gold 里区分的东西会被判成一样，指标虚高。
PREDICATES_ZH: dict[str, list[str]] = {
    "收购": ["收购", "买下", "受让", "购入", "竞得", "买断"],
    "并购": ["并购", "合并", "吸收合并"],
    "增持": ["增持", "加仓", "买入股份"],
    "减持": ["减持", "减仓", "卖出股份", "清仓", "抛售"],
    "回购": ["回购", "股份回购", "回购股份"],
    "中标": ["中标", "夺标", "赢得订单", "获得订单"],
    "签订": ["签订", "签署", "订立", "缔结", "达成协议", "签约"],
    "投资": ["投资", "注资", "出资", "参投", "投建"],
    "增资": ["增资", "增资扩股"],
    "定增": ["定增", "定向增发"],
    "控股": ["控股", "控制", "实际控制"],
    "参股": ["参股", "入股"],
    "上市": ["上市", "挂牌", "ipo", "首次公开发行"],
    "退市": ["退市", "摘牌"],
    "停牌": ["停牌"],
    "复牌": ["复牌"],
    "分红": ["分红", "派息", "现金分红", "利润分配"],
    "质押": ["质押", "股权质押"],
    "解押": ["解押", "解除质押"],
    "处罚": ["处罚", "罚款", "警示", "行政处罚", "被罚"],
    "立案": ["立案", "立案调查", "调查"],
    "问询": ["问询", "关注函", "监管函", "问询函"],
    "起诉": ["起诉", "提起诉讼", "诉讼", "状告"],
    "辞职": ["辞职", "离任", "辞去", "卸任"],
    "任命": ["任命", "聘任", "选举", "当选", "委任"],
    "合作": ["合作", "战略合作", "联手", "携手"],
    "供应": ["供应", "供货", "配套", "供给"],
    "采购": ["采购", "招标采购", "集中采购"],
    "设立": ["设立", "成立", "组建", "新设", "创办"],
    "更名": ["更名", "改名"],
    "破产": ["破产", "破产清算"],
    "重整": ["重整", "破产重整"],
    "重组": ["重组", "资产重组", "重大资产重组"],
    "澄清": ["澄清", "说明"],
    "否认": ["否认", "辟谣"],
    "解禁": ["解禁", "限售解禁"],
    "披露": ["披露", "公告", "发布公告"],
    "亏损": ["亏损", "由盈转亏", "预亏"],
    "盈利": ["盈利", "扭亏", "预盈"],
    # 通用动作：不属于股票专有术语，但在新闻里高频，且同义写法多。
    # 收进来的目的是让「出任 / 就任」和「担任」在评 gold 时算同一个关系。
    "担任": ["担任", "出任", "就任", "履新"],
    "推动": ["推动", "推进", "力推", "促进"],
    "布局": ["布局", "深耕", "落子"],
    "聚焦": ["聚焦", "专注", "着力"],
    "攻关": ["攻关", "突破", "攻克"],
    "融合": ["融合", "结合", "融入"],
    "转化": ["转化", "落地"],
    "整治": ["整治", "治理", "整顿"],
}

#: 规范词 -> 反查表。键统一小写、去空白。
PREDICATE_ALIASES_ZH: dict[str, str] = {}
for _canon, _aliases in PREDICATES_ZH.items():
    PREDICATE_ALIASES_ZH[_canon.lower()] = _canon
    for _a in _aliases:
        PREDICATE_ALIASES_ZH[_a.lower()] = _canon

#: 模型爱加的时态/情态前后缀，规范化的第一步就是剥掉它们。
#: 「被」也剥——被动语态的方向信息已经由主宾语顺序表达，谓词本身不用再带。
_ZH_PREFIXES = ("拟", "计划", "将", "已", "已经", "完成", "成功", "正式", "正在",
                "继续", "再度", "首次", "对外", "被", "遭到", "受到")
_ZH_SUFFIXES = ("了", "中", "过")
#: 「收购股权」这类带宾语的谓词，剥掉尾部名词后再查表。
_ZH_TAILS = ("股权", "股份", "资产", "有限公司", "公司", "协议", "合约", "合同")

#: 通配符式边界：中英文混排时，只取第一个中文字符串
_ZH_ONLY = re.compile(r"[\u4e00-\u9fff]+")


def _has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def _normalize_zh(pred: str) -> str:
    """中文谓词收敛：剥时态/情态 → 查同义词表 → 再剥宾语名词 → 再查一次。"""
    # 只保留中文主体（模型有时输出「收购（acquisition）」这种混合写法）
    text = pred.strip()
    chunks = _ZH_ONLY.findall(text)
    if chunks:
        text = max(chunks, key=len)

    for _ in range(3):  # 前缀可能叠加，如「拟完成收购」
        changed = False
        for prefix in _ZH_PREFIXES:
            if text.startswith(prefix) and len(text) > len(prefix):
                text = text[len(prefix) :]
                changed = True
                break
        if not changed:
            break

    for suffix in _ZH_SUFFIXES:
        if text.endswith(suffix) and len(text) > len(suffix) + 1:
            text = text[: -len(suffix)]

    if text.lower() in PREDICATE_ALIASES_ZH:
        return PREDICATE_ALIASES_ZH[text.lower()]

    for tail in _ZH_TAILS:
        if text.endswith(tail) and len(text) > len(tail):
            stripped = text[: -len(tail)]
            if stripped.lower() in PREDICATE_ALIASES_ZH:
                return PREDICATE_ALIASES_ZH[stripped.lower()]

    return text


def build_triple(article: Article, s: str, p: str, o: str, extractor: str, conf: float = 1.0) -> Triple:
    return Triple(
        subject=s.strip(),
        predicate=normalize_predicate(p),
        object=o.strip(),
        article_id=article.article_id,
        published_at=article.published_at,
        source=article.source,
        extractor=extractor,
        confidence=conf,
    )


def dedupe_triples(triples: list[Triple]) -> list[Triple]:
    """同一篇文章内，(s, p, o) 只保留置信度最高的一条。"""
    best: dict[tuple[str, str, str], Triple] = {}
    for t in triples:
        key = (t.subject.lower(), t.predicate, t.object.lower())
        cur = best.get(key)
        if cur is None or t.confidence > cur.confidence:
            best[key] = t
    return list(best.values())