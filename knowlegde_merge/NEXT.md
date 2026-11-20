# 续跑说明（2026-11-20 11:30）

## 现在的状态

| 项 | 值 |
|---|---|
| 文章 | 541 篇（唯一稿 536，判重副本 5） |
| 源 | 4 个：chinanews_day 348、sina_day 147、ifanr_day 37、most_day 2、ndrc_day 7 |
| 已抽 | 81 篇（chinanews 68 / sina 8 / ndrc 5） |
| 待抽 | 455 篇 |
| 三元组 | 1406 条 |
| 窗口 | 11-04 ~ 11-11，窗口外 0 篇 |

## 这一轮做了什么

1. `REPORT.md` §3.1 与 `HANDOFF.md` 里「另有 11-03 的 3 篇」改成正确口径：
   `published_at` 存 UTC，最早 `2026-11-03T23:00:00Z` 折北京时间是 11-04 07:00，
   按北京时间统计窗口外 0 篇。HANDOFF 的坑位表同步划掉。
2. 新增源 `sina_day`（`src/newskg/sources/sina.py`），接口
   `feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2515&num=50&page=N`，
   已注册进 `src/newskg/sources/__init__.py`。
3. 删除 `src/newskg/sources/cac.py` 及注册项（中央网信办源，未获采纳）。
4. 抓取 8 天，288 条候选按默认 tech/LLM≥6 口径筛后入库 147 篇。

## 怎么续跑

```powershell
$env:PYTHONPATH="src;.kgvenv"
$env:LITELLM_LOCAL_MODEL_COST_MAP="true"
$env:NEWSKG_BUDGET_SEC="600"
$env:NEWSKG_CAP="10"
.\.venv\Scripts\python.exe -u .tmp\run_short_first.py
```

单路 kggen，约 40 篇/小时，455 篇约需 **11~12 小时**。每次跑到预算就退出，
重跑即接着抽（按 `word_count ASC` 从短到长）。

## 注意事项

- **抽取必须在前台看着跑**，子进程脱离窗口会卡住不动。
- 环境变量 `LITELLM_LOCAL_MODEL_COST_MAP="true"` 不能少，否则 dspy 会去拉
  litellm 价格表，卡死在 ssl read。
- 端点只允许**一条** KGGen 流并发，并发 2 条就会静默返回 0 结果。
- 抽取后端只能是 kggen，不要换 llm-direct。
- 入库用的是与其他源完全一致的默认口径（`--topic tech`，LLM 打分 ≥6），
  没有对 `sina_day` 做任何门槛调整。