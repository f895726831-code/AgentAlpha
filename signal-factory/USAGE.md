# Quant Signal Factory — 使用指南

> RD-Agent + Qlib 自动化量化信号工厂  
> 22 个模块，5 层架构，零摩擦集成 AgentAlpha

---

## 架构总览

```
                         signal-factory
┌──────────────────────────────────────────────────────────────────────┐
│                                                                      │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐         │
│  │  config/  │   │ scripts/  │   │  src/    │   │  tests/  │         │
│  │  YAML 配置 │   │ CLI 入口  │   │  核心代码 │   │  20 测试  │         │
│  └──────────┘   └──────────┘   └──────────┘   └──────────┘         │
│                                     │                                │
│               ┌─────────────────────┼─────────────────────┐         │
│               ▼                     ▼                     ▼         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────┐│
│  │  data/        │  │  factors/    │  │  backtest/   │  │  export/ ││
│  │  PIT 引擎     │  │  RD-Agent    │  │  Qlib 适配   │  │  JSON    ││
│  │  泄漏检测     │  │  模型配置    │  │  Gate 1 打分 │  │  契约    ││
│  │  Wind 桥     │  │  Alpha 信号  │  │  信号生成器  │  │  导出    ││
│  │  配置加载    │  │  验证+合成   │  │              │  │          ││
│  └──────────────┘  └──────────────┘  └──────────────┘  └──────────┘│
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
        │                                                      │
        ▼                                                      ▼
  Wind MCP 数据                                        AgentAlpha 管线
  (行情/财务)                                    data/output/alpha_signals.json
```

---

## 快速开始

### 1. 安装

```bash
cd D:/AgentAlpha/signal-factory
python -m venv venv
venv\Scripts\activate
pip install -e .
```

### 2. 配置

```bash
# 编辑 config/data.yaml — 设置数据路径
# 编辑 config/rd_agent.yaml — 设置 DeepSeek API Key
```

### 3. 首次运行

```bash
# 同步数据（Wind MCP → Qlib）
python scripts/sync_data.py --wind-dir D:/AgentAlpha/my-bot/data/wind

# 运行 PIT 泄漏检测测试
python -m pytest tests/test_leakage_checker.py -v

# 生成 Alpha 信号
python -m src.factors.alpha_signals --model gbdt
```

---

## 数据层 (src/data/)

### 1. PIT 数据处理器 (`pit_handler.py`)

防止未来函数的第一道防线。确保任意历史时刻只能访问该时刻之前的数据。

```python
from src.data import PITDataHandler, PITCheckpoint

# 初始化
handler = PITDataHandler(
    release_delays={"financial": 30, "market": 0},
    bin_dir="D:/qlib/qlib_data/cn_data",
)

# 获取 PIT 数据
data = handler.get_features(
    instruments=["sh600519"],
    fields=["$close", "$volume", "$open"],
    as_of="2026-08-01",
)

# 创建检查点
cp = handler.checkpoint("2026-08-01")
prices = cp.get_prices("sh600519")  # 只包含 8/1 之前的数据
```

### 2. PIT 泄漏检测 (`pit_handler.py` + `leakage_checker.py`)

**双层防线**：正则表达式 + AST 静态分析，并行运行。

```python
from src.data import detect_pit_violation, validate_strategy_pit, analyze_ast, check_factor_code

code = """
close = data["close"]
for i in range(30, len(data)):
    if close.iloc[i + 3] > 60:  # ⚠ 向前索引 — 未来函数
        signal[i] = 1
"""

# 正则检测
violations = detect_pit_violation(code, "my_strategy")
# → [{"line": 4, "pattern_name": "向前索引", "severity": "critical", ...}]

# AST 检测（更深层）
passed, violations, errors = analyze_ast(code, "my_strategy")
# → passed=False, 检测到循环内向前索引

# 组合检测（推荐）
result = check_factor_code(code, "my_strategy")
# → {"passed": False, "ast": {...}, "regex": {...}, "summary": "..."}

# 策略验证
passed, violations = validate_strategy_pit(code, "my_strategy", strict=True)
```

**检测能力**（6 种模式）：

| 模式 | 严重度 | 示例 |
|:---|:---|:---|
| 向前索引 | critical | `close.iloc[i + 3]` |
| 负向 shift | critical | `close.shift(-1)` |
| 前瞻切片 | critical | `close.iloc[i+1:i+4]` |
| 滚动均值前移 | critical | `rolling().mean().shift(-1)` |
| 前瞻变量名 | high | `look_ahead = i + 3` |
| 全区间统计 | warning | `close.max()` 未限制范围 |

### 3. Wind 数据桥 (`wind_bridge.py`)

Wind MCP JSON → Qlib CSV → Qlib bin 的完整数据管道。

```python
from src.data import WindBridge, sync_wind_to_qlib, get_wind_data_summary

# 查看 Wind 数据摘要
summary = get_wind_data_summary("D:/AgentAlpha/my-bot/data/wind")
# → {"file_count": 50, "symbols": ["sh600519", ...], "total_size_mb": 12.5}

# 方式 1: 便捷函数
result = sync_wind_to_qlib(
    wind_json_dir="D:/AgentAlpha/my-bot/data/wind",
    symbols=["sh600519", "sz000001"],  # None = 全部
    force=False,  # 默认增量
)
# → {"synced_count": 2, "errors": [], "bin_dumped": True, "duration": 2.3}

# 方式 2: 类实例（更多控制）
bridge = WindBridge()
bridge.discover_wind_files("D:/AgentAlpha/my-bot/data/wind")
symbol = bridge.extract_symbol("600519_daily", {"code": "600519"})  # → "sh600519"
df = bridge.extract_ohlcv(data)  # → DataFrame [open, high, low, close, volume]
bridge.dump_to_bin()  # CSV → bin
```

**CLI 用法**：

```bash
# 全量同步
python scripts/sync_data.py --wind-dir D:/AgentAlpha/my-bot/data/wind

# 指定标的
python scripts/sync_data.py --symbols sh600519 sz000001

# 强制全量 + 保存日志
python scripts/sync_data.py --force --output logs/sync_20260807.json

# 仅查看摘要
python scripts/sync_data.py --summary
```

### 4. 配置加载器 (`config_loader.py`)

统一从 `config/data.yaml` 读取配置，支持环境变量覆盖。

```python
from src.data import get_config, get_qlib_csv_dir, get_qlib_bin_dir, get_wind_json_dir

csv_dir = get_qlib_csv_dir()   # D:/qlib/qlib_data/source/cn_1d
bin_dir = get_qlib_bin_dir()   # D:/qlib/qlib_data/cn_data
wind_dir = get_wind_json_dir() # D:/AgentAlpha/my-bot/data/wind

# 环境变量覆盖
# export QLIB_DATA_DIR=/custom/path  → get_qlib_bin_dir() 返回 /custom/path

# 强制重新加载
from src.data import reload_config
reload_config()
```

---

## 因子层 (src/factors/)

### 5. RD-Agent 因子挖掘 (`rd_runner.py`)

DeepSeek-V4 驱动的自动化因子挖掘。

```python
from src.factors import RDAgentRunner

runner = RDAgentRunner("config/rd_agent.yaml")
result = runner.run_mining(
    instruments=["sh600519", "sz000001"],
    start_time="2020-01-01",
    end_time="2026-08-07",
)
# → {"factors": [...], "pit_results": {...}, "summary": {...}}
```

**CLI**：
```bash
python -m src.factors.rd_runner --config config/rd_agent.yaml --output factors.json
```

### 6. 模型配置 (`model_configs.py`)

5 个预配置模型，支持 GBDT/XGBoost/LSTM/TabNet。

```python
from src.factors import (
    ModelConfig, MODEL_CONFIGS, get_model_config,
    list_models, get_active_models, get_model_ensemble_configs,
)

# 列出所有模型
list_models()  # → ['gbdt', 'xgboost', 'lstm', 'tabnet', 'gbdt_large']

# 获取默认活跃模型（日常预测用）
get_active_models()  # → ['gbdt', 'xgboost']

# 获取单个模型配置
cfg = get_model_config("gbdt")
print(cfg.display_name)   # "LightGBM (GBDT)"
print(cfg.num_boost_round)  # 300
print(cfg.train_frequency)  # "weekly"

# 构建 Qlib 模型实例
model = cfg.build_model()      # → LGBModel(loss='mse', num_leaves=64, ...)
handler = cfg.build_handler()  # → Alpha158(instruments='csi500', ...)

# 获取集成配置
ensemble = get_model_ensemble_configs(["gbdt", "xgboost"])
```

**模型一览**：

| 名称 | 类型 | 频率 | GPU | 参数 | 用途 |
|:---|:---|:---|:---|:---|:---|
| `gbdt` | LightGBM | 每周 | 否 | 300 轮 | 默认基线 |
| `xgboost` | XGBoost | 每周 | 否 | 300 轮 | 备选集成 |
| `lstm` | LSTM | 每月 | 是 | 100 epochs | 时序动量 |
| `tabnet` | TabNet | 每月 | 是 | 100 epochs | 特征交互 |
| `gbdt_large` | LightGBM | 每周 | 否 | 500 轮 | 大容量 |

### 7. Alpha 信号生成 (`alpha_signals.py`)

Qlib ML 模型训练→预测→排名→输出的完整管线。

```python
from src.factors import (
    AlphaSignalGenerator,
    generate_alpha_signals,
    generate_ensemble_signals,
    get_latest_alpha_signals,
)

# 方式 1: 便捷函数
signals = generate_alpha_signals("gbdt")
# → {"date": "2026-08-07", "top_10": [...], "top_50": [...], "all_scores": {...}}

# 方式 2: 类实例（更多控制）
gen = AlphaSignalGenerator()

# 训练模型
train_result = gen.train("gbdt", instruments="csi500", force_retrain=True)
# → {"model_name": "gbdt", "ic_mean": 0.032, "rank_ic": 0.041, "status": "trained"}

# 预测（跳过训练，使用已有模型）
ranked = gen.predict("gbdt", instruments="csi500", date="2026-08-07")
# → DataFrame [symbol, alpha_score, rank]

# 集成预测
ranked = gen.predict_ensemble(["gbdt", "xgboost"], method="mean")

# 完整运行
result = gen.run("gbdt", train_first=True, top_n=50)
# → 训练→预测→排名→保存 data/output/alpha_signals.json

# 集成运行
result = gen.run_ensemble(["gbdt", "xgboost"])

# 读取最近信号
signals = gen.get_latest_signals()
top_symbols = gen.get_top_symbols(10)  # → ['sh600519', 'sz000001', ...]
```

**CLI**：
```bash
# 单模型
python -m src.factors.alpha_signals --model gbdt --date 2026-08-07

# 集成（GBDT + XGBoost 均值）
python -m src.factors.alpha_signals --ensemble --models gbdt xgboost

# 跳过训练，仅预测
python -m src.factors.alpha_signals --model gbdt --no-train

# 输出到指定文件
python -m src.factors.alpha_signals --model gbdt --output signals.json
```

**输出格式** (`data/output/alpha_signals.json`)：
```json
{
  "date": "2026-08-07",
  "model": "gbdt",
  "generated_at": "2026-08-07T16:30:00",
  "total_instruments": 500,
  "top_10": [
    {"symbol": "sh600519", "alpha_score": 0.0823, "rank": 1.0},
    {"symbol": "sz000858", "alpha_score": 0.0751, "rank": 2.0}
  ],
  "top_50": [...],
  "all_scores": {"sh600519": 0.0823, "sz000858": 0.0751, ...}
}
```

### 8. 因子验证 + 合成 + 持久化

```python
from src.factors import validate_factor, batch_validate
from src.factors import combine_factors, get_consensus_ranking
from src.factors import FactorStore

# 验证
result = validate_factor(factor_dict, start_date="2020-01-01", end_date="2026-08-07")
results = batch_validate(factors_list)

# 合成
combined = combine_factors(factors, method="equal_weight")  # equal_weight / ic_weight / rank_ensemble
ranking = get_consensus_ranking(factors, date="2026-08-07")

# 持久化
store = FactorStore("data/output/factors/factor_store.json")
store.save_factors(new_factors, evaluations)
active = store.get_active_factors(min_ic=0.02)
retired = store.check_retirement(ic_threshold=0.01, consecutive_months=3)
```

---

## 回测层 (src/backtest/)

### 9. Qlib 回测适配器 (`adapter.py`)

```python
from src.backtest.adapter import init_qlib, run_backtest, map_strategy_to_qlib_config

# 初始化 Qlib
init_qlib()  # 自动读取 config/data.yaml 中的路径

# 执行回测
result = run_backtest(
    symbol="002371",
    strategy_name="breakout_60",
    signal=signal_series,
    start_time="2020-01-01",
    end_time="2026-08-07",
    account=1_000_000,
)
# → {"meta": {...}, "portfolio": {...}, "trades": [...], "summary": {
#     "total_trades": 45, "win_rate": 55.6, "profit_factor": 1.82,
#     "expectancy": 1.25, "verdict": "Deploy"
# }}
```

### 10. 信号生成器 (`signal_generator.py`)

6 个策略的信号生成器，全部通过 PIT 自检：

```python
from src.backtest.signal_generator import (
    generate_breakout_60,      # 60日突破
    generate_pullback_low,     # 回调低吸
    generate_vcp_breakout,     # VCP 突破
    generate_oversold_bounce,  # 超跌反弹
    generate_golden_cross,     # 金叉
    generate_limit_up_chase,   # 涨停追击
)
```

---

## 导出层 (src/export/)

### 11. AgentAlpha JSON 契约

```python
from src.export.export_signals import export_daily_signals

# 导出每日信号
result = export_daily_signals(
    date="2026-08-07",
    signals=alpha_rankings,
    output_dir="data/output/signals",
)
# → data/output/signals/2026-08-07.json
```

---

## 与 AgentAlpha 集成

### 数据流

```
Wind MCP                    signal-factory              AgentAlpha
───────                     ────────────────────              ───────
                                                              Stage 0
                                                                 │
JSON 行情 ──→ sync_data.py ──→ Qlib bin 数据                     │
                                                                 ▼
                Alpha 信号 ←── run_alpha.py                    Stage 1
                     │                                         选股阶段
                     ▼                                          │
         data/output/alpha_signals.json ──────────────→ qlib_alpha_signal skill
                                                              │
                                                           A ∩ B 交集
                                                              │
                                                              ▼
                Gate 1 回测 ←── backtest_adapter.py          Gate 1
                                                              │
                                                              ▼
                                                           Stage 2-4
```

### 每日运行命令

```bash
# 1. 同步数据（Wind → Qlib）
python scripts/sync_data.py

# 2. 生成 Alpha 信号
python -m src.factors.alpha_signals --ensemble --models gbdt xgboost

# 3. 运行 RD-Agent 因子挖掘（每周）
python -m src.factors.rd_runner --config config/rd_agent.yaml
```

### 信号工厂不可用时的降级

AgentAlpha 读取 `data/output/alpha_signals.json`。如果文件不存在（信号工厂未运行），AgentAlpha 自动降级为纯 Agent 定性选股，不阻塞管线。

---

## 配置参考

### `config/data.yaml`

```yaml
data:
  wind:
    json_dir: "D:/AgentAlpha/my-bot/data/wind"
    symbols_file: "D:/AgentAlpha/my-bot/data/watchlist.json"

  qlib:
    csv_dir: "D:/qlib/qlib_data/source/cn_1d"
    bin_dir: "D:/qlib/qlib_data/cn_data"
    freq: "day"
    region: "cn"

  sync:
    schedule: "daily_after_close"
    retention_days: 1825
    auto_dump_bin: true

  pit:
    enabled: true
    release_delays:
      financial: 30
      market: 0
    forbidden_operations:
      - "shift(-1)"
      - ".iloc[i+"
      - "look_ahead"
```

### 环境变量覆盖

| 变量 | 覆盖配置 | 默认值 |
|:---|:---|:---|
| `QLIB_DATA_DIR` | `data.qlib.bin_dir` | `D:/qlib/qlib_data/cn_data` |
| `QLIB_CSV_DIR` | `data.qlib.csv_dir` | `D:/qlib/qlib_data/source/cn_1d` |
| `WIND_JSON_DIR` | `data.wind.json_dir` | `D:/AgentAlpha/my-bot/data/wind` |
| `QLIB_ROOT` | Qlib Python 包路径 | `D:/qlib` |

---

## 测试

```bash
# 全部测试
python -m pytest tests/ -v

# 仅泄漏检测（20 个测试）
python -m pytest tests/test_leakage_checker.py -v

# 覆盖率
python -m pytest tests/ --cov=src --cov-report=html
```

---

## 项目完整度

| 层 | 模块 | 状态 |
|:---|:---|:---|
| config | `data.yaml`, `rd_agent.yaml` | ✅ |
| scripts | `sync_data.py` | ✅ |
| data/ | `pit_handler.py`, `leakage_checker.py`, `wind_bridge.py`, `config_loader.py`, `qlib_init_helper.py` | ✅ 5/5 |
| factors/ | `rd_runner.py`, `model_configs.py`, `alpha_signals.py`, `validator.py`, `combiner.py`, `store.py` | ✅ 6/6 |
| backtest/ | `adapter.py`, `signal_generator.py`, `gate_scorer.py` | ✅ 3/3 |
| export/ | `export_signals.py`, `contract.py` | ✅ 2/2 |
| tests/ | `test_leakage_checker.py` (20 tests) | ✅ |
| **总计** | | **22/22 (100%)** |