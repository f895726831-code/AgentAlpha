# 回测报告 — {{STRATEGY_NAME}}

> 回测日期：{{DATE}} | 代理：backtester | 评分引擎：evaluate_backtest.py
> 策略假设：{{HYPOTHESIS}}

---

## 一、策略规则

| 参数 | 值 |
|---|---|
| 标的 | {{SYMBOL}} |
| 入场规则 | {{ENTRY_RULE}} |
| 止损 | {{STOP_LOSS}} |
| 止盈 | {{TAKE_PROFIT}} |
| 仓位 | {{POSITION_SIZE}} |
| 回测区间 | {{START_DATE}} ~ {{END_DATE}} |

---

## 二、回测结果

| 指标 | 数值 |
|---|---|
| 总交易次数 | {{TOTAL_TRADES}} |
| 胜率 | {{WIN_RATE}}% |
| 平均盈利 | +{{AVG_WIN}}% |
| 平均亏损 | -{{AVG_LOSS}}% |
| 盈亏比 | {{PROFIT_FACTOR}} |
| 最大回撤 | -{{MAX_DRAWDOWN}}% |
| 年化收益 | {{ANNUAL_RETURN}}% |
| 期望值 | {{EXPECTANCY}}%/笔 |

### 逐年表现

| 年份 | 交易次数 | 胜率 | 盈亏 |
|---|---|---|---|
{{YEARLY_TABLE}}

### 参数敏感性

| 参数 | 测试范围 | 稳定区间 | 判断 |
|---|---|---|---|
| 止损% | {{STOP_RANGE}} | {{STOP_STABLE}} | {{STOP_JUDGMENT}} |
| 止盈% | {{TP_RANGE}} | {{TP_STABLE}} | {{TP_JUDGMENT}} |

---

## 三、5 维评分

| 维度 | 得分 | 满分 | 说明 |
|---|---|---|---|
| 样本量 | {{D1}} | 20 | {{D1_NOTE}} |
| 期望值 | {{D2}} | 20 | {{D2_NOTE}} |
| 风险管理 | {{D3}} | 20 | {{D3_NOTE}} |
| 稳健性 | {{D4}} | 20 | {{D4_NOTE}} |
| 执行真实性 | {{D5}} | 20 | {{D5_NOTE}} |
| **总分** | **{{TOTAL}}** | **100** | |

---

## 四、红旗检测

{{RED_FLAGS}}

---

## 五、判決

> **{{VERDICT}}**（{{TOTAL}}/100）

{{VERDICT_DETAIL}}

---

## 六、人工判断

| 维度 | 评估 |
|---|---|
| 当前市场环境是否匹配回测 regime？ | {{REGIME_MATCH}} |
| 策略逻辑是否有基本面支撑？ | {{FUNDAMENTAL}} |
| 是否有未覆盖的尾部风险？ | {{TAIL_RISK}} |
| **最终决策** | {{FINAL_DECISION}} |

---

> 回测引擎：`.agents/skills/backtest-expert/scripts/evaluate_backtest.py`
> 方法论：`.agents/skills/backtest-expert/references/methodology.md`
> ⚠️ 回测通过 ≠ 实盘盈利。历史表现不代表未来收益。