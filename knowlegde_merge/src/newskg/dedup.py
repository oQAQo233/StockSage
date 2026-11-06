"""去重：URL 规范化 + 精确指纹 + SimHash 近似去重。

为什么这一步在新闻场景里不能省：
一条美联社通稿会被几百家媒体转载，标题改两个字、正文几乎一样。
如果不去重，图里同一个事件会有几百条平行边，中心度、社区发现全部失真。
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

#: 常见的跟踪参数，直接在规范化时丢掉
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src", "spm", "from",
    "cmpid", "CMP", "smid", "share", "ito",
}


def canonicalize_url(url: str) -> str:
    """归一化 URL：去掉跟踪参数、片段、默认端口和末尾斜杠。"""
    if not url:
        return ""
    try:
        parts = urlparse(url.strip())
    except ValueError:
        return url

    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS and not k.lower().startswith("utm_")
    ]
    netloc = parts.netloc.lower()
    if netloc.endswith(":80"):
        netloc = netloc[:-3]
    elif netloc.endswith(":443"):
        netloc = netloc[:-4]

    path = parts.path.rstrip("/") or "/"
    return urlunparse((parts.scheme.lower() or "https", netloc, path, "", urlencode(query), ""))


def content_fingerprint(text: str) -> str:
    """精确指纹：用于判定「同一篇正文」。"""
    normalized = re.sub(r"\s+", " ", text).strip().lower()
    return hashlib.sha1(normalized.encode("utf-8", "ignore")).hexdigest()


def _tokens(text: str) -> list[str]:
    """分词：拉丁词 + 中文二元字组。

    只取 [a-z0-9'] 会把中文全部丢掉——中文文章的 SimHash 会退化成全 0，
    去重直接失效（转载稿判不出来）。中文按**二元字组**切分是检索界的通行做法，
    不依赖分词词典，对新闻这种专有名词多的文本够用。
    英文文本里 cjk 为空，行为与改动前完全一致。
    """
    tokens = re.findall(r"[a-z0-9']+", text.lower())
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    if cjk:
        tokens.extend("".join(cjk[i : i + 2]) for i in range(len(cjk) - 1))
    return tokens


def simhash(text: str, bits: int = 64) -> int:
    """64 位 SimHash。

    原理：把每个词的哈希按位投票，词频作为权重，最后取符号位。
    两篇近似重复的文章，汉明距离通常只有个位数。
    """
    vector = [0] * bits
    for token, weight in Counter(_tokens(text)).items():
        digest = int.from_bytes(
            hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest(), "big"
        )
        for i in range(bits):
            vector[i] += weight if (digest >> i) & 1 else -weight

    fingerprint = 0
    for i in range(bits):
        if vector[i] > 0:
            fingerprint |= 1 << i
    return fingerprint


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def shingles(text: str, k: int = 3) -> set[str]:
    """k 词滑窗。用于在 SimHash 之后做二次确认。"""
    words = _tokens(text)
    if len(words) < k:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + k]) for i in range(len(words) - k + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def find_duplicates(items: list, threshold: int = 6, min_jaccard: float = 0.55) -> dict[str, str]:
    """返回 {重复者 id: 保留者 id}。

    两道闸门，缺一不可：
    - SimHash 汉明距离 <= threshold：便宜的候选筛选；
    - 3 词 shingle 的 Jaccard >= min_jaccard：便宜的候选筛完还要看真重叠。

    只用 SimHash 会误判——同一场选举的两篇不同报道，词分布很像，但正文重叠很低。
    这正是本地样例集「阈值 7 抓住转载稿」在真实新闻上会翻车的原因。
    """
    kept: list[tuple[object, set[str]]] = []
    mapping: dict[str, str] = {}

    for item in items:
        grams = shingles(getattr(item, "body", "") or "")
        duplicate_of = None
        for keep, keep_grams in kept:
            if item.content_sha1 == keep.content_sha1:
                duplicate_of = keep.article_id
                break
            if hamming(item.simhash, keep.simhash) <= threshold and jaccard(grams, keep_grams) >= min_jaccard:
                duplicate_of = keep.article_id
                break

        if duplicate_of:
            mapping[item.article_id] = duplicate_of
        else:
            kept.append((item, grams))

    return mapping