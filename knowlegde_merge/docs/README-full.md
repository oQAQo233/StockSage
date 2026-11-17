# 新闻抽取与知识图谱构建（newskg）

把国际与中文**科技**资讯抓下来，抽出正文并统一成 Markdown，再用三种抽取器（规则模板 / 自写 LLM prompt / KGGen）生成 `(主语, 谓词, 宾语)` 三元组，落到 SQLite 图谱，导出成 JSON / Cypher / GraphML / 可交互 HTML，最后对照人工标注的 gold 集做双口径评估。

> **题材已从「中国股票」收敛到「科技」**（2026-11）。不是放弃股票方向，而是先在一个领域把「抓得准、抽得稳」做扎实：科技报道的信息密度高、实体明确（公司/产品/技术/机构）、相关性好判定，比「A 股快讯」更适合作为知识图谱的语料。同一时间语料从**中英混跑收敛到纯中文**（英文源全部移除，见 §5.2），筛选机制从「关键词打分」换成「LLM 打分 + 阈值」，见 §3.3。历史的中文财经四源仍在 git 记录里。

- **不需要联网也能跑通全链路**：`run.cmd demo` 用 8 篇合成样例、0 个 API key，约 3.5 秒产出一份完整报告。
- **三种抽取器的结果落在同一张表**（`triples.extractor` 区分），可以直接横向对照。
- **中文、英文两条链路都支持**：中文文章产出中文谓词（`收购` / `中标` / `签订`），英文产出 `snake_case`（`acquired` / `located_in`）。

三个入口：

| 想做什么 | 命令 | 需要联网 / key |
| --- | --- | --- |
| 先看它长什么样 | `run.cmd demo` | 都不需要 |
| 抓一批科技新闻 | `run.cmd fetch --source tech --limit 10 --days 3` | 联网 + 打分 key |
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
# .env —— 任意 OpenAI 兼容端点都行：DeepSeek / Ollama / vLLM / token-plan 网关
# ① 抽取用（llm 后端）：要遵守谓词表，选强一点的模型
NEWSKG_LLM_BASE_URL=https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1
NEWSKG_LLM_API_KEY=sk-xxxx
NEWSKG_LLM_MODEL=qwen3.7-plus
NEWSKG_LLM_TIMEOUT=300

# ② 科技相关性筛选用：判定「是不是科技新闻」，小模型就够，且便宜
NEWSKG_TOPIC_BASE_URL=https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1
NEWSKG_TOPIC_API_KEY=sk-xxxx
NEWSKG_TOPIC_MODEL=qwen3.8-flash
NEWSKG_TOPIC_THRESHOLD=6

# 可选：Guardian 源用的免费 key（仅限非商业用途）
GUARDIAN_API_KEY=

# 可选：逗号分隔，覆盖默认的三档 RSS
NEWSKG_RSS_FEEDS=
```

> 两组 key 可以指向同一个端点，也可以完全不同（比如抽取用 DeepSeek、筛选用本地 Ollama）。
> 只设 `NEWSKG_TOPIC_*` 里的任意一个，筛选就与抽取彻底解耦；一个都不设则沿用 `NEWSKG_LLM_*`。

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
│  ├─ sources/              发现层：11 个中文科技源（RSS + 列表页）+ 预设 + 两道过滤
│  │   ├─ base.py           Source 协议、带退避的 http_get、按域名限速
│  │   ├─ gdelt.py guardian.py rss.py fixtures.py
│  │   ├─ tech.py           科技 11 源：RSS 适配器 + 分额/去重/合并 + 预设
│  │   ├─ html_list.py      HTML 列表页适配器（新华网、科技日报这类 RSS 已下线的源）
│  │   ├─ topic_llm.py      科技相关性打分：提示词、批量请求、本地缓存
│  │   └─ filters.py        时间窗 + 题材过滤（LLM 打分卡阈值，装饰器式包装）
│  ├─ convert.py            抓 HTML、trafilatura 抽正文、markitdown 转 Markdown
│  ├─ dedup.py              URL 规范化、精确指纹、SimHash、shingle Jaccard
│  ├─ pipeline.py           process_stub / ingest / build_graph / reset
│  ├─ store.py              建表、统计、三种图导出（json / cypher / graphml）
│  ├─ kg/                   rule.py（离线基线） llm.py（OpenAI 兼容） kggen.py（第三方）
│  │   ├─ base.py           抽取器协议 + 中英谓词归一 + 篇内去重
│  │   └─ entities.py       实体归一化：字符层 + 别名表 + 泛化宾语过滤
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
│  ├─ aliases.json          实体别名表（canonical → 别名），手工维护
│  └─ gold/                 gold 集：corpus.csv / corpus_zh.csv / fixtures.csv + parts/
├─ docs/REPORT.md           实验报告正文（三方对照的完整分析）
└─ out/                     运行产物（数据库、图、对照表、日志）
```

---

## 3. 执行流程

### 3.1 全景

```
                    ┌──────────────── src/newskg/sources ────────────────┐
  fetch  ────────►  │ xinhua / stdaily / chinanews / jiemian / zqrb / …（11 个中文源）│
                    │   └─ FilteredSource：① 时间窗  ② LLM 科技相关性打分 │
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
| 解析源名 | `build_source(name)` | 支持单个源、逗号分隔、预设 `tech` / `tech-off` / `tech-media` / `all`；名字大小写不敏感 |
| 合并多源 | `TechFeed.discover` | 每个 feed 各取 `ceil(limit/n)+2` 条（封顶 40），按 URL 去重，再按发布时间倒序合并 |
| 过滤 | `FilteredSource.discover` | 先向底层多要 4 倍候选（送打分最多 60 条），依次过时间窗、题材打分，最后截到 `limit` |

`FilteredSource` 是**装饰器式包装**：抓取、转换、抽取、入库完全不知道过滤发生过，换源也不用重写过滤逻辑。

两条过滤的语义（容易误解，单独说明）：

- **`--days` 是滚动时间窗**：以「运行的那一刻」为基准往前推 N 天，**不是**按文章自身日期分组。可带小数，`--days 0.5` 就是 12 小时。源没给发布时间的条目默认丢弃（`--keep-undated` 可保留），否则无法判断是否落在窗口内；时间戳明显来自未来 6 小时以上的一律丢（多半是解析错）。
- **`--topic tech` 是 LLM 打分卡阈值**，不是关键词匹配。把「什么算科技新闻」写成提示词（判为科技的 6 类核心情形 + 5 类明确不算的情形 + 0-10 分档位锚点），让模型逐条给分并给一句理由，`score ≥ 阈值（默认 6）` 才进入抓取。

#### 为什么把关键词打分换成 LLM 打分

关键词打分在双语科技题材上失效得比财经更明显：**词表追不上新词**（"VLA 模型""光互连""固态电池"今年才成为热点，词表里没有）；**假阳性高**（"芯片法案"是政治新闻也是科技新闻，看标题里有没有"芯片"分不出来）；**中英要维护两套词表**，且中文科技词大量是复合词（"算力集群""端侧推理"），子串匹配既漏又误。

实测对照（**同一批 38 条中英混合候选**，阈值 6，关键词分数与缓存的 LLM 分数逐条比对）：

| 机制 | 阈值 6 保留 | 说明 |
| --- | --- | --- |
| 关键词打分 | **4 / 38** | 几乎没有假阳性，但召回极低：25 条真科技稿被漏掉 |
| LLM 打分 | **29 / 38** | 边界判断可解释（每条带一句理由） |
| 两者一致 | 13 / 38 = **34.2%** | 也就是只在三分之一的条目上判断相同 |

关键词漏掉的典型（括号内是规则分）：`Hacker behind 2022 GTA VI leaks pleads guilty`(0)、`Envoy Gateway 1.9.1 强化安全性并解决棘手的升级问题`(0)、`Uber Eats 重建搜索流水线，端到端延迟降低 50%`(0)、`从自动扩缩容到跨池调度：作业帮如何让 Flink 计算资源节省 30%`(0)、`谷歌 Pics 升级 Nano Banana 2.1 模型`(3)。这些标题里没有一个词在词表里，但人一眼就知道是科技新闻——这正是换 LLM 的理由。

实现要点（`topic_llm.py`）：

- **一次请求打 20 条**：候选只有标题 + 400 字摘要，逐条请求的固定开销（系统提示）会占大头。实测 40 条 ≈ 1.1 万 token，约 0.3 元/千条量级。
- **只打分不筛选**：阈值在 `filters.py` 里卡，便于做阈值敏感性分析（`--topic-threshold 5/6/7/8` 各跑一遍对比）。
- **本地缓存** `out/topic_scores.json`：键是 `sha1(标题+摘要)`。调阈值、重跑、改下游都不会重复付费；已被 git 忽略。
- **失败可降级**：某批解析失败/超时/限流，只把那一批退回关键词粗筛并打警告，不会因为一次 429 丢掉整批数据；`--topic-mode keyword` 可强制全走降级路径（离线可用）。
- **`--topic-desc` 换领域**：领域定义是提示词的一部分，传一段自定义描述就能把同一套 0-10 档位复用到别的领域（如"生物医药"），不必改代码。

日志会报三件事，便于调阈值：分数分布（0-2/3-4/5-6/7-8/9-10 各几条）、过阈值与丢弃条数、以及保留/丢弃各 3 条样例及其理由。

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

#### 实体归一化（`kg/entities.py`）

抽取器每篇独立跑，同一实体会写成不同样子。不合并的后果在图上最直观：`Capcom` 和 `卡普空` 是两个节点，`GTA VI` 和 `Grand Theft Auto VI` 也是，度数被切碎，跨文章共现统计不出来。

归一化分三层，**顺序不能颠倒**：

| 层 | 做什么 | 例子 |
| --- | --- | --- |
| 字符层 | NFKC 全半角、去首尾空白、去包裹的书名号/引号、压缩空白、去尾标点 | `《虚幻引擎》` → `虚幻引擎` |
| 别名层 | 查 `data/aliases.json`（ASCII 不区分大小写） | `卡普空` / `CAPCOM` → `Capcom`；`GTA 6` → `Grand Theft Auto VI` |
| 过滤层 | 丢掉宾语是泛化类别的边、归一后退化成自环的边 | `Anthropic uses AI` 整条丢弃 |

三个设计决定：

- **别名表外置成 JSON**（`data/aliases.json`），不写在代码里。它是知识不是逻辑，补一条别名不该动代码、更不该重跑测试。文件缺失时退化为「只做字符层」，不阻塞流水线。
- **只改主语/宾语，不动谓词**。谓词归一化是 `kg/base.py` 的事，两件事分开才能分别定位问题（是这个实体的写法没合，还是动词没收敛）。
- **泛化宾语的黑名单刻意很短**（AI / 技术 / 产品 / 软件 / 平台 / 系统…）。这类边的危害是让 `AI` 变成度数最高的节点；但黑名单一长就会错杀（`英伟达 供应 芯片` 里的「芯片」是真实关系），所以宁可漏杀。想更激进就自己往 `GENERIC_OBJECTS` 里加。

提示词侧同步加了两条硬规则（`kg/llm.py`）：宾语必须是**具体命名实体**，禁止类别词；不许把来源媒体当主语抽「报道」关系。两道防线是有意重叠的——提示词管「一开始就别抽」，归一化管「已经抽出来的怎么收拾」，因为模型并不总听话。

改名不必重新付费：`renormalize` 子命令直接对库里已有的三元组重算字符串层（`.\run.cmd --db out\tech.sqlite3 renormalize`）。**为什么不重跑抽取**：LLM 不是确定性函数，重跑一遍既花钱又可能得到不同结果，而你只想让 `卡普空` 变成 `Capcom`。

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
| `run.cmd fetch --source X --query "..." --limit N --days D` | 抓取并入库（可选 `--topic tech\|none`、`--topic-threshold`、`--topic-mode`、`--keep-undated`） |
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
:: 1. 抓 20 篇中文科技新闻（11 个源自动分额，LLM 打分卡 ≥6），再建图
.\run.cmd fetch --source tech --limit 20 --days 3 --db out\tech.sqlite3
.\run.cmd --db out\tech.sqlite3 build --backend llm
.\run.cmd --db out\tech.sqlite3 export --format all

:: 2. 只要官方/报社四源（中新网·财经、中新网·要闻、经观、界面），阈值推到 7（更严）
.\run.cmd fetch --source tech-off --limit 15 --days 7 --topic-threshold 7 --db out\tech_off.sqlite3

:: 2b. 只要科技垂媒八源（量子位、Solidot、IT之家、爱范儿、InfoQ、少数派、钛媒体、雷锋网）
.\run.cmd fetch --source tech-media --limit 15 --days 7 --db out\tech_media.sqlite3

:: 3. 阈值敏感性：同一批候选不重复付费（有缓存），只花在跨阈值的新条目上
.\run.cmd fetch --source tech --limit 20 --days 3 --topic-threshold 5 --db out\t5.sqlite3
.\run.cmd fetch --source tech --limit 20 --days 3 --topic-threshold 7 --db out\t7.sqlite3

:: 4. 用冻结语料复现对照实验（先重建，再跑）
.\run.cmd import-corpus --db out\corpus.sqlite3
.\run.cmd --db out\corpus.sqlite3 compare --backends rule,llm

:: 5. 改了 data\aliases.json 之后，就地重写实体名，不重跑抽取（不花钱）
.\run.cmd --db out\tech.sqlite3 renormalize
```

---

## 5. 新闻源

`--source` 支持单个源、逗号分隔、预设名。科技源（11 个，**全部中文**、全部免 key），按权威性从高到低：

| 源 | 站点 | 性质 | 接入方式 | 实测 |
| --- | --- | --- | --- | --- |
| `xinhua_tech` | 新华网·科技 | 国家通讯社（新华社） | **列表页** | 10–12 条/次，日期来自 URL，正文 ~1100 字符 |
| `stdaily` | 科技日报 | 科技日报社（科技部主管） | **列表页** | 10–12 条/次，日期来自 URL，正文 ~620 字符 |
| `chinanews_fin` | 中新网·财经 | 通讯社（中国新闻社） | RSS | 30 条/次，全带 `pubDate`，科技/产业稿多 |
| `chinanews_top` | 中新网·要闻 | 通讯社 | RSS | 30 条/次，全带时间，政策/部委动态 |
| `zqrb` | 证券日报 | 报社（经济日报报业集团） | RSS | 755 条/次，暂无偏移的本地时间（见 §5.3） |
| `jiemian` | 界面新闻 | 报社（上海报业集团） | RSS | 30 条/次，全带时间，持新闻信息服务许可证 |
| `eeo` | 经济观察报 | 报社（济南日报报业集团） | RSS | 4 条/次，全带时间，财经调查见长 |
| `tmtpost` | 钛媒体 | 商业科技媒体 | RSS | 20 条/次，深度稿 2700–3400 字符 |
| `leiphone` | 雷锋网 | 商业科技媒体 | RSS | 20 条/次，AI/机器人/车 |
| `infoq` | InfoQ 中文 | 技术社区媒体 | RSS | 20 条/次，工程/架构 |
| `ifanr` | 爱范儿 | 商业科技媒体 | RSS | 20 条/次，正文 ~8400 字符 |

非科技源仍在，用于对照实验与补漏：

| 源 | 要 key | 给什么 |
| --- | --- | --- |
| `fixtures` | 否 | 本地合成 HTML（8 篇），断网演示用 |
| `rss` | 否 | 任一 RSS（`NEWSKG_RSS_FEEDS` 覆盖） |
| `gdelt` | 否 | 250 条 URL/次，15 分钟更新，65 语种 |
| `guardian` | 是（免费） | 全文 + 元数据，500 次/天 |

预设：`tech`（= 上表 11 个）、`tech-off`（权威 7 个：新华网、科技日报、中新网×2、证券日报、界面、经观）、`tech-media`（垂媒 4 个）、`all`（= `gdelt,guardian,rss,tech`）。`NEWSKG_TECH_FEEDS` 可覆盖 `tech` 的成员。

**英文源已全部移除**（2026-11）：`techcrunch` / `verge` / `mittr` / `wired` / `ieee` 连同 `tech-en` 预设一起删掉，理由见 §5.2。

**四个来源被移除**（2026-11，权威性排序后）：`ithome`（青岛软媒，读者投稿为主）、`qbitai`（北京极客伙伴，编译稿比例高）、`sspai`（深圳烧麦，教程/评测为主）、`solidot`（至顶科技，用户提交+编译）。它们不是不能用，而是**出错后没有编辑责任链**，在「权威优先」的口径下让位。

### 5.1 源是怎么筛的

判据是**三条**，不是「RSS 地址能打开」：

1. 能解析出条目（RSS）或能解析出「标题 + 链接」（列表页）；
2. **条目带时间**——RSS 看 `pubDate`，列表页看 URL 路径里的日期；没有时间，`--days` 时间窗会静默失效，等于把时间过滤关掉；
3. 首条正文能被 trafilatura 抽出 **>400 字符**。

第 2 条是淘汰率最高的一条。被淘汰的候选（2026-11 实测）：

| 候选 | 现象 |
| --- | --- |
| 人民网·IT / 科技 | 100 条且都有日期，但**最新一条停在 2024-07 / 2025-04**（feed 已死） |
| 中国政府网 | 60 条全带时间，但正文页只抽出 **56 字符**（推送的是条例标题） |
| 中国科学报 / 央视网 / 光明网 / 第一财经 / 中国网 / 环球网 / 中国电子报 / 人民邮电报 / 通信世界 / 财联社 / 21 财经 / 上海证券报 / 中国证券报 / 证券时报 / 经济参考报 / 中国网信网 / 中科院 / 中国科协 | RSS 端点 **404 / 连不上**（未提供或已下线） |
| 科学网 / 澎湃 / 观察者网 / 品玩 / 央广网 / 环球时报 | 返回 HTML 而非 feed，且列表页是 JS 渲染（没有服务端日期） |
| 36氪 / 机器之心 / 腾讯科技 | 同上，`/feed` 已改成前端渲染 |
| 新浪·科技 | 有日期但**停在 2018 年** |
| 搜狐·IT | 有日期但**停在 2016 年** |

新华网与科技日报是这一轮**用列表页救回来的**：它们的 RSS 要么 404、要么 300 条零时间戳，但频道首页仍是服务端渲染，且文章 URL 自带日期（`/tech/20261117/<id>/c.html`、`/web/2026-11/17/content_x.html`）。`sources/html_list.py` 就把这条路径做成通用适配器：规则只有「从 URL 读日期」，站点换模板也不受影响，读不出日期的链接直接丢。

### 5.3 两个抓取层的坑（都已修）

**坑 1：不带时区的本地时间。** 证券日报的 `pubDate` 写作 `2026-11-17 09:24:01`，没有 `+0800`。feedparser 按 UTC 解释，时间整体快 8 小时，再被 `within_window` 的 `FUTURE_TOLERANCE`（6 小时）判成「来自未来的解析错误」丢掉——整个源静默归零。修法：原文没有时区标记时，中文源按 `+08:00` 折算（`_iso_from_entry`）。

**坑 2：只有日期、没有时刻的源永远排在最后。** 列表页源只能拿 `YYYY-MM-DD`，折算成 UTC 是当天 16:00Z 之前，正常 RSS 源能精确到分钟。打分池原本取「时间倒序前 60 条」，结果新华网、科技日报一条都进不去——加了源等于没加。修法：`_fair_pool()` 改成**按源轮流取**，每个源先各出 1 条再各出第 2 条，取满后再按时间排序，输出顺序不变。修完后同一批候选里两个新源都进榜（第 19、20 条）。

### 5.2 为什么砍掉英文源

1. **口径统一**：题目要的是中文科技新闻；中英混在同一张图里，实体名一半中文一半英文，`Capcom`/`卡普空` 这类问题只能靠别名表打补丁，不如源头上统一。
2. **时间标签**：英文源（TechCrunch/WIRED 等）本身没问题，但它们的价值在「深度长文」，而中文源在「多源互证」。两者混跑时，英文长文在 20 篇配额里只占 2–3 篇，性价比最低。
3. **可复现性**：英文源大量走 Cloudflare（403/429 波动），中文源这一轮的稳定性明显更好（20/20 抓取成功，0 失败）。

代价也要说清楚：**中文科技媒体的正文普遍更短**（IT之家 200–500 字符），抽出的三元组条数比英文深度稿少；`tmtpost` / `ifanr` / `jiemian` 是当前唯一能提供 2000+ 字符正文的中文源。

已知限制：

- **官方源不等于科技源**：`chinanews_top`、`eeo` 是综合新闻，科技命中率低。实测 60 篇候选里，官方四源贡献 30 篇，只有 9 篇过 6 分阈值——LLM 打分的价值正在这里，别指望 feed 自己分好类。
- **消费电子 ≠ 科技产业**：`ithome` / `sspai` 里混有大量促销、早报、开箱，LLM 打分会把促销打到 3-5 分正常拦掉，但「早报」类聚合文偶尔会以 6 分通过——这类文章的实体密度低，抽出来的三元组偏碎。
- `tech.py` 的 `per_feed` 按 feed 数摊平（`ceil(limit/n)+2`，封顶 40），保证「每个源各出一个头条」而不是「第一个源吃掉全部额度」；入库时 `articles.source` 记的是具体 feed 名（不是预设名），所以按源统计不会被合并抹平。

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

用裸 `requests` 而不是 openai SDK，是为了兼容一切「OpenAI 形状」的服务：DeepSeek、OpenCode、token-plan 网关、Ollama、vLLM、LM Studio、one-api 都行，只改三个环境变量。

本地 Ollama 示例：

```bat
set NEWSKG_LLM_BASE_URL=http://127.0.0.1:11434/v1
set NEWSKG_LLM_MODEL=qwen2.5:14b
```

**同一篇文章，换模型差别很大**（2026-11 实测，一篇中文产品发布稿，同一提示词）：

| 模型 | 三元组数 | 观感 |
| --- | --- | --- |
| `qwen3.7-plus` | 10 | 干净：「TRYX 联名 生化危机」「PANORAMA 展域 归属 TRYX」 |
| `qwen3.8-flash` | 22 | 数量多但噪声大：同一实体中英双语各来一条（`Capcom` / `卡普空`），还有「IT之家 报道 TRYX」这类元信息 |
| `deepseek-v4-pro` | 1 | 过于保守，只留最确定的一条 |

所以默认取平衡点 `qwen3.7-plus`。**注意超时**：6000 词的长稿在 flash 上会踩 120 秒读超时，调大 `NEWSKG_LLM_TIMEOUT`（默认 300 够用）。

**OpenCode Go 套餐有个坑**：不能走 `/inference/v1`（那是按量付费，会返回 402 余额不足），必须走带 `/go/` 的路径，并且**必须带 `x-opencode-session` 头**，否则：

```
400 MissingSessionID: Request is missing x-opencode-session and cannot be routed efficiently
```

这个头的值是一个**客户端生成的 UUID**，只用于路由，不是密钥；填成 API key 会得到另一个更容易误判的错（`400 Upstream request failed: this feature is not yet supported by atlas`）。代码里已处理：`base_url` 含 `/go/` 时自动生成 UUID 并发这个头。另外它有**周额度**，用尽后 `429` 会带一个巨大的 `retry-after`（按秒计的复位时间），此时换端点或等复位。

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
| `aliases.json` | 实体别名表 | 20 组（Capcom/卡普空、GTA VI…），可自行扩充 |

> `data/md` 里 126 篇 ≠ manifest 的 32 篇：多出来的是中文财经抓取与其他试跑留下的正文（`out/cn_gold.sqlite3` 等库引用的就是它们），manifest 只登记冻结的英文语料。

### 7.2 `out/`

| 文件 | 来源 | 说明 |
| --- | --- | --- |
| `corpus.sqlite3` | `import-corpus` + `compare` | 32 篇英文语料 + 三个后端共 1358 条三元组实例 |
| `cn_gold.sqlite3` | `cn-finance` 抓取 + `compare`（历史） | 73 篇中文快讯，`llm` 89 条 / `rule` 4 条 |
| `tech.sqlite3` | `fetch --source tech` | 20 篇中文科技新闻（11 个源注册，其中 9 个有稿件入选） |
| `topic_scores.json` | 打分缓存 | `sha1(标题+摘要)` → 分数/理由/模型；改阈值重跑不再付费 |
| `demo.sqlite3` | `demo` | 8 篇样例、19 条三元组 |
| `comparison.md` / `.csv` | `compare` | 对照总表 + 机器可读版本 |
| `report.md` | `demo` | 自动生成的实验报告 |
| `graph.json` / `.cypher` / `.graphml` | `export` / `demo` | 三种图格式 |
| `graph.html` / `graph_zh.html` | `tools/view_graph.py` | pyvis 渲染的可交互图（各 ~1.4 MB，用完即弃，需要时重跑生成） |
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

> **本节数字都来自改造前的财经/国际语料**（冻结在 `data/corpus` 与 `data/gold`），保留它们是为了「三方对照」这条主线可复现。科技语料（§7.2 的 `tech.sqlite3`）目前只验证了链路通不通，还没有 gold 集，所以**不报 P/R/F1**——见 §12 第 11 条。

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
| `NEWSKG_TOPIC_BASE_URL` | 空 → 回落 `NEWSKG_LLM_BASE_URL` | 题材打分的端点 |
| `NEWSKG_TOPIC_API_KEY` | 空 → 回落 `NEWSKG_LLM_API_KEY` | 题材打分的 key |
| `NEWSKG_TOPIC_MODEL` | 空 → `qwen3.8-flash`（显式配置时）/ 回落 LLM 模型 | 打分模型 |
| `NEWSKG_TOPIC_THRESHOLD` | `6` | 相关性阈值，0-10，低于此分丢弃 |
| `NEWSKG_TOPIC_BATCH` | `20` | 一次请求打多少条 |
| `NEWSKG_TOPIC_TIMEOUT` | `60` | 打分请求超时（秒） |
| `NEWSKG_TOPIC_CACHE` | `out/topic_scores.json` | 打分缓存路径 |
| `NEWSKG_TECH_FEEDS` | 空（= 全部 11 个） | 覆盖 `tech` 预设的成员，逗号分隔注册名 |
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
| `view_graph_lite.py` | **零依赖**版查看器，主环境直接跑：按连通分量做径向布局再货架排布，产出单文件 HTML（悬停看关系、滚轮缩放、拖节点）。`--min-degree 2` 可只看多文章共现的核心子图 |
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
| `429 ... weekly, resets in 5d` | OpenCode Go 是**周额度** | 换端点（见 §6.2），或等复位 |
| 筛选后一篇都不剩 | 阈值太高 / 源本身就没什么科技新闻 | 看日志的分数分布；`--topic-threshold 5` 或 `--topic-mode keyword` 对照一下 |
| 打分日志说「降级为关键词粗筛」 | 没配 `NEWSKG_TOPIC_*` 且 LLM 也没配 | 配 key；或接受降级（`--topic-mode keyword`） |
| 入库后标题都是站点名 | 页面 `<title>` 只有站点名（Solidot 就是） | 已改为**优先用 RSS 标题**；换新源若仍出现，检查该源 entry 是否真带 `title` |
| 前 20 篇里 8 篇来自同一家 | 高产源吃满额度 | 已加单源配额（`SRC_CAP_RATIO = 0.35`）+ 每源保底 1 条 |
| LLM 抽取结果全为空 | 模型把 token 烧在 reasoning 上（KGGen 尤其） | 换不产生 reasoning 的模型 |
| Markdown 里全是导航和推荐位 | 直接把整页 URL 丢给 markitdown | 走 `extract_article`（先 trafilatura 再 markitdown） |
| `ImportError: attempted relative import` | 直接 `python src\newskg\cli.py` | 用 `python -m newskg.cli`（或 `run.cmd`） |
| `pip ... PermissionError: whl.metadata` | 受限环境写不了 mkdtemp 目录 | 用 `tools\_pip_in_sandbox.py` |
| `kggen` 返回 `text: None` | 推理模型 + 输出被截断 | 换模型 / 提 `NEWSKG_KGGEN_MAX_TOKENS` |
| `graph.html` 白屏 | pyvis 0.3 的 `in_line` 并没真内联 | 已由 `view_graph._inline_assets()` 手工拼 vis.js；仍白屏就检查 pyvis 包内资源是否存在 |
| 中文正文虚高到 400+ 字 | 页面模板被当成正文 | 已按行清洗（`_strip_zh_chrome`），换站点可补词表 |

---

## 12. 已知局限

1. **实体消歧只做了「写法层」**：`kg/entities.py` 能合并 `Capcom`/`卡普空`、`GTA 6`/`Grand Theft Auto VI` 这类同指异写，靠的是**手工别名表**，不做上下文消歧——`Apple`（公司）与 `apple`（水果）、`Meta`（公司）与 `meta`（前缀）仍会合并。别名表没覆盖到的写法也不会自动发现。
2. **时间只到天，且用的是文章发布时间**，不是事件发生时间。真实系统需要 `valid_from` / `valid_to` 双时态。
3. **关系类型封闭与开放并存**：`rule` 只有固定模板；`llm` 开放但没有 schema 约束校验。
4. **gold 是模型辅助标注的（最大的效度威胁）**：246 条由标注代理逐篇通读生成 + 作者抽查，与 `llm` 抽取器共享同一模型家族，等于让被测系统参与制定评分标准。严格实验应人工独立标注，或双人标注算 Cohen's κ。
5. **gold 非穷尽**（§8 第 3 条），所以精确率只能当下界读。
6. **主表里 `llm` 与 `kggen` 的底层模型不同**（deepseek vs haiku），这是 KGGen 在推理模型上跑不通导致的，§8 的同模型对照组用来弥补。
7. **`llm` 的召回被输出预算卡住**：每篇稳定产出 25 条上下，正好卡在 prompt 上限，而 gold 平均每篇 12 条——部分是「理解能力」的差距，部分是「产出配额」的差距。
8. **没有增量更新**：按 `article_id` 幂等 upsert，但新增文章不会自动回溯旧文章。
9. **单机 SQLite**：上万条以上要换 Postgres 或图数据库，`export cypher` 就是迁移入口。
10. **样本量小**：20 篇英文 + 5 篇中文，逐篇 F1 波动极大，macro 平均的方差不可忽视。
11. **科技题材还没有 gold 集**（2026-11 改造后）：现有的 246 / 46 条 gold 标注的都是财经与国际新闻语料，`eval` 仍跑在旧语料上。科技语料的评估要重标，否则只能看三元组的**数量与观感**，不能报 P/R/F1。
12. **题材过滤是「有偏」的**：LLM 打分用一套固定提示词，等于把「什么算科技」的判准写死了。它对"芯片法案"这类科技/政治交叉题材的判断会随模型版本漂移，实测同一批候选换模型后边界条目的分数能差 2 分。阈值是一刀切，没有"高置信/待人工复核"的中间带。
13. **打分只看标题+摘要**：标题党或摘要信息量低的文章会被误判（偏低居多）。正文抓下来之后无法回头修正判定——要修正就得重跑抓取。
14. **权威优先的代价是科技密度**（2026-11）：11 个源里 7 个是通讯社/报社，`tech-off` 预设的科技命中率只有三成左右，60 篇候选要靠 LLM 打分才留下 20 篇。另一面是抓取层两个坑（无时区时间、只有日期的源进不了打分池）都已修，但「只有日期」的源永远只能提供到天的粒度，`--days 0.5` 这种细窗对它无效。

---

## 13. 版权与合规

- `data/fixtures/` 全部是**合成文本**，不是真实新闻，仅用于演示。
- `data/md/` 与 `data/raw/` 里的真实新闻版权归原站点（BBC / Al Jazeera / 东方财富 / 同花顺 / 新浪财经 / Solidot / IT之家 / 爱范儿 / TechCrunch / The Verge 等），仅为课程复现冻结在仓库内，**不得再分发**。
- 11 个中文科技源都取各站点公开的 RSS 或频道列表页，只取标题、链接与元数据；正文由本工程按 `NEWSKG_POLITE_DELAY` 礼让抓取，**不绕过任何反爬**（被 405/202/429 挡下的源直接放弃，见 §5）。
- GDELT 只提供 URL 与元数据，正文版权归原站点；Guardian 开发者 key 仅限非商业用途。
- 抓取遵守 `NEWSKG_POLITE_DELAY`（默认 1 秒/域名）。发布或商用前必须逐源确认许可。



