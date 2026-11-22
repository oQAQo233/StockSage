# 交付状态（2026-11-21 14:43）

## 已完成

| 项 | 值 |
|---|---|
| 文章 | 541 篇（唯一稿 536，判重副本 5） |
| 抽取 | **536 / 536 全部完成**（全部 kggen），待抽 **0** |
| 三元组 | **13346 条** |
| 图 | **10753 节点 / 13346 边**，已导出 |
| 窗口 | 11-04 ~ 11-11，窗口外 0 篇 |

按源（文章 / 已抽 / 三元组）：`chinanews_day` 348 / 343 / 8662、`sina_day` 147 / 147 / 3216、
`ifanr_day` 37 / 37 / 1274、`ndrc_day` 7 / 7 / 119、`most_day` 2 / 2 / 75。

按天（北京时间）：11-04 93、11-05 83、11-06 77、11-07 51、11-08 37、11-09 55、11-10 90、11-11 55。

## 产物

| 文件 | 内容 |
|---|---|
| `out/kg.sqlite3` | 全量库（文章、正文、三元组、抽取台账） |
| `out/graph.json` | 图（10753 节点 / 13346 边），2.71 MB |
| `out/graph.cypher` | Neo4j 导入脚本，2.21 MB |
| `out/graph.graphml` | Gephi / yEd 可读，2.88 MB |
| `REPORT.md` | 阶段交付报告（数字已按本次收工刷新） |
| `.tmp/final_summary.txt` | 收尾器自动写的最终统计 |

## 唯一没达成的目标：源 5/10

还差 5 个。已验证可走的路子只剩两条：

1. **门户 JSON 滚动接口**：新浪 `feed.mix.sina.com.cn/api/roll/get?lid=2515` 已跑通，
   同类可试网易 / 腾讯 / 搜狐的 roll 接口；
2. **部委老式服务端分页**：省级科技厅、行业协会、研究院所。

已验证的死路（不要再试）：36氪（网关 404）、虎嗅（阿里云 WAF）、机器之心（SPA 空壳）、
钛媒体（Nuxt 只吐 767 字符）、凤凰科技（翻页返回同一页）、量子位（WordPress 翻页失效）、
界面/网易/环球网（列表无日期）、观察者网（翻页同一页）、IT之家/快科技（只保留最新屏）、
RSS（只能吐最近 20~40 条，回不到 8 天前）。

## 复现命令

```powershell
# 抓取（某一天）
$env:PYTHONPATH="src"; $env:NEWSKG_TOPIC_MAX_SCORED="600"
.\.venv\Scripts\python.exe -m newskg.cli fetch --source sina_day --day 2026-11-04 --limit 500 --days 20

# 抽取（跑完自动停）
$env:PYTHONPATH="src;.kgvenv"; $env:LITELLM_LOCAL_MODEL_COST_MAP="true"
$env:NEWSKG_DEADLINE="2026-11-21T17:30:00"
.\.venv\Scripts\python.exe -u .tmp\run_until_done.py

# 导出
.\.venv\Scripts\python.exe -m newskg.cli export --db out/kg.sqlite3 --format all --out out
```

## 必须记住的坑

- `LITELLM_LOCAL_MODEL_COST_MAP="true"` 不能少，否则 dspy 去拉 litellm 价格表会卡死在 ssl read。
- 端点只允许**一条** KGGen 流并发，开 2 条就静默返回 0 条结果。
- 抽取后端只能是 kggen，不要换成 llm-direct 赶吞吐。
- 长早报（4000~6500 字）偶发整篇失败：模型把内容写进 `reasoning_content`、`text` 为空。
  处理：删掉该篇的 `runs` 记录重抽（11-21 那 3 篇重抽后出了 11/40/29 条）。
- 有 6 篇零三元组是中新网 200~260 字发射短讯，本来就抽不出关系，不是失败。
- 镜像仓 `.tmp/StockSage` 推送时**用仓库自带的代理**（127.0.0.1:7897），不要清空代理；
  直连 github 反而会超时。