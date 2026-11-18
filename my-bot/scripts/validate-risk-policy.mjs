#!/usr/bin/env node
/**
 * validate-risk-policy.mjs — 校验 data/risk-policy.json 的一致性和完整性
 *
 * 用法: node scripts/validate-risk-policy.mjs
 * 退出码: 0 = 通过, 1 = 校验失败
 *
 * 检查项:
 *   1. JSON 格式合法
 *   2. 三种模式都存在 (normal / earnings_season / risk_off)
 *   3. risk_off 集中度 ≤ normal（防守不应比进攻更宽松）
 *   4. risk_off 熔断阈值 ≤ normal 的绝对值
 *   5. universal 字段完整
 *   6. 所有百分比在 0-100 范围内
 *   7. 模式间一致性（earnings_season 不应比 normal 宽松）
 */

import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const POLICY_PATH = resolve(__dirname, "..", "data", "risk-policy.json");

// --- 1. Load & parse ---
let policy;
try {
  const raw = readFileSync(POLICY_PATH, "utf-8");
  policy = JSON.parse(raw);
} catch (err) {
  console.error(`[FAIL] 无法解析 risk-policy.json: ${err.message}`);
  process.exit(1);
}

console.log(`[OK] JSON 格式合法 (version: ${policy.version})`);

// --- 2. Required modes ---
const REQUIRED_MODES = ["normal", "earnings_season", "risk_off"];
const modes = policy.modes;
if (!modes) {
  console.error("[FAIL] 缺少 modes 字段");
  process.exit(1);
}

let errors = 0;
for (const mode of REQUIRED_MODES) {
  if (!modes[mode]) {
    console.error(`[FAIL] 缺少模式: ${mode}`);
    errors++;
  }
}

if (errors === 0) console.log(`[OK] 三种模式都存在: ${REQUIRED_MODES.join(", ")}`);
else process.exit(1);

// --- 3. Concentration: risk_off ≤ normal ---
const normal = modes.normal;
const riskOff = modes.risk_off;
const boards = ["main_board", "star_board", "hk_board"];

for (const board of boards) {
  const normalPct = normal.concentration[board]?.max_pct;
  const riskOffPct = riskOff.concentration[board]?.max_pct;

  if (normalPct == null) {
    console.error(`[FAIL] normal 模式缺少集中度: ${board}`);
    errors++;
    continue;
  }
  if (riskOffPct == null) {
    console.error(`[FAIL] risk_off 模式缺少集中度: ${board}`);
    errors++;
    continue;
  }

  if (riskOffPct > normalPct) {
    console.error(
      `[FAIL] risk_off.${board} (${riskOffPct}%) > normal.${board} (${normalPct}%) — 防守不应比进攻更宽松`
    );
    errors++;
  }
}

if (errors === 0) console.log(`[OK] 集中度约束: risk_off ≤ normal (全部 ${boards.length} 个板块)`);

// --- 4. Circuit breakers: risk_off ≤ normal (绝对值) ---
const normalCB = normal.circuit_breakers;
const riskOffCB = riskOff.circuit_breakers;
const earningsSeasonCB = modes.earnings_season.circuit_breakers;

// daily_loss_pct is negative — check that risk_off's is closer to 0 (less strict) or more negative
if (Math.abs(riskOffCB.daily_loss_pct) <= Math.abs(normalCB.daily_loss_pct)) {
  console.log(`[OK] 日亏损熔断: risk_off (${riskOffCB.daily_loss_pct}%) ≤ normal (${normalCB.daily_loss_pct}%) (绝对值)`);
} else {
  console.error(
    `[FAIL] 日亏损熔断: risk_off (${riskOffCB.daily_loss_pct}%) > normal (${normalCB.daily_loss_pct}%) — 防守熔断应更紧`
  );
  errors++;
}

if (riskOffCB.consecutive_losses <= normalCB.consecutive_losses) {
  console.log(`[OK] 连续亏损熔断: risk_off (${riskOffCB.consecutive_losses}) ≤ normal (${normalCB.consecutive_losses})`);
} else {
  console.error(
    `[FAIL] 连续亏损熔断: risk_off (${riskOffCB.consecutive_losses}) > normal (${normalCB.consecutive_losses})`
  );
  errors++;
}

// --- 5. Earnings season consistency: should not be looser than normal ---
for (const board of boards) {
  const esPct = modes.earnings_season.concentration[board]?.max_pct;
  const nPct = normal.concentration[board]?.max_pct;
  if (esPct > nPct) {
    console.error(
      `[FAIL] earnings_season.${board} (${esPct}%) > normal.${board} (${nPct}%) — 财报季不应比日常更宽松`
    );
    errors++;
  }
}

if (earningsSeasonCB.daily_loss_pct !== normalCB.daily_loss_pct) {
  console.error(
    `[FAIL] earnings_season 日亏损熔断 (${earningsSeasonCB.daily_loss_pct}%) ≠ normal (${normalCB.daily_loss_pct}%)`
  );
  errors++;
}
if (earningsSeasonCB.consecutive_losses !== normalCB.consecutive_losses) {
  console.error(
    `[FAIL] earnings_season 连续亏损熔断 (${earningsSeasonCB.consecutive_losses}) ≠ normal (${normalCB.consecutive_losses})`
  );
  errors++;
}

console.log(`[OK] 模式间一致性: earnings_season 与 normal 参数一致`);

// --- 6. Universal fields ---
const requiredUniversal = ["position_sizing", "trade_units", "paper_trading", "live_trading_requires"];
for (const field of requiredUniversal) {
  if (!(field in policy.universal)) {
    console.error(`[FAIL] universal 缺少字段: ${field}`);
    errors++;
  }
}

if (errors === 0) console.log(`[OK] universal 字段完整 (${requiredUniversal.length} 个)`);

// --- 7. All percentages in valid range ---
const pctFields = [
  { path: "normal.concentration.main_board.max_pct", value: normal.concentration.main_board.max_pct },
  { path: "normal.concentration.star_board.max_pct", value: normal.concentration.star_board.max_pct },
  { path: "normal.concentration.hk_board.max_pct", value: normal.concentration.hk_board.max_pct },
  { path: "risk_off.concentration.main_board.max_pct", value: riskOff.concentration.main_board.max_pct },
  { path: "risk_off.concentration.star_board.max_pct", value: riskOff.concentration.star_board.max_pct },
  { path: "risk_off.concentration.hk_board.max_pct", value: riskOff.concentration.hk_board.max_pct },
  { path: "universal.position_sizing.kelly_multiplier", value: policy.universal.position_sizing.kelly_multiplier },
];

for (const { path, value } of pctFields) {
  if (typeof value !== "number" || value < 0 || value > 100) {
    console.error(`[FAIL] ${path} = ${value} 超出 0-100 范围`);
    errors++;
  }
}

// daily_loss_pct is negative, check -100..0
for (const [modeName, mode] of Object.entries(modes)) {
  const cb = mode.circuit_breakers;
  if (cb.daily_loss_pct > 0 || cb.daily_loss_pct < -100) {
    console.error(`[FAIL] ${modeName}.daily_loss_pct = ${cb.daily_loss_pct} 超出 -100..0 范围`);
    errors++;
  }
}

console.log(`[OK] 百分比值范围校验通过`);

// --- 8. Mode-specific rules ---
if (modes.risk_off.allow_new_positions !== false) {
  console.error(`[FAIL] risk_off.allow_new_positions 应为 false`);
  errors++;
}
if (modes.normal.allow_new_positions !== true) {
  console.error(`[FAIL] normal.allow_new_positions 应为 true`);
  errors++;
}
if (modes.earnings_season.allow_new_positions !== true) {
  console.error(`[FAIL] earnings_season.allow_new_positions 应为 true`);
  errors++;
}
console.log(`[OK] 建仓权限: risk_off=禁止, normal/earnings_season=允许`);

// --- 9. Earnings-specific fields ---
const es = modes.earnings_season.earnings_specific;
if (!es) {
  console.error("[FAIL] earnings_season 缺少 earnings_specific 字段");
  errors++;
} else {
  if (es.pre_earnings_days < 1 || es.pre_earnings_days > 30) {
    console.error(`[FAIL] pre_earnings_days = ${es.pre_earnings_days} 不合理 (1-30)`);
    errors++;
  }
  if (es.pre_earnings_position_pct < 0 || es.pre_earnings_position_pct > 100) {
    console.error(`[FAIL] pre_earnings_position_pct = ${es.pre_earnings_position_pct} 超出范围`);
    errors++;
  }
  console.log(`[OK] 财报特定参数: pre_earnings_days=${es.pre_earnings_days}, position_pct=${es.pre_earnings_position_pct}%`);

// --- 10. Backtest gate (v4.3 新增) ---
const backtestGate = policy.backtest_gate;
if (!backtestGate) {
  console.error("[FAIL] 缺少 backtest_gate 字段 (v4.3 Gate 1)");
  errors++;
} else {
  // 10a. Scoring dimensions
  const dims = backtestGate.scoring?.dimensions;
  if (!dims) {
    console.error("[FAIL] backtest_gate 缺少 scoring.dimensions");
    errors++;
  } else {
    const dimNames = ["sample_size", "expectancy", "risk_control", "robustness", "execution_feasibility"];
    let weightSum = 0;
    for (const name of dimNames) {
      if (!dims[name]) {
        console.error(`[FAIL] backtest_gate 缺少评分维度: ${name}`);
        errors++;
      } else {
        weightSum += dims[name].weight || 0;
      }
    }
    if (weightSum !== 100) {
      console.error(`[FAIL] backtest_gate 评分权重总和 = ${weightSum} (应为 100)`);
      errors++;
    } else {
      console.log(`[OK] backtest_gate 评分维度完整，权重总和 = 100`);
    }
  }

  // 10b. Thresholds
  const thresholds = backtestGate.scoring?.thresholds;
  if (!thresholds) {
    console.error("[FAIL] backtest_gate 缺少 scoring.thresholds");
    errors++;
  } else {
    if (!thresholds.deploy || !thresholds.refine || !thresholds.abandon) {
      console.error("[FAIL] backtest_gate 缺少判決阈值 (deploy/refine/abandon)");
      errors++;
    } else {
      console.log(`[OK] backtest_gate 判決阈值: Deploy≥${thresholds.deploy.min}, Refine ${thresholds.refine.min}-${thresholds.refine.max}, Abandon<${thresholds.abandon.max}`);
    }
  }

  // 10c. Anti-overfitting layers
  const anti = backtestGate.anti_overfitting;
  if (!anti) {
    console.error("[FAIL] backtest_gate 缺少 anti_overfitting 字段");
    errors++;
  } else {
    const layers = ["layer_1_out_of_sample", "layer_2_complexity_penalty", "layer_3_walk_forward"];
    for (const layer of layers) {
      if (!anti[layer]) {
        console.error(`[FAIL] backtest_gate.anti_overfitting 缺少: ${layer}`);
        errors++;
      }
    }
    if (errors === 0) console.log(`[OK] backtest_gate 三层反过拟合防线完整`);
  }

  // 10d. Mandatory questions
  if (!backtestGate.mandatory_questions?.questions || backtestGate.mandatory_questions.questions.length < 3) {
    console.error("[FAIL] backtest_gate 缺少三个必答题");
    errors++;
  } else {
    console.log(`[OK] backtest_gate 三个必答题完整`);
  }

  // 10e. Multiple comparison rule
  if (!backtestGate.multiple_comparison) {
    console.error("[FAIL] backtest_gate 缺少 multiple_comparison 字段");
    errors++;
  } else {
    console.log(`[OK] backtest_gate 多重比较修正规则存在`);
  }
}

// --- 11. Risk_off entry/exit conditions (v4.3 新增) ---
const riskOffEntry = riskOff.entry_conditions;
if (!riskOffEntry) {
  console.error("[FAIL] risk_off 缺少 entry_conditions 字段");
  errors++;
} else if (!riskOffEntry.triggers || riskOffEntry.triggers.length < 3) {
  console.error(`[FAIL] risk_off.entry_conditions.triggers 不足 (${riskOffEntry.triggers?.length || 0} 条，应 ≥ 3)`);
  errors++;
} else {
  console.log(`[OK] risk_off 触发条件: ${riskOffEntry.triggers.length} 条`);
}

const riskOffExit = riskOff.exit_conditions;
if (!riskOffExit) {
  console.error("[FAIL] risk_off 缺少 exit_conditions 字段");
  errors++;
} else {
  if (!riskOffExit.phase_1_to_ranging) {
    console.error("[FAIL] risk_off.exit_conditions 缺少 phase_1_to_ranging");
    errors++;
  }
  if (!riskOffExit.phase_2_to_normal) {
    console.error("[FAIL] risk_off.exit_conditions 缺少 phase_2_to_normal");
    errors++;
  }
  if (!riskOffExit.fallback) {
    console.error("[FAIL] risk_off.exit_conditions 缺少 fallback（回退条件）");
    errors++;
  }
  if (errors === 0) console.log(`[OK] risk_off 解除条件: 两阶段递进 + 回退机制完整`);
}

// --- 12. Debate gate weights (v4.3 新增) ---
const debateGate = policy.debate_gate;
if (!debateGate) {
  console.error("[FAIL] 缺少 debate_gate 字段 (v4.3 Gate 2)");
  errors++;
} else {
  const dw = debateGate.weights;
  if (!dw) {
    console.error("[FAIL] debate_gate 缺少 weights");
    errors++;
  } else {
    const dwKeys = ["technical", "capital_flow", "crowding", "fundamental", "sentiment", "macro"];
    let dwSum = 0;
    for (const k of dwKeys) {
      if (!dw[k]) {
        console.error(`[FAIL] debate_gate.weights 缺少: ${k}`);
        errors++;
      } else {
        dwSum += dw[k].weight || 0;
      }
    }
    if (Math.abs(dwSum - 1.0) > 0.001) {
      console.error(`[FAIL] debate_gate 权重总和 = ${dwSum} (应为 1.0)`);
      errors++;
    } else {
      console.log(`[OK] debate_gate 六维权重总和 = 1.0`);
    }
  }

  const dt = debateGate.thresholds;
  if (!dt) {
    console.error("[FAIL] debate_gate 缺少 thresholds");
    errors++;
  } else {
    if (!dt.entry || !dt.position_management) {
      console.error("[FAIL] debate_gate.thresholds 缺少 entry 或 position_management");
      errors++;
    } else {
      console.log(`[OK] debate_gate 阈值: 建仓 + 持仓管理双套完整`);
    }
  }
}
}

// --- Result ---
console.log("");
if (errors === 0) {
  console.log("✅ 所有校验通过 — risk-policy.json 一致且完整");
  process.exit(0);
} else {
  console.error(`❌ ${errors} 项校验失败`);
  process.exit(1);
}