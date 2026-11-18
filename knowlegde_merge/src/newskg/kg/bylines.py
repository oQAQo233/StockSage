"""抽取后过滤：记者署名与媒介机构不该变成实体。

实测 11-04 的 27 篇中新网稿件里，`钟旖 -[就职于]-> 中新社` 这类三元组有 10 条（1.6%）。
比例不高，但它是**系统性**的：每篇带署名的稿子都会长出一个度为 1 的记者节点，
15 天跑下来就是几百个这种节点，把中心度和社区发现一起拉偏。

判据放在正文里找，而不是猜名字：只有「记者 钟旖」这种署名格式里出现的名字才算。
"""

from __future__ import annotations

import re

#: 「中新网记者 钟旖」「通讯员：李四」「作者：王五」——名字 2-4 个汉字
_BYLINE = re.compile(
    r"(?:记者|通讯员|撰稿|作者|编辑)\s*[：:、\s]\s*([\u4e00-\u9fff]{2,4})(?=[\s，。、）)]|$)"
)

#: 媒介机构：它们出现在三元组里通常是「中新网 -[报道]-> 茂名」这种出处标注
MEDIA = {
    "中新网", "中新社", "中国新闻网", "新华社", "新华网", "人民日报", "人民网",
    "央视", "央视新闻", "中国青年报", "经济日报", "光明日报", "科技日报",
}

#: 只有这些谓词才把「机构报道某地」当噪声；「中新网 -[属于]-> X」之类不动
MEDIA_PREDICATES = {"报道", "发布", "获悉", "刊发", "转载", "采访"}


def byline_names(body: str) -> set[str]:
    return {name for name in _BYLINE.findall(body or "")}


def drop_noise(triples, body: str) -> tuple[list, int]:
    """删掉主体是记者署名或媒介出处标注的三元组，返回 (保留的, 删掉的条数)。"""
    names = byline_names(body)
    kept = []
    dropped = 0
    for triple in triples:
        subject = triple.subject
        if subject in names or (subject in MEDIA and triple.predicate in MEDIA_PREDICATES):
            dropped += 1
            continue
        kept.append(triple)
    return kept, dropped


__all__ = ["byline_names", "drop_noise", "MEDIA", "MEDIA_PREDICATES"]