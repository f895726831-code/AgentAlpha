# my-bot

My AI agent harness — 趋势交易量化决策系统

> **Pipeline v4.3** — 三模式自适应管线 + 六阶段三关口 + 量化信号工厂集成 | 19 核心技能 | Wind MCP + Qlib 双引擎
> Generated with [`create-agent-harness`](https://github.com/ruvnet/agent-harness-generator) — `vertical:trading` template. **Paper mode by default.**

## 管线架构

```
Stage 0         Stage 1        [Gate 1]     Stage 2       [Gate 2]      Stage 3        Stage 4
全市场初筛  →  环境+选股  →   回测关   →  信号确认  →  多空论战关  →  交易执行  →  风控退出
(1 skill)      (7 skills)     (1 agent)    (2 skills)    (1 skill)     (4 skills)     (4 skills)

market_wide    market_regime   backtester   gap_open      bull_bear     dip_buy        stop_loss
scanner        breakout_                     interpreter   _debate       decision       discipline
(v4.3新增)     candidate_                   volume_        _gate         trade_plan     take_profit
               finder                       spike_         (v4.3新增)    builder        _ladder
               pullback_                    reasoning                    breakout_      failed_
               opportunity_                                              trade_         breakout_
               finder                                                    execution      exit
               vcp_breakout_                                            position_      support_
               scan                                                     sizing_        break_
               canslim_                                                 decision       warning
               growth_scan
               a_share_primary
               _theme
               qlib_alpha
               _signal
```

### 三道关卡

| 关卡 | 版本 | 位置 | 职能 |
|------|------|------|------|
| **Gate 1 回测关** | v4.1 | Stage 1→2 | 五维评分 + 三层反过拟合（样本外留出/复杂度惩罚/滚动窗口），必须 ≥70 分 Deploy 才能进入选股 |
| **Gate 2 多空论战关** | v4.3 | Stage 2→3 | 六维加权评分（技术30%/资金流15%/拥挤10%/基本面20%/情绪15%/宏观10%），绿灯/黄灯/橙灯/红灯四级决策 |
| **Gate 3 风控关** | v4.0 | Stage 4 | 仓位/集中度/熔断校验，参数按模式动态切换 |

### 三模式自适应

| 优先级 | 模式 | 触发条件 | 行为 |
|--------|------|----------|------|
| 1 (最高) | `risk_off` | market_regime 判为"防守" | 选股降级为仅观察，不建仓，风控收紧。退出需分阶段（risk_off→震荡→进攻） |
| 2 | `earnings_season` | 月份 ∈ {1,2,3,4,7,8,10} | 激活财报子管线，盘前/盘后闭环增强 |
| 3 (默认) | `normal` | 以上条件均不满足 | 标准六阶段管线 |

### 量化信号工厂集成

通过 `scripts/sync_signal_factory.py` 与 `D:/AgentAlpha/signal-factory` 松耦合集成：
- **模型**: CatBoost（CSI 300 成分股，Alpha158 特征，2015-2026 训练）
- **覆盖**: 77,994 只标的，12 个 RD-Agent 生成因子
- **集成方式**: ML Top 50 ∩ Agent 选股 = 高置信优先；纯 ML / 纯 Agent = 需额外论证

## Install

```bash
npm install -g my-bot
my-bot init
my-bot backtest --strategy default --period 2025-01-01:2025-12-31
```

## License

MIT — but **trading carries risk**. Past backtest performance does not predict future results. You're responsible for any losses.