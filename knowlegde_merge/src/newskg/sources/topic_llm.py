"""用 LLM 给候选新闻打「科技相关性」分（0-10），替代原来的关键词打分。

为什么换成 LLM
--------------
关键词打分在双语科技题材上失效得比财经更明显：

* 词表永远追不上新词——"VLA 模型""光互连""固态电池"今年才算热点，词表里没有；
* 假阳性高——"芯片法案"是政治新闻、也是科技新闻，看标题里有没有"芯片"分不出来；
* 中英要维护两套词表，而且中文的科技词大量是复合词（"算力集群""端侧推理"），
  子串匹配既漏又误。

LLM 只需一句领域定义，就能同时处理语言、判断"技术是否是主角"，并给出可解释的
理由（reason 会写进日志，便于调阈值）。

设计取舍
--------
* **批量打分**：20 条一次请求。候选本身只有标题+摘要（各 ≤400 字符），
  逐条请求的固定开销（系统提示）会占大头，批量能把每条的边际成本压到接近零。
* **只打分不筛选**：阈值由调用方（filters.py）决定，方便做阈值敏感性分析。
* **本地缓存**：同一 URL+标题+摘要在多轮调参时只花一次钱——这是本工程反复强调的
  「别为同一条数据重复付费」。
* **失败可降级**：某批解析失败返回 None，由调用方退回关键词粗筛并打警告，
  不让整条流水线因为一次 429 而丢光数据。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

from ..config import TOPIC

# --------------------------------------------------------------------------- #
# 提示词：这里就是「什么叫与科技相关」的定义
# --------------------------------------------------------------------------- #

TECH_DOMAIN = """科技（technology）指以工程技术、计算技术和科学研究的技术转化为主体的新闻。

判为科技的核心情形（技术本身是新闻主角）：
1. 计算与芯片：半导体、芯片设计/制造、AI 模型与训练、算力、操作系统、编程语言、
   数据库、云计算、开源项目、开发者工具；
2. 硬件与消费电子：手机、PC、可穿戴、AR/VR、显示与影像、机器人、无人机、智能汽车；
3. 前沿研究的技术转化：量子计算、生物技术工程化、新材料、航天工程、能源技术；
4. 网络安全与数据治理：漏洞与攻击、隐私、加密、数据合规技术；
5. 科技产业：科技公司的产品发布、技术路线、研发投入、技术人才与并购；
6. 科技政策与标准：出口管制、技术标准、频谱分配、AI 立法与监管。

明确不算科技（应打低分）：
- 纯政治、外交、军事冲突（除非核心议题是技术，如芯片出口管制、网络攻击归因）；
- 纯体育、娱乐、明星、影视（除非核心是技术产品发布）；
- 纯金融市场行情（股价涨跌、指数、基金）、纯宏观经济数据；
- 生活方式、健康养生、职场、教育（除非主题是新设备或新技术）；
- 泛商业新闻（零售、餐饮、地产、快消）。"""

RUBRIC = """打分档位（务必对齐锚点，不要普遍给 7-8 分）：
- 9-10：技术是整条新闻的主角（新模型发布、芯片流片、重大漏洞、技术突破、产品首发）；
- 7-8：科技是主线之一（科技公司的经营/人事/并购，且围绕其技术或产品）；
- 5-6：擦边（提到技术，但主体是商业、政策或社会议题）；
- 3-4：弱相关（技术只是背景或举例）；
- 0-2：无关（体育、娱乐、纯行情、纯宏观、生活服务）。"""

OUTPUT_CONTRACT = """只输出一个 JSON 对象，不要解释、不要 Markdown 代码块标记：
{"scores": [{"i": <候选序号>, "score": <0-10 的整数>, "reason": "<不超过 20 字的中文理由>"}]}

要求：
- 每个候选都要出现，序号与输入一致，不得增删；
- score 必须是整数；
- reason 指出决定性依据（例如「实为财经行情」「AI 监管立法」「芯片设计公司融资」）。"""


@dataclass
class TopicScore:
    score: int
    reason: str = ""
    cached: bool = False


@dataclass
class TopicScorer:
    """调用 OpenAI 兼容端点给候选打分。"""

    base_url: str = ""
    api_key: str = ""
    model: str = ""
    timeout: int = 60
    batch_size: int = 20
    cache_path: Path | None = None
    domain_desc: str = ""
    usage: dict = field(default_factory=dict, repr=False)
    _cache: dict = field(default_factory=dict, repr=False)
    _dirty: bool = False

    def __post_init__(self):
        self.base_url = (self.base_url or TOPIC.base_url).rstrip("/")
        self.api_key = self.api_key or TOPIC.api_key
        self.model = self.model or TOPIC.model
        self.timeout = self.timeout or TOPIC.timeout
        if self.cache_path is None:
            self.cache_path = TOPIC.cache_path
        self._cache = self._load_cache()

    # ------------------------------------------------------------------ 配置
    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)

    # ------------------------------------------------------------------ 缓存
    def _load_cache(self) -> dict:
        try:
            if self.cache_path and self.cache_path.exists():
                data = json.loads(self.cache_path.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001  缓存坏了不影响主流程
            pass
        return {}

    def _save_cache(self) -> None:
        if not self._dirty or not self.cache_path:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._cache, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.cache_path)  # 原子替换，避免中断留下半个文件
            self._dirty = False
        except Exception as exc:  # noqa: BLE001
            print(f"  [topic] 缓存写入失败（忽略）: {exc}")

    @staticmethod
    def _key(title: str, summary: str) -> str:
        raw = f"{(title or '').strip()}\x1f{(summary or '').strip()}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]

    # ------------------------------------------------------------------ 主流程
    def score_many(self, items: list[tuple[str, str, str]]) -> list[TopicScore | None]:
        """items: [(title, summary, source)]，返回等长的打分结果（失败为 None）。"""
        results: list[TopicScore | None] = [None] * len(items)
        pending: list[int] = []

        for idx, (title, summary, _source) in enumerate(items):
            hit = self._cache.get(self._key(title, summary))
            if isinstance(hit, dict) and isinstance(hit.get("score"), int):
                results[idx] = TopicScore(score=hit["score"], reason=hit.get("reason", ""), cached=True)
            else:
                pending.append(idx)

        if pending:
            cached_n = len(items) - len(pending)
            if cached_n:
                print(f"  [topic] 命中缓存 {cached_n} 条，待打分 {len(pending)} 条")

        for start in range(0, len(pending), self.batch_size):
            chunk = pending[start : start + self.batch_size]
            got = self._score_batch([items[i] for i in chunk])
            for offset, idx in enumerate(chunk):
                result = got.get(offset)
                results[idx] = result
                if result:
                    title, summary, _ = items[idx]
                    self._cache[self._key(title, summary)] = {
                        "score": result.score,
                        "reason": result.reason,
                        "model": self.model,
                        "ts": int(time.time()),
                    }
                    self._dirty = True

        self._save_cache()
        return results

    def _score_batch(self, batch: list[tuple[str, str, str]]) -> dict[int, TopicScore]:
        """一次请求打一批；返回 {批内偏移: TopicScore}，解析不到的条目缺席。"""
        if not self.configured:
            return {}

        system = "\n\n".join(
            [
                "你是新闻选题评审，负责判断一条新闻是否属于科技题材，并给出 0-10 的相关性分数。",
                "领域定义：\n" + (self.domain_desc.strip() or TECH_DOMAIN),
                RUBRIC,
                OUTPUT_CONTRACT,
            ]
        )
        lines = []
        for i, (title, summary, source) in enumerate(batch, start=1):
            summary = re.sub(r"\s+", " ", (summary or "")).strip()[:300]
            lines.append(f"{i}. 来源：{source}\n   标题：{title}\n   摘要：{summary or '（无）'}")
        user = "请给下面 {} 条候选打分：\n\n{}".format(len(batch), "\n".join(lines))

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "max_tokens": 120 + 80 * len(batch),
            "response_format": {"type": "json_object"},
        }

        data = self._post(payload)
        if data is None:
            return {}

        usage = data.get("usage") or {}
        for field_name in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if isinstance(usage.get(field_name), int):
                self.usage[field_name] = self.usage.get(field_name, 0) + usage[field_name]

        content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        parsed = self._parse(content)
        if parsed is None:
            print(f"  [topic] JSON 解析失败，该批退回关键词粗筛（原文前 80 字：{content[:80]!r}）")
            return {}

        out: dict[int, TopicScore] = {}
        for row in parsed.get("scores", []):
            try:
                pos = int(row.get("i")) - 1
                score = int(round(float(row.get("score"))))
            except (TypeError, ValueError):
                continue
            if 0 <= pos < len(batch):
                out[pos] = TopicScore(
                    score=max(0, min(10, score)),
                    reason=str(row.get("reason", ""))[:60],
                )
        return out

    def _post(self, payload: dict) -> dict | None:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        last_error = ""
        for attempt in range(3):
            try:
                resp = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                time.sleep(2 * (attempt + 1))
                continue

            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError:
                    last_error = "响应不是 JSON"
                    continue

            # 这些状态码重试没有意义，直接把可读原因抛给用户
            if resp.status_code in (401, 403):
                raise RuntimeError(
                    f"科技相关性打分失败：API key 被拒绝（HTTP {resp.status_code}）。"
                    "检查 NEWSKG_TOPIC_API_KEY / NEWSKG_TOPIC_BASE_URL。"
                )
            if resp.status_code == 402:
                raise RuntimeError("科技相关性打分失败：账户余额/额度不足（HTTP 402）。")
            if resp.status_code in (429, 500, 502, 503, 504):
                last_error = f"HTTP {resp.status_code}"
                time.sleep(3 * (attempt + 1))
                continue
            raise RuntimeError(
                f"科技相关性打分失败：HTTP {resp.status_code} {resp.text[:160]}"
            )

        print(f"  [topic] 重试 3 次仍失败（{last_error}），该批退回关键词粗筛")
        return None

    @staticmethod
    def _parse(content: str) -> dict | None:
        text = content.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        try:
            obj = json.loads(text)
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.S)
            if not match:
                return None
            try:
                obj = json.loads(match.group(0))
                return obj if isinstance(obj, dict) else None
            except json.JSONDecodeError:
                return None

    # ------------------------------------------------------------------ 便捷入口
    def close(self) -> None:
        self._save_cache()


def make_scorer(domain_desc: str = "") -> TopicScorer:
    return TopicScorer(domain_desc=domain_desc)