// SPDX-License-Identifier: MIT
/**
 * 管线模式检测工具 — v4.0 三模式自适应管线核心模块。
 *
 * 根据 market_regime 判档结果和当前日期，自动判断管线运行模式：
 *   - normal: 标准 v3.1 四阶段管线
 *   - earnings_season: 财报季增强模式，自动激活财报子管线
 *   - risk_off: 市场防守模式，选股降级 + 风控收紧
 *
 * 优先级：risk_off > earnings_season > normal
 */

// ── 类型 ──────────────────────────────────────────────────

export type PipelineMode = 'normal' | 'earnings_season' | 'risk_off';

export interface ModeResult {
  mode: PipelineMode;
  reason: string;
  earningsSeason: boolean;
  riskOff: boolean;
}

export interface RiskOffParams {
  concentration: {
    mainBoard: number;    // 主板/创业板
    starBoard: number;    // 科创板
    hkStock: number;      // 港股
  };
  circuitBreaker: {
    dailyLossLimitPct: number;
    maxConsecutiveLosses: number;
  };
  newPositionAllowed: boolean;
}

export interface NormalParams {
  concentration: {
    mainBoard: number;
    starBoard: number;
    hkStock: number;
  };
  circuitBreaker: {
    dailyLossLimitPct: number;
    maxConsecutiveLosses: number;
  };
  newPositionAllowed: boolean;
}

// ── 常量 ──────────────────────────────────────────────────

/** 财报季月份窗口（1-4月=年报+一季报，7-8月=中报，10月=三季报） */
const EARNINGS_SEASON_MONTHS = new Set([1, 2, 3, 4, 7, 8, 10]);

/** 防守 regime 关键词（中文） */
const RISK_OFF_KEYWORDS = ['防守', 'risk_off', 'bear', 'bearish', 'risk-off'];

/** 标准风控参数 */
const NORMAL_PARAMS: NormalParams = {
  concentration: { mainBoard: 10, starBoard: 25, hkStock: 15 },
  circuitBreaker: { dailyLossLimitPct: -2, maxConsecutiveLosses: 5 },
  newPositionAllowed: true,
};

/** 防守模式收紧参数 */
const RISK_OFF_PARAMS: RiskOffParams = {
  concentration: { mainBoard: 5, starBoard: 15, hkStock: 10 },
  circuitBreaker: { dailyLossLimitPct: -1.5, maxConsecutiveLosses: 3 },
  newPositionAllowed: false,
};

// ── 判断函数 ──────────────────────────────────────────────

/**
 * 判断当前日期是否在财报季窗口内。
 *
 * 财报季窗口：
 *   - 1-4 月：年报 + 一季报密集披露期
 *   - 7-8 月：中报密集披露期
 *   - 10 月：三季报密集披露期
 *
 * @param date  日期，默认今天
 * @returns     true 表示在财报季窗口内
 */
export function isEarningsSeason(date?: Date): boolean {
  const d = date ?? new Date();
  return EARNINGS_SEASON_MONTHS.has(d.getMonth() + 1); // getMonth() 返回 0-11
}

/**
 * 判断 regime 字符串是否为防守模式。
 *
 * 支持中文（"防守"）和英文（"risk_off", "bearish"）关键词。
 *
 * @param regime  market_regime_switch_skill 输出的判档结果
 * @returns      true 表示该 regime 是防守
 */
export function isRiskOffRegime(regime: string): boolean {
  const lower = regime.toLowerCase().trim();
  return RISK_OFF_KEYWORDS.some((kw) => lower.includes(kw));
}

// ── 模式检测 ──────────────────────────────────────────────

/**
 * 根据 market_regime 判档结果和当前日期，检测管线运行模式。
 *
 * 优先级：risk_off > earnings_season > normal
 * 即：市场防守时即使处于财报季，也优先使用 risk_off 模式。
 *
 * @param regime  market_regime_switch_skill 输出的判档结果
 * @param date    当前日期，默认今天
 * @returns       模式检测结果
 */
export function detectMode(regime: string, date?: Date): ModeResult {
  const riskOff = isRiskOffRegime(regime);
  const earnings = isEarningsSeason(date);

  if (riskOff) {
    return {
      mode: 'risk_off',
      reason: '市场判档为防守阶段 — 选股链降级为仅观察，风控阈值收紧',
      earningsSeason: earnings,
      riskOff: true,
    };
  }

  if (earnings) {
    return {
      mode: 'earnings_season',
      reason: '当前处于财报季窗口 — 自动激活财报预判→解读→机会子管线',
      earningsSeason: true,
      riskOff: false,
    };
  }

  return {
    mode: 'normal',
    reason: '市场环境正常 — 沿用标准 v3.1 四阶段管线',
    earningsSeason: false,
    riskOff: false,
  };
}

// ── 参数获取 ──────────────────────────────────────────────

/**
 * 获取防守模式下的收紧风控参数。
 */
export function getRiskOffParams(): RiskOffParams {
  return { ...RISK_OFF_PARAMS };
}

/**
 * 获取标准模式下的风控参数。
 */
export function getNormalParams(): NormalParams {
  return { ...NORMAL_PARAMS };
}

/**
 * 根据模式获取对应的风控参数。
 *
 * @param mode  管线模式
 * @returns     对应模式的风控参数
 */
export function getParamsForMode(mode: PipelineMode): NormalParams | RiskOffParams {
  return mode === 'risk_off' ? getRiskOffParams() : getNormalParams();
}

// ── 人类可读描述 ──────────────────────────────────────────

/**
 * 获取模式的中文描述。
 */
export function getModeDescription(mode: PipelineMode): string {
  switch (mode) {
    case 'normal':
      return '日常模式 — 标准 v3.1 四阶段管线，选股/信号/交易/风控全链路';
    case 'earnings_season':
      return '财报季模式 — 标准管线 + 财报预判→解读→机会子管线，盘前/盘后闭环增强';
    case 'risk_off':
      return '防守模式 — 选股仅观察不建仓，集中度上限减半，熔断阈值收紧至 1.5%/3次';
  }
}

/**
 * 获取模式对应的子管线技能列表。
 *
 * @param mode  管线模式
 * @returns     该模式下需要激活的额外技能名称列表
 */
export function getSubPipelineSkills(mode: PipelineMode): string[] {
  switch (mode) {
    case 'earnings_season':
      return [
        'earnings_calendar_planner_skill',
        'earnings_preview_skill',
        'earnings_reaction_interpreter_skill',
      ];
    case 'risk_off':
      return [
        'intraday_abnormal_move_alert_skill',
        'support_break_warning_skill',
      ];
    default:
      return [];
  }
}

// ── 盘前/盘后判断 ─────────────────────────────────────────

/**
 * 判断当前时间是否在盘前窗口（8:30-9:15）。
 */
export function isPreMarketWindow(date?: Date): boolean {
  const d = date ?? new Date();
  const h = d.getHours();
  const m = d.getMinutes();
  const totalMinutes = h * 60 + m;
  return totalMinutes >= 8 * 60 + 30 && totalMinutes < 9 * 60 + 15;
}

/**
 * 判断当前时间是否在盘后窗口（15:30-17:00）。
 */
export function isPostMarketWindow(date?: Date): boolean {
  const d = date ?? new Date();
  const h = d.getHours();
  const m = d.getMinutes();
  const totalMinutes = h * 60 + m;
  return totalMinutes >= 15 * 60 + 30 && totalMinutes < 17 * 60;
}