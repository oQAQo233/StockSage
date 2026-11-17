# NewsKG：新闻 → 统一 Markdown → 知识图谱

把新闻抓下来（当前是 11 个中文科技源），用 **KGGen** 抽成 `(主体, 关系, 客体)` 三元组，落进 SQLite，再导出成可交互的知识图谱。

这是一个课程作业工程，目标不是"做一个新闻 App"，而是把**一条完整可复现的信息抽取流水线**跑通：抓取 → 正文抽取 → 格式统一 → 去重 → 存储 → 三元组抽取 → 实体归一 → 可视化。

- 外部依赖只有**大模型的 OpenAI 兼容端点**（抽取必需，没有 key 跑不了建图）
- 全部数据落在**一个 SQLite 文件**里，可随时删库重跑
- 每条三元组都能**溯源**到具体文章与原文句子

> 抽取方案已定为 KGGen，早期用于对照的 `rule`（正则基线）与 `llm`（手写 prompt）两个后端、以及配套的 gold 集与评测/对照脚本**已全部删除**。删掉的原因与当年的结论压在这一句里：正则基线在真实新闻上 F1 不到 1%，手写 prompt 的产出量被输出预算卡在每篇 25 条上下，而 KGGen 自带实体聚类、没有这个上限。

---

## 0. 环境准备

需要 **Python 3.10+**（本地在 3.14 上跑通）。Windows 下：

```bat
:: 1) 主环境：抓取、转换、存储、可视化
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

:: 2) 抽取环境：kg-gen 会拖进 litellm / dspy / pyarrow，单独装一个目录，避免污染主环境
.venv\Scripts\pip install --target .kgvenv -r requirements-kggen.txt

:: 3) 配端点（不填跑不了 build）
copy .env.example .env
```

`requirements.txt` 里只有抓取/转换/存储要用的 6 个库；抽取用的 `kg-gen` 单独放在 `requirements-kggen.txt`——它体积大、依赖重，单独装进 `.kgvenv`，`run.cmd` 会把 `src` 和 `.kgvenv` 一起挂到 `PYTHONPATH` 上。

`kg_gen` 导入时会连带加载 `pyarrow`：`kg_gen` → `sentence_transformers` → `datasets` → `pyarrow.dataset`。如果所在机器有应用控制策略（WDAC/AppLocker）拦了 `pyarrow/_dataset.cp3xx-win_amd64.pyd`，会看到：

```
ImportError: The pyarrow installation is not built with support for 'dataset'
(DLL load failed while importing _dataset: 应用程序控制策略已阻止此文件。)
```

**这是环境限制，不是代码问题**，而且换一个 pyarrow 版本就能绕过（实测 26.0.1 被拦、23.0.1 可用）：

```bat
:: 把 .kgvenv 里的 pyarrow 换成 23.x（用系统 python 的 pip，装完把目录拷回来）
python -m pip install --target .tmp\pa23 pyarrow==23.0.1
rmdir /s /q .kgvenv\pyarrow .kgvenv\pyarrow-26.0.1.dist-info
xcopy /e /i .tmp\pa23\pyarrow .kgvenv\pyarrow
xcopy /e /i .tmp\pa23\pyarrow-23.0.1.dist-info .kgvenv\pyarrow-23.0.1.dist-info
```

换完用一句命令确认，能打印出版本号就说明通了：

```bat
set PYTHONPATH=src;.kgvenv
.venv\Scripts\python.exe -c "import pyarrow.dataset; from kg_gen import KGGen; print('ok')"
```

---

## 1. 先跑起来

```bat
:: 1) 真实抓取 + 建图：抓 20 篇中文科技新闻，抽 8 篇建成图谱
.\run.cmd fetch --source tech --limit 20 --days 3 --db out\tech.sqlite3
.\run.cmd --db out\tech.sqlite3 build --limit 8
.\run.cmd --db out\tech.sqlite3 export --format all

:: 2) 看图谱：生成单文件 HTML，双击即可打开（无需联网、无第三方 JS）
.\.venv\Scripts\python.exe tools\view_graph_lite.py --db out\tech.sqlite3 --min-degree 2 --out out\graph.html

:: 3) 端到端演示：内置 8 篇合成样例 → 三元组 → 图（样例不联网，抽取仍要模型）
.\run.cmd demo
```

`run.cmd` 只是个包装：设好 `PYTHONPATH=src;.kgvenv`、用 `.venv` 里的 Python、把参数原样透传。等价于：

```bat
set PYTHONPATH=src;.kgvenv
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
   │ json/cypher│  │entities.py│  │   KGGen   │  │ articles │
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
| 抽取 | `kg/kggen.py` | KGGen 一次调用出关系集，自带实体聚类 |
| 归一 | `kg/entities.py` | 字符层 → 别名表 → 泛化宾语过滤，三层去噪 |

---

## 3. 目录

```
knowlegde_merge/
├─ run.cmd                  一键包装（PYTHONPATH=src;.kgvenv + .venv + 参数透传）
├─ requirements.txt         抓取/转换/存储要用的 6 个库
├─ requirements-kggen.txt   抽取用的 kg-gen（装进 .kgvenv）
├─ .env.example             大模型端点模板（不要把真 key 提交）
├─ src/newskg/
│  ├─ cli.py                9 个子命令
│  ├─ config.py             路径、限速、阈值、.env 加载
│  ├─ models.py             ArticleStub / Article / Triple
│  ├─ sources/              发现层（tech.py、html_list.py、timestamps.py、gdelt/guardian/rss、topic_llm、filters）
│  ├─ convert.py            抓 HTML → 正文 → Markdown
│  ├─ dedup.py              四层去重
│  ├─ pipeline.py           process_stub / ingest / build_graph
│  ├─ store.py              建表、统计、三种图导出
│  └─ kg/
│     ├─ kggen.py           KGGen 适配（唯一抽取后端）
│     ├─ base.py            抽取器协议、谓词归一、篇内去重
│     └─ entities.py        实体归一
├─ tools/                   辅助脚本（看图、查文章、溯源、源体检）
├─ data/
│  ├─ fixtures/             8 篇合成 HTML（demo 用，勿当真实新闻引用）
│  ├─ md/                   已入库文章的 Markdown 正文（build 从这里读正文）
│  └─ aliases.json          实体别名表
├─ .kgvenv/                 kg-gen 独立环境（gitignore，按 §0 自建）
└─ out/                     SQLite 库、图、打分缓存（可全部删除重跑）
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

站点改版、RSS 下线是常态，所以要能自己体检：

```bat
:: 11 个源挨个过一遍：能解析 / 带时间 / 正文 >400 字符
.\.venv\Scripts\python.exe tools\probe_sources.py

:: 探一个候选源值不值得加
.\.venv\Scripts\python.exe tools\probe_sources.py --url https://example.com/rss --label 某站
```

四个跟时间有关的坑，前三个在 `sources/timestamps.py` 里修掉，第四个在 `pipeline.py`：

| 坑 | 症状 | 修法 |
| --- | --- | --- |
| 无时区的本地时间（证券日报） | 整源静默归零 | 原文无时区标记时按 `+08:00` 折算 |
| 只有日期、没有时刻（列表页源） | 打分池永远排最后 | `_fair_pool()` 按源轮流取 |
| **北京时间标成 GMT**（InfoQ 中文） | 时间超前 8 小时，日志无异常 | 折算后仍超前 >1 小时就再纠一次 |
| **页面元数据覆盖 feed 时间** | 全库时间都变成 `T00:00:00Z` | `pick_published()` 优先选带钟点的那个 |

第四个坑最隐蔽：`trafilatura` 从正文页抽的 `date` 常常只有日期，而入库时写的是 `ext.published_at or stub.published_at`——页面元数据优先，于是 52 篇真实新闻的发布时间全被抹成当天零点。时间窗过滤在入库**之前**就做完了，所以流水线一路绿灯，只有按时间排序时才看得出错。

时间归一**只有一份实现**：`tech.py`（科技池）、`rss.py`（通用 `--source rss`）、`tools/probe_sources.py`（体检器）、`pipeline.py`（入库）都 import `timestamps.py`。这几处以前各写一套，结果体检通过、入库却错位——检查器和被检查的代码用不同口径，检查就没有意义。

---

## 5. 抽取：KGGen

`build` 只有一种行为：把每篇文章交给 [KGGen](https://github.com/stair-lab/kg-gen)（NeurIPS'25），拿回它给出的关系集合，谓词归一后入库。

选它的理由，是它比"手写 prompt"多做了两件事：

| 能力 | 手写 prompt 的做法 | KGGen 的做法 |
| --- | --- | --- |
| 实体聚类 | 自己维护别名表逐条对 | 生成时就按语义把 `Apple` / `Apple Inc.` / `AAPL` 归簇 |
| 产出上限 | 受 `max_tokens` 约束，每篇 25 条封顶 | 不设条数上限，一篇长文可以出 100+ 条关系 |

每篇的置信度固定写 0.85：KGGen 不返回逐条置信度，这个数字只是占位，不要拿它排序。

配置项（`kg/kggen.py` 读取，也可写进 `.env`）：

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `NEWSKG_KGGEN_MODEL` | 沿用 `NEWSKG_LLM_MODEL` | 抽取模型；不带 `provider/` 前缀时自动补 `openai/` |
| `NEWSKG_KGGEN_BASE_URL` | 沿用 `NEWSKG_LLM_BASE_URL` | 端点 |
| `NEWSKG_KGGEN_MAX_TOKENS` | 8192 | 单篇输出上限；调小会让长文的 JSON 被截断、整篇记 0 条 |

本地实测（`qwen3.7-plus`，一篇 4000 字左右的科技新闻，全链路 `fetch → build → export`）：单篇 15–36 条三元组，4 篇合起来 85 条、`--min-degree 2` 后 22 个节点 24 条边；`demo` 的 8 篇合成样例出 75 条。

| 环节 | 实测 |
| --- | --- |
| 实体归一 | 每篇约 1 处合并（`谷歌`→`Google`、`华为`→`Huawei`、全角括号→半角） |
| 谓词语言 | 中文文章全出中文谓词（`支持` / `指出` / `引入`） |
| 失败台账 | 抽到 0 条也会写 `runs`，不会反复重付费 |

谓词按语种分流：中文出中文谓词（`收购` / `发布` / `搭载`），英文出 `snake_case`（`acquired` / `located_in`）。分流是必要的——中文实体配英文谓词，图上的边会显得"半土半洋"，后续按谓词聚合也做不了。

---

## 6. 实体归一化

KGGen 抽出来的实体名有三种脏：写法不同（`NVIDIA` / `英伟达`）、多余标点（`“梦想”号` 带引号）、以及**类别词当实体**（`AI` / `技术` / `产品`）。三层处理，顺序不能换：

| 层 | 做什么 | 例子 |
| --- | --- | --- |
| 1 字符层 | NFKC、去包裹引号、压空白、去尾随标点 | `“梦想”号大洋钻探船` → `梦想号大洋钻探船` |
| 2 别名表 | `data/aliases.json` 里的同义写法合并到规范名 | `英伟达` → `NVIDIA`，`GTA 6` → `Grand Theft Auto VI` |
| 3 泛化过滤 | 丢掉泛化宾语与自环 | 删 `(某公司, 推出, 产品)` 这类无信息边 |

别名表是**手工维护**的，只做"写法层"消歧，不做上下文消歧（`Apple`/`apple` 仍会被合并）——这是已知限制，不是遗漏。

改完别名表不必重跑抽取：`.\run.cmd --db out\tech.sqlite3 renormalize` 会按当前表重写已有实体名。

---

## 7. 命令速查

| 子命令 | 干什么 | 典型用法 |
| --- | --- | --- |
| `fetch` | 抓取 + 过滤 + 入库 | `--source tech --limit 20 --days 3` |
| `build` | 用 KGGen 抽三元组建图 | `--limit 8` |
| `export` | 导出图 | `--format all`（json/cypher/graphml） |
| `stats` | 统计 | `.\run.cmd --db out\tech.sqlite3 stats` |
| `dump` | 打印三元组 | `--limit 20` |
| `renormalize` | 按别名表重写实体名 | 改完别名表后跑 |
| `reset` | 清空数据 | `--keep-articles` 可保留文章 |
| `demo` | 端到端演示（样例 + KGGen） | `.\run.cmd demo` |
| `init` | 建目录与空库 | `.\run.cmd init` |

常用环境变量（也可写进 `.env`）：

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `NEWSKG_LLM_BASE_URL` / `_API_KEY` / `_MODEL` | DeepSeek 官方 | 抽取端点（KGGen 沿用） |
| `NEWSKG_TOPIC_MODEL` | 同抽取模型 | 题材打分用的模型（可用更便宜的） |
| `NEWSKG_TOPIC_THRESHOLD` | 6 | 题材分数阈值 |
| `NEWSKG_TECH_FEEDS` | 全部 11 个 | 覆盖 `tech` 预设成员 |
| `NEWSKG_POLITE_DELAY` | 1.0 | 同域名抓取间隔（秒） |

---

## 8. 产物

`out/` 里的东西**全部可删可重建**：

| 文件 | 来源 | 内容 |
| --- | --- | --- |
| `*.sqlite3` | `fetch` / `build` | 文章、三元组、运行记录 |
| `graph.json` / `.cypher` / `.graphml` | `export` | 三种图格式 |
| `*.html` | `tools/view_graph_lite.py` | 单文件可交互图（断网可用） |
| `topic_scores.json` | `fetch` | 打分缓存（改阈值重跑不再付费） |

---

## 9. 已知限制

1. **没有任何量化评测**：gold 集与评测/对照脚本已随 `rule` / `llm` 一起删除，现在只能看三元组数量、抽样读原文、看图上是否成立。要报 P/R/F1 得重新标注。
2. **只做写法层消歧**：别名表不解决上下文歧义。
3. **中文品牌名会被翻成英文**：`华为`→`Huawei`、`比亚迪`→`BYD`，尽管提示词要求保留原文。
4. **权威优先的代价是科技密度**：11 个源里 7 个是通讯社/报社，60 篇候选要靠 LLM 打分才留下 20 篇。
5. **列表页源只到"天"**：新华网、科技日报无法提供精确到分钟的发布时间。
6. **单机 SQLite**：上万篇要换 Postgres 或图数据库，`export cypher` 是迁移入口。
7. **KGGen 是黑盒**：它的分块与聚类策略不可调，只能通过 `max_tokens` 间接影响产出。
8. **英文谓词归一不彻底**：中文谓词有词典（`收购`/`中标`/`签订`…），英文只靠一张小别名表，于是 `raises` / `raised` / `raised_on` 会变成三条不同的边。中文语料不受影响，跑英文语料时图上会显得碎。

---

## 10. 声明

- 所有新闻正文版权归原站点，本工程只做技术验证，不转载、不再分发正文。
- 抓取按 `NEWSKG_POLITE_DELAY` 限速，**不绕过任何反爬**：被 403/405/429 挡下的源直接放弃。
- `.env` 里的大模型 key 不要提交；仓库只保留 `.env.example`。
- `data/fixtures/` 是合成样例，不是真实新闻，不能当语料引用。
