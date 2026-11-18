// SPDX-License-Identifier: MIT
// 宏观 EDB 指标定义 — 用于 market-watcher 宏观背景判断
//
// 每个指标包含：
//   - code:    EDB 指标代码（用于 fetch 模式精确提数）
//   - name:    中文名称
//   - query:   自然语言搜索词（用于 searchFetch 模式）
//   - freq:    更新频率（month / quarter / daily）
//   - bull:    对 A 股偏多的方向（higher / lower / stable）
//   - threshold: 关键阈值（用于判断多空）

export interface MacroIndicator {
  code: string;
  name: string;
  query: string;
  freq: 'month' | 'quarter' | 'daily';
  bull: 'higher' | 'lower' | 'stable';
  threshold?: { value: number; desc: string };
}

// 核心宏观指标 — 覆盖增长、通胀、流动性、估值四大维度
export const MACRO_INDICATORS: MacroIndicator[] = [
  // ===== 增长 =====
  {
    code: 'M0017126',
    name: '制造业PMI',
    query: '制造业PMI',
    freq: 'month',
    bull: 'higher',
    threshold: { value: 50, desc: '荣枯线：>50 扩张，<50 收缩' },
  },
  {
    code: 'M0017128',
    name: 'PMI:新订单',
    query: '制造业PMI新订单',
    freq: 'month',
    bull: 'higher',
    threshold: { value: 50, desc: '需求前瞻：>50 需求扩张' },
  },
  {
    code: 'M0039354',
    name: 'GDP当季同比',
    query: 'GDP不变价当季同比',
    freq: 'quarter',
    bull: 'higher',
    threshold: { value: 5.0, desc: '增长底线：<5% 偏弱' },
  },
  {
    code: 'M0000545',
    name: '工业增加值同比',
    query: '工业增加值同比',
    freq: 'month',
    bull: 'higher',
    threshold: { value: 5.0, desc: '工业活力：<5% 偏弱' },
  },

  // ===== 通胀 =====
  {
    code: 'M0000612',
    name: 'CPI同比',
    query: 'CPI同比',
    freq: 'month',
    bull: 'stable',
    threshold: { value: 3.0, desc: '警戒线：>3% 通胀压力，<0% 通缩风险' },
  },

  // ===== 流动性 =====
  {
    code: 'M0001385',
    name: 'M2同比',
    query: 'M2同比',
    freq: 'month',
    bull: 'higher',
    threshold: { value: 8.0, desc: '流动性：<8% 偏紧，>10% 偏宽' },
  },
  {
    code: 'M5206730',
    name: '社会融资规模',
    query: '社会融资规模当月值',
    freq: 'month',
    bull: 'higher',
    threshold: { value: 15000, desc: '信用扩张：<1.5万亿偏弱' },
  },

  // ===== 估值 =====
  {
    code: 'M0342074',
    name: '沪深300PE(TTM)',
    query: '沪深300滚动市盈率',
    freq: 'daily',
    bull: 'lower',
    threshold: { value: 12, desc: '低估：<12 倍低估，>18 倍高估' },
  },
];

// 用于报告中展示的宏观快照
export interface MacroSnapshot {
  date: string;
  indicators: {
    code: string;
    name: string;
    value: number;
    unit: string;
    prevValue?: number;
    change?: number;
    signal: 'bullish' | 'bearish' | 'neutral';
  }[];
}

// 信号判断
export function judgeSignal(
  indicator: MacroIndicator,
  value: number,
): 'bullish' | 'bearish' | 'neutral' {
  if (!indicator.threshold) return 'neutral';

  const { value: threshold, desc: _desc } = indicator.threshold;

  if (indicator.bull === 'higher') {
    if (value > threshold) return 'bullish';
    if (value < threshold * 0.95) return 'bearish';
    return 'neutral';
  }

  if (indicator.bull === 'lower') {
    if (value < threshold) return 'bullish';
    if (value > threshold * 1.5) return 'bearish';
    return 'neutral';
  }

  // stable: within threshold range is neutral
  if (indicator.bull === 'stable') {
    if (value < 0) return 'bearish'; // deflation
    if (value > threshold) return 'bearish'; // inflation
    return 'neutral';
  }

  return 'neutral';
}