# 盘前简报 — 直接执行

> ⚠️ **直接执行模式** — 此 prompt 通过 `claude -p` 无头模式由 Windows 任务计划触发。
> **禁止创建 Cron 任务或定时作业。** 立即执行盘前分析并将报告写入 `reports/daily/YYYY-MM-DD-morning-brief.md`。
> 
> **数据预生成**：`run-morning-brief.ps1` 在调用 Claude 前已执行 ETF fetch / Sector fetch / HK fetch / CONTEXT build。
> 以下文件应在读取时已为最新状态（由 PowerShell 脚本保证）：
> - `data/output/_etf_daily.json` — 11 类资产 ETF 日终数据
> - `data/output/_sector_ranking.json` — 31 申万行业排名（T-1 收盘）
> - `data/output/hk_quotes.json` — 港股 watchlist 行情
> - `data/output/_layer0_summary.md` / `_layer1_summary.md` — 由 build_report_context.py 生成
> - `data/output/alpha_signals.json` — 由 signal-factory pipeline 生成（16:00 昨天）
> - `data/output/alpha_signals_p1.json` — v5.0 P1 因子共识信号（由 signal-factory Step 2.6 生成，16:00 昨天）
> - `data/output/v4.3-vs-v5.0-comparison.json` — ML vs P1 对比数据（由 signal-factory Step 6.5 生成）

---

## 任务：盘前闭环（立即执行）

你是 my-bot 趋势交易量化决策系统的盘前分析模块。

### ⚠️ 日期验证规则（强制）

- **当前日期和星期几已经由脚本注入到本 prompt 开头的 `<!-- INJECTED BY ... -->` 注释块中。这是唯一权威的日期来源，必须直接使用。**
- **严禁自行推算或猜测日期对应的星期几**——模型对 2026 年日历的推算不可靠。
- 若发现上一个交易日距今超过 1 个日历日，**首先检查中间是否有周末**，而非默认认为"数据缺失"。
- 报告中引用日期时，**必须同时标注正确的星期几**（如"9/18（周四）"），且必须与注入的日期信息一致。
- 若注入块中未提供某日期是周几，标注为"待核实"而非臆测。

### 前置步骤

1. 读取 `CLAUDE.md` 了解管线 **v4.3** 规则（三道关卡：Gate 1 回测关 / Gate 2 多空论战关 / Gate 3 风控关、模式判断、风控参数）
2. 读取 `portfolio.json` 获取当前持仓和 watchlist
3. 读取昨日报告 `reports/daily/YYYY-MM-DD-watchlist-scan.md`（YYYY-MM-DD 为昨日日期）获取前日收盘状态
4. **★ML Alpha 信号**: 读取 `data/output/alpha_signals_latest.json`（优先，infer-only 无滞后）；若不存在或无效则 fallback 到 `data/output/alpha_signals.json`（由 quant-signal-factory 每日 cron 自动生成，有 5 日设计滞后）
4b. **★P1 因子共识信号 (v5.0)**: 读取 `data/output/alpha_signals_p1.json` 获取 Phase 1 37 因子 IC 加权共识排名。P1 与 ML 信号源**正交**（P1 偏好 sh6* 上海主板，ML 偏好 sz3* 创业板），重叠率约 5%。读取 `data/output/v4.3-vs-v5.0-comparison.json` 获取双轨对比数据（已到期信号数/胜率/超额收益等）。若 P1 文件不存在或 stale（excess_lag_days > 2），标注 `P1_UNAVAILABLE` 但不阻塞报告。
4c. **★止损黑名单 (W46 #3)**: 读取 `data/blacklist.json` 获取止损黑名单。在自选股晨报中标注黑名单标的为"🚫 BLACKLISTED"，在盘前检查清单的计划中排除黑名单标的（不建仓）。黑名单由 signal_tracker 自动维护（5日亏损 > 15% 触发，有效期 30 天）。
5. **★Layer 0 宏观配置**: 读取 `data/output/_layer0_summary.md` 获取预生成的宏观资产配置概览（由 `scripts/build_report_context.py` 生成）。若文件不存在，跳过并在报告中标注 Layer 0 数据待初始化。
6. **★Layer 1 板块配置**: 读取 `data/output/_layer1_summary.md` 获取预生成的板块轮动概览。若文件不存在，读取 `data/output/sector_signals.json` 自行生成板块摘要。
7. **★港股行情**: 读取 `data/output/hk_quotes.json` 获取港股 watchlist 实时行情（由 `scripts/hk_stock_data.py` 盘前生成）。若文件不存在或 as_of 非今日，标注 `DEGRADED: 港股行情不可用`。
8. **★akshare 健康检查**: 读取 `data/output/akshare_health.json` 获取数据源健康状态。若 `classification` 为 BLOCKED，在报告中标注 `DEGRADED: akshare 东财接口封锁` 并优先依赖 Wind MCP 替代通道。若 `version.behind_count >= 5`，提示 akshare 落后 {N} 个版本。若文件不存在，跳过。
9. **★CSI300 日线数据** (v4.3.1 新增): 读取 `data/output/_csi300_daily.json` 获取预生成的 CSI300 最近 10 个交易日 OHLCV 数据（由 `build_report_context.py` 生成）。这是**权威数据源**——晨报中 CSI300 的开盘/收盘/最高/最低/成交量必须以此为基准。
   - 若 `_csi300_daily.json` 不存在或 `data_date` 非上一个交易日：标注 `⚠️ CSI300 K线数据缺失`，然后尝试 Wind MCP `get_index_kline(000300.SH)` 作为 fallback
   - **交叉验证**（硬约束）：CSI300 涨跌方向必须与 `_etf_daily.json` 中 CSI300 ETF (159919) 的 `chg_pct` 方向一致。不一致 → 🚨 拒绝使用，仅输出 ETF 方向信号

### ★三层架构报告结构（v4.3 强制）

> 盘前简报必须按以下顺序组织，Layer 0 → Layer 1 → Layer 2 自上而下。
> 预生成的 `_layer0_summary.md` 和 `_layer1_summary.md` 可直接嵌入（需更新交叉引用部分）。

**报告章节顺序**：
```
零、宏观资产配置概览 (Layer 0)        ← 读取 _layer0_summary.md 嵌入
一、板块配置概览 (Layer 1)             ← 读取 _layer1_summary.md 嵌入，补充 watchlist 交叉
二、市场环境判断 (现有)
三、隔夜外盘与宏观 (现有)
四、自选股晨报 + ML Alpha 交叉 + P1 因子共识交叉 (现有)
五、盘前检查清单 (现有)
六、risk_off 解除评估 (现有)
七、账户 P&L + 本周展望 (现有)
```

### ★第零阶段：宏观资产配置概览 (Layer 0)

> 从宏观象限 → 资产权重 → 调仓建议，确立当日整体配置基调。

1. **嵌入预生成概览**：读取 `data/output/_layer0_summary.md`，嵌入为报告的「零、宏观资产配置概览」章节
2. **补充实时判断**：
   - 若当前为 risk_off 模式：标注风险覆盖规则（A股上限/现金下限/债券下限）
   - 若有重大宏观事件（如中东局势、美联储决议）：在宏观象限表后追加事件影响评估
   - 调仓建议中标注哪些是"今日可执行"vs"risk_off 解除后执行"
3. **降级处理**：若 `_layer0_summary.md` 不存在，输出简化版（四象限 + 11 类资产表），标注"Layer 0 数据待初始化"

### ★第一阶段：板块配置概览 (Layer 1)

> ML 模型选出 Top-5 板块 → 与 watchlist 交叉 → 标注板块顺风/逆风。

1. **嵌入预生成概览**：读取 `data/output/_layer1_summary.md`，嵌入为报告的「一、板块配置概览」章节
2. **补充 watchlist 交叉**：替换概览中"需运行时读取 portfolio.json 填充"的部分：
   - 遍历 `portfolio.json` → `watchlist`，按标的所属板块对照 ML 排名
   - 处于 Top-5 板块内的标的标注"⬆ 板块加持"
   - 排名 >20 的板块内标的标注"⚠️ 板块逆风"
3. **ML Top-5 评注**：对每个 Top-5 板块给出一句话判断（如"电子：ML 最强信号但半导体政策月承压"）
4. **risk_off 联动**：risk_off 模式下 Top-5 板块正常展示，但标注"risk_off 禁止建仓，仅作为观察方向"

### ★数据完整性约束（强制）

> ⚠️ **以下规则为硬约束，违反将导致持仓数据错误传播。**

| # | 规则 | 说明 |
|---|---|---|
| 1 | **portfolio.json 是唯一可信源** | 报告中所有持仓数量、成本、已平仓记录必须与 `portfolio.json` 完全一致。禁止自行推算或修改。 |
| 2 | **条件计划 ≠ 已执行交易** | 盘前简报中的"若...考虑..."、"建议..."等为**待确认计划**，不是已发生的交易。条件触发（如"收盘跌破 X"）仅表示计划被激活，不代表交易已执行。 |
| 3 | **平仓记录必须来自 closed 数组** | 报告"已平仓"章节的每一条记录，必须在 `portfolio.json` → `closed` 数组中有对应条目。禁止凭空创建交易记录。 |
| 4 | **禁止自行计算虚拟 P&L** | 不要根据条件触发自行计算"锁定利润 +X"。只有 `closed` 数组中实际存在的 `pnl` 字段才是真实的已实现盈亏。 |
| 5 | **持仓数量与昨日报告的差异** | 若昨日报告中的持仓数量与今日 `portfolio.json` 不一致，以 `portfolio.json` 为准。**如果 portfolio.json 持仓未变，则报告中的持仓数量也不应变。** |
| 6 | **报告中的操作描述** | 使用"触发减仓条件"（条件满足但未执行）vs "已减仓"（closed 中有记录）。两者不可混淆。 |
| 7 | **交叉验证** | 写入报告前，必须将报告中所有持仓数量与 `portfolio.json` 逐条对比。发现不一致 → 以 `portfolio.json` 为准修正。 |

### ★ML Alpha 信号集成（quant-signal-factory）

> 信号工厂每日盘前自动运行 ensemble（CatBoost+XGBoost+GBDT）模型，产出全市场 alpha 排名。
> 读取 data/output/alpha_signals.json 获取 ML 排名，与 Agent 定性选股取交集优先。

1. **读取 alpha 信号**：从 data/output/alpha_signals.json 获取 top_50 列表
2. **★staleness 检查**：读取 `@contract.staleness`：
   - `excess_lag_days ≤ 0` → 正常使用（设计滞后 5 个交易日 + 2 天周末缓冲 = ~7 日历日）
   - `excess_lag_days 1–2` → 标记 `DEGRADED`，尝试读取 `alpha_signals_latest.json`
   - `excess_lag_days > 2` → 拒绝使用，尝试 `alpha_signals_latest.json`（注意：`_latest` 可能只有 infer-only 的少量标的）
   - 若两者均过期（excess > 2） → 使用最近可用的信号 + 标注 `STALE_SIGNAL`
   - 若 `@contract` 缺失（旧格式信号） → 标记 `NO_CONTRACT`，正常使用但提示升级信号工厂
3. **ML 排名标注**：在 Watchlist 候选表格中增加"Alpha 排名"列，显示每只标的在 ML 模型中的排名（未进 Top 50 的标注 -）
4. **ML 发现**：列出 ML Top 10 中**不在**当前 Watchlist 的标的，作为"ML 发现"补充
5. **持仓交叉**：检查当前持仓是否在 ML Top 50 中，标注"ML 认可"或"ML 未覆盖"
6. **低置信度过滤**：标记了 low_confidence: true 的标的（新股/特征不足），降低优先级

### ★P1 因子共识信号集成 (v5.0 FactorFactory)

> v5.0 新增的第二 Alpha 源：37 个 Phase 1 因子通过 IC 加权截面 Z-score 合成共识信号。
> 与 ML ensemble 完全正交（重叠率 ~5%，P1 选 sh6* 上海主板，ML 选 sz3* 创业板）。

1. **读取 P1 信号**：从 `data/output/alpha_signals_p1.json` 获取 top_50 列表（字段名为 `alpha_score` 而非 ML 的 `alpha`）
2. **staleness 检查**：与 ML 信号相同的逻辑（检查 `@contract.staleness.excess_lag_days`）
3. **P1 vs ML 交叉**：列出同时出现在 P1 Top 10 和 ML Top 10 的标的（如有），标注"双轨共振"
4. **P1 独有发现**：列出 P1 Top 10 中不在 ML Top 50 的标的，作为"P1 独立发现"
5. **P1 基准对比**（如有历史数据）：从 `v4.3-vs-v5.0-comparison.json` 读取 ML vs P1 胜率/超额收益/Sharpe 对比，在报告中标注当前相对表现
6. **P1 覆盖度**：从 `coverage_note` 字段提取可用因子数，若 < 20 标注 `P1_DEGRADED`

### 第二阶段：市场环境判断

1. 通过 `wind-mcp-skill` 获取沪深 300（000300.SH）最近 5 日 K 线数据
2. 基于昨日收盘数据判断市场状态（进攻/防守/震荡/切换）
3. 确定当前管线模式：`normal` / `earnings_season` / `risk_off`
4. **★v4.3: risk_off 解除评估**: 如果当前为 risk_off 模式，评估是否满足解除条件（详见 `data/risk-policy.json` → `modes.risk_off.exit_conditions`）：
   - Phase 1（risk_off → 震荡）：沪深300 3日不创新低 + 科创50 3日站上 MA5 + 成交恢复 + 微观 2/3 + 1 日确认
   - Phase 2（震荡 → 进攻）：震荡 2 日 + 沪深300 站上 MA20 + MA20 走平
   - 财报季期间解除后先进入 earnings_season，而非震荡
   - 输出当前进度和距解除的估计天数
5. 输出当前模式标记，贯穿后续所有步骤

### ★第二阶段 A：CSI300 数据完整性验证（P0 强制，v4.3.1 新增）

> ⚠️ **此节为硬约束。** 2026-10-26 晨报因 Wind MCP 静默失败 + 回退到午间盘中数据，产生了虚构的 10/23 CSI300 收盘价（4011.24 vs 实际 4105.33），导致整个晨报的"底部反转"叙事错误。

**A1. Wind MCP 调用结果显式标注（必做）：**

在报告"降级说明"表格中**必须**包含以下行：

| 组件 | 状态 | 说明 |
|---|---|---|
| CSI300 K线 | ✅ 正常 / 🚨 失败 / ⚠️ 降级 | Wind MCP `index_data.get_index_kline` 返回 {N} 条记录，最新日期={DATE}，close={VALUE}。若失败注明错误码。 |

- 若 Wind MCP 调用成功：显式输出返回的最新一条记录的 **完整 OHLCV**（日期/open/high/low/close/volume），作为报告数据来源的审计痕迹
- 若 Wind MCP 调用失败：**标注 🚨 并输出原始错误码**（如 `ROUTE_ERROR`/`PARAM_VALIDATION_ERROR`/`BALANCE_ERROR`），然后尝试 akshare `stock_zh_index_daily(symbol='sh000300')` 作为 fallback
- 若两者均失败：标注 `🚨 DATA_UNAVAILABLE`，**禁止继续生成市场环境判断**，输出错误报告并退出

**A2. CSI300 收盘价 × ETF 涨跌幅交叉验证（必做）：**

- 读取 `data/output/_etf_daily.json` → `assets[?name=='沪深300ETF'].chg_pct`
- **方向一致性检查**：CSI300 指数涨跌方向必须与 CSI300 ETF (159919) 涨跌方向一致
  - 指数涨 + ETF 涨 → ✅
  - 指数跌 + ETF 跌 → ✅
  - 指数涨 + ETF 跌（或反之）→ 🚨 **数据矛盾，必须拒绝使用并标注**
- **幅度合理性检查**：|指数涨跌幅 - ETF涨跌幅| 应 ≤ 0.5%（ETF 跟踪误差）。若超出，标注 ⚠️ 警告但仍可使用（可能由折溢价导致）
- 若交叉验证失败：**禁止在市场环境章节中使用该数据**。标注 `🚨 CSI300 数据交叉验证失败：指数 {X}% vs ETF {Y}%，方向不一致/误差过大`。此时降级为**仅引用 ETF 方向性信号**（涨/跌/平），不给出具体点位。

**A3. 降级链（按优先级）：**

| 优先级 | 数据源 | 交叉验证 | 失败处理 |
|---|---|---|---|
| 1 | Wind MCP `get_index_kline(000300.SH)` | vs _etf_daily.json 159919 | → fallback 到 akshare |
| 2 | akshare `stock_zh_index_daily(sh000300)` | vs _etf_daily.json 159919 | → fallback 到仅 ETF 方向 |
| 3 | 仅 _etf_daily.json | — | → 🚨 标注 NO_CSI300_CLOSE，不给具体点位 |
| ❌ | ~~午间盘报告/历史报告~~ | — | **禁止**使用午间报告或历史报告的盘中数据作为收盘数据 |

### 第三阶段：自选股晨报 + 隔夜外盘

1. 隔夜重大事件：
   - 通过 Wind 获取财经新闻，关注政策、行业、公司重大公告
   - 美股/港股隔夜走势（如能获取）
   - 影响 A 股开盘的关键外部因素

2. ★美国政策风险扫描（v4.3 新增）：
   > 盘前扫描美国政策公告，识别可能影响持仓标的的跨境风险。Wind `get_financial_news` 已验证可搜到白宫行政令的中文报道。

   a. **关键词扫描**：通过 Wind `financial_docs.get_financial_news` 搜索以下关键词组（每组不含空格，逐个执行）：
      - `特朗普行政令制裁关税`
      - `美国禁令半导体芯片`
      - `美国制裁能源电力`
      - `美国关税贸易`

   b. **交叉比对**：若命中，将政策关键词与当前持仓+观察标的的行业标签对照：
      | 政策关键词 | 敏感持仓/关注标的 |
      |---|---|
      | 半导体/芯片/制裁/禁令 | 中芯国际、澜起科技、江丰电子、鼎龙股份 |
      | 变压器/电力/能源/电网 | 思源电气、特变电工 |
      | 储能/电池/新能源 | 思源电气（储能） |
      | 关税/贸易战 | 全部出口型标的 |
      | 金融/银行/制裁 | 中国平安 |

   c. **输出格式**：在简报"二、隔夜外盘与宏观"中新增"⚠️ 美国政策风险"子章节：
      | 政策事件 | 日期 | 影响标的 | 影响评估 |
      |---|---|---|---|
      | {标题} | {日期} | {标的列表} | {简要评估} |

   d. **无命中**：若未发现 48h 内新政策，注明"过去 48h 无新增美国政策风险"。

3. 外盘联动：
   - 通过 akshare `futures_zh_daily_sina` 获取 CFFEX 股指期货（IF0/IC0/IH0）前 3 日收盘价，计算升贴水（基差=期货-现货），作为 A 股市场情绪代理
   - 汇率变动（通过 akshare `currency_boc_sina` 获取 USD/CNY 现汇买入价）
   - > ⚠️ **北向资金实时净流向自 2024 年 8 月起永久停止公开披露**（监管要求改为季度发布）。**此项已从数据获取清单中移除，不再报告 DEGRADED。**替代指标：板块排名 + IF升贴水 + 涨跌比组合。

4. 竞价预期：
   - 自选标的（portfolio.json 中 watchlist + positions）的集合竞价预期
   - 关注可能的大单异动

### 第四阶段：盘前检查清单 (premarket_trade_checklist_skill)

1. 风控状态：
   - 熔断是否触发（日亏损熔断、连续亏损熔断）
   - 连续亏损次数统计
   - 当前模式的风控参数确认

2. 仓位检查：
   - 各标的集中度是否超限（按当前模式参数）
   - 融资账户风险敞口
   - 可用资金

3. 今日计划确认：
   - 哪些标的准备操作（建仓/加仓/减仓/止盈/止损）
   - 具体的触发条件（精确价格或条件）
   - ★v4.3: 建仓/加仓操作需通过 **Gate 2 多空论战关**（五维加权评分）
     🟢绿灯(Net≥2.0)→标准仓位 / 🟡黄灯(0.5~2.0)→半仓 / 🟠橙灯(-0.5~0.5)→观望 / 🔴红灯(<-0.5)→否决
   - 不操作清单（明确哪些标的今日不动）

### 输出要求

1. **主报告**：写入 `reports/daily/YYYY-MM-DD-morning-brief.md`（YYYY-MM-DD 为当前日期）
2. 报告格式参考已有的盘前简报（如 `reports/daily/YYYY-MM-DD-morning-brief.md`）
3. 报告末尾添加一行 `<!-- MORNING_BRIEF_COMPLETE -->` 作为完成标记

### 模式感知

- **normal**: 标准盘前简报，可给出建仓建议
- **earnings_season**: 增加财报日历提醒，财报前 3 天仓位降至 50%
- **risk_off**: 不给出建仓建议，重点检查止损触发和减仓需求

### 错误处理

- 单个数据源失败 → 标记为 `DEGRADED`，继续。各数据源独立降级：
  - akshare 不可用：CFFEX 期货/汇率标记 DEGRADED，尝试用 Wind MCP `index_data` 获取沪深300 K线（北向资金已永久停披，不在此列）
  - Wind MCP 不可用：个股行情标记 DEGRADED，尝试用 akshare `stock_zh_a_hist` 逐股获取
  - 两者均不可用 → 基于前一日报告中的价格继续（标注 DATA_UNAVAILABLE）
- **★CSI300 数据交叉验证**（v4.3.1 新增）：见第二阶段 A 节。CSI300 收盘必须与 ETF 涨跌方向一致。不一致 → 拒绝使用，仅输出 ETF 方向信号。Wind MCP 失败 → 显式标注错误码后尝试 akshare fallback。
- 如果连市场环境判断都失败 → 输出错误报告并退出

### 数据源

- **★Layer 0 宏观配置**: `data/output/_layer0_summary.md`（预生成）或 `data/output/asset_allocation.json`
- **★Layer 1 板块配置**: `data/output/_layer1_summary.md`（预生成）或 `data/output/sector_signals.json`
- **★Layer 2 CSI300 K线** (v4.3.1): 预生成 `data/output/_csi300_daily.json`（由 `build_report_context.py` 周六/周一盘前生成），包含最近 10 个交易日 OHLCV。优先使用此文件，Wind MCP 作为验证/补充。
- **Layer 2 个股行情**: 通过 `wind-mcp-skill` 获取
- 调用方式：`node "%USERPROFILE%/.agents/skills/wind-mcp-skill/scripts/cli.mjs" call <server_type> <tool_name> '{"windcode":"..."}'`
- 必须使用 `.agents/skills/` 真实路径，禁止 `cd`/`&&`/`;`
- **预生成脚本**: `python scripts/build_report_context.py` 生成 Layer 0/1 摘要 + CSI300 K线（每日盘前执行）
- **★CFFEX 股指期货** (替代 A50): akshare `futures_zh_daily_sina(symbol='IF0'/'IC0'/'IH0')` — 日频 K 线，计算升贴水
- **★汇率**: akshare `currency_boc_sina(symbol='美元')` — 中行外汇牌价，取现汇买入价
- **★北向板块偏好** (已永久停披): 北向资金实时净流向自 2024 年 8 月起停止公开披露（监管要求改为季度发布）。不再获取、不再报告 DEGRADED。替代：板块排名 + IF升贴水组合。

---

**最后一步：在完成所有分析并写入报告后，输出 `MORNING_BRIEF_COMPLETE` 作为最后一行。**