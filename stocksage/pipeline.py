import json
from datetime import datetime, timezone
from pathlib import Path

from .agents import AnalysisAgent, CollectionAgent, ForecastAgent, KnowledgeAgent, ReportAgent
from .contracts import CALENDAR_FIELDS, quality_report
from .data import read_csv, write_csv
from .storage import persist


def run_pipeline(data_dir=None, output_dir=Path("output"), *, knowledge_backend="rule", news_source="dataset",
                 news_limit=20, news_days=7, news_time_mode="observed", news_query=None, news_snapshot=None):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    directory = Path(data_dir) if data_dir is not None else output_dir / "demo_data"
    stocks, prices, news, metadata = CollectionAgent().run(directory, demo=data_dir is None)
    news, knowledge = KnowledgeAgent().run(stocks, news, output_dir, knowledge_backend, news_source,
                                           news_limit, news_days, news_time_mode, news_query, news_snapshot)
    metadata = dict(metadata)
    if news_source != "dataset":
        metadata["input_news_time_mode"] = metadata.get("news_time_mode")
        metadata.update(runtime_news_source=news_source, news_time_mode=news_time_mode,
                        news_completeness="curated_incomplete", hash_scope="base_input_csv_not_runtime_news")
        mode_label = "当前观测正文" if news_time_mode == "observed" else "历史正文回填假设"
        metadata["label"] = metadata["label"].replace("（仅标题）", "").replace("历史新闻回填", mode_label) + f" · 新闻引擎/{news_source}"
    calendar = read_csv(directory / "calendar.csv", CALENDAR_FIELDS) if metadata.get("contract_version") else None
    quality = quality_report(stocks, prices, news, metadata, calendar)
    quality["news_input"] = dict(source=news_source, linked=knowledge["linked_articles"],
                                 unlinked=knowledge["unlinked_articles"], duplicates=knowledge["duplicate_articles"],
                                 failed=len(knowledge["ingestion"]["failures"]),
                                 source_failed=len(knowledge["ingestion"]["source_failures"]))
    quality["warnings"].extend(knowledge["ingestion"]["notes"])
    if knowledge["unlinked_articles"]:
        quality["warnings"].append(f"{knowledge['unlinked_articles']} 条新闻未确定目标证券，未进入任何股票特征、摘要或图谱。")
    if quality["news_input"]["failed"] or quality["news_input"]["source_failed"]:
        quality["warnings"].append("部分新闻采集失败，详情保存在 knowledge.ingestion 中；未使用旧数据静默补齐。")
    if quality["warnings"]:
        quality["status"] = "passed_with_notes"
    reports = {}
    for stock in stocks:
        analysis = AnalysisAgent().run(stock, prices, news)
        forecast = ForecastAgent().run(analysis)
        reports[stock["symbol"]] = {**analysis, "forecast": forecast, "report": ReportAgent().run(analysis, forecast)}
    # 原始采集台账可含末日后信息；股票视图只接收通过 align_news 的新闻及其抽取结果。
    visible_ids = {n["id"] for analysis in reports.values() for n in analysis["news"]}
    knowledge["visible_articles"] = len(visible_ids)
    knowledge["visible_triples"] = sum(len(n.get("triples", [])) for n in news if n["id"] in visible_ids)
    report = dict(generated_at=datetime.now(timezone.utc).isoformat(), dataset=metadata, quality=quality,
                  knowledge=knowledge,
                  stocks=stocks, reports=reports,
                  agents=[dict(name="收集智能体", status="完成", detail=f"校验 {len(prices)} 条行情、{len(news)} 条新闻"),
                          dict(name="新闻知识智能体", status="完成", detail=f"内置新闻引擎/{knowledge_backend}：{len(news)} 篇、{knowledge['triples']} 条抽取关系；股票时点可见 {knowledge['visible_triples']} 条"),
                          dict(name="融合分析智能体", status="完成", detail="按可用时间对齐、提取特征、构建证据图谱"),
                          dict(name="预测智能体", status="完成", detail="时间顺序回测：零收益、纯行情、新闻融合"),
                          dict(name="报告智能体", status="完成", detail="汇总指标与新闻证据，生成规则摘要")],
                  limitations=(["输入为虚构合成数据，生成机制有意包含微弱新闻信号。"] if metadata.get("synthetic") else quality["warnings"]) + [
                               f"知识抽取使用内置新闻引擎/{knowledge_backend}，来源为 {news_source}；报告和编排仍为确定性服务，无自动交易。",
                               "词典情绪与人工字段关联属于教学简化；时间关联不代表因果。",
                               "预测采用当前收盘后可得信息，不包含手续费、滑点或可交易收益检验。"])
    (output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    (output_dir / "quality_report.json").write_text(json.dumps(quality, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    (output_dir / "knowledge_report.json").write_text(json.dumps(knowledge, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    (output_dir / "normalized_news.json").write_text(json.dumps(news, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    (output_dir / "knowledge_graph.json").write_text(json.dumps({symbol: analysis["graph"] for symbol, analysis in reports.items()}, ensure_ascii=False, indent=2), encoding="utf-8")
    aligned = [dict(symbol=symbol, id=item["id"], published_at=item["published_at"], available_at=item["available_at"],
                    effective_date=item["effective_date"], status="aligned")
               for symbol, analysis in reports.items() for item in analysis["news"]]
    aligned += [dict(symbol=symbol, id=item["id"], published_at=item["published_at"], available_at=item["available_at"],
                     effective_date="", status="pending")
                for symbol, analysis in reports.items() for item in analysis["pending_news"]]
    write_csv(output_dir / "aligned_news.csv", ("symbol", "id", "published_at", "available_at", "effective_date", "status"), aligned)
    feature_rows = [dict(symbol=symbol, date=row["date"], target_date=row["target_date"], target=row["target"],
                         evidence_ids="|".join(row["evidence_ids"]), **dict(zip(("return_1d", "return_3d", "volatility_5d", "volume_ratio", "sentiment", "news_count"), row["features"])))
                    for symbol, analysis in reports.items() for row in analysis["features"]]
    write_csv(output_dir / "features.csv", ("symbol", "date", "return_1d", "return_3d", "volatility_5d", "volume_ratio", "sentiment", "news_count", "target_date", "target", "evidence_ids"), feature_rows)
    persist(output_dir / "stocksage.sqlite3", stocks, prices, news, report, calendar)
    return report
