# Agent 技能（Skills）

AgentAlpha 的 Agent 管线使用「技能」作为提示词/工作流模板。仓库内**只保留自研技能**；
第三方技能（Wind、a-stock-data 等）不随仓库分发，请按需安装到用户级目录 `~/.agents/skills/`。

## 一、自研技能（仓库内，`my-bot/.agents/skills/`）

| 技能 | 用途 |
|---|---|
| `bull-bear-debate-gate` | 多空论战关（Gate 2），六维加权评分与红黄绿灯决策 |
| `market-wide-scanner` | Stage 0 全市场初筛，输出 Top 20 候选 |
| `qlib-alpha-signal` | 消费 Quant Signal Factory 的 ML Alpha 排名，与 Agent 选股取交集 |

## 二、第三方技能安装（不随仓库分发）

安装器为 [`skills`](https://github.com/vercel-labs/skills) CLI，安装到全局后位于 `~/.agents/skills/`：

```bash
# 万得 Wind 技能包（行情/财务/宏观/选股/文档等）
npx skills add Wind-Information-Co-Ltd/wind-skills --skill <name> -g -y

# 例：常用技能
npx skills add Wind-Information-Co-Ltd/wind-skills --skill wind-mcp-skill -g -y
npx skills add Wind-Information-Co-Ltd/wind-skills --skill market_regime_switch_skill -g -y
npx skills add Wind-Information-Co-Ltd/wind-skills --skill breakout_candidate_finder_skill -g -y
npx skills add Wind-Information-Co-Ltd/wind-skills --skill vcp_breakout_scan_skill -g -y
npx skills add Wind-Information-Co-Ltd/wind-skills --skill canslim_growth_scan_skill -g -y
```

国内网络可用 Gitee 镜像：

```bash
npx skills add https://gitee.com/wind_info/wind-skills.git --skill <name> -g -y
```

`a-stock-data`（Apache-2.0，第三方）：

```bash
npx skills add simonlin1212/a-stock-data -g -y
```

> 安装后如 CLI 不在默认路径，可通过环境变量覆盖：
> `WIND_MCP_CLI_PATH`（TypeScript 脚本）或 `WIND_MCP_CLI`（Python 脚本）。

## 三、使用约定

- 自研技能随仓库更新；第三方技能由上游维护，升级请重新执行 `npx skills add`。
- 第三方技能的版权归原作者，使用时请遵守其许可证；详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
- 未安装第三方技能时，依赖 Wind 数据的功能（如宏观 EDB 拉取、部分选股）会降级或跳过。
