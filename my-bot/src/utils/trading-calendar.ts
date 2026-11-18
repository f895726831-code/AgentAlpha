// SPDX-License-Identifier: MIT
/**
 * A股交易日历 — 判断指定日期是否为交易日。
 *
 * 规则：
 * 1. 周一至周五为候选交易日。
 * 2. 法定节假日（元旦、春节、清明、劳动节、端午、中秋、国庆）休市。
 * 3. 调休工作日（周末补班）视为交易日。
 *
 * 数据来源：src/data/trading-calendar-2026.json
 * 注意：节假日数据为预估，以交易所官方公告为准。
 */

import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

// ---------------------------------------------------------------------------
// 类型
// ---------------------------------------------------------------------------

interface HolidayEntry {
  range: [string, string];
  days: number;
  note?: string;
}

interface MakeupDay {
  date: string;
  note: string;
  confidence: string;
}

interface CalendarData {
  year: number;
  market: string;
  closed_dates: string[];
  special_trading_dates: string[];
  makeup_trading_days: MakeupDay[];
  holidays: Record<string, HolidayEntry>;
}

// ---------------------------------------------------------------------------
// 内部状态（懒加载）
// ---------------------------------------------------------------------------

let _calendar: CalendarData | null = null;
let _closedSet: Set<string> | null = null;
let _specialSet: Set<string> | null = null;

function loadCalendar(): CalendarData {
  if (_calendar) return _calendar;

  const __filename = fileURLToPath(import.meta.url);
  const __dirname = dirname(__filename);
  const dataPath = join(__dirname, '..', 'data', 'trading-calendar-2026.json');

  const raw = readFileSync(dataPath, 'utf-8');
  _calendar = JSON.parse(raw) as CalendarData;
  _closedSet = new Set(_calendar.closed_dates);
  _specialSet = new Set(_calendar.special_trading_dates);
  return _calendar;
}

function getClosedSet(): Set<string> {
  if (!_closedSet) loadCalendar();
  return _closedSet!;
}

function getSpecialSet(): Set<string> {
  if (!_specialSet) loadCalendar();
  return _specialSet!;
}

// ---------------------------------------------------------------------------
// 公开 API
// ---------------------------------------------------------------------------

/**
 * 将 Date 转为 YYYY-MM-DD 格式字符串。
 */
export function formatDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

/**
 * 判断给定日期是否为 A 股交易日。
 *
 * @param date  Date 对象 或 YYYY-MM-DD 字符串。默认为今天。
 * @returns    true 表示该日期是交易日（市场开放）。
 */
export function isTradingDay(date?: Date | string): boolean {
  const d = typeof date === 'string' ? new Date(date + 'T00:00:00') : (date ?? new Date());
  const dateStr = formatDate(d);

  // 1. 调休补班日（周末但开市）→ 交易日
  if (getSpecialSet().has(dateStr)) {
    return true;
  }

  // 2. 周末 → 休市
  const dayOfWeek = d.getDay(); // 0=Sun, 6=Sat
  if (dayOfWeek === 0 || dayOfWeek === 6) {
    return false;
  }

  // 3. 法定节假日 → 休市
  if (getClosedSet().has(dateStr)) {
    return false;
  }

  // 4. 周一到周五，非节假日 → 交易日
  return true;
}

/**
 * 获取下一个交易日。
 *
 * @param date  起始日期，默认为今天。从该日期之后开始查找。
 * @returns     下一个交易日的 Date 对象，若当年无更多交易日则返回 null。
 */
export function nextTradingDay(date?: Date | string): Date | null {
  const start = typeof date === 'string' ? new Date(date + 'T00:00:00') : (date ?? new Date());
  const cursor = new Date(start);
  cursor.setDate(cursor.getDate() + 1);

  // 安全上限：365 天
  for (let i = 0; i < 365; i++) {
    if (isTradingDay(cursor)) return cursor;
    cursor.setDate(cursor.getDate() + 1);
  }
  return null;
}

/**
 * 获取上一个交易日。
 *
 * @param date  起始日期，默认为今天。从该日期之前开始查找。
 * @returns     上一个交易日的 Date 对象。
 */
export function prevTradingDay(date?: Date | string): Date {
  const start = typeof date === 'string' ? new Date(date + 'T00:00:00') : (date ?? new Date());
  const cursor = new Date(start);
  cursor.setDate(cursor.getDate() - 1);

  for (let i = 0; i < 365; i++) {
    if (isTradingDay(cursor)) return cursor;
    cursor.setDate(cursor.getDate() - 1);
  }
  throw new Error('无法找到上一个交易日');
}

/**
 * 获取当前状态的人类可读描述。
 *
 * @param date  日期，默认为今天。
 * @returns     状态描述，如 "交易日"、"周末休市"、"春节休市" 等。
 */
export function getTradingStatus(date?: Date | string): {
  is_trading_day: boolean;
  reason: string;
  next_trading_day: string | null;
} {
  const d = typeof date === 'string' ? new Date(date + 'T00:00:00') : (date ?? new Date());
  const dateStr = formatDate(d);
  const dayOfWeek = d.getDay();

  if (getSpecialSet().has(dateStr)) {
    return {
      is_trading_day: true,
      reason: '调休工作日（周末补班→交易日）',
      next_trading_day: null,
    };
  }

  if (dayOfWeek === 0 || dayOfWeek === 6) {
    const next = nextTradingDay(d);
    return {
      is_trading_day: false,
      reason: dayOfWeek === 0 ? '周日休市' : '周六休市',
      next_trading_day: next ? formatDate(next) : null,
    };
  }

  if (getClosedSet().has(dateStr)) {
    const cal = loadCalendar();
    let holidayName = '法定节假日';
    for (const [name, entry] of Object.entries(cal.holidays)) {
      const [start, end] = entry.range;
      if (dateStr >= start && dateStr <= end) {
        holidayName = `${name}假期`;
        break;
      }
    }
    const next = nextTradingDay(d);
    return {
      is_trading_day: false,
      reason: holidayName,
      next_trading_day: next ? formatDate(next) : null,
    };
  }

  return {
    is_trading_day: true,
    reason: '交易日',
    next_trading_day: null,
  };
}

/**
 * 计算两个日期之间的交易日数量。
 *
 * @param from  起始日期（含）
 * @param to    结束日期（含）
 * @returns     交易日天数
 */
export function countTradingDays(from: Date | string, to: Date | string): number {
  const start = typeof from === 'string' ? new Date(from + 'T00:00:00') : from;
  const end = typeof to === 'string' ? new Date(to + 'T00:00:00') : to;

  let count = 0;
  const cursor = new Date(start);
  while (cursor <= end) {
    if (isTradingDay(cursor)) count++;
    cursor.setDate(cursor.getDate() + 1);
  }
  return count;
}

/**
 * 获取指定年份的所有交易日列表。
 *
 * @param year  年份，默认 2026
 * @returns     YYYY-MM-DD 字符串数组
 */
export function listTradingDays(year: number = 2026): string[] {
  const days: string[] = [];
  const cursor = new Date(year, 0, 1);
  const end = new Date(year, 11, 31);

  while (cursor <= end) {
    if (isTradingDay(cursor)) {
      days.push(formatDate(cursor));
    }
    cursor.setDate(cursor.getDate() + 1);
  }
  return days;
}

// ---------------------------------------------------------------------------
// CLI 入口
// ---------------------------------------------------------------------------

function main() {
  const args = process.argv.slice(2);
  const today = new Date();

  if (args.includes('--help') || args.includes('-h')) {
    console.log(`交易日历 CLI
用法:
  node trading-calendar.mjs              # 检查今天
  node trading-calendar.mjs 2026-07-04   # 检查指定日期
  node trading-calendar.mjs --next       # 下一个交易日
  node trading-calendar.mjs --count 2026 # 统计全年交易日数量
  node trading-calendar.mjs --list 2026  # 列出全年交易日
  node trading-calendar.mjs --status     # 今日状态详情`);
    return;
  }

  if (args.includes('--next')) {
    const next = nextTradingDay(today);
    console.log(next ? formatDate(next) : '无');
    return;
  }

  if (args.includes('--status')) {
    const status = getTradingStatus(today);
    console.log(JSON.stringify(status, null, 2));
    return;
  }

  if (args.includes('--count')) {
    const year = parseInt(args[args.indexOf('--count') + 1] || '2026', 10);
    const days = listTradingDays(year);
    console.log(`${year}年共 ${days.length} 个交易日`);
    return;
  }

  if (args.includes('--list')) {
    const year = parseInt(args[args.indexOf('--list') + 1] || '2026', 10);
    const days = listTradingDays(year);
    console.log(`${year}年交易日 (${days.length}天):`);
    days.forEach(d => console.log(d));
    return;
  }

  // 默认：检查指定日期或今天
  const dateStr = args[0] || formatDate(today);
  const result = isTradingDay(dateStr);
  const status = getTradingStatus(dateStr);
  console.log(`${dateStr}: ${status.reason}`);
  if (!status.is_trading_day && status.next_trading_day) {
    console.log(`下一个交易日: ${status.next_trading_day}`);
  }
}

// 仅当直接运行时执行 CLI
const invokedDirectly = (() => {
  if (!process.argv[1]) return false;
  try {
    const { realpathSync } = require('node:fs');
    const a = realpathSync(process.argv[1]);
    const b = realpathSync(fileURLToPath(import.meta.url));
    return process.platform === 'win32' ? a.toLowerCase() === b.toLowerCase() : a === b;
  } catch {
    return false;
  }
})();

if (invokedDirectly) {
  main();
}