"""按股票范围与日期采集，独立任务记录成功、空结果和失败。"""
from dataclasses import asdict
from datetime import date, timedelta

from .client import TushareClient
from .models import EXCHANGES, StockScope, api_day, day, json_text, now, optional_day
from . import normalize as norm
from .store import MarketKnowledgeBase


def _quarters(start, end):
    for year in range(date.fromisoformat(start).year, date.fromisoformat(end).year + 1):
        for suffix in ("03-31", "06-30", "09-30", "12-31"):
            value = f"{year:04d}-{suffix}"
            if start <= value <= end:
                yield value


def collect_market_data(*, scope: StockScope, start_date: str, end_date: str,
                        db_path="data/knowledge_base/tushare.sqlite3", client=None,
                        resume=False, include_shareholders=True, include_actions=True,
                        include_adjustments=True, strict=False):
    """返回含 run_id/status/tasks 的报告；partial 永远不会标记为成功。

    client 可注入 TushareClient 或兼容 pages() 的适配器。resume 仅复用原始响应，
    不更新其首次观测时间；需要刷新数据时勿使用 resume。
    """
    if not isinstance(scope, StockScope):
        raise TypeError("scope 须为 StockScope")
    start, end = day(start_date), day(end_date)
    if start > end:
        raise ValueError("开始日期不能晚于结束日期")
    store = MarketKnowledgeBase(db_path)
    client = client or TushareClient(cache_dir=store.path.parent/"tushare_raw", resume=resume)
    config = dict(scope=asdict(scope), start_date=start, end_date=end,
                  include_shareholders=include_shareholders, include_actions=include_actions,
                  include_adjustments=include_adjustments, resume=resume)
    run_id = store.start_run(config)
    tasks, warnings, selected = [], [], []
    summary = dict(run_id=run_id, status="running", database=str(store.path), config=config,
                   selected_symbols=[], tasks=tasks, warnings=warnings,
                   limitations=["主数据和行业筛选基于采集时目录，不代表历史完整股票池。",
                     "公司关系仅含发行主体映射和前十大股东快照；不含自动推断的控制、子公司、供应链关系。",
                     "公司行动首版覆盖分红送转与停复牌；配股、增发、回购等不在本次完整性范围内。",
                     "股东报告期向前补取366日，用于区间起点上下文；不保证每家公司都有先前披露。"])

    def task(api, params, handler=None, *, label=None):
        key = label or f"{api}:{json_text(params)}"
        received, written, found = 0, 0, []
        try:
            for result in client.pages(api, params):
                store.add_request(run_id, result)
                received += len(result.rows)
                found.extend((row, result) for row in result.rows)
            if handler:
                records = [record for row, result in found for record in handler(row, result)]
                # 股东快照的批次覆盖全部分页；同一报告期修订时不能混入上批已消失的股东。
                batch = found[0][1].request_id if found else None
                unique = {}
                for record in records:
                    if record[0] == "shareholding" or record[0] == "relation" and record[-1].get("predicate") == "holds_shares":
                        record[-1]["snapshot_batch"] = batch
                    identity = record[:2]
                    if identity in unique and unique[identity] != record[-1]:
                        raise ValueError("同一查询包含自然键冲突，拒绝任意选择版本")
                    unique[identity] = record[-1]
                written = store.add_records(records)
            status, error = ("success" if received else "empty"), None
        except Exception as exc:
            status = "failed"
            error = client.safe_message(exc) if hasattr(client, "safe_message") else str(exc)[:400]
            found = []
        store.task(run_id, key, status, received, written, error)
        tasks.append(dict(task=key, api=api, status=status, rows_received=received, records_written=written, error=error))
        return found

    try:
        # 包含退市/暂停记录，并按上市区间过滤，避免只取当前上市证券。
        directory = []
        exchanges = scope.exchanges or tuple(sorted({EXCHANGES[c.split(".")[1]] for c in scope.symbols})) or ("SSE", "SZSE", "BSE")
        for exchange in exchanges:
            for status in scope.statuses:
                directory.extend(task("stock_basic", dict(exchange=exchange, list_status=status)))
        if any(t["status"] == "failed" for t in tasks):
            raise ValueError("证券目录获取不完整，停止股票筛选；已保存失败任务")
        rows = scope.select([row for row, _ in directory], start, end)
        selected = {r["ts_code"]: r for r in rows}
        summary["selected_symbols"] = list(selected)
        origins = {row["ts_code"]: result for row, result in directory if row["ts_code"] in selected}
        store.add_records([r for code,row in selected.items() for r in norm.security(row, origins[code])])

        company_rows = {}
        for code, row in selected.items():
            result = task("stock_company", dict(ts_code=code),
                lambda r,q,c=code,s=row: norm.company(r,q,s) if r.get("ts_code") == c else [])
            match = [r for r,_ in result if r.get("ts_code") == code]
            if match:
                company_rows[code] = match[0]
            else:
                warnings.append(f"{code} 未取得公司资料；没有构造虚假的公司关系")
        name_candidates = {}
        for code, row in company_rows.items():
            name = row.get("com_name")
            if name:
                name_candidates.setdefault(name, []).append(norm.issuer_id(code))
        legal_names = {name: ids[0] for name,ids in name_candidates.items() if len(ids) == 1}

        for exchange in sorted({EXCHANGES[c.split(".")[1]] for c in selected}):
            # 官方 trade_cal 文档未列 BSE 请求枚举，明确用 SSE 日历作北交所参考。
            source_exchange = "SSE" if exchange == "BSE" else exchange
            if exchange == "BSE":
                warnings.append("BSE 使用 SSE 参考交易日历，记录 calendar_source_exchange=SSE")
            task("trade_cal", dict(exchange=source_exchange, start_date=api_day(start), end_date=api_day(end)),
                 lambda r,q,e=exchange: norm.calendar(r,q,e) if start <= day(r["cal_date"]) <= end else [],
                 label=f"trade_cal:{exchange}:{start}:{end}")

        for code, master in selected.items():
            def for_code(row):
                if row.get("ts_code") != code:
                    raise ValueError(f"接口返回范围外证券：{row.get('ts_code')}")

            def daily_handler(row, result):
                for_code(row)
                if start <= day(row["trade_date"]) <= end:
                    return norm.price(row,result)
                return []

            task("daily", dict(ts_code=code, start_date=api_day(start), end_date=api_day(end)), daily_handler)
            if include_adjustments:
                def factor_handler(row, result):
                    for_code(row)
                    return norm.adjustment(row,result) if start <= day(row["trade_date"]) <= end else []
                task("adj_factor", dict(ts_code=code, start_date=api_day(start), end_date=api_day(end)), factor_handler)

            def alias_handler(row, result):
                for_code(row)
                lower, upper, ann = (optional_day(row.get(k)) for k in ("start_date", "end_date", "ann_date"))
                if lower and lower > end or upper and upper < start or ann and ann > end:
                    return []
                return norm.alias(row,result)
            # 不按公告日筛选接口，否则会漏掉区间起点前已生效的曾用名。
            task("namechange", dict(ts_code=code), alias_handler)
            if include_shareholders:
                def holding_handler(row, result):
                    for_code(row)
                    if day(row["ann_date"]) > end or day(row["end_date"]) > end:
                        return []
                    issuer = company_rows.get(code, {}).get("com_name") or master.get("fullname") or master["name"]
                    return norm.holding(row,result,issuer,legal_names)
                lookback = (date.fromisoformat(start) - timedelta(days=366)).isoformat()
                # 按报告期查询，避免单次多年范围碰到上游未明确声明的返回条数上限。
                for quarter in _quarters(lookback, end):
                    task("top10_holders", dict(ts_code=code, period=api_day(quarter)), holding_handler)

            if include_actions:
                def dividend_handler(row, result):
                    for_code(row)
                    records = norm.dividend(row,result)
                    payload = records[0][-1]
                    # 全历史查询后按事件日期筛选，保留区间前公告、区间内除息的事项。
                    if any(start <= d <= end for d in payload["event_dates"]) and (not payload["ann_date"] or payload["ann_date"] <= end):
                        return records
                    return []
                task("dividend", dict(ts_code=code), dividend_handler)

                def suspension_handler(row, result):
                    for_code(row)
                    return norm.suspension(row,result) if start <= day(row["trade_date"]) <= end else []
                task("suspend_d", dict(ts_code=code, start_date=api_day(start), end_date=api_day(end)), suspension_handler)
        calendars = store.records("calendar", run_id=run_id)
        coverage = {}
        requested_days = {(date.fromisoformat(start) + timedelta(days=i)).isoformat()
                          for i in range((date.fromisoformat(end) - date.fromisoformat(start)).days + 1)}
        for code, master in selected.items():
            exchange = EXCHANGES[code.split(".")[1]]
            calendar_missing = sorted(requested_days - {r["date"] for r in calendars if r["exchange"] == exchange})
            listed, delisted = optional_day(master.get("list_date")), optional_day(master.get("delist_date"))
            expected = {r["date"] for r in calendars if r["exchange"] == exchange and r["is_open"]
                        and (not listed or r["date"] >= listed) and (not delisted or r["date"] <= delisted)}
            prices = {r["trade_date"] for r in store.records("price", ts_code=code, run_id=run_id)}
            factors = {r["trade_date"] for r in store.records("adjustment", ts_code=code, run_id=run_id)}
            halted = {r["trade_date"] for r in store.records("action", ts_code=code, run_id=run_id)
                      if r["action_type"] == "suspension" and not r.get("timing")}
            missing = sorted(expected - prices - halted)
            missing_factors = sorted(prices - factors) if include_adjustments else []
            coverage[code] = dict(expected_sessions=len(expected), price_sessions=len(prices),
                                  missing_price_dates=missing, missing_factor_dates=missing_factors,
                                  missing_calendar_dates=calendar_missing)
            if calendar_missing:
                warnings.append(f"{code} 的参考日历缺少 {len(calendar_missing)} 天，无法确认行情覆盖完整性")
            if missing:
                warnings.append(f"{code} 有 {len(missing)} 个开市日缺少行情且无全天停牌记录，详见 coverage")
            if missing_factors:
                warnings.append(f"{code} 有 {len(missing_factors)} 个行情日缺少复权因子，详见 coverage")
        summary["coverage"] = coverage
        failed = [t for t in tasks if t["status"] == "failed"]
        summary["status"] = "partial" if failed or warnings else "success"
    except Exception as error:
        summary.update(status="failed", error=client.safe_message(error) if hasattr(client,"safe_message") else str(error)[:400])
    summary["completed_at"] = now()
    summary["counts"] = store.run_counts(run_id)
    store.finish_run(run_id, summary)
    # run_id 独立的报告，不覆盖上一次运行的完整性证据。
    report_dir = store.path.parent / "tushare_runs"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{run_id}.json"
    report_path.write_text(json_text(summary), encoding="utf-8")
    if strict and summary["status"] != "success":
        raise RuntimeError(f"Tushare 采集 {summary['status']}，详情：{report_path}")
    return summary
