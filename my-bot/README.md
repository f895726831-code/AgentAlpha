# AgentAlpha — AI Agent 决策管线（my-bot）

> **Pipeline v5.0** — 三层信号架构 + 三模式自适应 + 三道 Gate
> 自研 Agent 技能 3 个（第三方技能按需安装，见 [docs/SKILLS.md](../docs/SKILLS.md)）｜Wind MCP + Qlib 双引擎
> 仅用于研究与学习，默认 **Paper 模式**，不构成投资建议。

## 管线架构

```
Layer 0 宏观大类资产配置（11 类资产，HMM + MVO/BL）
   ↓
Layer 1 板块轮动 ML（31 个申万行业 GBDT/XGBoost）
   ↓
Layer 2 个股选择（Pipeline v5.0）
   Stage 0    全市场初筛（market_wide_scanner）
   Stage 0.3  主题扩展（theme_expand）
   Stage 1    环境判断 + 选股（7 策略）
   Gate 1     回测关（5 维评分 + 反过拟合）
   Stage 2    信号确认（跳空 / 放量归因）
   Gate 2     多空论战关（六维加权）
   Stage 3    交易执行（仓位 / 计划）
   Gate 3     风控关（集中度 / 熔断）
   Stage 4    风控退出（止损 / 止盈）
```

### 三道 Gate

| Gate | 位置 | 职能 |
|---|---|---|
| **Gate 1 回测关** | Stage 1→2 | 五维评分 + 三层反过拟合（样本外留出 / 复杂度惩罚 / 滚动窗口），通过 Deploy 才进入选股 |
| **Gate 2 多空论战关** | Stage 2→3 | 六维加权（技术 30% / 资金流 15% / 拥挤 10% / 基本面 20% / 情绪 15% / 宏观 10%），红黄绿灯决策 |
| **Gate 3 风控关** | Stage 4 | 仓位 / 集中度 / 熔断校验，参数随模式动态调整 |

### 三模式自适应

| 优先级 | 模式 | 触发条件 | 行为 |
|---|---|---|---|
| 1 | `risk_off` | market_regime 判为"防守" | 选股降级为仅观察、不建仓，风控收紧 |
| 2 | `earnings_season` | 月份 ∈ {1,2,3,4,7,8,10} | 激活财报子管线，盘前/盘后增强 |
| 3 | `normal` | 其他 | 标准五阶段管线 |

## 与 Signal Factory 集成

通过 `scripts/sync_signal_factory.py` 与 `../signal-factory` 松耦合：

- **信号**：`alpha_signals*.json`（含 `@contract` 元数据、universe、staleness）、`sector_signals.json`、`asset_allocation.json`
- **模型**：GBDT / XGBoost / CatBoost 集成，Alpha158/360 特征，CSI 300 / 500 股票池
- **集成方式**：ML Top 50 ∩ Agent 选股 = 高置信优先；信号缺失或过期时降级为纯 Agent 定性选股

## 快速开始

```bash
cd my-bot
npm ci
npm test               # 97 tests
node bin/cli.js init   # 初始化
node bin/cli.js doctor # 环境自检
```

数据与路径：

- `scripts/qlib/config.json`：Qlib 根目录、bin 数据路径（按本机修改）
- `data/`：只保留默认配置（`risk-policy.json`、`sector-map.json`、`blacklist.json` 等），运行数据被 `.gitignore` 排除
- 第三方技能安装：`npx skills add Wind-Information-Co-Ltd/wind-skills --skill <name> -g -y`

## 目录

```
my-bot/
├── src/            # agents / strategies / data
├── scripts/        # 数据同步、信号追踪、Qlib 桥、WSL2 因子实验
├── bin/            # CLI
├── docs/           # 管线架构、运维、排障、报告模板
├── tests/          # 单元测试
└── data/           # 默认配置（不含个人数据）
```

## 免责声明

本项目仅供研究与学习，不构成投资建议。实盘交易风险由使用者自行承担；回测表现不代表未来收益。

## License

MIT
