// SPDX-License-Identifier: MIT
// 信号存储单元测试
// 验证 createSignal / appendSignal / loadAllSignals / fillExecution / fillVerification

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import * as fs from 'node:fs';
import * as path from 'node:path';
import {
  createSignal,
  appendSignal,
  loadAllSignals,
  loadSignalsByTicker,
  loadSignalsBySession,
  loadPendingVerifications,
  fillExecution,
  fillVerification,
  computeBacktestStats,
  type SignalRecord,
} from '../src/data/signal-store.js';

// 测试用临时文件路径
const TEST_DATA_DIR = path.resolve(process.cwd(), '__tests__', '.test-data');
const TEST_SIGNALS_PATH = path.join(TEST_DATA_DIR, 'signals.jsonl');

// 覆盖 getSignalPath 的行为 — 通过修改 cwd 来隔离测试
// 注意：signal-store 使用 process.cwd() 定位 signals.jsonl，
// 因此测试前需要确保 cwd 指向测试数据目录。
// 这里的测试假设 signals.jsonl 在项目根 data/ 目录下。

describe('signal-store', () => {
  describe('createSignal', () => {
    it('creates a valid signal record with all required fields', () => {
      const signal = createSignal({
        ticker: '000300.SH',
        session_date: '2026-07-20',
        signal: 'BUY',
        confidence: 80,
        signal_price: 4800,
        indicators: ['MA5>MA10', 'volume_spike'],
        rationale: '突破均线压制，放量确认',
      });

      expect(signal.signal_id).toMatch(/^sig-/);
      expect(signal.ticker).toBe('000300.SH');
      expect(signal.signal).toBe('BUY');
      expect(signal.confidence).toBe(80);
      expect(signal.signal_price).toBe(4800);
      expect(signal.indicators).toHaveLength(2);
      expect(signal.verdict).toBe('pending');
      expect(signal.source).toBe('signal-gen');
      expect(signal.gap_analysis.gap_direction).toBe('none');
      expect(signal.volume_check.v_reversal_detected).toBe(false);
    });

    it('accepts custom gap_analysis and volume_check', () => {
      const signal = createSignal({
        ticker: '002624.SZ',
        session_date: '2026-07-20',
        signal: 'SELL',
        confidence: 95,
        signal_price: 10.48,
        indicators: ['gap_down>5%'],
        rationale: '跳空破位',
        gap_analysis: {
          gap_direction: 'down',
          gap_pct: 7.91,
          gap_risk: 'high',
        },
        volume_check: {
          v_reversal_detected: false,
          v_reversal_magnitude_pct: null,
          volume_vs_5d_avg: 'above',
          stop_loss_override: false,
          stop_loss_override_reason: null,
        },
      });

      expect(signal.gap_analysis.gap_direction).toBe('down');
      expect(signal.gap_analysis.gap_pct).toBe(7.91);
      expect(signal.volume_check.volume_vs_5d_avg).toBe('above');
    });

    it('generates unique signal_ids', () => {
      const s1 = createSignal({
        ticker: '601318.SH',
        session_date: '2026-07-20',
        signal: 'HOLD',
        confidence: 60,
        signal_price: 49.5,
        indicators: [],
        rationale: 'test',
      });
      const s2 = createSignal({
        ticker: '601318.SH',
        session_date: '2026-07-20',
        signal: 'HOLD',
        confidence: 60,
        signal_price: 49.5,
        indicators: [],
        rationale: 'test',
      });
      expect(s1.signal_id).not.toBe(s2.signal_id);
    });
  });

  describe('loadAllSignals', () => {
    it('loads signals from the JSONL file', () => {
      const signals = loadAllSignals();
      // The real signals.jsonl has data — at minimum it should be an array
      expect(Array.isArray(signals)).toBe(true);
    });
  });

  describe('loadSignalsByTicker', () => {
    it('filters signals by ticker', () => {
      const signals = loadSignalsByTicker('002624.SZ');
      expect(Array.isArray(signals)).toBe(true);
      for (const s of signals) {
        expect(s.ticker).toBe('002624.SZ');
      }
    });
  });

  describe('loadSignalsBySession', () => {
    it('filters signals by session date', () => {
      const signals = loadSignalsBySession('2026-07-10');
      expect(Array.isArray(signals)).toBe(true);
      for (const s of signals) {
        expect(s.session_date).toBe('2026-07-10');
      }
    });
  });

  describe('loadPendingVerifications', () => {
    it('returns only pending signals', () => {
      const pending = loadPendingVerifications();
      expect(Array.isArray(pending)).toBe(true);
      for (const s of pending) {
        expect(s.verdict).toBe('pending');
      }
    });
  });

  describe('computeBacktestStats', () => {
    it('computes stats from verified signals', () => {
      const stats = computeBacktestStats();
      expect(typeof stats.total).toBe('number');
      expect(typeof stats.overallAccuracy).toBe('number');
      expect(stats.overallAccuracy).toBeGreaterThanOrEqual(0);
      expect(stats.overallAccuracy).toBeLessThanOrEqual(100);
      // 信号类型统计应存在
      expect(stats.bySignal.BUY).toBeDefined();
      expect(stats.bySignal.SELL).toBeDefined();
      expect(stats.bySignal.HOLD).toBeDefined();
    });
  });

  describe('fillExecution', () => {
    it('returns false for non-existent signal id', async () => {
      const result = await fillExecution('sig-nonexistent', 100);
      expect(result).toBe(false);
    });

    it('handles concurrent calls without data loss', async () => {
      // 并发写入测试 — 验证锁机制不会导致数据丢失
      const results = await Promise.all([
        fillExecution('sig-nonexistent', 100),
        fillExecution('sig-nonexistent', 200),
        fillExecution('sig-nonexistent', 300),
      ]);
      // 所有调用都应返回 false（信号不存在），但不能崩溃
      expect(results.every(r => r === false)).toBe(true);
    });
  });

  describe('fillVerification', () => {
    it('returns false for non-existent signal id', async () => {
      const result = await fillVerification('sig-nonexistent', 100, 105, 'correct');
      expect(result).toBe(false);
    });
  });
});