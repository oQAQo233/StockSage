"""规范化 CSV 接口及可复现的合成数据，不依赖外网。"""
import csv
import json
import math
import random
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from .contracts import publication_bound, validate_contract

SHANGHAI = timezone(timedelta(hours=8))
STOCK_FIELDS = ("symbol", "company", "industry", "aliases")
PRICE_FIELDS = ("symbol", "date", "open", "high", "low", "close", "volume")
NEWS_FIELDS = ("id", "title", "body", "source", "url", "published_at", "available_at", "symbols", "event_type")


def timestamp(value):
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    result = datetime.fromisoformat(value)
    if result.utcoffset() is None:
        raise ValueError(f"时间必须含时区：{value}")
    return result.astimezone(SHANGHAI)


def read_csv(path, fields):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = set(fields) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path.name} 缺少列：{', '.join(sorted(missing))}")
        rows = list(reader)
        for number, row in enumerate(rows, start=2):
            if None in row or any(row[field] is None for field in fields):
                raise ValueError(f"{path.name} 第 {number} 行列数与表头不一致")
        return rows


def write_csv(path, fields, rows):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def generate_demo(directory):
    directory.mkdir(parents=True, exist_ok=True)
    rng = random.Random(20261105)
    stocks = [
        dict(symbol="DEMO01", company="星河科技（虚构）", industry="半导体", aliases="星河科技|星河"),
        dict(symbol="DEMO02", company="青岚能源（虚构）", industry="新能源", aliases="青岚能源|青岚"),
        dict(symbol="DEMO03", company="远川消费（虚构）", industry="消费", aliases="远川消费|远川"),
    ]
    days, current = [], date(2025, 1, 6)
    while len(days) < 110:
        # 演示日历仅排除周末；真实模式完全采用输入行情中的交易日。
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    prices, news = [], []
    events = [
        ("订单增长", "公司新增订单增长，需求改善，预期盈利提升。", "订单", 1),
        ("业绩承压", "公司利润下滑，需求疲软，经营风险增加。", "业绩", -1),
        ("发布经营简报", "公司发布经营情况简报，业务保持平稳。", "公告", 0),
        ("技术突破", "研发取得突破，产品获得认证，业务增长。", "研发", 1),
        ("项目延期", "项目建设延期，成本上升，面临不确定性。", "项目", -1),
    ]
    for index, stock in enumerate(stocks):
        close, prior_signal = 30.0 + index * 18, 0
        for i, day in enumerate(days):
            opening = close * (1 + rng.gauss(0, 0.003))
            # 有意植入微弱新闻信号以检验数据链路，不代表真实市场可预测性。
            change = 0.0004 + 0.004 * prior_signal + rng.gauss(0, 0.012)
            close *= 1 + change
            prices.append(dict(symbol=stock["symbol"], date=day.isoformat(), open=round(opening, 2),
                               high=round(max(opening, close) * (1 + rng.uniform(.002, .015)), 2),
                               low=round(min(opening, close) * (1 - rng.uniform(.002, .015)), 2),
                               close=round(close, 2), volume=int(800000 * rng.uniform(.6, 1.7))))
            prior_signal = 0
            if i % 3 == index % 3:
                headline, body, event, signal = rng.choice(events)
                hour = 18 if i % 4 == 0 else 10
                published = datetime.combine(day, time(hour, 0), SHANGHAI)
                news.append(dict(id=f"N{index + 1}-{i:03}", title=f"{stock['aliases'].split('|')[0]}：{headline}",
                                 body=body, source="合成新闻生成器", url="",
                                 published_at=published.isoformat(), available_at=(published + timedelta(minutes=5)).isoformat(),
                                 symbols=stock["symbol"], event_type=event))
                # 只在次日影响生成价格；盘后新闻仍不会提前进入当日特征。
                prior_signal = signal
    write_csv(directory / "stocks.csv", STOCK_FIELDS, stocks)
    write_csv(directory / "prices.csv", PRICE_FIELDS, prices)
    write_csv(directory / "news.csv", NEWS_FIELDS, news)
    (directory / "metadata.json").write_text(json.dumps(dict(
        synthetic=True, label="合成演示数据 · 非真实行情", seed=20261105,
        calendar="工作日模拟，未处理法定节假日", price_basis="合成价格，无公司行动"), ensure_ascii=False, indent=2), encoding="utf-8")


def load_dataset(directory):
    directory = Path(directory)
    meta_path = directory / "metadata.json"
    metadata = json.loads(meta_path.read_text(encoding="utf-8-sig")) if meta_path.exists() else {}
    if not isinstance(metadata, dict):
        raise ValueError("metadata.json 必须是对象")
    if metadata.get("synthetic") is False and "contract_version" not in metadata:
        raise ValueError("真实数据必须声明 contract_version 并提供 v1 来源与口径")
    stocks = read_csv(directory / "stocks.csv", STOCK_FIELDS)
    prices = read_csv(directory / "prices.csv", PRICE_FIELDS)
    news = read_csv(directory / "news.csv", NEWS_FIELDS)
    if not stocks or not prices:
        raise ValueError("股票清单与行情不能为空")
    symbols, seen = set(), set()
    for stock in stocks:
        symbol = stock["symbol"].strip()
        if not symbol or symbol != stock["symbol"] or symbol in symbols or not stock["company"]:
            raise ValueError("股票代码需唯一、非空且无首尾空格，公司名不能为空")
        symbols.add(symbol)
    for row in prices:
        if row["symbol"] not in symbols:
            raise ValueError(f"行情包含未知股票：{row['symbol']}")
        if date.fromisoformat(row["date"]).isoformat() != row["date"]:
            raise ValueError(f"行情日期须为 YYYY-MM-DD：{row['date']}")
        key = row["symbol"], row["date"]
        if key in seen:
            raise ValueError(f"行情日期重复：{key}")
        seen.add(key)
        for field in ("open", "high", "low", "close", "volume"):
            row[field] = float(row[field])
            if not math.isfinite(row[field]) or row[field] < 0 or (field != "volume" and row[field] == 0):
                raise ValueError(f"行情数值无效：{key} {field}")
        if row["high"] < max(row["open"], row["close"]) or row["low"] > min(row["open"], row["close"]):
            raise ValueError(f"OHLC 范围无效：{key}")
    ids = set()
    for row in news:
        if not row["id"] or row["id"] in ids or not row["title"] or not row["source"]:
            raise ValueError("新闻 ID 必须非空且唯一，标题与来源不能为空")
        ids.add(row["id"])
        if timestamp(row["available_at"]) < publication_bound(row):
            raise ValueError(f"可用时间早于发布时间：{row['id']}")
        linked = [item.strip() for item in row["symbols"].split("|") if item.strip()]
        if not linked or len(linked) != len(set(linked)) or set(linked) - symbols:
            raise ValueError(f"新闻关联股票为空、重复或未知：{row['id']}")
        row["symbols"] = linked
    for symbol in symbols:
        if sum(row["symbol"] == symbol for row in prices) < 30:
            raise ValueError(f"{symbol} 至少需要 30 个交易日，建议 100 个以上")
    if "synthetic" in metadata and metadata["synthetic"] is not None and not isinstance(metadata["synthetic"], bool):
        raise ValueError("synthetic 必须为 true、false 或 null")
    if "label" in metadata and (not isinstance(metadata["label"], str) or not metadata["label"].strip()):
        raise ValueError("label 必须是非空文本")
    metadata.setdefault("synthetic", None)
    metadata.setdefault("label", "用户导入 CSV · 真实性由提供者确认")
    if "contract_version" in metadata:
        validate_contract(directory, stocks, prices, news, metadata)
    return stocks, sorted(prices, key=lambda row: (row["symbol"], row["date"])), news, metadata
