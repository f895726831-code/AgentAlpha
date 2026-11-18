# Quant Signal Factory

RD-Agent + Qlib 自动化量化信号工厂。每日盘后自动运行：数据同步 → 因子挖掘 → 回测验证 → 信号导出。

22 个模块，5 层架构，零摩擦集成 FM_trade。

## 架构

```
Wind MCP 数据 → PIT 数据引擎 → RD-Agent 因子挖掘 → Qlib 回测 → JSON 契约 → FM_trade
```

## 快速开始

```bash
# 安装
python -m venv venv
venv\Scripts\activate
pip install -e .

# 配置
# 编辑 config/data.yaml — 设置数据路径
# 编辑 config/rd_agent.yaml — 设置 DeepSeek API Key

# 同步数据
python scripts/sync_data.py

# 运行测试
python -m pytest tests/test_leakage_checker.py -v

# 生成 Alpha 信号
python -m src.factors.alpha_signals --ensemble --models gbdt xgboost
```

## 项目结构

```
quant-signal-factory/
├── config/           # YAML 配置文件
│   ├── data.yaml     # 数据源、Qlib 路径、PIT 配置
│   └── rd_agent.yaml # RD-Agent + DeepSeek API 配置
├── scripts/          # CLI 入口脚本
│   └── sync_data.py  # Wind → Qlib 数据同步
├── src/              # 核心代码
│   ├── data/         # 数据层 (5 模块)
│   │   ├── pit_handler.py       # PIT 数据引擎
│   │   ├── leakage_checker.py   # AST 级泄漏检测
│   │   ├── wind_bridge.py       # Wind MCP 数据桥
│   │   ├── config_loader.py     # 统一配置加载
│   │   └── qlib_init_helper.py  # Qlib mlflow 兼容
│   ├── factors/      # 因子层 (6 模块)
│   │   ├── rd_runner.py         # RD-Agent 因子挖掘
│   │   ├── model_configs.py     # 5 模型配置 (GBDT/XGBoost/LSTM/TabNet)
│   │   ├── alpha_signals.py     # ML Alpha 信号生成
│   │   ├── validator.py         # 因子验证
│   │   ├── combiner.py          # 因子合成
│   │   └── store.py             # 因子持久化
│   ├── backtest/     # 回测层 (3 模块)
│   │   ├── adapter.py           # Qlib 回测适配
│   │   ├── signal_generator.py  # 6 策略信号生成器
│   │   └── gate_scorer.py       # Gate 1 评分
│   └── export/       # 导出层 (2 模块)
│       ├── export_signals.py    # 信号导出
│       └── contract.py          # FM_trade JSON 契约
├── tests/            # 测试 (20 tests)
├── data/             # 数据目录
│   └── output/       # 输出 (alpha_signals.json, models/)
└── USAGE.md          # 详细使用指南
```

## 完整文档

详见 **[USAGE.md](USAGE.md)** — 包含所有 22 个模块的 API 文档、CLI 用法、配置参考和 FM_trade 集成指南。

## 与 FM_trade 的关系

信号工厂独立运行，通过 JSON 文件与 FM_trade 通信。FM_trade 读取 `data/output/alpha_signals.json` 获取每日信号。信号工厂不可用时，FM_trade 自动降级到 Agent 定性选股。

## 测试

```bash
python -m pytest tests/ -v                            # 全部测试
python -m pytest tests/test_leakage_checker.py -v     # 泄漏检测 (20 tests)
```

## 许可证

MIT — 可自由使用、修改、分发。