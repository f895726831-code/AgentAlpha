# Troubleshooting Guide

> 2026-11-04 | 常见故障模式与修复

## 诊断入口

```powershell
# 查看当日流水线状态
cat D:\AgentAlpha\my-bot\logs\cron\signal-factory-pipeline.log | Select-String "2026-11-04"
cat D:\AgentAlpha\my-bot\logs\cron\startup-factor-lab.txt | Select-String "2026-11-04"

# 运行健康检查
cd D:\AgentAlpha\my-bot
python scripts/healthcheck.py
```

---

## 1. akshare EastMoney IP 封锁

**症状**: `stock_zh_a_hist()` / `stock_zh_a_spot_em()` 返回空或报错

**检测**:
```powershell
python D:/AgentAlpha/signal-factory/scripts/akshare_health.py --quick
# classification: BLOCKED → Tier 1 不可用
```

**自动降级**: `akshare_fallback.py` 自动切换到 baostock

**手动修复** (baostock 也失败时):
```powershell
cd D:/AgentAlpha/signal-factory
python scripts/backfill_baostock.py --start 2026-10-01 --end 2026-11-04
```

---

## 2. baostock 列名不一致

**症状**: KeyError 'date' / 'close' — pandas 找不到列

**根因**: baostock 返回中文列名 (`日期`, `收盘`)，akshare 返回英文 (`date`, `close`)

**已修复**: `_get_index_close()` 自动检测列名:
```python
date_col = "日期" if "日期" in df.columns else "date"
close_col = "收盘" if "收盘" in df.columns else "close"
```

**手动检测**:
```python
from src.data.akshare_fallback import stock_zh_index_daily_with_fallback
df = stock_zh_index_daily_with_fallback(symbol="sh000300", start_date="20261101", end_date="20261104")
print(df.columns.tolist())  # 应包含 date/日期 和 close/收盘
```

---

## 3. WSL2 factor-lab 不可达

**症状**: `sync_signal_factory.py` 报 "factor-lab 不可用"；weekly factor-lab 任务报 FATAL

**检测**:
```powershell
wsl --status
wsl -d Ubuntu-22.04 -- bash -c "curl -s --connect-timeout 3 http://localhost:8700/api/v1/health"
```

**自动修复**: 管线 Step 0.5 自动检测并重启 (P2-9)

**手动修复**:
```powershell
# 重启 factor-lab
powershell -File d:\AgentAlpha\my-bot\scripts\start-factor-lab.ps1

# 如 WSL 完全无响应，重启 WSL
wsl --shutdown
wsl -d Ubuntu-22.04
# systemd 会自动启动 factor-lab
```

**预防**: `.wslconfig` 已配置 `vmIdleTimeout=-1`，但 Windows 休眠后可能需手动唤醒

---

## 4. Task Scheduler 静默跳过

**症状**: 日志无当日 STARTUP 条目，任务显示 "Ready" 而非 "Running"

**根因**: Windows 密码变更后 Task Scheduler 凭证失效

**修复**:
```powershell
cd D:\AgentAlpha\my-bot\scripts
.\update-tasks.ps1
# 或指定新密码: .\update-tasks.ps1 -Password "NewPassword"
```

**预防**: 密码变更后立即运行 update-tasks.ps1

---

## 5. CSI300 数据过期

**症状**: 晨报/扫描中的 CSI300 收盘价与实际不符 (如 4011 vs 4105)

**根因**: `claude -p` 未调 Wind MCP 时静默降级到午间盘

**已修复** (2026-10-26):
- `_csi300_daily.json` 由 EOD cron 15:30 预生成
- 报告 prompt 强制优先读取此文件
- 文件不存在时标注 DEGRADED，不尝试其他端点

**检测**:
```powershell
python -c "import json; d=json.load(open('D:/AgentAlpha/my-bot/data/output/_csi300_daily.json')); print(d.get('data_date'), d.get('latest',{}).get('close'))"
```

---

## 6. update_daily.py baostock 用户未登录

**症状**: `update_daily.py` 中途报 "用户未登录"

**已修复**: `_batch_baostock_download()` 自动检测并 re-login (P0-3)

**如持续失败**: baostock 服务端可能维护，等待 30 分钟后重试

---

## 7. Qlib 模型文件路径冲突

**症状**: CSI300 模型覆盖 CSI500 模型，或反之

**已修复**: `_model_path()` 生成标的特定的路径:
```
models/gbdt_csi500_weekly.pkl
models/gbdt_csi300_weekly.pkl
```

---

## 8. Signal Factory 管线卡在某个 Step

**诊断**:
```powershell
# 查看哪个 Step 耗时最长
cat D:\AgentAlpha\my-bot\logs\cron\signal-factory-pipeline.log | Select-String "Step|ERROR|FATAL"

# 检查各步骤耗时
cat D:\AgentAlpha\my-bot\logs\cron\signal-factory-pipeline.log | Select-String "s\)"
```

**常见卡点**:
- Step 2 (ML Ensemble): Qlib 数据不完整 → `python scripts/sync_data.py`
- Step 3 (Sync): factor-lab 不可达 → 见 #3
- Step 5.1 (TiMi): akshare blocked → 见 #1

---

## 9. Signal Tracker 基准数据不更新

**症状**: `excess_csi300`/`excess_csi500` 为 None

**检测**:
```powershell
python D:/AgentAlpha/my-bot/scripts/signal_tracker.py --report-only
```

**手动回填历史**:
```powershell
python D:/AgentAlpha/my-bot/scripts/signal_tracker.py --backfill-benchmark
```

---

## 10. OOS Weight State 文件损坏

**症状**: `predict_ensemble(method="weighted_mean")` 报 JSON 解析错误

**修复**: 删除状态文件，下次重训自动重建
```powershell
Remove-Item D:/AgentAlpha/signal-factory/data/output/oos_weight_state_csi500.json
Remove-Item D:/AgentAlpha/signal-factory/data/output/oos_weight_state_csi300.json
# 下周一重训后自动恢复
```

**影响**: 删除后回退等权集成 (0.333/0.333/0.333)，无功能影响

---

## 快速诊断命令集

```powershell
# === FM_trade ===
# 当日 pipeline 是否完成
cat D:\AgentAlpha\my-bot\logs\cron\signal-factory-pipeline.log | Select-String "complete"

# WSL factor-lab 状态
wsl -d Ubuntu-22.04 -- bash -c "curl -s --connect-timeout 3 http://localhost:8700/api/v1/health"

# 信号追踪统计
python D:/AgentAlpha/my-bot/scripts/signal_tracker.py --report-only | Select-String "平均|胜率|Sharpe"

# === QSF ===
# akshare 健康
python D:/AgentAlpha/signal-factory/scripts/akshare_health.py --quick

# 最新数据日期
python -c "from scripts.update_daily import get_last_trading_day; print(get_last_trading_day())"

# OOS 权重状态
python -c "from src.factors.oos_weight_guard import get_guard; import json; print(json.dumps(get_guard().get_summary('csi500'), indent=2))"
```