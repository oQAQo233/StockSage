# Tushare 市场数据采集与本地知识库

本模块位于 [`stocksage/tushare/`](../stocksage/tushare/)，是主项目内的可导入包，不依赖原新闻子项目，也不会在导入或启动离线 demo 时联网。使用 Python 标准库直接调用 Tushare Pro HTTP 接口，不必安装 SDK 或 pandas。

## 采集范围与配置

在项目根目录的 `.env` 或环境变量中设置 `TUSHARE_TOKEN`，已有环境变量优先。不要把真实 Token 提交到仓库。接口权限由账号决定，模块不会申请权限或购买服务。

```dotenv
TUSHARE_TOKEN=替换为自己的Token
```

```powershell
# 指定两只股票，开始与结束日期均包含在范围内
.venv/Scripts/python.exe -m stocksage.tushare collect --symbols 000001.SZ,600000.SH --start 2026-01-01 --end 2026-10-31

# 指定交易所、行业，以及六位代码区间；这些筛选条件取交集
.venv/Scripts/python.exe -m stocksage.tushare collect --exchange SZSE --industry 银行 --code-start 000001 --code-end 000999 --start 2026-01-01 --end 2026-10-31

# 显式全市场；可按账号权限和频次调整最小请求间隔，默认 1.3 秒
.venv/Scripts/python.exe -m stocksage.tushare collect --all --start 2026-10-01 --end 2026-10-31 --min-interval 1.3

# 失败后复用成功响应，继续请求原来未完成的部分
.venv/Scripts/python.exe -m stocksage.tushare collect --symbols 000001.SZ,600000.SH --start 2026-01-01 --end 2026-10-31 --resume
```

默认状态为 L、D、P，随后按上市/退市日期与请求区间是否相交筛选；`--statuses L` 可以只取当前上市。行业是 Tushare 目录中的精确名称。行业、公司信息和证券名称均为采集时快照，不能据此声称重建了历史完整股票池。沪深京均支持显式后缀 SH/SZ/BJ，不根据不完整代码猜交易所。北交所日历以 SSE 为参考，报告中会提示该替代。

默认数据库为 `data/knowledge_base/tushare.sqlite3`，可用 `--db` 指定。原始响应位于数据库同级的 `tushare_raw/`，每次运行报告位于 `tushare_runs/<run_id>.json`。它们都是运行数据，不进入 Git。备份时应一起复制数据库和原始响应；原始响应索引记录绝对路径，换目录后需同步调整 `kb_requests.raw_ref`。

`--resume` 只复用完全相同请求的缓存，不刷新数据，也不改写首次观测时间。需要获取供应商最新修订或新数据时不要使用该参数；正常重采会追加新观测版本。

## Python 调用

```python
from stocksage.tushare import StockScope, collect_market_data, MarketKnowledgeBase

report = collect_market_data(
    scope=StockScope(symbols=("000001.SZ", "600000.SH")),
    start_date="2026-01-01",
    end_date="2026-10-31",
    db_path="data/knowledge_base/tushare.sqlite3",
    strict=True,
)

kb = MarketKnowledgeBase(report["database"])
context = kb.stock_context(
    "000001.SZ", start_date="2026-01-01", end_date="2026-10-31"
)
prices = context["prices"]
relations = context["graph"]["edges"]
```

`StockScope` 还接受 `exchanges`、`industries`、`code_start`、`code_end`、`statuses`。未指定代码时按照其他条件选取范围，全部留空表示沪深京目录。公司发行主体使用稳定的本地 `issuer:<ts_code>` ID，统一社会信用代码另存；不声称已完成跨市场、多证券类别的统一法人解析。

`collect_market_data` 可以注入 `client`，供测试或其他传输适配器复用；可通过 `include_shareholders=False`、`include_actions=False`、`include_adjustments=False` 显式关闭相关类别。命令行对应 `--no-shareholders`、`--no-actions`、`--no-adjustments`。关闭类别是调用者声明的范围，不代表已经采到该类别。

返回状态包括 `success`、`partial`、`failed`，任务状态另外区分 `success`、`empty`、`failed`。权限错误、分页异常和数值校验失败都保存在任务报告中。默认返回报告；`strict=True` 在非成功状态时抛异常，原始响应和已经成功的任务仍保留。命令行遇到 partial/failed 返回退出码 2。成功只表示本次启用任务执行通过，不保证供应商历史数据或公司关系完整。

## 数据内容与字段口径

| 类别 | 接口 | 规范结果 |
|---|---|---|
| 证券目录 | `stock_basic` | 证券、发行主体 ID、行业快照、上市状态和日期 |
| 公司资料 | `stock_company` | 公司全称、统一社会信用代码、主营业务、网站等 |
| 曾用名 | `namechange` | 名称、有效起止日、公告日、变更原因 |
| 日线 | `daily` | OHLC、股数、人民币成交额；保留供应商涨跌幅和原字段 |
| 复权因子 | `adj_factor` | 单独保存因子，不覆盖未复权价格 |
| 日历 | `trade_cal` | 开休市、前交易日、使用的日历交易所 |
| 股东关系 | `top10_holders` | 报告期股东快照、持股比例、披露日期和原始证据 |
| 分红送转 | `dividend` | 预案/实施状态、每股现金、送转比例、登记/除息/派息日期 |
| 停复牌 | `suspend_d` | 停复牌类型、日期和日内时间段 |

- `daily.vol` 的“手”乘 100 转为 `volume_shares`；`daily.amount` 的“千元”乘 1000 转为 `turnover_cny`。供应商 `pct_chg` 保持百分数，并命名为 `provider_pct_chg`，不能当作小数收益率。
- `price_basis=unadjusted`。复权因子独立查询，不暗中切换价格口径；默认不生成任何“已正确处理公司行动”的收益率结论。
- `hold_ratio` 从百分数转换为 0—1 小数。税前/税后现金分红分别保存，缺失值不填零。
- 分红按公告、实施、登记、除息、派息日期是否与区间相交筛选，因此区间之前公告、区间内除息的事项不会被漏掉。分红所属年度不是事件发生日期。
- 名称获取历史记录再按有效期相交筛选，避免漏掉早于区间起点生效的名称。
- 股东报告期从区间起点向前补取 366 日，按季度查询，作为起点上下文；剔除晚于请求结束日披露的记录。报告期与披露日期分开保存，不把报告期末当作市场已经知道该信息的日期。同一报告期修订后按最新采集批次取股东，避免旧股东残留。
- 报告的 `coverage` 核对交易日历、上市区间、行情与全天停牌记录；无法解释的行情缺口、行情日缺少因子会使运行标记为 partial。关闭停复牌采集时，无法解释的停牌缺口也会保留提示。

关系仅生成 `issues` 和 `holds_shares`。股东名称仅在本次已取得的公司法定全称中唯一精确匹配时关联发行主体；否则使用未消歧的 holder ID，保留股东类型，不把个人、基金产品等直接当作公司。持股比例本身不会自动产生 `controls` 关系。

目前不包含配股、增发、回购、供应链、客户及子公司事实的完整采集；这些需要增加专门接口或公告证据。前十大股东也不是完整股东名册。

## 知识查询与历史边界

```powershell
.venv/Scripts/python.exe -m stocksage.tushare context --symbol 000001.SZ --start 2026-01-01 --end 2026-10-31 --out output/000001_context.json

.venv/Scripts/python.exe -m stocksage.tushare verify
```

`stock_context()` 返回公司、证券、名称、日线、复权因子、公司行动、最近股东快照及 `nodes/edges`。关系携带 `_provenance`，可通过 `request_id` 找到原始响应及 SHA256。股东视图取截至截止时间可用的最近报告期和对应披露批次，避免把不同报告期的旧股东累加成当前股东。

默认 `availability="observed"`：只有 `observed_at <= as_of` 的数据可见。这意味着今天才首次采到的历史数据不会自动成为过去已知信息。

显式 `availability="disclosed_assumption"` 可以按披露日期的次日零点进行回顾；日线与因子按交易日期的次日零点。无历史披露日期的公司资料、发行主体关系和行业快照仍按真实观测时间过滤。此模式不能证明真实历史首次可得，也不能消除供应商修订造成的未来信息问题。

```python
historical = kb.stock_context(
    "000001.SZ",
    as_of="2026-07-01T18:00:00+08:00",
    availability="disclosed_assumption",
)
```

知识库采用追加观测记录，按自然键查询最新版本；相同缓存重放不会重复写入。它与现有 `storage.persist()` 的报告数据库分开，避免演示流程清表时删除市场知识。

### 存储表

- `kb_runs`：请求范围、启用类别、起止时间、状态与报告。
- `kb_requests` / `kb_run_requests`：原始响应、哈希及各次运行的证据引用。
- `kb_tasks`：每个接口查询的状态、行数、规范记录数与错误。
- `kb_records`：`kind`、自然键、证券、业务日期、披露日期、观测时间、请求外键、规范 JSON。kind 为 security/company/alias/calendar/price/adjustment/shareholding/action/relation。

不会把这些记录强行导出成现有 v1 新闻 CSV 契约；本模块没有采集新闻，不能伪造新闻或为符合演示导入要求补造数据。后续新闻融合服务直接通过代码 ID、公司 ID 和查询接口读取上下文。

## 验证与官方参考

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -p test_tushare.py -v
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

测试使用虚构响应，覆盖范围筛选、单位转换、分页、缓存、部分权限失败、原始证据完整性、股东快照、时点隔离和异常任务原子性。真实账号的接口权限、限频和供应商可用性需要配置 Token 后验证。

官方文档核对日期：2026-11-12。

- [HTTP 协议](https://tushare.pro/document/1?doc_id=130)
- [证券目录](https://tushare.pro/document/2?doc_id=25)、[公司资料](https://tushare.pro/document/2?doc_id=112)、[曾用名](https://tushare.pro/document/2?doc_id=100)
- [日线](https://tushare.pro/document/2?doc_id=27)、[复权因子](https://tushare.pro/document/2?doc_id=28)、[交易日历](https://tushare.pro/document/2?doc_id=26)
- [前十大股东](https://tushare.pro/document/2?doc_id=61)、[分红送股](https://tushare.pro/document/2?doc_id=103)、[停复牌](https://tushare.pro/document/2?doc_id=214)
