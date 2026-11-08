"""确定性角色服务：展示智能体边界；后续可替换为 LLM + 工具调用。"""
from .data import generate_demo, load_dataset, timestamp
from .forecast import evaluate
from .fusion import FEATURE_NAMES, align_news, build_features
from .graph import build_graph
from .knowledge import extract_news, normalize_news, summarize
from .news import collect_news


class CollectionAgent:
    name = "收集智能体"

    def run(self, directory, demo=False):
        if demo:
            generate_demo(directory)
        return load_dataset(directory)


class AnalysisAgent:
    name = "融合分析智能体"

    def run(self, stock, all_prices, all_news):
        prices = [row for row in all_prices if row["symbol"] == stock["symbol"]]
        news = [row for row in all_news if stock["symbol"] in row["symbols"]]
        buckets, pending = align_news(prices, news)
        features = build_features(prices, buckets)
        aligned_news = [{**item, "effective_date": day} for day, items in buckets.items() for item in items]
        # 超过最新收盘的信息不会被用于最新预测、摘要或图谱，单独展示为待对齐。
        observable = sorted(aligned_news, key=lambda item: timestamp(item["available_at"]), reverse=True)
        latest = prices[-1]
        last_features = features[-1]
        triples = [t for item in observable for t in item.get("triples", [])]
        return dict(stock=stock, prices=prices, news=observable, pending_news=pending,
                    features=features, feature_names=FEATURE_NAMES,
                    knowledge=dict(articles=len(observable), triples=triples,
                                   backends=sorted({n.get("extraction", {}).get("backend", "none") for n in observable}),
                                   no_relations=sum(n.get("extraction", {}).get("status") == "no_relations" for n in observable)),
                    graph=build_graph(stock, observable[:12]),
                    summary=dict(latest_close=latest["close"], latest_date=latest["date"],
                                 daily_return=latest["close"] / prices[-2]["close"] - 1,
                                 period_return=latest["close"] / prices[0]["close"] - 1,
                                 news_count=len(observable), pending_count=len(pending),
                                 latest_sentiment=last_features["features"][4],
                                 latest_news_ids=last_features["evidence_ids"]))


class KnowledgeAgent:
    name = "新闻知识智能体"

    def run(self, stocks, news, output_dir, backend="rule", source="dataset", limit=20,
            days=7, time_mode="observed", query=None, snapshot=None):
        collected, ingestion = collect_news(stocks, news, output_dir, source, limit, days, time_mode, query, snapshot)
        normalized, duplicates = normalize_news(collected)
        enriched = extract_news(normalized, backend)
        summary = summarize(enriched, duplicates, backend)
        summary.update(ingestion=ingestion, duplicate_records=duplicates)
        return enriched, summary


class ForecastAgent:
    name = "预测智能体"

    def run(self, analysis):
        return evaluate(analysis["features"])


class ReportAgent:
    name = "报告智能体"

    def run(self, analysis, forecast):
        summary = analysis["summary"]
        direction = "上涨" if forecast["next_return"] > 0 else "下跌" if forecast["next_return"] < 0 else "持平"
        evidence = summary["latest_news_ids"]
        basis = analysis["prices"][-1].get("price_basis", "")
        price_label = {"qfq_snapshot": "前复权快照收盘价", "hfq_snapshot": "后复权快照收盘价"}.get(basis, "收盘价")
        return dict(text=(f"截至 {summary['latest_date']} 收盘，{analysis['stock']['company']}{price_label}为 "
                          f"{summary['latest_close']:.2f}，当日收益率 {summary['daily_return']:+.2%}。"
                          f"当日对齐新闻 {len(evidence)} 条。岭回归预计下一交易日收益率 "
                          f"{forecast['next_return']:+.2%}（{direction}方向），仅用于课程方法演示。"),
                    evidence_ids=evidence, mode="规则生成摘要；未调用大语言模型")
