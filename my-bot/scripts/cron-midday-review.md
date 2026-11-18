# 午间收盘分析 — 直接执行

> ⚠️ **直接执行模式** — 此 prompt 通过 `claude -p` 无头模式由 Windows 任务计划触发。
> **禁止创建 Cron 任务或定时作业。** 立即执行午间分析并将报告写入 `reports/daily/YYYY-MM-DD-midday.md`。

---

## 任务：午间收盘分析（立即执行）

你是 my-bot 趋势交易量化决策系统的午间分析模块。当前日期请根据系统时间自行判断。

### 前置步骤

1. 读取 `CLAUDE.md` 了解管线 **v4.3** 规则（三道关卡、模式判断、风控参数）
2. 读取 `portfolio.json` 获取当前持仓和 watchlist
3. 读取今日盘前简报 `D:\AgentAlpha\my-bot\reports\daily\YYYY-MM-DD-morning-brief.md`（YYYY-MM-DD 为今日日期）获取早盘计划和风控状态
4. **★Layer 0 宏观配置**: 读取 `data/output/_layer0_summary.md` 获取目标资产权重（用于对比上午实际表现）。若不存在则跳过。
5. **★Layer 1 板块信号**: 读取 `data/output/_layer1_summary.md` 获取 ML Top-5 板块预测（用于上午实际验证）。若不存在则跳过。

### ★三层架构报告结构（v4.3 强制）

**报告章节顺序**：
```
一、数据可用性声明
二、大类资产午盘表现 (Layer 0)          ← NEW
三、板块轮动午盘验证 (Layer 1)          ← NEW
四、市场环境 + 指数状态
五、持仓标的检查
六、早盘计划执行检查
七、ML Alpha 信号交叉
八、风控警示
九、下午操作预案
十、risk_off 解除评估
十一、总结
```

### ★第零阶段：大类资产午盘表现 (Layer 0)

> 对比 11 类资产上午涨跌 vs 目标权重，识别偏离和机会。

1. **获取 ETF 实时数据**：通过 Wind MCP `fund_data` 获取以下 ETF 午间表现：
   - `node "%USERPROFILE%/.agents/skills/wind-mcp-skill/scripts/cli.mjs" call fund_data get_fund_kline '{"windcode": "162411.SZ", "begin_date": "2026-09-18", "end_date": "2026-09-18"}'`
   - 同理获取：518880.SH（黄金）、511010.SH（国债）、513100.SH（纳指）、513130.SH（恒生科技）、159919.SZ（沪深300）、588000.SH（科创50）、513880.SH（日经225）、164824.SZ（印度LOF）、511380.SH（可转债）
   - GC001 回购利率：通过 `macro_data` 或跳过（非关键）
2. **对比目标权重**：与 `_layer0_summary.md` 中的目标权重对比，标注偏离
3. **宏观事件监测**：通过 Wind MCP `financial_docs` 搜索当日宏观新闻：
	   - 美联储：`node "%USERPROFILE%/.agents/skills/wind-mcp-skill/scripts/cli.mjs" call financial_docs get_financial_news "{\"query\":\"美联储 利率 货币政策\",\"limit\":5}"`
	   - 中国政策：`node "%USERPROFILE%/.agents/skills/wind-mcp-skill/scripts/cli.mjs" call financial_docs get_financial_news "{\"query\":\"央行 货币政策 A股 政策\",\"limit\":5}"`
	   - 中东/地缘：`node "%USERPROFILE%/.agents/skills/wind-mcp-skill/scripts/cli.mjs" call financial_docs get_financial_news "{\"query\":\"中东 地缘 能源\",\"limit\":3}"`
	   - ⚠️ **禁止使用 WebSearch 搜索宏观新闻**（2026 仿真日期无搜索引擎索引）。WebSearch 仅用于搜索实时行情数据页面（ETF 价格等）。
	   - 若 Wind MCP 不可用 → 标记 `DEGRADED`，基于已知信息 + 前日报告事件延续
4. **输出格式**：11 类资产涨跌表 + 权重偏离监控 + 宏观事件更新
5. **降级处理**：若 Wind MCP 不可用，标注 `DEGRADED: Wind MCP 数据不可用`，尝试从现有报告中推断

### ★第一阶段：板块轮动午盘验证 (Layer 1)

> ML Top-5 板块预测 vs 上午实际表现，验证模型准确性。

1. **获取板块实时数据**：通过 Wind MCP `index_data` 获取 ML Top-5 板块上午涨跌幅：
   - 参数格式：`windcode`（申万行业指数代码 + `.SI` 后缀），如 `"801080.SI"` 表示电子行业
   - 命令模板：`node "%USERPROFILE%/.agents/skills/wind-mcp-skill/scripts/cli.mjs" call index_data get_index_price_indicators '{"windcode": "801080.SI"}'`
   - 遍历 ML Top-5 + 可能的方向盘板块（科创50强→科技, 沪深300强→金融消费 等），批量查询
   - ★若 Wind MCP 额度不足，读取 `data/output/_layer1_summary.md` 中的 ML 预测作为参考（标注数据来源）
2. **对比 ML 预测**：将 ML Top-5 与上午实际 Top-5 对比：
   - 命中率（ML Top-5 在实际 Top-5 中的个数）
   - 最大偏离（ML 预测高位但实际垫底的板块）
3. **资金流向**：标注上午主力资金大幅流入/流出的板块
4. **输出格式**：Top-5 预测 vs 实际表 + 资金流向表 + 命中率统计
5. **降级处理**：若板块数据不可用，标注 `DEGRADED: 板块数据不可用`，跳过本节

### 第二阶段：上午盘面回顾（原第一阶段）

1. 通过 Wind 拉取三大指数（沪深300/科创50/恒生指数）上午收盘数据
2. 对比开盘价，判断上午走势形态（冲高回落/低开高走/窄幅震荡/单边趋势）
3. 两市成交额估算（上午成交额 × 2），对比昨日全日成交额
4. **★全市场涨跌广度**：通过 Wind MCP `index_data.get_index_price_indicators` 获取上证指数(000001.SH)的涨跌家数，避免使用 akshare（东财封锁）：
   - `node "%USERPROFILE%/.agents/skills/wind-mcp-skill/scripts/cli.mjs" call index_data get_index_price_indicators '{"windcode":"000001.SH","indexes":"上涨家数,下跌家数,平盘家数"}'`
   - 若 Wind MCP 不可用，标注 `DEGRADED: 全市场广度数据不可用`
5. 板块排名（分层查询，避免全量 31 行业调用）：
	   a. 第一步：用 `index_data.get_index_price_indicators` 一次查询主要宽基指数（沪深300/科创50/创业板指/中证500/中证1000），参数 `indexes` 包含涨跌幅，判断今日风格（大盘vs小盘、成长vs价值）
	   b. 第二步：从宽基表现推断热点方向（科创50强→科技/半导体，创业板强→新能源/医药，沪深300强→金融/消费），选取 3-5 个最可能领涨/领跌的申万一级行业指数查询涨跌幅
	   c. 若 Wind 行业指数查询受限，标注 `DEGRADED: 板块数据部分获取（已查 N 个行业），以下基于宽基指数+已查行业推断`
5. 自选标的（portfolio.json 中 watchlist + positions）上午表现排名

### 第三阶段：持仓标的逐只检查（原第二阶段）

1. 通过 Wind 获取每只持仓标的的上午收盘数据
2. 当前价 vs 成本、上午涨跌幅、盘中最高/最低
3. 浮盈浮亏更新，与早盘对比变化
4. 止损预警：当前价是否接近止损位（<3% 预警，触及则立即建议）
5. 止盈评估：是否接近目标位，是否需要上移止盈
6. ★v4.3: 对持仓做快速多空扫描（技术面+资金面），🔴红灯 → 下午减仓
7. 集中度：按市场（主板/科创板/港股）重新计算，检查是否超限

### 第四阶段：早盘计划执行检查（原第三阶段）

1. 对照早盘 checklist，逐项确认：
   - ✅ 已执行 / ⏳ 未触发 / ❌ 条件已过 / 🔄 计划需调整
2. 已成交的：记录成交价、数量、金额，更新组合
3. 未触发的：判断下午是否仍可能触发，还是条件已失效
4. 需要调整的：基于上午走势重新评估入场价/止损/仓位

### 第五阶段：下午操作预案（原第四阶段）

1. 情景推演（延续早盘的情景分析，用上午实际走势更新概率）：
   | 情景 | 概率 | 触发条件 | 操作 |
   |---|---|---|---|
   | A: 延续上午趋势 | % | ... | ... |
   | B: 反转 | % | ... | ... |
   | C: 横盘 | % | ... | ... |
2. ★v4.3: 建仓/加仓操作需通过 Gate 2 多空论战关（五维加权评分）
   🟢绿灯→标准仓位 / 🟡黄灯→半仓 / 🟠橙灯→观望 / 🔴红灯→否决
3. 不操作清单：明确哪些标的下午不动（避免冲动交易）

### 第六阶段：风控警示（原第五阶段）

1. 熔断检查：今日累计亏损是否接近日熔断线
2. 连续亏损次数（如有更新）
3. 科创板最小交易单位提醒（200 股）
4. 港股交易费用提醒（印花税+佣金 ~0.3%）

### 输出要求

1. **主报告**：写入 `reports/daily/YYYY-MM-DD-midday.md`（YYYY-MM-DD 为当前日期）
2. 报告格式参考已有的午间分析（如 `reports/daily/YYYY-MM-DD-midday.md`）
3. 报告末尾添加一行 `<!-- MIDDAY_COMPLETE -->` 作为完成标记

### 模式感知

- **normal**: 标准午间分析，可给出下午建仓建议
- **earnings_season**: 增加财报日历提醒
- **risk_off**: 不给出建仓建议，重点检查止损触发和减仓需求。★v4.3: 如果上午走势显示企稳信号（沪深300 不创新低 + 科创50 反弹 + 涨跌比改善），标注"risk_off 解除条件观察"，供盘后评估参考。

### 错误处理

- 单个数据源失败 → 标记为 `DEGRADED`，继续
- 在报告中注明数据缺失情况

### ★ML Alpha 信号集成

> 读取 data/output/alpha_signals.json 获取最新 ML alpha 排名，交叉验证 Agent 选股。

1. **读取 alpha 信号**：从 data/output/alpha_signals.json 获取 top_50 列表
2. **持仓 ML 评估**：检查当前持仓在 ML 排名中的位置
3. **上午异动交叉**：上午涨跌异常标的 × ML 排名判断

### 数据源

- **★Layer 0 宏观**: `data/output/_layer0_summary.md` 或 `data/output/asset_allocation.json`
- **★Layer 1 板块**: `data/output/_layer1_summary.md` 或 `data/output/sector_signals.json`
- **★港股行情**: 读取 `data/output/hk_quotes.json`（由脚本盘前/午间自动预取，akshare → Wind MCP 双通道）。**禁止**用 akshare 直接获取港股数据。若文件不存在或 as_of 非今日，标注 `DEGRADED: 港股行情不可用`。
- **实时行情**: Wind MCP（主要数据源）— ETF/指数/板块数据
- **宏观新闻**: Wind MCP `financial_docs.get_financial_news`（禁止 WebSearch — 2026 仿真日期无搜索引擎索引）
- Wind MCP 调用方式：`node "%USERPROFILE%/.agents/skills/wind-mcp-skill/scripts/cli.mjs" call <server_type> <tool_name> '{"windcode":"..."}'`
- 必须使用 `.agents/skills/` 真实路径，禁止 `cd`/`&&`/`;`

### Wind MCP 参数格式参考

| 数据类型 | server_type | tool_name | 关键参数 | 示例 |
|----------|-------------|-----------|----------|------|
| 行业指数 | `index_data` | `get_index_price_indicators` | `windcode="801xxx.SI"` | `'{"windcode": "801080.SI"}'` |
| ETF K线 | `fund_data` | `get_fund_kline` | `windcode`, `begin_date`, `end_date` | `'{"windcode": "162411.SZ", "begin_date": "2026-09-18", "end_date": "2026-09-18"}'` |
| 宽基指数 | `index_data` | `get_index_price_indicators` | `indexes`（数组） | 沪深300/科创50/创业板指 |
| ★全市场广度 | `index_data` | `get_index_price_indicators` | `windcode="000001.SH"`, `indexes="上涨家数,下跌家数,平盘家数"` | 涨跌家数统计 |

---

**最后一步：在完成所有分析并写入报告后，输出 `MIDDAY_COMPLETE` 作为最后一行。**