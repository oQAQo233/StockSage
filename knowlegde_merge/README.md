# 新闻抽取与知识图谱构建（newskg）

把国际新闻与中文财经资讯抓下来，抽出正文并统一成 Markdown，再用三种抽取器（规则模板 / 自写 LLM prompt / KGGen）生成 `(主语, 谓词, 宾语)` 三元组，落到 SQLite 图谱，导出成 JSON / Cypher / GraphML / 可交互 HTML，最后对照人工标注的 gold 集做双口径评估。

- **不需要联网也能跑通全链路**：`run.cmd demo` 用 8 篇合成样例、0 个 API key，约 3.5 秒产出一份完整报告。
- **三种抽取器的结果落在同一张表**（`triples.extractor` 区分），可以直接横向对照。
- **中文、英文两条链路都支持**：中文文章产出中文谓词（`收购` / `中标` / `签订`），英文产出 `snake_case`（`acquired` / `located_in`）。

三个入口：

| 想做什么 | 命令 | 需要联网 / key |
| --- | --- | --- |
| 先看它长什么样 | `run.cmd demo` | 都不需要 |
| 抓一批真新闻 | `run.cmd fetch --source rss --limit 10 --days 2` | 联网 |
| 跑对照实验 | `run.cmd --db out\corpus.sqlite3 compare --backends rule,llm` | 联网 + LLM key |

---

## 1. 快速开始

### 1.1 环境

- Python ≥ 3.11（本机实测 3.14.6，虚拟环境在 `.venv\`）
- Windows 下用 `run.cmd`（它负责设 `PYTHONPATH=src` 并挑出 `.venv` 里的解释器）
- 只有跑 KGGen 后端才需要第二个隔离环境 `.kgvenv\`（约 1.5 GB，见 §6.3）

### 1.2 依赖

| 包 | 版本 | 用途 | 必需性 |
| --- | --- | --- | --- |
| `requests` | ≥2.31 | 抓页面、调 LLM 端点 | 必需 |
| `trafilatura` | ≥2.0 | 定位正文容器、抽元数据（标题/作者/日期） | 必需 |
| `markitdown` | ≥0.1.0 | 干净 HTML 片段 → Markdown；PDF/DOCX 等异构文件入口 | 必需 |
| `feedparser` | ≥6.0 | 解析 RSS / Atom（含东方财富 RSS） | 必需（RSS 类源） |
| `networkx` | ≥3.1 | 导出 GraphML | 必需（导出时） |
| `python-dateutil` | ≥2.8 | 宽松解析各种日期写法 | 必需 |
| `kg-gen` | ≥0.2 | 第三方抽取器 KGGen（拉入 litellm + dspy + torch） | 可选，装 `.kgvenv` |
| `pyvis` | 随 kg-gen | 渲染可交互 `graph.html` | 可选 |

`requirements.txt` 一共 7 行，前 6 个是必需项，第 7 行 `kg-gen>=0.2` 装完约 1.5 GB。
**只跑 `rule` / `llm` 时建议把那一行注释掉**；需要 KGGen 再按 §6.3 单独装进 `.kgvenv\`（本机的 `.venv` 里就没装 kg-gen）。

### 1.3 安装

虚拟环境已就绪时，直接跳到 §1.4。换机器重建：

```bat
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

> 受限执行环境里 `pip` 可能报 `PermissionError ... whl.metadata`（`tempfile.mkdtemp` 建出的目录写不进去）。
> 这时改用垫片脚本，行为一致、只替换临时目录的创建方式：
> ```bat
> .\.venv\Scripts\python.exe tools\_pip_in_sandbox.py install --target .venv\Lib\site-packages -r requirements.txt
> ```
> 普通命令行里用不到。

### 1.4 配置

复制 `.env.example` 为 `.env` 后按需填写。**不填也能跑 `demo`**；要跑 `llm` 后端至少得填 key。

```bat
# .env —— 任意 OpenAI 兼容端点都行：DeepSeek / Ollama / vLLM / OpenCode 网关
NEWSKG_LLM_BASE_URL=https://api.deepseek.com/v1
NEWSKG_LLM_API_KEY=sk-xxxx
NEWSKG_LLM_MODEL=deepseek-chat

# 可选：Guardian 源用的免费 key（仅限非商业用途）
GUARDIAN_API_KEY=

# 可选：逗号分隔，覆盖默认的三档 RSS
NEWSKG_RSS_FEEDS=
```

> `.env` 的解析很朴素：`#` 只能**整行**注释，不要写在值的后面——
> 行内注释会被当成值的一部分（`KEY=abc  # 说明` 会读成 `abc  # 说明`）。

`config.py` 启动时自动加载 `.env`，且**已存在的环境变量优先**——临时 `set` 仍能覆盖文件里的值。全部可配项见 §9。

### 1.5 先跑一遍（离线，3 秒）

```bat
cd C:\JRTAT\Application\DSH\workspace\knowlegde_merge
.\run.cmd demo
```

实测输出（节选）：

```
[1/3] 发现文章：source=fixtures query=None limit=50
      拿到 8 条候选
[2/3] 抓取 + 正文抽取 + 转 Markdown
      成功 8 篇，失败 0 篇
[3/3] 去重 + 入库
      入库 8 篇，判定重复 1 篇
[build] 共写入 19 条三元组
文章 8 篇（判重丢弃 1） | 三元组 19 条 | 去重后 18 条 | 多源支持 1 条
[eval] strict P=100.00% R=95.00% F1=97.44%  (gold=20)
[report] ...\out\report.md
```

产物：

| 文件 | 内容 |
| --- | --- |
| `out/demo.sqlite3` | 文章表 + 三元组表 + 抽取台账 + 视图 `v_triples` |
| `out/graph.json` | 通用 `nodes` / `edges` |
| `out/graph.cypher` | 可直接粘进 Neo4j Browser 执行 |
| `out/graph.graphml` | 可拖进 Gephi / yEd 看拓扑 |
| `out/report.md` | 自动生成的规模、来源分布、高频实体、关系类型、双口径评估、局限 |
| `data/md/*.md` | 统一格式的中间产物（YAML front matter + 正文） |
| `data/raw/*.html` | 抓取下来的原始页面（可回溯、可复现） |

> 样例集的高分**不代表泛化能力**：8 篇样例是为规则模板量身合成的，是「可复现的最小闭环」。
> 真实新闻上的表现见 §8。

---

## 2. 目录结构

```
knowlegde_merge/
├─ run.cmd                  一键入口：设 PYTHONPATH=src，调 .venv 解释器
├─ run-kggen.cmd            跑 KGGen 后端的入口（先起本地转发器，再跑对照）
├─ requirements.txt         主环境依赖
├─ .env.example             配置模板（复制成 .env）
├─ src/newskg/
│  ├─ cli.py                命令行：12 个子命令，argparse 分发
│  ├─ config.py             路径、限速、去重阈值、.env 加载
│  ├─ models.py             ArticleStub / Article / Triple
│  ├─ sources/              发现层：8 个源 + 预设 + 两道过滤
│  │   ├─ base.py           Source 协议、带退避的 http_get、按域名限速
│  │   ├─ gdelt.py guardian.py rss.py fixtures.py
│  │   ├─ cn_finance.py     中文财经四源
│  │   └─ filters.py        时间窗 + 股票相关性（装饰器式包装）
│  ├─ convert.py            抓 HTML、trafilatura 抽正文、markitdown 转 Markdown
│  ├─ dedup.py              URL 规范化、精确指纹、SimHash、shingle Jaccard
│  ├─ pipeline.py           process_stub / ingest / build_graph / reset
│  ├─ store.py              建表、统计、三种图导出（json / cypher / graphml）
│  ├─ kg/                   rule.py（离线基线） llm.py（OpenAI 兼容） kggen.py（第三方）
│  │   └─ base.py           抽取器协议 + 中英谓词归一 + 篇内去重
│  ├─ evaluate.py           gold 打分（strict / relaxed）+ 报告生成
│  └─ compare.py            多后端对照，缺 key 不中断
├─ tools/                   辅助脚本（不入主链路，见 §10）
├─ .venv/                   主环境（requirements.txt 前 6 项）
├─ .kgvenv/                 KGGen 隔离环境（kg-gen + pyvis，约 1.5 GB，可重装）
├─ data/
│  ├─ fixtures/             8 篇合成新闻 HTML + index.json（勿当真实新闻引用）
│  ├─ raw/                  抓取快照 *.html
│  ├─ md/                   统一 Markdown *.md（当前 126 篇，见 §7.1）
│  ├─ corpus/manifest.json  32 篇冻结英文语料的元数据清单
│  └─ gold/                 gold 集：corpus.csv / corpus_zh.csv / fixtures.csv + parts/
├─ docs/REPORT.md           实验报告正文（三方对照的完整分析）
└─ out/                     运行产物（数据库、图、对照表、日志）
```

---

## 3. 执行流程

### 3.1 全景

```
                    ┌──────────────── src/newskg/sources ────────────────┐
  fetch  ────────►  │ gdelt / guardian / rss / eastmoney-rss / ths / …   │
                    │   └─ FilteredSource：① 时间窗  ② 股票相关性        │
                    └───────────────┬────────────────────────────────────┘
                                    │ list[ArticleStub]  ← 只有 URL + 元数据
                                    ▼
                 process_stub：限速抓 HTML → trafilatura 抽正文 → markitdown 转 Markdown
                                    │ 落盘 data/raw/<id>.html、data/md/<id>.md
                                    ▼
                 dedup：URL 规范化 + sha1 精确指纹 + SimHash/Jaccard 近似判重
                                    ▼
                             articles 表（duplicate_of 标出重复篇）
                                    ▼
  build  ────────►  kg/{rule, llm, kggen}：读 data/md 正文 → 抽三元组 → 谓词归一
                                    ▼
                 triples 表 + runs 台账  ──►  v_triples 视图（按 (S,P,O) 聚合 support）
                                    ▼
  export ────────►  graph.json / graph.cypher / graph.graphml / graph.html
                                    ▼
  eval / compare ►  strict & relaxed P/R/F1 → comparison.md / comparison.csv
```

两条命令链，不要混：

- **采集链**（`fetch`）：发现 → 抓取 → 转换 → 去重 → 入库，写 `articles` + `data/md`。
- **建图链**（`build` / `compare`）：只读 `data/md`，写 `triples` + `runs`。**它不联网、不抓页面**，所以同一个库可以换后端反复重跑。

### 3.2 阶段 0：启动与配置

`config.py` 在 import 时就做三件事：确定工程根目录（`parents[2]`）、加载 `.env`、实例化 `LLMSettings` / `RunSettings`。之所以在 import 时加载 `.env`，是因为 dataclass 的默认值在类定义时求值，晚一步就来不及了。

### 3.3 阶段 1：发现（discover）

| 步骤 | 代码 | 说明 |
| --- | --- | --- |
| 解析源名 | `build_source(name)` | 支持单个源、逗号分隔、预设 `cn-finance` / `all`；名字大小写不敏感 |
| 合并多源 | `MultiSource.discover` | 每源先取 `max(limit//n, 5)` 条，按规范化 URL 去重，再按发布时间倒序截断 |
| 过滤 | `FilteredSource.discover` | 先向底层多要 8 倍候选，再依次过时间窗、股票相关性，最后截到 `limit` |

`FilteredSource` 是**装饰器式包装**：抓取、转换、抽取、入库完全不知道过滤发生过，换源也不用重写过滤逻辑。

两条过滤的语义（容易误解，单独说明）：

- **`--days` 是滚动时间窗**：以「运行的那一刻」为基准往前推 N 天，**不是**按文章自身日期分组。可带小数，`--days 0.5` 就是 12 小时。源没给发布时间的条目默认丢弃（`--keep-undated` 可保留），否则无法判断是否落在窗口内；时间戳明显来自未来 6 小时以上的一律丢（多半是解析错）。
- **`--topic stock` 只认三种证据**：A 股代码（`60x/68x/00x/30x/43x/8xx/92x`）、中国股市专有词汇（`A股`/`涨停`/`上会`/`定增`/`北向资金`…）、源站自带的 A 股个股代码。泛金融词（`股票`/`基金`/`IPO`）单独命中**不算**，否则「美股恐慌指数飙升」也会混进来。日志会分别报「超窗丢弃」「无时间」「只命中泛金融词」三类数量。

输出统一契约：`ArticleStub(source, url, title, published_at, lang, license, extra)`，其中 `published_at` **一律是 ISO 8601 UTC 字符串**（中文本地时间由各适配器自己换算），因为下游的时间窗比较只认这个格式。

### 3.4 阶段 2：抓取 + 正文抽取 + 规范化

`pipeline.process_stub()` 把一条 stub 变成可入库的 `Article`：

1. **定 ID**：`canonicalize_url(url)`（去掉 `utm_*`、`fbclid`、片段、默认端口、末尾斜杠）→ `article_id = sha1(canonical)[:16]`。所以同一篇文章无论被多少个源发现，ID 都相同，天然幂等。
2. **取 HTML**：三路分支——`fixtures` 读本地文件；Guardian 直接用 API 返回的 `bodyText`（省一次请求）；其余走限速抓取（`RateLimiter` 按域名 1 秒间隔，UA 可配，`requests` 猜错编码时用 `apparent_encoding` 兜底）。
3. **抽正文**：`trafilatura.extract_metadata` 拿标题/作者/日期（日期归一到 UTC）；`trafilatura.extract(output_format="html")` 拿干净 HTML 片段，同时用 `text` 输出拿纯文本（算指纹、兜底）。
4. **转 Markdown**：`html_fragment_to_markdown` 交给 markitdown；markitdown 缺席时退回内置的朴素转换（保段落与标题层级）。
5. **中文页面壳清洗**：只对含汉字的文本生效，按行剔除导航条、免责声明、备案号、页脚栏目。同花顺一条快讯能从 400+ 字的壳降到 54–267 字的真实正文。
6. **落盘**：`data/raw/<id>.html` 留原样；`data/md/<id>.md` 写成「YAML front matter + 正文」，字段固定为 `article_id / source / url / canonical_url / title / published_at / lang / license / authors / content_sha1 / word_count / extractor`。

**为什么两段式（trafilatura → markitdown）**：markitdown 的 `HtmlConverter` 会转换整个 `<body>`，导航栏、cookie 横幅、侧栏、页脚、「相关阅读」全部进 Markdown。用真实 BBC 文章实测：

| 处理方式 | 字符数 | 约 token |
| --- | --- | --- |
| 原始 HTML | 471,833 | — |
| 整页丢给 markitdown | 24,649 | ≈6,160 |
| **trafilatura 抽正文 → markitdown** | **5,039** | **≈1,260** |

差 4.9 倍，而且站点模板越复杂差距越大。markitdown 真正的战场是 PDF / DOCX / PPTX / XLSX（研报、公报、财报附件），入口是 `convert.file_to_markdown()`，和新闻走同一条下游。

正文为空（付费墙、反爬页、纯 JS 渲染）时抛异常，该篇计入「失败」并跳过，不会污染正文表。

### 3.5 阶段 3：去重 + 入库

只用 SimHash 会翻车：实测两篇 BBC 关于同一场中期选举的**不同**报道，汉明距离只有 5（在阈值 6 之内），会被直接判成重复。所以判定是**两道闸门**：

```
content_sha1 完全相同                     → 重复（精确命中）
汉明距离 ≤ 6  且  3 词 shingle Jaccard ≥ 0.55  → 重复（近似命中）
```

`dedup._tokens()` 同时切拉丁词与**中文二元字组**，否则中文文章的 SimHash 会退化成全 0，转载稿判不出来。

判定结果写进 `articles.duplicate_of`（指着被保留的那篇），`v_triples` 视图会自动排除重复篇。阈值可用 `NEWSKG_DEDUP_HAMMING` / `NEWSKG_DEDUP_JACCARD` 调整。

### 3.6 阶段 4：抽取建图

`pipeline.build_graph()` 的选片逻辑是整条流水线里最容易踩坑的一处：

```sql
SELECT * FROM articles a
WHERE (a.duplicate_of IS NULL OR a.duplicate_of = '')
  AND NOT EXISTS (SELECT 1 FROM runs r
                  WHERE r.article_id = a.article_id AND r.extractor = ?)
```

- 按 **extractor** 去重，不是按文章去重——否则第二个后端会因为「没有未处理文章」而一条都跑不出来，对照实验无从谈起。
- `compare` 会传 `only_ids=set(gold)`，把范围限制在「有 gold 的文章」：没标注的文章跑了也不进指标，对按 token 计费的后端就是纯浪费。
- `runs` 台账**无条件写入**（抽到 0 条也算处理过），否则 0 产出的文章每次都会重跑 —— 用 LLM 时等于重复付费。
- 每篇单独 `commit`：LLM 一轮可能几十分钟，中途崩掉不该把已付费的结果一起丢掉。

三个后端的行为差异见 §6。

### 3.7 阶段 5：存储模型

四张结构，全部在 `store.SCHEMA` 里：

| 对象 | 作用 | 关键点 |
| --- | --- | --- |
| `articles` | 文章表 | `article_id` 主键（= 规范化 URL 的 sha1 前 16 位），带 `duplicate_of`、`simhash`、`markdown_path` |
| `triples` | 三元组实例表 | `triple_id = sha1(S\|P\|O\|article_id\|extractor)[:20]`，**主键含 extractor** |
| `runs` | 抽取台账 | 主键 `(article_id, extractor)`，记 `n_triples` 与时间 |
| `v_triples` | 聚合视图 | `support = COUNT(DISTINCT article_id)`，另给 `first_seen` / `last_seen` / `sources` |

两个设计点值得记下来，都是踩过的坑：

- **主键为什么必须含 extractor**：最初是 `sha1(S|P|O|article_id)`，`llm` 跑完后把它抽到的、与 `rule` 重合的那一条覆盖成了自己的记录，`rule` 的 F1 从 0.80% 直接变成 0——看起来像「规则后端一条都没抽对」。
- **`support` 为什么必须 `COUNT(DISTINCT article_id)`**：三个后端跑同一篇会各写一行，用 `COUNT(*)` 会把「同一篇文章被抽了三遍」误算成「三篇文章互相印证」。

视图每次连接都 `DROP` 再 `CREATE`：`CREATE VIEW IF NOT EXISTS` 不会更新已存在的定义，改了 SQL 却看不到效果；视图不存数据，重建没有代价。

### 3.8 阶段 6：导出

| 产物 | 函数 | 格式要点 |
| --- | --- | --- |
| `graph.json` | `export_json` | `nodes[{id,name}]` + `edges[{source,target,predicate,confidence}]`，`id = sha1(lower(name))[:16]` |
| `graph.cypher` | `export_cypher` | 先 `MERGE` 节点再建边，谓词大写化做关系类型，可直接粘进 Neo4j Browser |
| `graph.graphml` | `export_graphml` | networkx `DiGraph`，节点带 `label`、边带谓词与时间，可拖进 Gephi / yEd |
| `graph.html` | `tools/view_graph.py` | pyvis 渲染，节点可拖拽、悬停看关系名；vis.js 内联，断网也能打开 |

### 3.9 阶段 7：评估与对照

**双口径**，缺一不可：

| 口径 | 匹配条件 | 衡量什么 |
| --- | --- | --- |
| `strict` | `(主语, 谓词, 宾语)` 全等 | 连关系词都抽对了 |
| `relaxed` | 只看 `(主语, 宾语)` | 至少把这对实体连起来了 |

为什么要两个：规则后端的谓词来自封闭模板，LLM 的谓词是自由生成的，只比 strict 会把「说对了同一件事、用词不同」判成错。**两个口径的差值本身就是一条结论**——实体配对能力和关系词自由度是两回事。

评分前两侧都做归一（`norm()` 小写去冠词 + `normalize_predicate()` 同义收敛），否则 `based_in` / `headquartered_in` / `based in` 会互相扣分，把三个后端的真实差距淹没在噪声里。聚合同时给 micro（混在一起算）与 macro（先算每篇再平均）。

`compare.run()` 的健壮性约定：某个后端缺依赖、缺 key、调不通时**只记 `unavailable` / `failed` 和原因，不中断整场对照**——课程复现不该因为评审没配 key 就看不到结果。耗时只在真的产生了新结果时写入 `out/timing.json`，避免第二次跑（缓存命中 0 秒）把 0.0s 冒充成真实成本。

### 3.10 阶段 8：报告

`demo` 走 `evaluate.write_report()` 生成 `out/report.md`；`compare` 走 `compare.write_markdown()` 生成 `out/comparison.md`（含「怎么读这张表」一节：谁最高、relaxed 比 strict 高多少、精确率为何只是下界）。

---

## 4. 命令速查

`run.cmd` 之后跟子命令；`--db` 写在子命令前后都行。

| 命令 | 作用 |
| --- | --- |
| `run.cmd init` | 建目录与空库 |
| `run.cmd demo [--backend rule]` | 离线一键：样例 → 建图 → 导出 → 统计 → 评估 → 报告 |
| `run.cmd fetch --source X --query "..." --limit N --days D` | 抓取并入库（可选 `--topic stock`、`--keep-undated`） |
| `run.cmd build --backend rule\|llm\|kggen [--limit N]` | 抽三元组建图 |
| `run.cmd compare --backends rule,llm,kggen [--gold P] [--limit N]` | 多后端对照实验 |
| `run.cmd eval --backend X [--gold P]` | 单后端打分，输出 JSON |
| `run.cmd export [--format all\|json\|cypher\|graphml] [--out DIR]` | 导出图 |
| `run.cmd stats [--json]` | 规模、来源、高频实体、高频谓词 |
| `run.cmd dump [--limit N]` | 打表看三元组（按 support 排序） |
| `run.cmd export-corpus [--out P]` | 导出语料清单 `data/corpus/manifest.json` |
| `run.cmd import-corpus [--manifest P]` | 从清单重建文章表，**不联网** |
| `run.cmd reset [--keep-articles]` | 清空三元组 / 台账（可选保留文章） |

不用 `run.cmd` 时（cmd 与 PowerShell 的写法不同）：

```bat
:: cmd
set PYTHONPATH=src
.venv\Scripts\python.exe -m newskg.cli --db out\corpus.sqlite3 stats
```
```powershell
# PowerShell
$env:PYTHONPATH="src"
.\.venv\Scripts\python.exe -m newskg.cli --db out\corpus.sqlite3 stats
```

`-m newskg.cli` 里的 `-m` 是必需的：`newskg` 是包，`cli.py` 里用的是相对导入，直接 `python src\newskg\cli.py` 会报 `ImportError`。

### 常用组合

```bat
:: 1. 抓 10 篇 RSS 新闻（BBC / 半岛 / NPR）
.\run.cmd fetch --source rss --limit 10 --days 2
.\run.cmd build --backend rule
.\run.cmd export --format all

:: 2. 中国股票新闻，最近 2 天，只要股票相关
.\run.cmd fetch --source cn-finance --limit 10 --days 2 --topic stock
.\run.cmd build --backend llm

:: 3. 用冻结语料复现对照实验（先重建，再跑）
.\run.cmd import-corpus --db out\corpus.sqlite3
.\run.cmd --db out\corpus.sqlite3 compare --backends rule,llm
```

---

## 5. 新闻源

`--source` 支持单个源、逗号分隔、预设名：

| 源 | 要 key | 给什么 | 适合 |
| --- | --- | --- | --- |
| `fixtures` | 否 | 本地合成 HTML（8 篇） | 断网演示、可复现实验 |
| `rss` | 否 | 标题 + 链接（正文再抓） | 补漏、盯固定栏目 |
| `gdelt` | 否 | 250 条 URL/次，15 分钟更新，65 语种 | 主题追踪、跨源同一事件 |
| `guardian` | 是（免费） | 全文 + 元数据，500 次/天 | 需要干净全文时最省事 |
| `eastmoney-rss` | 否 | 东方财富 RSS，**整篇文章** | 中文财经首选 |
| `eastmoney` | 否 | 东方财富 7×24 JSON 快讯 | 时效性快讯 |
| `ths` | 否 | 同花顺 7×24 JSON，带 `stock` 个股字段 | 股票相关性最干净的信号 |
| `sina-fin` | 否 | 新浪财经滚动 JSON，偏美股/宏观 | 多源覆盖 |

预设：`cn-finance`（= `eastmoney-rss,eastmoney,ths,sina-fin`）、`all`（= 全部网络源）。

**中文财经源是逐个实测筛出来的**（2026-11）。公开接口大多已废弃：和讯 RSS / 中金在线 502；证券之星 / 第一财经 / 环球网财经 / 金十 404；中国证券网 SSL 错、中证网超时；雪球返回 WAF 挑战页；财联社要签名算法；36 氪 / 华尔街见闻 / 虎嗅返回 HTML 或 404。活下来且能给发布时间的只有上表四个，覆盖三家发行人、三种数据结构（RSS + 两种形状的 JSON），所以只实现这四个，不写死掉的。

已知限制：这些 7×24 接口本身是综合快讯，没有「A股」频道；东方财富的 `column` 参数服务端基本忽略（344–356 返回同一批内容），所以股票相关性只能靠 §3.3 的两道过滤，而不是靠源头分流。实测 96 条候选里能筛出 8 条真正与中国股市相关的。

GDELT 按出口 IP 限流，实测会返回 `429`；`http_get` 一共尝试 3 次，失败后依次等 5 秒、10 秒再试，仍失败会给出明确提示（而不是让整轮抓取白跑）。

---

## 6. 抽取后端

### 6.1 三个档位

| 后端 | 原理 | 依赖 | 成本 | 质量 |
| --- | --- | --- | --- | --- |
| `rule` | 正则 + 动词模板表（英文 12 条；中文 19 组动词 + 机构后缀白名单） | 无 | 0 | 只认模板句式，真实新闻上几乎失效 |
| `llm` | 单次调用，强制 JSON 输出，最多 25 条 | 任意 OpenAI 兼容端点 | 按 token | 最好 |
| `kggen` | KGGen（NeurIPS'25），自带实体聚类 | `kg-gen` + litellm + dspy | 按 token + 依赖重 | 关系描述完整，但与短谓词 gold 对不齐 |

三者的产出落在**同一张表**（`triples.extractor` 区分），所以 `stats` / `dump` / `eval` / `compare` 都能直接混看或分组比。

`rule` 存在的意义不是「能用」，而是**基线**：没有它就无法回答「LLM 到底带来了多少提升」。它在 8 篇配套样例上 F1 = 97.44%，在 32 篇真实新闻上 0.80%——这两个数字必须放在一起看才有意义。

### 6.2 LLM 后端

用裸 `requests` 而不是 openai SDK，是为了兼容一切「OpenAI 形状」的服务：DeepSeek、OpenCode、Ollama、vLLM、LM Studio、one-api 网关都行，只改三个环境变量。

本地 Ollama 示例：

```bat
set NEWSKG_LLM_BASE_URL=http://127.0.0.1:11434/v1
set NEWSKG_LLM_MODEL=qwen2.5:14b
```

**OpenCode Go 套餐有个坑**：不能走 `/inference/v1`（那是按量付费，会返回 402 余额不足），必须走带 `/go/` 的路径，并且**必须带 `x-opencode-session` 头**，否则：

```
400 MissingSessionID: Request is missing x-opencode-session and cannot be routed efficiently
```

这个头的值是一个**客户端生成的 UUID**，只用于路由，不是密钥；填成 API key 会得到另一个更容易误判的错（`400 Upstream request failed: this feature is not yet supported by atlas`）。代码里已处理：`base_url` 含 `/go/` 时自动生成 UUID 并发这个头。

错误码直接抛人话，不吐 traceback：`401` key 无效、`402` 余额不足、`429` 限流（带 `retry-after`）——这三种重试都没意义。

### 6.3 KGGen 后端（三个坑）

`kg-gen` 依赖 litellm + dspy + torch，装完约 1.5 GB，**装在隔离目录**里：

```bat
.\.venv\Scripts\python.exe tools\_pip_in_sandbox.py install --target .kgvenv kg-gen
```

装好后用 `run-kggen.cmd` 一键跑（它把下面这些环境变量都设好、并先起转发器）。三个坑：

1. **Go 网关要 `x-opencode-session`，而 litellm 不会发这个头。**
   解决办法是在前面放一个本地转发器 `tools/go_proxy.py`，把请求补上头发给 Go；抽取器只需把 base_url 指向 `http://127.0.0.1:8766/v1`。
2. **构造参数名不一致**：0.4.0 是 `api_base`，早期版本是 `base_url`。适配器两个都试，遇到 `TypeError` 自动回退。
3. **NLTK 会尝试下载 punkt 到 `%APPDATA%`**，沙箱里写不进去。把 `NLTK_DATA` 指向工程内的 `.nltk_data/` 即可。

```bat
:: 手动跑：开两个终端
:: 终端 1：本地转发器（给 Go 网关补 session 头）
.\.venv\Scripts\python.exe tools\go_proxy.py
:: 终端 2：跑三方对照
.\run-kggen.cmd
```

还有一个不是坑但会浪费钱的坑：**推理模型会把 token 全烧在 reasoning 上**。用 deepseek 跑 KGGen，10 篇里 6 篇返回 `text: None`，dspy 解析直接失败；提到 `max_tokens=8192` 也没用（新增额度继续用于推理）。所以 `run-kggen.cmd` 指定了不产生 reasoning 的模型，单篇从 126 秒 0 条变成 12.7 秒 36 条。

### 6.4 谓词归一：中文和英文各一张表

一个中文三元组如果写成 `国务院国资委 | held | 座谈会`，实体是中文、谓词是英文，这张图和 gold 永远对不上——评出来的分数只是在量「翻译差异」，不是抽取质量。所以谓词按**文章语言**分流：

| 文章语言 | 谓词形态 | 例子 |
| --- | --- | --- |
| 英文 | 小写 `snake_case` | `acquired` / `located_in` / `signed_with` |
| 中文 | 2–4 个汉字动词 | `收购` / `中标` / `签订` / `增持` |

判语言看**正文汉字占比**（>15% 判中文），而不是只信 `lang` 字段——中文站把 `lang` 标成 `en` 的情况很常见。也可用 `NEWSKG_PREDICATE_LANG=zh|en` 强制指定。

中文侧由 `kg/base.py` 的 `PREDICATES_ZH` 收敛 47 组规范谓词，查表前先剥时态词（`了/中/过`）和情态词（`拟/将/已/完成/被`），再剥尾部名词（`股权`/`协议`）：

| 原始写法 | 归一结果 |
| --- | --- |
| `收购了` / `拟收购` / `完成收购` / `收购股权` | `收购` |
| `签署` / `订立` | `签订` |
| `定向增发` | `定增` |
| `出任` / `就任` | `担任` |

但**语义不同的不合**：`收购` ≠ `并购`、`增资` ≠ `定增`、`控股` ≠ `参股`——合并了会让 gold 里本应区分的关系统计成一个，指标虚高。

`rule` 后端也补了中文模板，但中文没有空格和大小写，靠正则切实体必然切出「项目计划总」这类碎片，所以加了**机构后缀白名单**（主语必须以「公司/集团/股份/银行/交易所…」结尾才认）换精度：召回很低，但进图的都是像样的机构名。实测 8 篇 A 股新闻只抽出 4 条，和英文侧 32 篇只出 3 条是同一个量级——这正是模板法的天花板。

---

## 7. 数据与产物

### 7.1 `data/`

| 路径 | 内容 | 现在有多少 |
| --- | --- | --- |
| `fixtures/` | 8 篇合成新闻 HTML + `index.json` | 8 篇（**合成文本，不是真实新闻**） |
| `raw/` | 抓取快照 `*.html` | 126 个 |
| `md/` | 统一 Markdown `*.md` | 126 篇 |
| `corpus/manifest.json` | 冻结语料的元数据清单（离线重建依据） | 32 篇 |
| `gold/corpus.csv` | 英文 gold 三元组 | 246 条 / 20 篇 |
| `gold/corpus_zh.csv` | 中文 gold 三元组 | 46 条 / 5 篇 |
| `gold/fixtures.csv` | 样例集 gold | 20 条 / 7 篇 |
| `gold/parts/*.json` | 逐篇标注原文（溯源用） | 20 个 |
| `gold/parts_zh/*.json` | 中文逐篇标注 | 5 个 |

> `data/md` 里 126 篇 ≠ manifest 的 32 篇：多出来的是中文财经抓取与其他试跑留下的正文（`out/cn_gold.sqlite3` 等库引用的就是它们），manifest 只登记冻结的英文语料。

### 7.2 `out/`

| 文件 | 来源 | 说明 |
| --- | --- | --- |
| `corpus.sqlite3` | `import-corpus` + `compare` | 32 篇英文语料 + 三个后端共 1358 条三元组实例 |
| `cn_gold.sqlite3` | `cn-finance` 抓取 + `compare` | 73 篇中文快讯，`llm` 89 条 / `rule` 4 条 |
| `demo.sqlite3` | `demo` | 8 篇样例、19 条三元组 |
| `comparison.md` / `.csv` | `compare` | 对照总表 + 机器可读版本 |
| `report.md` | `demo` | 自动生成的实验报告 |
| `graph.json` / `.cypher` / `.graphml` | `export` / `demo` | 三种图格式 |
| `graph.html` / `graph_zh.html` | `tools/view_graph.py` | 可交互图（断网可开） |
| `timing.json` | `compare` | 各后端首次真实耗时 |
| `*.log` | 手工重定向 | KGGen / LLM 跑批日志 |

### 7.3 语料冻结与离线重建

语料已经冻结在仓库里，**断网也能复现实验**：

```bat
:: 把当前库的文章清单导出成 manifest（配合 data/md 即可重建）
.\run.cmd export-corpus --db out\corpus.sqlite3

:: 反过来：从 manifest 重建文章表
.\run.cmd import-corpus --db out\corpus.sqlite3
```

`import-corpus` 只写 `articles` 表，三元组要重新跑 `build` / `compare`。

### 7.4 gold 集

格式固定四列：`article_id,subject,predicate,object`（UTF-8 with BOM 也能读）。

标注规则（英文、中文一致，只有谓词形态不同）：

- 只收**文中明确写出**的关系：一句话直接支持，不推理、不补常识。
- 主语宾语必须是文中出现的具名实体，不许出现代词。
- 不收言语转述（`said` / `told` / `according to`）——那是消息来源，不是事实边。
- 谓词写规范形态（`收购` 而不是「收购了」「签署协议」），归一化能兜住大部分写法，但 gold 本身规范才让报错好看、结论好读。

维护流程：逐篇标注写 `data/gold/parts/<article_id>.json`（`{"article_id": "...", "triples": [{"subject","predicate","object"}, ...]}`），再合并成 CSV：

```bat
:: 只统计，不写文件（打印条数、每篇分布、谓词分布）
.\.venv\Scripts\python.exe tools\merge_gold.py

:: 写 data/gold/corpus.csv
.\.venv\Scripts\python.exe tools\merge_gold.py --write

:: 维护中文 gold
.\.venv\Scripts\python.exe tools\merge_gold.py --parts data\gold\parts_zh --out data\gold\corpus_zh.csv --write
```

`merge_gold.py` 负责去重、丢掉空字段与自环、并把谓词送进 `normalize_predicate`。

---

## 8. 实验结果摘要

完整分析见 [docs/REPORT.md](docs/REPORT.md)，这里只放结论表。

**英文语料**（32 篇 BBC/半岛新闻，20 篇有 gold，共 246 条）：

| 后端 | 底层模型 | 产出 | Strict P | Strict R | Strict F1 | Relaxed F1 | 耗时 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `rule` | 无 | 3 条 | 33.33% | 0.41% | **0.80%** | 0.83% | 0.3 s |
| `llm` | deepseek-v4.1-flash | 475 条 | 18.53% | 35.77% | **24.41%** | 35.90% | 883 s |
| `kggen` | claude-haiku-5-5 | 772 条 | 6.09% | 19.11% | **9.23%** | 27.98% | 194 s |
| `llm`（同模型对照组） | claude-haiku-5-5 | 405 条 | 19.51% | 32.11% | **24.27%** | 36.11% | 126 s |

**中文语料**（A 股快讯，5 篇有 gold，共 46 条）：

| 后端 | 产出 | Strict P | Strict R | Strict F1 | Relaxed F1 | 耗时 |
| --- | --- | --- | --- | --- | --- | --- |
| `rule`（中文模板） | 4 条 | 0.00% | 0.00% | **0.00%** | 0.00% | 0.1 s |
| `llm`（deepseek-v4.1-flash） | 89 条 | 21.35% | 41.30% | **28.15%** | 36.09% | 112 s |

**样例集**（8 篇合成，`rule` 后端）：19 条三元组，strict P=100%、R=95%、F1=97.44%。

五条要点：

1. **模板法的天花板与语种无关。** 英文 0.80%、中文 0.00%，同一个结论：真实新闻用叙事体，「The company has been accused of…」不构成 `(实体)(动词)(实体)` 的连续结构。
2. **换模型几乎没用，换方法才有用。** deepseek → claude：F1 24.41% → 24.27%（−0.14 pp）；手写 prompt → KGGen（同一模型）：24.41% → 9.23%（−15.04 pp）。原因在谓词长度：`llm` 平均 9.8 字符，`kggen` 平均 21.6 字符（`facilitated_deportations_by_administration_of`），strict 口径下几乎不可能命中。
3. **那个 18.53% 的精确率是假的。** 抽查 Palantir 一篇（gold 16 条 / 产出 25 条 / 命中 5 条），未命中的 20 条里**人工判定 19 条其实正确**——gold 标的是「最重要的 16 条」，不是全文所有关系。按人工判定，这篇真实精确率约 (5+19)/25 ≈ 96%。**用非穷尽 gold 评价开放抽取，精确率必然被系统性低估，而且产出越多的系统被罚得越狠**，所以要读 Recall。
4. **中文并不比英文难。** strict F1 28.15% vs 24.41%，relaxed 36.09% vs 35.90%，两边几乎一样。瓶颈不在语言，在「什么算一条该抽的关系」。
5. **严格匹配被实体写法差异吃掉。** 中文抽查里，`宏盛华源 | 中标 | 国家电网项目` 对 `宏盛华源 | 中标 | 国家电网有限公司` 被判错，但事实是对的。同一个坑在两种语言里各踩了一次，这也是 relaxed 口径存在的理由。

---

## 9. 环境变量

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `NEWSKG_LLM_BASE_URL` | `https://api.deepseek.com/v1` | OpenAI 兼容端点根地址 |
| `NEWSKG_LLM_API_KEY` | 空 | LLM key（`llm` 后端必需） |
| `NEWSKG_LLM_MODEL` | `deepseek-chat` | 模型名 |
| `NEWSKG_LLM_TEMPERATURE` | `0.0` | 采样温度 |
| `NEWSKG_LLM_TIMEOUT` | `120` | 单次请求超时（秒） |
| `NEWSKG_LLM_SESSION_ID` | 空 | `x-opencode-session` 的值；留空时含 `/go/` 自动生成 UUID |
| `NEWSKG_PREDICATE_LANG` | `auto` | 谓词语言 `zh` / `en` / `auto`（按正文汉字占比判） |
| `GUARDIAN_API_KEY` | 空 | Guardian Open Platform key |
| `NEWSKG_RSS_FEEDS` | 空 | 覆盖默认 RSS 列表（逗号分隔） |
| `NEWSKG_POLITE_DELAY` | `1.0` | 同域名最小抓取间隔（秒） |
| `NEWSKG_REQUEST_TIMEOUT` | `25` | 页面抓取超时（秒） |
| `NEWSKG_MAX_BODY_CHARS` | `12000` | 正文入库前截断长度（防 LLM 爆 token） |
| `NEWSKG_DEDUP_HAMMING` | `6` | SimHash 汉明距离阈值 |
| `NEWSKG_DEDUP_JACCARD` | `0.55` | 3 词 shingle Jaccard 阈值 |
| `NEWSKG_USER_AGENT` | 课程用 UA | 抓取时的 User-Agent |
| `NEWSKG_KGGEN_MODEL` | `openai/gpt-4o-mini` | KGGen 用的模型（`run-kggen.cmd` 会覆盖） |
| `NEWSKG_KGGEN_MAX_TOKENS` | `8192` | KGGen 输出上限（太小会被截断，dspy 解析失败） |
| `NEWSKG_KGGEN_BASE_URL` | 空 | KGGen 的端点；配合转发器时指向 `127.0.0.1:8766/v1` |
| `NLTK_DATA` | 空 | 指向工程内 `.nltk_data/`，避免沙箱写 `%APPDATA%` 失败 |

> `config.RunSettings` 里另有 `NEWSKG_MAX_ARTICLES` / `NEWSKG_WINDOW_DAYS` 两个预留项，当前**不生效**——条数与时间窗由命令行 `--limit` / `--days` 决定。

---

## 10. `tools/` 辅助脚本

都不在主链路上，按需用。

| 脚本 | 用途 |
| --- | --- |
| `view_graph.py` | 渲染可交互 `graph.html`：`--db` `--extractor` `--out` `--limit` `--min-degree`（需 `PYTHONPATH=src;.kgvenv`） |
| `provenance.py` | 三层溯源：三元组 → 文章行 → 原文 URL / HTML 快照 / 具体句子。用法 `--db out\cn_gold.sqlite3 --subject 超聚变`，或 `--s 侨源股份 --p 签订` |
| `progress.py` | 看各抽取器处理了多少篇、产出多少条（读 `runs` 台账） |
| `dump_triples.py` | 连文章标题一起打印三元组 |
| `list_articles.py` | 按字数排序列出文章（挑长文建 gold 用） |
| `inspect_article.py` | 看某篇英文文章的 gold 命中/漏抽详情 |
| `inspect_zh.py` | 中文版对照：gold 与 LLM 产出的逐条差异 |
| `peek_gold.py` | gold 条数分布与内容抽查 |
| `merge_gold.py` | `parts/*.json` → `corpus.csv`（`--parts` `--out` `--write`） |
| `pick_event_articles.py` | 从中文库里挑事件型文章（公告/中标/回购/增持…），排除行情播报 |
| `stub_llm.py` | 本地假 OpenAI 端点，验证请求/解析/入库链路，**不花钱** |
| `go_proxy.py` | 本地转发器，给 Go 网关补 `x-opencode-session` 头 |
| `llm_same_model.py` | 同模型对照：用 KGGen 那个模型再跑一遍手写 prompt |
| `_pip_in_sandbox.py` | 受限环境下的 pip 垫片 |

想先验链路再配 key：

```bat
:: 终端 1：起假端点
python tools\stub_llm.py

:: 终端 2：把 base_url 指过去，跑 3 篇
set NEWSKG_LLM_BASE_URL=http://127.0.0.1:8765/v1
.\run.cmd --db out\test.sqlite3 build --backend llm --limit 3
```

假端点固定返回四条候选，其中两条故意做坏（自环、超长实体），用来确认过滤逻辑真的在生效。

---

## 11. 常见问题

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `GDELT 限流了（HTTP 429）` | 按出口 IP 限速，约 5 秒一次 | 等几分钟，或换 `--source rss` / `fixtures` |
| `401` / `402` / `429` | key 无效 / 余额不足 / 限流 | 换 key、充值、等 `retry-after` 秒；这三种重试无意义 |
| LLM 抽取结果全为空 | 模型把 token 烧在 reasoning 上（KGGen 尤其） | 换不产生 reasoning 的模型 |
| Markdown 里全是导航和推荐位 | 直接把整页 URL 丢给 markitdown | 走 `extract_article`（先 trafilatura 再 markitdown） |
| `ImportError: attempted relative import` | 直接 `python src\newskg\cli.py` | 用 `python -m newskg.cli`（或 `run.cmd`） |
| `pip ... PermissionError: whl.metadata` | 受限环境写不了 mkdtemp 目录 | 用 `tools\_pip_in_sandbox.py` |
| `kggen` 返回 `text: None` | 推理模型 + 输出被截断 | 换模型 / 提 `NEWSKG_KGGEN_MAX_TOKENS` |
| `graph.html` 白屏 | pyvis 0.3 的 `in_line` 并没真内联 | 已由 `view_graph._inline_assets()` 手工拼 vis.js；仍白屏就检查 pyvis 包内资源是否存在 |
| 中文正文虚高到 400+ 字 | 页面模板被当成正文 | 已按行清洗（`_strip_zh_chrome`），换站点可补词表 |

---

## 12. 已知局限

1. **实体消歧没做。** `Apple` / `Apple Inc.` 只按小写归并，没有别名词典与上下文消歧。
2. **时间只到天，且用的是文章发布时间**，不是事件发生时间。真实系统需要 `valid_from` / `valid_to` 双时态。
3. **关系类型封闭与开放并存**：`rule` 只有固定模板；`llm` 开放但没有 schema 约束校验。
4. **gold 是模型辅助标注的（最大的效度威胁）**：246 条由标注代理逐篇通读生成 + 作者抽查，与 `llm` 抽取器共享同一模型家族，等于让被测系统参与制定评分标准。严格实验应人工独立标注，或双人标注算 Cohen's κ。
5. **gold 非穷尽**（§8 第 3 条），所以精确率只能当下界读。
6. **主表里 `llm` 与 `kggen` 的底层模型不同**（deepseek vs haiku），这是 KGGen 在推理模型上跑不通导致的，§8 的同模型对照组用来弥补。
7. **`llm` 的召回被输出预算卡住**：每篇稳定产出 25 条上下，正好卡在 prompt 上限，而 gold 平均每篇 12 条——部分是「理解能力」的差距，部分是「产出配额」的差距。
8. **没有增量更新**：按 `article_id` 幂等 upsert，但新增文章不会自动回溯旧文章。
9. **单机 SQLite**：上万条以上要换 Postgres 或图数据库，`export cypher` 就是迁移入口。
10. **样本量小**：20 篇英文 + 5 篇中文，逐篇 F1 波动极大，macro 平均的方差不可忽视。

---

## 13. 版权与合规

- `data/fixtures/` 全部是**合成文本**，不是真实新闻，仅用于演示。
- `data/md/` 与 `data/raw/` 里的真实新闻版权归原站点（BBC / Al Jazeera / 东方财富 / 同花顺 / 新浪财经等），仅为课程复现冻结在仓库内，**不得再分发**。
- GDELT 只提供 URL 与元数据，正文版权归原站点；Guardian 开发者 key 仅限非商业用途。
- 抓取遵守 `NEWSKG_POLITE_DELAY`（默认 1 秒/域名）。发布或商用前必须逐源确认许可。
