# AgentAlpha — AI-Driven Quantitative Trading Decision System

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](signal-factory/pyproject.toml)
[![Node](https://img.shields.io/badge/Node-20%2B-brightgreen.svg)](my-bot/package.json)

AgentAlpha 是一个 **AI Agent 驱动的量化交易决策框架**，面向 A 股市场：用多层信号工厂产出 Alpha / 板块 / 宏观信号，再用一套带风控 Gate 的 Agent 管线完成"初筛 → 选股 → 回测关 → 信号确认 → 多空论战 → 执行 → 风控退出"的闭环。

> ⚠️ **免责声明**：本项目仅供研究与学习参考，不构成任何投资建议。使用本项目代码进行实盘交易的风险由使用者自行承担。

## 项目起源

项目起步于 **本地部署微软 RD-Agent 与 Qlib**，进行自动化因子挖掘与 Alpha 信号生成的探索。在此基础上逐步扩展为：

- **多数据源**：akshare / baostock 为主，Wind MCP 可选（A 股行情、财务、宏观）
- **前沿算法**：参考 RD-Agent（NeurIPS 2025）、Qlib、Alpha158/360 等公开工作
- **AI 辅助开发**：主体代码由 Claude Code 辅助编写，Agent 管线本身也是 Claude Code SDK 构建

## 核心架构

```
signal-factory/  (Quant Signal Factory)
  ├── Layer 0: 宏观大类资产配置（11 类资产，HMM 制度识别 + MVO/BL 组合）
  ├── Layer 1: 板块轮动（31 个申万行业 GBDT/XGBoost）
  └── Layer 2: 个股 ML Alpha（GBDT / XGBoost / CatBoost，Alpha158/360 特征）
        └── WSL2 factor-lab：GFlowNet / RD-Agent 因子发现（可选）

my-bot/  (AI Agent 决策管线)
  Layer 0/1 上下文 → Stage 0 全市场初筛 → Stage 0.3 主题扩展
    → Stage 1 环境判断 + 选股（7 策略）
    → Gate 1 回测关（5 维评分 + 反过拟合）
    → Stage 2 信号确认（跳空/放量归因）
    → Gate 2 多空论战关（六维加权）
    → Stage 3 交易执行（仓位/计划）
    → Gate 3 风控关（集中度/熔断）
    → Stage 4 风控退出（止损/止盈）
```

## 核心特性

- **自研 Agent 技能**：多空论战关（Gate 2）、全市场初筛、Qlib Alpha 信号；第三方技能（Wind 等）按需安装，见 [docs/SKILLS.md](docs/SKILLS.md)
- **三模式自适应**：`normal` / `earnings_season` / `risk_off` 随市场状态切换
- **Quant Signal Factory**：独立 ML Alpha 信号工厂，Alpha158/360 特征、多模型集成，与 Agent 通过 JSON 契约松耦合
- **信号治理**：`@contract` 元数据（schema 版本、as_of、universe、staleness） + `manifest.json` + 同步端新鲜度门禁
- **研究纪律**：PIT 泄漏检测（正则 + AST）、OOS 访问门禁、假设登记、验证日志、Gate 1 五维评分
- **执行现实性**：ST/停牌过滤、涨跌停门禁、成交额占比约束（`execution_gate`）
- **可选因子发现**：WSL2 + GFlowNet / RD-Agent，通过 factor-lab HTTP 服务与 Windows 管线解耦

## 目录结构

```
AgentAlpha/
├── signal-factory/          # 研究与信号工厂（Python）
│   ├── src/
│   │   ├── data/            # Wind/akshare 桥、PIT、泄漏检测、OOS 门禁
│   │   ├── factors/         # 模型配置、Alpha 信号、验证/假设/日志
│   │   ├── backtest/        # Qlib 适配、信号生成、Gate1 评分、执行门禁
│   │   ├── export/          # JSON 契约（供 Agent 消费）
│   │   ├── sector_rotation/ # 板块轮动数据/特征/模型/回测/契约
│   │   └── macro_allocation/# 宏观大类资产（HMM/MVO/BL）
│   ├── scripts/             # 数据更新、每日管线、回测、宏观看板
│   ├── config/              # YAML 配置（模型/数据/回测）
│   └── tests/               # 单元测试
├── my-bot/                  # AI Agent 决策管线（TypeScript）
│   ├── src/                 # agents / strategies / data
│   ├── scripts/             # 数据同步、信号追踪、QLib 桥、WSL2 因子实验
│   ├── docs/                # 管线架构、运维、排障、报告模板
│   └── tests/               # 单元测试
├── my-bot/.agents/skills/   # 自研 Agent 技能（第三方技能按需安装）
├── .github/workflows/       # CI
├── env.example              # 环境变量模板
└── docs/                    # 第三方声明、技能安装说明等
```

> 仓库**不包含**任何个人持仓、交易记录、日报/周报或运行数据；`my-bot/data/` 只保留默认配置，`my-bot/docs/templates/` 提供空白报告模板。

## 快速开始

### 环境要求

- Python 3.10+
- Node.js 20+
- [Qlib](https://github.com/microsoft/qlib)（ML Alpha 信号与回测）
- Wind 终端（可选）
- WSL2 + NVIDIA GPU（可选，GFlowNet 因子发现）

### 安装

```bash
git clone https://github.com/f895726831-code/AgentAlpha.git
cd AgentAlpha

# Python 信号工厂（核心依赖）
pip install -e "signal-factory[dev]"

# 可选：数据源与 ML 扩展
pip install -e "signal-factory[data,ml]"

# Agent 管线
cd my-bot && npm install

# 可选：安装第三方技能（Wind / a-stock-data 等，详见 docs/SKILLS.md）
npx skills add Wind-Information-Co-Ltd/wind-skills --skill wind-mcp-skill -g -y
```

### 配置

```bash
cp env.example .env          # 填入 DeepSeek / Wind / Tushare 等 key（全部可选，按需）
```

- 数据路径：`signal-factory/config/data.yaml`、`my-bot/scripts/qlib/config.json`
- 模型与回测：`signal-factory/config/model_*.yaml`、`backtest.yaml`

> 仓库内示例路径假设：仓库位于 `D:/AgentAlpha`，Qlib 数据位于 `D:/qlib/qlib_data/cn_data`。请按本机实际路径修改后再运行。

### 运行

```bash
# 1) 更新数据（akshare/baostock → Qlib bin）
python signal-factory/scripts/update_daily.py --end 2026-01-01

# 2) 生成 Alpha 信号（跳过数据更新）
python signal-factory/scripts/daily_pipeline.py --skip-update

# 3) Agent 管线（Windows PowerShell）
powershell -File .\my-bot\scripts\run-morning-brief.ps1

# 4) 测试
pytest signal-factory/tests -q
cd my-bot && npm test
```

## 数据与隐私

- 本仓库**不附带行情/财务数据**，也不包含个人账户、持仓、交易记录或分析报告。
- 数据需自行准备：`signal-factory/scripts/update_daily.py`（akshare/baostock）或接入 Wind MCP；输出为 Qlib bin 格式。
- `portfolio.json`、`my-bot/data/*`、`my-bot/reports/` 等运行数据已被 `.gitignore` 排除。

## 安全扫描

> GitHub 原生 **Secret scanning / Push protection** 对个人私有仓不可用（属于 GitHub Secret Protection，主要面向组织版；公开仓免费）。因此本仓库自带两层扫描：

- **CI 全历史扫描**：`.github/workflows/secret-scan.yml`（TruffleHog OSS，push / PR / 每周一自动运行）
- **本地扫描**：
  ```powershell
  winget install gitleaks          # 一次性安装
  powershell -File scripts/secret_scan.ps1          # 扫描 git 历史 + 工作区
  powershell -File scripts/secret_scan.ps1 -Files   # 仅扫描工作区
  ```

## 第三方组件与许可

本项目 MIT 许可；同时使用/参考了若干第三方组件（RD-Agent、Qlib、LightGBM、XGBoost、CatBoost、a-stock-data、Wind 技能模板等）。详见 [docs/THIRD_PARTY_NOTICES.md](docs/THIRD_PARTY_NOTICES.md)。

## 已知限制

- **复权口径**：为简化本地化，当前数据管线使用前复权价 + `factor=1`，与 Qlib "原始价 + 复权因子" 的约定不完全一致；跨除权日的历史序列可能被重写，实盘前建议改为原始价 + 真实复权因子。
- **T+1 / 涨跌停**：回测与执行门禁已支持部分规则（ST/停牌、涨跌停、成交额占比），但 T+1 卖出限制尚未完整建模。
- **路径**：示例路径按作者环境编写，首次运行前请按本机修改配置。
- **运行环境**：以 Windows 11 + WSL2 为主；其他平台需自行适配脚本。

## 参考

- RD-Agent：<https://github.com/microsoft/RD-Agent>，论文 *R&D-Agent-Quant*（arXiv 2505.15155，NeurIPS 2025）
- Qlib：<https://github.com/microsoft/qlib>，论文 *Qlib: An AI-oriented Quantitative Investment Platform*（arXiv 2009.11189）
- Alpha158/360：Qlib 内置特征集

## 许可证

MIT License — 详见 [LICENSE](LICENSE)。

---

🤖 本项目代码主体由 [Claude Code](https://claude.com/claude-code) 辅助编写。
