"""默认规则抽取仅需标准库；联网正文和 LLM 后端按需安装可选依赖。"""
import argparse
from pathlib import Path

from stocksage.pipeline import run_pipeline
from stocksage.server import serve


def main():
    parser = argparse.ArgumentParser(description="StockSage 离线课程演示")
    parser.add_argument("--data-dir", type=Path, help="包含 stocks.csv、prices.csv、news.csv 的目录")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).parent / "output")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-server", action="store_true", help="仅执行分析并导出报告")
    parser.add_argument("--knowledge-backend", choices=("rule", "llm", "kggen", "none"), default="rule")
    parser.add_argument("--news-source", default="dataset", help="dataset 保留 CSV；urls 抓取原链接；corpus 重放内置冻结语料；snapshot 重放正文快照；或内置来源名称")
    parser.add_argument("--news-snapshot", type=Path, help="snapshot 模式的采集 manifest.json 路径")
    parser.add_argument("--news-limit", type=int, default=20)
    parser.add_argument("--news-days", type=int, default=7)
    parser.add_argument("--news-query", help="发现层标题查询；corpus 为正文检索")
    parser.add_argument("--news-time-mode", choices=("observed", "historical_assumption"), default="observed")
    args = parser.parse_args()
    try:
        report = run_pipeline(args.data_dir, args.output_dir, knowledge_backend=args.knowledge_backend,
                              news_source=args.news_source, news_limit=args.news_limit, news_days=args.news_days,
                              news_time_mode=args.news_time_mode, news_query=args.news_query, news_snapshot=args.news_snapshot)
    except (ValueError, OSError) as error:
        parser.exit(2, f"数据或文件错误：{error}\n")
    print(f"StockSage | {report['dataset']['label']} | {len(report['stocks'])} 只股票", flush=True)
    print(f"报告与 SQLite 数据库：{args.output_dir.resolve()}", flush=True)
    print(f"内置新闻引擎/{args.knowledge_backend}：{report['knowledge']['articles']} 篇、{report['knowledge']['triples']} 条关系；股票时点可见 {report['knowledge']['visible_triples']} 条", flush=True)
    if not args.no_server:
        serve(report, args.host, args.port)


if __name__ == "__main__":
    main()
