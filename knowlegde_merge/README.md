# NewsKG

**新闻 → 统一 Markdown → 知识图谱 → 带引用的报告。**

抓 11 个中文科技源，用 KGGen 抽 `(主体, 关系, 客体)` 三元组落进一个 SQLite 文件；再从「图上实体」和「段落全文」两路召回，把查询变成一份每句话都带来源编号的五节报告。

- 这是一个**流水线工程**，不是新闻 App。每一步都能单独跑、单独看产物、单独重来。
- 核心目标是**可核对**：报告里每个结论都有 `[n]`，编号能点回原文；抽不到证据时它说"没有"，而不是编一个。
- 只有一个外部依赖：**OpenAI 兼容的大模型端点**（抽取 + 写作）。没有 key 可以抓取、统计、看图，但跑不了 `build` 和 `ask`。

## 能做什么 / 不能做什么

| 能做 | 不能做 |
| --- | --- |
| 抓 11 个中文科技源的正文，统一成 Markdown | 绕过反爬；被 403/405/429 挡下就放弃这个源 |
| 抽三元组建图，导出 json / cypher / graphml | 保证抽取完整：一篇长文出 14~36 条关系，不是全量事实 |
| 一句话查询 → 五节报告，逐句带引用 | 回答语料外的问题：查不到就返回 0 篇，不硬凑 |
| 同一事件的多篇报道合并成一条时间线 | 语义聚类：换措辞、只共享一个实体时仍会拆成两条 |
| 越界引用、对不上原文的数字 → 标出来 | 判断数字**用得对不对**（张冠李戴、口径不一致查不出） |
| 生成单文件可交互图，断网可用 | 大规模：单机 SQLite，上万篇要换 Postgres 或图数据库 |

---

## 1. 跑起来

需要 **Python 3.10+**（开发环境为 3.14 / Windows）。SQLite 需带 FTS5 与 trigram 分词器（3.34+，系统自带的基本都满足）。

```bat
:: 一次性环境：主环境负责抓取/存储/可视化，kg-gen 单独装一个目录
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pip install --target .kgvenv -r requirements-kggen.txt
copy .env.example .env
:: 编辑 .env：NEWSKG_LLM_API_KEY / _BASE_URL / _MODEL
```

`kg-gen` 会拖进 litellm、dspy、pyarrow、sentence-transformers，体积大且互相牵制，所以装进 `.kgvenv` 这个平铺目录而不是主环境；`run.cmd` 把 `src` 和 `.kgvenv` 一起挂到 `PYTHONPATH`。

```bat
:: 1) 真实语料：抓 20 篇 → 抽 8 篇建图 → 导出 → 看图
.\run.cmd fetch --source tech --limit 20 --days 3 --db out\tech.sqlite3
.\run.cmd --db out\tech.sqlite3 build --limit 8
.\run.cmd --db out\tech.sqlite3 export --format all
.\.venv\Scripts\python.exe tools\view_graph_lite.py --db out\tech.sqlite3 --min-degree 2 --out out\graph.html

:: 2) 问一句，拿一份报告
.\run.cmd --db out\tech.sqlite3 ask "小米机器人在具身智能上有什么进展" --top 5 --debug

:: 3) 不联网的端到端演示：8 篇合成样例 → 三元组 → 图（抽取仍要模型）
.\run.cmd demo
```

`run.cmd` 只是包装：设 `PYTHONPATH=src;.kgvenv`、用 `.venv` 里的 Python、参数原样透传。等价于：

```bat
set PYTHONPATH=src;.kgvenv
.venv\Scripts\python.exe -m newskg.cli --db out\kg.sqlite3 <子命令> ...
```

> 必须用 `python -m newskg.cli`，包内部是相对导入；`python src/newskg/cli.py` 会直接报错。

---

## 2. 三块组成

```
        抓取入库                     建图                        问答成文
┌──────────────────────┐   ┌──────────────────────┐   ┌──────────────────────────┐
│ 发现 → 抓正文 → 转 MD │   │ 逐篇 KGGen → 谓词归一 │   │ 实体链接 + 段落检索       │
│  → 去重 → 入 SQLite   │──►│  → 实体归一 → triples │──►│  → RRF 融合 → 事件聚类    │
│ (articles/paragraphs) │   │  → export 图          │   │  → 五节报告 + 引用校验    │
└──────────────────────┘   └──────────────────────┘   └──────────────────────────┘
```

| 环节 | 实现 | 关键取舍 |
| --- | --- | --- |
| 发现 | `sources/`（RSS + 列表页） | 只要免 key、带时间的源；宁可少 10 个源，不要一个时间错乱的源 |
| 过滤 | `sources/filters.py` | 时间窗 + LLM 题材打分（阈值 6）两道闸，先砍量再抓正文 |
| 抓取 | `convert.py`（trafilatura） | 正文 >400 字符才算抓成功；抽不出就记失败，不猜 |
| 统一 | `convert.py`（markitdown） | 一份正文两种表示：HTML→MD 供人读，纯文本供索引 |
| 正文闸 | `quality.py` | 删页脚/推荐列表；只剩许可证与电话的残稿不进库 |
| 去重 | `dedup.py` | URL 规范化 → 精确指纹 → SimHash → shingle Jaccard，四层递进 |
| 抽取 | `kg/kggen.py` | 采用 Kggen |
| 归一 | `kg/entities.py` | 字符层 → 别名表 → 泛化过滤，顺序不可换 |
| 索引 | `textindex.py` | 以**段落**为单位建 FTS5，不索引句子 |
| 检索 | `retrieve.py` | 图路 + 文本路双召回，RRF 只比名次 |
| 成文 | `events.py` + `report.py` | 事实用模板、叙述用模型，两类内容分开生产 |


---

## 3. 数据模型

全部落在一个 SQLite 文件里，`out/` 可整体删除重跑。

| 表 / 视图 | 一行是什么 | 写入者 |
| --- | --- | --- |
| `articles` | 一篇入库文章：标题、来源、URL、发布时间、Markdown 路径、正文、SimHash | `fetch` |
| `paragraphs` | 一篇文章切成的一段（目标 420 字，上限 900），带段序号 | `ask`（首次查询时懒建） |
| `paragraphs_fts` | `paragraphs` 的 FTS5 trigram 索引 | 同上 |
| `triples` | 一条三元组：主体、谓词、客体、置信度、`article_id` | `build` |
| `runs` | 一次抽取/抓取的运行记录，抽到 0 条也写，避免重复付费 | `pipeline` |
| `v_triples` | 三元组 + 文章标题/来源/日期的联表视图 | 建库时创建 |

设计上有三处值得说明：

1. **检索单位是段落，不是句子。** 句子级检索需要句对齐，成本高、中文标点还会把一句话切断；段落保留上下文，又足够短到能塞进提示词。
2. **三元组只记 `article_id`，不记句子。** 句子级溯源看着精确，实测却会挑出"只包含一半信息"的句子（例如只有主体、没有客体），比段落级更误导。报告因此按段摘录、按文章编号引用。
3. **段落与索引由 `ask` 懒建。** 新抓的文章第一次被查询时才切段、建索引，`fetch` 不必为可能没人问的文章付索引成本。

---

## 4. 查询与报告

`ask` 把一句自然语言（20~300 字）变成一份 Markdown 报告。原则是**先把查询落到具体报道与事实上，再让模型组织语言**——不把整库塞进上下文。

```
查询
 ├─ 实体链接：别名表 → 子串 → 前缀（中文 3~6 字 / 英文首词）→ 模糊 0.82
 │     └─ 图路召回：从命中实体出发 2 跳，按距离衰减给文章打分
 ├─ 文本路召回：FTS5(trigram) 段落检索，两字词用 LIKE 兜底
 │     └─ 段落得分 = Σ (词长/2) × min(出现次数, 3)
 ├─ RRF 融合（k=60）→ 相关性下限（图路命中，或文本分 ≥ 首名 25%）
 ├─ 覆盖度守门：查询最具体的词全库都没有 → 返回 0 篇
 ├─ 事件聚类（并查集）
 └─ 证据包 out/reports/*.evidence.json
        └─ 分节配置 → 背景 → 主体 → 时间线 → 趋势 → 摘要（最后写）
```

```bat
:: 只检索，不花 token
.\run.cmd --db out\tech.sqlite3 ask "华为和赛力斯在智能汽车上有什么合作" --dry-run

:: 完整报告 + 附录（检索名次、事件簇、引用与数字校验）
.\run.cmd --db out\tech.sqlite3 ask "攀枝花的微电网项目有什么特点" --top 5 --debug

:: 不调用模型，用规则摘要排查检索
.\run.cmd --db out\tech.sqlite3 ask "岚图汽车的销量" --no-llm

:: 十个查询的检索体检（不生成报告）
.\.venv\Scripts\python.exe tools\retrieval_smoke.py --db out\tech.sqlite3
```

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--top` | 8 | 入选报道数上限 |
| `--hops` | 2 | 图路跳数：1 只出直接关系，3 会把整张图吞进来 |
| `--sections` | `configs/report_sections.json` | 换一套报告结构 |
| `--out` | `out/reports/<slug>.md` | 报告路径 |
| `--dry-run` | 关 | 只打印检索结果与证据包路径 |
| `--no-llm` | 关 | 规则摘要，不产生费用 |
| `--debug` | 关 | 报告末尾附检索过程表、事件簇、校验结果 |

### 4.1 分节是配置，不是代码

`configs/report_sections.json` 决定节的标题、顺序、字数、开关；代码只认识**选择器**：

```json
{ "id": "background", "title": "背景与现状", "words": 600,
  "selector": "text_hits", "cite": true, "enabled": true }
```

| 选择器 | 取什么 | 谁写 |
| --- | --- | --- |
| `text_hits` | 文本路命中的段落 → 背景与现状 | 模型 |
| `graph_clusters` | 按主体分组的图路事实 → 关键主体 | 模板 |
| `chrono` | 事件簇 + 日期 → 时间线 | 模板 |
| `outlook` | 事实 + 事件 → 趋势与不确定性（**不带引用**） | 模型 |
| `top_facts` | 前面写好的正文 → 摘要 | 模型 |
| `sources_all` | 来源表 | 模板 |

两类内容分开生产，是刻意的：

- **事实清单与时间线用模板**。编号、日期、主体名让模型复述一遍只会引入错误，模板输出还能逐行核对。
- **叙述用模型，但摘要最后写**。摘要要概括正文，先写摘要等于让模型在不知道后面会写什么时先下结论，两者迟早打架。
- **推断单独成节**。`outlook` 开头明确声明是推断，且不做引用校验——把推断混进事实里，是这个流程最容易出的错。

### 4.2 事件聚类：同一件事只写一次

一个发布会常有 3~5 家媒体同时报道。不聚类，报告就会把同一件事写五遍。四个形式信号，总分 ≥3 合并：

| 信号 | 权重 | 判据 |
| --- | --- | --- |
| 近重复 | 3 | SimHash 汉明距离 ≤6（转载、改写） |
| 标题重合 | 2 | 标题二元组 Jaccard ≥0.4 |
| 共享实体 | 2 | 两篇共享 ≥2 个非泛化实体 |
| 时间接近 | 1 | 发布时间相差 ≤48h（单靠它不合并） |

只有并查集 + 两两比较，O(n²)。报告规模最多几十篇，够用；好处是当合并看起来不对时，能指出是哪条信号导致的。

### 4.3 四道校验

1. **逐句引用**：每句话必须带 `[n]`；生成后按节统计"引用几处 / 无引用几句"，终端打印。
2. **越界引用**：`[99]` 这类超出来源表长度的编号会被标出来。
3. **数字回查**：带单位或 4 位以上的数字逐个回查证据原文；找不到的列进附录。`18%` 与 `18 percent` 会先归一化，避免误报。
4. **没覆盖就说没有**：查询里最具体的词在全库不存在时返回 0 篇，不用泛词凑答案。

校验只作用于**模型写的节**。规则摘要和原文摘录本身就是清单，用"逐句带引用"去量只会制造噪声。

### 4.4 实测

语料：20 篇真实中文科技新闻，5 篇建图，101 条三元组，376 个段落，33 个主体。

检索层，10 个查询（详见 `tools/retrieval_smoke.py`）：

| 情况 | 查询数 | 结果 |
| --- | --- | --- |
| 语料有覆盖 | 8 | 正确报道**全部排第 1**，其中 7 个靠图路、1 个靠文本路 |
| 语料没覆盖 | 2 | 返回 0 篇（固态电池产业链、钠离子电池量产） |

报告层，4 个查询逐节核对：

| 查询 | 入选 | 事件 | 观察 |
| --- | --- | --- | --- |
| 小米机器人在具身智能上有什么进展 | 3 篇 | 3 个 | 三篇属不同事件，未误合并 |
| Northwind 和 Sunbelt 的合并（合成样例） | 2 篇 | **1 个** | 两篇同事件正确合并，时间线只一行 |
| 攀枝花的微电网项目有什么特点 | 1 篇 | 1 个 | 单篇时背景节主动收短，不硬凑 |
| 商务部对半导体出口管制是什么态度 | 2 篇 | 2 个 | 图路无事实时"关键主体"节如实留空 |

四份报告的模型生成节均为 0 处越界引用；数字回查抓到 1 处（`18%` 对 `18 percent`），归一化后消失。

这个规模只能验证"链路通不通"，**不是召回率**。语料扩大后要重测，并把 gold 文章集固化下来。

---

## 5. 新闻源与时间

当前 **11 个源，全部中文、全部免 key**：

| 源 | 站点 | 性质 | 接法 |
| --- | --- | --- | --- |
| `xinhua_tech` | 新华网·科技 | 国家通讯社 | 列表页 |
| `stdaily` | 科技日报 | 科技日报社 | 列表页 |
| `chinanews_fin` / `chinanews_top` | 中新网·财经 / 要闻 | 通讯社 | RSS |
| `zqrb` | 证券日报 | 报社 | RSS |
| `jiemian` | 界面新闻 | 报社（上海报业集团） | RSS |
| `eeo` | 经济观察报 | 报社 | RSS |
| `tmtpost` | 钛媒体 | 商业科技媒体 | RSS |
| `leiphone` | 雷锋网 | 商业科技媒体 | RSS |
| `infoq` | InfoQ 中文 | 技术社区媒体 | RSS |
| `ifanr` | 爱范儿 | 商业科技媒体 | RSS |

预设：`tech`（11 个）、`tech-off`（7 个权威源）、`tech-media`（4 个垂媒）、`all`（再并入 gdelt / guardian / 通用 rss）。

**回溯历史日期**：RSS 只保留最近两三天（实测 11-18 抓，最早只到 11-16），指定日期要走站点的日页归档。目前接了中新网：

```bat
.\.venv\Scripts\python.exe -m newskg.cli fetch --source chinanews_day --day 2026-11-04 --days 15 --limit 60
```

`chinanews_day` 解析 `scroll-news/YYYY/MMDD/news.shtml`，一天约 530 条、每条带分钟，站点时间是北京时，入库统一折成 UTC。其它源要回溯，得各加一个 `sources/archive.py` 里的 spec。

**入选判据三条**，缺一不可：

1. 能解析出条目（RSS 的 `<item>`，或列表页的「标题 + 链接」）；
2. 条目**带时间**——没有时间，`--days` 会静默失效；
3. 正文能被 trafilatura 抽出 >400 字符（这是筛源口径；运行时不会因为短正文就丢弃）。

第 2 条淘汰率最高：人民网有日期但停在 2024 年，新浪停在 2018 年，搜狐停在 2016 年。「有日期」和「日期是新的」是两件事。

四个时间坑，三个在 `sources/timestamps.py`（只有一份实现，体检器与入库共用），一个在 `sources/filters.py`：

| 坑 | 症状 | 修法 |
| --- | --- | --- |
| 无时区的本地时间（证券日报） | 整源静默归零 | 原文无时区标记时按 `+08:00` 折算 |
| 只有日期没有时刻（列表页源） | 打分池永远排最后 | `filters._fair_pool()` 按源轮流取 |
| 北京时间标成 GMT（InfoQ 中文） | 时间超前 8 小时且无异常 | 折算后仍超前 >1h 就再纠一次 |
| 页面元数据覆盖 feed 时间 | 全库时间变成 `T00:00:00Z` | `pick_published()` 优先选带钟点的 |

最后一个最隐蔽：trafilatura 从正文页抽的 `date` 常常只有日期，而入库写的是 `ext.published_at or stub.published_at`，页面元数据优先，于是整库真实新闻的发布时间都被抹成当天零点。时间窗过滤在入库**之前**就完成了，所以流水线一路绿灯，只有按时间排序时才看得出来。

源会改版、RSS 会下线，所以要能自己体检：

```bat
.\.venv\Scripts\python.exe tools\probe_sources.py
.\.venv\Scripts\python.exe tools\probe_sources.py --url https://example.com/rss --label 某站
```

---

## 6. 抽取与归一

`build` 只有一种行为：把每篇文章交给 [KGGen](https://github.com/stair-lab/kg-gen)（NeurIPS'25），拿回它的关系集合，谓词归一后入库。

| 能力 | 手写 prompt | KGGen |
| --- | --- | --- |
| 实体聚类 | 自己维护别名表逐条对 | 生成时按语义归簇 |
| 产出上限 | 受 `max_tokens` 约束，每篇 25 条封顶 | 不设上限，长文可出 100+ 条 |

每篇置信度固定 0.85：KGGen 不返回逐条置信度，这个数字是占位，不要拿它排序。

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `NEWSKG_KGGEN_MODEL` | 沿用 `NEWSKG_LLM_MODEL` | 抽取模型；不带 `provider/` 前缀时自动补 `openai/` |
| `NEWSKG_KGGEN_BASE_URL` | 沿用 `NEWSKG_LLM_BASE_URL` | 端点 |
| `NEWSKG_KGGEN_MAX_TOKENS` | 8192 | 单篇输出上限；调小会让长文 JSON 截断、整篇记 0 条 |

本地实测（`qwen3.7-plus`）：单篇 14~36 条三元组；5 篇合起来 101 条、33 个主体；`demo` 的 8 篇合成样例出 75 条。失败会写 `runs`，不会反复重付费。

谓词按语种分流：中文出中文谓词（`收购` / `发布` / `搭载`），英文出 `snake_case`（`acquired` / `located_in`）。中文实体配英文谓词会让边"半土半洋"，后续按谓词聚合也做不了。

### 实体归一三层

| 层 | 做什么 | 例子 |
| --- | --- | --- |
| 1 字符层 | NFKC、去包裹引号、压空白、去尾随标点 | `“梦想”号大洋钻探船` → `梦想号大洋钻探船` |
| 2 别名表 | `data/aliases.json` 的写法合并 | `英伟达` → `NVIDIA`，`GTA 6` → `Grand Theft Auto VI` |
| 3 泛化过滤 | 丢泛化宾语与自环 | 删 `(某公司, 推出, 产品)` 这类无信息边 |

别名表手工维护，只做写法层消歧，不做上下文消歧。改完不必重跑抽取：

```bat
.\run.cmd --db out\tech.sqlite3 renormalize
```

---

## 7. 命令与环境变量

| 子命令 | 干什么 | 典型用法 |
| --- | --- | --- |
| `init` | 建目录与空库 | `.\run.cmd init` |
| `fetch` | 抓取 + 过滤 + 入库 | `--source tech --limit 20 --days 3` |
| `build` | 用 KGGen 抽三元组建图 | `--limit 8` |
| `export` | 导出图 | `--format all`（json/cypher/graphml） |
| `stats` | 打印统计 | `.\run.cmd --db out\tech.sqlite3 stats` |
| `dump` | 打印三元组 | `--limit 20` |
| `renormalize` | 按别名表重写实体名 | 改完 `aliases.json` 后 |
| `reset` | 清空数据 | `--keep-articles` 保留文章 |
| `ask` | 查询 → 检索 → 报告 | `ask "小米机器人有什么进展" --top 5` |
| `demo` | 端到端演示（样例 + KGGen） | `.\run.cmd demo` |

| 环境变量 | 默认 | 作用 |
| --- | --- | --- |
| `NEWSKG_LLM_BASE_URL` / `_API_KEY` / `_MODEL` | DeepSeek 官方 | 抽取与写作共用端点 |
| `NEWSKG_TOPIC_MODEL` | 同抽取模型 | 题材打分（可用更便宜的模型） |
| `NEWSKG_TOPIC_THRESHOLD` | 6 | 题材分数阈值 |
| `NEWSKG_TECH_FEEDS` | 全部 11 个 | 覆盖 `tech` 预设成员（逗号分隔注册名） |
| `NEWSKG_POLITE_DELAY` | 1.0 | 同域名抓取间隔（秒） |
| `NEWSKG_MAX_BODY_CHARS` | 12000 | 单篇正文截断长度 |
| `NEWSKG_MIN_BODY_CHARS` | 150 | 正文低于此字数拒收（页脚残稿、付费墙残页） |

---

## 8. 产物与目录

```
knowlegde_merge/
├─ run.cmd                   包装脚本（PYTHONPATH + .venv + 参数透传）
├─ requirements.txt          抓取/转换/存储依赖（6 个库）
├─ requirements-kggen.txt    抽取依赖，装进 .kgvenv
├─ .env.example              端点模板（不要提交真 key）
├─ src/newskg/
│  ├─ cli.py                 10 个子命令
│  ├─ config.py              路径、限速、阈值、.env 加载
│  ├─ models.py              ArticleStub / Article / Triple
│  ├─ sources/               发现层：tech.py、html_list.py、timestamps.py、filters.py、topic_llm.py
│  ├─ convert.py             抓 HTML → 正文 → Markdown
│  ├─ dedup.py               四层去重
│  ├─ pipeline.py            process_stub / ingest / build_graph
│  ├─ store.py               建表、统计、三种图导出
│  ├─ textindex.py           段落切分 + FTS5 trigram 索引
│  ├─ retrieve.py            实体链接 + 双路召回 + RRF + 证据包
│  ├─ events.py              事件聚类（并查集）
│  ├─ report.py              证据包 → 五节报告 + 引用/数字校验
│  ├─ llm_client.py          最小 OpenAI 兼容客户端
│  └─ kg/
│     ├─ kggen.py            KGGen 适配（唯一抽取后端）
│     ├─ base.py             抽取器协议、谓词归一、篇内去重
│     └─ entities.py         实体归一
├─ configs/report_sections.json   报告分节配置
├─ tools/                    看、查文章、溯源、源体检、检索体检
├─ data/
│  ├─ fixtures/              8 篇合成 HTML（demo 用，勿当真实新闻引用）
│  ├─ md/                    已入库文章的 Markdown（build 从这里读正文）
│  └─ aliases.json           实体别名表
├─ .kgvenv/                  kg-gen 独立环境（gitignore，按 §1 自建）
└─ out/                      SQLite、图、报告、打分缓存（可整体删除）
```

`out/` 里的东西全部可删可重建：

| 文件 | 来源 | 内容 |
| --- | --- | --- |
| `*.sqlite3` | `fetch` / `build` / `ask` | 文章、段落、三元组、运行记录 |
| `graph.json` / `.cypher` / `.graphml` | `export` | 三种图格式 |
| `*.html` | `tools/view_graph_lite.py` | 单文件可交互图，断网可用 |
| `topic_scores.json` | `fetch` | 题材打分缓存（改阈值重跑不再付费） |
| `rejected.jsonl` | `fetch` | 正文不合格被拒的记录（标题/URL/原因），跑大语料后靠它复核 |
| `reports/*.md` | `ask` | 报告：摘要 / 背景 / 主体 / 时间线 / 趋势 / 来源 |
| `reports/*.evidence.json` | `ask` | 证据包：入选报道、命中段落、图路事实、两路名次、事件簇 |

---

## 9. 已知限制

**评测**

1. 没有任何量化评测。gold 集与评测脚本随 `rule` / `llm` 一起删除，现在只能看三元组数量、抽样读原文、看图上是否成立。§4.4 的实测是 10 个查询的定性结论，不是召回率。

**检索**

2. 两字词靠 `LIKE` 全表扫（trigram 索引不到两字词）。几百段没问题，上万篇要换 bigram 索引或向量检索。
3. 事件聚类只看形式信号，没有正文语义相似度。换措辞、只共享一个实体的同一事件仍会被拆开。
4. 中文查询切词只按连接词与语气词切分，不做分词。好处是"华为"不会被切成"华"，代价是会产生一些无用的 n-gram。

**成文**

5. 数字回查只验"在原文出现过"，不验用法。张冠李戴、口径不一致查不出来。
6. 摘要基于模型写的正文再压缩，正文里的评价性措辞（"巨大潜力"这类）会被继承。逐句引用保证出处，保证不了措辞中性。
7. 报告长度靠提示词约束（当前配置 4000 字总量），不是硬截断；复杂查询容易写短。

**图谱**

8. 只做写法层消歧（别名表），不解决上下文歧义（`Apple` / `apple` 仍会合并）。
9. 中文品牌名会被翻成英文（`华为`→`Huawei`、`比亚迪`→`BYD`），尽管提示词要求保留原文。
10. 英文谓词归一不彻底：中文有谓词词典，英文只有小别名表，`raises` / `raised` / `raised_on` 会变成三条边。
11. KGGen 是黑盒：分块与聚类策略不可调，只能通过 `max_tokens` 间接影响产出。

**工程**

12. 单机 SQLite：上万篇要换 Postgres 或图数据库，`export cypher` 是迁移入口。
13. 权威优先的代价是科技密度：11 个源里 7 个是通讯社/报社，题材打分要筛掉大量弱相关条目，20 篇入库靠的是 LLM 打分这一道闸。
14. 列表页源只到"天"：新华网、科技日报给不出精确到分钟的发布时间。
15. 近重复只抓逐字转载：SimHash 阈值 6，长文删掉一段就够不着（实测删一句距离 15）。所以聚类实际主要靠标题重合与共享实体，SimHash 只兜住通稿原文。
16. 短快讯会被正文闸错杀：低于 150 字的合法快讯与页脚残稿在形式上分不开，宁可错杀。被拒的理由记在 `out/rejected.jsonl`，要复核直接看这个文件。

---

## 10. 排错

**`ImportError: The pyarrow installation is not built with support for 'dataset'`**

`kg_gen` 导入时会连带加载 `pyarrow.dataset`。若机器上有应用控制策略（WDAC/AppLocker）拦了这个 `.pyd`，报错原文会带「应用程序控制策略已阻止此文件」。这是环境限制，不是代码问题；换 pyarrow 版本即可（实测 26.0.1 被拦、23.0.1 可用）：

```bat
python -m pip install --target .tmp\pa23 pyarrow==23.0.1
rmdir /s /q .kgvenv\pyarrow .kgvenv\pyarrow-26.0.1.dist-info
xcopy /e /i .tmp\pa23\pyarrow .kgvenv\pyarrow
xcopy /e /i .tmp\pa23\pyarrow-23.0.1.dist-info .kgvenv\pyarrow-23.0.1.dist-info

:: 验证
set PYTHONPATH=src;.kgvenv
.venv\Scripts\python.exe -c "import pyarrow.dataset; from kg_gen import KGGen; print('ok')"
```

**`no such module` / 相对导入报错**

用 `python -m newskg.cli`，不要直接跑 `src/newskg/cli.py`。

**`no such module: fts5` 或 trigram 相关报错**

SQLite 版本过旧（需 3.34+ 且编译时启用 FTS5）。`python -c "import sqlite3; print(sqlite3.sqlite_version)"` 确认；官方 Python 3.10+ 的 Windows 安装包默认满足。

**`ask` 返回 0 篇**

先看是不是真没覆盖：`--dry-run` 打印的实体链接与两路名次。查不到是设计行为（见 §4.3 第 4 条），不是报错。

---

## 11. 声明

- 新闻正文版权归原站点。本工程只做技术验证，不转载、不再分发正文。
- 抓取按 `NEWSKG_POLITE_DELAY` 限速，**不绕过任何反爬**：被 403/405/429 挡下的源直接放弃。
- `.env` 里的大模型 key 不要提交；仓库只保留 `.env.example`。
- `data/fixtures/` 是合成样例，不是真实新闻，不能当语料引用。





