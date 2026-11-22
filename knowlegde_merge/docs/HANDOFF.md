# newskg 交接文档

> 写于 2026-11-19 16:00（Asia/Shanghai）。上一轮会话太长、上下文被压缩，这份文档是唯一可靠的事实来源。
> 新会话请**先读完第 4 节「必须先读的 10 条经验」再动手**，那一节能省掉 5 个小时的重复踩坑。

---

## 1. 阶段目标（用户原话）

> 抽取 **10 个源**，从 **11 月 4 号开始往后 8 天**（即 2026-11-04 ~ 2026-11-11）的新闻，然后一直做到**转换成知识图谱**那一步。

拆成可验收的四件事：

| # | 交付 | 验收标准 |
|---|---|---|
| 1 | 10 个新闻源，每源都能按日期回溯 | 每个源 `--day 2026-11-05` 能独立跑出 ≥10 条带时间的文章 |
| 2 | 8 天 × 10 源的语料 | 库内 11-04 ~ 11-11 每天都有数据，按源统计无空洞 |
| 3 | 全部文章抽成三元组 | `runs` 台账里待抽数为 0，失败数为 0 |
| 4 | 知识图谱产物 | `export` 出 json/cypher/graphml，`stats` + 报告可读，实体跨文章可合并 |

---

## 2. 现状快照（2026-11-21 14:43 全量完成实测）

> **抽取已完成**：541 篇里 536 篇唯一稿全部抽完（全部 KGGen），待抽 0。
> 端点只允许单路 KGGen，单篇 60~870 秒，全量累计约 21 小时（分两天跑完）。

数据库：`out/kg.sqlite3`

| 指标 | 数值 |
|---|---|
| 文章 | **541 篇**（判重丢弃 5 篇，唯一稿 536；语料抓取已完成） |
| 已抽取 | **536 篇**（全部 kggen） |
| 待抽 | **0 篇** |
| 三元组 | **13346 条**（多源支持 57 条） |
| 图规模 | **10753 节点 / 13346 边** |
| 已接的**按天归档**源 | **5 个**：`chinanews_day`、`sina_day`、`ifanr_day`、`most_day`、`ndrc_day`（3 个电子报源已按要求删除） |
| 覆盖日期（北京时间，含副本） | 11-04: 93、11-05: 83、11-06: 77、11-07: 51、11-08: 37、11-09: 55、11-10: 90、11-11: 55；窗口外 0 篇 |
| **缺** | 源 5/10（还差 5 个，原因与实测证据见 §8.5、§9.2、§9.6）；抽取已无缺口 |

### 按源分布（实测）

| 源 | 文章 | 已抽 | 三元组 |
|---|---|---|---|
| `chinanews_day`（中新网日页） | 348 | 343 | 8662 |
| `sina_day`（新浪科技产经滚动，11-20 新增） | 147 | 147 | 3216 |
| `ifanr_day`（爱范儿按日） | 37 | 37 | 1274 |
| `ndrc_day`（国家发改委新闻发布） | 7 | 7 | 119 |
| `most_day`（科技部工作动态） | 2 | 2 | 75 |
| ~~`cnstock_day`~~（上海证券报电子报） | 已删除 | — | — |
| ~~`ce_day`~~（经济日报电子报） | 已删除 | — | — |
| ~~`zqrb_day`~~（证券日报电子报） | 已删除 | — | — |

> 三个电子报源已于 2026-11-20 按要求整体删除（财经报，科技题材太少）；部委两源合计 9 篇，不是抓取失败：
> 电子报是**财经报**（每天科技题材 0~3 篇）；部委频道**发布频率低**（每天 0~4 篇）。
> 作为「源」它们成立，作为「语料量」贡献有限，报告里要照实说明。
> 新浪源 8 天 288 条候选，按 LLM ≥6 口径入库 147 篇，实现见 `src/newskg/sources/sina.py`。

### 抽取器现状

| 后端 | 篇数 | 平均三元组/篇 | 单篇耗时 | 说明 |
|---|---|---|---|---|
| `kggen`（KGGen） | **536** | **24.9** | 60~870 秒 | 质量最好，自带实体聚类；当前唯一在用的后端 |
| `llm-direct`（本项目自写） | 0（已全部清除） | ~9.0 | 11~14 秒 | 曾用于赶吞吐，已按用户要求废弃，数据备份在 `out/kg.sqlite3.bak-20261119-221740` |

---

## 2.5 这个会话做过什么（时间线概述）

按发生顺序，一句话一件事，方便快速定位「哪些已经试过」：

1. **换窗口**：语料窗口从「11-04 起 15 天」缩到「11-04 起 7 天」，只留科技题材。
2. **接第二个源**：先验证中新网日页效果，再新增爱范儿 `/date/YYYY/MM/DD` 适配器 `IfanrDate`。
3. **修正文闸**：爱范儿一篇 1539 字的文章被判「页脚占 100%」拒收 —— 因为正文被抽成一行且含「电话：」。改成只数尾部 20% 的短行。
4. **发现打分上限陷阱**：`MAX_SCORED=60` 时 11-04 只留 27 篇，调到 600 后是 59 篇。全部 7 天重抓了一遍。
5. **抓完 7 天**：中新网 321 篇 + 爱范儿 36 篇 = 357 篇。
6. **抽取反复失败**（本会话最耗时的部分，约 5 小时）：
   - `--workers 6` 卡死 → 查 dspy 磁盘缓存锁 → 关缓存，短批通过、长批仍卡；
   - 试图多进程分片（4/6 路）→ 全部卡死；
   - 单进程有时 93 秒成功、有时 20 分钟不动；
   - **最终定位：端点限流**。加 `PACE=5` 后 15 篇 366 秒、24 篇 601 秒、24 篇 562 秒，0 失败。
7. **新增直连后端**：KGGen 一篇两发大请求太容易被限流打死，写 `kg/direct.py` 改成一次小请求，单篇 23~25 秒。
8. **换后端不重复付费**：待抽筛选从「按 extractor 过滤」改成「任何后端抽过即完成」。
9. **断点续航**：中途用户断电关机一次（11-19 凌晨），重启后确认数据完整、WAL 已合并、进程已清。
10. **当前停点**：118/357 已抽。用户决定开新会话，把剩下的事交给本文件。

---

## 3. 已经做完、不需要重做的事

### 3.1 代码（已提交并推送到 `c6f7784`）

| 文件 | 内容 |
|---|---|
| `src/newskg/sources/archive.py` | `ChinanewsArchive`（中新网日页）+ `IfanrDate`（爱范儿 `/date/YYYY/MM/DD`） |
| `src/newskg/sources/html_list.py` | 通用 HTML 列表页源：**从 URL 路径里读日期**（`/20261117/`、`/2026-11/17/`），读不出日期的丢弃 |
| `src/newskg/kg/direct.py` | **新增**：`DirectExtractor`，一次 chat 调用抽三元组，带 `PACE` 节流（见第 4 节） |
| `src/newskg/kg/__init__.py` | 工厂支持 `NEWSKG_BACKEND=direct` / `--backend direct` |
| `src/newskg/pipeline.py` | `build_graph` 支持 `--workers`、`--shard i/n`；**待抽筛选改为「任何后端抽过即算完成」**，换后端不会重复付费 |
| `src/newskg/store.py` | WAL 模式 + `busy_timeout`；SCHEMA 竞态容忍（多进程同启不再因 `view v_triples already exists` 崩） |
| `src/newskg/kg/kggen.py` | 关掉 dspy 磁盘缓存（会锁死）；`litellm.request_timeout` 默认 600s |
| `src/newskg/quality.py` | 页脚闸只认尾部 20% 内的短行，修掉「单行正文被判页脚 100% 而拒收」 |

### 3.2 数据

- 中新网 11-04 ~ 11-10 七天已全量抓完（321 篇）。
- 爱范儿 11-04 ~ 11-10 七天已抓完（36 篇，该站每天只有 1~12 篇科技稿）。
- 41 篇用 KGGen 抽过（903 条），77 篇用直连抽过（637 条）。**这 118 篇不要重抽**。

### 3.3 修过并验证的坑（不要再踩一遍）

1. 中新网日页一天有 250~555 条，但打分上限默认 60，会**静默少抓**（11-04 只留 27 篇 vs 实际 59 篇）。必须 `NEWSKG_TOPIC_MAX_SCORED=600` 且 `--limit 140`。
2. 爱范儿那篇 Bose 文章被页脚闸误杀 → 已修 `quality.py`。
3. `--source` 抓取默认写 `out/kg.sqlite3`，不是 `data/*.sqlite3`；做实验先复制库。
4. `.ps1` 脚本被执行策略拒绝（未签名），一律用 `pwsh -Command` 内联跑。

---

## 4. 必须先读的 10 条经验（最重要的一节）

### 4.1 这个 LLM 端点会「假死」，必须节流

事实（2026-11-19 实测，反复复现）：

- **并发 ≥ 2 必挂**。2 路、3 路、4 路、6 路都试过，全部卡死：请求发出去既不返回也不报错，进程 CPU 归零、只留一条到 `39.106.x.x:443` 的 ESTABLISHED 连接。
- **不加节流连续发，跑到第 3~4 篇必挂**。
- **每篇之间强制睡 5 秒就全过**：8 篇 202 秒、15 篇 366 秒、24 篇 601 秒、24 篇 562 秒，**全部 0 失败**。
- 结论：这不是「慢」，是**限流**。端点给一个很小的突发额度，用完就把后续请求挂起。

因此：

```powershell
$env:NEWSKG_DIRECT_PACE = "5"     # 必须；0 会挂
```

### 4.2 必须分段跑，不要一次跑全量

`build --backend direct`（不带 `--limit`）实测在开头就卡住，一篇都不出；`--limit 24` 每段都稳。
**固定节奏：每段 24 篇，约 10 分钟，跑完就落库。**

### 4.3 不要用 `Start-Process` 脱离终端跑抽取

实测三次：前台跑（`& python -m ...`）正常出数；用 `Start-Process -NoNewWindow -RedirectStandardOutput` 拉起来的进程虽然活着，但一篇都抽不出来。原因未查清，**别用**。要长跑就用会话的后台任务（`run_in_background`）。

### 4.4 单次生成预算压到 2000 token

同一篇文章：`max_tokens=4000` 会挂住，`max_tokens=2000` 18 秒返回。
直连后端的默认参数已经按这个配好（见 6.2）。

### 4.5 kill 进程会污染队列，之后必须等

被 kill 的请求在服务端不会取消，继续占位。每次 kill 之后要等 **10~15 分钟**再发新请求，否则新请求排在后面一起卡。今晚反复重启把情况越搞越糟，就是这条。

### 4.6 时间一律存 UTC

站点时间是北京时（UTC+8），库里统一存 `%Y-%m-%dT%H:%M:%SZ`。凌晨发的稿子按 UTC 会落到前一天，统计「每天篇数」时要心里有数。`sources/timestamps.py`、`archive.py` 的 `BJT` 常量是现成的。

### 4.7 RSS 只有最近 2~3 天，回溯必须走日期页

11 个 RSS 源实测最早只到 3 天前。**要 8 天前的数据，只能按日期页抓**（`chinanews_day` / `ifanr_day` 就是这么做的）。

### 4.8 打分阈值与上限

`NEWSKG_TOPIC_THRESHOLD=6`，`NEWSKG_TOPIC_MAX_SCORED` 默认 60 会少抓；一天一页（250~555 条）要设 600，配 `--limit 140`。

### 4.9 dspy 磁盘缓存必须关

`NEWSKG_KGGEN_CACHE=1` 时 dspy 会写磁盘缓存，多进程互等锁，整批挂死。代码里已默认关闭，不要打开。

### 4.10 库里视图每次启动重建

`store.py` 里 SCHEMA 先 `DROP VIEW` 再 `CREATE VIEW`，多进程同时启动会撞 `already exists`；已加容错。改视图 SQL 后重启即生效。

---

## 5. 接下来的任务（按顺序做）

### T1. 补齐 8 个「按天归档」源（本阶段最大的工作量）

**现状**：`_REGISTRY` 里 23 个名字，但真正能按日期回溯的只有 2 个。其余是 RSS（`tech` 预设下 9~11 个 feed），RSS 拿不到 8 天前。

**目标**：新增 8 个类，命名 `xxx_day`，写进 `src/newskg/sources/__init__.py` 的 `_REGISTRY`。

**候选站点**（下表「归档页」一列是**待探明**，不是已知结论，别当事实用）：

| 源 | RSS（已有，作对照） | 推测的日期归档入口 | 状态 |
|---|---|---|---|
| 钛媒体 | `https://www.tmtpost.com/rss.xml` | 站内 `/tag/` 或列表分页，日期在 URL | 待探明 |
| 雷锋网 | `https://www.leiphone.com/feed` | 列表页分页，日期可能在页面节点 | 待探明 |
| InfoQ 中文 | `https://www.infoq.cn/feed` | 列表页 + `?page=N` | 待探明 |
| 界面新闻 | `https://a.jiemian.com/...rss` | `jiemian.com/lists/N.html` 分页 | 待探明 |
| 证券日报 | `http://www.zqrb.cn/rss.xml` | 频道页分页 | 待探明 |
| 经济观察报 | `http://www.eeo.com.cn/rss.xml` | 频道页分页 | 待探明 |
| 新华网科技 | 无（RSS 已废） | `news.cn/tech/` 列表，URL 含 `/20261117/` | 待探明，可复用 `html_list.py` |
| 科技日报 | 无（RSS 不给 pubDate） | `stdaily.com` 列表，URL 含日期 | 待探明，可复用 `html_list.py` |

**实现套路**（照抄 `archive.py` 的 `IfanrDate`）：

1. 先手工用浏览器/`curl` 确认「某一天的列表页 URL 长什么样」，以及条目里时间怎么给（页面节点 or URL 路径）。
2. 写一个类：`name = "xxx_day"`，`discover()` 返回 `list[ArticleStub]`（字段：`url`、`title`、`published_at`（**UTC、带 Z**）、`source`、`summary`）。
3. 注册：`_REGISTRY["xxx_day"] = XxxDay`。
4. 验收：`--source xxx_day --day 2026-11-05 --days 15 --limit 40` 出 ≥10 条，且 `published_at` 落在 11-05。

> 建议节奏：先做 **2 个**跑通（比如新华网 + 科技日报，`html_list.py` 能直接复用），确认抽取链路没问题，再复制到剩下 6 个。不要一次写 8 个再一起调。

**没有日期归档的兜底**（按优先级）：
- 站点 `sitemap.xml`（按天分片的那种）；
- 列表页翻页直到日期早于目标日（`HtmlListFeed` 目前只解析单页，需要加翻页循环）；
- 站内搜索按日期过滤（最不可靠）。

### T2. 补 11-11 这一天

现在只到 11-10。对已有两个源补抓：

```powershell
$env:NEWSKG_TOPIC_MAX_SCORED = "600"
python -m newskg.cli fetch --source chinanews_day --day 2026-11-11 --days 15 --limit 140
python -m newskg.cli fetch --source ifanr_day    --day 2026-11-11 --days 15 --limit 40
```

### T3. 用 10 个源重抓 8 天

每天每源跑一次，8 天 × 10 源 = 80 次抓取（每次几十秒到两分钟）。建议写成循环：

```powershell
$env:NEWSKG_TOPIC_MAX_SCORED = "600"
foreach ($d in 4..11) {
  $day = "2026-11-{0:D2}" -f $d
  foreach ($src in $sources) {                      # $sources = 10 个 xxx_day
    python -m newskg.cli fetch --source $src --day $day --days 15 --limit 140
  }
}
```

**抓取阶段没有限流问题**（每篇正文抓取是普通 HTTP，不是生成），可以连续跑。

验收：`select substr(published_at,1,10) d, source, count(*) ... group by 1,2` 每天每源都有数。

### T4. 抽取全部

```powershell
$env:PYTHONPATH = "src;.kgvenv"
$env:NEWSKG_BACKEND = "direct"
$env:NEWSKG_DIRECT_MAX_TOKENS = "2000"
$env:NEWSKG_DIRECT_LIMIT = "14"
$env:NEWSKG_DIRECT_CHARS = "2200"
$env:NEWSKG_DIRECT_PACE = "5"
# 每段 24 篇，重复直到待抽为 0
python -u -m newskg.cli build --backend direct --limit 24
```

**不要**加 `--shard`、**不要**并发、**不要** kill 后立刻重启。

**时间估算**（基于实测 23~25 秒/篇）：

| 语料规模 | 抽取耗时 |
|---|---|
| 800 篇 | ≈ 5.5 小时 |
| 1500 篇 | ≈ 10 小时 |
| 2500 篇 | ≈ 17 小时 |

用户白天可用，建议按「每段 24 篇 ≈ 10 分钟」推进，随时可停、随时续跑（台账保证不重抽）。

### T5. 建图收尾

```powershell
python -m newskg.cli stats                       # 总量、每日分布、度数
python -m newskg.cli export --format all --out out/
python -m newskg.cli dump --limit 50             # 抽查三元组质量
python -m newskg.cli renormalize                 # 改过别名表后重写实体名（不重抽）
```

### T6. 出报告（用户在意的三件事）

1. **总量**：文章数、三元组数、实体数、边数；
2. **跨文章实体比例**：被 ≥2 篇文章提到的实体占比（`v_triples.support` 里数 `support>=2` 的比例）；
3. **每日图规模**：每天的文章数 / 三元组数 / 新增实体数，用来看时间上的覆盖是否均匀。

> 报告里每个数字都要标明是**实测**还是**估算**，用户明确要求过这一点。

### T7. 提交与镜像

```powershell
$m = ".tmp\StockSage"
Copy-Item src\newskg\... (Join-Path $m "knowlegde_merge\src\newskg\...") -Force
git -C $m add -A knowlegde_merge
git -C $m -c user.name="oQAQo233" -c user.email="oQAQo233@users.noreply.github.com" commit -m "..."
git -C $m -c http.sslBackend=openssl push origin main
```

- 仓库根是另一个项目（`stocksage`），本项目只在 `knowlegde_merge/` 子目录，**不要写到根目录**。
- **当前已推送**：`c6f7784`（GitHub `oQAQo233/StockSage` 的 `knowlegde_merge/` 子目录）。若后续推送报 TLS 错，等几分钟重试即可。
- 不入库：`.env`、`out/`、`data/md/`、`data/raw/`、`.venv/`、`.kgvenv/`、`.tmp/`、`.nltk_data/`。

---

## 6. 环境与命令速查

### 6.1 环境

```powershell
cd C:\JRTAT\Application\DSH\workspace\knowlegde_merge
$env:PYTHONPATH = "src;.kgvenv"          # .kgvenv 里才有 kg-gen（KGGen）
python = .\.venv\Scripts\python.exe
```

`.env`（**不要提交，也不要贴到聊天里**）现有键：

```
NEWSKG_TOPIC_BASE_URL / NEWSKG_TOPIC_API_KEY / NEWSKG_TOPIC_MODEL / NEWSKG_TOPIC_THRESHOLD
NEWSKG_LLM_BASE_URL   / NEWSKG_LLM_API_KEY   / NEWSKG_LLM_MODEL   / NEWSKG_LLM_TIMEOUT
```

- 抽取模型：`qwen3.7-plus`（KGGen 用）；直连默认 `qwen3.6-flash`（`NEWSKG_DIRECT_MODEL` 可改）。
- 打分模型：`qwen3.8-flash`，阈值 6。
- 端点可用模型 23 个，含 `qwen3.6-flash` / `qwen3.8-flash` / `qwen3.7-plus` / `deepseek-v4-flash` 等（`GET /v1/models` 可查）。

### 6.2 直连抽取的全部可调参数

| 环境变量 | 默认 | 作用 |
|---|---|---|
| `NEWSKG_BACKEND` | `kggen` | `direct` 走直连 |
| `NEWSKG_DIRECT_MODEL` | `qwen3.6-flash` | 直连用的模型 |
| `NEWSKG_DIRECT_LIMIT` | 28 | 每篇最多抽几条（实测 14 更稳） |
| `NEWSKG_DIRECT_CHARS` | 3000 | 送进模型的正文长度（实测 2200 更稳） |
| `NEWSKG_DIRECT_MAX_TOKENS` | 4000 | **必须设 2000**，4000 会挂 |
| `NEWSKG_DIRECT_TIMEOUT` | 120 | 单次请求超时 |
| `NEWSKG_DIRECT_PACE` | 4 | **每篇之间睡几秒，必须 5** |

### 6.3 常用命令

```powershell
# 抓一天（按天归档源）
python -m newskg.cli fetch --source chinanews_day --day 2026-11-04 --days 15 --limit 140
python -m newskg.cli fetch --source ifanr_day    --day 2026-11-04 --days 15 --limit 40

# 抽一段（24 篇）
python -u -m newskg.cli build --backend direct --limit 24

# 统计 / 导出 / 抽查
python -m newskg.cli stats
python -m newskg.cli export --format all --out out/
python -m newskg.cli dump --limit 30
```

---

## 7. 文件地图

```
src/newskg/
  cli.py             命令入口（init/fetch/build/export/stats/dump/renormalize/reset/ask/demo）
  config.py          .env 加载与环境变量（LLM、TOPIC、路径）
  pipeline.py        build_graph：预筛 → 抽取（串行/线程/分片）→ 主线程逐篇写库
  store.py           SQLite schema（articles/paragraphs/paragraphs_fts/triples/runs/v_triples）+ WAL
  quality.py         正文闸：HARD_CUT 40% / SOFT_CUT 80%、长度 150、页脚占比
  dedup.py           URL 归一 → sha1 → SimHash ≤6 → shingle Jaccard ≥0.55
  convert.py         正文 → Markdown（markitdown）
  events.py          事件时间线
  retrieve.py        检索 + 报告
  report.py          五节报告生成
  llm_client.py      chat()：一次补全 + 3 次重试（直连后端用它）
  kg/
    __init__.py      build_extractor(kind)：kggen / direct
    kggen.py         KGGen 后端（dspy，两次大请求）
    direct.py        直连后端（一次小请求 + PACE 节流）   ← 本阶段主力
    base.py          build_triple / dedupe_triples / normalize_predicate
    entities.py      实体归一（别名表）
    bylines.py       记者署名识别（不进图）
  sources/
    archive.py       ChinanewsArchive（中新网日页）+ IfanrDate（爱范儿日期页）
    html_list.py     通用 HTML 列表页（从 URL 读日期）      ← 新源优先复用
    tech.py          9~11 个 RSS feed 定义与预设
    rss.py           RSS 源
    topic_llm.py     题材打分（qwen3.8-flash，阈值 6）
    timestamps.py    时间解析与时区
    filters.py       关键词/时间窗过滤
data/                md/ 与 raw/（不入库）
out/                 kg.sqlite3、导出产物、日志
.tmp/StockSage/      GitHub 镜像（knowlegde_merge/ 子目录）
```

---

## 8. 已知风险与待查

| # | 问题 | 影响 | 建议 |
|---|---|---|---|
| 1 | 端点限流规律没完全摸清（预算多少、多久恢复） | 抽取速度不可控 | 严守 `PACE=5` + `--limit 24`；出现 3 分钟不落库就等 10 分钟，别重启 |
| 2 | 「脱离终端启动就抽不出东西」原因未查清 | 不能无人值守长跑 | 用会话后台任务或前台分段 |
| 3 | 直连后端密度只有 6~8 条/篇（KGGen 22 条） | 图偏稀 | 跑完可补一轮换窗口重抽取并集 |
| 4 | 8 个新源的日期归档入口都还没探明 | T1 有不确定性 | 先做 2 个验证，再批量复制 |
| 5 | ~~11-03 有 3 篇窗口外文章~~ | 实为时区假象：`published_at` 存 UTC，最早 2026-11-03T23:00:00Z = 北京 11-04 07:00 | 已按北京时间重算，窗口外 0 篇 |
| 6 | GitHub 推送 TLS 偶尔被掐 | 代码没同步 | `git -c http.sslBackend=openssl push`，失败隔几分钟重试 |

---

## 8.5 探源实测记录（2026-11-19 16:20 补，新一轮会话实测，**勿重复**）

### 8.5.1 sitemap 兜底路线已证明无效

对 8 个候选源的 `sitemap.xml` 逐个实测（`User-Agent` 用常见桌面 Chrome，超时 15s），结论是
**这些 sitemap 只覆盖最近 1~2 天，无法回溯到 11-11**，第 T1 节里「站点 sitemap.xml」这条兜底
可以直接划掉：

| 站点 | sitemap | loc 数 | sitemap 内实际日期范围 |
|---|---|---|---|
| 钛媒体 | 200 | 34 | 仅 2026-11-19 |
| 雷锋网 | 200 | 3000 | 仅 2026-11-19（URL 不带日期，是内容 id） |
| 科技日报 | 200 | 200 | 仅 2026-11-18 ~ 11-19 |
| 界面新闻 | **404** | — | 无 |
| 证券日报 | **404** | — | 无 |
| 经济观察报 | **404** | — | 无 |
| 新华网 | **403** | — | 无（但 `/tech/` 频道页 200 可读） |
| InfoQ 中文 | 200 但返回 HTML 壳 | — | 是 SPA，sitemap 不存在 |

### 8.5.2 各站 RSS 现状（实测 200，但都只到近期）

钛媒体 `rss.xml` 283KB、雷锋网 `feed` 624KB、证券日报 `rss.xml` 584KB 都还能拿到，但和第 4.7 节
的结论一致：**只有最近 2~3 天**，对 11-04 ~ 11-11 这段窗口无用。经济观察报 `rss.xml` 只有 1KB，是残废 feed。

### 8.5.3 `html_list.py` 是「写好但没接线」的死代码

`src/newskg/sources/html_list.py` 里已经有 `HtmlListFeed` 和两个现成规格（`xinhua_tech`、`stdaily`），
但 **`_REGISTRY` 里没有注册它们**（`sources/__init__.py` 只 import 了 `ChinanewsArchive` 和 `IfanrDate`）。
用法是 `HtmlListFeed("名字", ["xinhua_tech", "stdaily"])`，注册一行就能跑。

**但注意：它对本阶段目标帮助有限。** 它只解析**单个列表页**、`discover()` 完全忽略 `days` 参数，
前端页面上只有今天/昨天的稿子 —— 拿到的是 11-18、11-19，不是我们要的 11-04~11-11。
要让它有用，必须先给它加**翻页循环**（列表页 `?page=N` 或 `/lists/N.html` 往回翻到目标日），
这是比「新增 8 个类」更省事的起手式：一个站点只要列表页能翻页、且 URL 带日期，就能回溯。

### 8.5.4 因此 T1 的可行路线收窄为两条

1. **列表页翻页回溯**（优先）：找站点列表页的分页参数（`?page=N`、`/lists/N.html`），
   往回翻直到条目日期早于目标日，日期从 URL 读（复用 `html_list.date_from_url`）。
   界面新闻 `https://www.jiemian.com/lists/1.html` 实测 200、88KB，看起来最有希望。
2. **页面节点取日期**：URL 不带日期时，从列表页的 `<time>`/`class="time"` 节点读（如爱范儿的做法），
   代价是站点改模板就失效。

> 本轮**没有**新增任何源类：验证翻页与节点解析的耗时超出 20 分钟预算，只把入口事实固定下来。

### 8.5.5 11-11 已补齐（T2 完成），以及一个绕开 LLM 打分的办法

**结论**：11-11 已入库，天数覆盖 8/8 齐了。库现在是 **385 篇**（11-03: 3、11-04: 68、11-05: 54、
11-06: 56、11-07: 44、11-08: 33、11-09: 36、11-10: 65、11-11: 26）。

**过程**：第一次用默认的 LLM 打分跑抓取，卡死了 —— python 进程 CPU 停在 1.34 秒、**零个 TCP 连接**、
7 分钟无输出。确认不是日页的问题：`scroll-news/2026/1111/news.shtml` 200 / 207KB / 0.1 秒，
`ifanr.com/date/2026/11/11` 200 / 42KB / 0.6 秒，两个日页都正常。所以卡在**题材打分那几百次
LLM 调用**上，和第 4.1 节同一种「假死」。

**绕过办法（以后抓取卡住就用这招）**：`fetch` 支持 `--topic-mode keyword`，走本地关键词打分，
**完全不发 LLM 请求**，因此不受限流影响，秒级完成。

但有个坑必须记住：**关键词打分和 LLM 打分的量纲不一样**。同一批 533 条：

| 打分方式 | 分数分布 |
|---|---|
| keyword | 0-2 分：505、3-4 分：28、≥5 分：**0 条** |
| 阈值 6（默认） | 保留 **0** 篇 —— 等于全军覆没 |

所以关键词模式必须**同时降阈值**：

```powershell
$env:PYTHONPATH = "src"; $env:NEWSKG_TOPIC_MAX_SCORED = "600"
# 中新网 11-11：533 条候选 → 阈值 3 → 保留 28 篇 → 入库 27 篇（1 篇正文不达标）
.\.venv\Scripts\python.exe -m newskg.cli fetch --source chinanews_day --day 2026-11-11 `
    --days 15 --limit 140 --topic-mode keyword --topic-threshold 3
# 爱范儿 11-11：1 条 → 入库 1 篇
.\.venv\Scripts\python.exe -m newskg.cli fetch --source ifanr_day --day 2026-11-11 `
    --days 15 --limit 40 --topic-mode keyword --topic-threshold 3
```

**注意「26 vs 28」的差额**：入库 28 篇，但按 UTC 统计只有 26 篇落在 11-11 —— 另外 2 篇是凌晨稿件，
换成 UTC 落到 11-10 了（11-10 从 63 变成 65）。这是第 4.6 节的口径问题，不是数据丢失。

> 口径提醒：11-11 这 28 篇是 **keyword 打分**筛的，和之前 7 天的 **LLM 打分**口径不同。
> 报告里要么注明这批用了降级筛选，要么等 LLM 端点空闲时重抓这天。

---

## 9. 本轮会话的新发现（2026-11-19 20:00~21:45，全部实测）

这一节推翻或修正了前面几节的几条结论，**新会话请优先看这里**。

### 9.1 「抽取会挂死」的真正原因：模型，不是并发

用 `faulthandler.dump_traceback_later` 抓到卡点栈，稳定停在：

```
ssl.py read → http.client._read_status → urllib3.getresponse → requests.post
→ llm_client.chat → direct.py extract
```

也就是说**请求发出去了，服务端收下但不回**，连 socket 都没断。此时 `NEWSKG_DIRECT_TIMEOUT=90`
并没有在 90 秒内生效（实测等了 150 秒以上仍在 read）。

根因是**模型选错**：`DirectExtractor` 默认 `NEWSKG_DIRECT_MODEL=qwen3.6-flash`。
同一个端点、同样大小的请求：

| 模型 | 表现 |
|---|---|
| `qwen3.6-flash`（默认值） | 会「收下不回」，一挂几十分钟，表现为 `build` 零产出 |
| `qwen3.7-plus` | 20~35 秒稳定返回，12 次连续调用零失败 |

**结论：抽取必须显式设 `NEWSKG_DIRECT_MODEL=qwen3.7-plus`。**

### 9.2 并发分片完全可以跑，而且必须跑

老结论「`--workers` / 多进程分片必卡死」是**在 `qwen3.6-flash` 下得出的**，换模型后不成立：

| 方式 | 实测 |
|---|---|
| 单进程 | 43.6 秒/篇 |
| **4 片并行**（`--shard i/4`） | **11.7~14 秒/篇，零失败** |

4 片是实测稳的档位，批量 30~36 篇/批，一批 6~9 分钟。

### 9.3 进程必须由 PowerShell 的 `Start-Process` 启动

这条最容易踩：

| 启动方式 | 结果 |
|---|---|
| 直接前台跑 `.venv\Scripts\python.exe -m newskg.cli build` | ✅ 正常 |
| 在 `run_in_background` 的后台任务里跑 | ❌ 卡在 ssl read，零产出 |
| **Python 的 `subprocess.Popen` 起的子进程** | ❌ 4 片全卡在第一行 |
| **PowerShell 的 `Start-Process -NoNewWindow` 起的子进程** | ✅ 正常 |

所以续抽的唯一可靠写法是：前台调用里用 `Start-Process` 起 4 个分片，`WaitForExit`，
单次调用控制在 10 分钟内（超过 10 分钟会被工具转成后台任务）。可复用脚本：

```powershell
$env:PYTHONPATH="src"; $env:NEWSKG_BACKEND="direct"
$env:NEWSKG_DIRECT_MODEL="qwen3.7-plus"
$env:NEWSKG_DIRECT_MAX_TOKENS="1500"; $env:NEWSKG_DIRECT_LIMIT="12"
$env:NEWSKG_DIRECT_CHARS="1800"; $env:NEWSKG_DIRECT_PACE="2"
$env:NEWSKG_DIRECT_TIMEOUT="90"
$py = ".\.venv\Scripts\python.exe"
$procs = @()
for ($i=0; $i -lt 4; $i++) {
  $procs += Start-Process -FilePath $py -ArgumentList '-u','-m','newskg.cli','build',
    '--backend','direct','--limit','9','--shard',"$i/4" -NoNewWindow -PassThru `
    -RedirectStandardOutput ".tmp\c$i.log" -RedirectStandardError ".tmp\c$i.err"
}
foreach ($p in $procs) { $p.WaitForExit(540000) | Out-Null }
```

### 9.4 【已废弃】新增 3 个源：走「电子报（方正 CMS）」

> **2026-11-20 更新：这三个源已按要求删除**（财经报，科技题材太少），`epaper.py` 与注册项均已移除。
> 以下内容只作为「电子报这条技术路线怎么走通」的记录保留。

频道列表这条路已被证据判死：澎湃、IT之家、新浪科技滚动的 `?page=N` 对服务端 HTML 无效
（每页字节完全相同）；人民网、钛媒体、雷锋网的 sitemap 只留今天和昨天；新华网、界面、
证券日报首页的「按日」链接都要 403 或没有日期。详见 §8.5。

真正的出路是**报纸电子版**：编辑按日出版，URL 里直接写日期，天生可回溯。

| 源 | 版面 URL 模板 | 文章链接形态 | 标题 |
|---|---|---|---|
| `zqrb_day` 证券日报 | `epaper.zqrb.cn/html/YYYY-MM/DD/node_N.htm` | `<area href="content_xxx.htm">` | 无（回填） |
| `ce_day` 经济日报 | `paper.ce.cn/pc/layout/YYYYMM/DD/node_NN.html` | `<area href="...content_xxx.html">` | 无（回填） |
| `cnstock_day` 上海证券报 | `paper.cnstock.com/html/YYYY-MM/DD/node_N.htm`（版面号不连续，固定从 `node_700.htm` 进，再读导航） | `<h3><a href="content_xxx.htm">` | 有 |

实现见 `src/newskg/sources/epaper.py`，已注册进 `_REGISTRY`。两个坑：

1. **版面页多数只有图片热区，没有标题**。标题为空会让关键词打分恒为 0、整版被丢，
   所以 `_title_of()` 会回抓文章页的 `<title>` 回填（并剥掉「证券日报网-」这类前缀）。
2. **日期口径**：版面页只给日期不给钟点，统一记成当天 09:00（北京时间）→ UTC 01:00Z，
   这样按 UTC 统计时文章仍落在同一个日历日（对比 `html_list.py` 用 00:00 BJT 会整体前移一天）。

其余电子报（人民日报、光明日报、中国证券报、中国青年报、经济参考报、工人日报、
证券时报、每日经济新闻、21 世纪经济报道、新京报、南方都市报、南方日报、广州日报、
深圳特区报、羊城晚报、新快报、科技日报）全部实测不通：`paperindex.htm` 是 JS 空壳
（400~600 字节），或者日期路径 404。**科技日报尤其可惜**：`epaper.stdaily.com` 整个站
是 SPA（`/statics/technology-site/index.html#/`），服务端拿不到版面 HTML。

### 9.5 最终口径提醒（写报告必须交代）

- **源 7/10**。差的 3 个不是没找，是「中文新闻站的列表页几乎全是前端 JS 翻页」这个结构性
  限制（证据见 §8.5 与 §9.4、§9.6）。硬凑源只会引入伪数据。
- **11-11 那天的筛选口径与其余 7 天不同**：11-11 用关键词打分（阈值 3），其余 7 天用 LLM 打分。
- **电子报三源已删除**（2026-11-20，财经报科技题材太少）；部委两源合计 9 篇，发布频率低。
- 抽取器混用：41 篇 KGGen（22 条/篇）+ 384 篇 llm-direct（~9 条/篇），图里密度不均。

---

## 9.6 新增 2 个部委源：老式服务端分页

除了电子报，还有一条路走通了：**部委网站的新闻列表仍是老式服务端分页**
（`index.html` / `index_1.html` / `index_2.html`…），列表项上直接印日期，所以「翻到第几页」
真的能拿到更早的文章。实现见 `src/newskg/sources/govlist.py`：

| 源 | 频道 | 8 天篇数 |
|---|---|---|
| `most_day` | `most.gov.cn/kjbgz/` 科技部工作 | 2 |
| `ndrc_day` | `ndrc.gov.cn/xwdt/xwfb/` 发改委新闻发布 | 7 |

写适配器的两个坑（已在代码注释里标出）：

1. **`<li[^>]*>` 会把 `<link href=...>` 也当成列表项**，必须写 `<li\b`；
2. **整页混着侧栏/页脚的旧链接**，一起解析会让「本页覆盖哪几天」失真、翻页判断失效，
   所以要只解析主列表容器（`LIST_UL = "info_list2"` / `"u-list"`），
   并且**用整页最大日期决定是否停止翻页**（不用「某条比目标日更早」，会被页脚旧链误判）。

同标准下又探了 8 个机构站（中科院、中国科协、基金委、中国工程院、中国信通院、国家数据局、
北京科委、上海科委），全部不通过：列表页是 200~2500 字节的 JS 空壳、或列表里没有日期、
或那个 URL 是文章页。**要补源就沿这条线找发布频率更高的机构站**（省级科技厅、行业协会、
研究院所），不要再试门户频道。

---

## 9.7 【最重要】KGGen 卡死的真相：dspy 在下载价格表

之前所有会话（包括我自己）都得出了错误结论：「KGGen 一篇要 93~200 秒、并发就挂、
所以改用自写直连」。**这是误判。**

用 `faulthandler` 抓卡住时的栈：

```
dspy/clients/model_metadata/__init__.py:63 in _load
  → urllib.request.urlopen → http.client.read → ssl.py:read     ← 无限挂在这里
  ← dspy/clients/costs.py:44 _estimate_cost
  ← dspy/clients/execution.py:643 _price_result                  ← 每次调用后都算钱
```

**KGGen 每次调完模型，dspy 都要联网拉一份模型价格表**
（`raw.githubusercontent.com/BerriAI/litellm/.../model_prices_and_context_window.json`）。
这个域名在本机被墙，于是进程挂在 SSL 读上，而真正的模型请求早就返回了。
同样的请求直接用 litellm 打，9.3 秒就回来。

**解法（必须加，否则必挂）**：

```powershell
$env:LITELLM_LOCAL_MODEL_COST_MAP="true"   # 用 dspy 自带离线快照，不联网
```

加上之后：单篇 77 秒出 15 条三元组。

### 其他三条实测约束

1. **单篇耗时 ≈ 0.09×字数 + 60 秒**，同长度波动可达 3 倍。
   实测：208 字/77 秒、634 字/77 秒、2515 字/238 秒。
2. **端点只允许单路**。2/3/6/8/10 路并行全部 0 产出（进程活着、不报错、无限等待）；
   单路稳定出结果。这是硬限制，调参绕不开。
3. **进程必须被前台调用守着**。`Start-Process` 起完父调用立刻返回（放养），子进程会停住；
   父调用 `WaitForExit()` 等到跑完则正常。
4. **排序决定产出**。`build` 默认 `ORDER BY published_at DESC`，会先啃最新那天的长文
   （单篇 6~8 分钟）。今晚改用 `.tmp/run_short_first.py`（按字数升序），
   30 分钟完成 19 篇，约 40 篇/小时。

**抽取已于 11-21 14:43 全量完成**（536/536 篇、13346 条三元组），不用再补跑。
剩下的缺口只有「源 5/10」，见 §9.6。

两天累积下来最值得复用的两个做法：

1. 把守护逻辑写成「外层循环 + 每篇单独提交」的 `.tmp/run_until_done.py`，比 `build` 更抗抖动——
   单篇异常不会拖垮整趟，到点自停。11-20 连跑 10.7 小时净增 402 篇，11-21 再跑 209 分钟收尾 65 篇。
2. 再套一层 `.tmp/finish_today.py`：每 10 分钟查一次「还有没有待抽」，为 0 就自动导出并写
   `.tmp/final_summary.txt`。这样整条链路不需要人守着等结束。
3. 长早报（4000~6500 字）偶尔会整篇失败：模型把内容全写进 `reasoning_content`、`text` 返回空，
   适配器解析不到 `relations`，结果记成 0 条。处理办法是删掉该篇的 `runs` 记录重抽，
   11-21 那 3 篇重抽后分别出了 11/40/29 条。

---
## 10. 给新会话的开场建议

> 先读第 4 节和第 9 节，再确认 `runs` 台账和进程状态。
> 抽取记住四件事：**`NEWSKG_DIRECT_MODEL=qwen3.7-plus`、4 片 `--shard` 并行、用 PowerShell 的 `Start-Process` 起进程、单次调用别超 10 分钟**。

查状态的一行命令：

```powershell
$env:PYTHONPATH="src"; .\.venv\Scripts\python.exe -c "import sys;sys.path.insert(0,'src');from newskg.store import connect;c=connect();print('文章',c.execute('select count(*) from articles').fetchone()[0],'| 已抽',c.execute('select count(*) from runs').fetchone()[0],'| 三元组',c.execute('select count(*) from triples').fetchone()[0])"
```






