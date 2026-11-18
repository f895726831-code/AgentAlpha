#!/usr/bin/env node
/**
 * 情景预判 — 前置条件权重计算器
 *
 * 将 6 个前置条件维度映射为权重，调整四情景概率分布。
 * 设计文档: reports/research/2026-07-27-scenario-prediction-weights.md
 *
 * 用法:
 *   node scripts/adjust-scenario-probs.mjs \
 *     --prev-change -2.99 \
 *     --streak 3 \
 *     --vol-change -36 \
 *     --weekday 5 \
 *     --is-extreme false \
 *     --regime attack
 *
 *   # 也可通过管道传入基础概率:
 *   echo '{"baseA":40,"baseB":30,"baseC":25,"baseD":5}' | node scripts/adjust-scenario-probs.mjs --prev-change -2.99 ...
 *
 * 输出: JSON { scenarios: [...], weights: {...}, diagnosis: [...] }
 */

import { parseArgs } from 'node:util';
import { readFileSync } from 'node:fs';

// ─── 权重表（来源: 2026-07-27-scenario-prediction-weights.md） ───

const WEIGHTS = {
  // 3.1 前日涨跌幅
  amplitude: {
    'surge_up':     { A: 0.7, B: 0.6, C: 1.8, D: 1.2, label: '暴涨 > 8%' },
    'big_up':       { A: 0.8, B: 0.7, C: 1.5, D: 1.1, label: '大涨 5-8%' },
    'small_up':     { A: 1.0, B: 1.1, C: 0.9, D: 0.9, label: '小涨 2-5%' },
    'tiny_up':      { A: 1.2, B: 0.9, C: 0.9, D: 0.8, label: '微涨 0-2%' },
    'tiny_down':    { A: 1.2, B: 0.9, C: 0.9, D: 0.8, label: '微跌 0~-2%' },
    'small_down':   { A: 1.0, B: 1.1, C: 0.9, D: 0.9, label: '小跌 -2~-5%' },
    'big_down':     { A: 0.8, B: 0.7, C: 1.5, D: 1.1, label: '大跌 -5~-8%' },
    'surge_down':   { A: 0.7, B: 0.6, C: 1.8, D: 1.2, label: '暴跌 < -8%' },
  },

  // 3.2 连续涨跌天数
  streak: {
    1:  { A: 0.9, B: 1.0, C: 1.1, D: 0.9, label: '1 天' },
    2:  { A: 0.9, B: 1.1, C: 1.0, D: 0.9, label: '2 天' },
    3:  { A: 0.8, B: 1.0, C: 1.2, D: 1.0, label: '3 天' },
    4:  { A: 0.7, B: 0.8, C: 1.4, D: 1.1, label: '4 天' },
    5:  { A: 0.5, B: 0.6, C: 1.5, D: 1.3, label: '5+ 天' },
  },

  // 3.3 量能变化 (vs 5日均量)
  volume: {
    'surge_up':     { A: 0.5, B: 0.8, C: 1.0, D: 2.0, label: '放量 > 100%' },
    'big_up':       { A: 0.7, B: 1.3, C: 0.8, D: 1.3, label: '放量 50-100%' },
    'small_up':     { A: 0.9, B: 1.2, C: 0.9, D: 1.0, label: '放量 20-50%' },
    'normal':       { A: 1.0, B: 1.0, C: 1.0, D: 1.0, label: '正常 ±20%' },
    'small_down':   { A: 1.1, B: 0.8, C: 1.2, D: 0.9, label: '缩量 20-40%' },
    'big_down':     { A: 1.3, B: 0.6, C: 1.1, D: 0.8, label: '缩量 40-60%' },
    'surge_down':   { A: 1.5, B: 0.5, C: 1.0, D: 0.7, label: '缩量 > 60%' },
  },

  // 3.4 星期效应
  weekday: {
    1: { A: 1.0, B: 1.0, C: 1.0, D: 1.1, label: '周一' },
    2: { A: 1.0, B: 1.1, C: 0.9, D: 0.9, label: '周二' },
    3: { A: 1.1, B: 0.9, C: 1.0, D: 0.9, label: '周三' },
    4: { A: 1.0, B: 1.0, C: 1.0, D: 1.0, label: '周四' },
    5: { A: 0.8, B: 0.8, C: 1.3, D: 1.0, label: '周五' },
  },

  // 3.5 历史极值
  extreme: {
    'high_gain':    { A: 0.6, B: 0.5, C: 1.8, D: 1.3, label: '涨幅 > 95% 分位' },
    'high_loss':    { A: 0.6, B: 0.5, C: 1.8, D: 1.3, label: '跌幅 > 95% 分位' },
    'high_volume':  { A: 0.5, B: 0.8, C: 1.0, D: 2.0, label: '成交额 > 95% 分位' },
    'none':         { A: 1.0, B: 1.0, C: 1.0, D: 1.0, label: '正常范围' },
  },

  // 3.6 市场判档
  regime: {
    'attack':    { A: 1.0, B: 1.1, C: 0.9, D: 1.0, label: '🟢 进攻' },
    'neutral':   { A: 1.1, B: 0.9, C: 1.0, D: 0.9, label: '🟡 震荡' },
    'defense':   { A: 0.9, B: 0.8, C: 1.2, D: 1.1, label: '🔴 防守' },
  },
};

// ─── 分类函数 ───

function classifyAmplitude(prevChange) {
  if (prevChange > 8) return 'surge_up';
  if (prevChange > 5) return 'big_up';
  if (prevChange > 2) return 'small_up';
  if (prevChange > 0) return 'tiny_up';
  if (prevChange >= -2) return 'tiny_down';
  if (prevChange >= -5) return 'small_down';
  if (prevChange >= -8) return 'big_down';
  return 'surge_down';
}

function classifyStreak(days) {
  if (days >= 5) return 5;
  return Math.max(1, Math.floor(days));
}

function classifyVolume(volChange) {
  if (volChange > 100) return 'surge_up';
  if (volChange > 50) return 'big_up';
  if (volChange > 20) return 'small_up';
  if (volChange >= -20) return 'normal';
  if (volChange >= -40) return 'small_down';
  if (volChange >= -60) return 'big_down';
  return 'surge_down';
}

function classifyExtreme(isExtreme, prevChange) {
  if (!isExtreme) return 'none';
  if (prevChange > 0) return 'high_gain';
  return 'high_loss';
}

function classifyRegime(regime) {
  const map = { attack: 'attack', neutral: 'neutral', defense: 'defense', risk_off: 'defense' };
  return map[regime] || 'neutral';
}

// ─── 主计算 ───

function adjust(baseProbs, prevChange, streak, volChange, weekday, isExtreme, regime) {
  const ampClass = classifyAmplitude(prevChange);
  const strClass = classifyStreak(streak);
  const volClass = classifyVolume(volChange);
  const extClass = classifyExtreme(isExtreme, prevChange);
  const regClass = classifyRegime(regime);

  const dims = [
    { name: '前日涨跌幅', key: 'amplitude', cls: ampClass, weights: WEIGHTS.amplitude[ampClass] },
    { name: '连续天数',   key: 'streak',    cls: String(strClass), weights: WEIGHTS.streak[strClass] },
    { name: '量能变化',   key: 'volume',    cls: volClass, weights: WEIGHTS.volume[volClass] },
    { name: '星期效应',   key: 'weekday',   cls: String(weekday), weights: WEIGHTS.weekday[weekday] },
    { name: '历史极值',   key: 'extreme',   cls: extClass, weights: WEIGHTS.extreme[extClass] },
    { name: '市场判档',   key: 'regime',    cls: regClass, weights: WEIGHTS.regime[regClass] },
  ];

  // 计算综合权重
  const combined = { A: 1.0, B: 1.0, C: 1.0, D: 1.0 };
  const detail = [];

  for (const dim of dims) {
    for (const s of ['A', 'B', 'C', 'D']) {
      combined[s] *= dim.weights[s];
    }
    detail.push({
      dimension: dim.name,
      classification: dim.weights.label,
      weights: dim.weights,
    });
  }

  // 调整概率
  const raw = {};
  let sum = 0;
  for (const s of ['A', 'B', 'C', 'D']) {
    raw[s] = baseProbs[s] * combined[s];
    sum += raw[s];
  }

  const adjusted = {};
  const changes = {};
  for (const s of ['A', 'B', 'C', 'D']) {
    adjusted[s] = sum > 0 ? Math.round((raw[s] / sum) * 100) : 0;
    changes[s] = adjusted[s] - baseProbs[s];
  }

  // 诊断: 找出影响最大的维度
  const diagnosis = [];
  for (const s of ['A', 'B', 'C', 'D']) {
    const impactful = dims
      .filter(d => d.weights[s] !== 1.0)
      .sort((a, b) => Math.abs(b.weights[s] - 1.0) - Math.abs(a.weights[s] - 1.0))
      .slice(0, 3)
      .map(d => `${d.name}(${d.weights.label}: ×${d.weights[s].toFixed(1)})`);
    if (impactful.length > 0) {
      diagnosis.push({ scenario: s, change: changes[s], drivers: impactful });
    }
  }

  return {
    input: { prevChange, streak, volChange, weekday, isExtreme, regime },
    dimensions: detail,
    base_probs: baseProbs,
    combined_weights: Object.fromEntries(
      Object.entries(combined).map(([k, v]) => [k, Math.round(v * 100) / 100])
    ),
    adjusted_probs: adjusted,
    changes,
    diagnosis,
  };
}

// ─── CLI ───

function parseCLI() {
  const { values } = parseArgs({
    options: {
      'prev-change':  { type: 'string' },
      'streak':       { type: 'string' },
      'vol-change':   { type: 'string' },
      'weekday':      { type: 'string' },
      'is-extreme':   { type: 'string' },
      'regime':       { type: 'string' },
      'base-a':       { type: 'string' },
      'base-b':       { type: 'string' },
      'base-c':       { type: 'string' },
      'base-d':       { type: 'string' },
      'json':         { type: 'boolean', default: false },
    },
    allowPositionals: true,
  });

  const prevChange = parseFloat(values['prev-change']);
  const streak = parseInt(values['streak']);
  const volChange = parseFloat(values['vol-change']);
  const weekday = parseInt(values['weekday']);
  const isExtreme = values['is-extreme'] === 'true';
  const regime = values['regime'] || 'neutral';

  if (isNaN(prevChange) || isNaN(streak) || isNaN(volChange) || isNaN(weekday)) {
    console.error('Usage: node adjust-scenario-probs.mjs --prev-change <pct> --streak <days> --vol-change <pct> --weekday <1-5> [--is-extreme true] [--regime attack|neutral|defense] [--base-a 40 --base-b 30 --base-c 20 --base-d 10]');
    process.exit(1);
  }

  // 基础概率: CLI 参数优先，否则尝试从 stdin 读取 JSON
  let baseProbs = null;
  if (values['base-a'] !== undefined) {
    baseProbs = {
      A: parseInt(values['base-a']) || 40,
      B: parseInt(values['base-b']) || 30,
      C: parseInt(values['base-c']) || 20,
      D: parseInt(values['base-d']) || 10,
    };
  }

  if (!baseProbs) {
    try {
      const stdin = readFileSync(0, 'utf-8').trim();
      if (stdin) {
        const parsed = JSON.parse(stdin);
        baseProbs = {
          A: parsed.baseA || 40,
          B: parsed.baseB || 30,
          C: parsed.baseC || 20,
          D: parsed.baseD || 10,
        };
      }
    } catch { /* no stdin */ }
  }

  if (!baseProbs) {
    baseProbs = { A: 40, B: 30, C: 20, D: 10 };
  }

  return { prevChange, streak, volChange, weekday, isExtreme, regime, baseProbs };
}

// ─── Main ───

const args = parseCLI();
const result = adjust(args.baseProbs, args.prevChange, args.streak, args.volChange, args.weekday, args.isExtreme, args.regime);

console.log(JSON.stringify(result, null, 2));