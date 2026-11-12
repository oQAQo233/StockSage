"""采集范围、日期和接口响应契约。"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import re

EXCHANGES = {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}
SHANGHAI = timezone(timedelta(hours=8))


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(json_text(value).encode("utf-8")).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def day(value):
    text = str(value).strip()
    if re.fullmatch(r"\d{8}", text):
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    result = date.fromisoformat(text)
    if result.isoformat() != text:
        raise ValueError("日期须为 YYYY-MM-DD 或 YYYYMMDD")
    return result.isoformat()


def optional_day(value):
    return day(value) if value not in (None, "", "None") else None


def api_day(value):
    return day(value).replace("-", "")


def date_available(value):
    """仅有披露日期时取次日零点，不能假定公告当日开盘前已知。"""
    return datetime.combine(date.fromisoformat(day(value)) + timedelta(days=1), time(), SHANGHAI)


def instant(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("as_of 必须包含时区，例如 2026-11-12T18:00:00+08:00")
    return parsed


def symbol(value):
    text = str(value).strip().upper()
    if not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", text):
        raise ValueError(f"证券代码须显式带交易所后缀：{text!r}，例如 000001.SZ")
    return text


@dataclass(frozen=True)
class StockScope:
    """所有非空筛选条件取交集；行业与名称是采集时的目录快照。"""

    symbols: tuple[str, ...] = ()
    exchanges: tuple[str, ...] = ()
    industries: tuple[str, ...] = ()
    code_start: str | None = None
    code_end: str | None = None
    statuses: tuple[str, ...] = ("L", "D", "P")

    def __post_init__(self):
        object.__setattr__(self, "symbols", tuple(dict.fromkeys(symbol(v) for v in self.symbols)))
        for name in ("exchanges", "statuses"):
            object.__setattr__(self, name, tuple(dict.fromkeys(v.upper() for v in getattr(self, name))))
        object.__setattr__(self, "industries", tuple(self.industries))
        if set(self.exchanges) - set(EXCHANGES.values()):
            raise ValueError("exchange 只支持 SSE、SZSE、BSE")
        if not self.statuses or set(self.statuses) - {"L", "D", "P"}:
            raise ValueError("statuses 只支持 L、D、P，且不能为空")
        for bound in (self.code_start, self.code_end):
            if bound is not None and not re.fullmatch(r"\d{6}", bound):
                raise ValueError("代码范围须为六位字符串，保留前导零")
        if self.code_start and self.code_end and self.code_start > self.code_end:
            raise ValueError("代码范围起点晚于终点")

    def select(self, rows, start, end):
        selected = {}
        for row in rows:
            code = symbol(row["ts_code"])
            exchange = EXCHANGES[code.rsplit(".", 1)[1]]
            number = code.split(".")[0]
            if self.symbols and code not in self.symbols:
                continue
            if self.exchanges and exchange not in self.exchanges:
                continue
            if self.industries and row.get("industry") not in self.industries:
                continue
            if row.get("list_status") not in self.statuses:
                continue
            if self.code_start and number < self.code_start or self.code_end and number > self.code_end:
                continue
            listed, delisted = optional_day(row.get("list_date")), optional_day(row.get("delist_date"))
            if listed and listed > end or delisted and delisted < start:
                continue
            selected[code] = row
        missing = set(self.symbols) - set(selected)
        if missing:
            raise ValueError(f"指定证券不在目录、上市日期区间或筛选范围内：{', '.join(sorted(missing))}")
        if not selected:
            raise ValueError("股票范围为空，请检查代码、行业、交易所和上市日期")
        return [selected[k] for k in sorted(selected)]


@dataclass(frozen=True)
class QueryResult:
    request_id: str
    api_name: str
    params: dict
    rows: list[dict]
    observed_at: str
    raw_ref: str
    sha256: str
