# 四步实现方案（给明天执行用）

> 写于 2026-11-22 00:20（Asia/Shanghai）。今天断电，本文档是明天唯一依据。
> 执行者：明天的我。用户不必读完全文，只需看 §12「需要你确认的」。
>
> **一句话方案**：在不改原始 `triples`、不重跑抽取的前提下，新增 7 张表 + 4 个步骤脚本，
> 把「关系」收敛成 25 个族、把「实体」打成 12 类、把同一实体合并、把多义实体拆开，
> 最后产出 3 份 label 清单。全部语义判断走 LLM（端点已通），结构性判断走字符串规则
> （本机无法联网装包/下模型）。

---

## 0. 冷启动检查清单（明天第一件事，5 分钟）

按顺序做，任何一条不过就先修，不要往下跑。

```powershell
cd C:\JRTAT\Application\DSH\workspace\knowlegde_merge
$env:PYTHONPATH="src;.kgvenv"
$py = ".\.venv\Scripts\python.exe"

# 1) 断电后数据库完整性
& $py -c "import sqlite3;c=sqlite3.connect('out/kg.sqlite3');print(c.execute('pragma quick_check').fetchone())"
# 期望: ('ok',)

# 2) 关键数字没变（断电不能丢数据）
& $py -c "import sqlite3;c=sqlite3.connect('out/kg.sqlite3');print('articles',c.execute('select count(*) from articles').fetchone()[0],'triples',c.execute('select count(*) from triples').fetchone()[0])"
# 期望: articles 541, triples 13346

# 3) 没有残留 python 进程
Get-Process python -ErrorAction SilentlyContinue | Select-Object Id,StartTime
# 期望: 空。有就 Stop-Process

# 4) 端点仍然可用（不打印 key）
& $py .tmp\probe_llm.py
# 期望: TCP OK / models 数 23

# 5) 备份（后面所有写操作都基于这份备份可回滚）
Copy-Item out\kg.sqlite3 out\kg.sqlite3.bak-before-schema -Force
```

> 若 `quick_check` 不是 ok：先 `sqlite3 out/kg.sqlite3 "PRAGMA integrity_check"` 看坏在哪；
> WAL 文件残留（`kg.sqlite3-wal`）通常 reconnect 一次就会合并，不要手删。

---

## 1. 环境事实（已实测，别再假设）

| 项 | 实测结果 | 对方案的影响 |
|---|---|---|
| Python | 3.14.6 | 只能用标准库 + 已装包 |
| `.venv` | **没有 pip 模块**（`python -m pip` 报 No module named pip） | 装不了新包 |
| `.venv` 包 | 无 sentence-transformers / torch / sklearn / faiss | 见下 |
| `.kgvenv` 包 | torch 2.15.0+cpu、sentence_transformers 6.2.0、sklearn 1.9.1、numpy 2.5.4 | **可 import，但没有模型权重** |
| 缺失包 | rapidfuzz / pypinyin / jieba / hdbscan / faiss 全缺 | 字符串相似度只能用 `difflib`（标准库） |
| HF 缓存 | **不存在**（无 `~/.cache/huggingface`） | BGE-M3 没有本地副本 |
| 网络 | huggingface.co / modelscope.cn / hf-mirror.com / pypi.org / 清华源 / 阿里源 **全部 TLS 失败** | 下不了模型、装不了包 |
| LLM 端点 | `token-plan.maas.qianwenaiapi.com` **通**，23 个模型可用 | 语义判断全靠它 |
| `/embeddings` | **404，不支持** | 不能用 API 向量 |
| `.env` | 已有 `NEWSKG_LLM_API_KEY`（114 字符）等 8 个键 | **不需要用户再提供 key** |

**结论（决定整个方案形态）**：明天**不使用任何向量/embedding**。
所有「像不像」的判断用字符串规则；所有「是什么意思」的判断用 LLM。

---

## 2. 数据现状（已实测，写进报告要用的）

| 指标 | 数值 |
|---|---|
| 文章 / 唯一稿 | 541 / 536 |
| 三元组 | 13346 |
| 实体（subject∪object 去重） | **10760** |
| 谓词（distinct） | **2210**（中文 12917 条边 / 英文 429 条边） |
| 只出现 1 次的谓词 | 1123 个，承载 1123 条边（**8.4%**，且多为高信息量关系） |
| 出现 ≤5 次的谓词 | 1870 个，承载 3238 条边（24.3%） |
| 出现 ≥6 次的谓词 | 340 个，承载 10108 条边（75.7%） |
| 前 40 个谓词覆盖 | 6086 条边（45.6%） |
| 度数 ≥2 的实体 | 4416 |
| 度数 ≥3 的实体 | 2292 |
| 度数 ≥5 的实体 | 1073 |
| 规则可直接定类型的实体 | 1041（9.7%：组织 788 / 地点 238 / 数值 15） |

**已有可复用的宝藏**：图里已存在 184 条「别名类」边，是**零成本合并种子**：

| 谓词 | 条数 | 样例 |
|---|---|---|
| 简称 | 108 | `第九届中国国际进口博览会 --简称--> 进博会` |
| 别名 | 26 | `甲骨文 --别名--> Oracle`、`Token --别名--> 词元` |
| 又称 | 26 | `智元 --又称--> 智元机器人` |
| 又名 | 14 | `苹果全球开发者大会 --又名--> WWDC` |
| 全称 | 9 | `第九届进博会 --全称--> 第九届中国国际进口博览会` |
| 缩写 | 1 | `皮开尔文 --缩写--> pK` |

> 注意：`进博会` 的合并在图里**已经有证据了**，不需要 LLM 猜。

---

## 3. 总体结构

### 3.1 四个步骤的依赖顺序（不可换）

```
Step 1 关系归一 ──┐
                  ├──► Step 3 实体合并 ──► Step 4 实体消歧 ──► 交付 3 份清单
Step 2 实体类型 ──┘         ▲                    ▲
        │                    │                    │
        └────────────────────┴────────────────────┘
              （类型是合并的硬约束、是消歧的主特征）
```

- **Step 1 与 Step 2 可并行**（互不依赖），但都必须在 3 之前
- **Step 3 依赖 Step 2 的类型**（`中国` vs `中国移动` 靠类型否决）
- **Step 4 依赖 Step 2**（用邻居类型分簇，替代向量聚类）

### 3.2 新增代码与产物

```
src/newskg/schema/
  __init__.py
  ddl.py           7 张表的建表语句（幂等）
  llm.py           ask_json()：批量调用 + 缓存 + 解析兜底（核心）
  rel.py           Step 1
  typing.py        Step 2
  merge.py         Step 3
  senses.py        Step 4
  report.py        生成 3 份清单
tools/
  run_schema.py    统一 CLI：--step rel|type|merge|sense|report --limit N
out/labels/
  rel_families.md      关系族清单（族 / 定义 / 吸收的原始谓词 / 条数）
  entity_types.md      实体类型清单（类型 / 定义 / 实体数 / 示例）
  merge_report.csv     合并对照（alias, canonical, method, evidence）
  sense_report.csv     消歧对照（entity, sense, edges, evidence）
  SCHEMA.md            总览：实体 N 个标签 + 关系 M 个标签（用户要的最终答案）
```

### 3.3 新增 7 张表

```sql
-- ① 关系族目录（手工定义，25 行）
CREATE TABLE IF NOT EXISTS rel_family (
    family      TEXT PRIMARY KEY,
    definition  TEXT NOT NULL,
    examples    TEXT
);

-- ② 谓词 → 族 映射（2210 行，全覆盖）
CREATE TABLE IF NOT EXISTS predicate_map (
    predicate   TEXT PRIMARY KEY,
    family      TEXT,
    direction   TEXT,        -- forward | reverse | symmetric
    decided_by  TEXT,        -- dict | rule | llm | human | other
    confidence  REAL,
    evidence    TEXT,        -- 样例三元组 / 原文标题
    note        TEXT
);

-- ③ 实体类型（10760 行，全覆盖）
CREATE TABLE IF NOT EXISTS entity_type (
    entity      TEXT PRIMARY KEY,
    etype       TEXT,
    confidence  REAL,
    decided_by  TEXT,        -- rule | llm | human | unset
    evidence    TEXT
);

-- ④ 实体合并（alias → canonical）
CREATE TABLE IF NOT EXISTS entity_merge (
    alias       TEXT PRIMARY KEY,
    canonical   TEXT NOT NULL,
    method      TEXT,        -- seed_alias | norm_key | substring | edit | llm
    score       REAL,
    decided_by  TEXT,
    evidence    TEXT
);

-- ⑤ 义项目录（消歧结果）
CREATE TABLE IF NOT EXISTS entity_sense (
    entity      TEXT NOT NULL,
    sense_id    TEXT NOT NULL,   -- '苹果#1'
    label       TEXT NOT NULL,   -- '公司' / '水果'
    definition  TEXT,
    evidence    TEXT,
    PRIMARY KEY (entity, sense_id)
);

-- ⑥ mention 级 sense 分配
CREATE TABLE IF NOT EXISTS triple_sense (
    triple_id   TEXT NOT NULL,
    entity      TEXT NOT NULL,
    role        TEXT NOT NULL,   -- subject | object
    sense_id    TEXT PRIMARY KEY,
    decided_by  TEXT,
    evidence    TEXT
);

-- ⑦ LLM 缓存 + 台账（断电续跑的核心）
CREATE TABLE IF NOT EXISTS llm_cache (
    cache_key   TEXT PRIMARY KEY,   -- sha1(step|model|prompt)
    step        TEXT,
    response    TEXT,
    created_at  TEXT
);
CREATE TABLE IF NOT EXISTS schema_runs (
    step        TEXT,
    item        TEXT,
    status      TEXT,              -- ok | failed | skipped
    detail      TEXT,
    created_at  TEXT,
    PRIMARY KEY (step, item)
);
```

**为什么必须有 `llm_cache`**：今天断电就是教训。任何一批调用完成后立刻
`INSERT OR REPLACE`，重跑时先查缓存。**同一份 prompt 永不重复付费。**

### 3.4 LLM 调用的统一封装

新增 `src/newskg/schema/llm.py`：

```python
ask_json(step: str, prompt: str, *, model=None, max_tokens=2000, timeout=120) -> dict|list
```

要求：
1. 先查 `llm_cache`（key = sha1(f"{step}|{model}|{prompt}")），命中直接返回
2. 未命中才调 `newskg.llm_client.chat()`（已有 3 次重试 / 429 退避）
3. 解析 JSON：先 `json.loads`，失败则剥 ```json 围栏再试，再失败则记 `schema_runs(status='failed')` 并返回 `None`（**不抛异常、不阻断整批**）
4. 成功后写 `llm_cache`
5. **串行调用**，批间 `sleep(1.5)`；单批 ≤10 分钟

**模型选择**：
- 批量结构化任务用 `qwen3.7-plus`（`.env` 默认值，实测稳定）
- 追求便宜可换 `qwen3.8-flash`，但先在 20 条样本上对比准确率

---

## 4. Step 1：关系归一

### 4.1 目标

`2210 个谓词` → 每个都挂到 `25 个关系族` 之一。**不删任何谓词、不删任何边。**

### 4.2 交付

`predicate_map` 2210 行全覆盖；`out/labels/rel_families.md`。

### 4.3 关系族定义（25 个，先写进 `rel_family`）

| # | family | 定义 | 典型谓词 |
|---|---|---|---|
| 1 | 任职 | 人在组织中担任职务 | 任职于、就职于、供职于、担任、理事长、CEO |
| 2 | 归属 | A 属于/隶属于 B（含 is-a「A 是 B 的一种」；含反方向的「拥有/持有」，用 direction 区分） | 属于、隶属于、归入、是、拥有 |
| 3 | 包含 | A 包含/涵盖 B（与「归属」方向相反） | 包含、包括、涵盖、涉及、覆盖 |
| 4 | 位于 | 实体处于某地 | 位于、地处、坐落在 |
| 5 | 活动地点 | 活动在哪里举办 | 举办地、举办于、举办、举办地点 |
| 6 | 发布推出 | 主体发布/推出产品 | 发布、推出、亮相、上市、发售、展出、展示 |
| 7 | 研发 | 研发/开发/攻关 | 研发、研制、开发、攻关 |
| 8 | 生产制造 | 生产/制造/代工 | 生产、制造、代工生产、投产 |
| 9 | 使用应用 | 使用/采用/搭载/应用于 | 使用、采用、搭载、配备、应用于、用于 |
| 10 | 技术支持 | 技术/能力对主体的支撑 | 支持、支撑、赋能、依托、基于、接入、提供、服务 |
| 11 | 合作签约 | 合作/签约/供应/采购 | 合作、签订、供应、采购、联合研发 |
| 12 | 投资交易 | 投资/收购/股权/增资 | 投资、收购、并购、增资、控股、参股、增持、减持、回购、中标 |
| 13 | 组织设立 | 设立/成立/组建 | 设立、成立、组建、创办 |
| 14 | 参与活动 | 参加/出席/参展/主办/承办 | 参与、参加、出席、参展、主办、承办 |
| 15 | 人物言论 | 人物表态/介绍/认为 | 表示、指出、认为、强调、介绍、称 |
| 16 | 媒体报道 | 媒体/机构报道与统计 | 报道、披露、公布、统计 |
| 17 | 因果影响 | 推动/促进/带动/提升 | 推动、促进、带动、提升、影响、赋能 |
| 18 | 属性参数 | 实体的规格/参数/能力 | 支持格式、生成图像分辨率、时长、重量 |
| 19 | 命名别名 | 简称/全称/别名 | 简称、全称、别名、又称、又名、缩写 |
| 20 | 比较评价 | 高于/低于/领先 | 高于、低于、领先、超过、优于 |
| 21 | 法规监管 | 处罚/立案/起诉/批准 | 处罚、立案、起诉、问询、禁止、批准 |
| 22 | 医疗 | 诊断/检查/治疗 | 检查出、诊断、治疗、患 |
| 23 | 时间 | 发生于/上线于/将于 | 发生于、上线于、发布于、将于、首发于 |
| 24 | 竞争替代 | 竞争/对标/取代 | 竞争、对标、取代、替代 |
| 25 | 其他 | 无法归类 / 语义噪音 | （兜底，并保留原谓词） |

> 族的粒度可以调，但**不要超过 30 个**，否则「出清单」的意义就没了。

### 4.4 判定流水线（四层，逐层降成本）

**第 1 层｜确定性归一（复用现有代码）**
调 `newskg.kg.base.normalize_predicate()`（剥「将/已/拟」前缀、「了/中」后缀）。
结果写 `predicate_map.note` 记录原始写法；`decided_by='rule'`。

**第 2 层｜字典直配（零成本，覆盖约 200~400 个）**
用 `newskg.kg.base.PREDICATE_ALIASES_ZH`（50 个规范词）+ `PREDICATE_ALIASES`（英文 40 个），
命中即 `decided_by='dict'`，并映射到族。
**注意**：字典里的规范词是「细粒度关系」，不是「族」——中间要加一层映射（§4.5）。

**第 3 层｜LLM 批量归类（覆盖剩余，约 60~75 次调用）**
- 批大小 **30 个谓词/次**
- 每个谓词带 **1 条样例三元组 + 两端类型 + 原文标题**（类型来自 Step 2；若 Step 2 未完成，先用规则预标的类型或留空）
- 输出严格 JSON 数组

Prompt 模板（固化进代码）：

```
你是中文科技新闻知识图谱的关系本体专家。下面给你一批抽取出来的关系谓词，
请把每一个归入给定的关系族。

【关系族清单】
1 任职: 人在组织中担任职务
2 归属: A 属于/隶属于 B
...（25 个，全量贴入）

【规则】
- 必须从上面 25 个族里选，不许自创
- 判断依据是「样例三元组」的语义，不是谓词字面
- direction: forward（主→客）/ reverse（客→主，如「包含」相对「属于」）/ symmetric
- 若确实无法归类，family 填 "其他"
- 只输出 JSON，不要解释

【待归类】
{predicates_block}   # 每条形如：
                     # {"predicate":"理事长","sample":"谢少锋 --理事长--> 开放原子开源基金会","subj_type":"人物","obj_type":"组织","title":"促技术研发走向产业应用…","n":1}

【输出格式】
[{"predicate":"理事长","family":"任职","direction":"forward","confidence":0.95}]
```

- 解析成功后写 `predicate_map(decided_by='llm')`
- **对 `n<=5` 的长尾谓词同样处理**（这正是上一轮方案被否掉的地方：低频不等于无效）

**第 4 层｜一致性校验（必做）**

```sql
-- ① 每个谓词都有归属
SELECT predicate FROM triples
EXCEPT SELECT predicate FROM predicate_map;
-- 必须为空

-- ② 同一族内不能混入方向相反的谓词而没标 direction
SELECT family, COUNT(DISTINCT direction) d FROM predicate_map
GROUP BY family HAVING d > 2;

-- ③ 族与端点类型相容性抽查：任职族应连 人物→组织
SELECT p.predicate, t.subject, t.object
FROM triples t JOIN predicate_map p ON p.predicate = t.predicate
WHERE p.family = '任职'
  AND (SELECT etype FROM entity_type WHERE entity=t.subject) NOT IN ('人物','组织','其他')
LIMIT 50;
-- 有异常不一定要改，但要记进 rel_families.md 的「存疑」小节
```

### 4.5 中间层：细粒度关系（不要丢掉）

`放` 在 `predicate_map` 里再加一列 `sub_relation`（可选，第二阶段做）：
- `收购` 与 `并购` 同属「投资交易」族，但细粒度保留差异
- 这正是 `base.py` 注释里坚持的设计（交易结构要能区分）

### 4.6 验收

- [ ] `predicate_map` 行数 = 2210，无遗漏
- [ ] `rel_family` 25 行，每行有 definition
- [ ] 「其他」族占比 < 15%（若超了，说明族设计不够，回去补）
- [ ] 抽样 20 个谓词人工核对，准确率 ≥ 90%
- [ ] `out/labels/rel_families.md` 生成，列出每族下的原始谓词与条数

---

## 5. Step 2：实体类型

### 5.1 目标

`10760 个实体` → 每个贴 1 个类型标签（12 类闭集）。

### 5.2 交付

`entity_type` 10760 行；`out/labels/entity_types.md`。

### 5.3 类型闭集（12 类）

| # | etype | 定义 | 反例（易错） |
|---|---|---|---|
| 1 | 公司企业 | 营利性企业、品牌 | 「中国」不是公司；「苹果」在此语料指公司 |
| 2 | 机构组织 | 政府/协会/院所/媒体/国际组织 | 「工信部」「新华社」「中科院」 |
| 3 | 人物 | 自然人（含职务身份） | 「记者」「用户」是泛称，不是人物 |
| 4 | 地点行政 | 国家/省/市/县/区/镇/乡 | 「上海」「浙江」「乌镇」 |
| 5 | 地点设施 | 场馆/园区/车站/基地/工程 | 「国家会展中心」「航天桥科创孵化论坛」是活动，不是设施 |
| 6 | 产品型号 | 具体产品或型号 | 「iPhone Duo」「第二代海豹07」「Hy4」 |
| 7 | 技术概念 | 技术/标准/算法/框架 | 「AI」「5G」「具身智能」「大模型」 |
| 8 | 活动事件 | 会议/展会/论坛/比赛/发布 | 「进博会」「世界互联网大会乌镇峰会」 |
| 9 | 作品内容 | 影视/游戏/报告/标准文件/论文 | 「原神」「白皮书」 |
| 10 | 数值度量 | 纯数字、金额、比例、日期 | 「110」「4.27%」「2026年」（**建议后续过滤**） |
| 11 | 泛称角色 | 类指词，无具体指称 | 「机器人」「医生」「企业」「用户」 |
| 12 | 其他 | 无法判定 | — |

> **注意**：「3G/4G/5G/6G」是**技术概念**，不是数值。规则打标时别被 `\d` 误伤。

### 5.4 判定流水线

**第 1 层｜规则打标（1041 个，9.7%）**
正则见 `.tmp/probe_sizing.py` 的 `rule_type()`，但**先修两个 bug**：
- 数值规则加排除：`^(3G|4G|5G|6G|WiFi|AI|AR|VR)$` 不算数值
- 组织规则排除「活动」后缀：含「论坛/峰会/大会/博览会/发布会」→ 优先判「活动事件」

**第 2 层｜LLM 批量打标（只对度数 ≥2 的 4416 个）**
- 批大小 **50 个实体/次** → 约 **89 次调用**
- 每个实体带 **最多 3 条邻居边**（作为语义证据）

Prompt 模板：

```
你是中文科技新闻知识图谱的实体类型标注器。给每个实体从 12 类中选 1 个类型。

【类型清单】1 公司企业 / 2 机构组织 / 3 人物 / 4 地点行政 / 5 地点设施 /
6 产品型号 / 7 技术概念 / 8 活动事件 / 9 作品内容 / 10 数值度量 /
11 泛称角色 / 12 其他

【判定规则】
- 依据是「邻居边」的语义，不是名字字面。例如：
  "苹果 --发布--> iPhone Duo" → 公司企业（不是水果）
  "机器人手指 --抓握--> 苹果" 说明这条边里的苹果是物体，但实体整体按多数边判
- 名字是泛称（机器人/医生/企业/用户）→ 11 泛称角色
- 纯数字/金额/比例/日期 → 10 数值度量
- 只输出 JSON

【待标注】
{"entity":"苹果","degree":94,"edges":["苹果 --发布--> iPhone Duo","苹果 --研发--> iOS 27","约翰·特努斯 --任职于--> 苹果"]}

【输出】
[{"entity":"苹果","etype":"公司企业","confidence":0.98}]
```

**第 3 层｜度数 =1 的 6344 个实体（降级处理）**
- 先跑规则；剩下的标 `decided_by='unset'`，`etype='其他'`
- **不要为它们花 LLM 预算**（只出现一次，对下游合并/消歧没有价值）
- 报告里注明覆盖率：LLM 覆盖 4416，规则覆盖 1041，其余 5303 未标

**第 4 层｜一致性校验**

```sql
-- 类型 vs 边类型冲突：地点不应该是「研发」的主体
SELECT t.subject, e.etype, t.predicate, t.object
FROM triples t JOIN entity_type e ON e.entity = t.subject
WHERE e.etype = '地点行政' AND t.predicate IN ('研发','发布','收购','任职于')
LIMIT 100;

-- 数值类实体清单（后续过滤用）
SELECT entity, etype FROM entity_type WHERE etype = '数值度量';
```

### 5.5 验收

- [ ] 度数 ≥2 的实体 100% 有 etype
- [ ] 数值度量类 15~40 个（实测规则命中 15，LLM 可能再抓几个）
- [ ] 抽样 30 个实体人工核对，准确率 ≥ 85%
- [ ] `entity_types.md` 生成，每类列实体数与 10 个示例

---

## 6. Step 3：实体合并

### 6.1 目标

把「同一实体的不同写法」合成一个规范名。`10760 → 预计 7500~9000`。

### 6.2 交付

`entity_merge`；`out/labels/merge_report.csv`。

### 6.3 四层候选 + 一道硬约束

**第 1 层｜种子层（零成本，最高置信）**

直接读图里已有的 184 条别名类边：

```sql
SELECT subject, object, predicate FROM triples
WHERE predicate IN ('简称','全称','别名','又称','又名','缩写');
```

处理规则：
- 统一成无向对：`(subject, object)`
- **过滤垃圾别名**：若 alias 一方是泛称（长度 ≤2 且在全库度数 >50，如「意见」「一张图」「Balance」），丢弃
- `canonical` 取：度数更高的一方；若接近，取更长的全称形式
- `decided_by='seed_alias'`，`score=1.0`

**第 2 层｜归一 key 完全相同（56 组 / 115 个实体）**

`norm_key()`：NFKC → 去「第X届」→ 去括号内容 → 去「有限公司/股份有限公司/集团/公司/股份」后缀 → 去空白
同 key 的归一组，`method='norm_key'`。

**第 3 层｜子串包含 + 类型一致（严格过滤）**

```
候选条件：
  A ⊂ B
  且 len(A) >= 2
  且 len(B) - len(A) <= 4
  且 etype(A) == etype(B)
  且 A 不是泛称（度数占比检查）
```
- **不加类型约束就会炸**：实测 9009 对里绝大多数是 `中国 ⊂ 中国移动/中国新闻网` 这类
- 加上「类型一致」后预计剩几十~两百对
- `method='substring'`

**第 4 层｜同首字 + 编辑距离（677 对）**

```
首字相同、长度差 ≤2、difflib.ratio >= 0.80
```
实测样例（真假混在一起，正好交给 LLM 判）：
- 真：`马鞍山 / 马鞍山市`、`泰安 / 泰安市`、`中原 / 中原区`
- 假：`中国 / 中国角`、`东亚 / 东南亚`、`东方红三号 / 东方红二号`（姊妹产品，不同实体）

**硬约束（贯穿所有层）：`etype` 必须一致才允许合并。**
类型不同直接否决，不看相似度——这是防 `中国 vs 中国移动` 的唯一可靠手段。

### 6.4 LLM 判定层

- 只把「第 2/3/4 层产出且类型一致」的候选对交给 LLM
- 批大小 **40 对/次** → 预计 **25~40 次调用**
- 每对带：两个名字、各自类型、各自度数、**共享邻居（top 3）**

Prompt 模板：

```
判断下面每一对实体，是否指同一个真实世界对象。

【判定标准】
- 是：同一对象的不同写法（全称/简称/带不带「市」/中英对照/新旧名）
- 否：兄弟产品（iPhone 17 / iPhone 18）、包含关系（中国 / 中国移动）、
      不同实体恰好名字像（马欣 / 马佳欣）
- 证据优先级：共享邻居 > 别名语义 > 字面相似
- 若无法确定，answer 填 "unsure"

【待判】
{"a":"马鞍山","b":"马鞍山市","type_a":"地点行政","type_b":"地点行政",
 "deg_a":3,"deg_b":5,"shared_neighbors":["安徽","马钢"]}

【输出】
[{"a":"马鞍山","b":"马鞍山市","answer":"yes","canonical":"马鞍山市","confidence":0.96,"reason":"同一城市，带市不带市"}]
```

### 6.5 合并与规范名

1. 把「yes」的对 + 种子层 + norm_key 层放进并查集
2. **连通分量** → 一个分量 = 一个实体簇
3. 分量内选 `canonical`：度数最高 → 若有全称则取全称 → 否则取最长
4. 写 `entity_merge(alias, canonical, ...)`（canonical 自身不写行）
5. **可选**：把高置信结果合并进 `data/aliases.json`（`canonical: [aliases...]`），
   这样以后 `build` / `renormalize` 自动生效。**这一步等人工确认后再做**。

### 6.6 验收

- [ ] 合并后节点数在 7500~9000（超出范围说明阈值不对）
- [ ] 没有任何簇内出现 2 个不同类型
- [ ] 抽样 20 对人工核对，准确率 ≥ 90%
- [ ] `进博会` 与 `第九届中国国际进口博览会` 在同一个簇
- [ ] `中国` 与 `中国移动` **不在**同一个簇
- [ ] `苹果` 没有被误合并到别的实体

---

## 7. Step 4：实体消歧

> **优先级最低**。前 3 步做完、清单出来之后再决定要不要全做。
> 建议先只做 top-20 嫌疑实体，验证方法有效再扩。

### 7.1 目标

把「一个名字指多个东西」的实体拆成多个义项。

### 7.2 交付

`entity_sense` + `triple_sense`；`out/labels/sense_report.csv`。

### 7.3 嫌疑实体筛选（用类型替代向量聚类）

原方案用 embedding 聚类邻居；**本机没有模型**，改用：

```
嫌疑 = 短名（2~4 字）
     且 度数 >= 5
     且 邻居类型分布出现「不相容的两组」
```

「不相容的两组」判定：
- 把该实体的邻居按 `etype` 分组
- 若同时存在「公司企业/机构组织/产品型号」组 与 「泛称角色/地点设施/其他」组
  → 该实体可能一分为二

实测候选（已确认）：
- `苹果`：邻居里既有 `iPhone Duo / 芯片 / 高管`（公司），又有 `机器人手指 --抓握-->`（物体）
- 预计还有若干，需明天跑出来看

### 7.4 mention 级 sense 分配

对每个嫌疑实体的**每一条边**，用 LLM 判「这条边里的它指哪个义项」：

- 输入：`(subject, predicate, object, 两端 etype, 原文段落前 300 字)`
- 原文段落从 `articles.body` / `paragraphs` 里按 `article_id` 取（`ask` 曾懒建 `paragraphs`，
  若为空直接从 `articles.body` 截取包含该实体的窗口即可）
- 批大小 **20 条边/次**；top-20 实体总计约几百条边 → **20~40 次调用**

Prompt 模板：

```
下面是一个可能有多个含义的实体 "{entity}" 的若干条关系。请判断每条关系里
"{entity}" 指的是哪一类对象。

【已知义项】（先由 LLM 从证据归纳，最多 3 个）
1. 公司（Apple Inc.）
2. 水果（可被机器人抓握的实体）

【判定依据】谓词语义 + 另一端实体类型 + 原文片段

【待判】
{"predicate":"抓握","other":"机器人手指","other_type":"泛称角色",
 "snippet":"…演示中机器人手指精准抓握苹果…"}
{"predicate":"发布","other":"iPhone Duo","other_type":"产品型号",
 "snippet":"苹果发布 iPhone Duo…"}

【输出】
[{"i":0,"sense":"2","confidence":0.97}]
```

### 7.5 拆分

1. 按 sense 分组
2. 写 `entity_sense`：`苹果#1[公司]`、`苹果#2[水果]`
3. 写 `triple_sense`：每条边挂到对应 sense
4. **不改 `triples` 表**——下游要用时 join `triple_sense` 即可

### 7.6 验收

- [ ] `苹果` 被拆成 ≥2 个义项，`抓握` 那条边落在「水果」义项
- [ ] 每个义项的边数加起来 = 原实体的边数（不丢边）
- [ ] 抽样 10 条边核对 sense 正确率 ≥ 85%

---

## 8. 执行顺序与预算

| 顺序 | 步骤 | 批大小 | 预计调用 | 预计耗时 | 可中断 |
|---|---|---|---|---|---|
| 1 | Step 1 关系归一 | 30 谓词 | 60~75 | 40~60 min | ✅ 按批提交 |
| 2 | Step 2 实体类型 | 50 实体 | 89 | 60~90 min | ✅ |
| 3 | Step 3 实体合并 | 40 对 | 25~40 | 20~30 min | ✅ |
| 4 | Step 4 实体消歧 | 20 条边 | 20~40 | 20~30 min | ✅ |
| 5 | 生成清单 | — | 0 | 2 min | — |

**总计约 200~250 次 LLM 调用，2.5~3.5 小时**（按每批 10 分钟内可完成估算）。

**推荐的调用节奏**（照 HANDOFF 的教训）：
- 每批一批一提交，单次前台调用 ≤10 分钟
- 批间 `sleep(1.5)`
- **不要并发**（该端点并发会假死）
- 断了就重跑同一条命令，缓存会自动跳过已完成的批

```powershell
# 每步都是「跑一批、退出、再跑一批」
$env:PYTHONPATH="src;.kgvenv"
$py = ".\.venv\Scripts\python.exe"

& $py -m newskg.schema.cli --step rel   --limit 30   # 调 1 批（30 谓词）就退出
& $py -m newskg.schema.cli --step type  --limit 50
& $py -m newskg.schema.cli --step merge --limit 40
& $py -m newskg.schema.cli --step sense --limit 20
& $py -m newskg.schema.cli --step report            # 生成清单
```

> 说明：`--limit` 是「这一批处理多少条」，不是总量。脚本自己从 `schema_runs`
> 里找还没做的，所以反复跑同一条命令 = 继续推进，不会重复。

---

## 9. 三份交付物（用户真正要的东西）

### 9.1 `out/labels/SCHEMA.md`（总览，最重要）

```markdown
# 知识图谱 Schema

## 实体标签（12 类）
| 标签 | 定义 | 实体数 | 占比 | 示例 |
|---|---|---|---|---|
| 公司企业 | … | 1832 | 17.0% | 苹果、比亚迪、腾讯 |
...

## 关系标签（25 族）
| 族 | 定义 | 边数 | 占比 | 吸收的原始谓词（Top 10） |
|---|---|---|---|---|
| 任职 | 人在组织中担任职务 | 902 | 6.8% | 任职于、就职于、担任… |
...
```

### 9.2 `out/labels/rel_families.md`

每族一节，列出该族下**全部**原始谓词 + 条数 + 判定方式（dict/llm/rule）。

### 9.3 `out/labels/entity_types.md`

每类一节，列出实体数与代表实体；末尾附「数值度量类清单」（建议过滤）。

### 9.4 `out/labels/merge_report.csv` / `sense_report.csv`

供人工抽查：`alias, canonical, method, score, evidence`。

---

## 10. 明确不做的事

1. **不重跑抽取**（536 篇已抽完，重跑要 21 小时）
2. **不修改 `triples` 表**（原始数据保持可核对）
3. **不删除任何边**（数值类实体只标记，不删）
4. **不改 `articles` / `data/md` / `data/raw`**
5. **不自动改 `data/aliases.json`**（等人工确认合并结果后再做）
6. **不做 embedding**（本机无模型、无网络、端点无 /embeddings）

---

## 11. 已知坑（来自 HANDOFF，明天别再踩）

| 坑 | 应对 |
|---|---|
| 端点「假死」：请求发出不返回 | 串行 + 批间 sleep + 单次 timeout=120；连续 2 批失败就停 10 分钟 |
| 并发 ≥2 全部 0 产出 | 绝不并发 |
| JSON 输出被 ``` 包裹或带解释文字 | `ask_json` 里先剥围栏、再取第一个 `[`/`{` 到最后一个 `]`/`}` |
| 模型把内容写进 reasoning_content | 只取 `message.content`；为空则记 failed，不重试同一 prompt |
| LITELLM_LOCAL_MODEL_COST_MAP | **本方案不经过 dspy/litellm**，不需要设 |
| 断点续跑 | `llm_cache`（prompt 级）+ `schema_runs`（批次级）双保险 |
| 中文方括号/引号污染实体名 | 合并前对两边都跑 `entities.normalize_name()` |

---

## 12. 需要你确认的（回答你问的）

### 不需要你提供 LLM key

`.env` 里已经配好且**我实测通了**：

- 端点：`https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1`
- 模型：23 个可用，含 `qwen3.7-plus`（当前默认）、`qwen3.8-flash`、`deepseek-v4.1-flash-1017`
- key 长度 114，`/models` 返回 200
- **不支持 `/embeddings`**（404），所以向量方案本来也用不了

### 需要你确认的 4 件事

1. **预算**：全流程约 200~250 次调用、合计约 60~80 万 token（估算）。
   用 `qwen3.7-plus` 大概几块钱，用 `qwen3.8-flash` 更便宜。
   **要不要限制在 flash 模型上跑？** 我建议 Step 2（实体类型，量最大）用 flash，Step 1/3 用 plus。

2. **是否接受「度数 =1 的 6344 个实体不标类型」**。
   它们只出现一次，标注价值低；标了会让成本翻倍。我建议不标，报告里注明覆盖率。

3. **（可选）BGE-M3 模型文件**：如果你手头有本地模型目录（或能拷进来），
   实体合并的准确率会明显更高。没有也能跑——用字符串规则 + LLM 判定，只是召回略低。

4. **明天是否需要我先把脚本写出来**：现在是「文档先行」。
   如果你希望今晚就把 `src/newskg/schema/*.py` 骨架写好，明天直接跑，说一声就行。

### 我不需要的东西

- 不需要你手工标数据（可以用 LLM 自检 + 抽样人工核对）
- 不需要图数据库（SQLite 够）
- 不需要额外磁盘（新增表 < 10 MB）

---

## 13. 明天的第一句话

> 「按 PLAN_FOUR_STEPS.md 跑，先做冷启动检查（§0），然后 Step 1。」