"""直连抽取后端：一次 chat 调用拿完一篇的三元组。

为什么会有这个后端：KGGen 每篇要发两次超大请求（抽取 + 实体聚类），
在网关侧经常会挂住不返回（实测同一篇文章单发 93 秒、进 batch 后 20 分钟不动），
整批抽取因此停摆。这里用一次小请求换稳定性：提示词写死、输出上限可控、失败能重试。

代价要说清楚：没有 KGGen 的实体聚类，同一实体在不同文章里的不同写法要靠
`kg/entities.py` 的规则归一（本来也是这么做的），跨文章合并质量略低于 KGGen。
台账里 extractor 记作 `llm-direct`，和 `kggen` 产出可以分开统计、随时回滚。
"""

from __future__ import annotations

import json
import os
import re
import time

from ..config import LLM
from ..llm_client import chat
from ..models import Article, Triple
from .base import build_triple, dedupe_triples

PROMPT = """你是新闻知识图谱抽取器。从下面这篇新闻里抽取实体关系三元组。

规则：
1. 只输出一个 JSON 数组，每项是 ["主体", "关系", "客体"] 三个字符串。不要解释，不要 markdown 代码块。
2. 关系用中文动词或动词短语：发布、投资、收购、研发、搭载、任职于、位于、合作、签署、增速、超过、覆盖。
3. 主体和客体必须是具体实体：公司、机构、人物、产品、型号、地点、技术、项目、数据。不要用"记者""编辑""记者获悉"这类词，也不要把整句话当实体。
4. 最多 {limit} 条，只抽正文里明确陈述的事实，不要推测。
5. 一条都抽不出来就输出 []。

标题：{title}

正文：
{body}
"""


def parse_triples(text: str, limit: int = 40) -> list[tuple[str, str, str]]:
    """从模型回复里抠出三元组。模型偶尔会加代码块或前后废话，这里都容忍。"""
    if not text:
        return []
    cleaned = re.sub(r"^```[a-zA-Z]*|```$", "", text.strip(), flags=re.M).strip()
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start >= 0 and end > start:
        try:
            data = json.loads(cleaned[start : end + 1])
            rows = []
            for item in data if isinstance(data, list) else []:
                if isinstance(item, (list, tuple)) and len(item) >= 3:
                    s, p, o = (str(x).strip() for x in item[:3])
                    rows.append((s, p, o))
            if rows:
                return rows[:limit]
        except json.JSONDecodeError:
            pass
    # 退路：一行一条的 ["s", "p", "o"]
    rows = []
    for line in cleaned.splitlines():
        m = re.search(r'\[\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\]', line)
        if m:
            rows.append((m.group(1).strip(), m.group(2).strip(), m.group(3).strip()))
    return rows[:limit]


class DirectExtractor:
    name = "llm-direct"

    def __init__(self, model: str | None = None, limit: int | None = None, max_chars: int | None = None):
        self.model = model or os.getenv("NEWSKG_DIRECT_MODEL", "qwen3.6-flash")
        self.limit = int(limit or os.getenv("NEWSKG_DIRECT_LIMIT", "28"))
        self.max_chars = int(max_chars or os.getenv("NEWSKG_DIRECT_CHARS", "3000"))
        self.max_tokens = int(os.getenv("NEWSKG_DIRECT_MAX_TOKENS", "4000"))
        self.timeout = int(os.getenv("NEWSKG_DIRECT_TIMEOUT", "120"))
        # 节流：这个端点在一分钟内被塞太多生成请求时会把后续请求挂起不返回
        # （不报错、不断连，客户端只能干等）。每篇之间强制睡一会儿，
        # 把请求速率压到端点吃得下的水平——比并发快，比挂死强。
        self.pace = float(os.getenv("NEWSKG_DIRECT_PACE", "4"))

    def extract(self, article: Article) -> list[Triple]:
        prompt = PROMPT.format(
            limit=self.limit,
            title=article.title,
            body=article.body[: self.max_chars],
        )
        text = chat(
            [{"role": "user", "content": prompt}],
            model=self.model,
            temperature=0.0,
            max_tokens=self.max_tokens,
            timeout=self.timeout,
        )
        if self.pace > 0:
            time.sleep(self.pace)
        triples: list[Triple] = []
        for s, p, o in parse_triples(text, self.limit):
            if not (s and p and o) or s.lower() == o.lower():
                continue
            if len(s) > 40 or len(o) > 40 or len(p) > 20:
                # 长字符串基本是整句，进图只会变成噪声节点
                continue
            triples.append(build_triple(article, s, p, o, self.name, 0.8))
        return dedupe_triples(triples)


__all__ = ["DirectExtractor", "parse_triples"]