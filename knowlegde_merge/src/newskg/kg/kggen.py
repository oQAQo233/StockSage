"""KGGen 后端适配。

KGGen（NeurIPS'25，`pip install kg-gen`）相比手写 prompt 的增量在于**实体聚类**：
它会把 "Apple"、"Apple Inc."、"AAPL" 归到同一个簇，直接降低图的稀疏度。
课程作业里把它作为「第三方抽取器」对照，能说明自写规则/自写 prompt 的差距在哪。

注意：kg-gen 依赖 litellm + dspy，安装体积不小；没装时这里给出明确提示而不是 ImportError 栈。
"""

from __future__ import annotations

import os

from ..config import LLM
from ..models import Article, Triple
from .base import build_triple, dedupe_triples


class KGGenExtractor:
    name = "kggen"

    def __init__(self, model: str | None = None, api_key: str | None = None, base_url: str | None = None):
        try:
            from kg_gen import KGGen
        except ImportError:
            raise SystemExit(
                "KGGen 后端需要先安装：pip install kg-gen\n"
                "装不动的话，用 --backend rule 或 --backend llm 跑同样的流程，"
                "三种后端的输出落的是同一张表，可以直接对比。"
            ) from None

        self.model = model or os.getenv("NEWSKG_KGGEN_MODEL", "openai/gpt-4o-mini")
        kwargs = {
            "model": self.model,
            "temperature": 0.0,
            # 默认上限太低：一篇 8000 字符的新闻能产出 100+ 条关系，
            # 输出被截断会让 dspy 的 JSON 解析直接失败，整篇记 0 条。
            "max_tokens": int(os.getenv("NEWSKG_KGGEN_MAX_TOKENS", "8192")),
            "api_key": api_key or LLM.api_key or None,
        }
        endpoint = base_url or os.getenv("NEWSKG_KGGEN_BASE_URL")
        if endpoint:
            # kg-gen 0.4 用 api_base，早期版本用 base_url；两个都试，别为版本差异改代码。
            try:
                self._kg = KGGen(api_base=endpoint, **kwargs)
            except TypeError:
                self._kg = KGGen(base_url=endpoint, **kwargs)
        else:
            self._kg = KGGen(**kwargs)

    def extract(self, article: Article) -> list[Triple]:
        try:
            graph = self._kg.generate(
                input_data=article.body[:8000],
                context=article.title,
            )
        except Exception as exc:
            print(f"  [kggen] {article.article_id[:8]} 抽取失败：{exc}")
            return []

        relations = getattr(graph, "relations", None) or set()
        triples: list[Triple] = []
        for rel in relations:
            if not isinstance(rel, (tuple, list)) or len(rel) != 3:
                continue
            s, p, o = (str(x).strip() for x in rel)
            if not (s and p and o) or s.lower() == o.lower():
                continue
            triples.append(build_triple(article, s, p, o, self.name, 0.85))
        return dedupe_triples(triples)