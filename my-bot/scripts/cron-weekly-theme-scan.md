# 周度主题扩展扫描 — 周六执行

> 触发: 每周六 10:00 | 模式: Python 脚本 + LLM 纯度评分
> 用途: 扫描所有活跃主题，更新候选成分股，发现新标的
>
> ## 执行方式

### 方式 A: Windows 任务计划 (推荐)

```
程序: python
参数: -I scripts/theme_expand.py --scan-active
起始于: D:\AgentAlpha\my-bot
触发: 每周六 10:00
```

### 方式 B: 手动执行

```bash
cd D:/AgentAlpha/my-bot
python -I scripts/theme_expand.py --scan-active
```

---

## 任务流程

1. 加载 `data/theme_candidates.json` 中的所有活跃主题
2. 健康检查: `--health` 确认数据源可用层级
3. 逐主题重新获取成分股 + LLM 纯度评分
4. 对比新旧候选列表，标注新增/退出标的
5. 更新 `theme_candidates.json`
6. 对新 pending 候选自动生成 Gate 1 回测任务文件

---

## 输出

- `data/theme_candidates.json`: 更新后的候选池
- `data/gate1_jobs/gate1_<主题>_<日期>.json`: 新 pending 候选的 Gate 1 任务文件

---

## risk_off 期间行为

风险熔断期间 (`risk-policy.json` → `modes.risk_off.active = true`):

- **主题扩展继续执行** — 候选发现不依赖市场状态，熊市中更应积累候选池
- **Gate 1 回测暂停** — 不生成新的回测任务文件（避免浪费 backtester 算力，且 risk_off 禁止新建仓）
- **不自动 --promote** — 即使有 gate1_passed 候选，也不加入自选池
- **risk_off 解除后**: 批量运行 `--gate1-prepare` + backtester + `--promote` 一次性推进所有 pending 候选

检查 risk_off 状态:
```python
import json
with open("data/risk-policy.json") as f:
    rp = json.load(f)
is_risk_off = rp.get("modes", {}).get("risk_off", {}).get("active", False)
```