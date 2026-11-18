// SPDX-License-Identifier: MIT
// 交易日历单元测试
// 验证 isTradingDay / nextTradingDay / prevTradingDay / countTradingDays / listTradingDays

import { describe, it, expect } from 'vitest';
import {
  isTradingDay,
  nextTradingDay,
  prevTradingDay,
  countTradingDays,
  listTradingDays,
  getTradingStatus,
  formatDate,
} from '../src/utils/trading-calendar.js';

describe('trading-calendar', () => {
  describe('isTradingDay', () => {
    // 已知的周末（周六/周日）
    it('returns false for Saturday', () => {
      // 2026-07-18 is a Saturday
      expect(isTradingDay('2026-07-18')).toBe(false);
    });

    it('returns false for Sunday', () => {
      // 2026-07-19 is a Sunday
      expect(isTradingDay('2026-07-19')).toBe(false);
    });

    // 已知的交易日（周一到周五，非节假日）
    it('returns true for a normal weekday', () => {
      // 2026-07-20 is a Monday
      expect(isTradingDay('2026-07-20')).toBe(true);
    });

    it('returns true for a mid-week trading day', () => {
      // 2026-07-15 is a Wednesday
      expect(isTradingDay('2026-07-15')).toBe(true);
    });

    // 已知的节假日（春节）
    it('returns false for Spring Festival (春节)', () => {
      // 2026 春节: 2/16-2/23
      expect(isTradingDay('2026-02-17')).toBe(false);
    });

    // 已知的节假日（国庆）
    it('returns false for National Day (国庆)', () => {
      // 2026 国庆: 10/1-10/7
      expect(isTradingDay('2026-10-02')).toBe(false);
    });
  });

  describe('nextTradingDay', () => {
    it('returns Monday when called on Friday', () => {
      // 2026-07-17 is a Friday
      const next = nextTradingDay('2026-07-17');
      expect(next).not.toBeNull();
      expect(formatDate(next!)).toBe('2026-07-20'); // Monday
    });

    it('returns next day when called on a weekday', () => {
      const next = nextTradingDay('2026-07-15'); // Wednesday
      expect(next).not.toBeNull();
      expect(formatDate(next!)).toBe('2026-07-16'); // Thursday
    });
  });

  describe('prevTradingDay', () => {
    it('returns Friday when called on Monday', () => {
      const prev = prevTradingDay('2026-07-20'); // Monday
      expect(formatDate(prev)).toBe('2026-07-17'); // Friday
    });

    it('returns previous day when called mid-week', () => {
      const prev = prevTradingDay('2026-07-16'); // Thursday
      expect(formatDate(prev)).toBe('2026-07-15'); // Wednesday
    });
  });

  describe('countTradingDays', () => {
    it('counts 5 trading days in a normal work week', () => {
      // 2026-07-13 (Mon) to 2026-07-17 (Fri)
      const count = countTradingDays('2026-07-13', '2026-07-17');
      expect(count).toBe(5);
    });

    it('counts 0 when start equals end and is a weekend', () => {
      const count = countTradingDays('2026-07-18', '2026-07-18'); // Saturday
      expect(count).toBe(0);
    });
  });

  describe('listTradingDays', () => {
    it('returns an array of trading days for 2026', () => {
      const days = listTradingDays(2026);
      expect(days.length).toBeGreaterThan(200); // ~242 trading days typically
      expect(days.length).toBeLessThan(260);
      // All entries should be valid dates
      for (const d of days) {
        expect(d).toMatch(/^\d{4}-\d{2}-\d{2}$/);
      }
    });
  });

  describe('getTradingStatus', () => {
    it('returns trading day status for a weekday', () => {
      const status = getTradingStatus('2026-07-20');
      expect(status.is_trading_day).toBe(true);
      expect(status.reason).toBe('交易日');
    });

    it('returns holiday status for Spring Festival', () => {
      const status = getTradingStatus('2026-02-17');
      expect(status.is_trading_day).toBe(false);
      expect(status.reason).toContain('假期');
    });
  });

  describe('formatDate', () => {
    it('formats a Date as YYYY-MM-DD', () => {
      const d = new Date(2026, 6, 20); // July 20, 2026
      expect(formatDate(d)).toBe('2026-07-20');
    });
  });
});