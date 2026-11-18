// SPDX-License-Identifier: MIT
// v4.0 管线模式检测单元测试
//
// 测试范围：
// - isEarningsSeason() — 财报季月份判断
// - isRiskOffRegime() — 防守关键词识别
// - detectMode() — 三模式切换 + 优先级
// - getRiskOffParams() / getNormalParams() / getParamsForMode() — 参数获取
// - getModeDescription() — 中文描述
// - getSubPipelineSkills() — 子管线技能列表
// - isPreMarketWindow() / isPostMarketWindow() — 盘前/盘后窗口判断

import { describe, it, expect } from 'vitest';
import {
  isEarningsSeason,
  isRiskOffRegime,
  detectMode,
  getRiskOffParams,
  getNormalParams,
  getParamsForMode,
  getModeDescription,
  getSubPipelineSkills,
  isPreMarketWindow,
  isPostMarketWindow,
} from '../src/agents/pipeline-mode.js';

// ── isEarningsSeason ─────────────────────────────────────

describe('isEarningsSeason', () => {
  it('returns true for January (年报+一季报)', () => {
    expect(isEarningsSeason(new Date(2026, 0, 15))).toBe(true);
  });

  it('returns true for April (年报+一季报末期)', () => {
    expect(isEarningsSeason(new Date(2026, 3, 30))).toBe(true);
  });

  it('returns true for July (中报)', () => {
    expect(isEarningsSeason(new Date(2026, 6, 20))).toBe(true);
  });

  it('returns true for August (中报)', () => {
    expect(isEarningsSeason(new Date(2026, 7, 1))).toBe(true);
  });

  it('returns true for October (三季报)', () => {
    expect(isEarningsSeason(new Date(2026, 9, 15))).toBe(true);
  });

  it('returns false for May (非财报季)', () => {
    expect(isEarningsSeason(new Date(2026, 4, 15))).toBe(false);
  });

  it('returns false for June (非财报季)', () => {
    expect(isEarningsSeason(new Date(2026, 5, 1))).toBe(false);
  });

  it('returns false for September (非财报季)', () => {
    expect(isEarningsSeason(new Date(2026, 8, 15))).toBe(false);
  });

  it('returns false for November (非财报季)', () => {
    expect(isEarningsSeason(new Date(2026, 10, 1))).toBe(false);
  });

  it('returns false for December (非财报季)', () => {
    expect(isEarningsSeason(new Date(2026, 11, 31))).toBe(false);
  });

  it('defaults to today when no date provided', () => {
    const result = isEarningsSeason();
    expect(typeof result).toBe('boolean');
  });
});

// ── isRiskOffRegime ──────────────────────────────────────

describe('isRiskOffRegime', () => {
  it('returns true for "防守"', () => {
    expect(isRiskOffRegime('防守')).toBe(true);
  });

  it('returns true for "risk_off"', () => {
    expect(isRiskOffRegime('risk_off')).toBe(true);
  });

  it('returns true for "bear"', () => {
    expect(isRiskOffRegime('bear')).toBe(true);
  });

  it('returns true for "bearish"', () => {
    expect(isRiskOffRegime('bearish')).toBe(true);
  });

  it('returns true for "risk-off"', () => {
    expect(isRiskOffRegime('risk-off')).toBe(true);
  });

  it('returns false for "进攻"', () => {
    expect(isRiskOffRegime('进攻')).toBe(false);
  });

  it('returns false for "risk_on"', () => {
    expect(isRiskOffRegime('risk_on')).toBe(false);
  });

  it('returns false for "震荡"', () => {
    expect(isRiskOffRegime('震荡')).toBe(false);
  });

  it('returns false for "neutral"', () => {
    expect(isRiskOffRegime('neutral')).toBe(false);
  });

  it('is case-insensitive', () => {
    expect(isRiskOffRegime('BEAR')).toBe(true);
    expect(isRiskOffRegime('RISK_OFF')).toBe(true);
  });

  it('handles whitespace', () => {
    expect(isRiskOffRegime('  防守  ')).toBe(true);
  });
});

// ── detectMode ───────────────────────────────────────────

describe('detectMode', () => {
  it('returns risk_off when regime is 防守', () => {
    const result = detectMode('防守', new Date(2026, 4, 15)); // May, not earnings season
    expect(result.mode).toBe('risk_off');
    expect(result.riskOff).toBe(true);
    expect(result.earningsSeason).toBe(false);
  });

  it('returns risk_off when regime is bearish (even in earnings season)', () => {
    // risk_off 优先级最高，即使财报季也优先防守
    const result = detectMode('bearish', new Date(2026, 0, 15)); // January, earnings season
    expect(result.mode).toBe('risk_off');
    expect(result.riskOff).toBe(true);
    expect(result.earningsSeason).toBe(true); // 仍然标记为财报季
  });

  it('returns earnings_season when regime is 进攻 and in earnings month', () => {
    const result = detectMode('进攻', new Date(2026, 7, 1)); // August, earnings season
    expect(result.mode).toBe('earnings_season');
    expect(result.earningsSeason).toBe(true);
    expect(result.riskOff).toBe(false);
  });

  it('returns earnings_season when regime is 震荡 and in earnings month', () => {
    const result = detectMode('震荡', new Date(2026, 9, 15)); // October, earnings season
    expect(result.mode).toBe('earnings_season');
    expect(result.earningsSeason).toBe(true);
  });

  it('returns normal when regime is 进攻 and not in earnings month', () => {
    const result = detectMode('进攻', new Date(2026, 4, 15)); // May, not earnings
    expect(result.mode).toBe('normal');
    expect(result.earningsSeason).toBe(false);
    expect(result.riskOff).toBe(false);
  });

  it('returns normal when regime is 震荡 and not in earnings month', () => {
    const result = detectMode('震荡', new Date(2026, 5, 1)); // June, not earnings
    expect(result.mode).toBe('normal');
  });

  it('includes human-readable reason in result', () => {
    const normal = detectMode('进攻', new Date(2026, 4, 15));
    expect(normal.reason).toBeTruthy();
    expect(typeof normal.reason).toBe('string');

    const earnings = detectMode('进攻', new Date(2026, 0, 15));
    expect(earnings.reason).toBeTruthy();
    expect(earnings.reason).toContain('财报季');

    const riskOff = detectMode('防守', new Date(2026, 4, 15));
    expect(riskOff.reason).toBeTruthy();
    expect(riskOff.reason).toContain('防守');
  });
});

// ── getRiskOffParams / getNormalParams / getParamsForMode ─

describe('Risk parameters', () => {
  it('getRiskOffParams returns tightened concentration limits', () => {
    const params = getRiskOffParams();
    expect(params.concentration.mainBoard).toBe(5);
    expect(params.concentration.starBoard).toBe(15);
    expect(params.concentration.hkStock).toBe(10);
  });

  it('getRiskOffParams returns tightened circuit breakers', () => {
    const params = getRiskOffParams();
    expect(params.circuitBreaker.dailyLossLimitPct).toBe(-1.5);
    expect(params.circuitBreaker.maxConsecutiveLosses).toBe(3);
  });

  it('getRiskOffParams disallows new positions', () => {
    const params = getRiskOffParams();
    expect(params.newPositionAllowed).toBe(false);
  });

  it('getNormalParams returns standard concentration limits', () => {
    const params = getNormalParams();
    expect(params.concentration.mainBoard).toBe(10);
    expect(params.concentration.starBoard).toBe(25);
    expect(params.concentration.hkStock).toBe(15);
  });

  it('getNormalParams returns standard circuit breakers', () => {
    const params = getNormalParams();
    expect(params.circuitBreaker.dailyLossLimitPct).toBe(-2);
    expect(params.circuitBreaker.maxConsecutiveLosses).toBe(5);
  });

  it('getNormalParams allows new positions', () => {
    const params = getNormalParams();
    expect(params.newPositionAllowed).toBe(true);
  });

  it('getParamsForMode returns risk_off params for risk_off mode', () => {
    const params = getParamsForMode('risk_off');
    expect(params.concentration.mainBoard).toBe(5);
    expect('newPositionAllowed' in params && params.newPositionAllowed).toBe(false);
  });

  it('getParamsForMode returns normal params for normal mode', () => {
    const params = getParamsForMode('normal');
    expect(params.concentration.mainBoard).toBe(10);
  });

  it('getParamsForMode returns normal params for earnings_season mode', () => {
    const params = getParamsForMode('earnings_season');
    expect(params.concentration.mainBoard).toBe(10);
  });

  it('returned params are independent top-level objects', () => {
    // Note: spread operator creates shallow copies — nested objects (concentration, circuitBreaker)
    // share references. This is fine for the intended use case (read-only config retrieval).
    const p1 = getRiskOffParams();
    const p2 = getRiskOffParams();
    expect(p1).not.toBe(p2); // top-level objects are different
    expect(p1.concentration).toEqual(p2.concentration); // but nested objects are equal
    expect(p1.newPositionAllowed).toBe(false);
    expect(p2.newPositionAllowed).toBe(false);
  });
});

// ── getModeDescription ───────────────────────────────────

describe('getModeDescription', () => {
  it('returns Chinese description for normal', () => {
    const desc = getModeDescription('normal');
    expect(desc).toContain('日常');
    expect(desc).toContain('v3.1');
  });

  it('returns Chinese description for earnings_season', () => {
    const desc = getModeDescription('earnings_season');
    expect(desc).toContain('财报季');
  });

  it('returns Chinese description for risk_off', () => {
    const desc = getModeDescription('risk_off');
    expect(desc).toContain('防守');
    expect(desc).toContain('1.5%');
  });
});

// ── getSubPipelineSkills ─────────────────────────────────

describe('getSubPipelineSkills', () => {
  it('returns empty array for normal mode', () => {
    const skills = getSubPipelineSkills('normal');
    expect(skills).toEqual([]);
  });

  it('returns earnings skills for earnings_season mode', () => {
    const skills = getSubPipelineSkills('earnings_season');
    expect(skills).toContain('earnings_calendar_planner_skill');
    expect(skills).toContain('earnings_preview_skill');
    expect(skills).toContain('earnings_reaction_interpreter_skill');
    expect(skills.length).toBe(3);
  });

  it('returns monitoring skills for risk_off mode', () => {
    const skills = getSubPipelineSkills('risk_off');
    expect(skills).toContain('intraday_abnormal_move_alert_skill');
    expect(skills).toContain('support_break_warning_skill');
    expect(skills.length).toBe(2);
  });
});

// ── isPreMarketWindow / isPostMarketWindow ────────────────

describe('Market window detection', () => {
  it('isPreMarketWindow returns true for 8:45', () => {
    const d = new Date(2026, 0, 15, 8, 45, 0);
    expect(isPreMarketWindow(d)).toBe(true);
  });

  it('isPreMarketWindow returns true for 9:00', () => {
    const d = new Date(2026, 0, 15, 9, 0, 0);
    expect(isPreMarketWindow(d)).toBe(true);
  });

  it('isPreMarketWindow returns false for 8:00', () => {
    const d = new Date(2026, 0, 15, 8, 0, 0);
    expect(isPreMarketWindow(d)).toBe(false);
  });

  it('isPreMarketWindow returns false for 9:30', () => {
    const d = new Date(2026, 0, 15, 9, 30, 0);
    expect(isPreMarketWindow(d)).toBe(false);
  });

  it('isPreMarketWindow returns false for 14:00', () => {
    const d = new Date(2026, 0, 15, 14, 0, 0);
    expect(isPreMarketWindow(d)).toBe(false);
  });

  it('isPostMarketWindow returns true for 16:00', () => {
    const d = new Date(2026, 0, 15, 16, 0, 0);
    expect(isPostMarketWindow(d)).toBe(true);
  });

  it('isPostMarketWindow returns false for 15:00', () => {
    const d = new Date(2026, 0, 15, 15, 0, 0);
    expect(isPostMarketWindow(d)).toBe(false);
  });

  it('isPostMarketWindow returns false for 17:30', () => {
    const d = new Date(2026, 0, 15, 17, 30, 0);
    expect(isPostMarketWindow(d)).toBe(false);
  });

  it('isPostMarketWindow returns false for 10:00', () => {
    const d = new Date(2026, 0, 15, 10, 0, 0);
    expect(isPostMarketWindow(d)).toBe(false);
  });
});