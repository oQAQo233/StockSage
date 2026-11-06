# 新闻知识图谱抽取：规则、LLM 与 KGGen 的三方对照

> 课程作业 · 实验报告
> 代码与数据：本仓库（`src/newskg/`、`data/corpus/`、`data/gold/`）
> 复现：`run.cmd --db out\corpus.sqlite3 compare --backends rule,llm,kggen`

## 1 任务与假设

给定一篇新闻正文，抽出一组 `(主语, 关系, 宾语)` 三元组，并与人工标注比对。

本实验要回答的不是「哪个模型强」，而是三个更基础的问题：

1. **模板方法在真实新闻上能走多远？** 如果只有 0.8%，说明问题不在工程实现，而在方法本身。
2. **LLM 的差距体现在召回还是精确？** 两者对应完全不同的改进方向。
3. **用什么指标衡量才不算自欺？** 当 gold 是人挑出来的「重点事实」而非全文穷尽时，精确率还有意义吗？

## 2 系统

### 2.1 管线

```
发现(RSS/GDELT) → 抓取 → trafilatura 抽正文 → markitdown 转 Markdown
   → 规范化(YAML front matter) → SimHash + Jaccard 去重 → 抽取 → SQLite → 导出(JSON/Cypher/GraphML)
```

正文抽取放在 markitdown 之前是刻意的：markitdown 对整页 HTML 做转换，会把导航栏、推荐位、
评论区一起搬进 Markdown，噪声直接进模型的上下文。trafilatura 先切出正文，再交给 markitdown 保结构。

去重用了两级：SimHash 汉明距离 ≤ 6 只做候选筛选，再用 3 词 shingle 的 Jaccard ≥ 0.55 二次确认。
只用 SimHash 会在同一事件的相似报道上误判（实测曾把两篇不同的 BBC 选举稿判为重复，汉明距离 5）。

### 2.2 三个抽取器

| 后端 | 原理 | 依赖 | 成本 |
| --- | --- | --- | --- |
| `rule` | 正则 + 动词模板表（acquired / appointed / located_in …） | 无 | 0 |
| `llm` | 单次调用，要求返回 JSON 三元组数组，最多 25 条 | 任意 OpenAI 兼容端点 | 按 token |
| `kggen` | KGGen（NeurIPS'25），带实体聚类 | `kg-gen` + litellm + dspy | 按 token |

三个后端的产出落在**同一张表**（`triples.extractor` 区分），主键含抽取器名——
否则后跑的后端会覆盖先跑的结果（这个 bug 实际发生过，见 §5.5）。

谓词形态**按文章语言分流**：英文文章用小写 `snake_case`（`acquired`、`located_in`），
中文文章用 2–4 个汉字的动词（`收购`、`中标`、`签订`）。判语言看正文汉字占比，而不是只信 `lang` 字段
（中文站标成 `en` 的情况很常见）。同一语言内的同义写法在评分前归一
（`收购了` / `拟收购` / `收购股权` → `收购`），但语义不同的不合
（`收购` ≠ `并购`、`增资` ≠ `定增`、`控股` ≠ `参股`），否则 gold 里本应区分的关系统计成一个。
**本报告的全部实验在英文语料上完成，故表中谓词均为英文形态。**

## 3 实验设置

### 3.1 语料

32 篇真实新闻，来自 BBC World（18 篇）与 Al Jazeera（14 篇）的 RSS 源，抓取后冻结为
`data/md/*.md` + `data/corpus/manifest.json`，可在断网环境原样重建。其中 20 篇正文 ≥ 250 词的
进入标注集；短视频页、直播页、图集页因正文不足 250 词排除。

### 3.2 标注

246 条三元组，20 篇，每篇 9–16 条。规则：

- 只收**正文明确陈述**的关系，不做推理补全；
- 不收转述（`X told BBC`）与纯评价（`X is controversial`）；
- 实体用文中出现的规范名，代词消解到最近的具体实体。

标注是**模型辅助生成 + 人工抽查**，不是纯人工标注——这一点在 §6 里作为主要效度威胁列出。

### 3.3 指标

**双口径**：

- *strict*：`(主语, 关系, 宾语)` 三元组完全一致才算命中；
- *relaxed*：只比 `(主语, 宾语)`，忽略关系词。

两者之差就是「谓词自由度」的代价。只报 strict 会把「实体配对全对、关系词用了同义词」的系统
判得和「什么都没抽出来」一样差。

**谓词归一**在评分前实施：`based_in` / `headquartered_in` / `based in` 统一为 `located_in`，
gold 与抽取结果两侧都做。不归一的话，同义不同词会互相扣分，把真实差距淹掉。

聚合方式同时给 micro（把所有文章的三元组混在一起算）与 macro（先算每篇再平均）。

## 4 结果

32 篇语料，20 篇有 gold，246 条 gold。

| 后端 | 底层模型 | 产出 | Strict P | Strict R | Strict F1 | Relaxed F1 | 耗时 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `rule` | 无 | 3 条 | 33.33% | 0.41% | 0.80% | 0.83% | 0.3 s |
| `llm` | deepseek-v4.1-flash | 475 条 | 18.53% | 35.77% | 24.41% | 35.90% | 883 s |
| `kggen` | claude-haiku-5-5 | 772 条 | 6.09% | 19.11% | 9.23% | 27.98% | 194 s |
| `llm`（同模型对照组） | claude-haiku-5-5 | 405 条 | 19.51% | 32.11% | 24.27% | 36.11% | 126 s |

对照组不是凑数：`kggen` 那行被迫换了模型（见 §5.6），若不补这一行，
「KGGen 比手写 prompt 差」就会被「模型不同」这个解释吞掉。

对照：同一套 `rule` 后端在配套的 8 篇合成样例（20 条 gold）上 strict F1 = **97.44%**
（P=100%、R=95%）。同一个抽取器，从 97% 掉到 0.8%。

## 5 讨论

### 5.1 模板法的天花板

`rule` 在 246 条 gold 上只命中 1 条。原因很直接：真实新闻用叙事体，
「The company has been accused of…」「Vance walked a tightrope…」这类句子不构成
`(实体)(动词)(实体)` 的连续结构，正则无从下手。

这不是实现缺陷。样例集上的 97.44% 证明模板对**它被设计来处理的句式**是有效的；
0.80% 证明这种句式在真实新闻里是少数。**这两个数字必须放在一起看才有意义**——
只报 97.44% 是误导，只报 0.80% 则低估了基线本身的价值。

### 5.2 精确率被系统性低估

`llm` 的 strict 精确率 18.53%，看起来很差。抽查其中一篇（Palantir 报道，`4d6c5216`）：

| 项 | 数 |
| --- | --- |
| gold | 16 |
| llm 产出 | 25 |
| 命中 gold | 5 |
| 未命中但人工判定**正确** | 19 / 20 |

未命中的三元组例如：

- `elke schwarz | works_for | queen mary university of london`（原文："a lecturer … at Queen Mary University of London"）
- `palantir | implicated_in | genocide`（原文："technologies are implicated in human rights abuses, including in Israel's genocide in Gaza"）
- `ukraine | collects | battlefield data`（原文："the first nation … to systematically collect battlefield data"）

它们都对，只是没被收进 gold——因为 gold 标的是「最重要的 16 条」，不是「全文所有关系」。

**结论：用非穷尽的 gold 评价开放抽取，精确率必然被低估，而且产出越多的系统被罚得越狠。**
按这篇的人工判定，真实精确率约 (5+19)/25 = 96%，而指标只给 20%。
因此在这个设定下，**召回才是可信指标，精确率只是一个下界**。

### 5.3 谓词自由度的代价

`llm` 的 relaxed 比 strict 高 11.49 个百分点（35.90% vs 24.41%）。
这说明它把「谁和谁有关系」找对的概率，明显高于「用 gold 认可的那个词描述这种关系」的概率。
新闻里同一关系有多种自然表达（`acquired` / `bought` / `took over`），
在不给封闭谓词表的前提下，strict 指标对生成式抽取天然不利。

### 5.4 产出上限，而不是理解上限

`llm` 每篇的产出稳定在 25 条上下（25, 25, 25, 22, 25…），正好卡在 prompt 里
「最多 25 条」的上限。而 gold 平均每篇 12 条。

这意味着：**召回率有一部分被输出预算卡住，而不是被理解能力卡住。** 若要公平比较，
应当让各后端在相同预算下运行，或让抽取器给关系排序后取 top-k 与 gold 比对。

### 5.5 一个会让对照失真的实现缺陷

三元组主键最初是 `sha1(主语|关系|宾语|文章ID)`，**不含抽取器名**。
`llm` 跑完后，它抽到的、与 `rule` 重合的那条把 `rule` 的记录覆盖成了自己的，
`rule` 的 F1 从 0.80% 直接变成 0——看起来像「规则后端一条都没抽对」。

在多后端对照实验里，结果表的主键必须包含「产生者」这一维，否则各后端的产出会互相吞噬。

### 5.6 换模型几乎没用，换方法才有用

这是本次实验最反直觉的发现。看两行：

| 对比 | Strict F1 | 变化 |
| --- | --- | --- |
| `llm` + deepseek-v4.1-flash | 24.41% | — |
| `llm` + claude-haiku-5-5（同模型对照组） | 24.27% | **−0.14 个百分点** |
| `kggen` + claude-haiku-5-5 | 9.23% | **−15.04 个百分点** |

把底层模型从 deepseek 换成 claude，F1 几乎不动；把抽取方法从「手写 prompt」
换成 KGGen、模型保持不变，F1 掉到 37%。

原因看谓词长度就清楚了：

| 后端 | 谓词平均长度 | 例子 |
| --- | --- | --- |
| `llm` | 9.8 字符 | `facilitated`、`based_in`、`located_in` |
| `kggen` | 21.6 字符 | `facilitated_deportations_by_administration_of` |

KGGen 追求「关系描述尽量完整」，产出的是句子级谓词；而 gold 用的是短的规范化谓词。
在 strict 口径下，这类长谓词几乎不可能命中。同时在 relaxed 口径下 KGGen 也只拿到 27.98%，
低于手写 prompt 的 36.11%——因为它每篇产出 38 条、是 gold 的三倍，把精确率压到了 6.09%。

**结论：在「对齐人工标注口径」这件事上，prompt 的设计（谓词空间、产出数量）比模型选型重要一个数量级。**
KGGen 的卖点是实体聚类（实验里确实出现了 `Britain | is_same_as | United Kingdom` 这类合并），
但那解决的是「图稀疏、别名多」的问题，不是「和人工标注对齐」的问题。

### 5.7 KGGen 落地时踩的三个坑

1. **推理模型会把 token 全烧在 reasoning 上。** 用 deepseek-v4.1-flash 跑 KGGen，
   10 篇里 6 篇返回 `text: None`（reasoning 写了几千字，正文一个字没有），dspy 解析直接失败。
   把 `max_tokens` 从默认值提到 8192 也没用——它会把新增的额度继续用于推理。
   最终换成不产生 reasoning 的 claude-haiku-5-5，单篇从 126 秒 0 条变成 12.7 秒 36 条。
2. **`reasoning_effort` 参数只对 gpt-5 系列有效**，KGGen 内部会直接 `raise ValueError`，
   所以不能靠调这个参数绕过上一条。
3. **构造参数名在版本间变过**（0.4.0 是 `api_base`，早期是 `base_url`），
   Go 网关还要求一个 litellm 不会发的 `x-opencode-session` 头。
   解决办法是在前面加一个本地转发器补头，而不是去改 litellm。

## 6 效度威胁

1. **gold 是模型辅助标注，不是人工独立标注。** 这是最大的威胁：标注过程与 LLM 抽取器共享同一个
   模型家族，等于让被测系统参与制定评分标准。理想做法是人工独立标注，或双人标注后算 Cohen's κ。
2. **gold 非穷尽**（§5.2），使精确率只能作为下界。
3. **主表里 `llm` 与 `kggen` 用了不同模型**（deepseek 与 haiku）。这是工具约束导致的（§5.7 第 1 条），
   不是刻意安排；用 §4 的同模型对照组可以部分弥补，但最干净的做法是全部统一到同一模型。
4. **单一语料、单一语言**：32 篇英文新闻，结论不能外推到中文或多语言场景。
5. **样本量小**：20 篇标注文章，逐篇 F1 从 0 到 0.44 波动极大，macro 平均的方差不可忽视。
6. **`rule` 基线未针对本语料调优**：它是先于语料写成的，属于预注册基线。
   照着 gold 调正则能显著提高数字，但那等于用测试集训练，会使整张对照表失效。

## 7 复现

```bat
:: 离线重建语料（不联网）
.\run.cmd import-corpus

:: 跑对照（已有 key 时）
.\run.cmd --db out\corpus.sqlite3 compare --backends rule,llm,kggen
```

产物：`out/comparison.md`、`out/comparison.csv`、`out/report.md`。

无 key 时 `llm` 一行会标为「不可用」而不中断；`kggen` 未安装时同理。
`runs` 台账记录每篇文章被哪个后端处理过（含 0 产出的），重复运行不会重复调用付费 API。

## 附录：文件清单

| 路径 | 内容 |
| --- | --- |
| `src/newskg/pipeline.py` | 发现→抓取→抽取→入库 |
| `src/newskg/convert.py` | trafilatura + markitdown |
| `src/newskg/evaluate.py` | 双口径 P/R/F1，逐篇 + micro/macro |
| `src/newskg/compare.py` | 多后端对照，缺 key 不中断 |
| `src/newskg/kg/{rule,llm,kggen}.py` | 三个抽取器 |
| `data/gold/corpus.csv` | 246 条 gold（20 篇） |
| `data/gold/parts/*.json` | 逐篇标注原始文件（溯源用） |
| `data/corpus/manifest.json` | 语料清单，离线重建依据 |
| `out/comparison.md` | 对照结果 |