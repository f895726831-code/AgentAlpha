// SPDX-License-Identifier: MIT
// 信号结构化存储 — signals.jsonl 读写工具
//
// 每条信号以 JSONL 格式追加写入，支持：
// - 信号生成时写入（generate）
// - 执行后回填执行时间戳（fillExecution）
// - 次日回填验证结果（fillVerification）
// - 批量读取用于自动回测（loadAll）
//
// 文件路径: <project>/data/signals.jsonl

import * as fs from 'node:fs';
import * as path from 'node:path';
import * as crypto from 'node:crypto';
import { countTradingDays } from '../utils/trading-calendar.js';

// ── Schema ──────────────────────────────────────────────

export interface GapAnalysis {
  gap_direction: 'up' | 'down' | 'none';
  gap_pct: number | null;
  gap_risk: 'high' | 'medium' | 'low' | 'none';
}

export interface VolumeCheck {
  v_reversal_detected: boolean;
  v_reversal_magnitude_pct: number | null;
  volume_vs_5d_avg: 'above' | 'below' | 'normal';
  stop_loss_override: boolean;
  stop_loss_override_reason: string | null;
}

export type SignalType = 'BUY' | 'SELL' | 'HOLD';
export type Verdict = 'correct' | 'wrong' | 'ambiguous' | 'pending';

export interface SignalRecord {
  // ── 身份 ──
  signal_id: string; // uuid v4
  ticker: string;

  // ── 信号生成 ──
  generated_at: string; // ISO 8601 timestamp
  session_date: string; // YYYY-MM-DD (交易日)
  signal: SignalType;
  confidence: number; // 0-100
  signal_price: number;
  indicators: string[];
  rationale: string;

  // ── 量价确认 (W28 改进 #1) ──
  gap_analysis: GapAnalysis;
  volume_check: VolumeCheck;

  // ── 执行追踪 (W28 改进 #4) ──
  executed_at: string | null; // ISO 8601, null if not executed
  execution_price: number | null;
  delay_minutes: number | null; // signal → execution
  delay_sessions: number | null; // 跨越的交易日数
  first_trigger_date: string | null; // 首次触发同一信号的日期

  // ── 验证 (回测用) ──
  verified_at: string | null; // ISO 8601, 验证时间
  next_day_open: number | null;
  next_day_close: number | null;
  next_day_pct: number | null;
  verdict: Verdict; // correct / wrong / ambiguous / pending

  // ── 元数据 ──
  source: 'signal-gen' | 'manual' | 'backfill' | 'timi';
  notes: string | null;
}

// ── 工厂函数 ────────────────────────────────────────────

function generateId(): string {
  return `sig-${crypto.randomUUID()}`;
}

export function createSignal(partial: {
  ticker: string;
  session_date: string;
  signal: SignalType;
  confidence: number;
  signal_price: number;
  indicators: string[];
  rationale: string;
  gap_analysis?: GapAnalysis;
  volume_check?: VolumeCheck;
  first_trigger_date?: string;
  source?: 'signal-gen' | 'manual' | 'backfill' | 'timi';
  notes?: string;
}): SignalRecord {
  return {
    signal_id: generateId(),
    ticker: partial.ticker,
    generated_at: new Date().toISOString(),
    session_date: partial.session_date,
    signal: partial.signal,
    confidence: partial.confidence,
    signal_price: partial.signal_price,
    indicators: partial.indicators,
    rationale: partial.rationale,
    gap_analysis: partial.gap_analysis ?? {
      gap_direction: 'none',
      gap_pct: null,
      gap_risk: 'none',
    },
    volume_check: partial.volume_check ?? {
      v_reversal_detected: false,
      v_reversal_magnitude_pct: null,
      volume_vs_5d_avg: 'normal',
      stop_loss_override: false,
      stop_loss_override_reason: null,
    },
    executed_at: null,
    execution_price: null,
    delay_minutes: null,
    delay_sessions: null,
    first_trigger_date: partial.first_trigger_date ?? null,
    verified_at: null,
    next_day_open: null,
    next_day_close: null,
    next_day_pct: null,
    verdict: 'pending',
    source: partial.source ?? 'signal-gen',
    notes: partial.notes ?? null,
  };
}

// ── 并发保护 ────────────────────────────────────────────

// 内存互斥锁，防止并发读-改-写导致数据丢失。
// 所有写操作（writeAllSignals、fillExecution、fillVerification）串行化。
let _writeLock: Promise<void> = Promise.resolve();

async function withLock<T>(fn: () => T): Promise<T> {
  const prev = _writeLock;
  let release!: () => void;
  _writeLock = new Promise<void>((resolve) => { release = resolve; });
  await prev;
  try {
    return fn();
  } finally {
    release();
  }
}

// ── 文件路径 ────────────────────────────────────────────

function getSignalPath(): string {
  // 相对于项目根目录
  return path.resolve(process.cwd(), 'data', 'signals.jsonl');
}

// ── 写入 ────────────────────────────────────────────────

export function appendSignal(signal: SignalRecord): void {
  const filePath = getSignalPath();
  const dir = path.dirname(filePath);
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
  const line = JSON.stringify(signal) + '\n';
  fs.appendFileSync(filePath, line, 'utf-8');
}

// ── 读取 ────────────────────────────────────────────────

export function loadAllSignals(): SignalRecord[] {
  const filePath = getSignalPath();
  if (!fs.existsSync(filePath)) return [];
  const text = fs.readFileSync(filePath, 'utf-8');
  return text
    .trim()
    .split('\n')
    .filter((line) => line.length > 0)
    .map((line) => JSON.parse(line) as SignalRecord);
}

export function loadSignalsBySession(sessionDate: string): SignalRecord[] {
  return loadAllSignals().filter((s) => s.session_date === sessionDate);
}

export function loadSignalsByTicker(ticker: string): SignalRecord[] {
  return loadAllSignals().filter((s) => s.ticker === ticker);
}

export function loadPendingVerifications(): SignalRecord[] {
  return loadAllSignals().filter((s) => s.verdict === 'pending');
}

// ── 更新 (回填) ─────────────────────────────────────────

export function fillExecution(
  signalId: string,
  executionPrice: number,
  executedAt?: string,
): Promise<boolean> {
  return withLock(() => {
    const signals = loadAllSignals();
    const idx = signals.findIndex((s) => s.signal_id === signalId);
    if (idx === -1) return false;

    const s = signals[idx];
    const execTime = executedAt ? new Date(executedAt) : new Date();
    const genTime = new Date(s.generated_at);

    s.executed_at = execTime.toISOString();
    s.execution_price = executionPrice;
    s.delay_minutes = Math.round(
      (execTime.getTime() - genTime.getTime()) / 60_000,
    );
    s.delay_sessions = computeSessionDelay(s.generated_at, s.executed_at);

    writeAllSignals(signals);
    return true;
  });
}

export function fillVerification(
  signalId: string,
  nextDayOpen: number,
  nextDayClose: number,
  verdict: Verdict,
): Promise<boolean> {
  return withLock(() => {
    const signals = loadAllSignals();
    const idx = signals.findIndex((s) => s.signal_id === signalId);
    if (idx === -1) return false;

    const s = signals[idx];
    s.verified_at = new Date().toISOString();
    s.next_day_open = nextDayOpen;
    s.next_day_close = nextDayClose;
    s.next_day_pct = parseFloat(
      (((nextDayClose - s.signal_price) / s.signal_price) * 100).toFixed(2),
    );
    s.verdict = verdict;

    writeAllSignals(signals);
    return true;
  });
}

function writeAllSignals(signals: SignalRecord[]): void {
  const filePath = getSignalPath();
  const dir = path.dirname(filePath);
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
  // 原子写入：先写临时文件，再 rename 替换，防止写入中断导致数据损坏
  const tmpPath = filePath + '.tmp';
  const text = signals.map((s) => JSON.stringify(s)).join('\n') + '\n';
  fs.writeFileSync(tmpPath, text, 'utf-8');
  fs.renameSync(tmpPath, filePath);
}

// ── 回测统计 ────────────────────────────────────────────

export interface BacktestStats {
  total: number;
  bySignal: Record<SignalType, { total: number; correct: number; wrong: number; ambiguous: number }>;
  byTicker: Record<string, { total: number; correct: number; wrong: number; ambiguous: number }>;
  overallAccuracy: number; // correct / (correct + wrong), excludes ambiguous
  avgDelayMinutes: number | null;
  vReversalFalseSignals: number; // V-reversal detected but verdict was wrong
}

export function computeBacktestStats(): BacktestStats {
  const signals = loadAllSignals().filter((s) => s.verdict !== 'pending');
  const stats: BacktestStats = {
    total: signals.length,
    bySignal: { BUY: { total: 0, correct: 0, wrong: 0, ambiguous: 0 }, SELL: { total: 0, correct: 0, wrong: 0, ambiguous: 0 }, HOLD: { total: 0, correct: 0, wrong: 0, ambiguous: 0 } },
    byTicker: {},
    overallAccuracy: 0,
    avgDelayMinutes: null,
    vReversalFalseSignals: 0,
  };

  let totalDecided = 0;
  let totalCorrect = 0;
  let totalDelay = 0;
  let delayCount = 0;

  for (const s of signals) {
    // 按信号类型
    const sigGroup = stats.bySignal[s.signal];
    sigGroup.total++;
    if (s.verdict === 'correct') { sigGroup.correct++; totalCorrect++; totalDecided++; }
    else if (s.verdict === 'wrong') { sigGroup.wrong++; totalDecided++; }
    else if (s.verdict === 'ambiguous') sigGroup.ambiguous++;

    // 按标的
    if (!stats.byTicker[s.ticker]) {
      stats.byTicker[s.ticker] = { total: 0, correct: 0, wrong: 0, ambiguous: 0 };
    }
    const tickerGroup = stats.byTicker[s.ticker];
    tickerGroup.total++;
    if (s.verdict === 'correct') tickerGroup.correct++;
    else if (s.verdict === 'wrong') tickerGroup.wrong++;
    else if (s.verdict === 'ambiguous') tickerGroup.ambiguous++;

    // V 反假信号
    if (s.volume_check.v_reversal_detected && s.verdict === 'wrong') {
      stats.vReversalFalseSignals++;
    }

    // 延迟
    if (s.delay_minutes !== null) {
      totalDelay += s.delay_minutes;
      delayCount++;
    }
  }

  stats.overallAccuracy = totalDecided > 0
    ? parseFloat(((totalCorrect / totalDecided) * 100).toFixed(1))
    : 0;
  stats.avgDelayMinutes = delayCount > 0
    ? parseFloat((totalDelay / delayCount).toFixed(1))
    : null;

  return stats;
}

// ── 辅助 ────────────────────────────────────────────────

function computeSessionDelay(genTime: string, execTime: string): number {
  // 按交易日计算，而非自然日。避免周末/节假日计入延迟。
  const genDate = new Date(genTime);
  const execDate = new Date(execTime);
  return countTradingDays(genDate, execDate) - 1; // 不含当天
}

// ── 导出验证用的 Schema (供自动回测脚本使用) ──────────

export const SIGNAL_JSON_SCHEMA = {
  $schema: 'https://json-schema.org/draft/2020-12/schema',
  title: 'SignalRecord',
  type: 'object',
  required: [
    'signal_id', 'ticker', 'generated_at', 'session_date',
    'signal', 'confidence', 'signal_price', 'indicators',
    'rationale', 'gap_analysis', 'volume_check', 'verdict',
  ],
  properties: {
    signal_id: { type: 'string' },
    ticker: { type: 'string' },
    generated_at: { type: 'string', format: 'date-time' },
    session_date: { type: 'string', format: 'date' },
    signal: { enum: ['BUY', 'SELL', 'HOLD'] },
    confidence: { type: 'number', minimum: 0, maximum: 100 },
    signal_price: { type: 'number' },
    indicators: { type: 'array', items: { type: 'string' } },
    rationale: { type: 'string' },
    gap_analysis: {
      type: 'object',
      properties: {
        gap_direction: { enum: ['up', 'down', 'none'] },
        gap_pct: { type: ['number', 'null'] },
        gap_risk: { enum: ['high', 'medium', 'low', 'none'] },
      },
    },
    volume_check: {
      type: 'object',
      properties: {
        v_reversal_detected: { type: 'boolean' },
        v_reversal_magnitude_pct: { type: ['number', 'null'] },
        volume_vs_5d_avg: { enum: ['above', 'below', 'normal'] },
        stop_loss_override: { type: 'boolean' },
        stop_loss_override_reason: { type: ['string', 'null'] },
      },
    },
    executed_at: { type: ['string', 'null'], format: 'date-time' },
    execution_price: { type: ['number', 'null'] },
    delay_minutes: { type: ['number', 'null'] },
    delay_sessions: { type: ['number', 'null'] },
    first_trigger_date: { type: ['string', 'null'], format: 'date' },
    verified_at: { type: ['string', 'null'], format: 'date-time' },
    next_day_open: { type: ['number', 'null'] },
    next_day_close: { type: ['number', 'null'] },
    next_day_pct: { type: ['number', 'null'] },
    verdict: { enum: ['correct', 'wrong', 'ambiguous', 'pending'] },
    source: { enum: ['signal-gen', 'manual', 'backfill'] },
    notes: { type: ['string', 'null'] },
  },
} as const;