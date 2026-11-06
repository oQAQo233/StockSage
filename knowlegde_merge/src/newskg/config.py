"""全局路径与运行参数。

所有可配置项都优先读环境变量，其次用这里的默认值，
这样命令行、.env、CI 三种场景下行为一致。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# src/newskg/config.py -> src/newskg -> src -> <project root>
ROOT = Path(__file__).resolve().parents[2]


def load_dotenv(path: Path) -> int:
    """读极简 .env：KEY=VALUE，# 开头是注释，已存在的环境变量不覆盖。

    不引 python-dotenv：只有一个用途，二十行够了，还少一个依赖。
    必须在 LLMSettings 实例化之前调用——dataclass 的默认值是定义时就求值的。
    """
    if not path.exists():
        return 0
    count = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            count += 1
    return count


load_dotenv(ROOT / ".env")

DATA = ROOT / "data"
RAW = DATA / "raw"
MD = DATA / "md"
FIXTURES = DATA / "fixtures"
GOLD = DATA / "gold"
OUT = ROOT / "out"

DB_PATH = OUT / "kg.sqlite3"

USER_AGENT = os.getenv(
    "NEWSKG_USER_AGENT",
    "newskg-coursework/0.1 (+https://example.edu/course; contact: student)",
)

#: 抓取时每个域名之间的最小间隔（秒），避免把对方站点打疼
POLITE_DELAY = float(os.getenv("NEWSKG_POLITE_DELAY", "1.0"))

#: 单篇文章正文最长保留字符数，超出截断（防止 LLM 调用爆 token）
MAX_BODY_CHARS = int(os.getenv("NEWSKG_MAX_BODY_CHARS", "12000"))


@dataclass
class LLMSettings:
    """任意 OpenAI 兼容端点都能用：DeepSeek、Ollama、vLLM、OpenCode 均可。"""

    base_url: str = os.getenv("NEWSKG_LLM_BASE_URL", "https://api.deepseek.com/v1")
    api_key: str = os.getenv("NEWSKG_LLM_API_KEY", "")
    model: str = os.getenv("NEWSKG_LLM_MODEL", "deepseek-chat")
    temperature: float = float(os.getenv("NEWSKG_LLM_TEMPERATURE", "0.0"))
    timeout: int = int(os.getenv("NEWSKG_LLM_TIMEOUT", "120"))
    #: OpenCode Go 套餐要求带 x-opencode-session：一个客户端生成的 UUID，用于路由，
    #: 不是密钥。留空则自动生成；换成自建端点时可以设成空字符串关掉。
    session_id: str = os.getenv("NEWSKG_LLM_SESSION_ID", "")

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


@dataclass
class RunSettings:
    max_articles: int = int(os.getenv("NEWSKG_MAX_ARTICLES", "30"))
    window_days: int = int(os.getenv("NEWSKG_WINDOW_DAYS", "2"))
    # SimHash 汉明距离只做候选筛选，真正判定要靠 shingle Jaccard 二次确认。
    # 6 位在短文上足够宽容，误报交给 Jaccard 挡掉。
    dedup_hamming: int = int(os.getenv("NEWSKG_DEDUP_HAMMING", "6"))
    # 3 词 shingle 的重叠率下限：转载稿通常 >0.8，同事件不同稿通常 <0.4
    dedup_jaccard: float = float(os.getenv("NEWSKG_DEDUP_JACCARD", "0.55"))
    request_timeout: int = int(os.getenv("NEWSKG_REQUEST_TIMEOUT", "25"))


LLM = LLMSettings()
RUN = RunSettings()


def ensure_dirs() -> None:
    """建好所有输出目录；重复调用无副作用。"""
    for d in (DATA, RAW, MD, FIXTURES, GOLD, OUT):
        d.mkdir(parents=True, exist_ok=True)