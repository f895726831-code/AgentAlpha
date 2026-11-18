#!/usr/bin/env node
/**
 * Gate 2 权重校准脚本
 *
 * 当累积足够 Gate 2 判決记录后，用网格搜索校准五维权重。
 * 数据来源: data/gate2-decisions.jsonl (每行一条 JSON 判決记录)
 *
 * 用法:
 *   # 记录一次 Gate 2 判決
 *   node scripts/calibrate-gate2-weights.mjs record --file data/gate2-decisions.jsonl < decision.json
 *
 *   # 运行网格搜索校准
 *   node scripts/calibrate-gate2-weights.mjs calibrate --file data/gate2-decisions.jsonl
 *
 *   # 查看当前校准状态
 *   node scripts/calibrate-gate2-weights.mjs status --file data/gate2-decisions.jsonl
 */

import { readFileSync, writeFileSync, appendFileSync, existsSync } from 'node:fs';
import { parseArgs } from 'node:util';

// ─── 当前权重（基准） ───

const CURRENT_WEIGHTS = {
  technical: 0.30,
  capital_flow: 0.25,
  fundamental: 0.20,
  sentiment: 0.15,
  macro: 0.10,
};

const DIMENSIONS = ['technical', 'capital_flow', 'fundamental', 'sentiment', 'macro'];
const WEIGHT_STEP = 0.05;
const WEIGHT_MIN = 0.05;
const WEIGHT_MAX = 0.50;

// ─── 判決记录 ───

function recordDecision(filePath) {
  const stdin = readFileSync(0, 'utf-8').trim();
  if (!stdin) {
    console.error('Error: no decision JSON provided via stdin');
    process.exit(1);
  }

  let decision;
  try {
    decision = JSON.parse(stdin);
  } catch {
    console.error('Error: invalid JSON');
    process.exit(1);
  }

  // Validate required fields
  const required = ['date', 'symbol', 'scenario', 'scores', 'net_score', 'verdict'];
  for (const field of required) {
    if (!(field in decision)) {
      console.error(`Error: missing required field: ${field}`);
      process.exit(1);
    }
  }

  appendFileSync(filePath, JSON.stringify(decision) + '\n', 'utf-8');
  console.log(`Decision recorded: ${decision.symbol} ${decision.verdict} (${decision.scenario})`);
}

// ─── 加载判決 ───

function loadDecisions(filePath) {
  if (!existsSync(filePath)) {
    return [];
  }

  const content = readFileSync(filePath, 'utf-8').trim();
  if (!content) return [];

  return content.split('\n')
    .filter(line => line.trim())
    .map(line => {
      try { return JSON.parse(line); } catch { return null; }
    })
    .filter(Boolean);
}

// ─── 网格搜索权重 ───

function generateWeightCombinations() {
  const combinations = [];

  function recurse(dimIdx, remaining, current) {
    if (dimIdx === DIMENSIONS.length - 1) {
      // Last dimension: fill remaining weight
      const rounded = Math.round(remaining / WEIGHT_STEP) * WEIGHT_STEP;
      if (rounded >= WEIGHT_MIN && rounded <= WEIGHT_MAX) {
        const combo = { ...current };
        combo[DIMENSIONS[dimIdx]] = rounded;
        combinations.push(combo);
      }
      return;
    }

    for (let w = WEIGHT_MIN; w <= Math.min(WEIGHT_MAX, remaining - (DIMENSIONS.length - dimIdx - 1) * WEIGHT_MIN); w += WEIGHT_STEP) {
      const rounded = Math.round(w * 100) / 100;
      const next = { ...current };
      next[DIMENSIONS[dimIdx]] = rounded;
      recurse(dimIdx + 1, remaining - rounded, next);
    }
  }

  recurse(0, 1.0, {});
  return combinations;
}

function calcNetScore(decision, weights) {
  let bullScore = 0;
  let bearScore = 0;

  for (const dim of DIMENSIONS) {
    const w = weights[dim] || 0;
    const scores = decision.scores[dim];
    if (scores) {
      bullScore += w * (scores.bull || 0);
      bearScore += w * (scores.bear || 0);
    }
  }

  return bullScore - bearScore;
}

function getVerdict(netScore) {
  if (netScore >= 2.0) return 'green';
  if (netScore >= 0.5) return 'yellow';
  if (netScore > -0.5) return 'orange';
  return 'red';
}

function isCorrectVerdict(decision) {
  if (!decision.outcome || decision.outcome === 'pending') return null;
  return decision.outcome === 'correct';
}

function calibrate(filePath) {
  const decisions = loadDecisions(filePath);

  // Filter to decisions with known outcomes
  const withOutcomes = decisions.filter(d => d.outcome && d.outcome !== 'pending');

  if (withOutcomes.length < 5) {
    console.log(`Insufficient data: ${withOutcomes.length} decisions with known outcomes (need ≥ 5 for calibration, ≥ 20 for reliable results)`);
    console.log(`Total decisions: ${decisions.length} (${decisions.filter(d => !d.outcome || d.outcome === 'pending').length} pending)`);
    return;
  }

  console.log(`Calibrating with ${withOutcomes.length} decisions (${decisions.length} total)...`);
  console.log();

  const combinations = generateWeightCombinations();
  console.log(`Grid search space: ${combinations.length} weight combinations`);

  // Evaluate each combination
  let bestAccuracy = -1;
  let bestWeights = null;
  const results = [];

  for (const weights of combinations) {
    let correct = 0;
    let total = 0;

    for (const decision of withOutcomes) {
      const netScore = calcNetScore(decision, weights);
      const verdict = getVerdict(netScore);
      const actualCorrect = isCorrectVerdict(decision);

      if (actualCorrect === null) continue;

      // A verdict is "correct" if:
      // - green/yellow → correct outcome (profitable)
      // - orange/red → correct outcome (avoided loss)
      // This is a simplification; in reality we need to map verdict to outcome
      const predictedCorrect = (verdict === 'green' || verdict === 'yellow');
      if (predictedCorrect === actualCorrect) correct++;
      total++;
    }

    const accuracy = total > 0 ? correct / total : 0;
    results.push({ weights, accuracy, correct, total });

    if (accuracy > bestAccuracy) {
      bestAccuracy = accuracy;
      bestWeights = weights;
    }
  }

  // Sort by accuracy
  results.sort((a, b) => b.accuracy - a.accuracy);

  // Report top 10
  console.log('Top 10 weight combinations:');
  console.log('Rank | Accuracy | Tech | Flow | Fund | Sent | Macro');
  console.log('-----|----------|------|------|------|------|------');
  for (let i = 0; i < Math.min(10, results.length); i++) {
    const r = results[i];
    const w = r.weights;
    console.log(`  ${i + 1}  |  ${(r.accuracy * 100).toFixed(1)}%   | ${(w.technical*100).toFixed(0)}% | ${(w.capital_flow*100).toFixed(0)}% | ${(w.fundamental*100).toFixed(0)}% | ${(w.sentiment*100).toFixed(0)}% | ${(w.macro*100).toFixed(0)}%`);
  }

  console.log();
  console.log('Current weights accuracy:');
  let currentCorrect = 0;
  let currentTotal = 0;
  for (const decision of withOutcomes) {
    const netScore = calcNetScore(decision, CURRENT_WEIGHTS);
    const verdict = getVerdict(netScore);
    const actualCorrect = isCorrectVerdict(decision);
    if (actualCorrect === null) continue;
    const predictedCorrect = (verdict === 'green' || verdict === 'yellow');
    if (predictedCorrect === actualCorrect) currentCorrect++;
    currentTotal++;
  }
  const currentAccuracy = currentTotal > 0 ? currentCorrect / currentTotal : 0;
  console.log(`  ${(currentAccuracy * 100).toFixed(1)}% (${currentCorrect}/${currentTotal})`);

  console.log();
  console.log(`Best accuracy: ${(bestAccuracy * 100).toFixed(1)}% (improvement: ${((bestAccuracy - currentAccuracy) * 100).toFixed(1)}pp)`);

  if (withOutcomes.length < 20) {
    console.log();
    console.log('⚠️  WARNING: < 20 decisions with outcomes. Results are indicative only.');
    console.log('   Do NOT update weights until 20+ decisions are accumulated.');
  }

  // Output best weights as JSON
  console.log();
  console.log('Best weights (JSON):');
  console.log(JSON.stringify(bestWeights, null, 2));
}

// ─── 状态报告 ───

function status(filePath) {
  const decisions = loadDecisions(filePath);

  const byScenario = { entry: [], add: [], reduce: [] };
  const byVerdict = { green: [], yellow: [], orange: [], red: [] };
  const byOutcome = { correct: [], wrong: [], pending: [] };

  for (const d of decisions) {
    if (byScenario[d.scenario]) byScenario[d.scenario].push(d);
    if (byVerdict[d.verdict]) byVerdict[d.verdict].push(d);
    const outcome = d.outcome || 'pending';
    if (byOutcome[outcome]) byOutcome[outcome].push(d);
  }

  console.log('=== Gate 2 Decision Database Status ===');
  console.log();
  console.log(`Total decisions: ${decisions.length}`);
  console.log(`With known outcome: ${byOutcome.correct.length + byOutcome.wrong.length}`);
  console.log(`Pending: ${byOutcome.pending.length}`);
  console.log();
  console.log('By scenario:');
  for (const [scenario, items] of Object.entries(byScenario)) {
    console.log(`  ${scenario}: ${items.length}`);
  }
  console.log();
  console.log('By verdict:');
  for (const [verdict, items] of Object.entries(byVerdict)) {
    console.log(`  ${verdict}: ${items.length}`);
  }
  console.log();
  console.log('By outcome:');
  console.log(`  correct: ${byOutcome.correct.length}`);
  console.log(`  wrong: ${byOutcome.wrong.length}`);
  console.log(`  pending: ${byOutcome.pending.length}`);

  const needed = Math.max(0, 20 - (byOutcome.correct.length + byOutcome.wrong.length));
  if (needed > 0) {
    console.log();
    console.log(`⚠️  Need ${needed} more decisions with known outcomes before calibration is reliable.`);
  } else {
    console.log();
    console.log('✅  Sufficient data for calibration. Run: node scripts/calibrate-gate2-weights.mjs calibrate');
  }

  // Recent decisions
  if (decisions.length > 0) {
    console.log();
    console.log('Recent decisions:');
    for (const d of decisions.slice(-5)) {
      const outcome = d.outcome || 'pending';
      console.log(`  ${d.date} ${d.symbol} ${d.verdict} (${d.scenario}) → ${outcome}`);
    }
  }
}

// ─── CLI ───

const { values, positionals } = parseArgs({
  options: {
    file: { type: 'string', default: 'data/gate2-decisions.jsonl' },
  },
  allowPositionals: true,
});

const command = positionals[0];

switch (command) {
  case 'record':
    recordDecision(values.file);
    break;
  case 'calibrate':
    calibrate(values.file);
    break;
  case 'status':
    status(values.file);
    break;
  default:
    console.log('Usage:');
    console.log('  node scripts/calibrate-gate2-weights.mjs record   < decision.json');
    console.log('  node scripts/calibrate-gate2-weights.mjs calibrate');
    console.log('  node scripts/calibrate-gate2-weights.mjs status');
    break;
}