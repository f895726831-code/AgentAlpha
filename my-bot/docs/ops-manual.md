# my-bot 运维手册

## 密码变更恢复流程

### 问题

Windows 登录密码变更后，Task Scheduler 中所有任务的存储凭证自动失效。症状：
- 任务计划器中任务状态显示 "The task has not yet run" 或 "Ready"
- 日志文件 (`logs/cron/startup-*.txt`) 无当日 STARTUP 条目
- 任务 **静默跳过**，不报错，不重试

### 恢复步骤

#### 1. 确认受影响

```powershell
# 检查 startup 日志是否有当日条目
cat D:\AgentAlpha\my-bot\logs\cron\startup-daily.txt | Select-String "2026-09-04"
cat D:\AgentAlpha\my-bot\logs\cron\startup-morning.txt | Select-String "2026-09-04"
cat D:\AgentAlpha\my-bot\logs\cron\startup-midday.txt | Select-String "2026-09-04"
```

#### 2. 批量更新密码

以管理员身份打开 PowerShell，运行：

```powershell
cd D:\AgentAlpha\my-bot\scripts
.\update-tasks.ps1
```

或指定密码：

```powershell
.\update-tasks.ps1 -Password "YourNewPassword"
```

#### 3. 手动补执行

检查当日哪些报告缺失，手动触发：

```powershell
# 盘前简报（08:35）
Get-Content scripts/cron-morning-brief.md | claude -p

# 午间复盘（11:30）
Get-Content scripts/cron-midday-review.md | claude -p

# 信号工厂（16:00）
d:\AgentAlpha\signal-factory\venv\Scripts\python.exe d:\AgentAlpha\signal-factory\scripts\daily_pipeline.py

# 盘后扫描（17:30）
Get-Content scripts/cron-daily-scan.md | claude -p
```

#### 4. 运行健康检查

```powershell
d:\AgentAlpha\signal-factory\venv\Scripts\python.exe scripts/healthcheck.py
```

### 预防措施

1. **密码变更后立即运行** `update-tasks.ps1`
2. **健康检查**（每日 17:45）会自动检测任务缺失并生成告警
3. 告警文件位于 `alerts/daily/` 目录

---

## 定时任务清单

| 任务 | 触发时间 | 频率 | 输出 |
|---|---|---|---|
| MyBot-FactorLab | 系统启动 | 每次 | — |
| MyBot-FactorLab-Wakeup | 07:55 | 每日 | — |
| MyBot-ModelRetrain-Weekly | 07:30 | 周一 | models/ |
| MyBot-FactorLab-Weekly-Compute | 08:00 | 周一 | factor_lab_weekly.json |
| MyBot-MorningBrief | 08:35 | 每日 | reports/daily/{date}-morning-brief.md |
| MyBot-MiddayReview | 11:30 | 每日 | reports/daily/{date}-midday.md |
| MyBot-SignalFactory | 16:00 | 每日 | data/output/alpha_signals.json |
| MyBot-DailyScan | 17:30 | 每日 | reports/daily/{date}-watchlist-scan.md |
| MyBot-HealthCheck | 17:45 | 每日 | alerts/daily/{date}.md |
| MyBot-FactorLab-Weekly-Research | 10:00 | 周六 | factor_lab_weekly.json |

---

## 每日流水线时间线

```
07:30  [Mon only] 模型重训 (CSI 500 + CSI 300)
07:55   factor-lab 启动 (WSL2 API server)
08:00  [Mon only] 周度因子计算+合成
08:35   盘前简报 (读取 alpha_signals.json)
11:30   午间复盘
16:00   信号工厂 (数据更新 → ML 集成 → 同步)
17:30   盘后扫描 (全量 watchlist 扫描)
17:45   健康检查 (心跳 + 端到端)
```

---

## 告警处理

### 告警级别

| 级别 | 含义 | 处理 |
|---|---|---|
| 🔴 CRITICAL | 任务未触发 | 检查 Task Scheduler 凭证、手动补执行 |
| 🟡 WARNING | 任务触发但报告不完整 | 检查报告内容、确认管道是否中途失败 |
| ✅ OK | 正常 | 无需处理 |

### 告警文件位置

- `alerts/daily/{date}.md` — 当日告警报告
- `alerts/healthcheck.log` — 历史告警日志

---

## 相关文件

- `scripts/update-tasks.ps1` — 批量更新任务密码
- `scripts/healthcheck.py` — 自动化健康检查
- `scripts/run-healthcheck.ps1` — 健康检查 PowerShell 包装器
- `scripts/register-all-tasks.bat` — 注册所有定时任务（需管理员权限）

---

## 手册触发命令

| 场景 | 命令 |
|------|------|
| 补充上次管道断点 | `python d:/AgentAlpha/signal-factory/scripts/update_daily.py --end 2026-11-04` |
| 手动运行完整管道 | `powershell -File d:\AgentAlpha\my-bot\scripts\run-signal-factory-pipeline.ps1` |
| 检查管道结果 | `cat D:\AgentAlpha\my-bot\logs\cron\signal-factory-pipeline.log \| Select-String "complete\|ERROR"` |
| 检查因子健康 | `python D:/AgentAlpha/my-bot/scripts/signal_tracker.py --report-only \| Select-String "平均\|胜率\|Sharpe"` |
| 周度因子开采(周六) | `powershell -File d:\AgentAlpha\my-bot\scripts\run-weekly-factor-lab.ps1` |
| 模型重训(周一) | `powershell -File d:\AgentAlpha\my-bot\scripts\run-weekly-model-retrain.ps1` |

### WSL2 管理

| 场景 | 命令 |
|------|------|
| 查看 WSL 状态 | `wsl --status` |
| 启动 factor-lab | `powershell -File d:\AgentAlpha\my-bot\scripts\start-factor-lab.ps1` |
| 检查 factor-lab 健康 | `wsl -d Ubuntu-22.04 -- bash -c "curl -s http://localhost:8700/api/v1/health"` |
| 重启 WSL VM | `wsl --shutdown` (下次任务自动启动) |

### 数据管道

| 场景 | 命令 |
|------|------|
| akshare 健康检查 | `python D:/AgentAlpha/signal-factory/scripts/akshare_health.py --quick` |
| 最近交易日数据日期 | `python -c "from scripts.update_daily import get_last_trading_day; print(get_last_trading_day())"` |
| 全量数据重拉 | `python D:/AgentAlpha/signal-factory/scripts/rebuild_cn_data.py` (耗时 30-60 分钟) |
| 单日补偿 | `python D:/AgentAlpha/signal-factory/scripts/backfill_baostock.py --start 2026-10-30 --end 2026-11-04` |

### ML 信号管理

| 场景 | 命令 |
|------|------|
| 查看 OOS 权重 | `python -c "from src.factors.oos_weight_guard import get_guard; import json; print(json.dumps(get_guard().get_summary('csi500'), indent=2))"` |
| 重置 OOS 权重 | 删除 `D:/AgentAlpha/signal-factory/data/output/oos_weight_state_*.json` (下周一重训恢复) |
| 信号追踪报告 | `python D:/AgentAlpha/my-bot/scripts/signal_tracker.py --report-only` |
| 基准回填 | `python D:/AgentAlpha/my-bot/scripts/signal_tracker.py --backfill-benchmark` |

### 任务计划器管理

| 场景 | 命令 |
|------|------|
| 注册所有任务 | 管理员 PowerShell: `D:\AgentAlpha\my-bot\scripts\register-all-tasks.bat` |
| 列出所有任务 | `schtasks /query /tn "MyBot-*" /v /fo list \| findstr /i "TaskName Status"` |
| 手动运行一个任务 | `schtasks /run /tn "MyBot-SignalFactory"` |
| 更新密码 | `cd D:\AgentAlpha\my-bot\scripts; .\update-tasks.ps1` |

---

## 关键配置文件

| 文件 | 位置 | 作用 |
|------|------|------|
| `.wslconfig` | `%USERPROFILE%\.wslconfig` | WSL2 VM 空闲超时、内存、CPU |
| `trading-calendar-2026.json` | `D:\AgentAlpha\my-bot\src\data\` | 交易日历 |
| `backtest.yaml` | `D:\AgentAlpha\signal-factory\config\` | 回测参数 |
| `portfolio.json` | `D:\AgentAlpha\my-bot\` | 持仓（唯一可信源） |
| `register-all-tasks.bat` | `D:\AgentAlpha\my-bot\scripts\` | Task Scheduler 任务注册 |
| `cron-*.md` | `D:\AgentAlpha\my-bot\scripts\` | LLM cron prompt 模板 |

## 相关文档

- [Pipeline Architecture Guide](pipeline-architecture.md) — 完整架构和数据流
- [Troubleshooting Guide](troubleshooting.md) — 常见故障修复