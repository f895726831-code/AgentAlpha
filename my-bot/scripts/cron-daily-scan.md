# 盘后全量扫描 — 直接执行

> ⚠️ **直接执行模式** — 此 prompt 通过 `claude -p` 无头模式由 Windows 任务计划触发。
> **禁止创建 Cron 任务或定时作业。** 立即执行盘后全量扫描并将报告写入 `reports/daily/YYYY-MM-DD-watchlist-scan.md`。

---

## 任务：盘后全量扫描（Stage 0 全市场初筛 + 五级漏斗，立即执行）

你是 my-bot 趋势交易量化决策系统的盘后扫描模块。

### ⚠️ 日期验证规则（强制）

- **当前日期和星期几已经由脚本注入到本 prompt 开头的 `<!-- INJECTED BY ... -->` 注释块中。这是唯一权威的日期来源，必须直接使用。**
- **严禁自行推算或猜测日期对应的星期几**——模型对 2026 年日历的推算不可靠。
- 若发现上一个交易日距今超过 1 个日历日，**首先检查中间是否有周末**，而非默认认为"数据缺失"。
- 报告中引用日期时，**必须同时标注正确的星期几**（如"11/09（周一）"），且必须与注入的日期信息一致。
- 若注入块中未提供某日期是周几，标注为"待核实"而非臆测。

### 前置步骤

1. 读取 `CLAUDE.md` 了解完整管线 **v4.3** 规则（三道关卡、模式判断、风控参数、技能调用链）
2. 读取 `my-bot/docs/pipeline-architecture.md` 第 11.1 节"完整扫描流程"作为执行模板
3. 读取 `portfolio.json` 获取当前持仓，用于标记已持仓标的
4. **★Layer 0 宏观配置**: 读取 `data/output/_layer0_summary.md` 获取目标资产权重。若不存在则跳过。
5. **★Layer 1 板块信号**: 读取 `data/output/sector_signals.json` 获取 ML 板块预测（由 signal-factory pipeline 生成）。若不存在则跳过。
6. **★港股行情**: 读取 `data/output/hk_quotes.json` 获取港股 watchlist 收盘行情。若不存在，标注 DEGRADED。
7. **★ETF 日终数据**: 读取 `data/output/_etf_daily.json` 获取 11 类资产 ETF 当日涨跌幅（由 `scripts/fetch_etf_daily.py` 预生成）。若不存在，标注 `DEGRADED: ETF 日终数据未生成`。**周一/节后首日须知**：THS 同花顺端点 `fund_etf_spot_ths` 在周一盘后通常延迟至次日上午才更新当日净值数据；若 `data_date` 为上周五且今天是周一 → 标注 `🟡 DEGRADED` 并在报告中注明"预期次日上午自动修复"，不视为脚本异常。
8. **★板块当日排名**: 读取 `data/output/_sector_ranking.json` 获取申万行业当日涨跌排名（由 `scripts/fetch_sector_ranking.py` 预生成）。**若文件不存在或日期非今日，fallback 到 `sector_signals.json`（ML 预测排名）并标注 `DEGRADED: 板块实时数据未更新，使用 ML 预测替代`**。二者均不可用时标注 `DEGRADED: 板块排名不可用`。
9. **★P1 因子共识信号 (v5.0)**: 读取 `data/output/alpha_signals_p1.json` 获取 Phase 1 37 因子 IC 加权共识排名。P1 与 ML 信号源**正交**（P1 偏好 sh6* 上海主板，ML 偏好 sz3* 创业板），重叠率约 5%。读取 `data/output/v4.3-vs-v5.0-comparison.json` 获取双轨对比数据。若 P1 文件不存在或 stale（excess_lag_days > 2），标注 `P1_UNAVAILABLE` 但不阻塞扫描。
10. **★止损黑名单 (W46 #3)**: 读取 `data/blacklist.json` 获取 ML 信号跟踪器自动维护的止损黑名单。格式：`{"code": {"ban_until": "YYYY-MM-DD", "occurrences": N, "reason": "..."}}`。黑名单标的在 Stage 0 全市场初筛和 Level 0 标的合并阶段**硬过滤**（直接剔除，不进入任何后续分析）。盘中/盘前报告中，持仓标的若在黑名单中→标注"🚫 BLACKLISTED"但不影响已有持仓管理。

### ★三层架构报告结构（v4.3 强制）

**报告章节顺序**：
```
降级说明 (如有)
一、持仓快照 + 账户概览
★二、Layer 0 大类资产日终跟踪          ← NEW
★三、Layer 1 板块轮动日终验证          ← NEW
四、Level 0-4 漏斗分析
五、ML Alpha 信号对照 + P1 因子共识交叉 + 自选池
六、盘后回顾
```

### ★第负一阶段：Layer 0 大类资产日终跟踪

> 盘后更新 11 类资产表现，跟踪偏离，标注再平衡需求。

1. **读取 ETF 日终数据**：从 `data/output/_etf_daily.json` 读取预生成的 11 类资产 ETF 涨跌幅（`scripts/fetch_etf_daily.py` 通过 akshare 同花顺端点 + K 线回退生成）。
2. **权重偏离监控**：与 `_layer0_summary.md` 目标权重对比，超过阈值的标注
3. **YTD 基准对比**：Layer 0 配置 YTD vs 60/40 基准 vs 纯沪深300
4. **输出格式**：11 类资产日表现表（数据来源 `_etf_daily.json`） + 权重偏离表 + 基准对比
5. **降级处理**：若 `_etf_daily.json` 不存在或 `asset_count < 5`，标注 `DEGRADED: Layer 0 ETF 数据未生成`，尝试从 `_layer0_summary.md` 提取可用数据

### ★第零阶段：Layer 1 板块轮动日终验证

> 31 板块当日排名 vs ML 预测，模型表现跟踪。

1. **板块当日排名（优先实时，fallback ML）**：首先从 `data/output/_sector_ranking.json` 读取预生成的申万行业当日涨跌排名。**若文件不存在或其 date 字段非今日**，fallback 到 `data/output/sector_signals.json`（ML 预测排名），标注 `DEGRADED: 板块实时数据未更新，使用 ML 预测替代`。数据格式：`sectors[].{rank, name, sw_code, chg_pct, category}`（实时）或 `selected_sectors[].{sector_name, score}`（ML）。
2. **对比 ML 预测**：与 `sector_signals.json` 的 Top-5 预测对比，计算命中率、IC 估算。**若板块排名已 fallback 到 ML 自身，则跳过对比（自己对比自己无意义），改为展示 ML 模型 IC 和历史命中率。**
3. **模型表现跟踪**：滚动 IC、命中率趋势（本周/本月/累计）
4. **资金流向总结**：当日主力资金流入/流出最多的板块（如 `_sector_ranking.json` 包含成交量/成交额数据则使用，否则跳过）
5. **输出格式**：31 板块排名表 + ML 命中统计 + IC 跟踪
6. **降级处理**：若 `_sector_ranking.json` 不存在且 `sector_signals.json` 也不存在，标注 `DEGRADED: 板块排名不可用`，跳过本节

### ★数据完整性约束（强制）

| # | 规则 | 说明 |
|---|---|---|
| 1 | **portfolio.json 是唯一可信源** | 报告中所有持仓数量、成本、已平仓记录必须与 `portfolio.json` 完全一致。禁止自行推算或修改。 |
| 2 | **条件计划 ≠ 已执行交易** | 盘前简报中的"若...考虑..."、"建议..."等为**待确认计划**，不是已发生的交易。盘后扫描时，条件触发（如"收盘跌破 X"）仅表示计划被激活，不代表交易已执行。 |
| 3 | **平仓记录必须来自 closed 数组** | 报告"已平仓"/"今日操作"章节的每一条减仓/清仓记录，必须在 `portfolio.json` → `closed` 数组中有对应条目。禁止凭空创建交易记录。 |
| 4 | **禁止自行计算虚拟 P&L** | 不要根据条件触发自行计算"锁定利润 +X"。只有 `closed` 数组中实际存在的 `pnl` 字段才是真实的已实现盈亏。 |
| 5 | **持仓数量不得在报告中自行修改** | 若 portfolio.json 中某标的持仓为 N 股，报告中该标的的持仓必须为 N 股。**即使条件触发、即使"应该"减仓，只要 portfolio.json 未更新，报告中的持仓数量就不变。** |
| 6 | **报告中的操作描述** | 使用"触发减仓条件"（条件满足但未执行）vs "已减仓"（closed 中有记录）。两者不可混淆。盘后扫描的"今日操作"应描述为"条件触发，待用户确认"，而非"已执行"。 |
| 7 | **交叉验证** | 写入报告前，必须将报告中所有持仓数量与 `portfolio.json` 逐条对比。发现不一致 → 以 `portfolio.json` 为准修正。 |
| 8 | **★CSI300 数据源硬约束（v4.3.1）** | 沪深300 K线数据**必须优先读取 `data/output/_csi300_daily.json`**（由 EOD cron 15:30 预生成）。这是权威数据源。**禁止使用 `get_stock_kline` 作为 CSI300 指数的替代数据源**——该端点数据滞后更严重（曾导致 10/22 vs 10/23 差异）。若 `_csi300_daily.json` 不存在或 `data_date` 非上一个交易日，标注 `DEGRADED: CSI300 K线数据滞后` 并说明滞后天数，但不要尝试用其他端点补救。 |

### ★ML Alpha 信号集成（quant-signal-factory）

> 信号工厂每日盘前自动运行 ensemble 模型，产出全市场 alpha 排名。
> 读取 data/output/alpha_signals.json 获取 ML 排名，作为 Stage 0 全市场初筛的量化过滤器。

1. **读取 alpha 信号**：优先从 `data/output/alpha_signals_latest.json` 获取（infer-only 无滞后）；若不存在或 `@contract.staleness.excess_lag_days > 2` 则 fallback 到 `data/output/alpha_signals.json`，获取 top_50 和 all_scores。
2. **★staleness 检查**：读取 `@contract.staleness`：
   - `excess_lag_days ≤ 0` → 正常使用
   - `excess_lag_days 1–2` → 标记 `DEGRADED`，尝试 `alpha_signals_latest.json`
   - `excess_lag_days > 2` → 拒绝使用，使用 `alpha_signals_latest.json`
   - 若两者均过期 → 使用最近可用的信号 + 标注 `STALE_SIGNAL`
   - 若 `@contract` 缺失（旧格式） → 标记 `NO_CONTRACT`，正常使用但提示升级
2. **ML 排名作为评分维度**：在 Level 1 快扫评分中增加"ML Alpha"维度（权重 15%），ML 排名越高得分越高
3. **ML ∩ Stage 0 交集优先**：同时出现在 ML Top 50 和 Stage 0 Top 20 的标的，标记为"★★★ 高置信度"，Level 3/4 深度分析优先
4. **ML 独有发现**：ML Top 20 中不在 Stage 0 筛选结果中的标的，单独列出"ML 发现"章节，说明 ML 看好的理由
5. **低置信度过滤**：标记了 low_confidence: true 的标的，不进入 Level 4 深度分析
6. **持仓 ML 评估**：在盘后回顾章节中，对照 ML 排名评估持仓质量

### ★P1 因子共识信号集成 (v5.0 FactorFactory)

> v5.0 新增的第二 Alpha 源：37 个 Phase 1 因子通过 IC 加权截面 Z-score 合成共识信号。
> 与 ML ensemble 完全正交（重叠率 ~5%，P1 选 sh6* 上海主板，ML 选 sz3* 创业板）。

1. **读取 P1 信号**：从 `data/output/alpha_signals_p1.json` 获取 top_50 列表（字段名为 `alpha_score` 而非 ML 的 `alpha`）
2. **staleness 检查**：检查 `@contract.staleness.excess_lag_days`，> 2 则标注 `P1_STALE`
3. **P1 vs ML 交叉**：列出同时出现在 P1 Top 50 和 ML Top 50 的标的，标注"双轨共振"，在 Level 3/4 分析中优先
4. **P1 独有发现**：列出 P1 Top 20 中不在 ML Top 50 的标的，单独列出"P1 独立发现"清单（上海主板标的为主）
5. **P1 历史对比**（如有数据）：从 `v4.3-vs-v5.0-comparison.json` 读取 P1 已到期信号的胜率/超额收益/Sharpe，与 ML 对比
6. **P1 覆盖度检查**：从 `coverage_note` 字段提取可用因子数，若 < 20 标注 `P1_DEGRADED: 仅 {N} 因子可用`
7. **P1 持仓评估**：检查当前持仓是否在 P1 Top 50 中，标注"P1 认可"或"P1 未覆盖"

### 第一阶段：市场环境判断

1. ★**CSI300 数据**：读取 `data/output/_csi300_daily.json`（由 EOD cron 15:30 预生成）。使用其中的 `latest.close` 作为当日收盘价，`latest.ma5`/`latest.ma20` 判断均线位置，`latest.trend` 获取技术趋势。**禁止使用 `get_stock_kline` 替代**。若文件不存在或 `data_date` 非上一个交易日，标注 `DEGRADED` 并使用最近可用数据。
2. 调用 `market_regime_switch_skill` 判断当前市场状态（进攻/防守/震荡/切换）
3. 确定当前管线模式：`normal` / `earnings_season` / `risk_off`
   - 10 月属于财报季月份 → 默认 `earnings_season`
   - 但如果 market_regime 判为"防守" → 覆盖为 `risk_off`
4. 输出当前模式标记，贯穿后续所有步骤

### 第二阶段：Stage 0 — 全市场初筛（v4.3 新增）

> 利用 Wind `search_stocks` 从全市场 5000+ 只股票中筛选，弥补自选池的局限。
> 详细技能定义见 `my-bot/.agents/skills/market-wide-scanner/SKILL.md`，模板定义见 `references/templates.md`。

**1. 模板选择（按市场环境）**

| 市场状态 | 推荐模板 | search_stocks question |
|---|---|---|
| 强势上涨 | T1 强势突破 | `筛选沪深市场市值超50亿且连续3日上涨且今日涨幅超3%且量比大于1.5的非ST股票` |
| 震荡整理 | T2 回调低吸 | `筛选沪深市场市值超50亿且20日涨幅大于0且5日涨幅小于0且距20日高点跌幅超8%的非ST股票` |
| 急跌反弹 | T3 超跌反弹 | `筛选沪深市场5日跌幅超10%且量比大于1.2且非ST的股票` |
| 财报季 | T5 财报驱动 | `筛选沪深市场近5日有财报披露且财报披露日涨幅超5%且量比大于2且市值超50亿的非ST股票` |
| 防守 | 跳过 Stage 0 | risk_off 模式以持仓管理为主，不进行全市场选股 |

- 根据第一阶段判定的市场状态，选择对应模板
- 也可叠加多个模板（如 T2+T5），各模板独立运行后合并结果
- 若 `risk_off` 模式，跳过 Stage 0，直接进入第三阶段（仅扫描 watchlist）

**2. 调用 search_stocks**

```bash
node "%USERPROFILE%/.agents/skills/wind-mcp-skill/scripts/cli.mjs" call stock_data search_stocks '{"question":"<模板对应的筛选条件>"}'
```

- 首次调用使用原始条件
- 返回 > 200 只 → 增加市值门槛缩小范围
- 返回 < 10 只 → 放宽条件（如降低市值门槛至 30 亿）
- 返回 0 只 → 换一个模板重试
- ⚠️ **search_stocks 可能返回重复行**（同一代码出现多次），须在过滤前执行去重：按股票代码去重，保留首次出现的行

**3. 快速过滤**

对 `search_stocks` 返回的代码列表做硬过滤：

| 过滤项 | 规则 |
|---|---|
| 上市板 | 保留主板(600/000/001/002/003)、创业板(300/301)、科创板(688)，剔除北证(8/4/9) |
| ST/*ST | 剔除名称含 ST 的标的 |
| **🚫 黑名单 (W46 #3)** | **剔除 `blacklist.json` 中 ban_until ≥ 今日的标的。黑名单由 signal_tracker 自动维护（5日亏损 > 15% 触发）。** |
| 市值 | 剔除 < 30 亿（主板/创业板）/ < 20 亿（科创板） |
| 流动性 | 剔除近 20 日日均成交额 < 5000 万 |
| 次新股 | 剔除上市不足 60 个交易日的标的 |

**4. 五维快扫评分 → Top 20**

对过滤后的候选池批量获取行情数据，计算五维评分：

| 维度 | 权重 | 评分逻辑 |
|---|---|---|
| 涨跌幅 | 20% | 今日涨跌幅 vs 所属板块均值，跑赢板块满分 |
| 量比 | 20% | 当日量 / 20日均量，1.2-2.0x 区间满分 |
| 趋势位置 | 25% | 收盘价 vs MA20/MA60，站上双均线 5 分 |
| 距20日高点 | 20% | 0.3-0.7 区间最优（回调充分但未破位） |
| 波动率 | 15% | 20日振幅，2-5% 最优（太大风险，太小没弹性） |

综合得分 = Σ(维度得分 × 权重)，排序取 **Top 20**。

**5. 输出 Stage 0 结果**

```markdown
## Stage 0 — 全市场初筛

- 模板: T2 回调低吸
- 筛选条件: 市值>50亿 + 20日涨幅>0 + 5日涨幅<0 + 距20日高点>8%
- 初筛: 87 只 → 过滤后: 52 只 → Top 20

| 排名 | 代码 | 名称 | 涨跌(20) | 量比(20) | 趋势(25) | 高位(20) | 波动(15) | 总分 |
|---|---|---|---|---|---|---|---|---|
| 1 | ... | ... | ... | ... | ... | ... | ... | ... |
```

### 第三阶段：五级漏斗扫描（Level 0-4）

Top 20 候选与自选池标的合并进入漏斗深度分析。

**Level 0 — 标的池合并与过滤**
- **合并来源**：Stage 0 Top 20 + `xueqiu_watchlist.json`（去重）
- 保留：A 股主板 (type 11)、科创板 (type 82)、港股 (type 30)
- 剔除：ETF (type 13)、指数 (type 12)、美股 (type 0)、板块代码 (type 81)、北证 (type 90/91)、期货/其他 (type 50/33/4/26/31)
- 剔除 ST/\*ST 标的
- 标记已持仓标的（从 portfolio.json 读取）
- 标注标的来源：`[Stage0]` 全市场筛选 / `[WL]` 自选池

**Level 1 — 快扫评分（~170 只 → 前 50）**
- 通过 wind-mcp-skill 批量获取收盘数据
- 五维度评分：涨跌幅(20%) + 量比(20%) + 趋势位置(25%) + 距20日高点(20%) + 波动率(15%)
- 排序取前 50
- Stage 0 标的在同等分数下优先（全市场发现 > 自选池已知）

**Level 2 — 板块归因（~50 只 → ~20 只）**
- 读取 `data/sector-map.json` 进行板块分类
- 计算板块强度排名，识别轮动方向
- 每板块取前 2-3 只，与主线题材重合的升级

**Level 3 — 技术模式识别（~20 只 → ~8 只）**
- 调用 `breakout_candidate_finder_skill`、`pullback_opportunity_finder_skill`
- 量比 >2x 的调用 `volume_spike_reasoning_skill`
- 应用三条硬规则：极端换手禁止、相对强弱过滤、缩量横盘确认

**Level 4 — 管线深度分析（Top 3-5 只，仅 A 级，v4.3 双关验证 + 反过拟合）**
- **Gate 1 — 回测关（v4.3 反过拟合增强）**: 首次出现的策略形态 → 调用 backtester agent 做历史回测
  - 策略编码：精确编码入场/止损/止盈，统计参数个数
  - 数据切分：前 70% 开发集，后 30% 验证集（锁住），仅在开发集上迭代
  - 5 维评分 + 复杂度惩罚：1-2 参数 0 扣分 / 3-4 参数 -5 / 5-6 参数 -10 / 7+ 参数 -15 强制降级
  - 开发集 Deploy（≥70）→ 解锁验证集跑一次；验证集评分 < 开发集 - 15 → 过拟合，降级 Refine
  - 同时测 N 个策略变体 → Deploy 阈值 = 70 + 5×log₂(N)
  - 滚动窗口验证（≥3 年数据建议执行）：至少 2 个窗口全部 ≥ 40
  - 三个必答题：最大回撤何时发生？连续亏损能否扛住？不同环境表现差异？
  - 最终判決：Deploy（≥70）→ 继续；Refine（40-69）→ 调整参数；Abandon（<40）→ 放弃
- 调用 `dip_buy_decision_skill` 或 `breakout_trade_execution_skill`
- ★**Gate 2 — 多空论战关**: 调用 `bull_bear_debate_gate_skill` 做六维加权评分
  - 技术面(30%) + 资金流向(15%) + ★拥挤度(10%) + 基本面(20%) + 情绪面(15%) + 宏观面(10%)
  - 拥挤度五指标：分析师覆盖/机构集中度/换手率/两融/舆论热度
  - 极端拥挤时超驰保护（止盈×0.7、仓位×0.5、止损收紧1%）
  - 🟢绿灯(Net≥2.0)→标准仓位 / 🟡黄灯(0.5~2.0)→半仓 / 🟠橙灯(-0.5~0.5)→观望 / 🔴红灯(<-0.5)→否决
  - 🔴红灯 → 不生成交易计划，终止该标的后续阶段
- 两关通过后 → 调用 `trade_plan_builder_skill`（🟡黄灯时仓位减半）
- 调用 `position_sizing_decision_skill`、`stop_loss_discipline_skill` + `take_profit_ladder_skill`
- 集中度合规检查（按当前模式参数）

### 模式感知规则

- **normal**: Stage 0 全市场扫描（T2 回调低吸，可选叠加 T1/T4）+ 完整五级漏斗，Level 4 输出 3-5 个交易计划
- **earnings_season**: Stage 0 优先使用 T5 财报驱动模板（可叠加 T2），增加财报日历检查，标注财报披露日期，财报前 3 天仓位降至 50%
- **risk_off**: 跳过 Stage 0 全市场扫描，仅扫描自选池。Level 4 降级，不生成交易计划，仅输出"防守观察清单"。★v4.3: 额外执行 risk_off 解除评估（Phase 1 条件是否满足），输出解除进度和估计天数。

### 输出要求

1. **主报告**：写入 `reports/daily/YYYY-MM-DD-watchlist-scan.md`（YYYY-MM-DD 为当前日期）
2. 报告格式参考 `reports/daily/YYYY-MM-DD-watchlist-scan.md` 的结构，在开头增加 Stage 0 全市场初筛结果章节
3. 报告末尾添加一行 `<!-- SCAN_COMPLETE -->` 作为完成标记

### 第四阶段：盘后回顾（融合入扫描报告）

在 Level 4 完成后，在报告中增加以下盘后回顾章节：

**1. 收盘自选回顾 (after_close_watchlist_recap_skill)**
- 当日涨跌幅排名（持仓 + watchlist Top 10/Bottom 10）
- 成交量 vs 5日均量（标注放量/缩量异常）
- 技术形态变化（突破/破位/关键位得失）
- 与昨日扫描结果对比（哪些标的形态升级/降级）

**2. 盘后总结 (post-market-debrief)**
- P&L 归因：哪些策略赚了/亏了，为什么
- 今日信号准确性评估：BUY/SELL 信号是否被实际走势验证
- ★v4.3: **Gate 2 多空论战准确率回顾**：灯号是否被后续走势验证（🟢绿灯标的实际走势如何）
- 技能链表现评估：哪个技能输出最有价值/最差
- 经验教训：今天犯的错，明天怎么避免
- 明日预判：关键观察标的、可能的触发条件

### 错误处理

- **Stage 0 search_stocks 失败**：降级为仅扫描自选池（跳过全市场初筛），在报告中注明"全市场初筛不可用，仅扫描自选池"
- 单个技能失败（如 Wind 数据不可用）→ 标记为 `DEGRADED`，继续后续步骤
- 在报告中注明数据缺失情况和降级原因
- 如果连市场环境判断都失败 → 输出错误报告并退出

### 数据源

- **★Layer 0 宏观**: `data/output/_layer0_summary.md` 或 `data/output/asset_allocation.json`
- **★Layer 1 板块**: `data/output/_layer1_summary.md` 或 `data/output/sector_signals.json`
- **★ETF 日终数据**: `data/output/_etf_daily.json`（`scripts/fetch_etf_daily.py` 预生成，akshare 同花顺端点）
- **★板块当日排名**: `data/output/_sector_ranking.json`（`scripts/fetch_sector_ranking.py` 预生成，akshare 同花顺端点）
- **实时行情**: 需盘中数据的场景通过 `wind-mcp-skill` 获取
- **ML Alpha**: `data/output/alpha_signals.json`
- **★P1 因子共识**: `data/output/alpha_signals_p1.json` + `data/output/v4.3-vs-v5.0-comparison.json`
- Wind MCP 调用方式：`node "%USERPROFILE%/.agents/skills/wind-mcp-skill/scripts/cli.mjs" call <server_type> <tool_name> '{"windcode":"..."}'`
- 必须使用 `.agents/skills/` 真实路径，禁止 `cd`/`&&`/`;`
- **预生成脚本**: `python scripts/build_report_context.py` 生成 Layer 0/1 摘要；`python scripts/fetch_etf_daily.py` + `python scripts/fetch_sector_ranking.py` 生成 ETF/板块数据

---

**最后一步：在完成所有分析并写入报告后，输出 `SCAN_COMPLETE` 作为最后一行。**