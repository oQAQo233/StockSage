# StockSage · 新闻与股票多维度数据融合

本科数据融合课程项目原型：以“新闻＋行情→时间对齐→知识图谱→分析→预测→证据摘要”为主线。

- **[详细项目计划书](docs/项目计划书.md)**：需要收集的信息、各模块任务、融合方法、实验与验收要求；不包含时间安排。
- **[统一数据契约 v1.0](docs/统一数据契约.md)**：真实数据的市场/时间范围、字段含义、单位、来源、时间规则和质量要求；真实入口以此为准。
- **[数据来源调研](docs/数据来源调研.md)**：能拿到什么、实际来源、历史新闻覆盖限制和选型依据。
- **[真实数据验收](docs/真实数据验收.md)**：本次 2 只股票、156 条日线、5 条报道通过完整管线的复现与结果。
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
5. 查看下一交易日收益率估计与线性特征贡献；查看收集、分析、预测、报告四个角色的处理结果。

## 生成结果

| 路径 | 内容 |
|---|---|
| `output/demo_data/` | 默认合成 CSV 与元数据，可作为导入格式参考 |
| `output/report.json` | 每只股票的特征、证据图谱、回测、预测和摘要 |
| `output/stocksage.sqlite3` | 规范化行情、新闻、新闻与股票关系、最新分析快照 |
| `output/quality_report.json` | 契约校验结果、覆盖、时间口径及数据限制 |
| `output/aligned_news.csv` | 每条新闻的可用时间、对齐日期和待对齐状态 |
| `output/features.csv` | 证券与交易日的六个特征、标签与证据 ID |

重复运行会重建默认合成数据并更新上述报告和数据库快照。`output/` 是运行产物，未加入版本控制；需要保留实验时请指定独立的 `--output-dir`。

## 当前实现范围

当前“智能体”是职责清晰的**确定性角色服务**，由编排器顺序调用，尚未实现 LLM 自主规划、连续实时抓取、自动实体消歧、自动事件抽取或自动交易。新增采集命令仅获取固定小批数据并支持离线重放。新闻关联股票和事件类型来自人工核验的输入字段，情绪来自简化中文词典。知识图谱为内存属性图并随报告导出，尚未接入 Neo4j。

合成价格生成过程有意包含微弱新闻信号，仅验证链路。预测效果、新闻与价格的时间相关性、线性特征贡献均不能作为真实市场可预测性或因果关系的证明。默认报告中的金额单位为模拟价格；v1 真实入口统一人民币、股数及单一复权口径。
