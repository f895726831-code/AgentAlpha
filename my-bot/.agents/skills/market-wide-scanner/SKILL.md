---
name: market-wide-scanner
description: 全市场初筛 — 利用 Wind search_stocks 从全市场海选标的，通过条件筛选→快速过滤→五维快扫评分，输出 Top 20 候选进入管线深度分析。Pipeline v5.0 Stage 0 核心技能。
---

# 全市场初筛 (Market-Wide Scanner)

Pipeline v5.0 Stage 0 — 在现有选股链之前，从全市场海选标的，弥补自选池的局限。

## 核心理念

**"好机会不一定在你的自选池里。"**

- 自选池是静态的，全市场是动态的
- 主动发现 > 被动等待
- 批量初筛 + 管线精选 = 从 5000+ 只 → 1-3 个可执行计划

## 触发条件

| 触发方式 | 说明 |
|---|---|
| 主动触发 | 用户明确要求"全市场选股"或指定筛选条件 |
| 盘后定时 | 盘后闭环中，在 daily_watchlist_scanner 之前运行 |
| 周度扫描 | 每周一次全市场扫描，更新候选池 |

## 工作流

### Step 1: 条件筛选

调用 Wind `stock_data.search_stocks`，用自然语言描述筛选条件。

**使用预置模板（推荐）：**

```bash
node scripts/cli.mjs call stock_data search_stocks '{"question":"<模板对应的筛选条件>"}'
```

模板定义见 `references/templates.md`。

**使用自定义条件：**

用户直接描述筛选条件，原样传入 `question` 字段。

### Step 2: 快速过滤

对 `search_stocks` 返回的代码列表做硬过滤：

| 过滤项 | 规则 |
|---|---|
| 上市板 | 保留主板(600/000/001/002/003)、创业板(300/301)、科创板(688)，剔除北证(8/4/9) |
| ST/*ST | 剔除名称含 ST 的标的 |
| 市值 | 剔除 < 30 亿（主板/创业板）/ < 20 亿（科创板） |
| 流动性 | 剔除近 20 日日均成交额 < 5000 万 |
| 次新股 | 剔除上市不足 60 个交易日的标的 |

### Step 3: 批量快扫评分

对过滤后的候选池（通常 30-80 只），批量拉取日线数据，做 5 维快扫评分：

| 维度 | 权重 | 评分逻辑 | 数据来源 |
|---|---|---|---|
| 涨跌幅 | 20% | 相对板块均值的偏离度，跑赢板块满分 | `get_stock_price_indicators` |
| 量比 | 20% | 当日量 / 20日均量，1.2-2.0x 区间满分 | `get_stock_kline` |
| 趋势位置 | 25% | 价格 vs MA20/MA60，站上双均线 5 分 | `get_stock_kline` |
| 距20日高点 | 20% | 0.3-0.7 区间最优（回调充分但未破位） | `get_stock_kline` |
| 波动率 | 15% | 20日振幅，2-5% 最优（太大风险，太小没弹性） | `get_stock_kline` |

综合得分 = Σ(维度得分 × 权重)，排序后取 **Top 20** 进入 Stage 1。

### Step 4: 管线深度分析

Top 20 候选交给现有管线：
- Stage 1: `breakout_candidate_finder` / `pullback_opportunity_finder` 等形态识别
- Gate 1: `backtester` 回测验证
- Stage 2: `gap_open_interpreter` / `volume_spike_reasoning` 信号确认
- Gate 2: `bull_bear_debate_gate` 多空论战
- Stage 3: `trade_plan_builder` / `position_sizing_decision` 交易计划
- Stage 4: `stop_loss_discipline` / `take_profit_ladder` 风控

最终输出 1-3 个可执行交易计划。

## 模式感知

| 模式 | 行为 |
|---|---|
| `normal` | 完整 Stage 0→4，输出 Top 5-10 候选，最终 1-3 个交易计划 |
| `earnings_season` | 增加 T5 财报驱动模板，筛选条件融入财报因子 |
| `risk_off` | Stage 0 降级为仅观察，不生成交易计划，输出"防守观察清单" |

## 输出格式

```markdown
## Stage 0 — 全市场初筛

### 筛选条件
- 模板: T2 回调低吸
- 市场: A股
- 初筛结果: 87 只 → 过滤后 52 只 → Top 20

### Top 20 快扫评分

| 排名 | 代码 | 名称 | 板块 | 涨跌(20) | 量比(20) | 趋势(25) | 高位(20) | 波动(15) | 总分 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 688690 | 纳微科技 | 生物科技 | 3.5 | 3.8 | 4.0 | 4.2 | 3.0 | 3.79 |
| 2 | ... | ... | ... | ... | ... | ... | ... | ... | ... |

### 进入管线分析
Top 20 候选已提交至 signal-gen，等待 Stage 1 形态识别。
```

## 与管线其他技能的关系

- **下游**: `market-watcher` — 接收初筛结果，判档后传递给 signal-gen
- **下游**: `signal-gen` — 对 Top 20 做形态识别
- **同级**: `daily_watchlist_scanner` — 盘后扫描自选池，market-wide-scanner 扫描全市场，两者互补
- **依赖**: `stock_data.search_stocks` — Wind 全市场筛选工具

## 参考

- `references/templates.md` — 6 个预置筛选模板的参数定义和示例