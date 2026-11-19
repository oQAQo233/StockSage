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

## 2. 现状快照（2026-11-19 16:00 实测）

数据库：`out/kg.sqlite3`

| 指标 | 数值 |
|---|---|
| 文章 | **357 篇** |
| 已抽取 | **118 篇**（kggen 41 篇 / llm-direct 77 篇） |
| 三元组 | **1528 条** |
| 已接的**按天归档**源 | **2 个**：`chinanews_day`、`ifanr_day` |
| 覆盖日期 | 11-03: 3、11-04: 68、11-05: 54、11-06: 56、11-07: 44、11-08: 33、11-09: 36、11-10: 63 |
| **缺** | 11-11 一整天；另外 8 个源一条也没有 |

**与目标的差距**：源 2/10，天数 7/8，抽取 118/357（重抓后基数还会变大）。

### 抽取器现状

| 后端 | 篇数 | 平均三元组/篇 | 单篇耗时 | 说明 |
|---|---|---|---|---|
| `kggen`（KGGen） | 41 | 22.0 | 93~200 秒 | 质量最好，自带实体聚类，但一次要发两发大请求 |
| `llm-direct`（本项目自写） | 77 | 8.3 | **23~25 秒** | 一次小请求，稳、快；无聚类，靠规则归一 |

> 差距要如实对待：直连每篇只抽 6~8 条，KGGen 是 22 条。若最终图要求密度，跑完可再补一轮（换正文窗口二次抽、取并集），密度能到 12~15 条/篇。

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
| 5 | 11-03 有 3 篇窗口外文章 | 统计时口径不一 | 要么删掉，要么报告里注明 |
| 6 | GitHub 推送 TLS 偶尔被掐 | 代码没同步 | `git -c http.sslBackend=openssl push`，失败隔几分钟重试 |

---

## 9. 给新会话的开场建议

> 先读 `HANDOFF.md` 第 4 节，再确认当前 `runs` 台账和进程状态，然后从 T1（补源）或 T4（续抽）接着干。
> 抽取永远记住三件事：**PACE=5、--limit 24、不要 kill 后立刻重启**。

查状态的一行命令：

```powershell
$env:PYTHONPATH="src"; .\.venv\Scripts\python.exe -c "import sys;sys.path.insert(0,'src');from newskg.store import connect;c=connect();print('文章',c.execute('select count(*) from articles').fetchone()[0],'| 已抽',c.execute('select count(*) from runs').fetchone()[0],'| 三元组',c.execute('select count(*) from triples').fetchone()[0])"
```