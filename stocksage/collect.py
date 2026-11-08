"""固定小样本采集和离线重放；无 API Key，仅使用 Python 标准库。"""
import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .contracts import CALENDAR_FIELDS, NEWS_EXTRA, PRICE_EXTRA, STOCK_EXTRA, content_hash, publication_bound
from .data import NEWS_FIELDS, PRICE_FIELDS, STOCK_FIELDS, SHANGHAI, load_dataset, write_csv

START, END = "2025-01-01", "2025-04-30"
STOCKS = [
    dict(symbol="600519.SH", company="贵州茅台酒股份有限公司", industry="白酒", aliases="贵州茅台|茅台", exchange="SH"),
    dict(symbol="000858.SZ", company="宜宾五粮液股份有限公司", industry="白酒", aliases="五粮液", exchange="SZ"),
]
# 人工选取的链接清单，仅用于集成验收；不是完整历史新闻库。
ARTICLES = [
    dict(url="https://www.cnfin.com/gs-lb/detail/20250429/4225946_1.html", symbols="600519.SH", event_type="业绩", source="新华财经", format="cnfin"),
    dict(url="https://finance.sina.com.cn/wm/2025-03-04/doc-inenpfic8408485.shtml", symbols="600519.SH", event_type="回购", source="新浪财经（页面来源：酒业内参）", format="sina"),
    dict(url="https://finance.sina.com.cn/stock/zqgd/2025-02-06/doc-ineiqeaw0850082.shtml", symbols="600519.SH", event_type="回购", source="新浪财经（页面来源：券商中国）", format="sina",
         event_evidence="贵州茅台（600519）发布回购股份实施最新进展公告。"),
    dict(url="https://thinktank.cnfin.com/jy-sjlb/detail/20250326/4206284_1.html", symbols="000858.SZ", event_type="产品经营", source="新华财经", format="cnfin"),
    dict(url="https://finance.sina.com.cn/stock/aiassist/yjbg/2025-04-25/doc-ineumcmt9403735.shtml", symbols="000858.SZ", event_type="业绩", source="新浪财经-鹰眼工作室", format="sina", document_type="automated_report"),
]


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def fetch(url, attempts=3):
    """有限重试；403/404 等立即失败，不绕过访问限制。"""
    for attempt in range(attempts):
        try:
            with urlopen(Request(url, headers={"User-Agent": "StockSage-CourseResearch/0.1"}), timeout=25) as response:
                payload = response.read()
                charset = response.headers.get_content_charset()
            return payload, charset
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            if isinstance(error, HTTPError) and error.code not in {429, 500, 502, 503, 504}:
                raise
            if attempt + 1 == attempts:
                raise
            time.sleep(1 + attempt)


class PageMetadata(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta, self.title, self.in_title = {}, [], False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            self.meta[(attrs.get("name") or attrs.get("property") or "").lower()] = attrs.get("content", "")
        if tag == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, text):
        if self.in_title:
            self.title.append(text)


def extract_article(payload, charset, spec, fetched_at):
    charset_match = re.search(br'charset\s*=\s*["\x27]?([\w-]+)', payload[:8000], re.I)
    encoding = charset or (charset_match.group(1).decode("ascii") if charset_match else "utf-8")
    html = payload.decode(encoding)
    parser = PageMetadata()
    parser.feed(html)
    title = parser.meta.get("og:title") or "".join(parser.title)
    title = re.sub(r"\s+", " ", unescape(title)).strip()
    title = re.sub(r"\s*-\s*中国金融信息网$|_新浪财经_新浪网$", "", title).strip()
    if not title or not any(alias in title for stock in STOCKS if stock["symbol"] == spec["symbols"] for alias in stock["aliases"].split("|")):
        raise ValueError(f"标题无法确认证券关联：{spec['url']}")
    if spec["format"] == "cnfin":
        value = parser.meta.get("publishtime", "")
        precision = "second"
    else:
        value = parser.meta.get("weibo: article:create_at", "") or parser.meta.get("article:published_time", "")
        precision = "second"
        if not value:
            # 新浪正文发布日期；绝不从 URL 或当前股价插件推测时间。
            match = re.search(r'(2025年\d{1,2}月\d{1,2}日)\s*(\d{2}:\d{2})(?::(\d{2}))?', html)
            if not match:
                raise ValueError(f"页面缺少可核验的发布时间：{spec['url']}")
            value = re.sub(r"年|月", "-", match[1]).replace("日", "") + " " + match[2]
            if match[3]:
                value += ":" + match[3]
            else:
                precision = "minute"
    parsed = datetime.fromisoformat(value)
    published = (parsed.replace(tzinfo=SHANGHAI) if parsed.utcoffset() is None else parsed.astimezone(SHANGHAI)).isoformat()
    evidence = spec.get("event_evidence", title)
    if spec.get("event_evidence"):
        visible = re.sub(r"\s+", "", unescape(re.sub(r"<[^>]+>", "", html)))
        if re.sub(r"\s+", "", evidence) not in visible:
            raise ValueError("人工事件证据未在原页面中找到")
    return dict(url=spec["url"], title=title, body="", source=spec["source"], published_at=published,
                published_precision=precision, symbols=spec["symbols"], event_type=spec["event_type"],
                fetched_at=fetched_at, response_sha256=hashlib.sha256(payload).hexdigest(),
                event_evidence=evidence, document_type=spec.get("document_type", "media_report"),
                evidence_kind="page_metadata_excerpt", note="仅保留页面标题与时间元数据；未保存或授权转载全文。")


def download(raw_dir):
    raw_dir.mkdir(parents=True)
    manifest = dict(start=START, end=END, requests=[], adapter_version="tencent-cnfin-sina-v1")
    for symbol in ("sh600519", "sz000858", "sh000300"):
        url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?" + urlencode({"param": f"{symbol},day,{START},{END},320,qfq"})
        payload, _ = fetch(url)
        json.loads(payload)  # HTML 限流页/JSONP 不应伪装成有效行情。
        name = symbol + ".json"
        (raw_dir / name).write_bytes(payload)
        manifest["requests"].append(dict(file=name, symbol=symbol, url=url,
                                         fetched_at=datetime.now(timezone.utc).isoformat(),
                                         sha256=hashlib.sha256(payload).hexdigest(), kind="prices"))
        print(f"Fetched {symbol}", flush=True)
        time.sleep(0.5)
    for index, spec in enumerate(ARTICLES, 1):
        payload, charset = fetch(spec["url"])
        fetched_at = datetime.now(timezone.utc).isoformat()
        extracted = extract_article(payload, charset, spec, fetched_at)
        name = f"article_{index:02}.json"
        save_json(raw_dir / name, extracted)
        manifest["requests"].append(dict(file=name, url=spec["url"], fetched_at=fetched_at,
                                         sha256=hashlib.sha256((raw_dir / name).read_bytes()).hexdigest(), kind="news"))
        print(f"Fetched article {index}", flush=True)
        time.sleep(0.5)
    save_json(raw_dir / "manifest.json", manifest)


def parse_tencent(payload, symbol):
    value = json.loads(payload)
    if value.get("code") != 0 or symbol not in value.get("data", {}):
        raise ValueError(f"腾讯返回错误：{symbol}")
    block = value["data"][symbol]
    # 指数无需复权，返回 day；股票必须有 qfqday，禁止静默回落到不复权。
    key = "day" if symbol == "sh000300" else "qfqday"
    rows = block.get(key)
    if not isinstance(rows, list) or not rows or any(not isinstance(row, list) or len(row) < 6 for row in rows):
        raise ValueError(f"腾讯 {symbol} 缺少 {key} 或返回结构改变")
    return rows


def normalize(raw_dir, directory):
    """原始 JSON / 新闻元数据摘录 -> v1 CSV；相同快照离线重建一致。"""
    raw_dir, directory = Path(raw_dir), Path(directory)
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("目标目录非空；请指定一个新的目录以保留原快照")
    directory.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((raw_dir / "manifest.json").read_text(encoding="utf-8"))
    copied = directory / "raw"
    copied.mkdir()
    (copied / "manifest.json").write_bytes((raw_dir / "manifest.json").read_bytes())
    requests = manifest["requests"]
    for item in requests:
        name = item["file"]
        if Path(name).name != name:
            raise ValueError("原始快照文件名必须为单层文件名")
        payload = (raw_dir / name).read_bytes()
        if hashlib.sha256(payload).hexdigest() != item["sha256"]:
            raise ValueError(f"原始快照 SHA256 不匹配：{name}")
        (copied / name).write_bytes(payload)
    stocks, prices, news, calendar = [], [], [], []
    for stock, provider in zip(STOCKS, ("sh600519", "sz000858")):
        entry = next(item for item in requests if item.get("symbol") == provider)
        stocks.append(dict(**stock, currency="CNY", security_type="A_SHARE", industry_scheme="course_manual_v1",
                           source_url=f"https://gu.qq.com/{provider}", fetched_at=entry["fetched_at"]))
        rows = parse_tencent((copied / entry["file"]).read_bytes(), provider)
        for row in rows:
            if not manifest["start"] <= row[0] <= manifest["end"]:
                continue
            # 腾讯原始 K 线的第六列是手；AKShare 新版已换算为股，不可再乘一次。
            volume = Decimal(row[5]) * 100
            if volume != volume.to_integral_value():
                raise ValueError("原始成交量换算后不是整数股")
            prices.append(dict(symbol=stock["symbol"], date=row[0], open=row[1], close=row[2], high=row[3], low=row[4],
                               volume=int(volume), source="腾讯财经", source_url=entry["url"], fetched_at=entry["fetched_at"],
                               price_basis="qfq_snapshot", raw_ref="raw/" + entry["file"]))
    entry = next(item for item in requests if item.get("symbol") == "sh000300")
    index_rows = parse_tencent((copied / entry["file"]).read_bytes(), "sh000300")
    for exchange in ("SH", "SZ"):
        for row in index_rows:
            if manifest["start"] <= row[0] <= manifest["end"]:
                calendar.append(dict(exchange=exchange, date=row[0], source="腾讯财经沪深300实际行情日（开市代理）",
                                     source_url=entry["url"], fetched_at=entry["fetched_at"], raw_ref="raw/" + entry["file"]))
    for entry in (item for item in requests if item["kind"] == "news"):
        item = json.loads((copied / entry["file"]).read_text(encoding="utf-8"))
        identifier = "NEWS-" + hashlib.sha256(item["url"].encode()).hexdigest()[:16]
        news.append(dict(id=identifier, **{k: item[k] for k in ("title", "body", "source", "url", "published_at", "symbols", "event_type", "published_precision", "fetched_at")},
                         available_at=(publication_bound(item) + timedelta(minutes=5)).isoformat(),
                         document_type=item["document_type"], text_kind="title_only", availability_basis="historical_assumption",
                         first_seen_at="", updated_at="", link_method="manual_verified", link_evidence=item["title"],
                         event_evidence=item["event_evidence"], content_sha256=content_hash(item["title"], item["body"]),
                         raw_ref="raw/" + entry["file"]))
    write_csv(directory / "stocks.csv", STOCK_FIELDS + STOCK_EXTRA, stocks)
    write_csv(directory / "prices.csv", PRICE_FIELDS + PRICE_EXTRA, sorted(prices, key=lambda p: (p["symbol"], p["date"])))
    write_csv(directory / "news.csv", NEWS_FIELDS + NEWS_EXTRA, sorted(news, key=lambda n: (n["published_at"], n["id"])))
    write_csv(directory / "calendar.csv", CALENDAR_FIELDS, calendar)
    hashes = {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(directory.rglob("*")) if p.is_file()}
    save_json(directory / "metadata.json", dict(
        contract_version="1.0", dataset_id="cn-a-liquor-2025-jan-apr-v1-" + hashlib.sha256((copied / "manifest.json").read_bytes()).hexdigest()[:12], synthetic=False,
        label="真实数据小样本 · 两只白酒A股 · 前复权快照 · 历史新闻回填（仅标题）",
        timezone="Asia/Shanghai", currency="CNY", volume_unit="share", price_basis="qfq_snapshot",
        adjustment_asof=max(item["fetched_at"] for item in requests if item["kind"] == "prices"),
        news_time_mode="historical_assumption", calendar_basis="index_observed_sessions",
        coverage=dict(start=manifest["start"], end=manifest["end"]), news_completeness="curated_incomplete",
        usage_notes="公开来源的课程研究快照，不表示已获得商业使用或全文转载许可；新闻仅留标题、链接与时间元数据。行情来源许可须在对外分发前另行核实。",
        selection="两家公司及五个报道链接按获取便利选取，仅做数据链路验收，不是代表性抽样或有效预测证据。",
        sha256=hashes))
    load_dataset(directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True, help="新的规范数据目录；已有数据不会被覆盖")
    parser.add_argument("--replay", type=Path, help="已有 raw 快照目录；提供时完全不访问网络")
    args = parser.parse_args()
    try:
        if args.replay:
            normalize(args.replay, args.output_dir)
        else:
            raw = args.output_dir.with_name(args.output_dir.name + "_raw")
            if (args.output_dir.exists() and any(args.output_dir.iterdir())) or raw.exists():
                raise ValueError("采集目标或 raw 目录已存在；请指定新名称")
            download(raw)
            normalize(raw, args.output_dir)
    except (ValueError, OSError, KeyError, StopIteration) as error:
        parser.exit(2, f"采集/转换失败，未伪造或替换数据：{error}\n")
    print(f"Validated real dataset: {args.output_dir}")


if __name__ == "__main__":
    main()
