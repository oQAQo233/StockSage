"""源体检器：判断一个 feed / 列表页还能不能用。

筛源判据是三条（README §4）：能解析出条目、条目带时间、正文抽得出 >400 字符。
这个脚本把三条都跑一遍，用来回答两个问题：

1. **现在的 11 个源还活着吗**（站点改版、RSS 下线是常态）——
   `python tools/probe_sources.py`
2. **某个候选源值不值得加**——
   `python tools/probe_sources.py --url https://example.com/rss --label 某站`

批量候选：`--candidates cands.txt`，每行 `名称<空格或制表符>URL`，`#` 开头为注释。

判定口径与 `sources/filters.py` 的运行时条件一致：
- 时间串没有时区标记时按 +08:00 折算（中文源的常见写法，见 `sources/timestamps.py`）；
- 「最新一条超过 `--max-age` 天」视为死源，直接判不可用。
"""

from __future__ import annotations

import argparse
import calendar
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import feedparser

sys.path.insert(0, "src")

from newskg.convert import extract_article, fetch_html  # noqa: E402
from newskg.sources.html_list import HTML_SPECS, date_from_url  # noqa: E402
from newskg.sources.tech import FEEDS  # noqa: E402
from newskg.sources.timestamps import iso_from_entry  # noqa: E402

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122 Safari/537.36"
TZ_MARK = re.compile(r"(?:GMT|UTC|Z\b|[+-]\d{2}:?\d{2})", re.I)
MIN_BODY = 400


def _fetch_bytes(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(800_000)


def _age_days(raw_time: str, parsed) -> float:
    """条目时间 -> 距今天数，口径与运行时一致（复用 `timestamps.iso_from_entry`）。"""
    entry = {"published": raw_time, "published_parsed": parsed}
    iso = iso_from_entry(entry, "zh")
    if not iso:
        return float("inf")
    dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 86400


def _body_chars(url: str) -> int:
    try:
        html = fetch_html(url)
        return len((extract_article(html, url).body_text or "").strip())
    except Exception:  # noqa: BLE001
        return -1


def _best_body(links: list[str]) -> int:
    """取前几个链接里最长的正文——列表页首条常是组图/视频，正文天然短。"""
    best = -1
    for link in links[:3]:
        chars = _body_chars(link)
        if chars > best:
            best = chars
        if best >= MIN_BODY:
            break
    return best


def probe_feed(label: str, url: str, max_age: float, check_body: bool) -> tuple[str, bool]:
    """探一个 RSS/Atom。返回（结论行, 是否可用）。"""
    try:
        raw = _fetch_bytes(url)
    except Exception as exc:  # noqa: BLE001
        return f"{label:14} 连不上            {type(exc).__name__}", False

    parsed = feedparser.parse(raw)
    if not parsed.entries:
        return f"{label:14} 不是 feed         {len(raw)} 字节", False

    dated = [
        (e.get("published") or e.get("updated") or "", e.get("published_parsed") or e.get("updated_parsed"))
        for e in parsed.entries
        if e.get("published_parsed") or e.get("updated_parsed")
    ]
    if not dated:
        return f"{label:14} 零时间戳          {len(parsed.entries)} 条（时间窗会失效）", False

    age = min(_age_days(raw_time, tm) for raw_time, tm in dated)
    if age > max_age:
        return f"{label:14} 日期过旧          最新一条 {age:.0f} 天前（死源）", False

    body = ""
    if check_body:
        links = [e.get("link") for e in parsed.entries if e.get("link")]
        chars = _best_body(links)
        if chars < 0:
            return f"{label:14} 抓不到正文        {age:.1f} 天前，链接打不开", False
        if chars < MIN_BODY:
            return f"{label:14} 正文过短          {chars} 字符（低于 {MIN_BODY}）", False
        body = f"正文 {chars} 字符"

    return f"{label:14} 可用              条目 {len(parsed.entries):4d}  最新 {age:4.1f} 天前  {body}", True


def probe_html(label: str, spec, max_age: float, check_body: bool) -> tuple[str, bool]:
    """探一个 HTML 列表页源（日期从 URL 读）。"""
    try:
        raw = _fetch_bytes(spec.list_url)
    except Exception as exc:  # noqa: BLE001
        return f"{label:14} 连不上            {type(exc).__name__}", False

    html = raw.decode("utf-8", "ignore")
    items = spec.item.findall(html)
    dated = [(href, date_from_url(href)) for href, _ in items]
    dated = [(href, day) for href, day in dated if day]
    if not dated:
        return f"{label:14} 无带日期条目      模板可能变了（{len(items)} 个链接）", False

    newest = max(day for _, day in dated)
    age = (time.time() - calendar.timegm(time.strptime(newest, "%Y-%m-%d"))) / 86400
    if age > max_age:
        return f"{label:14} 日期过旧          最新 {newest}（{age:.0f} 天前）", False

    body = ""
    if check_body:
        chars = _best_body([urljoin(spec.list_url, href) for href, _ in dated])
        if 0 <= chars < MIN_BODY:
            return f"{label:14} 正文过短          {chars} 字符", False
        body = f"正文 {chars} 字符" if chars >= 0 else "正文抓取失败"

    return f"{label:14} 可用              条目 {len(dated):4d}  最新 {newest}  {body}", True


def main() -> int:
    ap = argparse.ArgumentParser(description="源体检：能解析、带时间、正文够长")
    ap.add_argument("--url", help="单独探一个 feed URL")
    ap.add_argument("--label", default="候选", help="--url 的显示名")
    ap.add_argument("--html", help="单独探一个 HTML 列表页（需同时在 HTML_SPECS 里注册）")
    ap.add_argument("--candidates", help="批量候选文件，每行：名称 URL")
    ap.add_argument("--max-age", type=float, default=7.0, help="最新一条超过多少天算死源（默认 7）")
    ap.add_argument("--no-body", action="store_true", help="跳过正文抽取（更快）")
    args = ap.parse_args()

    check_body = not args.no_body
    results: list[bool] = []

    if args.url:
        line, ok = probe_feed(args.label, args.url, args.max_age, check_body)
        print(line)
        return 0 if ok else 1

    if args.html:
        spec = HTML_SPECS.get(args.html)
        if not spec:
            print(f"未注册的列表页源：{args.html}（可选：{', '.join(HTML_SPECS)}）")
            return 1
        line, ok = probe_html(args.label, spec, args.max_age, check_body)
        print(line)
        return 0 if ok else 1

    if args.candidates:
        rows = []
        for raw in Path(args.candidates).read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            label, _, url = line.partition(" ")
            rows.append((label.strip(), url.strip() or label.strip()))
        for label, url in rows:
            out, _ = probe_feed(label, url, args.max_age, check_body)
            print(out)
        return 0

    # 默认：体检当前注册的全部源
    print(f"== RSS 源（{len(FEEDS)} 个）")
    for key, (url, _lang, sitename) in FEEDS.items():
        line, ok = probe_feed(f"{key}", url, args.max_age, check_body)
        print(f"{line}   [{sitename}]")
        results.append(ok)

    print(f"== 列表页源（{len(HTML_SPECS)} 个）")
    for key, spec in HTML_SPECS.items():
        line, ok = probe_html(key, spec, args.max_age, check_body)
        print(f"{line}   [{spec.sitename}]")
        results.append(ok)

    alive = sum(results)
    print(f"\n可用 {alive}/{len(results)}")
    return 0 if alive == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())


