import json
from datetime import datetime, timezone
from pathlib import Path

from .agents import AnalysisAgent, CollectionAgent, ForecastAgent, ReportAgent
from .contracts import CALENDAR_FIELDS, quality_report
from .data import read_csv, write_csv
from .storage import persist


def run_pipeline(data_dir=None, output_dir=Path("output")):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    directory = Path(data_dir) if data_dir is not None else output_dir / "demo_data"
    stocks, prices, news, metadata = CollectionAgent().run(directory, demo=data_dir is None)
    calendar = read_csv(directory / "calendar.csv", CALENDAR_FIELDS) if metadata.get("contract_version") else None
    quality = quality_report(stocks, prices, news, metadata, calendar)
    reports = {}
    for stock in stocks:
        analysis = AnalysisAgent().run(stock, prices, news)
        forecast = ForecastAgent().run(analysis)
        reports[stock["symbol"]] = {**analysis, "forecast": forecast, "report": ReportAgent().run(analysis, forecast)}
    report = dict(generated_at=datetime.now(timezone.utc).isoformat(), dataset=metadata, quality=quality,
                  stocks=stocks, reports=reports,
                  agents=[dict(name="收集智能体", status="完成", detail=f"校验 {len(prices)} 条行情、{len(news)} 条新闻"),
                          dict(name="融合分析智能体", status="完成", detail="按可用时间对齐、提取特征、构建证据图谱"),
                          dict(name="预测智能体", status="完成", detail="时间顺序回测：零收益、纯行情、新闻融合"),
                          dict(name="报告智能体", status="完成", detail="汇总指标与新闻证据，生成规则摘要")],
                  limitations=(["输入为虚构合成数据，生成机制有意包含微弱新闻信号。"] if metadata.get("synthetic") else quality["warnings"]) + [
                               "智能体是可替换的确定性角色服务，当前未接入 LLM、实时采集或自动交易。",
                               "词典情绪与人工字段关联属于教学简化；时间关联不代表因果。",
                               "预测采用当前收盘后可得信息，不包含手续费、滑点或可交易收益检验。"])
    (output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    (output_dir / "quality_report.json").write_text(json.dumps(quality, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
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
