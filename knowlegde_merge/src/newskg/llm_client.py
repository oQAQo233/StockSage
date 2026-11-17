"""极简 LLM 调用：一次 chat completion，带重试，返回纯文本。

只服务报告生成。抓取层的 `sources/topic_llm.py` 保留自己的实现——它要 JSON 模式、
批量打分、缓存和"退回关键词粗筛"，和这里的诉求不同，硬合并只会让两边都难读。
"""

from __future__ import annotations

import time

import requests

from .config import LLM


class LLMError(RuntimeError):
    pass


def chat(
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 2000,
    timeout: int | None = None,
) -> str:
    """调用 OpenAI 兼容端点，返回 message.content。"""
    if not LLM.api_key:
        raise LLMError("没有配置 NEWSKG_LLM_API_KEY，无法生成报告")

    url = f"{LLM.base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": model or LLM.model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {"Authorization": f"Bearer {LLM.api_key}", "Content-Type": "application/json"}

    last = ""
    for attempt in range(3):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout or LLM.timeout)
        except requests.RequestException as exc:  # noqa: PERF203
            last = f"{type(exc).__name__}: {exc}"
            time.sleep(2 * (attempt + 1))
            continue

        if resp.status_code == 200:
            data = resp.json()
            return ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "").strip()

        if resp.status_code in (401, 403):
            raise LLMError(f"API key 被拒绝（HTTP {resp.status_code}），检查 NEWSKG_LLM_API_KEY")
        if resp.status_code == 429:
            last = "HTTP 429（限流）"
            time.sleep(3 * (attempt + 1))
            continue
        last = f"HTTP {resp.status_code}: {resp.text[:200]}"
        time.sleep(1 + attempt)

    raise LLMError(f"调用模型失败：{last}")


def configured() -> bool:
    return bool(LLM.api_key)