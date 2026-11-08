"""按配置重新采集行情与检索选定的新报道，输出独立 v1 真实数据快照。"""
import argparse
import hashlib
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from html import unescape
from pathlib import Path
from urllib.parse import urlencode

from .collect import PageMetadata, fetch, save_json
from .contracts import CALENDAR_FIELDS, NEWS_EXTRA, PRICE_EXTRA, STOCK_EXTRA, content_hash, local_file, publication_bound
from .data import NEWS_FIELDS, PRICE_FIELDS, SHANGHAI, STOCK_FIELDS, load_dataset, timestamp, write_csv


def price_rows(payload, provider, basis):
    value = json.loads(payload)
    if value.get("code") != 0:
        raise ValueError(f"行情源返回错误：{provider}")
    key = "qfqday" if basis == "qfq_snapshot" and provider != "sh000300" else "day"
    rows = value.get("data", {}).get(provider, {}).get(key)
    if not isinstance(rows, list) or not rows or any(not isinstance(r, list) or len(r) < 6 for r in rows):
        raise ValueError(f"行情源缺少 {provider}/{key}，不回退到旧数据")
    return rows


def article_record(payload, charset, spec, stocks, fetched_at):
    match = re.search(br'charset\s*=\s*["\x27]?([\w-]+)', payload[:8000], re.I)
    encoding = charset or (match[1].decode("ascii") if match else "utf-8")
    html = payload.decode(encoding)
    page = PageMetadata()
    page.feed(html)
    title = unescape(page.meta.get("og:title") or "".join(page.title)).strip()
    title = re.sub(r"\s*-\s*中国金融信息网$|_新浪财经_新浪网$|_新浪科技_新浪网$", "", title).strip()
    try:
        from .news_knowledge import convert
    except ModuleNotFoundError as error:
        raise ValueError(f"正文转换缺少 {error.name}；请安装 requirements-news.txt") from error
    extracted = convert.extract_article(html, url=spec["url"])
    body = extracted.body_text.strip()
    if not title or len(body) < 20:
        raise ValueError("标题/正文为空或疑似反爬页")
    precision = "second"
    published = page.meta.get("publishtime") or page.meta.get("weibo: article:create_at") or page.meta.get("article:published_time")
    if published:
        parsed = datetime.fromisoformat(published.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=SHANGHAI)
        published = parsed.astimezone(SHANGHAI).isoformat()
    else:
        # 查实际页面显示的日期，不从 URL 或电脑日期生成发布时间。
        visible = unescape(re.sub(r"<[^>]+>", " ", html))
        match = re.search(r"(20\d{2})年(\d{1,2})月(\d{1,2})日\s*(\d{2}):(\d{2})(?::(\d{2}))?", visible)
        if not match:
            raise ValueError("页面缺少可核验的完整发布时间")
        values = [int(v) if v else 0 for v in match.groups()]
        precision = "second" if match[6] else "minute"
        published = datetime(*values, tzinfo=SHANGHAI).isoformat()
    evidence = []
    for symbol in spec["symbols"]:
        stock = next(s for s in stocks if s["symbol"] == symbol)
        aliases = [a for a in stock["aliases"].split("|") if len(a) >= 3]
        found = next((a for a in [stock["company"], *aliases] if a in title or a in body), None)
        if not found:
            raise ValueError(f"页面不能支持人工选定的证券关联：{symbol}")
        sentences = re.split(r"(?<=[。！？])|\n+", title + "\n" + body)
        quote = next(s for s in sentences if found in s).strip()
        evidence.append(dict(symbol=symbol, alias=found, quote=quote[:260]))
    return dict(title=title, body=body, url=spec["url"], source=spec["source"],
                published_at=published, published_precision=precision, fetched_at=fetched_at,
                symbols=spec["symbols"], link_evidence=json.dumps(evidence, ensure_ascii=False),
                event_type="未分类", event_evidence="", document_type="media_report", text_kind="excerpt",
                text_engine=extracted.engine, body_chars=len(body))


def download(config, raw_dir):
    """每次网络采集；不读取旧数据集或冻结正文。失败记录留在原始清单。"""
    raw_dir.mkdir(parents=True, exist_ok=True)
    manifest = dict(config=config, requests=[], failures=[], collector="fresh-config-v1",
                    started_at=datetime.now(timezone.utc).isoformat())
    save_json(raw_dir / "manifest.json", manifest)
    providers = [s["symbol"][-2:].lower() + s["symbol"][:6] for s in config["stocks"]] + ["sh000300"]
    for provider in providers:
        adjustment = "qfq" if config["price_basis"] == "qfq_snapshot" and provider != "sh000300" else ""
        url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?" + urlencode({"param":f"{provider},day,{config['start']},{config['end']},640,{adjustment}"})
        try:
            payload, _ = fetch(url, attempts=2)
            rows = price_rows(payload, provider, config["price_basis"])
            name = f"{provider}.json"
            (raw_dir / name).write_bytes(payload)
            manifest["requests"].append(dict(file=name, symbol=provider, url=url, kind="prices",
                                             fetched_at=datetime.now(timezone.utc).isoformat(),
                                             sha256=hashlib.sha256(payload).hexdigest()))
            print(f"Downloaded {provider}: {len(rows)} provider rows, {rows[0][0]} .. {rows[-1][0]}", flush=True)
            save_json(raw_dir / "manifest.json", manifest)
        except Exception as error:
            manifest["failures"].append(dict(kind="prices", url=url, reason=str(error)))
            save_json(raw_dir / "manifest.json", manifest)
            raise
        time.sleep(.5)
    for index, spec in enumerate(config["articles"], 1):
        try:
            payload, charset = fetch(spec["url"], attempts=2)
            fetched_at = datetime.now(timezone.utc).isoformat()
            # 原始字节保留，不把重编码后的网页当成原始响应。
            raw_name = f"news_{index:03}.html"
            (raw_dir / raw_name).write_bytes(payload)
            article = article_record(payload, charset, spec, config["stocks"], fetched_at)
            if not config["start"] <= article["published_at"][:10] <= config["end"]:
                raise ValueError(f"新闻日期超出请求范围：{article['published_at']}")
            name = f"news_{index:03}.json"
            save_json(raw_dir / name, article)
            manifest["requests"].append(dict(file=name, html_file=raw_name, url=spec["url"], kind="news",
                                             fetched_at=fetched_at, sha256=hashlib.sha256((raw_dir/name).read_bytes()).hexdigest(),
                                             html_sha256=hashlib.sha256(payload).hexdigest()))
            print(f"Downloaded news {index}: {article['published_at'][:10]} {article['title'][:50]}", flush=True)
        except Exception as error:
            manifest["failures"].append(dict(kind="news", url=spec["url"], reason=str(error)))
            print(f"Failed news {index}: {error}", flush=True)
        save_json(raw_dir / "manifest.json", manifest)
        time.sleep(.5)
    manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
    save_json(raw_dir / "manifest.json", manifest)
    if not any(item["kind"] == "news" for item in manifest["requests"]):
        raise ValueError("没有成功新闻；未使用旧新闻替代")
    return manifest


def normalize(raw_dir, directory, mode):
    if mode not in {"observed", "historical_assumption"}:
        raise ValueError("未知新闻时间模式")
    raw_dir, directory = Path(raw_dir), Path(directory)
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("规范数据目录非空，请使用新目录保留快照")
    directory.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((raw_dir / "manifest.json").read_text(encoding="utf-8"))
    config, requests = manifest["config"], manifest["requests"]
    copied = directory / "raw"
    copied.mkdir()
    for entry in requests:
        for key, digest_key in (("file", "sha256"), ("html_file", "html_sha256")):
            if key not in entry:
                continue
            source = local_file(raw_dir, entry[key])
            payload = source.read_bytes()
            if hashlib.sha256(payload).hexdigest() != entry[digest_key]:
                raise ValueError(f"原始响应哈希不匹配：{entry[key]}")
            (copied / entry[key]).write_bytes(payload)
    (copied / "manifest.json").write_bytes((raw_dir/"manifest.json").read_bytes())
    stocks, prices, calendar, news = [], [], [], []
    for stock in config["stocks"]:
        provider = stock["symbol"][-2:].lower() + stock["symbol"][:6]
        entry = next(r for r in requests if r.get("symbol") == provider)
        stocks.append(dict(**stock, currency="CNY", security_type="A_SHARE", industry_scheme="course_manual_v1",
                           source_url=f"https://gu.qq.com/{provider}", fetched_at=entry["fetched_at"]))
        for row in price_rows((copied/entry["file"]).read_bytes(), provider, config["price_basis"]):
            if not config["start"] <= row[0] <= config["end"]:
                continue
            volume = Decimal(row[5]) * 100
            if volume != volume.to_integral_value():
                raise ValueError("行情成交量无法换算为整数股")
            prices.append(dict(symbol=stock["symbol"], date=row[0], open=row[1], close=row[2], high=row[3], low=row[4],
                               volume=int(volume), source="腾讯财经（本次重新下载）", source_url=entry["url"],
                               fetched_at=entry["fetched_at"], price_basis=config["price_basis"], raw_ref="raw/"+entry["file"]))
    entry = next(r for r in requests if r.get("symbol") == "sh000300")
    for exchange in sorted({s["exchange"] for s in stocks}):
        for row in price_rows((copied/entry["file"]).read_bytes(), "sh000300", "unadjusted"):
            if config["start"] <= row[0] <= config["end"]:
                calendar.append(dict(exchange=exchange, date=row[0], source="腾讯财经沪深300实际行情日（开市代理）",
                                     source_url=entry["url"], fetched_at=entry["fetched_at"], raw_ref="raw/"+entry["file"]))
    seen_urls, seen_hashes = set(), set()
    for entry in (r for r in requests if r["kind"] == "news"):
        item = json.loads((copied/entry["file"]).read_text(encoding="utf-8"))
        digest = content_hash(item["title"], item["body"])
        if item["url"] in seen_urls or digest in seen_hashes:
            continue
        seen_urls.add(item["url"]); seen_hashes.add(digest)
        available = timestamp(item["fetched_at"]) if mode == "observed" else publication_bound(item)+timedelta(minutes=5)
        news.append(dict(id="FRESH-"+hashlib.sha256(item["url"].encode()).hexdigest()[:16],
                         **{key:item[key] for key in ("title","body","source","url","published_at","published_precision","fetched_at","event_type","event_evidence","document_type","text_kind","link_evidence")},
                         symbols="|".join(item["symbols"]), available_at=available.isoformat(), availability_basis=mode,
                         first_seen_at=item["fetched_at"] if mode == "observed" else "", updated_at="", link_method="manual_verified",
                         content_sha256=digest, raw_ref="raw/"+entry["file"]))
    for stock in stocks:
        if not any(stock["symbol"] in n["symbols"].split("|") for n in news):
            raise ValueError(f"{stock['symbol']} 没有成功采集到关联新闻")
    write_csv(directory/"stocks.csv", STOCK_FIELDS+STOCK_EXTRA, stocks)
    write_csv(directory/"prices.csv", PRICE_FIELDS+PRICE_EXTRA, sorted(prices,key=lambda r:(r["symbol"],r["date"])))
    write_csv(directory/"news.csv", NEWS_FIELDS+NEWS_EXTRA, sorted(news,key=lambda r:r["published_at"]))
    write_csv(directory/"calendar.csv", CALENDAR_FIELDS, calendar)
    hashes = {p.relative_to(directory).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(directory.rglob("*")) if p.is_file()}
    save_json(directory/"metadata.json", dict(contract_version="1.0", dataset_id=config["dataset_name"]+"-"+mode+"-"+hashes["raw/manifest.json"][:12],
        synthetic=False, label=f"全新采集 · 新能源3股 · {config['start']}—{config['end']} · {'观测时间' if mode=='observed' else '历史回填假设'}",
        timezone="Asia/Shanghai", currency="CNY", volume_unit="share", price_basis=config["price_basis"],
        adjustment_asof=max(r["fetched_at"] for r in requests if r["kind"]=="prices"), news_time_mode=mode,
        calendar_basis="index_observed_sessions", coverage=dict(start=config["start"],end=config["end"]), news_completeness="curated_incomplete",
        usage_notes="本次联网采集的公开研究快照，不表示获得全文分发或商业许可；复权/公司行动与新闻覆盖限制须单独评估。",
        selection=config["discovery"], acquisition_started_at=manifest["started_at"], acquisition_completed_at=manifest.get("completed_at"),
        requested_news=len(config["articles"]), collected_news=len(news), collection_failures=manifest["failures"], sha256=hashes))
    load_dataset(directory)
    return directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--replay", type=Path, help="只从本次采集的 raw 清单重建")
    parser.add_argument("--availability-mode", choices=("observed","historical_assumption"), default="observed")
    args = parser.parse_args()
    try:
        if args.replay:
            raw_dir = args.replay
        else:
            if not args.config:
                raise ValueError("新采集需要 --config")
            config = json.loads(args.config.read_text(encoding="utf-8"))
            if date.fromisoformat(config["start"]) > date.fromisoformat(config["end"]):
                raise ValueError("采集区间顺序无效")
            if (args.output_dir/"metadata.json").exists():
                raise ValueError("目标已完成采集，拒绝覆盖")
            raw_dir = args.output_dir.with_name(args.output_dir.name+"_raw")
            download(config, raw_dir)
        normalize(raw_dir, args.output_dir, args.availability_mode)
    except (ValueError, OSError, KeyError, StopIteration) as error:
        parser.exit(2, f"新数据采集/校验失败：{error}\n")
    print(f"Validated NEW dataset: {args.output_dir.resolve()}", flush=True)


if __name__ == "__main__":
    main()
