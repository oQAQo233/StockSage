"""python -m stocksage.tushare collect / context / verify"""
import argparse
import json
from pathlib import Path

from . import MarketKnowledgeBase, StockScope, TushareClient, collect_market_data


def _parts(value):
    return tuple(p.strip() for p in (value or "").split(",") if p.strip())


def main():
    parser = argparse.ArgumentParser(description="Tushare 市场数据采集与本地知识查询（Token 从环境变量/.env 读取）")
    commands = parser.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("collect", help="按范围采集主数据、行情、股东和公司行动")
    collect.add_argument("--symbols", help="带后缀代码，逗号分隔，例如 000001.SZ,600000.SH")
    collect.add_argument("--all", action="store_true", help="显式选择全市场，再应用其他条件")
    collect.add_argument("--exchange", help="SSE,SZSE,BSE，逗号分隔")
    collect.add_argument("--industry", help="Tushare 目录行业名称，逗号分隔，精确匹配")
    collect.add_argument("--code-start", help="六位代码范围下界（含）")
    collect.add_argument("--code-end", help="六位代码范围上界（含）")
    collect.add_argument("--statuses", default="L,D,P", help="上市状态，默认包含上市、退市和暂停")
    collect.add_argument("--start", required=True, help="YYYY-MM-DD 或 YYYYMMDD（含）")
    collect.add_argument("--end", required=True, help="YYYY-MM-DD 或 YYYYMMDD（含）")
    collect.add_argument("--resume", action="store_true", help="复用相同请求的原始缓存，不刷新响应")
    collect.add_argument("--min-interval", type=float, default=1.3, help="请求最小间隔秒数，按账号频次调整")
    collect.add_argument("--no-shareholders", action="store_true")
    collect.add_argument("--no-actions", action="store_true")
    collect.add_argument("--no-adjustments", action="store_true")
    context = commands.add_parser("context", help="输出指定股票的规范数据和证据图谱 JSON")
    context.add_argument("--symbol", required=True)
    context.add_argument("--start")
    context.add_argument("--end")
    context.add_argument("--as-of", help="带时区的知识可用截止时间")
    context.add_argument("--availability", choices=("observed", "disclosed_assumption"), default="observed")
    context.add_argument("--out", type=Path, help="保存 JSON；缺省输出到终端")
    commands.add_parser("verify", help="核对知识库所有原始响应的 SHA256")
    for command in commands.choices.values():
        command.add_argument("--db", type=Path, default=Path("data/knowledge_base/tushare.sqlite3"))
    args = parser.parse_args()
    try:
        if args.command == "collect":
            if not any((args.symbols, args.all, args.exchange, args.industry, args.code_start, args.code_end)):
                raise ValueError("请指定 --symbols、--all、交易所、行业或代码范围")
            if args.symbols and args.all:
                raise ValueError("--symbols 与 --all 不能同时使用")
            scope = StockScope(symbols=_parts(args.symbols), exchanges=_parts(args.exchange),
                industries=_parts(args.industry), code_start=args.code_start, code_end=args.code_end,
                statuses=_parts(args.statuses))
            client = TushareClient(cache_dir=args.db.resolve().parent/"tushare_raw", resume=args.resume,
                                   min_interval=args.min_interval)
            report = collect_market_data(scope=scope, start_date=args.start, end_date=args.end,
                db_path=args.db, client=client, resume=args.resume,
                include_shareholders=not args.no_shareholders, include_actions=not args.no_actions,
                include_adjustments=not args.no_adjustments)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            if report["status"] != "success":
                return 2
        elif args.command == "context":
            if not args.db.is_file():
                raise ValueError("知识库不存在，请先执行 collect")
            result = MarketKnowledgeBase(args.db).stock_context(args.symbol, start_date=args.start,
                end_date=args.end, as_of=args.as_of, availability=args.availability)
            text = json.dumps(result, ensure_ascii=False, indent=2)
            if args.out:
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(text, encoding="utf-8")
                print(f"已保存：{args.out.resolve()}")
            else:
                print(text)
        else:
            if not args.db.is_file():
                raise ValueError("知识库不存在")
            result = MarketKnowledgeBase(args.db).verify_raw()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["status"] == "passed" else 2
    except (ValueError, RuntimeError, OSError) as error:
        parser.exit(2, f"Tushare 模块错误：{error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
