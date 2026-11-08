# StockSage · 新闻与股票多维度数据融合

本科数据融合课程项目原型：以“新闻＋行情→时间对齐→知识图谱→分析→预测→证据摘要”为主线。

- **[详细项目计划书](docs/项目计划书.md)**：需要收集的信息、各模块任务、融合方法、实验与验收要求；不包含时间安排。
- **[统一数据契约 v1.0](docs/统一数据契约.md)**：真实数据的市场/时间范围、字段含义、单位、来源、时间规则和质量要求；真实入口以此为准。
- **[数据来源调研](docs/数据来源调研.md)**：能拿到什么、实际来源、历史新闻覆盖限制和选型依据。
- **[真实数据验收](docs/真实数据验收.md)**：本次 2 只股票、156 条日线、5 条报道通过完整管线的复现与结果。
- **[新闻知识模块整合与实测](docs/新闻知识模块整合与实测.md)**：newskg 接入位置、真实正文抓取、快照重放与测试结果。
- **[新闻知识模块独立化验收](docs/新闻知识模块独立化验收.md)**：代码迁移位置、不含原子项目的独立运行验证与复现入口。
- **[全新数据采集与验收（2026-11-08）](docs/新数据采集与验收_20261108.md)**：三只新能源股、2026年7—11月、261条新行情及15篇新检索报道；与旧数据零重合。
- **[构建任务与模块分工](docs/构建任务与模块分工.md)**：建议起点、可并行方向、目标文件结构与现有代码迁移方式。
- **[架构与接口](docs/架构与接口.md)**：目录职责、数据契约、HTTP API、智能体扩展方式。
- **[实验说明](docs/实验说明.md)**：预测时点、时间验证、模型边界与复现方法。

## 快速运行

需要 **Python 3.10 或以上**。当前 demo 只用 Python 标准库，**不需要 pip install、不需要 API Key、不访问网络**。

```powershell
python run.py
```

若 Windows 提供的是 Python Launcher，可使用 `py -3 run.py`。也可以运行带解释器检测的启动脚本：

```powershell
powershell -ExecutionPolicy Bypass -File .\start_demo.ps1
```

在浏览器打开 **http://127.0.0.1:8765**，按 `Ctrl+C` 停止。该地址仅在本机访问。端口被占用时使用 `python run.py --port 8766`。

```powershell
# 不启动网页，仅运行分析和导出
python run.py --no-server

# 运行自动化检查
python -m unittest discover -s tests -v

# 导入符合约定的自有 CSV；可参考自动生成的 output/demo_data
python run.py --data-dir ./my_data --output-dir ./output/imported
```

默认运行自动生成三只**虚构股票**、每只 110 个工作日的合成行情与新闻。模拟日历未处理法定节假日。每次生成使用固定随机种子，不覆盖用户指定的数据目录。

## 使用已核验的小批真实数据

**最新独立数据集**是[新能源新样本](data/fresh_energy_20261108/)，由本次网络请求产生，未使用下面的旧白酒样本。页面可直接运行：

```powershell
.venv/Scripts/python.exe run.py --data-dir data/fresh_energy_20261108 --output-dir output/fresh_energy_20261108 --port 8767
```

重新采集使用 [collect_fresh.py](stocksage/collect_fresh.py) 和[采集配置](configs/fresh_energy_20261108.json)，完整命令见[新数据验收记录](docs/新数据采集与验收_20261108.md)。旧样本继续保留作回归测试，以下命令指向旧样本。

已提供固定的 [data/real_sample/](data/real_sample/) 快照，**离线运行，无需账号或安装第三方包**：

```powershell
python run.py --data-dir data/real_sample --output-dir output/real_sample --no-server

# 打开现有仪表盘，使用同一批真实输入
python run.py --data-dir data/real_sample --output-dir output/real_sample

# 可选：仅凭原始快照重建 CSV（目标目录须为空）
python -m stocksage.collect --replay data/real_sample/raw --output-dir output/replayed_real

# 可选：联网重新获取固定小样本（会写新的原始快照目录）
python -m stocksage.collect --output-dir output/new_real_snapshot
```

只有 Python Launcher 时把 `python` 换成 `py -3`；本机启动脚本也支持 `-DataDir data/real_sample`。

本阶段范围是沪深 A 股、人民币、日频、中文公司资讯。建议正式课程实验先固定白酒行业 5 只股票、2025 年全年；目前已获取贵州茅台和五粮液 2025 年 1—4 月各 78 个实际交易日，以及 5 篇人工核验的标题记录。新闻来源为新华财经和新浪财经页面；行情为腾讯财经前复权固定快照。样本不是完整历史新闻库，可用时间为明确标记的历史回填假设，只用于管线验收。

真实数据必须提供 `metadata.json` 并声明 `contract_version=1.0`、`synthetic=false`，以及四张 CSV、来源快照和 SHA256。详情见[统一数据契约](docs/统一数据契约.md)。旧合成格式仍兼容。

## 可以演示什么

1. 切换股票，查看收盘价、区间变化、新闻落点和证据摘要。
2. 查看公司、证券、行业、新闻、事件、来源构成的知识图谱，点击新闻节点追溯证据。
3. 根据新闻的**实际可用时间**对齐交易日：15:00 之后及非交易日的信息进入下一个实际有行情的收盘时点。
4. 比较零收益、纯行情岭回归、行情＋新闻岭回归在同一时间窗口的 MAE、RMSE、方向准确率。
5. 查看下一交易日收益率估计与线性特征贡献；查看收集、新闻知识、分析、预测、报告五个角色的处理结果。
6. 使用内置新闻知识引擎抽取带证据的候选关系，可选切换 LLM/KGGen 或抓取新闻正文。图谱与关系表直接读取主流程结果。

## 新闻知识模块

新闻采集、正文转换、去重和 rule/LLM/KGGen 实现已迁入 [news_knowledge/](stocksage/news_knowledge/)，图谱工具已迁入 [visualization.py](stocksage/visualization.py)。**运行主项目不再需要原 `knowlegde_merge` 目录**；主流程使用正常包内导入，默认 CSV + rule 仍无需额外依赖。原目录保留作为历史参考，不参与运行。迁移与验证详见[独立化验收](docs/新闻知识模块独立化验收.md)。

```powershell
# 默认 CSV + 规则抽取
python run.py --data-dir data/real_sample --output-dir output/integrated --no-server

# 冻结语料离线重放；无法确定关联的新闻不会强行分配给证券
python run.py --data-dir data/real_sample --news-source corpus --news-limit 126 --output-dir output/corpus --no-server

# 安装联网正文处理依赖（本机已在 .venv 安装）
.venv/Scripts/python.exe -m pip install -r requirements-news.txt

# 抓取 CSV 原链接的正文，默认按实际接收时间使用
.venv/Scripts/python.exe run.py --data-dir data/real_sample --news-source urls --news-limit 5 --output-dir output/live --no-server

# 离线重放刚采集的正文，保留实际接收时间
python run.py --data-dir data/real_sample --news-source snapshot --news-snapshot output/live/news_inputs/manifest.json --output-dir output/replay --no-server

# 回顾性实验：显式采用“发布上界加5分钟”的历史可用假设
python run.py --data-dir data/real_sample --news-source snapshot --news-snapshot output/live/news_inputs/manifest.json --news-time-mode historical_assumption --output-dir output/retrospective
```

`--news-source` 还接受内置来源名称，如 `eastmoney-rss`、`eastmoney`、`ths`、`sina-fin`、`rss`、`gdelt`、`guardian`、`cn-finance`。`--news-days`、`--news-limit`、`--news-query` 控制发现范围；`fixtures` 明确读取内置合成样例。实时源取决于上游，未逐个重新验证上游服务可用性。

`--knowledge-backend rule|llm|kggen|none` 切换后端。LLM 需新闻依赖和 `NEWSKG_LLM_BASE_URL`、`NEWSKG_LLM_MODEL`、`NEWSKG_LLM_API_KEY`，KGGen 另需 `kg-gen`；不自动切换付费后端。本次未配置云端 LLM Key，云端 LLM/KGGen 未实测。

新抓正文默认只在实际接收之后使用，不进入更早的预测；历史回填只用于回顾性验收。规则可能抽不出关系，会在页面和报告中明确显示。更多接线、产物与边界见[整合与实测](docs/新闻知识模块整合与实测.md)。

## 当前数据的交互图谱

内置 [PyVis 工具](stocksage/visualization.py) 可直接读取主项目报告或数据库。安装 `requirements-news.txt` 后，在运行中的页面侧栏点击“可拖拽图谱 · 新窗口”，也可访问 **http://127.0.0.1:8767/graph/interactive**；增加 `?symbol=300750.SZ` 可筛选宁德时代。

生成可离线打开、无需外部 CDN 的 HTML：

```powershell
.venv/Scripts/python.exe -m stocksage.visualization --report output/fresh_energy_20261108/report.json --out output/fresh_energy_20261108/graph_interactive.html

# 从主项目 SQLite 快照读取单只证券
.venv/Scripts/python.exe -m stocksage.visualization --db output/fresh_energy_20261108/stocksage.sqlite3 --symbol 300750.SZ --out output/fresh_energy_20261108/graph_300750.html
```

当前新能源新样本合并后为 **46 个节点、52 条边**，可拖拽、缩放并悬停查看来源信息。**抽取候选关系为 0 条**：这些边表示证券主数据和新闻提及、报道、来源关联，未分类事件沿用占位标签，不能视为抽取出的业务事实。工具使用主流程已经完成时间过滤的图谱。

## 生成结果

| 路径 | 内容 |
|---|---|
| `output/demo_data/` | 默认合成 CSV 与元数据，可作为导入格式参考 |
| `output/report.json` | 每只股票的特征、证据图谱、回测、预测和摘要 |
| `output/stocksage.sqlite3` | 规范化行情、新闻、新闻与股票关系、最新分析快照 |
| `output/quality_report.json` | 契约校验结果、覆盖、时间口径及数据限制 |
| `output/aligned_news.csv` | 每条新闻的可用时间、对齐日期和待对齐状态 |
| `output/features.csv` | 证券与交易日的六个特征、标签与证据 ID |
| `output/knowledge_report.json` | 采集/抽取、重复、未关联、失败与时点可见统计 |
| `output/normalized_news.json` | 含关联、候选关系及抽取状态的运行时新闻 |
| `output/knowledge_graph.json` | 按股票收盘时点过滤的图谱 |
| `output/news_inputs/` | 可选 HTML、Markdown、内容哈希、正文与重放清单 |

重复运行会重建默认合成数据并更新上述报告和数据库快照。`output/` 是运行产物，未加入版本控制；需要保留实验时请指定独立的 `--output-dir`。

## 当前实现范围

当前“智能体”是职责清晰的**确定性角色服务**。新闻知识角色已整合 newskg 的来源、正文转换、去重和规则/可选 LLM/KGGen 抽取。CSV 保留已有人工证券与事件标签；外部新闻只做精确代码/长别名匹配，不确定时保留未关联，事件未分类时明确标记。尚未实现 LLM 自主规划、连续调度、可靠实体消歧、完整事件抽取或自动交易。情绪仍为简化词典。知识图谱为内存属性图并随报告导出，尚未接入 Neo4j。

合成价格生成过程有意包含微弱新闻信号，仅验证链路。预测效果、新闻与价格的时间相关性、线性特征贡献均不能作为真实市场可预测性或因果关系的证明。默认报告中的金额单位为模拟价格；v1 真实入口统一人民币、股数及单一复权口径。
