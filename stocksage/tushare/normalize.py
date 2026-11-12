"""供应商字段到知识记录的转换；未知值保持未知，不推断控制关系。"""
from decimal import Decimal, InvalidOperation
import math

from .models import EXCHANGES, digest, optional_day, symbol


def number(value, *, required=False, scale=1, nonnegative=False):
    if value in (None, ""):
        if required:
            raise ValueError("缺少必需数值")
        return None
    try:
        result = float(Decimal(str(value)) * Decimal(str(scale)))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"非法数值：{value!r}") from None
    if not math.isfinite(result) or nonnegative and result < 0:
        raise ValueError(f"非有限或越界数值：{value!r}")
    return result


def issuer_id(code):
    # A 股上市主体的稳定本地 ID；统一社会信用代码另存，禁止仅凭简称合并主体。
    return f"issuer:{code}"


def record(kind, key, code, result, payload, effective=None, disclosed=None):
    return kind, key, code, effective, disclosed, result, payload


def security(row, result):
    code = symbol(row["ts_code"])
    payload = dict(security_id=f"security:{code}", ts_code=code, symbol=code,
        company_id=issuer_id(code), security_name=row["name"],
        exchange=EXCHANGES[code.split(".")[1]], industry=row.get("industry"),
        industry_scheme="tushare_stock_basic_snapshot", currency=row.get("curr_type") or "CNY",
        list_status=row["list_status"], list_date=optional_day(row.get("list_date")),
        delist_date=optional_day(row.get("delist_date")), source_fields=row)
    return [record("security", code, code, result, payload)]


def company(row, result, security_row):
    code = symbol(row["ts_code"])
    name = row.get("com_name") or security_row.get("fullname") or security_row["name"]
    payload = dict(company_id=issuer_id(code), ts_code=code, name=name,
        unified_social_credit_code=row.get("com_id"), main_business=row.get("main_business"),
        business_scope=row.get("business_scope"), website=row.get("website"), source_fields=row)
    relation = dict(relation_id=digest([code, "issues"]), ts_code=code,
        subject=issuer_id(code), subject_name=name, predicate="issues",
        object=f"security:{code}", object_name=security_row["name"],
        verification="provider_security_issuer_mapping")
    return [record("company", code, code, result, payload), record("relation", relation["relation_id"], code, result, relation)]


def alias(row, result):
    code = symbol(row["ts_code"])
    start, end, announced = (optional_day(row.get(k)) for k in ("start_date", "end_date", "ann_date"))
    payload = dict(ts_code=code, company_id=issuer_id(code), name=row["name"],
                   valid_from=start, valid_to=end, ann_date=announced, change_reason=row.get("change_reason"))
    return [record("alias", digest([code, row["name"], start]), code, result, payload, start, announced)]


def price(row, result):
    code, date = symbol(row["ts_code"]), optional_day(row["trade_date"])
    if date is None:
        raise ValueError("行情缺少交易日期")
    values = {key: number(row.get(key), required=True, nonnegative=True) for key in ("open", "high", "low", "close")}
    if min(values.values()) <= 0 or values["high"] < max(values["open"], values["close"]) or values["low"] > min(values["open"], values["close"]) or values["low"] > values["high"]:
        raise ValueError(f"{code} {date} OHLC 关系无效")
    volume = number(row.get("vol"), required=True, scale=100, nonnegative=True)
    if not volume.is_integer():
        raise ValueError("成交量换算后不是整数股")
    payload = dict(ts_code=code, trade_date=date, **values, volume_shares=int(volume),
        turnover_cny=number(row.get("amount"), required=True, scale=1000, nonnegative=True),
        provider_pre_close=number(row.get("pre_close")), provider_pct_chg=number(row.get("pct_chg")),
        currency="CNY", price_basis="unadjusted", source_fields=row)
    return [record("price", f"{code}/{date}", code, result, payload, date, date)]


def adjustment(row, result):
    code, date = symbol(row["ts_code"]), optional_day(row["trade_date"])
    factor = number(row.get("adj_factor"), required=True)
    if date is None or factor <= 0:
        raise ValueError("复权因子须有日期且大于零")
    return [record("adjustment", f"{code}/{date}", code, result,
                   dict(ts_code=code, trade_date=date, adj_factor=factor), date, date)]


def calendar(row, result, requested_exchange):
    date = optional_day(row["cal_date"])
    if date is None or str(row.get("is_open")) not in {"0", "1"}:
        raise ValueError("交易日历日期或 is_open 无效")
    payload = dict(exchange=requested_exchange, calendar_source_exchange=row.get("exchange"),
                   date=date, is_open=int(row["is_open"]), pretrade_date=optional_day(row.get("pretrade_date")))
    # 日历发布日期未知，不将未来交易日历假定为在历史上已知。
    return [record("calendar", f"{requested_exchange}/{date}", "", result, payload, date)]


def holding(row, result, issuer_name, legal_names):
    code = symbol(row["ts_code"])
    report, announced = optional_day(row["end_date"]), optional_day(row["ann_date"])
    name = str(row["holder_name"]).strip()
    if not name or not report or not announced:
        raise ValueError("股东快照缺少名称、报告期或披露日")
    holder_id = legal_names.get(name, "holder:" + digest(name))
    holding_id = digest([code, report, announced, name])
    ratio = number(row.get("hold_ratio"), scale="0.01", nonnegative=True)
    if ratio is not None and ratio > 1:
        raise ValueError("持股比例超过 100%")
    payload = dict(holding_id=holding_id, ts_code=code, holder_id=holder_id, holder_name=name,
        holder_type=row.get("holder_type"), report_date=report, ann_date=announced,
        hold_shares=number(row.get("hold_amount"), nonnegative=True), hold_ratio=ratio,
        identity_resolution="exact_provider_legal_name" if name in legal_names else "unresolved_holder_name",
        source_fields=row)
    relation = dict(relation_id=holding_id, holding_id=holding_id, ts_code=code,
        subject=holder_id, subject_name=name, predicate="holds_shares", object=issuer_id(code),
        object_name=issuer_name, report_date=report, ann_date=announced,
        hold_ratio=ratio, hold_shares=payload["hold_shares"], holder_type=payload["holder_type"],
        identity_resolution=payload["identity_resolution"], verification="provider_reported_holding_snapshot")
    return [record("shareholding", holding_id, code, result, payload, report, announced),
            record("relation", holding_id, code, result, relation, report, announced)]


def dividend(row, result):
    code = symbol(row["ts_code"])
    dates = {key: optional_day(row.get(key)) for key in ("ann_date", "imp_ann_date", "record_date", "ex_date", "pay_date", "div_listdate", "end_date")}
    action_id = digest([code, dates["end_date"], dates["ann_date"], row.get("div_proc"), dates["ex_date"]])
    disclosed = dates["imp_ann_date"] or dates["ann_date"]
    payload = dict(action_id=action_id, ts_code=code, action_type="dividend_and_bonus",
        status=row.get("div_proc"), **dates, cash_per_share_before_tax=number(row.get("cash_div_tax"), nonnegative=True),
        cash_per_share_after_tax=number(row.get("cash_div"), nonnegative=True),
        bonus_share_ratio=number(row.get("stk_div"), nonnegative=True),
        event_dates=sorted({v for k,v in dates.items() if v and k != "end_date"}), source_fields=row)
    return [record("action", action_id, code, result, payload, dates["ex_date"] or dates["ann_date"], disclosed)]


def suspension(row, result):
    code, date = symbol(row["ts_code"]), optional_day(row["trade_date"])
    if date is None or row.get("suspend_type") not in {"S", "R"}:
        raise ValueError("停复牌日期或类型无效")
    action_id = digest([code, date, row["suspend_type"], row.get("suspend_timing")])
    payload = dict(action_id=action_id, ts_code=code,
        action_type="suspension" if row["suspend_type"] == "S" else "resumption",
        trade_date=date, timing=row.get("suspend_timing"), event_dates=[date], source_fields=row)
    return [record("action", action_id, code, result, payload, date, date)]
