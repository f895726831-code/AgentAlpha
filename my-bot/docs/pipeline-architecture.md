# Pipeline Architecture Guide

> 2026-11-16 | v2.3 | W47 P1 信号优化 (HMM过滤+质量门+板块集中度+持仓期)

## 系统总览

```
┌─────────────────────────────────────────────────────────────┐
│                    quant-signal-factory (QSF)                │
│  数据采集 → 因子计算 → ML 训练 → 信号生成 → Gate 1 回测      │
│  路径: D:\AgentAlpha\signal-factory                               │
└──────────────┬────────────────────────────┬─────────────────┘
               │ sync_signal_factory.py     │ paper_factor
               ▼                            ▼
┌──────────────────────────────┐  ┌──────────────────────────┐
│     FM_trade (my-bot)        │  │  factor-lab (WSL2:8700)  │
│  信号筛选 → 追踪 → 风控      │  │  GFlowNet → Factory →    │
│  Morning/Midday/Daily/Scan   │  │  FactorRegistry → Contract│
│  路径: D:\AgentAlpha\my-bot    │  │  路径: D:\...\scripts\wsl2│
└──────────────────────────────┘  └──────────────────────────┘
```

## 定时任务时间线 (Windows Task Scheduler)

```
07:30 [Mon]  ModelRetrain-Weekly    → 重训 CatBoost/XGBoost/GBDT
07:55         FactorLab-Wakeup        → WSL2 启动 + health check
08:00 [Mon]  FactorLab-Weekly-Compute → 因子计算+合成+导出
08:35         MorningBrief            → 盘前简报 (claude -p)
11:30         MiddayReview            → 午间复盘 (claude -p)
16:00         SignalFactory           → 完整流水线 (见下)
17:30         DailyScan               → 盘后扫描 (claude -p)
17:45         HealthCheck             → 心跳 + E2E 验证
Sat 10:00     FactorLab-Weekly-Research → 论文挖掘 + RD-Agent
Sat 11:00     Evolution-Review        → 双轨进化审查
1st 09:00     Evolution-Scan          → Track B 外部扫描
```

## SignalFactory 日频流水线 (16:00)

```
Step 0:    akshare 健康检查 → BLOCKED/DEGRADED/OK
Step 0.5:  WSL2 + factor-lab 就绪检查 (P2-9)
Step 1.0:  STABLE 宏观资产配置 (Layer 0)
Step 1.0a: 数据更新 (update_daily.py, akshare→baostock fallback)
Step 1.5:  板块数据采集 (31 SW)
Step 1.6:  板块信号生成 (Layer 1 ML)
Step 2:    CSI 500 ML Ensemble (alpha_signals.json)
Step 2b:   CSI 300 ML Ensemble (alpha_signals_csi300.json)
Step 2.5:  CSI 500 Infer-Only (alpha_signals_latest.json)
Step 2.5b: CSI 300 Infer-Only (alpha_signals_latest_csi300.json)
Step 2.6:  Phase 1 Factor Consensus (alpha_signals_p1.json, IC-weighted Z-score)
Step 3:    Sync → FM_trade + Paper Factor Enrichment
Step 3.5:  VWAP Factor Merge (IC=0.356)
Step 4:    Signal Watchlist 初始化
Step 5:    Build Report Context (Layer 0/1 摘要)
Step 5.1:  TiMi Market Data Build (OHLCV + MA/RSI)
Step 5.5:  HK Stock Data (港股持仓行情)
Step 5.6:  ETF Daily Performance (11-class assets)
Step 5.7:  Sector Ranking (31 SW industries)
Step 6:    ML Signal Tracker (Top 20, 5d hold, benchmark comparison)
```

## 数据流

### 行情数据: akshare → baostock 双通道降级

```
akshare (Tier 1)
  ├─ stock_zh_a_hist()        → 个股日K (EastMoney)
  ├─ stock_zh_index_daily()   → 指数日K (EastMoney)
  └─ blocked? → baostock (Tier 2)
       ├─ query_history_k_data_plus() → 个股/指数日K
       ├─ 自动重连 (检测"用户未登录"自动 relogin)
       └─ 列名差异处理 (baostock 中文列名 → 统一英文)
```

关键文件:
- `D:\AgentAlpha\signal-factory\src\data\akshare_fallback.py` — fallback 函数
- `D:\AgentAlpha\signal-factory\scripts\update_daily.py` — 增量更新
- `D:\AgentAlpha\signal-factory\scripts\akshare_health.py` — 日频健康检查

### ML 信号: Qlib Alpha158 → Ensemble

```
Qlib Alpha158 因子 (158 features)
  ├─ Ref($close, -5)/$close - 1 (label)
  └─ Train: 2015~fit_end, Valid: fit_end~end, Test: end~

Model Training (每周一 07:30)
  ├─ CatBoost  → CatBoostModel (300 iters)
  ├─ XGBoost   → XGBModel (300 rounds)
  ├─ GBDT      → LGBModel (300 rounds)
  └─ Each: train → valid IC → OOSWeightGuard (P1-8)

Ensemble Prediction (每日 16:00)
  ├─ method="weighted_mean" (--oos-weight, P1-8)
  ├─ 滚动 IC 降权: floor=0.30, target_ic=0.03, window=4
  └─ CSI500 + CSI300 分别重训/预测

Signal Output
  ├─ alpha_signals.json         → CSI500 标准预测
  ├─ alpha_signals_csi300.json  → CSI300 标准预测 (行业中性化)
  ├─ alpha_signals_latest.json  → CSI500 infer-only (无滞后)
  └─ alpha_signals_latest_csi300.json → CSI300 infer-only
```

### 信号同步: QSF → FM_trade

```
sync_signal_factory.py
  ├─ 复制 JSON → FM_trade/data/output/
  ├─ Staleness gate: excess_lag_days > 2 → 拒绝
  ├─ Paper factor enrichment (factor-lab API)
  └─ Manifest 更新 (data/output/manifest.json)
```

### 信号追踪: Signal Tracker (P1-5, W47 #1A)

```
signal_tracker.py
  ├─ 每日记录 Top 20 alpha signals → signal_tracker.json
  ├─ 5 calendar days 持有期后结算 (return_5d)  ← W47: 7→5 (≈5→4 trading days)
  ├─ 基准对比: CSI300/CIS500 同期收益 → excess_csi300/csi500
  ├─ 统计: avg_return, win_rate, sharpe, excess_win_rate
  ├─ --backfill-benchmark: 历史数据回填基准
  └─ --report-only: 仅生成报告不更新
```

### 执行现实性门禁: ExecutionGate (P1-7)

```
Backtest pipeline (run_pipeline.py)
  └─ RealismConfig(
       check_st_suspended=True,     ← ST/停牌日历过滤
       check_price_limits=True,     ← 涨跌停门禁
       check_amount_constraint=True ← 成交量约束
     )
```

## Universe 管理 (P1-6)

```
data/instruments/
  ├─ all.txt    → 700 全量标的
  ├─ csi300.txt → CSI 300 成分股 (201 only, 需扩展)
  └─ csi500.txt → CSI 500 成分股 (500)

run_pipeline.py --universe csi500 (默认)
run_pipeline.py --symbols sh600519 sz000001  (手动覆盖)
```

## OOS 自动降权 (P1-8)

```
周一重训:
  _train_model() → valid segment IC → OOSWeightGuard.record_ic()

每日预测:
  predict_ensemble(method="weighted_mean")
  → guard.compute_weights()
  → rolling_ic[model] / target_ic × equal_weight
  → floor=0.30 (不完全剔除)

数据: data/output/oos_weight_state_{csi500|csi300}.json
```

## WSL2 factor-lab

```
Windows Task Scheduler
  └─ start-factor-lab.ps1 (启动时 + 07:55)
       ├─ wsl -d Ubuntu-22.04 → systemd auto-start
       └─ wsl curl http://localhost:8700/api/v1/health

Pipeline readiness (P2-9):
  └─ Step 0.5: WSL check → health check → auto-restart
  └─ run-weekly-factor-lab.ps1: same pattern

.wslconfig (P2-9):
  ├─ vmIdleTimeout=-1  (禁止空闲关闭)
  ├─ memory=8GB
  └─ processors=4
```

## v5.0 Phase 1 因子注入与 Bridge (NEW)

```
Phase 1 Factor Factory (LLM Agent + GFlowNet)
  └─ 70 factors, 7 types, 71% pass rate (CSI500 2015-2025)
       ↓ P0-2: inject_phase1_factors.py
  D:/qlib/qlib_data/cn_data/features/<stock>/p1_*.day.bin
       │  60 factors × 500 stocks = 30,000 files
       │  Format: little-endian float32, first element = start_idx
       ↓ P0-C: phase1_bridge.py (直读, 不依赖 Qlib auto-discovery)
  MultiIndex DataFrame (instrument, datetime) → 可与 Qlib features join
       ↓ validate_p1_ic.py
  Cross-sectional RankIC: 37/60 pass, 9 strong, best = -0.0428
       ↓ (Phase 2 待接入)
  ML Ensemble feature enrichment → daily signal generation
```

### 注入管线

```
inject_phase1_factors.py
  ├─ sys.path → wsl2/ (ffo.parser + ffo.computer)
  ├─ For each of 60 non-trivial Phase 1 factors:
  │   ├─ ffo.parser.parse(formula) → AST
  │   └─ ffo.computer.FactorComputer.compute(AST) → 500 stocks × 2874 days
  ├─ For each stock, write np.float32([start_idx, values...]).tofile()
  └─ Output: features/<stock>/p1_<normalized_name>.day.bin
```

### Bridge API

```python
from phase1_bridge import load_p1_factors, list_p1_factors

# List all available Phase 1 factors
names = list_p1_factors()  # ['p1__close_Delay_close_5_....day', ...]

# Load specific factors for Qlib pipeline
df = load_p1_factors(
    factor_names=['p1__close_Delay_close_5_Delay_close_5_.day'],
    start='2015-01-05',     # CSI500 earliest valid date
    end='2024-12-31',       # evaluation window
)
# Returns: MultiIndex DataFrame (instrument, datetime) with factor columns
```

### 关键文件

| 文件 | 用途 |
|------|------|
| `scripts/wsl2/phase1_bridge.py` | Phase 1 因子直读 Bridge |
| `scripts/wsl2/scripts/inject_phase1_factors.py` | 因子注入 Qlib bin |
| `scripts/wsl2/scripts/validate_p1_ic.py` | 增量 IC 独立验证 |
| `scripts/wsl2/scripts/backtest_phase1_factors.py` | v4.3 vs v5.0 因子对比 |
| `scripts/wsl2/results/phase1_incremental_ic.json` | IC 验证结果 |
| `scripts/wsl2/results/phase1_vs_v43_comparison.json` | 回测对比结果 |
| `scripts/wsl2/results/phase1_injection_manifest.json` | 注入清单 |

### 已知约束

- Qlib 不自动发现 `p1_*` 特征字段 → 必须通过 `phase1_bridge.py` 直读
- CSI500 最小有效日期: 2015-01-05 (指数发布日)
- `.day.bin` 文件名中的 `.day` 是 stem 的一部分 (不是独立 extension)
- 必须用 `python` (C:\Python312) 而非 `python3` (Windows Store 版本被安全分类器拦截)

## v5.0 Phase 2 P1 信号优化 (W47)

> 2026-11-16 | W47 #1A-#1D | 四项优化提升 P1 因子共识信号质量

### 设计原则

W46 首批 P1 信号验证：5D avg -1.16%, win rate 10%（D4 时 +0.25%, 35%）。四项优化将信号质量从盲目乐观转向条件驱动。

### Step 2.6 细化: Phase 1 Consensus Generation

```
generate_p1_signals.py (新增, W47)
  │
  ├─ Phase 0: HMM Regime Gate (W47 #1B)
  │   ├─ 加载 hmm_divergence_state.json (STABLE HMM 判档)
  │   ├─ risk_off/bear → 全部信号标签 low_confidence
  │   ├─ diverged + alert + >2w → 拒绝生成 (exit 0, 非错误)
  │   └─ --no-hmm-filter 绕过强制生成
  │
  ├─ Phase 1: IC-Weighted Z-Score Consensus
  │   ├─ 加载 37 因子 (|RankIC| >= 0.015) × 500 stocks
  │   ├─ 截面 Z-score + winsorize (1%/99%)
  │   └─ |IC| 加权求和 → raw consensus
  │
  ├─ Phase 2: Quality Gate (W47 #1C)
  │   ├─ raw consensus >= μ + k*σ 截面门槛
  │   ├─ 默认 k=0.5 (11/12: 72/500 通过)
  │   └─ 不足 10 支 → 拒绝 (共识太弱)
  │
  ├─ Phase 3: Rank Normalize + Output
  │   ├─ 质量通过股票百分位排名 → [-1, +1] score
  │   ├─ low_confidence: score 绝对值 < 0.4 (W47: 0.3→0.4)
  │   ├─ Sector Concentration Check (W47 #1D)
  │   │   ├─ sector-map.json 分类 (388 stocks, 29 sectors)
  │   │   ├─ 单板块 >30% 已知股票 → 降级 low_confidence
  │   │   └─ 已知覆盖率 <15% → 跳过
  │   └─ Output: alpha_signals_p1.json (双路径保存)
  │
  └─ 合约字段 (@contract):
      ├─ hmm_regime, hmm_filter_active, hmm_filter_reason
      ├─ quality_pass, quality_filtered_out, quality_threshold_sigma
      └─ sector_overconcentrated, sector_concentration_report
```

### 信号追踪配合 (W47 #1A)

```
signal_tracker.py HOLD_DAYS = 5  (7→5 calendar days, ≈4 trading days)
  ├─ 验证: W46 11/05 batch D4 avg +0.25% vs D5 -1.16%
  └─ 止损黑名单阈值不变 (-15%), D4 max loss = -3.97%
```

### CLI 参数

```
python scripts/generate_p1_signals.py \
  --date 2026-11-13 \         # 目标交易日 (默认: latest)
  --top-n 50 \                # 输出股票数 (默认: 50)
  --min-ic 0.015 \            # 最小 |IC| 门槛
  --min-consensus-sigma 0.5 \ # 质量门槛 k (默认: 0.5)
  --no-hmm-filter \           # 绕过 HMM 过滤
  --dry-run \                 # 不保存
  --verbose                   # 显示因子详情
```

### 关键文件

| 文件 | 用途 |
|------|------|
| `scripts/wsl2/scripts/generate_p1_signals.py` | P1 共识信号生成 (W47 新增) |
| `scripts/signal_tracker.py` | 信号追踪 (HOLD_DAYS 已更新) |
| `data/output/alpha_signals_p1.json` | P1 信号输出 (FM_trade) |
| `data/sector-map.json` | 行业分类映射 (388 stocks, 29 sectors) |

```
All scripts now use:
  adjust="qfq" (前复权) + factor=1.0
  → raw 价格 + 真实复权因子

Backward compatible: old "qfq" data treated as raw × factor=1.0
```

## 关键配置文件

| 文件 | 用途 |
|------|------|
| `D:\AgentAlpha\my-bot\scripts\register-all-tasks.bat` | 注册所有 Task Scheduler 任务 |
| `D:\AgentAlpha\my-bot\scripts\update-tasks.ps1` | 批量更新任务密码 |
| `D:\AgentAlpha\my-bot\portfolio.json` | 持仓/交易记录 (唯一可信源) |
| `D:\AgentAlpha\signal-factory\config\backtest.yaml` | 回测参数 |
| `D:\AgentAlpha\signal-factory\config\data.yaml` | 数据路径配置 |
| `C:\Users\USERNAME\.wslconfig` | WSL2 配置 (P2-9) |

## a-stock-data 补充数据流（v4.5 新增）

> 2026-11-11 | 原则：轻量补充、增量覆盖、不侵入现有管线
>
> Skill 安装：`~/.agents/skills/a-stock-data/SKILL.md`（第三方，V3.10.4；安装方式见 `docs/SKILLS.md`）
> 注：a-stock-data 集成方案未随本仓库发布，此处仅保留接口说明。

### 三层补充架构

```
a-stock-data Skill (15层·87端点·34数据源·零鉴权)
         │
         ├─ §13 期货大宗 → 厄尔尼诺管线 fallback
         │     └─ fetch_el_nino_futures.py: akshare 失败 → Sina HTTP direct
         │     └─ 数据通道: P0(棕榈油)/RU0(橡胶)/SR0(白糖) 新浪日K
         │
         ├─ §3 研报 → Gate 2 拥挤度 + 基本面
         │     └─ bull-bear-debate-gate: 分析师覆盖计数 + 评级分布
         │     └─ 修复: Wind MCP 迁移遗留的数据源缺口（2026-09-14）
         │
         └─ §4 龙虎榜/解禁 → 主题策略 + 风险日历
               ├─ a-share-primary-theme-identification: 龙虎榜席位 → theme_heat 量化
               └─ data/output/lockup_calendar.json: 未来90天解禁事件 → 盘前风险提示
```

### 数据流（不影响现有管线）

```
现有 cron 管线（不变）              a-stock-data 补充（新增）
══════════════════════              ══════════════════════

16:00 SignalFactory                   —
  ├─ akshare 健康检查                 —
  ├─ 日K数据更新                      —
  └─ ML Ensemble + Sync               —
       │
       ▼
signal-gen Agent                ★ §4 龙虎榜席位数据（主题候选时按需）
       │
       ▼
debate-judge Agent              ★ §3 研报覆盖计数+评级分布（Gate 2 拥挤度）
                                ★ §3 盈利预测变动（Gate 2 基本面）
       │
       ▼
risk-checker Agent              ★ lockup_calendar.json（持仓解禁预警）

每周六 Evolution-Scan (11:00)   ★ 刷新 lockup_calendar.json
每周一/四 ElNino (08:00)        ★ akshare 失败 → Sina HTTP fallback
```

### 关键文件

| 文件 | 用途 | 数据源 |
|------|------|--------|
| `data/output/el_nino_futures.json` | 厄尔尼诺商品期货（三品种日K+信号） | akshare（主）/ Sina HTTP（fallback） |
| `data/output/lockup_calendar.json` | 限售解禁 90 天前瞻日历 | a-stock-data §4 |
| `data/schemas/lockup_calendar.schema.json` | 解禁日历 JSON Schema | — |
| `scripts/fetch_el_nino_futures.py` | 期货数据采集（含 Sina fallback） | akshare → Sina HTTP |
| `~/.agents/skills/a-share-primary-theme-identification/SKILL.md` | 主题识别数据源指引（含龙虎榜） | a-stock-data §4 |
| `my-bot/.agents/skills/bull-bear-debate-gate/SKILL.md` | Gate 2 数据源指引（含研报/两融） | a-stock-data §3/§5 |