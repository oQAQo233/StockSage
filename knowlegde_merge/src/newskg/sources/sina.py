"""新浪科技产经滚动（feed.mix.sina.com.cn）的按日回溯。

为什么加这个源：上一轮把政府站排除后，需要一家**国内知名科技媒体**，且满足
（1）权威性中等以上、（2）11-04~11-11 窗口内确实有稿、（3）企业/商业主体多而非政策稿。

实测证据（2026-11-20）：
- 接口 `feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2515&num=50&page=N`
  直接返回 JSON，每条带 `ctime`（精确到秒），无需破解签名或过 WAF。
- 翻页是线性的：page 1 是 11-19~11-20，page 10 是 11-06~11-09，page 20 到 10-26，
  所以按页扫描即可稳定覆盖任意目标日。
- 窗口内 288 篇，其中 131 篇标题直接含科技实体；内容是企业向的
  （优必选人形机器人采购、荣耀 Robot Phone、夸克叫停真人讲题、MiniMax 谈中美 AI 差距…）。
- 正文抽取稳定：随机 15 篇全部成功，中位数 633 字，0 篇短于 150 字。

一个必须记住的事实：这些文章的域名都在 `finance.sina.com.cn` 下——它是**新浪财经的
科技产经滚动**，不是 `tech.sina.com.cn`。因此纯金融稿（股价、财报、基金）会混进来，
需要靠上层 topic 过滤挡掉；本适配器只负责「按天取全」，不做题材判断。

注意：lid=2516 / 2518 看起来像纯科技频道，但实测翻不回窗口（pages 5~14 全 0 条），
只有 2515 可用。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from ..models import ArticleStub
from .base import http_get

BJT = timezone(timedelta(hours=8))

API = "https://feed.mix.sina.com.cn/api/roll/get"
#: lid=2515 是财经站的科技产经滚动；2516/2518 翻不到历史，不要换
LID = "2515"
PAGE_SIZE = 50
#: 防止目标日太老时无限翻页
MAX_PAGES = 40


class SinaTechDay:
    """新浪科技产经滚动 · 按日回溯。"""

    name = "sina_day"
    sitename = "新浪科技"
    license = "新闻内容版权归新浪所有"

    def __init__(self, day: str = ""):
        self.day = (day or os.getenv("NEWSKG_ARCHIVE_DATE", "")).strip()

    def _target(self) -> datetime:
        if not self.day:
            raise SystemExit(f"[{self.name}] 按日期抓取需要指定日期：--day 2026-11-04")
        try:
            return datetime.strptime(self.day, "%Y-%m-%d")
        except ValueError:
            raise SystemExit(f"[{self.name}] 日期格式应为 YYYY-MM-DD，收到 {self.day!r}") from None

    def _fetch_page(self, page: int) -> list[dict]:
        resp = http_get(
            API,
            params={
                "pageid": "153",
                "lid": LID,
                "k": "",
                "num": str(PAGE_SIZE),
                "page": str(page),
            },
            timeout=20,
        )
        if resp is None or resp.status_code != 200:
            return []
        try:
            data = json.loads(resp.content.decode("utf-8", "ignore"))
        except json.JSONDecodeError:
            return []
        return data.get("result", {}).get("data") or []

    def discover(self, query: str | None, limit: int, days: int) -> list[ArticleStub]:
        target = self._target()
        stubs: list[ArticleStub] = []
        seen: set[str] = set()
        pages_read = 0

        for page in range(1, MAX_PAGES + 1):
            items = self._fetch_page(page)
            if not items:
                break
            pages_read += 1

            stamps: list[datetime] = []
            for item in items:
                raw = item.get("ctime")
                url = (item.get("url") or "").strip()
                title = (item.get("title") or "").strip()
                if not (raw and url and title):
                    continue
                try:
                    published = datetime.fromtimestamp(int(raw), tz=BJT)
                except (ValueError, OSError, OverflowError):
                    continue
                stamps.append(published)
                if published.date() != target.date() or url in seen:
                    continue
                if query and query.lower() not in title.lower():
                    continue
                seen.add(url)
                stubs.append(
                    ArticleStub(
                        source=self.name,
                        url=url,
                        title=title,
                        published_at=published.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        lang="zh",
                        license=self.license,
                        extra={"org": self.sitename, "page": page, "lid": LID},
                    )
                )
                if len(stubs) >= limit:
                    break

            if len(stubs) >= limit:
                break
            # 整页最旧的时间早于目标日 → 已经翻过目标日，停。
            # 用「最旧」而不是「某一条」，避免置顶/推荐把旧稿插进来时误判。
            if stamps and min(stamps).date() < target.date():
                break

        print(
            f"  [{self.name}] {target:%Y-%m-%d}：{len(stubs)} 条"
            f"（翻了 {pages_read} 页，接口 lid={LID}）"
        )
        return stubs


__all__ = ["SinaTechDay"]