# 续跑说明（2026-11-20 23:48 收工）

## 现在的状态

| 项 | 值 |
|---|---|
| 文章 | 541 篇（唯一稿 536，判重副本 5） |
| 已抽 | **471 篇**（全部 kggen） |
| 待抽 | **70 篇**（唯一稿 65） |
| 三元组 | **10922 条** |
| 图 | **8874 节点 / 10922 边**（已导出） |
| 窗口 | 11-04 ~ 11-11，窗口外 0 篇 |

按源（文章 / 已抽 / 三元组）：`chinanews_day` 348 / 314 / 7515、`sina_day` 147 / 142 / 3037、
`ifanr_day` 37 / 6 / 176、`ndrc_day` 7 / 7 / 119、`most_day` 2 / 2 / 75。

按天（北京时间）：11-04 93、11-05 83、11-06 77、11-07 51、11-08 37、11-09 55、11-10 90、11-11 55。

## 剩下的 65 篇是什么

全是长稿，**2000 字以上 62 篇、1000~2000 字 3 篇**，字数中位 **2617**，最长 8983。

按 `0.09×字数+60` 秒估算，还需要 **6.4 小时**。单篇 4~8 分钟，单路。

## 明天怎么续跑

一口气跑完（自己反复重试，到 deadline 自停）：

```powershell
$env:PYTHONPATH="src;.kgvenv"
$env:LITELLM_LOCAL_MODEL_COST_MAP="true"
$env:NEWSKG_DEADLINE="2026-11-21T09:00:00"
.\.venv\Scripts\python.exe -u .tmp\run_until_done.py
```

只想跑一批（按字数升序，跑 10 篇）：

```powershell
$env:NEWSKG_BUDGET_SEC="600"; $env:NEWSKG_CAP="10"
.\.venv\Scripts\python.exe -u .tmp\run_short_first.py
```

抽完导出（`graph.json` / `graph.cypher` / `graph.graphml`）：

```powershell
.\.venv\Scripts\python.exe -m newskg.cli export --db out/kg.sqlite3 --format all --out out
```

## 今天做了什么

1. 新增第 5 个源 `sina_day`（`src/newskg/sources/sina.py`）：
   `feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2515&num=50&page=N`，
   按 `ctime` 精确归日，8 天 288 条候选，按默认 tech/LLM≥6 入库 147 篇。
   已注册、已推送 GitHub（`ff6965c`）。
2. 删除 `src/newskg/sources/cac.py`（中央网信办源，未采纳）。
3. 13:05~23:45 单路补抽，已抽从 73 推到 471（净增 402 篇，约 36 篇/小时）。
4. 修正文档口径：`REPORT.md` / `HANDOFF.md` 全量刷新到收工数字；
   §5 新增 5.4 节记录新浪 JSON 接口的实测结论。

## 必须记住的坑

- **抽取必须以前台或受管方式运行**，脱离窗口的子进程会卡住不动。
- `LITELLM_LOCAL_MODEL_COST_MAP="true"` 不能少，否则 dspy 去拉 litellm 价格表会卡死。
- 端点只允许**一条** KGGen 流并发，开 2 条就静默返回 0 条结果。
- 抽取后端只能是 kggen，不要换成 llm-direct 赶吞吐。
- 实测速率：250 字≈65 秒、640 字≈95 秒、746 字≈110 秒、1380 字≈110~160 秒、
  2600 字≈4~8 分钟。
- 有 6 篇零三元组，都是中新网 200~260 字的短讯（发射成功之类），属正常。
- `run_until_done.py` 的 deadline 只在**每趟之间**检查，到点那一刻会先把当前这篇抽完；
  要立刻停就 `Stop-Process` 掉 python 进程（未提交的那篇会保持待抽，不会留半条数据）。