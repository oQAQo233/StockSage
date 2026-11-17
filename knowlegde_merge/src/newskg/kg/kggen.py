"""KGGen 后端适配（当前唯一的抽取后端）。

KGGen（NeurIPS'25，`pip install kg-gen`）的增量在于**实体聚类**：
它会把 "Apple"、"Apple Inc."、"AAPL" 归到同一个簇，直接降低图的稀疏度。

依赖较重（litellm + dspy），且需要独立环境：`kg-gen` 装在 `.kgvenv`，
运行时用 `PYTHONPATH=src;.kgvenv`。没装时这里给明确提示，而不是抛 ImportError 栈。
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
                "装好后用 PYTHONPATH=src;.kgvenv 运行（kg-gen 放在独立环境里）。"
            ) from None

        # 三个 NEWSKG_KGGEN_* 都是可选的：不设就沿用 NEWSKG_LLM_*，只有一个 key 的场景
        # 不用再配一遍。默认模型用 LLM.model（而不是 gpt-4o-mini），否则接的是国产网关时
        # 模型名对不上，报的错会指向“模型不存在”，看起来像代码问题。
        self.model = model or os.getenv("NEWSKG_KGGEN_MODEL") or LLM.model
        endpoint = base_url or os.getenv("NEWSKG_KGGEN_BASE_URL") or LLM.base_url
        if endpoint and "/" not in self.model:
            # 自定义端点都是 OpenAI 协议；litellm 要求模型名带 provider 前缀，
            # 只写 qwen3.7-plus 会被当成“没给服务商”直接报错，而报错信息里看不出是前缀问题。
            self.model = f"openai/{self.model}"
        kwargs = {
            "model": self.model,
            "temperature": 0.0,
            # 默认上限太低：一篇 8000 字符的新闻能产出 100+ 条关系，
            # 输出被截断会让 dspy 的 JSON 解析直接失败，整篇记 0 条。
            "max_tokens": int(os.getenv("NEWSKG_KGGEN_MAX_TOKENS", "8192")),
            "api_key": api_key or LLM.api_key or None,
        }
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
