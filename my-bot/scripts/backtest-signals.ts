#!/usr/bin/env node
// SPDX-License-Identifier: MIT
// 信号自动化回测 — 读取 signals.jsonl，对比验证结果
//
// 用法:
//   npx tsx scripts/backtest-signals.ts              # 全量回测报告
//   npx tsx scripts/backtest-signals.ts --ticker 002624.SZ  # 单标的
//   npx tsx scripts/backtest-signals.ts --session 2026-07-10 # 单日
//   npx tsx scripts/backtest-signals.ts --v-reversal          # 仅 V 反假信号分析
//   npx tsx scripts/backtest-signals.ts --delay               # 信号延迟分析
//   npx tsx scripts/backtest-signals.ts --markdown            # Markdown 表格输出

import {
  loadAllSignals,
  loadSignalsByTicker,
  loadSignalsBySession,
  computeBacktestStats,
  type SignalRecord,
  type BacktestStats,
} from '../src/data/signal-store.js';

// ── 格式化 ──────────────────────────────────────────────

function pct(value: number, decimals = 1): string {
  return `${value.toFixed(decimals)}%`;
}

function verdictEmoji(v: string): string {
  switch (v) {
    case 'correct': return '✅';
    case 'wrong': return '❌';
    case 'ambiguous': return '⚠️';
    default: return '⬜';
  }
}

// ── 回测报告 ────────────────────────────────────────────

function printHeader(title: string): void {
  console.log(`\n${'='.repeat(60)}`);
  console.log(`  ${title}`);
  console.log(`${'='.repeat(60)}`);
}

function printOverallStats(stats: BacktestStats): void {
  console.log(`\n## 总体统计\n`);
  console.log(`| 指标 | 值 |`);
  console.log(`|---|---|`);
  console.log(`| 信号总数 | ${stats.total} |`);
  console.log(`| 综合准确率 | ${pct(stats.overallAccuracy)} |`);
  console.log(`| 平均延迟 | ${stats.avgDelayMinutes !== null ? `${stats.avgDelayMinutes}分钟` : 'N/A'} |`);
  console.log(`| V 反假信号数 | ${stats.vReversalFalseSignals} |`);
}

function printBySignal(stats: BacktestStats): void {
  console.log(`\n## 按信号类型\n`);
  console.log(`| 信号 | 总数 | ✅ | ❌ | ⚠️ | 准确率 |`);
  console.log(`|---|---|---|---|---|---|`);
  for (const [sig, group] of Object.entries(stats.bySignal)) {
    const decided = group.correct + group.wrong;
    const acc = decided > 0 ? ((group.correct / decided) * 100).toFixed(1) : '—';
    console.log(`| ${sig} | ${group.total} | ${group.correct} | ${group.wrong} | ${group.ambiguous} | ${acc}% |`);
  }
}

function printByTicker(stats: BacktestStats): void {
  console.log(`\n## 按标的\n`);
  console.log(`| 标的 | 总数 | ✅ | ❌ | ⚠️ | 准确率 |`);
  console.log(`|---|---|---|---|---|---|`);
  for (const [ticker, group] of Object.entries(stats.byTicker)) {
    const decided = group.correct + group.wrong;
    const acc = decided > 0 ? ((group.correct / decided) * 100).toFixed(1) : '—';
    console.log(`| ${ticker} | ${group.total} | ${group.correct} | ${group.wrong} | ${group.ambiguous} | ${acc}% |`);
  }
}

function printSignalTable(signals: SignalRecord[]): void {
  console.log(`\n| 日期 | 标的 | 信号 | 置信度 | 信号价 | 次日收盘 | 次日涨跌 | 判定 | 延迟 | V反 |`);
  console.log(`|---|---|---|---|---|---|---|---|---|---|`);
  for (const s of signals) {
    const delay = s.delay_sessions !== null ? `${s.delay_sessions}天` : '—';
    const vRev = s.volume_check.v_reversal_detected ? '⚠️' : '—';
    console.log(
      `| ${s.session_date} | ${s.ticker} | ${s.signal} | ${s.confidence}% | ${s.signal_price} | ${s.next_day_close ?? '—'} | ${s.next_day_pct !== null ? pct(s.next_day_pct, 2) : '—'} | ${verdictEmoji(s.verdict)} | ${delay} | ${vRev} |`,
    );
  }
}

function printVReversalAnalysis(signals: SignalRecord[]): void {
  printHeader('V 反假信号分析');
  const vRevSignals = signals.filter((s) => s.volume_check.v_reversal_detected);
  const vRevWrong = vRevSignals.filter((s) => s.verdict === 'wrong');
  const vRevCorrect = vRevSignals.filter((s) => s.verdict === 'correct');

  console.log(`\nV 反信号总数: ${vRevSignals.length}`);
  console.log(`  正确: ${vRevCorrect.length} (${vRevSignals.length > 0 ? pct((vRevCorrect.length / vRevSignals.length) * 100) : '0%'})`);
  console.log(`  错误: ${vRevWrong.length} (${vRevSignals.length > 0 ? pct((vRevWrong.length / vRevSignals.length) * 100) : '0%'})`);

  if (vRevWrong.length > 0) {
    console.log(`\n### V 反假信号明细\n`);
    for (const s of vRevWrong) {
      console.log(`- ${s.session_date} ${s.ticker} ${s.signal}(${s.confidence}%) @ ${s.signal_price}: V反幅度 ${s.volume_check.v_reversal_magnitude_pct}%, 成交量 ${s.volume_check.volume_vs_5d_avg} 5日均量`);
    }
  }

  if (vRevSignals.length >= 5) {
    const falseRate = vRevSignals.length > 0 ? (vRevWrong.length / vRevSignals.length) * 100 : 0;
    console.log(`\n### V 反假信号率: ${pct(falseRate)}`);
    if (falseRate > 30) {
      console.log(`⚠️  V 反假信号率 > 30%，量价确认规则需要进一步收紧阈值`);
    }
  }
}

function printDelayAnalysis(signals: SignalRecord[]): void {
  printHeader('信号延迟分析');
  const executed = signals.filter((s) => s.delay_minutes !== null);
  if (executed.length === 0) {
    console.log('\n无已执行信号。');
    return;
  }

  const delays = executed.map((s) => s.delay_sessions!).filter((d) => d !== null) as number[];
  const avgDelay = delays.reduce((a, b) => a + b, 0) / delays.length;
  const maxDelay = Math.max(...delays);
  const delayed3Plus = executed.filter((s) => (s.delay_sessions ?? 0) >= 3);

  console.log(`\n已执行信号: ${executed.length}`);
  console.log(`平均延迟: ${avgDelay.toFixed(1)} 天`);
  console.log(`最大延迟: ${maxDelay} 天`);
  console.log(`延迟 ≥ 3 天: ${delayed3Plus.length} 个信号`);

  if (delayed3Plus.length > 0) {
    console.log(`\n### 延迟 ≥ 3 天的信号\n`);
    for (const s of delayed3Plus) {
      console.log(`- ${s.session_date} ${s.ticker} ${s.signal}(${s.confidence}%): 延迟 ${s.delay_sessions} 天 → ${verdictEmoji(s.verdict)} ${s.verdict}`);
    }
  }
}

// ── 主入口 ──────────────────────────────────────────────

function main(): void {
  const args = process.argv.slice(2);
  const ticker = args.includes('--ticker') ? args[args.indexOf('--ticker') + 1] : null;
  const session = args.includes('--session') ? args[args.indexOf('--session') + 1] : null;
  const vRevOnly = args.includes('--v-reversal');
  const delayOnly = args.includes('--delay');

  let signals: SignalRecord[];

  if (ticker) {
    signals = loadSignalsByTicker(ticker);
  } else if (session) {
    signals = loadSignalsBySession(session);
  } else {
    signals = loadAllSignals();
  }

  if (signals.length === 0) {
    console.log('📭 无信号记录。运行一次早盘分析后，信号将自动写入 data/signals.jsonl。');
    return;
  }

  const stats = computeBacktestStats();

  if (vRevOnly) {
    printVReversalAnalysis(signals);
    return;
  }

  if (delayOnly) {
    printDelayAnalysis(signals);
    return;
  }

  // 完整报告
  printHeader('信号回测报告');
  printOverallStats(stats);
  printBySignal(stats);
  printByTicker(stats);

  const verified = signals.filter((s) => s.verdict !== 'pending');
  if (verified.length > 0) {
    console.log(`\n## 逐信号明细 (${verified.length} 条已验证)\n`);
    printSignalTable(verified);
  }

  printVReversalAnalysis(signals);
  printDelayAnalysis(signals);

  // 改进建议
  if (stats.vReversalFalseSignals > 0) {
    console.log(`\n## ⚠️ 改进提醒`);
    console.log(`\n- 发现 ${stats.vReversalFalseSignals} 个 V 反假信号，量价确认规则需要持续关注`);
  }
  if (stats.avgDelayMinutes !== null && stats.avgDelayMinutes > 60) {
    console.log(`- 平均信号延迟 ${stats.avgDelayMinutes} 分钟，超过 60 分钟阈值`);
  }
}

main();