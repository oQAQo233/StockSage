# NewsKG：新闻 → 统一 Markdown → 知识图谱

把新闻抓下来（当前是 11 个中文科技源），抽成 `(主体, 关系, 客体)` 三元组，落进 SQLite，再导出成可交互的知识图谱。

这是一个课程作业工程，目标不是"做一个新闻 App"，而是把**一条完整可复现的信息抽取流水线**跑通并留下实验记录：抓取 → 正文抽取 → 格式统一 → 去重 → 存储 → 三元组抽取 → 实体归一 → 评测 → 可视化。

- 唯一外部依赖是**大模型的 OpenAI 兼容端点**（可选；不配也能跑离线演示与规则基线）
- 全部数据落在**一个 SQLite 文件**里，可随时删库重跑
- 每条三元组都能**溯源**到具体文章，评测可复现

> 更细的设计说明、踩坑记录与逐条实验数据在 [docs/README-full.md](docs/README-full.md)。

---

## 0. 环境准备

需要 **Python 3.10+**（本地在 3.14 上跑通）。Windows 下：

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env      :: 按需填 NEWSKG_LLM_*，不填也能跑 demo 与 rule 基线
```

`requirements.txt` 里只有 6 个常用库（requests / trafilatura / markitdown / feedparser / networkx / python-dateutil）加一个可选的 `kg-gen`。没有数据库服务，没有前端构建。

---

## 1. 先跑起来

```bat
:: 1) 离线一键演示：不联网、不花钱，8 篇内置样例 → 19 条三元组 → 可交互图
.\run.cmd demo

:: 2) 真实抓取 + 建图：抓 20 篇中文科技新闻，抽 8 篇建成图谱
.\run.cmd fetch --source tech --limit 20 --days 3 --db out\tech.sqlite3
.\run.cmd --db out\tech.sqlite3 build --backend llm --limit 8
.\run.cmd --db out\tech.sqlite3 export --format all

:: 3) 看图谱：生成单文件 HTML，双击即可打开（无需联网、无第三方 JS）
.\.venv\Scripts\python.exe tools\view_graph_lite.py --db out\tech.sqlite3 --min-degree 2 --out out\graph.html
```

`run.cmd` 只是个包装：设好 `PYTHONPATH=src`、用 `.venv` 里的 Python、把参数原样透传。等价于：

```bat
set PYTHONPATH=src
.venv\Scripts\python.exe -m newskg.cli --db out\kg.sqlite3 <子命令> ...
```

> 必须用 `python -m newskg.cli`（包内部是相对导入），直接 `python src/newskg/cli.py` 会报错。

---

## 2. 流水线

```
   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
   │ 发现      │──►│ 抓取+抽取 │──►│ 转 Markdown│──►│ 去重      │
   │ sources/ │   │trafilatura│   │markitdown │   │ SimHash  │
   └──────────┘   └──────────┘   └──────────┘   └────┬─────┘
                                                      ▼
   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
   │ 导出/可视化│◄──│ 实体归一  │◄──│ 三元组抽取 │◄──│ SQLite   │
   │ json/cypher│  │entities.py│  │rule/llm/kggen│ │ articles │
   └──────────┘   └──────────┘   └──────────┘   └──────────┘
```

| 环节 | 关键实现 | 一句话说明 |
| --- | --- | --- |
| 发现 | `sources/` | 11 个中文科技源（RSS + 列表页），预设 `tech` |
| 过滤 | `sources/filters.py` | 时间窗 + LLM 题材打分（阈值 6），两道闸 |
| 抓取 | `convert.py` | trafilatura 抽正文，正文 >400 字符才算成功 |
| 统一 | `convert.py` | markitdown 转 Markdown，一份正文两种表示 |
| 去重 | `dedup.py` | URL 规范化 → 精确指纹 → SimHash → shingle Jaccard |
| 存储 | `store.py` | `articles` / `triples` / `runs` 三张表 + `v_triples` 视图 |
| 抽取 | `kg/` | 三种后端，产出落同一张表，用 `extractor` 字段区分 |
| 归一 | `kg/entities.py` | 字符层 → 别名表 → 泛化宾语过滤，三层去噪 |
| 评测 | `evaluate.py` | strict / relaxed 两套口径，缺 key 不中断 |

---

## 3. 目录

```
knowlegde_merge/
├─ run.cmd                  一键包装（PYTHONPATH + .venv + 参数透传）
├─ requirements.txt         requests / trafilatura / markitdown / feedparser / networkx …
├─ .env.example             大模型端点模板（不要把真 key 提交）
├─ src/newskg/
│  ├─ cli.py                13 个子命令
│  ├─ config.py             路径、限速、阈值、.env 加载
│  ├─ models.py             ArticleStub / Article / Triple
│  ├─ sources/              发现层（tech.py、html_list.py、gdelt/guardian/rss、topic_llm、filters）
│  ├─ convert.py            抓 HTML → 正文 → Markdown
│  ├─ dedup.py              四层去重
│  ├─ pipeline.py           process_stub / ingest / build_graph
│  ├─ store.py              建表、统计、三种图导出
│  ├─ kg/                   rule.py（离线基线）/ llm.py / kggen.py / entities.py
│  ├─ evaluate.py           gold 打分 + 报告
│  └─ compare.py            多后端对照
├─ tools/                   辅助脚本（看图、查文章、看 gold、溯源）
├─ docs/README-full.md      详细版文档（设计取舍、踩坑全记录、逐条实验数据）
├─ data/
│  ├─ fixtures/             8 篇合成 HTML（离线演示，勿当真实新闻引用）
│  ├─ gold/                 gold 标注（英文 246 条 / 中文 46 条）
│  ├─ corpus/               结语料清单
│  ├─ md/                   冻结的 Markdown 正文（可离线重建）
│  └─ aliases.json          实体别名表
└─ out/                     SQLite 库、图、报告、打分缓存（可全部删除重跑）
```

---

## 4. 新闻源

当前 **11 个源，全部中文、全部免 key**，按权威性从高到低：

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

预设：`tech`（全部 11 个）、`tech-off`（权威 7 个）、`tech-media`（垂媒 4 个）、`all`（连 `gdelt` / `guardian` / `rss`）。

**入选判据是三条**，一条都不能少：

1. 能解析出条目（RSS 的 `<item>`，或列表页的「标题 + 链接」）；
2. **条目带时间**——RSS 看 `pubDate`，列表页看 URL 里的日期。没有时间，`--days` 时间窗会静默失效；
3. 正文能被 trafilatura 抽出 **>400 字符**（这是**筛源时的实测口径**；运行时不会硬性丢弃短正文，只是抽出信息少、质量差）。

第 2 条淘汰率最高：人民网的 feed 有日期但停在 2024 年，新浪停在 2018 年，搜狐停在 2016 年——"有日期"和"日期是新的"是两件事。

新华网、科技日报的 RSS 都已下线，`sources/html_list.py` 用**列表页 + URL 里的日期**把它们接了回来。规则只有一条："从 URL 读日期"，站点换模板也不受影响。

---

## 5. 抽取后端

| 后端 | 原理 | 依赖 | 成本 | 实测 strict F1 |
| --- | --- | --- | --- | --- |
| `rule` | 正则 + 动词模板（中英各一套） | 无 | 0 | 英文 0.80% / 中文 0.00% |
| `llm` | 单次调用 + 强制 JSON，每篇 ≤25 条 | 任意 OpenAI 兼容端点 | 按 token | 英文 24.41% / 中文 28.15% |
| `kggen` | KGGen（NeurIPS'25），自带实体聚类 | `kg-gen` + litellm + dspy | 按 token + 重依赖 | 与短谓词 gold 对不齐，见详细文档 |

`rule` 不是"能用的方案"，而是**基线**：没有它就回答不了"LLM 到底提升了多少"。同一批数据上 0.80% 与 24.41% 的差距，就是这个项目最有说服力的一个数字。

谓词按语种分流：中文出中文谓词（`收购` / `发布` / `搭载`），英文出 `snake_case`（`acquired` / `located_in`），否则中文实体会配英文谓词，评出来的分数只是在量翻译差异。

---

## 6. 评测

| 语料 | 规模 | gold | `llm` strict P/R/F1 | `llm` relaxed F1 |
| --- | --- | --- | --- | --- |
| 英文（BBC/半岛） | 32 篇 | 246 条 | 18.53% / 35.77% / 24.41% | 35.90% |
| 中文（财经快讯） | 5 篇 | 46 条 | 21.35% / 41.30% / 28.15% | 36.09% |
| 离线样例（demo） | 8 篇 | 20 条 | 100% / 95% / **97.44%** | — |

`demo` 的 97% 不是"效果好"，而是**配套样例被设计成模板句式**——它的作用是当回归测试（改代码后跑一遍，数字不能变），不是当效果证明。

strict 与 relaxed 的差别只在"关系描述写长了算不算对"。两套口径都报，是因为只报高的那套等于自欺。

---

## 7. 实体归一化

LLM 抽出来的实体名有三种脏：写法不同（`NVIDIA` / `英伟达`）、多余标点（`“梦想”号` 带引号）、以及**类别词当实体**（`AI` / `技术` / `产品`）。三层处理，顺序不能换：

| 层 | 做什么 | 例子 |
| --- | --- | --- |
| 1 字符层 | NFKC、去包裹引号、压空白、去尾随标点 | `“梦想”号大洋钻探船` → `梦想号大洋钻探船` |
| 2 别名表 | `data/aliases.json` 里的同义写法合并到规范名 | `英伟达` → `NVIDIA`，`GTA 6` → `Grand Theft Auto VI` |
| 3 泛化过滤 | 丢掉泛化宾语与自环 | 删 `(某公司, 推出, 产品)` 这类无信息边 |

别名表是**手工维护**的，只做"写法层"消歧，不做上下文消歧（`Apple`/`apple` 仍会被合并）——这是已知限制，不是遗漏。

改完别名表不必重跑抽取：`.\run.cmd --db out\tech.sqlite3 renormalize` 会按当前表重写已有实体名。

---

## 8. 命令速查

| 子命令 | 干什么 | 典型用法 |
| --- | --- | --- |
| `demo` | 离线一键演示 | `.\run.cmd demo` |
| `init` | 建目录与空库 | `.\run.cmd init` |
| `fetch` | 抓取 + 过滤 + 入库 | `--source tech --limit 20 --days 3` |
| `build` | 抽三元组建图 | `--backend llm --limit 8` |
| `export` | 导出图 | `--format all`（json/cypher/graphml） |
| `stats` | 统计 | `.\run.cmd --db out\tech.sqlite3 stats` |
| `dump` | 打印三元组 | `--limit 20` |
| `eval` | 对 gold 打分 | `--gold data\gold\corpus.csv`（默认值） |
| `compare` | 多后端对照 | `--backends rule,llm` |
| `export-corpus` / `import-corpus` | 冻结/重建语料（不联网） | 见详细文档 |
| `renormalize` | 按别名表重写实体名 | 改完别名表后跑 |
| `reset` | 清空数据 | `--keep-articles` 可保留文章 |

常用环境变量（也可写进 `.env`）：

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `NEWSKG_LLM_BASE_URL` / `_API_KEY` / `_MODEL` | DeepSeek 官方 | 抽取后端端点 |
| `NEWSKG_TOPIC_MODEL` | 同抽取模型 | 题材打分用的模型（可用更便宜的） |
| `NEWSKG_TOPIC_THRESHOLD` | 6 | 题材分数阈值 |
| `NEWSKG_TECH_FEEDS` | 全部 11 个 | 覆盖 `tech` 预设成员 |
| `NEWSKG_POLITE_DELAY` | 1.0 | 同域名抓取间隔（秒） |

---

## 9. 产物

`out/` 里的东西**全部可删可重建**：

| 文件 | 来源 | 内容 |
| --- | --- | --- |
| `*.sqlite3` | `fetch` / `build` | 文章、三元组、运行记录 |
| `graph.json` / `.cypher` / `.graphml` | `export` | 三种图格式 |
| `*.html` | `tools/view_graph_lite.py` | 单文件可交互图（断网可用） |
| `report.md` | `demo` | 自动生成的实验报告 |
| `comparison.md` / `.csv` | `compare` | 多后端对照表 |
| `topic_scores.json` | `fetch` | 打分缓存（改阈值重跑不再付费） |

---

## 10. 已知限制

1. **科技题材还没有 gold 集**：246 / 46 条 gold 都是旧的财经与国际语料，科技语料现在只能看三元组的数量与观感，报不了 P/R/F1。
2. **样本量小**：20 篇英文 + 5 篇中文，逐篇 F1 波动大，macro 平均方差不可忽视。
3. **gold 是模型辅助标注的**：与 `llm` 抽取器共享同一模型家族，等于让被测系统参与制定评分标准。
4. **只做写法层消歧**：别名表不解决上下文歧义。
5. **中文品牌名会被翻成英文**：`华为`→`Huawei`、`比亚迪`→`BYD`，尽管提示词要求保留原文。
6. **权威优先的代价是科技密度**：11 个源里 7 个是通讯社/报社，60 篇候选要靠 LLM 打分才留下 20 篇。
7. **列表页源只到"天"**：新华网、科技日报无法提供精确到分钟的发布时间。
8. **单机 SQLite**：上万篇要换 Postgres 或图数据库，`export cypher` 是迁移入口。

---

## 11. 声明

- 所有新闻正文版权归原站点，本工程只做技术验证，不转载、不再分发正文。
- 抓取按 `NEWSKG_POLITE_DELAY` 限速，**不绕过任何反爬**：被 403/405/429 挡下的源直接放弃。
- `.env` 里的大模型 key 不要提交；仓库只保留 `.env.example`。
- `data/fixtures/` 是合成样例，不是真实新闻，不能当语料引用。

