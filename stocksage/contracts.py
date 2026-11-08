"""真实数据 v1 契约；保持旧合成 CSV 的兼容入口。"""
import hashlib
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from urllib.parse import urlsplit

STOCK_EXTRA = ("exchange", "currency", "security_type", "industry_scheme", "source_url", "fetched_at")
PRICE_EXTRA = ("source", "source_url", "fetched_at", "price_basis", "raw_ref")
NEWS_EXTRA = ("document_type", "text_kind", "published_precision", "availability_basis",
              "first_seen_at", "fetched_at", "updated_at", "link_method", "link_evidence",
              "event_evidence", "content_sha256", "raw_ref")
CALENDAR_FIELDS = ("exchange", "date", "source", "source_url", "fetched_at", "raw_ref")
EVENT_TYPES = {"业绩", "分红", "回购", "股东增减持", "订单", "研发", "政策", "并购", "诉讼处罚", "项目", "管理层", "产品经营", "其他", "未分类"}


def content_hash(title, body):
    return hashlib.sha256((title.strip() + "\n" + body.strip()).encode("utf-8")).hexdigest()


def publication_bound(row):
    from .data import SHANGHAI, timestamp
    precision = row.get("published_precision", "second")
    value = row["published_at"]
    if precision == "date":
        day = date.fromisoformat(value)
        if day.isoformat() != value:
            raise ValueError("日期精度的 published_at 必须为 YYYY-MM-DD")
        return datetime.combine(day + timedelta(days=1), time(), SHANGHAI)
    parsed = timestamp(value)
    if precision == "minute":
        if parsed.second or parsed.microsecond:
            raise ValueError("分钟精度时间的秒数必须为 0")
        return parsed + timedelta(minutes=1)
    if precision != "second":
        raise ValueError("published_precision 必须为 second、minute 或 date")
    return parsed


def local_file(directory, reference):
    root = Path(directory).resolve()
    path = (root / reference).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError(f"快照路径越出数据目录：{reference}")
    return path


def validate_contract(directory, stocks, prices, news, metadata):
    from .data import SHANGHAI, read_csv, timestamp
    if metadata.get("contract_version") != "1.0":
        raise ValueError("不支持的数据契约版本；当前为 1.0")
    required_meta = ("dataset_id", "label", "synthetic", "timezone", "currency", "volume_unit", "price_basis",
                     "adjustment_asof", "news_time_mode", "calendar_basis", "coverage", "usage_notes", "sha256")
    for field in required_meta:
        if field not in metadata:
            raise ValueError(f"metadata 缺少 {field}")
    for field in ("dataset_id", "label", "timezone", "currency", "volume_unit", "price_basis", "news_time_mode", "calendar_basis", "usage_notes"):
        if not isinstance(metadata[field], str) or not metadata[field].strip():
            raise ValueError(f"metadata {field} 必须为非空文本")
    if metadata["synthetic"] is not False:
        raise ValueError("v1 真实数据契约要求 synthetic=false")
    for key, expected in (("timezone", "Asia/Shanghai"), ("currency", "CNY"), ("volume_unit", "share")):
        if metadata[key] != expected:
            raise ValueError(f"v1 要求 {key}={expected}")
    if metadata["price_basis"] not in {"unadjusted", "qfq_snapshot", "hfq_snapshot"}:
        raise ValueError("未知 price_basis")
    if metadata["price_basis"] != "unadjusted":
        timestamp(metadata["adjustment_asof"])
    if metadata["news_time_mode"] not in {"historical_assumption", "observed"}:
        raise ValueError("news_time_mode 无效")
    if metadata["calendar_basis"] not in {"official_sessions", "index_observed_sessions"}:
        raise ValueError("calendar_basis 无效")
    coverage = metadata["coverage"]
    if not isinstance(coverage, dict) or not {"start", "end"} <= coverage.keys():
        raise ValueError("coverage 必须包含 start/end")
    if not all(isinstance(coverage[key], str) for key in ("start", "end")):
        raise ValueError("coverage 日期必须为文本")
    start, end = date.fromisoformat(coverage["start"]), date.fromisoformat(coverage["end"])
    if start > end:
        raise ValueError("coverage 日期顺序无效")
    if start.isoformat() != coverage["start"] or end.isoformat() != coverage["end"]:
        raise ValueError("coverage 日期须为 YYYY-MM-DD")
    if not metadata["usage_notes"] or not metadata["dataset_id"]:
        raise ValueError("dataset_id 与 usage_notes 不能为空")
    hashes = metadata["sha256"]
    if not isinstance(hashes, dict) or not {"stocks.csv", "prices.csv", "news.csv", "calendar.csv"} <= hashes.keys():
        raise ValueError("sha256 须覆盖四张 CSV 及引用快照")
    for reference, digest in hashes.items():
        if not re.fullmatch(r"[0-9a-f]{64}", str(digest)):
            raise ValueError(f"SHA256 格式无效：{reference}")
        if hashlib.sha256(local_file(directory, reference).read_bytes()).hexdigest() != digest:
            raise ValueError(f"SHA256 不匹配：{reference}")

    def fields(row, names, blank=()):
        for name in names:
            if name not in row or (name not in blank and not str(row[name]).strip()):
                raise ValueError(f"真实数据缺少字段 {name}")

    def url(value):
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"来源 URL 无效：{value}")

    def provenance(row):
        url(row["source_url"])
        timestamp(row["fetched_at"])
        if row.get("raw_ref") not in hashes:
            raise ValueError("raw_ref 未登记 SHA256")

    lookup = {s["symbol"]: s for s in stocks}
    for stock in stocks:
        fields(stock, STOCK_EXTRA + ("industry",))
        if not re.fullmatch(r"\d{6}\.(SH|SZ)", stock["symbol"]) or stock["exchange"] != stock["symbol"][-2:]:
            raise ValueError("证券标识必须为六位代码.SH/SZ，且与 exchange 一致")
        if stock["currency"] != "CNY" or stock["security_type"] != "A_SHARE":
            raise ValueError("首版仅接受人民币 A 股")
        url(stock["source_url"])
        timestamp(stock["fetched_at"])
    for row in prices:
        fields(row, PRICE_EXTRA)
        provenance(row)
        if row["price_basis"] != metadata["price_basis"]:
            raise ValueError("不允许混用复权口径")
        close_time = datetime.combine(date.fromisoformat(row["date"]), time(15), SHANGHAI)
        if timestamp(row["fetched_at"]) < close_time:
            raise ValueError("完整日线的抓取时间不得早于该日收盘")
        if metadata["price_basis"] != "unadjusted" and timestamp(row["fetched_at"]) > timestamp(metadata["adjustment_asof"]):
            raise ValueError("复权快照截止时间早于行情抓取时间")
        if not start.isoformat() <= row["date"] <= end.isoformat():
            raise ValueError("行情超出 coverage")
        if not row["volume"].is_integer():
            raise ValueError("volume 应为整数股数")
    seen_urls, seen_content = set(), set()
    for row in news:
        fields(row, NEWS_EXTRA + ("title", "source"), blank=("first_seen_at", "updated_at", "event_evidence"))
        url(row["url"])
        timestamp(row["fetched_at"])
        if row["raw_ref"] not in hashes:
            raise ValueError("新闻 raw_ref 未登记 SHA256")
        if row["document_type"] not in {"media_report", "company_announcement", "commentary", "automated_report"}:
            raise ValueError("document_type 无效")
        if row["text_kind"] not in {"title_only", "excerpt", "licensed_fulltext"}:
            raise ValueError("text_kind 无效")
        if row["text_kind"] == "title_only" and row["body"]:
            raise ValueError("title_only 的正文应留空")
        if row["text_kind"] != "title_only" and not row["body"].strip():
            raise ValueError("摘录或全文正文不能为空")
        if row["event_type"] not in EVENT_TYPES or (row["event_type"] != "未分类" and not row["event_evidence"]):
            raise ValueError("事件类型无效或缺少事件证据")
        if row["link_method"] != "manual_verified" or not row["link_evidence"]:
            raise ValueError("v1 证券关联须人工核验并保留证据")
        if row["availability_basis"] != metadata["news_time_mode"]:
            raise ValueError("新闻可用时间口径与 metadata 不一致")
        available, fetched = timestamp(row["available_at"]), timestamp(row["fetched_at"])
        bound = publication_bound(row)
        if not start <= date.fromisoformat(row["published_at"][:10]) <= end:
            raise ValueError("新闻超出 coverage")
        if available < bound or fetched < bound:
            raise ValueError("新闻可用/抓取时间早于发布精度上界")
        if row["availability_basis"] == "historical_assumption":
            if row["first_seen_at"] or available != bound + timedelta(minutes=5):
                raise ValueError("历史回填须保留未知 first_seen_at，并按发布上界加 5 分钟")
        else:
            if not row["first_seen_at"]:
                raise ValueError("观测模式须提供 first_seen_at")
            observed = timestamp(row["first_seen_at"])
            if available < observed or observed > fetched or available > fetched:
                raise ValueError("观测时间顺序无效")
        if row["updated_at"] and available < timestamp(row["updated_at"]):
            raise ValueError("更新版本不得提前使用")
        if row["content_sha256"] != content_hash(row["title"], row["body"]):
            raise ValueError("新闻内容哈希不匹配")
        canonical_url = row["url"].split("#")[0]
        if canonical_url in seen_urls or row["content_sha256"] in seen_content:
            raise ValueError("重复新闻 URL 或内容；应在规范化前合并关联证券")
        seen_urls.add(canonical_url)
        seen_content.add(row["content_sha256"])
    calendar = read_csv(directory / "calendar.csv", CALENDAR_FIELDS)
    sessions = set()
    for row in calendar:
        fields(row, CALENDAR_FIELDS)
        provenance(row)
        day = date.fromisoformat(row["date"])
        key = row["exchange"], row["date"]
        if row["exchange"] not in {"SH", "SZ"} or day.isoformat() != row["date"] or day.weekday() > 4 or not start <= day <= end or key in sessions:
            raise ValueError("交易日历日期、交易所或唯一性无效")
        sessions.add(key)
    for row in prices:
        if (lookup[row["symbol"]]["exchange"], row["date"]) not in sessions:
            raise ValueError(f"行情日期不在交易日历：{row['date']}")
    for exchange in {s["exchange"] for s in stocks}:
        if not any(item[0] == exchange for item in sessions):
            raise ValueError("交易日历缺少目标交易所")
    return calendar


def quality_report(stocks, prices, news, metadata, calendar=None):
    from .fusion import align_news
    warnings = []
    if not metadata.get("contract_version"):
        warnings.append("旧版 CSV 未验证 v1 来源、单位、快照与交易日历。")
    if metadata.get("news_time_mode") == "historical_assumption":
        warnings.append("历史回填：可用时间为发布精度上界加 5 分钟的假设，未获得历史首次接收和版本记录。")
    if metadata.get("price_basis") == "qfq_snapshot":
        warnings.append("前复权为采集时的固定快照，历史价格可能受后续公司行动重算，不是当时可见价格或总回报。")
    if metadata.get("price_basis") == "unadjusted":
        warnings.append("未复权收益包含除权除息缺口；正式评估前须处理公司行动。")
    if metadata.get("calendar_basis") == "index_observed_sessions":
        warnings.append("日历采用沪深300实际有行情日作为市场开市代理，不能替代交易所官方日历或证明无停牌。")
    if metadata.get("synthetic") is False:
        warnings.append("新闻为人工选择的小样本；无记录表示未收集到，不能解释为市场没有新闻。仅验收管线。")
    summaries = {}
    for stock in stocks:
        rows = [p for p in prices if p["symbol"] == stock["symbol"]]
        related = [n for n in news if stock["symbol"] in n["symbols"]]
        buckets, pending = align_news(rows, related)
        expected = {c["date"] for c in calendar or [] if c["exchange"] == stock.get("exchange")}
        missing = sorted(expected - {p["date"] for p in rows})
        if missing:
            warnings.append(f"{stock['symbol']} 缺少 {len(missing)} 个市场交易日行情，原因待核验；标签按下一有行情日计算。")
        summaries[stock["symbol"]] = dict(price_rows=len(rows), start=rows[0]["date"], end=rows[-1]["date"],
                                         news_rows=len(related), aligned_news=sum(map(len, buckets.values())),
                                         pending_news=len(pending), missing_price_sessions=missing)
    return dict(contract_version=metadata.get("contract_version", "legacy"), status="passed_with_notes" if warnings else "passed",
                stocks=len(stocks), price_rows=len(prices), news_rows=len(news),
                duplicate_prices=0, duplicate_news=0, unknown_symbols=0, invalid_rows=0,
                availability_counts={basis: sum(n.get("availability_basis") == basis for n in news)
                                     for basis in sorted({n.get("availability_basis", "legacy") for n in news})},
                per_stock=summaries, warnings=warnings,
                note="非法记录直接拒绝整批导入；零异常计数表示校验通过，不代表外部来源完整。")
