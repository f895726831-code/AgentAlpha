// SPDX-License-Identifier: MIT
// 宏观指标定义单元测试
// 验证 MACRO_INDICATORS 结构完整性和 judgeSignal 判断逻辑

import { describe, it, expect } from 'vitest';
import {
  MACRO_INDICATORS,
  judgeSignal,
  type MacroIndicator,
} from '../src/data/macro-indicators.js';

describe('macro-indicators', () => {
  describe('MACRO_INDICATORS', () => {
    it('contains exactly 8 core indicators', () => {
      expect(MACRO_INDICATORS).toHaveLength(8);
    });

    it('every indicator has required fields', () => {
      for (const ind of MACRO_INDICATORS) {
        expect(ind.code).toBeTruthy();
        expect(ind.name).toBeTruthy();
        expect(ind.query).toBeTruthy();
        expect(['month', 'quarter', 'daily']).toContain(ind.freq);
        expect(['higher', 'lower', 'stable']).toContain(ind.bull);
      }
    });

    it('every indicator has unique code', () => {
      const codes = MACRO_INDICATORS.map((i) => i.code);
      expect(new Set(codes).size).toBe(codes.length);
    });
  });

  describe('judgeSignal', () => {
    it('returns bullish when PMI > 50 (higher is bullish)', () => {
      const pmi = MACRO_INDICATORS.find((i) => i.code === 'M0017126')!;
      expect(judgeSignal(pmi, 51)).toBe('bullish');
    });

    it('returns bearish when PMI < 50 * 0.95 (higher is bullish)', () => {
      const pmi = MACRO_INDICATORS.find((i) => i.code === 'M0017126')!;
      expect(judgeSignal(pmi, 46)).toBe('bearish');
    });

    it('returns neutral when PMI is near 50', () => {
      const pmi = MACRO_INDICATORS.find((i) => i.code === 'M0017126')!;
      expect(judgeSignal(pmi, 49)).toBe('neutral');
    });

    it('returns bullish when PE < 12 (lower is bullish)', () => {
      const pe = MACRO_INDICATORS.find((i) => i.code === 'M0342074')!;
      expect(judgeSignal(pe, 10)).toBe('bullish');
    });

    it('returns bearish when PE > 18 (lower is bullish)', () => {
      const pe = MACRO_INDICATORS.find((i) => i.code === 'M0342074')!;
      expect(judgeSignal(pe, 20)).toBe('bearish');
    });

    it('returns bearish when CPI is negative (deflation)', () => {
      const cpi = MACRO_INDICATORS.find((i) => i.code === 'M0000612')!;
      expect(judgeSignal(cpi, -1)).toBe('bearish');
    });

    it('returns bearish when CPI > 3% (inflation)', () => {
      const cpi = MACRO_INDICATORS.find((i) => i.code === 'M0000612')!;
      expect(judgeSignal(cpi, 4)).toBe('bearish');
    });

    it('returns neutral when CPI is between 0 and 3', () => {
      const cpi = MACRO_INDICATORS.find((i) => i.code === 'M0000612')!;
      expect(judgeSignal(cpi, 1.5)).toBe('neutral');
    });

    it('returns neutral for indicator without threshold', () => {
      const ind: MacroIndicator = {
        code: 'TEST',
        name: 'Test',
        query: 'test',
        freq: 'month',
        bull: 'higher',
      };
      expect(judgeSignal(ind, 100)).toBe('neutral');
    });
  });
});