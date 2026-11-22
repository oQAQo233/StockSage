"""Schema 层统一的 LLM 调用：批量 + 缓存 + JSON 解析兜底。

三条硬约束（都来自断电那次的教训）：
1. 同一份 prompt 只付一次费 —— 用 sha1(step|model|prompt) 做 key 落 `llm_cache`；
2. 解析失败不抛异常 —— 记 `schema_runs(status='failed')`，让整批继续跑；
3. 串行调用 —— 该端点并发会假死，绝不并发。
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time

from .. import llm_client
from ..config import LLM

#: 每次调用之间至少隔这么久，避免把端点打疼。
PACE_SECONDS = 1.5

#: 便宜的模型用于大批量结构化任务（可通过环境变量覆盖）
TYPE_MODEL = "qwen3.8-flash"


def cache_key(step: str, model: str, prompt: str) -> str:
    return hashlib.sha1(f"{step}|{model}|{prompt}".encode("utf-8")).hexdigest()


def parse_json(text: str):
    """从模型输出里抠出 JSON。容错：剥围栏、截取第一个括号到最后一个括号。"""
    if not text:
        return None
    s = text.strip()

    fence = re.search(r"```(?:json)?\s*(.*?)```", s, re.S)
    if fence:
        s = fence.group(1).strip()

    for open_ch, close_ch in (("[", "]"), ("{", "}")):
        start = s.find(open_ch)
        end = s.rfind(close_ch)
        if start != -1 and end > start:
            chunk = s[start : end + 1]
            try:
                return json.loads(chunk)
            except json.JSONDecodeError:
                continue

    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return None


def _normalize_shape(obj):
    """模型有时返回 {"results":[...]}，统一成 list。"""
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for key in ("results", "items", "data", "list"):
            if isinstance(obj.get(key), list):
                return obj[key]
        return [obj]
    return None


def ask_json(
    conn: sqlite3.Connection,
    step: str,
    prompt: str,
    *,
    model: str | None = None,
    max_tokens: int = 3000,
    timeout: int = 600,
):
    """调一次模型并返回解析后的 list/dict；失败返回 None（不抛）。

    timeout 是 requests 的**读**超时，不是总时长：只要服务端持续吐字节就不会触发。
    批很大时单次响应可能要好几分钟，180 秒那种默认值会误杀（实测踩过）。
    """
    model = model or LLM.model
    key = cache_key(step, model, prompt)

    row = conn.execute("SELECT response FROM llm_cache WHERE cache_key = ?", (key,)).fetchone()
    if row:
        try:
            return json.loads(row["response"])
        except (json.JSONDecodeError, TypeError):
            pass  # 缓存坏了就重新调

    try:
        text = llm_client.chat(
            [{"role": "user", "content": prompt}],
            model=model,
            temperature=0.0,
            max_tokens=max_tokens,
            timeout=timeout,
        )
    except llm_client.LLMError as exc:
        conn.execute(
            "INSERT OR REPLACE INTO schema_runs(step,item,status,detail,created_at) "
            "VALUES (?,?,?,?,datetime('now'))",
            (step, key[:12], "failed", f"LLMError: {exc}"[:500]),
        )
        conn.commit()
        return None

    obj = parse_json(text)
    if obj is None:
        conn.execute(
            "INSERT OR REPLACE INTO schema_runs(step,item,status,detail,created_at) "
            "VALUES (?,?,?,?,datetime('now'))",
            (step, key[:12], "failed", f"parse: {text[:400]}"[:500]),
        )
        conn.commit()
        return None

    obj = _normalize_shape(obj)
    conn.execute(
        "INSERT OR REPLACE INTO llm_cache(cache_key,step,response,created_at) "
        "VALUES (?,?,?,datetime('now'))",
        (key, step, json.dumps(obj, ensure_ascii=False)),
    )
    conn.commit()
    time.sleep(PACE_SECONDS)
    return obj


def mark(conn: sqlite3.Connection, step: str, item: str, status: str, detail: str = "") -> None:
    conn.execute(
        "INSERT OR REPLACE INTO schema_runs(step,item,status,detail,created_at) "
        "VALUES (?,?,?,?,datetime('now'))",
        (step, item, status, detail[:500]),
    )


def done_items(conn: sqlite3.Connection, step: str, status: str = "ok") -> set[str]:
    rows = conn.execute(
        "SELECT item FROM schema_runs WHERE step = ? AND status = ?", (step, status)
    ).fetchall()
    return {r["item"] for r in rows}


def progress(conn: sqlite3.Connection) -> dict:
    q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "predicate_map": q("SELECT COUNT(*) FROM predicate_map"),
        "entity_type": q("SELECT COUNT(*) FROM entity_type"),
        "entity_type_llm": q("SELECT COUNT(*) FROM entity_type WHERE decided_by='llm'"),
        "entity_merge": q("SELECT COUNT(*) FROM entity_merge"),
        "entity_sense": q("SELECT COUNT(*) FROM entity_sense"),
        "triple_sense": q("SELECT COUNT(*) FROM triple_sense"),
        "llm_cache": q("SELECT COUNT(*) FROM llm_cache"),
        "failed": q("SELECT COUNT(*) FROM schema_runs WHERE status='failed'"),
    }