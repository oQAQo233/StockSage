# 新闻知识图谱 · 交接说明

这份文档写给接手的人。读完你应该知道三件事：**我们做了什么**、**留下了什么**、**这些东西怎么用**。
不需要先读代码。

---

## 1. 一句话概括

抓 2026 年 11 月 4 日到 11 日的中文科技新闻，把每篇文章抽成一条条「主语—谓语—宾语」三元组，
再给实体和关系打标签、把同一个东西的不同叫法合并、把一词多义拆开，
最后得到一个可以用 SQL 或 Excel 直接查的知识图谱。

---

## 2. 整条流程

```
选源 → 抓取并统一成 Markdown → 抽三元组 → 后处理（4 步） → 导出
```

下面按顺序说每一步做了什么、为什么这么做。

### 2.1 选源

一共 5 个站，都是公开、能按日期往回翻的中文页面（不依赖 RSS，因为 RSS 只给最近几十条，翻不到历史）：

| 代号 | 是什么 | 文章数 |
| --- | --- | ---: |
| `chinanews_day` | 中国新闻网（按天归档） | 348 |
| `sina_day` | 新浪科技（产经滚动） | 147 |
| `ifanr_day` | 爱范儿 | 37 |
| `ndrc_day` | 国家发改委（新闻发布） | 7 |
| `most_day` | 科技部 | 2 |

选择思路：**中文 + 科技/产业 + 有日期归档**。后两个是政府口，篇数少，
但里面的关系（监管、规划、试点）是商业媒体没有的，属于互补。

- 时间窗口：2026-11-04 ~ 2026-11-11，共 8 天。
- 抓到 **541 篇**，其中 **5 篇是同一篇稿子的转载**，用内容指纹（SimHash）+ 文本重叠率判掉了。
  被判定重复的文章不会删除，而是记在 `articles.duplicate_of` 里指向它重复的那篇。

### 2.2 抓取与统一格式

每篇文章落两个文件：

- `data/raw/`：抓下来的原始 HTML（留档，一般用不到）
- `data/md/<article_id>.md`：统一后的 Markdown（**这个才是后面所有步骤的输入**）

Markdown 的真实开头长这样：

```markdown
---
article_id: 778585f73c8cd041
source: chinanews_day
url: https://www.chinanews.com.cn/aseaninfo/2026/11-04/10726318.shtml
title: 中国与东盟南宁共探AI赋能防震减灾区域合作
published_at: 2026-11-04T14:54:00Z
lang: zh
word_count: 779
---

# 中国与东盟南宁共探AI赋能防震减灾区域合作

【东盟专线】中国与东盟南宁共探AI赋能防震减灾区域合作

中新社南宁11月4日电 (韦佳秀)2026中国(广西)—东盟应急管理(防震减灾)合作与发展大会4日在南宁举办……
```

上面两行 `---` 之间那段叫 YAML 头，相当于文章的身份证（来源、原始链接、标题、时间、字数）。
正文只保留标题和段落，导航栏、广告、版权尾巴都去掉了。

**为什么要统一格式**：五个站的 HTML 结构完全不同。不先抹平成同一种 Markdown，
后面抽三元组就要为每个站写一套解析，改一个站会连带改抽取逻辑。抹平之后，
抽取只认一种输入。

这些元信息同时也写进了数据库的 `articles` 表，用 SQL 查更方便。

### 2.3 抽三元组

用 **KGGen**（一个开源的图谱抽取工具）把每篇正文交给大模型，抽成 `(主语, 谓语, 宾语)`：

```
中国地震局局长王昆 --任职于--> 中国地震局
中国地震局 --位于--> 南宁
```

- 共抽出 **13346 条**，`extractor` 字段全是 `kggen`。
- 每条都记着 `article_id`（来自哪篇文章）、`published_at`（发布时间）、`source`（哪个站）。
  **这是后面能回溯到原文的关键**，不要丢。

### 2.4 抽完之后做的四件事

原始三元组直接拿来用有三个问题：谓语太碎、实体名不统一、一个词有多个意思。
所以做了四步后处理，结果都存在同一个库里：

| 做了什么 | 为什么 | 结果存在哪 |
| --- | --- | --- |
| **关系归一** | 2210 个不同谓语太碎（"任职于/担任/出任"其实是同一种关系），不归类没法统计 | `predicate_map`、`rel_family` |
| **实体类型** | 给每个实体打 12 类标签，才能按类筛选（只看公司、只看人物） | `entity_type` |
| **实体合并** | 同一个东西的不同写法合成一个（"进博会" = "第九届中国国际进口博览会"），否则图是碎的 | `entity_merge` |
| **实体消歧** | 一个词有多个意思时分清楚（苹果=公司 / 苹果=水果），否则查"苹果"会查出一堆水果 | `entity_sense`、`triple_sense` |

几个数：谓语 2210 个归进 **25 个关系族**；实体 **10760** 个字符串合并后剩 **10408** 个（并入 352 个别名）；
**34 个实体**被拆出 **64 个义项**（其中 20 个是多义词）。

> 术语对齐一下，免得混：
> **实体合并（实体解析）**= 同一个对象的不同叫法合成一个；**实体消歧**= 同一个词的不同意思分开。

---

## 3. 交接内容（重点看这节）

### 3.1 一张表看完

| 你要的东西 | 在哪 | 格式 |
| --- | --- | --- |
| 知识图谱本体 | `out/kg.sqlite3` | SQLite 数据库（单文件，6.7 MB） |
| **一条边一行的宽表（推荐入口）** | `out/kg_labeled.csv` | CSV，13346 行 × 16 列，2.4 MB |
| 图文件（导入 Neo4j / Gephi） | `out/graph.json`、`graph.cypher`、`graph.graphml` | JSON / Cypher / GraphML |
| 原文 | `data/md/<article_id>.md` | Markdown + YAML 头 |
| 给人看的清单 | `out/labels/` | Markdown / CSV |

### 3.2 图谱本体：`out/kg.sqlite3`

一个文件装下所有东西，用任何 SQLite 客户端（DB Browser for SQLite、DBeaver、Python 的 `sqlite3`）都能打开。

核心就两张表：

**`articles`（文章表，541 行）**

| 字段 | 含义 |
| --- | --- |
| `article_id` | 文章编号，和文件名 `data/md/<article_id>.md` 一一对应 |
| `source` | 来源站代号 |
| `url` / `canonical_url` | 原始链接 / 去掉追踪参数后的链接 |
| `title` | 标题 |
| `published_at` | 发布时间（UTC，末尾带 `Z`） |
| `markdown_path` | 统一后的 Markdown 路径 |
| `word_count` | 字数 |
| `duplicate_of` | 非空表示这篇是转载，值指向它重复的那篇的 `article_id` |

**`triples`（三元组表，13346 行）**

| 字段 | 含义 |
| --- | --- |
| `triple_id` | 这条边的唯一编号（16 位十六进制） |
| `subject` / `predicate` / `object` | 主语 / 谓语 / 宾语 |
| `article_id` | **来自哪篇文章**（对应 `data/md/<article_id>.md`） |
| `published_at` / `source` | 那篇文章的发布时间与来源 |
| `extractor` | 用哪个工具抽的（现在全是 `kggen`） |
| `confidence` | 置信度，**目前是固定值 0.85，不是真实概率**，见 §5 |

另外还有一个视图 `v_triples`，把「同一组三元组」合并成一行，附带：

- `support`：有几篇**不同**文章支持它（数字越大越可信）
- `first_seen` / `last_seen`：最早/最晚出现时间
- `sources`：哪些站报道过

**查数据时优先用 `v_triples`**：直接查 `triples` 会把同一件事的多次报道当成多条。

### 3.3 原文怎么找

每条三元组都有 `article_id`，按编号打开 `data/md/<article_id>.md` 就是原文：

```
triples.article_id = facdee47493e3be1
        ↓
data/md/facdee47493e3be1.md   ← YAML 头有原始链接和标题，正文里能搜到这句话
```

举个真实例子：三元组 `西迪·穆罕默德·本·阿卜杜拉大学 --位于--> 非斯` 的 `article_id` 是 `facdee47493e3be1`，
打开 `data/md/facdee47493e3be1.md` 就能找到报道原文和出处链接。

> **注意粒度**：我们只做到「能定位到文章」，没有精确到句子。如果要句子级，
> 拿实体名在正文里做一次字符串匹配即可（实体名就是最好的关键词）。

### 3.4 宽表：`out/kg_labeled.csv`（最省事的入口）

把「三元组 + 四种标签」拼成了一张表，一行一条边，Excel 双击就能开。
如果不想写 SQL，直接用它。

| 列 | 含义 |
| --- | --- |
| `triple_id` | 边的编号 |
| `subject` | 主语（原文里的写法） |
| `subject_canonical` | 主语合并后的规范名（没合并过就和 `subject` 相同） |
| `subject_type` | 主语类型（12 类之一） |
| `predicate` | 谓语（原文里的写法） |
| `rel_family` | 谓语归到哪个关系族（25 族之一） |
| `object` / `object_canonical` / `object_type` | 宾语，同上 |
| `published_at` / `source` | 时间、来源 |
| `article_id` | 对应 `data/md/<article_id>.md` |
| `subject_sense` / `subject_sense_label` | 主语的义项编号与名字（如 `苹果#1` / `公司`） |
| `object_sense` / `object_sense_label` | 宾语的义项，同上 |

真实的两行（省略了部分列）：

```
subject,subject_canonical,subject_type,predicate,rel_family,object,object_type
西迪·穆罕默德·本·阿卜杜拉大学,非斯西迪·穆罕默德·本·阿卜杜拉大学,机构组织,位于,位于,非斯,地点行政
叶玉如,叶玉如,人物,开发,研发,血液检测方法,技术概念
```

义项那四列**只有 34 个实体有值**，其它行是空的——这是正常的，只有多义词才需要消歧。

### 3.5 给人看的清单：`out/labels/`

| 文件 | 内容 |
| --- | --- |
| `SCHEMA.md` | 一页纸总览：多少实体、多少关系、多少类 |
| `rel_families.md` | 25 个关系族的定义、包含哪些原始谓语、例子 |
| `entity_types.md` | 12 个类型的定义、各有多少实体、代表实体 |
| `merge_report.csv` | 352 条合并记录：谁并进了谁、用什么方法判的 |
| `sense_report.csv` | 34 个实体的义项列表 |
| `sense_edges.csv` | 339 条被判定过义项的边 |

这些是**给人读的**，不是给程序读的。要数据用 §3.2 或 §3.4。

### 3.6 图文件

- `out/graph.json`：`{"nodes": [...], "edges": [...]}`，节点是 `{id, name}`，边是 `{source, target, predicate, confidence}`
- `out/graph.cypher`：Neo4j 的 `MERGE` 语句，可直接执行
- `out/graph.graphml`：Gephi / yEd 能直接打开

⚠️ **这三个文件只存了「谁—谓词—谁」，不含实体类型、关系族、规范名**。
要带标签的数据，用 `out/kg_labeled.csv`，或者按 §4 的 SQL 自己 join。

---

## 4. 三个常用查询

**① 查一个实体相关的所有边**（按支持文章数排序）

```sql
SELECT subject, predicate, object, support
FROM v_triples
WHERE subject = '苹果' OR object = '苹果'
ORDER BY support DESC
LIMIT 20;
```

**② 看某个关系族有哪些边**

```sql
SELECT t.subject, t.predicate, t.object
FROM triples t
JOIN predicate_map pm ON pm.predicate = t.predicate
WHERE pm.family = '合作签约'
LIMIT 20;
```

**③ 按实体类型找度最高的实体**

```sql
SELECT et.entity, COUNT(*) AS n
FROM entity_type et
JOIN triples t ON t.subject = et.entity OR t.object = et.entity
WHERE et.etype = '公司企业'
GROUP BY et.entity
ORDER BY n DESC
LIMIT 10;
```

用 Python / pandas 读宽表：

```python
import pandas as pd

df = pd.read_csv("out/kg_labeled.csv")
# 公司企业之间的合作签约
df[(df.subject_type == "公司企业") & (df.rel_family == "合作签约")][
    ["subject", "object", "published_at"]
].head(20)
```

---

## 5. 接手前必须知道的坑

这一节是**我们踩过的地方**，照着做能少走弯路。

1. **`confidence` 不是真概率**。13346 条全是 `0.85`，是抽取时写死的占位值，
   不要拿它做阈值过滤。真正能用的可信度信号是 `v_triples.support`（几篇文章支持）。
2. **关系族只有 25 个，兜底是「其他」**。1914 条边（14.3%）落在「其他」，
   因为那些谓语（"完成""通过""打造"）的语义取决于上下文，硬塞进某个族就是错标。
   用的时候记得把「其他」单独看，别当成一个真实关系。
3. **别名合并是保守的，会漏**。只并了 352 个（330 组），因为候选对要逐对让模型判定，
   时间不够没判完。**没判过的不会被合并**——所以是"漏并"，不是"错并"。
   如果发现两个节点其实是同一个东西，先查 `entity_merge` 里有没有，没有就是没判到。
4. **消歧只覆盖了 34 个实体**。只有出现次数多、且邻居类型明显不一致的实体才送去消歧，
   其余实体的边没有义项信息。查冷门实体时不要期待 `*_sense` 列有值。
5. **有 2 个实体是「未标注」**。类型判不出来就留空，我们没有拿「其他」去填充——
   这两件事不一样：`未标注`=不知道，`其他`=确定是杂项。
6. **语料只有 8 天、541 篇**。这是课程作业规模的数据，不要当成"科技新闻的全量"。
   时间范围之外的、这 5 个站之外的，都没有。
7. **抽取有损**。一篇长文一般出 10~30 条关系，不是文章里的全部事实。
   图里没有，不代表原文没提。
8. **时间统一存 UTC**（`2026-11-04T14:54:00Z`），北京时间要 +8 小时。

---

## 6. 目录结构

```
knowledge_merge/
├─ data/
│  ├─ md/              # 统一后的 Markdown（<article_id>.md），抽取的输入
│  └─ raw/             # 原始 HTML，留档用
├─ out/
│  ├─ kg.sqlite3       # ★ 知识图谱本体
│  ├─ kg_labeled.csv   # ★ 一条边一行的带标签宽表
│  ├─ graph.json/.cypher/.graphml   # 图文件（不含标签）
│  └─ labels/          # 给人看的清单
├─ src/newskg/
│  ├─ sources/         # 各站的抓取实现
│  ├─ convert.py       # HTML → 统一 Markdown
│  ├─ dedup.py         # 转载判重
│  ├─ kg/              # 三元组抽取（KGGen 后端）
│  └─ schema/          # 后处理四步：关系归一、实体类型、合并、消歧
├─ tools/              # 一些单独跑的脚本（看图、导宽表）
└─ docs/               # 过程笔记（PIPELINE.md 是后处理四步的实现细节；
                       # 其余是开发过程中的记录，已过时，仅供参考）
```

**`src/newskg/schema/`** 是后处理那四步的代码，每一步都能单独重跑、断点续跑
（模型调用的结果有缓存，重跑不会重复花钱）。细节见 `docs/PIPELINE.md`。

---

## 7. 如果想重跑（可选）

环境：Python 3.10+（开发用 3.14）。抽取依赖 KGGen，装在单独的 `.kgvenv` 目录里。

```powershell
$env:PYTHONPATH="src;.kgvenv"
.\.venv\Scripts\python.exe -m newskg.cli stats          # 看统计
.\.venv\Scripts\python.exe -m newskg.cli export --format all
.\.venv\Scripts\python.exe -m newskg.schema.cli --step report
.\.venv\Scripts\python.exe tools\export_labeled_csv.py  # 重新生成宽表
```

抓取和抽取需要 `.env` 里配一个 OpenAI 兼容的模型端点（`NEWSKG_LLM_BASE_URL` / `_API_KEY` / `_MODEL`）。
没有 key 也能读库、导表、看图，只是不能重跑抽取。

---

## 8. 这个仓库不做什么

- **不做报告生成**：这里是"到知识图谱为止"的仓库。把图变成一篇给人读的文章、
  按自然语言提问检索，那是下游的事，相关代码已经移除了。
- **不做实时更新**：语料是一次性抓的固定窗口，没有定时任务。
- **不做多语言**：只有中文语料，英文实体（Apple、NVIDIA）是原文里的写法，没有做翻译对齐。