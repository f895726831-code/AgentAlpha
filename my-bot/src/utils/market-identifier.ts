// SPDX-License-Identifier: MIT
/**
 * 市场标识统一方案 — 所有模块必须使用 ticker 后缀区分市场。
 *
 * ## 标准方案 (v3.1+)
 *
 * 使用 ticker 代码后缀判断市场，无需额外的 market/type 字段:
 *
 * | 后缀 | 市场 | 说明 |
 * |---|---|---|
 * | `.SH` | A股上海 | 主板 (600xxx/601xxx/603xxx) + 科创板 (688xxx) + ETF |
 * | `.SZ` | A股深圳 | 主板 (000xxx/001xxx) + 创业板 (300xxx) + 中小板 (002xxx) |
 * | `.HK` | 港股 | 香港联交所，5位数字代码 |
 *
 * ## 集中度限制 (按板块细分)
 *
 * | 板块 | 后缀 | 代码范围 | 集中度上限 |
 * |---|---|---|---|
 * | 科创板 | .SH | 688xxx | ≤ 25% (最小交易单位 200 股) |
 * | 主板/创业板 | .SH/.SZ | 其他 | ≤ 10% |
 * | 港股 | .HK | 全部 | ≤ 15% (含汇兑风险缓冲) |
 *
 * ## 废弃方案
 *
 * - `portfolio.json` 的 `"market": "A"/"HK"` 字段 — 冗余，保留向后兼容但不再使用
 * - `xueqiu_watchlist.json` 的 `"type": 11/30/82/13` — 仅用于雪球 API 对接，内部逻辑不依赖
 */

/** 从 ticker 代码提取市场后缀 */
export function getMarketSuffix(ticker: string): '.SH' | '.SZ' | '.HK' | null {
  if (ticker.endsWith('.SH')) return '.SH';
  if (ticker.endsWith('.SZ')) return '.SZ';
  if (ticker.endsWith('.HK')) return '.HK';
  return null;
}

/** 判断是否为科创板股票 */
export function isSTARBoard(ticker: string): boolean {
  return ticker.startsWith('688') && ticker.endsWith('.SH');
}

/** 判断是否为创业板股票 */
export function isChiNext(ticker: string): boolean {
  return ticker.startsWith('300') && ticker.endsWith('.SZ');
}

/** 获取集中度上限 (占总资产 %) */
export function getConcentrationLimit(ticker: string): number {
  if (isSTARBoard(ticker)) return 25;
  if (ticker.endsWith('.HK')) return 15;
  return 10;
}

/** 判断是否为有效 A 股代码 */
export function isValidAShare(ticker: string): boolean {
  return ticker.endsWith('.SH') || ticker.endsWith('.SZ');
}

/** 判断是否为港股代码 */
export function isValidHKStock(ticker: string): boolean {
  return ticker.endsWith('.HK');
}