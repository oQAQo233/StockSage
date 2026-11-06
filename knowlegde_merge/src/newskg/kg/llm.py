"""LLM 抽取器：对接任意 OpenAI 兼容端点。

用裸 requests 而不是 openai SDK，是为了兼容一切「OpenAI 形状」的服务：
DeepSeek、OpenCode、Ollama、vLLM、LM Studio、one-api 网关都行，
只要改三个环境变量：

    NEWSKG_LLM_BASE_URL=https://api.deepseek.com/v1
    NEWSKG_LLM_API_KEY=sk-...
    NEWSKG_LLM_MODEL=deepseek-chat

强制要求模型返回 JSON 对象，并且只接受 subject / predicate / object 三个字段，
解析失败就整篇丢弃——宁可少一条，不要脏一条。
"""

from __future__ import annotations

import json
import os
import re
import uuid

import requests

from ..config import LLM
from ..models import Article, Triple
from .base import build_triple, dedupe_triples


class ExtractorUnavailable(RuntimeError):
    """额度/限流/鉴权问题：换 key 才能继续，重试没有意义。"""


SYSTEM = (
    "You extract knowledge graph triples from news articles. "
    "Return ONLY a JSON object with this shape: "
    '{"entities":[{"name":"...","type":"PERSON|ORG|GPE|EVENT|CONCEPT"}],'
    '"relations":[{"subject":"...","predicate":"...","object":"..."}]}. '
    "Rules: subject and object must appear in the text; predicate must be a short "
    "lowercase verb phrase (acquired, appointed, leads, located_in, sanctioned, said...); "
    "no pronouns; no more than 25 relations; no explanations, no markdown fences."
)

#: 中文文章用这套。谓词改成**中文规范词**，与 `PREDICATES_ZH` 的规范形态对齐。
#: 不用英文谓词配中文实体——那样评 gold 时永远对不上，指标只是在量翻译差异。
SYSTEM_ZH = (
    "你是一个新闻知识图谱抽取器。只输出一个 JSON 对象，结构如下："
    '{"entities":[{"name":"...","type":"PERSON|ORG|GPE|EVENT|CONCEPT"}],'
    '"relations":[{"subject":"...","predicate":"...","object":"..."}]}。'
    "规则："
    "1) subject 与 object 必须是原文中出现的实体，中文用原文写法，不要翻译、不要加书名号；"
    "2) predicate 必须是**简短的中文动词**，优先从下面这张规范词表里选："
    "收购、并购、增持、减持、回购、中标、签订、投资、增资、定增、控股、参股、上市、退市、"
    "停牌、复牌、分红、质押、解押、处罚、立案、问询、起诉、辞职、任命、合作、供应、采购、"
    "设立、更名、破产、重整、重组、澄清、否认、解禁、披露、亏损、盈利；"
    "3) 词表里没有合适动词时，用 2–4 个汉字自拟，不要用英文、不要带「了/的/被/进行/完成」；"
    "4) 不要抽「说了什么」「认为」「表示」这类转述关系；"
    "5) 不要抽行情状态（涨停、下跌）——那是行情不是关系；"
    "6) 最多 25 条，subject 与 object 不能相同；"
    "7) 只输出 JSON，不要解释、不要 Markdown 代码块。"
)

USER = """Source: {source}
Published: {published}
Title: {title}

Body:
{body}
"""


class LLMExtractor:
    name = "llm"

    @staticmethod
    def _looks_chinese(article: Article) -> bool:
        """按正文实际字符判断语种，而不是只信 lang 字段。

        新闻站的 lang 常写错（中文站标 en 的不少），正文里汉字占比更可靠。
        """
        body = article.body[:2000]
        cjk = len(re.findall(r"[\u4e00-\u9fff]", body))
        visible = len(re.sub(r"\s", "", body))
        if (article.lang or "").lower().startswith("zh") and cjk >= 20:
            return True
        return visible > 0 and cjk / visible > 0.15

    def _system_for(self, article: Article) -> str:
        """挑选 system prompt：`NEWSKG_PREDICATE_LANG=zh|en|auto`（默认 auto）。

        中文文章配中文谓词。不要用英文谓词配中文实体——
        评 gold 时会永远对不上，指标只在量「翻译差异」而不是「抽取质量」。
        """
        mode = os.getenv("NEWSKG_PREDICATE_LANG", "auto").strip().lower()
        if mode == "zh":
            return SYSTEM_ZH
        if mode == "en":
            return SYSTEM
        return SYSTEM_ZH if self._looks_chinese(article) else SYSTEM

    def __init__(self, base_url: str | None = None, api_key: str | None = None, model: str | None = None):
        self.base_url = (base_url or LLM.base_url).rstrip("/")
        self.api_key = api_key or LLM.api_key
        self.model = model or LLM.model
        # 只有 Go 网关需要会话头；其它 OpenAI 兼容端点带了也无害，但没必要。
        self._default_session = str(uuid.uuid4()) if "/go/" in self.base_url else ""
        if not self.api_key and "localhost" not in self.base_url and "127.0.0.1" not in self.base_url:
            raise SystemExit(
                "LLM 后端需要 API key。设置 NEWSKG_LLM_API_KEY 后重试；"
                "没有 key 就先用 --backend rule。"
            )

    def extract(self, article: Article) -> list[Triple]:
        payload = {
            "model": self.model,
            "temperature": LLM.temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": self._system_for(article)},
                {
                    "role": "user",
                    "content": USER.format(
                        source=article.source,
                        published=article.published_at or "unknown",
                        title=article.title,
                        body=article.body[:8000],
                    ),
                },
            ],
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        # OpenCode Go 的网关要求这个头，值是一个 UUID（用于路由，不是密钥）。
        # 填成 key 会返回 400 "this feature is not yet supported by atlas"，别踩。
        session_id = LLM.session_id or self._default_session
        if session_id:
            headers["x-opencode-session"] = session_id

        resp = requests.post(
            f"{self.base_url}/chat/completions",
            json=payload,
            headers=headers,
            timeout=LLM.timeout,
        )

        # 这三类错误重试没用，必须让人看到原因，而不是吐一屏 traceback
        if resp.status_code == 401:
            raise ExtractorUnavailable(
                "LLM 端点返回 401：key 无效或已过期。检查 NEWSKG_LLM_API_KEY。"
            )
        if resp.status_code == 402:
            raise ExtractorUnavailable(
                f"LLM 端点返回 402：账户余额不足（model={self.model}）。"
                "充值后重试，或换一个免费的模型名。"
            )
        if resp.status_code == 429:
            wait = resp.headers.get("retry-after", "?")
            detail = ""
            try:
                detail = resp.json().get("error", {}).get("message", "")[:120]
            except Exception:  # noqa: BLE001
                pass
            raise ExtractorUnavailable(
                f"LLM 端点限流（429），retry-after={wait} 秒。{detail}".strip()
            )

        if resp.status_code >= 400:
            # 把服务端的原话带出来：静默的 400 会让人以为是网络问题
            raise ExtractorUnavailable(
                f"LLM 端点返回 {resp.status_code}：{resp.text[:300]}"
            )

        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]

        try:
            data = json.loads(_strip_fences(content))
        except json.JSONDecodeError as exc:
            print(f"  [llm] {article.article_id[:8]} 返回的不是合法 JSON，已跳过：{exc}")
            return []

        triples: list[Triple] = []
        for rel in data.get("relations", [])[:40]:
            s = str(rel.get("subject", "")).strip()
            p = str(rel.get("predicate", "")).strip()
            o = str(rel.get("object", "")).strip()
            if not (s and p and o) or s.lower() == o.lower():
                continue
            if len(s) > 80 or len(o) > 80 or len(p) > 40:
                continue
            triples.append(build_triple(article, s, p, o, self.name, 0.9))
        return dedupe_triples(triples)


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return text.strip()