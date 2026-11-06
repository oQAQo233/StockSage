"""规则抽取器（离线基线）。

为什么要有它：
1. 课程作业要在没有 API key 的机器上跑；
2. 任何 LLM 抽取都需要一个基线来对照——否则你无法回答「LLM 到底带来了多少提升」。

它只做三件事：认实体（大写串）、认关系（动词模板）、配对后再截断到短语边界。
准确率不高，但它快、免费、完全可复现，这正是基线的定义。
"""

from __future__ import annotations

import re

from ..models import Article, Triple
from .base import build_triple, dedupe_triples

# 组织名常见后缀 / 关键词
ORG_WORDS = {
    "inc", "corp", "corporation", "ltd", "plc", "ag", "sa", "nv", "gmbh", "llc",
    "group", "holdings", "bank", "agency", "ministry", "university", "council",
    "committee", "party", "union", "fund", "capital", "partners", "securities",
    "airlines", "airways", "motors", "energy", "pharma", "technologies", "systems",
    "networks", "labs", "institute", "association", "organization", "organisation",
    "commission", "authority", "department", "bureau", "federation", "exchange",
}

GPE = {
    "united states", "china", "japan", "germany", "france", "britain", "uk",
    "india", "russia", "brazil", "canada", "australia", "italy", "spain",
    "south korea", "north korea", "saudi arabia", "iran", "israel", "turkey",
    "mexico", "indonesia", "vietnam", "netherlands", "switzerland", "sweden",
    "washington", "beijing", "brussels", "london", "tokyo", "paris", "berlin",
    "new york", "hong kong", "singapore", "taiwan", "ukraine", "europe",
    "arizona", "california", "texas", "frankfurt", "chicago", "atlanta",
    "vienna", "amsterdam", "veldhoven", "hsinchu", "phoenix", "san francisco",
}

STOP_ENT = {
    "the", "a", "an", "this", "that", "these", "those", "it", "he", "she",
    "they", "we", "you", "i", "but", "and", "or", "if", "when", "while",
    "after", "before", "since", "as", "in", "on", "at", "for", "with", "by",
    "from", "to", "of", "its", "his", "her", "their", "our", "mr", "ms",
    "mrs", "dr", "prof", "sir", "also", "however", "meanwhile", "still",
    "now", "then", "there", "here", "what", "which", "who", "how", "why",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december", "analysts", "officials",
    "policymakers", "regulators", "shares", "retailers", "leaders",
}

# 短语边界：遇到这些词就截断，避免把整个从句吞进宾语
PHRASE_STOP = {
    "by", "on", "in", "at", "for", "to", "after", "before", "this", "last",
    "next", "from", "with", "as", "than", "and", "or", "that", "which", "who",
    "according", "amid", "over", "under", "about",
}

# 实体：大写开头的连续词串，允许中间夹 of/the/and 之类的小词
_ENT = (
    r"[A-Z][A-Za-z0-9&.\-']*"
    r"(?:\s+(?:of|the|and|for|&|de|van|von|al)\s+[A-Z][A-Za-z0-9&.\-']*"
    r"|\s+[A-Z][A-Za-z0-9&.\-']*){0,4}"
)

# 小写宾语：用于「raised interest rates」「cut oil production」这类
_LOW = r"[a-z][\w&.\-']*(?:\s+[a-z][\w&.\-']*){0,3}"

# 宾语位置上「the Department of Commerce」很常见，必须允许前置冠词，
# 否则 "president of the European Central Bank" 会因为 the 而整条匹配失败。
_OBJ = rf"(?:(?:The\s+|the\s+)?{_ENT}|{_LOW})"

# (正则, 谓词) —— 顺序即优先级
PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(rf"({_ENT})\s+(?:has\s+|have\s+)?(?:acquired|bought|purchased|took over)\s+({_OBJ})"), "acquired"),
    (re.compile(rf"({_ENT})\s+(?:is|was)\s+(?:acquiring|buying)\s+({_OBJ})"), "acquired"),
    (re.compile(rf"({_ENT})\s+(?:has\s+|have\s+)?(?:appointed|named|hired)\s+({_OBJ})"), "appointed"),
    (re.compile(rf"({_ENT})\s+(?:is|was)\s+(?:the\s+)?(?:CEO|chief executive|president|chairman|chairwoman)\s+of\s+({_OBJ})"), "leads"),
    (re.compile(rf"({_ENT}),\s+(?:the\s+)?(?:CEO|chief executive|president|chairman|chairwoman|spokesperson|spokesman|spokeswoman)\s+of\s+({_OBJ})"), "leads"),
    (re.compile(rf"({_ENT})\s+(?:is|was)\s+(?:based|headquartered)\s+in\s+({_OBJ})"), "located_in"),
    (re.compile(rf"({_ENT})\s+(?:has\s+|have\s+)?(?:raised|increased|hiked|lifted)\s+({_OBJ})"), "raised"),
    (re.compile(rf"({_ENT})\s+(?:has\s+|have\s+)?(?:cut|reduced|lowered|slashed)\s+({_OBJ})"), "cut"),
    (re.compile(rf"({_ENT})\s+(?:has\s+|have\s+)?(?:banned|sanctioned|blocked)\s+({_OBJ})"), "sanctioned"),
    (re.compile(rf"({_ENT})\s+(?:has\s+|have\s+)?(?:invested|invests)\s+in\s+({_OBJ})"), "invested_in"),
    (re.compile(rf"({_ENT})\s+(?:has\s+|have\s+)?(?:partnered|teamed up)\s+with\s+({_OBJ})"), "partnered_with"),
    (re.compile(rf"({_ENT})\s+(?:has\s+|have\s+)?(?:signed|agreed)\s+(?:a\s+)?(?:deal|agreement)\s+with\s+({_OBJ})"), "signed_with"),
]

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")

# --------------------------------------------------------------------------- #
# 中文模板
# --------------------------------------------------------------------------- #
# 设计取舍：中文没有空格、没有大小写，靠正则认实体必然比英文更脏。
# 所以这里只收**高精度**动词，并且对两端实体加硬约束（长度、黑名单），
# 宁可少抽，也不要往图里灌垃圾——基线可以弱，但不能是噪声源。
_ZH_ENT = r"[\u4e00-\u9fffA-Za-z0-9]{2,14}"

#: 主语不能以这些词开头（它们说明匹配从句子中间切进来了）
_ZH_BAD_PREFIX = (
    "的", "了", "在", "于", "和", "与", "及", "本次", "该", "其",
    "如有", "虽然", "稳妥", "同时", "此外", "目前", "其中", "另外",
    "上述", "有关", "对此", "据此", "据", "另", "又", "并",
)
#: 宾语不能是这些「伪实体」
_ZH_BAD_OBJ = {
    "公司", "股份", "股权", "股票", "本次", "上述", "相关", "部分", "全体股东",
    "者据此操作", "咨询服务", "建议", "风险", "操作",
}
#: 主语必须以这些「机构后缀」结尾，否则不认。
#: 中文没有空格和大小写，正则切出来的主语经常是动词片段（"项目计划总""拟3亿元"）。
#: 用后缀白名单换精度：召回很低，但进图的都是像样的机构名——
#: 基线可以弱，不能脏。
_ZH_ORG_SUFFIX = (
    "公司", "集团", "股份", "银行", "基金", "证券", "交易所", "委员会", "机构", "控股",
    "合伙企业", "事务所", "研究院", "研究所", "大学", "医院", "交易所", "局", "部", "委", "办",
)

#: 含这些字样的句子是免责声明，一律不抽——财经页面结尾必然有
#: 「据此操作，风险自担」，不清掉的话每条新闻都会产出同样几条垃圾。
_ZH_SKIP_SENTENCE = re.compile(
    r"据此操作|风险自担|仅供参考|不构成投资建议|投资建议|免责声明|证券投资咨询|据此入市"
)

_ZH_VERBS: list[tuple[str, str]] = [
    (r"收购|买下|受让", "收购"),
    (r"并购|吸收合并", "并购"),
    (r"增持", "增持"),
    (r"减持", "减持"),
    (r"回购", "回购"),
    (r"中标", "中标"),
    (r"签订|签署|订立", "签订"),
    (r"投资|注资|出资", "投资"),
    (r"增资", "增资"),
    (r"定增|定向增发", "定增"),
    (r"参股|入股", "参股"),
    (r"控股", "控股"),
    (r"设立|成立|组建", "设立"),
    (r"质押", "质押"),
    (r"处罚|罚款", "处罚"),
    (r"起诉|提起诉讼", "起诉"),
    (r"辞职|离任", "辞职"),
    (r"任命|聘任|当选", "任命"),
    (r"解禁", "解禁"),
]

#: 中文里动词前常出现的时态/情态词，吃掉它们再取实体
_ZH_MODAL = r"(?:拟|将|已|已经|完成|成功|正式|正在|继续|再度|首次|对外|近日|昨日|今日)*"

PATTERNS_ZH: list[tuple[re.Pattern, str]] = [
    (
        re.compile(rf"({_ZH_ENT}){_ZH_MODAL}(?:{verbs})(?:了)?({_ZH_ENT})"),
        canon,
    )
    for verbs, canon in _ZH_VERBS
]

_ZH_SENT_SPLIT = re.compile(r"[。！？；\n]+")


def _zh_valid(span: str, require_org: bool = False) -> bool:
    # 中文实体至少 3 个字：2 字多半是被切断的片段
    if not (3 <= len(span) <= 14):
        return False
    if span.startswith(_ZH_BAD_PREFIX):
        return False
    if span in _ZH_BAD_OBJ:
        return False
    if all(ch.isdigit() for ch in span):
        return False
    if require_org and not span.endswith(_ZH_ORG_SUFFIX):
        return False
    return True


def _extract_zh(article: Article) -> list[Triple]:
    """中文模板抽取。只在正文确实含中文时调用。"""
    out: list[Triple] = []
    for sentence in _ZH_SENT_SPLIT.split(article.body or ""):
        sentence = sentence.strip()
        if len(sentence) < 8 or _ZH_SKIP_SENTENCE.search(sentence):
            continue
        for pattern, predicate in PATTERNS_ZH:
            for match in pattern.finditer(sentence):
                subject = (match.group(1) or "").strip()
                obj = (match.group(2) or "").strip()
                if not _zh_valid(subject, require_org=True) or not _zh_valid(obj):
                    continue
                if subject == obj:
                    continue
                # 宾语被吞进主语（"X收购X的子公司"）说明边界错了
                if obj in subject or subject in obj:
                    continue
                out.append(build_triple(article, subject, predicate, obj, "rule", 0.6))
    return out


def _trim(span: str) -> str:
    """截断到短语边界：'interest rates by 25 basis points' -> 'interest rates'。"""
    words: list[str] = []
    for word in span.split():
        if word.lower().strip(".,;:") in PHRASE_STOP and words:
            break
        words.append(word)
    return " ".join(words)


def _clean(span: str) -> str:
    span = re.sub(r"\s+", " ", span).strip(" ,.;:()[]\"'")
    words = span.split()
    while words and words[0].lower() in STOP_ENT:
        words.pop(0)
    return _trim(" ".join(words)).strip()


def _valid(span: str) -> bool:
    if not span or len(span) < 2 or len(span) > 60:
        return False
    if span.lower() in STOP_ENT:
        return False
    if span.isupper() and len(span) > 6:
        return False
    return bool(re.search(r"[A-Za-z]", span))


def guess_type(name: str) -> str:
    low = name.lower()
    if low in GPE:
        return "GPE"
    words = {w.strip(".,") for w in low.split()}
    if words & ORG_WORDS:
        return "ORG"
    if len(name.split()) >= 2 and name == name.title():
        return "PERSON"
    return "ORG"


class RuleExtractor:
    name = "rule"

    def extract(self, article: Article) -> list[Triple]:
        text = article.body or ""
        if re.search(r"[\u4e00-\u9fff]", text):
            return dedupe_triples(_extract_zh(article))

        out: list[Triple] = []

        for sentence in _SENT_SPLIT.split(text):
            if len(sentence) < 15:
                continue
            for pattern, predicate in PATTERNS:
                for match in pattern.finditer(sentence):
                    subject = _clean(match.group(1) or "")
                    obj = _clean(match.group(2) or "")
                    if not _valid(subject) or not _valid(obj):
                        continue
                    if obj.lower() == subject.lower():
                        continue
                    # 宾语落回主语里（"ECB ... ECB"）说明匹配跑偏了
                    if obj.lower() in subject.lower() or subject.lower() in obj.lower():
                        continue
                    out.append(build_triple(article, subject, predicate, obj, self.name, 0.7))

        return dedupe_triples(out)