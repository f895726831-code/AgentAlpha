# 第三方组件与许可声明

本仓库（AgentAlpha）以 MIT 许可证发布。仓库中包含或引用了以下第三方组件/内容，
其版权归各自作者所有，使用与再分发请遵守对应许可证。

## 直接依赖（运行时/开发）

| 组件 | 用途 | 许可证 |
|---|---|---|
| [Qlib](https://github.com/microsoft/qlib) | 量化数据与回测引擎 | MIT |
| [RD-Agent](https://github.com/microsoft/RD-Agent) | 自动化因子挖掘（可选，WSL2 factor-lab） | MIT |
| [LightGBM](https://github.com/microsoft/LightGBM) | GBDT 模型 | MIT |
| [XGBoost](https://github.com/dmlc/xgboost) | GBDT 模型 | Apache-2.0 |
| [CatBoost](https://github.com/catboost/catboost) | GBDT 模型 | Apache-2.0 |
| [akshare](https://github.com/akfamily/akshare) | 行情/财务数据 | MIT |
| [baostock](http://baostock.com/) | 行情数据（备用） | 免费使用（见官网条款） |
| [PyYAML](https://pyyaml.org/) / [pandas](https://pandas.pydata.org/) / [NumPy](https://numpy.org/) / [SciPy](https://scipy.org/) | 基础库 | BSD/MIT 等 |

## Agent 技能模板

- 本仓库**只包含自研技能**（`my-bot/.agents/skills/`：`bull-bear-debate-gate`、`market-wide-scanner`、`qlib-alpha-signal`），随仓库以 MIT 许可发布。
- 第三方技能（Wind Information Co., Ltd. 的 `wind-skills`、`simonlin1212/a-stock-data` 等）**不随仓库分发**，请按 [SKILLS.md](SKILLS.md) 的安装命令自行安装到 `~/.agents/skills/`；其版权与许可归原作者，使用前请阅读对应仓库的许可证。

## 数据

- 本仓库**不包含**任何行情、财务、宏观或交易数据。
- 使用 Wind / Tushare / 同花顺等商业数据源时，请遵守对应数据服务条款；数据版权归数据提供方所有。
- 示例中的股票代码、日期仅用于说明格式，不构成投资建议。

## 论文与算法参考

- *R&D-Agent-Quant: A Multi-Agent Framework for Data-Centric Factors and Model Joint Optimization*（arXiv 2505.15155，NeurIPS 2025）
- *Qlib: An AI-oriented Quantitative Investment Platform*（arXiv 2009.11189）
- Alpha158 / Alpha360 特征集（Qlib 内置）
