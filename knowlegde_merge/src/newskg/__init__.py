"""newskg —— 新闻采集 → 统一 Markdown → 知识图谱 的最小可跑管线。

本包只做四件事：
1. 从一个或多个新闻源发现文章（GDELT / Guardian / RSS / 本地 fixtures）
2. 用 trafilatura 抽正文、用 markitdown 统一转成 Markdown
3. 用规则 / LLM / KGGen 三种后端抽取三元组
4. 落到 SQLite，并导出 Cypher / GraphML / JSON

设计原则：没有 API key、没有网络时，`demo` 子命令也必须能跑通全链路。
"""

__version__ = "0.1.0"