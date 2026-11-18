# Quant Signal Factory — 自然语言指令集

> 适用版本: signal-factory v1.0 | 22 模块 | 日期: 2026-08-07
> 通过自然语言操控信号工厂的完整数据→因子→回测→导出管线。

---

## 一、数据同步

### 1.1 每日盘后同步

```
【数据同步】将 Wind MCP 数据同步到 Qlib：

1. 扫描 D:/AgentAlpha/my-bot/data/wind 目录下的 JSON 文件
2. 自动识别标的代码和交易所前缀
3. 提取 OHLCV 数据，转换为 Qlib CSV 格式
4. 增量合并：只添加新日期，不重复写入
5. 转换为 Qlib bin 格式
6. 输出同步日志

使用: python scripts/sync_data.py
```

### 1.2 指定标的同步

```
【数据同步】只同步以下标的：
- 标的: sh600519, sz000001, sh688981, sz300750
- 增量模式（默认），不强制全量
- 同步完成后自动转换 bin 格式

使用: python scripts/sync_data.py --symbols sh600519 sz000001 sh688981 sz300750
```

### 1.3 强制全量同步

```
【数据同步】强制全量同步，覆盖已有数据：
- 清空已有 CSV，从 Wind JSON 重新生成
- 适用场景：数据格式变更、发现数据错误、首次初始化
- 保存同步日志到 logs/sync_20260807.json

使用: python scripts/sync_data.py --force --output logs/sync_20260807.json
```

### 1.4 查看数据摘要

```
【数据摘要】查看 Wind 数据目录中有多少文件、哪些标的、数据量：
- 不执行同步，仅扫描统计

使用: python scripts/sync_data.py --summary
```

---

## 二、Alpha 信号生成

### 2.1 日常生成（推荐）

```
【Alpha 信号】生成今日 ML 选股排名：

1. 使用 GBDT + XGBoost 集成模型（均值法）
2. 对全市场 csi500 标的预测 alpha 分数
3. 输出 Top 50 排名到 data/output/alpha_signals.json
4. 使用已有模型（不重训，快速）

使用: python -m src.factors.alpha_signals --ensemble --models gbdt xgboost --no-train
```

### 2.2 单模型生成

```
【Alpha 信号】仅用 LightGBM 生成：

1. 使用已有 gbdt 模型预测
2. 输出 Top 50 排名
3. 跳过训练（--no-train）

使用: python -m src.factors.alpha_signals --model gbdt --no-train
```

### 2.3 重训 + 生成

```
【Alpha 信号】重新训练 GBDT 模型并生成信号：

1. 从 Alpha158 因子集加载特征
2. 在 csi500 上训练 LightGBM（300 轮，early stop 50 轮）
3. 计算 IC 和 Rank IC
4. 保存模型到 data/output/models/gbdt_weekly.pkl
5. 全市场预测并输出排名

使用: python -m src.factors.alpha_signals --model gbdt
```

### 2.4 财报季模式（TabNet）

```
【Alpha 信号】财报季切换为 TabNet 模型：

1. TabNet 注意力机制，自动选择重要特征交互
2. 适合财报季特征重要性变化大的场景
3. 需要 GPU，无 GPU 则回退到 GBDT

使用: python -m src.factors.alpha_signals --model tabnet
```

### 2.5 指定日期

```
【Alpha 信号】生成指定日期的信号：

1. 预测日期: 2026-08-07
2. 使用 GBDT + XGBoost 集成
3. 输出到指定文件

使用: python -m src.factors.alpha_signals --ensemble --models gbdt xgboost --date 2026-08-07 --output signals/20260807.json
```

### 2.6 查看模型列表

```
【Alpha 信号】列出所有可用模型和配置：

可用模型: gbdt, xgboost, lstm, tabnet, gbdt_large
活跃模型（日常默认）: gbdt, xgboost
GPU 模型: lstm, tabnet

使用: python -c "from src.factors import list_models, get_active_models; print('可用:', list_models()); print('活跃:', get_active_models())"
```

---

## 三、模型训练

### 3.1 训练单个模型

```
【模型训练】训练 GBDT 模型：

1. 特征集: Alpha158（158 维因子）
2. 训练标的: csi500
3. 训练区间: 2015-01-01 ~ 2024-12-31
4. 超参数: num_leaves=64, lr=0.05, feature_fraction=0.8
5. 300 轮 Boosting，early stop 50 轮
6. 验证集比例 20%
7. 输出 IC 指标

Python API:
from src.factors import AlphaSignalGenerator
gen = AlphaSignalGenerator()
result = gen.train("gbdt", instruments="csi500", force_retrain=True)
print(result["ic_mean"], result["rank_ic"])
```

### 3.2 训练 LSTM（GPU）

```
【模型训练】训练 LSTM 时序模型：

1. 需要 GPU，无 GPU 则跳过
2. 特征集: Alpha158
3. 超参数: hidden_size=128, num_layers=2, dropout=0.2
4. 100 epochs，early stop 10 epochs
5. 训练频率: 每月一次

Python API:
gen = AlphaSignalGenerator()
result = gen.train("lstm", instruments="csi500")
```

### 3.3 查看训练结果

```
【模型训练】查看 GBDT 模型的 IC 指标：

Python API:
from src.factors import AlphaSignalGenerator
gen = AlphaSignalGenerator()
result = gen.train("gbdt")  # 如果已训练，返回 cached
print(f"IC Mean: {result.get('ic_mean')}")
print(f"Rank IC: {result.get('rank_ic')}")
```

---

## 四、RD-Agent 因子挖掘

### 4.1 运行因子挖掘

```
【因子挖掘】启动 RD-Agent 自动化因子挖掘：

1. 使用 DeepSeek-V4 作为 LLM 后端
2. 每轮生成 20 个因子假设，最多 5 轮迭代
3. 自动编写因子代码 → 回测评估 → 迭代优化
4. PIT 验证：拦截所有未来函数（向前索引、负向 shift 等）
5. IC 筛选：IC 绝对值 < 0.02 的因子淘汰
6. 输出标准化因子列表

使用: python -m src.factors.rd_runner --config config/rd_agent.yaml --output factors.json
```

### 4.2 因子挖掘（指定标的）

```
【因子挖掘】在指定标的上挖掘因子：

1. 标的: sh600519, sz000001, sh688981
2. 时间区间: 2020-01-01 ~ 2026-08-07
3. 使用 DeepSeek-V4 生成因子假设
4. PIT 验证 + IC 筛选

Python API:
from src.factors import RDAgentRunner
runner = RDAgentRunner("config/rd_agent.yaml")
result = runner.run_mining(
    instruments=["sh600519", "sz000001", "sh688981"],
    start_time="2020-01-01",
    end_time="2026-08-07",
)
print(f"生成: {result['summary']['total_generated']}, PIT通过: {result['summary']['pit_passed']}")
```

### 4.3 因子库管理

```
【因子库】管理因子生命周期：

1. 查看活跃因子: store.get_active_factors(min_ic=0.02)
2. 淘汰低 IC 因子: store.check_retirement(ic_threshold=0.01, consecutive_months=3)
3. 导出因子摘要: store.export_summary()
4. 获取 Top 10 因子: store.get_top_factors(10)

Python API:
from src.factors import FactorStore
store = FactorStore("data/output/factors/factor_store.json")
active = store.get_active_factors(min_ic=0.02)
print(f"活跃因子: {len(active)}, 总因子: {store.get_factor_count()}")
```

---

## 五、回测验证

### 5.1 单标的全回测

```
【回测】对 002371 北方华创做 breakout_60 策略回测：

1. 初始化 Qlib 环境（自动读取 config/data.yaml）
2. 加载 breakout_60 策略信号
3. 执行 Qlib 回测（含交易成本模拟）
4. 提取: 组合指标、交易记录、权益曲线
5. 输出判決: Deploy / Refine / Abandon

Python API:
from src.backtest.adapter import init_qlib, run_backtest
from src.backtest.signal_generator import generate_breakout_60

init_qlib()
signal = generate_breakout_60("002371", "2020-01-01", "2026-08-07")
result = run_backtest("002371", "breakout_60", signal, "2020-01-01", "2026-08-07")
print(result["summary"]["verdict"], result["summary"]["win_rate"])
```

### 5.2 Gate 1 评分

```
【回测评分】对回测结果做 5 维 Gate 1 评分：

1. 样本量 (20%): 交易笔数是否充足
2. 期望值 (25%): Sharpe + 胜率
3. 风控 (25%): 最大回撤 + 连续亏损
4. 稳健性 (20%): 样本外验证 + 滚动窗口
5. 执行可行性 (10%): 换手率 + 滑点估计

反过拟合三层防线:
- 样本外留出: 验证集 < 开发集 - 15 分 → 过拟合
- 复杂度惩罚: 参数越多扣分越重, 7+ 参数强制降级
- 滚动窗口: ≥3 年数据按时间滚动验证

Python API:
from src.backtest.gate_scorer import score_gate1
score = score_gate1(result, param_count=3)
print(score["verdict"], score["total_score"])
```

### 5.3 跨标的批量回测

```
【批量回测】对 5 个标的同时做 breakout_60 回测：

标的: 002371, 688981, 600519, 300750, 000858
策略: breakout_60
区间: 2020-01-01 ~ 2026-08-07
输出: 每个标的的判決 + 对比表

Python API:
symbols = ["002371", "688981", "600519", "300750", "000858"]
for sym in symbols:
    signal = generate_breakout_60(sym, "2020-01-01", "2026-08-07")
    result = run_backtest(sym, "breakout_60", signal, "2020-01-01", "2026-08-07")
    print(f"{sym}: {result['summary']['verdict']} (胜率 {result['summary']['win_rate']}%)")
```

---

## 六、PIT 泄漏检测

### 6.1 检测策略代码

```
【泄漏检测】检查策略代码是否存在未来函数：

检测 6 种模式:
1. 向前索引: close.iloc[i + 3]
2. 负向 shift: close.shift(-1)
3. 前瞻切片: close.iloc[i+1:i+4]
4. 滚动前移: rolling().mean().shift(-1)
5. 前瞻变量: look_ahead = i + 3
6. 全区间统计: close.max() 未限制范围

双层防线: 正则表达式 + AST 静态分析

Python API:
from src.data import check_factor_code

code = """
close = data["close"]
for i in range(30, len(data)):
    if close.iloc[i + 3] > 60:
        signal[i] = 1
"""
result = check_factor_code(code, "my_strategy")
print(f"通过: {result['passed']}")
print(f"AST critical: {result['ast']['critical_count']}")
print(f"Regex critical: {result['regex']['critical_count']}")
```

### 6.2 批量检查因子库

```
【泄漏检测】对因子库中所有因子做 PIT 检查：

1. 遍历所有活跃因子
2. 正则 + AST 双层检测
3. 标记有未来函数的因子
4. 输出检测报告

Python API:
from src.data import batch_check_factors, generate_ast_report

results = batch_check_factors(factors_list)
report = generate_ast_report(results)
print(report)
```

### 6.3 验证策略生成器

```
【泄漏检测】验证所有 6 个内置策略信号生成器：

策略: breakout_60, pullback_low, vcp_breakout, oversold_bounce, golden_cross, limit_up_chase
验证: 每个策略的源代码是否通过 PIT 检查

Python API:
from src.data import detect_pit_violation
from src.backtest.signal_generator import (
    generate_breakout_60, generate_pullback_low, generate_vcp_breakout,
    generate_oversold_bounce, generate_golden_cross, generate_limit_up_chase,
)
import inspect

for name, fn in [("breakout_60", generate_breakout_60), ...]:
    source = inspect.getsource(fn)
    violations = detect_pit_violation(source, name)
    critical = [v for v in violations if v["severity"] == "critical"]
    print(f"{name}: {'PASS' if not critical else 'FAIL'} ({len(critical)} critical)")
```

---

## 七、信号导出

### 7.1 导出每日信号

```
【信号导出】将今日 Alpha 信号导出为 AgentAlpha 可读的 JSON 契约：

1. 读取 data/output/alpha_signals.json
2. 按 AgentAlpha 契约格式转换
3. 输出到 data/output/signals/2026-08-07.json

Python API:
from src.export.export_signals import export_daily_signals
result = export_daily_signals(date="2026-08-07", output_dir="data/output/signals")
```

### 7.2 查看信号契约

```
【信号契约】查看 AgentAlpha 集成的 JSON 格式：

契约格式:
{
  "date": "2026-08-07",
  "generated_at": "2026-08-07T16:30:00",
  "model": "ensemble_gbdt+xgboost",
  "signals": [
    {"symbol": "sh600519", "alpha_score": 0.0823, "rank": 1, "action": "BUY"},
    ...
  ],
  "metadata": {"total_instruments": 500, "pit_verified": true}
}
```

---

## 八、配置管理

### 8.1 查看当前配置

```
【配置】查看信号工厂当前配置：

1. Qlib 数据路径
2. Wind 数据路径
3. PIT 发布延迟设置
4. 数据同步参数

Python API:
from src.data import get_config, get_qlib_csv_dir, get_qlib_bin_dir, get_wind_json_dir, get_pit_config

print(f"Qlib CSV: {get_qlib_csv_dir()}")
print(f"Qlib Bin: {get_qlib_bin_dir()}")
print(f"Wind:     {get_wind_json_dir()}")
print(f"PIT:      {get_pit_config()}")
```

### 8.2 环境变量覆盖

```
【配置】通过环境变量覆盖默认路径：

# 临时切换 Qlib 数据目录
export QLIB_DATA_DIR=/mnt/data/qlib_bin

# 临时切换 Wind 数据目录
export WIND_JSON_DIR=/mnt/data/wind_export

# 验证
python -c "from src.data import get_qlib_bin_dir, get_wind_json_dir; print(get_qlib_bin_dir())"
```

### 8.3 重新加载配置

```
【配置】修改 config/data.yaml 后重新加载：

Python API:
from src.data import reload_config, get_qlib_bin_dir
reload_config()
print(get_qlib_bin_dir())  # 已使用新配置
```

---

## 九、完整日终管线

### 9.1 一键运行（推荐）

```
【日终管线】运行完整的信号工厂日终流程：

1. 数据同步: Wind MCP → Qlib CSV → Qlib bin
2. 模型训练: 检查是否需要重训（每周六重训 GBDT）
3. Alpha 预测: GBDT + XGBoost 集成 → 全市场排名 Top 50
4. 信号导出: 转换为 AgentAlpha JSON 契约
5. 输出: data/output/alpha_signals.json + data/output/signals/{date}.json

使用:
python scripts/sync_data.py
python -m src.factors.alpha_signals --ensemble --models gbdt xgboost --no-train
python -c "from src.export.export_signals import export_daily_signals; export_daily_signals()"
```

### 9.2 周度重训管线

```
【周度重训】周六凌晨运行完整重训：

1. 数据同步（增量）
2. 重训 GBDT 模型（force_retrain=True）
3. 重训 XGBoost 模型
4. 集成预测
5. 因子库淘汰检查（IC 连续 3 个月 < 0.01 的因子淘汰）
6. 信号导出

使用:
python scripts/sync_data.py
python -m src.factors.alpha_signals --model gbdt
python -m src.factors.alpha_signals --model xgboost
python -c "
from src.factors import FactorStore
store = FactorStore()
retired = store.check_retirement(ic_threshold=0.01, consecutive_months=3)
print(f'淘汰因子: {len(retired)}')
"
python -m src.factors.alpha_signals --ensemble --models gbdt xgboost
```

### 9.3 月度重训管线（含 GPU 模型）

```
【月度重训】每月第一个周日运行，包含 GPU 模型：

1. 数据同步
2. 重训 GBDT + XGBoost + GBDT_large
3. 重训 LSTM（GPU）→ 时序动量
4. 重训 TabNet（GPU）→ 特征交互
5. 四模型集成预测
6. RD-Agent 因子挖掘（可选）
7. 因子库淘汰 + 信号导出

使用:
python -m src.factors.alpha_signals --model gbdt
python -m src.factors.alpha_signals --model xgboost
python -m src.factors.alpha_signals --model gbdt_large
python -m src.factors.alpha_signals --model lstm
python -m src.factors.alpha_signals --model tabnet
python -m src.factors.alpha_signals --ensemble --models gbdt xgboost gbdt_large
```

---

## 十、与 AgentAlpha 集成

### 10.1 在管线中调用 ML 信号

```
【Stage 1 — ML 交叉验证】获取 Qlib Alpha 信号并与 Agent 选股合并：

1. 读取 data/output/alpha_signals.json 获取 ML Top 50
2. 获取 Stage 1 Agent 选股结果（5 个 skill 的候选列表 A）
3. 合并策略:
   A ∩ B → 高置信度（优先进入 Gate 1）
   A \ B → 中置信度（Agent 独有，需额外论证）
   B \ A → 低置信度（ML 独有，Agent 复核）
4. 输出 ML 交叉验证标记的候选列表

详见: qlib_alpha_signal_skill
```

### 10.2 信号工厂不可用时的降级

```
【降级】信号工厂未运行时的处置：

1. 检查 data/output/alpha_signals.json 是否存在
2. 如果不存在:
   - 标记 ML 信号为 DEGRADED
   - 仅使用 Agent 定性选股（5 个 skill）
   - 所有候选标记为"中置信度 A\B"
   - 不阻塞管线继续运行
3. 如果文件存在但日期不是今天:
   - 使用最近可用的信号（标记日期）
   - 降低置信度权重
```

### 10.3 手动触发信号生成

```
【手动触发】在 AgentAlpha 中手动触发信号工厂：

1. 切换到信号工厂目录
2. 运行集成模式
3. 读取输出

cd D:/AgentAlpha/signal-factory
python -m src.factors.alpha_signals --ensemble --models gbdt xgboost
cat data/output/alpha_signals.json | python -m json.tool | head -30
```

---

## 十一、故障排查

### 11.1 数据不可用

```
【故障排查】Qlib bin 数据目录为空：

1. 检查 config/data.yaml 中 bin_dir 路径是否正确
2. 运行 python scripts/sync_data.py --summary 查看 Wind 数据状态
3. 如果 Wind JSON 目录为空 → 先运行 AgentAlpha 的 Wind MCP 数据采集
4. 如果有 JSON 但 bin 为空 → 运行 python scripts/sync_data.py --force
```

### 11.2 模型未训练

```
【故障排查】Alpha 信号生成报错"模型尚未训练"：

1. 检查 data/output/models/ 目录是否有 .pkl 文件
2. 如果没有 → 运行 python -m src.factors.alpha_signals --model gbdt
3. 训练完成后，后续可以用 --no-train 跳过训练
```

### 11.3 PIT 泄漏检测

```
【故障排查】因子被 PIT 检测拦截：

1. 查看违规详情: result["ast"]["violations"]
2. 常见原因:
   - close.iloc[i + 3] → 改为 close.iloc[i - 3]
   - close.shift(-1) → 改为 close.shift(1)
   - 全区间 .max() → 改为 .iloc[:i].max()
3. 修复后重新检测: check_factor_code(fixed_code, "strategy_name")
```

### 11.4 运行测试

```
【测试】验证信号工厂是否正常工作：

1. 运行全部测试: python -m pytest tests/ -v
2. 仅泄漏检测: python -m pytest tests/test_leakage_checker.py -v
3. 检查导入: python -c "from src.factors import *; print('OK')"
```

---

## 十二、Prompt 设计原则

| 原则 | 说明 |
|---|---|
| **明确任务** | 指定是"数据同步"、"Alpha 生成"、"回测"还是"因子挖掘" |
| **指定标的** | 列出具体 ticker 或范围（csi500 / 全市场） |
| **指定模型** | 明确用哪个模型：gbdt / xgboost / ensemble / tabnet |
| **指定日期** | 预测日期或回测区间，不指定则默认今天 |
| **增量优先** | 数据同步默认增量，避免不必要的全量覆盖 |
| **PIT 先行** | 任何新因子代码必须先通过 PIT 检测，再进入回测 |
| **降级设计** | 信号工厂不可用时 AgentAlpha 自动降级，不阻塞管线 |
| **配置驱动** | 路径从 config/data.yaml 读取，支持环境变量覆盖 |

---

> 版本：v1.0 | 最后更新：2026-08-07