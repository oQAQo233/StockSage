"""按信息可用时间对齐，保留新闻 ID 作为证据。"""
import math
from bisect import bisect_left
from datetime import datetime, time

from .data import SHANGHAI, timestamp

POSITIVE = ("增长", "改善", "盈利提升", "突破", "认证")
NEGATIVE = ("下滑", "疲软", "风险", "延期", "成本上升", "不确定")
FEATURE_NAMES = ["日收益率", "三日收益率", "五日波动率", "成交量比", "新闻情绪", "新闻数量"]


def sentiment(text):
    positive = sum(text.count(word) for word in POSITIVE)
    negative = sum(text.count(word) for word in NEGATIVE)
    return (positive - negative) / max(positive + negative, 1)


def align_news(prices, news):
    cutoffs = [datetime.combine(datetime.fromisoformat(row["date"]).date(), time(15), SHANGHAI) for row in prices]
    buckets = {row["date"]: [] for row in prices}
    pending = []
    for item in news:
        position = bisect_left(cutoffs, timestamp(item["available_at"]))
        enriched = {**item, "sentiment": sentiment(item["title"] + " " + item["body"])}
        if position == len(cutoffs):
            pending.append(enriched)
        else:
            buckets[prices[position]["date"]].append(enriched)
    return buckets, pending


def build_features(prices, buckets):
    rows = []
    returns = [0.0] + [prices[i]["close"] / prices[i - 1]["close"] - 1 for i in range(1, len(prices))]
    for i in range(5, len(prices)):
        recent = returns[i - 4:i + 1]
        average = sum(recent) / len(recent)
        volatility = math.sqrt(sum((value - average) ** 2 for value in recent) / len(recent))
        news = buckets[prices[i]["date"]]
        baseline_volume = sum(row["volume"] for row in prices[i - 5:i]) / 5
        features = [returns[i], prices[i]["close"] / prices[i - 3]["close"] - 1, volatility,
                    prices[i]["volume"] / baseline_volume - 1 if baseline_volume else 0,
                    sum(item["sentiment"] for item in news) / max(len(news), 1), float(len(news))]
        rows.append(dict(date=prices[i]["date"], features=features,
                         target=returns[i + 1] if i + 1 < len(prices) else None,
                         target_date=prices[i + 1]["date"] if i + 1 < len(prices) else None,
                         evidence_ids=[item["id"] for item in news]))
    return rows
