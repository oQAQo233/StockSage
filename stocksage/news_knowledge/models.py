"""数据模型。

刻意分成两层：
- ArticleStub：源站只告诉我们「有这么一篇文章」，还没有正文；
- Article：正文抓下来、转换完、算好指纹之后的完整记录。

这样抓取失败时，stub 仍然可以记账，不会污染正文表。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def sha1_of(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class ArticleStub:
    """发现阶段的最小记录。"""

    source: str
    url: str
    title: str = ""
    published_at: str = ""
    lang: str = "en"
    license: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return sha1_of(self.url)[:16]


@dataclass
class Article:
    """正文阶段的完整记录。article_id 由规范化 URL 决定，保证幂等。"""

    article_id: str
    source: str
    url: str
    canonical_url: str
    title: str
    published_at: str
    lang: str
    license: str
    authors: list[str]
    body: str
    content_sha1: str
    simhash: int
    raw_path: str
    markdown_path: str
    fetched_at: str = field(default_factory=now_iso)

    @property
    def word_count(self) -> int:
        """词数：拉丁词按空格切，中文按字数计。

        只按空格切会把整段中文数成一个词，千字文只报十几个词，
        用来筛「正文是否够长」会全线误判。
        """
        text = self.body
        latin = len(re.findall(r"[A-Za-z0-9']+", text))
        cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
        return latin + cjk

    def to_row(self) -> dict[str, Any]:
        d = asdict(self)
        d["word_count"] = self.word_count
        return d


@dataclass
class Triple:
    """一条带出处与时间的三元组。

    valid_from 取自文章发布时间——这是新闻图谱区别于通用 KG 的关键字段，
    没有它，去年的事实和今天的事实会混在同一条边上。
    """

    subject: str
    predicate: str
    object: str
    article_id: str
    published_at: str
    source: str
    extractor: str
    confidence: float = 1.0

    def as_tuple(self) -> tuple[str, str, str]:
        return (self.subject, self.predicate, self.object)