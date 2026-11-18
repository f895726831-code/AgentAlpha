---
name: qlib-alpha-signal
description: Qlib ML Alpha 信号生成 — 使用 signal-factory 的 GBDT/XGBoost/LSTM/TabNet 模型对全市场标的生成定量 alpha 排名，作为 Stage 1 选股的第六个 skill，与 Agent 定性选股并行运行。读取 data/output/alpha_signals.json 获取 ML 排名，与 Agent 选股结果取交集优先。
---

# Qlib Alpha 信号 (Qlib Alpha Signal)

使用 signal-factory（RD-Agent + Qlib 自动化量化信号工厂）的 ML 模型对全市场标的生成定量 alpha 排名，弥补 Agent 定性选股的盲区。

## 核心理念

**"Agent 定性判断 + ML 定量排名 = 更全面的选股"**

- Agent 擅长形态识别、逻辑推理、多维度综合判断
- ML 擅长从海量数据中发现统计规律和隐藏因子
- 两者互补：交集优先 = 高置信度，差集 = 需要复核

## 触发条件

| 触发方式 | 说明 |
|---|---|
| 盘后定时 | 盘后闭环中，cron 自动调用 `python -m src.factors.alpha_signals --ensemble` |
| 主动触发 | 用户要求"运行 Qlib alpha"或"ML 选股" |
| 周度重训 | 每周一次模型重训（默认周六凌晨），`python -m src.factors.alpha_signals --model gbdt` |

## 依赖

- **数据**: Qlib bin 数据（由 `scripts/sync_data.py` → `WindBridge` 维护）
- **模型**: 训练好的 LightGBM/XGBoost 模型（缓存在 `data/output/models/`）
- **信号工厂**: `D:/AgentAlpha/signal-factory` — 独立 Python 项目
- **输出**: `data/output/alpha_signals.json`

## 工作流

### 1. 读取 Alpha 信号

读取 `data/output/alpha_signals.json` 获取最新一天的 ML 排名：

```json
{
  "date": "2026-08-07",
  "model": "ensemble_gbdt+xgboost",
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

### 2. 获取 Agent 选股结果

从 Stage 1 的 5 个选股 skill 获取候选列表 A：
- `breakout_candidate_finder` — 突破候选
- `pullback_opportunity_finder` — 回调低吸
- `vcp_breakout_scan` — VCP 突破
- `canslim_growth_scan` — CANSLIM 成长
- `a-share-primary-theme-identification` — 主线题材

### 3. 合并策略

```
A = Agent 选股候选列表
B = Qlib ML Top 50 (alpha 排名)

A ∩ B → 高置信度（优先进入 Gate 1 回测关）
A \ B → 中置信度（Agent 独有，需额外论证为何 ML 未发现）
B \ A → 低置信度（ML 独有，提示 Agent 复核是否遗漏）
```

### 4. 输出合并结果

在 Stage 1 输出中增加 ML 交叉标记：

```markdown
## Stage 1 选股 — ML 交叉验证

| 代码 | 名称 | 来源 | ML Rank | ML Alpha | 置信度 | 操作 |
|---|---|---|---|---|---|---|
| 688981 | 中芯国际 | breakout + vcp | 3 | +0.0723 | 高 ∩ | 优先进入 Gate 1 |
| 002371 | 北方华创 | pullback | 12 | +0.0512 | 高 ∩ | 优先进入 Gate 1 |
| 600519 | 贵州茅台 | canslim | 87 | +0.0081 | 中 A\B | 需额外论证 |
| 300750 | 宁德时代 | — | 5 | +0.0689 | 低 B\A | Agent 复核 |
```

### 5. 置信度处理

| 置信度 | Gate 1 回测关处理 |
|---|---|
| 高 ∩ (A ∩ B) | 标准回测，Deploy 阈值不变 |
| 中 A\B (Agent 独有) | 标准回测，但需附 Agent 论证说明 ML 未发现的原因 |
| 低 B\A (ML 独有) | Agent 快速复核后决定是否进入回测，如进入则降低 Deploy 阈值 3 分 |

## 模式感知

| 模式 | 行为 |
|---|---|
| `normal` | ML alpha 正常参与选股，Top 50 与 Agent 选股取交集 |
| `earnings_season` | 可切换为 `tabnet` 模型（特征交互更强，适合财报季） |
| `risk_off` | ML alpha 仅输出观察列表，不参与选股决策 |

## 模型信息

signal-factory 提供 5 个预配置模型：

| 模型 | 类型 | 训练频率 | GPU | 说明 |
|---|---|---|---|---|
| `gbdt` | LightGBM (GBDT) | 每周 | 否 | 日常 alpha 基线，默认首选 |
| `xgboost` | XGBoost | 每周 | 否 | 备选，与 GBDT 取均值 |
| `lstm` | LSTM (PyTorch) | 每月 | 是 | 时序动量捕捉 |
| `tabnet` | TabNet (PyTorch) | 每月 | 是 | 特征交互，财报季推荐 |
| `gbdt_large` | LightGBM (大容量) | 每周 | 否 | 更大容量 + 更多叶子 |

## 手动运行

```bash
cd D:/AgentAlpha/signal-factory

# 使用缓存模型预测（日常）
python -m src.factors.alpha_signals --model gbdt --no-train

# 集成模型（GBDT + XGBoost 均值，推荐）
python -m src.factors.alpha_signals --ensemble --models gbdt xgboost

# 重新训练 + 预测（每周）
python -m src.factors.alpha_signals --model gbdt

# 指定日期
python -m src.factors.alpha_signals --model gbdt --date 2026-08-07

# 切换模型（财报季）
python -m src.factors.alpha_signals --model tabnet

# 查看 Python API
python -c "from src.factors import list_models, get_active_models; print(list_models())"
```

## Alpha 分数解读

- **alpha > 0.05**: 强看多，模型预测该标的未来收益显著高于市场均值
- **alpha 0.02 ~ 0.05**: 看多，有一定超额收益预期
- **alpha -0.02 ~ 0.02**: 中性，无明显方向性信号
- **alpha < -0.02**: 看空，模型预测该标的可能跑输市场

**注意**: Alpha 是相对排名信号，不是绝对收益率预测。重点看排名而非绝对值。

## 局限性

1. **只基于历史数据**: ML 模型无法预测突发事件（政策、黑天鹅）
2. **因子覆盖有限**: Alpha158 覆盖 158 个因子，无法捕捉所有 alpha 来源
3. **过拟合风险**: 小样本或短周期训练可能导致过拟合
4. **市场结构变化**: 因子有效性会随时间衰减（alpha decay）
5. **不替代基本面**: ML 排名不能替代基本面深度研究

## 与管线其他技能的关系

- **上游**: `sync_data.py` (signal-factory) — 提供 Qlib bin 数据
- **同级**: Stage 1 的 5 个选股 skill — 并行运行，结果取交集
- **下游**: `backtester` (Gate 1) — 接收 ML 交叉验证标记的候选列表
- **下游**: `signal-gen` — 接收合并后的选股结果

## 信号工厂架构参考

signal-factory 是一个独立的 22 模块 Python 项目，通过 JSON 契约与 AgentAlpha 松耦合：

```
signal-factory/
├── src/data/           # PIT 引擎 + 泄漏检测 + Wind 数据桥 + 配置加载
├── src/factors/        # RD-Agent 因子挖掘 + 模型配置 + Alpha 信号生成
├── src/backtest/       # Qlib 回测适配 + 信号生成器 + Gate 1 评分
├── src/export/         # AgentAlpha JSON 契约导出
├── config/             # YAML 配置
└── scripts/            # CLI 入口
```

详见 `D:/AgentAlpha/signal-factory/USAGE.md`。

## 输出文件

- `data/output/alpha_signals.json` — 每日 alpha 排名输出
- `data/output/models/<model_name>_weekly.pkl` — 训练好的模型缓存
- `data/output/predictions/` — 预测结果存档