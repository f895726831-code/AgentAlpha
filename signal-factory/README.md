# Quant Signal Factory — AgentAlpha 信号工厂

每日盘后：**数据同步 → 因子/模型 → 回测验证 → JSON 契约 → AgentAlpha（my-bot）**。

## 架构

```
Wind MCP / akshare / baostock 数据
   ↓
PIT 数据引擎 + 泄漏检测 + OOS 门禁
   ↓
因子 / 模型（GBDT / XGBoost / CatBoost，Alpha158/360）
   ↓
Qlib 回测 + Gate 1 评分 + 执行现实性门禁
   ↓
JSON 契约（@contract / manifest / staleness）
   ↓
AgentAlpha（my-bot）消费
```

## 快速开始

```bash
cd signal-factory
pip install -e ".[dev]"

# 配置：config/data.yaml（数据路径）、config/model_*.yaml（模型参数）

# 1) 数据更新（akshare/baostock → Qlib bin）
python scripts/update_daily.py --end 2026-01-01

# 2) 生成信号（跳过数据更新）
python scripts/daily_pipeline.py --skip-update

# 3) 测试
pytest tests -q      # 49 passed, 1 skipped
```

## 目录

```
signal-factory/
├── src/
│   ├── data/             # Wind/akshare 桥、PIT、泄漏检测、OOS 门禁、配置
│   ├── factors/          # 模型配置、Alpha 信号、验证/假设/日志
│   ├── backtest/         # Qlib 适配、信号生成、Gate1 评分、交叉验证、执行门禁
│   ├── export/           # JSON 契约（agent_alpha_bridge）
│   ├── sector_rotation/  # 板块轮动：数据/特征/模型/回测/契约
│   ├── macro_allocation/ # 宏观大类资产：HMM/MVO/BL/回测
│   └── utils/            # 通用工具
├── scripts/              # 数据更新、每日管线、回测、宏观看板
├── config/               # data.yaml / model_*.yaml / backtest.yaml
└── tests/                # 4 个测试文件
```

## 与 AgentAlpha（my-bot）的关系

信号工厂独立运行，通过 JSON 文件与 AgentAlpha 通信：

- 输出：`data/output/alpha_signals*.json`、`sector_signals.json`、`asset_allocation.json`
- `my-bot/scripts/sync_signal_factory.py` 将 JSON 同步到 AgentAlpha 的 `data/output/`
- AgentAlpha 读取 `alpha_signals.json` 获取 ML 排名；文件缺失或过期（`@contract.staleness`）时降级为纯 Agent 定性选股

> 详细接口以源码 docstring 与 `src/export/agent_alpha_bridge.py` 为准；`USAGE.md` 为历史文档，可能滞后。

## 数据说明

- 本仓库**不附带**行情/财务数据；需自行准备 Qlib bin 数据（默认路径 `D:/qlib/qlib_data/cn_data`，可配置）。
- 使用 Wind / Tushare 等商业数据源时，请遵守对应服务条款。

## 测试

```bash
pytest tests -q
```

## 许可证

MIT — 可自由使用、修改、分发。
