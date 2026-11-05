"""Python 3.10+，仅使用标准库。"""
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
    args = parser.parse_args()
    try:
        report = run_pipeline(args.data_dir, args.output_dir)
    except (ValueError, OSError) as error:
        parser.exit(2, f"数据或文件错误：{error}\n")
    print(f"StockSage | {report['dataset']['label']} | {len(report['stocks'])} 只股票", flush=True)
    print(f"报告与 SQLite 数据库：{args.output_dir.resolve()}", flush=True)
    if not args.no_server:
        serve(report, args.host, args.port)


if __name__ == "__main__":
    main()
