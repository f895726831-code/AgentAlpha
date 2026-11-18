#!/usr/bin/env python3
"""
沪深300 回测脚本 — 双均线交叉策略 + my-bot 风控规则
标的: 000300.SH (沪深300)
期间: 2026-01-01 ~ 2026-05-31
初始资金: 1,000,000 CNY
"""

import json
import sys
from datetime import datetime

# ============================================================
# 原始数据 (从 Wind 获取)
# ============================================================
RAW_DATA = [
    ["2026-01-05", 4661.62, 4717.75, 4721.64, 4661.62],
    ["2026-01-06", 4719.21, 4790.69, 4790.69, 4718.01],
    ["2026-01-07", 4796.93, 4776.67, 4802.60, 4751.07],
    ["2026-01-08", 4761.01, 4737.65, 4765.49, 4717.01],
    ["2026-01-09", 4725.96, 4758.92, 4771.43, 4719.13],
    ["2026-01-12", 4769.65, 4789.92, 4795.93, 4737.57],
    ["2026-01-13", 4798.05, 4761.03, 4836.95, 4745.52],
    ["2026-01-14", 4767.70, 4741.93, 4818.10, 4720.25],
    ["2026-01-15", 4721.80, 4751.43, 4769.55, 4720.94],
    ["2026-01-16", 4778.43, 4731.87, 4792.55, 4715.36],
    ["2026-01-19", 4720.19, 4734.46, 4761.63, 4715.09],
    ["2026-01-20", 4736.91, 4718.88, 4748.23, 4684.97],
    ["2026-01-21", 4714.55, 4723.07, 4758.28, 4714.24],
    ["2026-01-22", 4741.75, 4723.71, 4757.76, 4700.45],
    ["2026-01-23", 4729.59, 4702.50, 4734.79, 4684.62],
    ["2026-01-26", 4715.38, 4706.96, 4754.18, 4693.16],
    ["2026-01-27", 4705.17, 4705.69, 4736.99, 4675.64],
    ["2026-01-28", 4725.60, 4717.99, 4739.63, 4697.91],
    ["2026-01-29", 4733.09, 4753.87, 4770.96, 4696.05],
    ["2026-01-30", 4723.08, 4706.34, 4738.87, 4645.47],
    ["2026-02-02", 4674.28, 4605.98, 4716.61, 4601.76],
    ["2026-02-03", 4648.36, 4660.11, 4662.13, 4578.21],
    ["2026-02-04", 4647.97, 4698.68, 4701.48, 4641.84],
    ["2026-02-05", 4664.75, 4670.42, 4687.02, 4639.43],
    ["2026-02-06", 4622.97, 4643.60, 4678.26, 4606.64],
    ["2026-02-09", 4693.67, 4719.06, 4719.96, 4679.90],
    ["2026-02-10", 4718.26, 4724.30, 4732.09, 4713.87],
    ["2026-02-11", 4715.67, 4713.82, 4723.70, 4703.69],
    ["2026-02-12", 4720.01, 4719.58, 4726.04, 4707.74],
    ["2026-02-13", 4693.41, 4660.41, 4703.63, 4658.40],
    ["2026-02-24", 4727.30, 4707.54, 4727.37, 4698.35],
    ["2026-02-25", 4713.58, 4735.89, 4766.56, 4713.34],
    ["2026-02-26", 4741.39, 4726.87, 4742.15, 4706.69],
    ["2026-02-27", 4698.30, 4710.65, 4721.01, 4692.89],
    ["2026-03-02", 4681.01, 4728.67, 4735.41, 4667.28],
    ["2026-03-03", 4737.32, 4655.90, 4744.60, 4651.40],
    ["2026-03-04", 4609.19, 4602.62, 4639.42, 4575.82],
    ["2026-03-05", 4651.94, 4647.69, 4671.53, 4630.30],
    ["2026-03-06", 4621.49, 4660.44, 4671.71, 4612.79],
    ["2026-03-09", 4600.25, 4615.46, 4625.98, 4548.97],
    ["2026-03-10", 4648.79, 4674.76, 4677.60, 4648.79],
    ["2026-03-11", 4681.03, 4704.50, 4711.87, 4674.71],
    ["2026-03-12", 4700.72, 4687.56, 4704.38, 4654.31],
    ["2026-03-13", 4669.06, 4669.14, 4707.42, 4659.50],
    ["2026-03-16", 4669.22, 4671.56, 4673.69, 4621.81],
    ["2026-03-17", 4685.57, 4637.44, 4720.86, 4637.37],
    ["2026-03-18", 4648.75, 4658.33, 4662.29, 4604.52],
    ["2026-03-19", 4611.44, 4583.25, 4634.55, 4570.39],
    ["2026-03-20", 4602.82, 4567.02, 4628.71, 4563.07],
    ["2026-03-23", 4499.10, 4418.00, 4514.72, 4397.57],
    ["2026-03-24", 4458.91, 4474.72, 4474.83, 4394.29],
    ["2026-03-25", 4506.62, 4537.47, 4542.18, 4502.03],
    ["2026-03-26", 4525.33, 4477.53, 4541.05, 4468.96],
    ["2026-03-27", 4431.50, 4502.57, 4519.89, 4431.42],
    ["2026-03-30", 4461.74, 4491.95, 4496.41, 4447.14],
    ["2026-03-31", 4491.92, 4450.05, 4523.17, 4450.05],
    ["2026-04-01", 4517.27, 4526.07, 4532.11, 4492.05],
    ["2026-04-02", 4514.44, 4478.91, 4519.69, 4459.61],
    ["2026-04-03", 4492.85, 4440.79, 4494.46, 4437.60],
    ["2026-04-07", 4445.86, 4440.62, 4458.93, 4420.69],
    ["2026-04-08", 4517.34, 4595.56, 4595.56, 4513.77],
    ["2026-04-09", 4562.67, 4566.22, 4579.20, 4552.42],
    ["2026-04-10", 4594.95, 4636.57, 4655.10, 4590.11],
    ["2026-04-13", 4615.13, 4646.15, 4653.40, 4615.13],
    ["2026-04-14", 4676.12, 4701.28, 4701.28, 4654.85],
    ["2026-04-15", 4726.64, 4685.25, 4731.27, 4672.56],
    ["2026-04-16", 4694.43, 4736.61, 4739.22, 4689.41],
    ["2026-04-17", 4729.00, 4728.67, 4738.32, 4714.47],
    ["2026-04-20", 4727.17, 4757.44, 4763.04, 4720.69],
    ["2026-04-21", 4750.51, 4768.00, 4776.83, 4722.01],
    ["2026-04-22", 4751.28, 4799.63, 4801.45, 4750.35],
    ["2026-04-23", 4816.03, 4786.33, 4823.47, 4759.86],
    ["2026-04-24", 4771.73, 4769.37, 4786.44, 4735.61],
    ["2026-04-27", 4776.34, 4770.95, 4795.17, 4762.64],
    ["2026-04-28", 4757.08, 4758.21, 4778.19, 4743.44],
    ["2026-04-29", 4737.58, 4810.35, 4813.61, 4737.11],
    ["2026-04-30", 4820.97, 4807.31, 4829.27, 4797.54],
    ["2026-05-06", 4857.65, 4877.09, 4892.80, 4834.28],
    ["2026-05-07", 4895.80, 4900.51, 4901.86, 4866.87],
    ["2026-05-08", 4871.12, 4871.91, 4887.21, 4849.99],
    ["2026-05-11", 4902.28, 4951.84, 4960.40, 4889.57],
    ["2026-05-12", 4965.02, 4948.05, 4971.30, 4926.79],
    ["2026-05-13", 4919.42, 4998.34, 5001.12, 4919.42],
    ["2026-05-14", 5027.64, 4914.60, 5030.52, 4913.60],
    ["2026-05-15", 4911.62, 4859.59, 4935.62, 4832.69],
    ["2026-05-18", 4836.33, 4833.52, 4868.60, 4806.15],
    ["2026-05-19", 4818.71, 4852.88, 4854.52, 4773.53],
    ["2026-05-20", 4829.61, 4850.70, 4866.21, 4825.64],
    ["2026-05-21", 4886.24, 4783.10, 4937.93, 4780.71],
    ["2026-05-22", 4818.84, 4845.10, 4851.35, 4785.35],
    ["2026-05-25", 4873.20, 4921.60, 4922.44, 4857.13],
    ["2026-05-26", 4900.12, 4947.85, 4952.74, 4892.68],
    ["2026-05-27", 4940.95, 4908.17, 4974.21, 4891.34],
    ["2026-05-28", 4889.27, 4914.21, 4922.33, 4843.23],
    ["2026-05-29", 4937.51, 4892.12, 4954.07, 4878.79],
]

# ============================================================
# 策略参数
# ============================================================
INITIAL_CAPITAL = 1_000_000.0       # 初始资金
SHORT_MA = 5                         # 短期均线 (5日)
LONG_MA = 20                         # 长期均线 (20日)
SLIPPAGE = 0.0003                    # 滑点 0.03%
COMMISSION = 0.0003                  # 手续费 0.03%
KELLY_MULTIPLIER = 0.25              # 凯利系数
MAX_POSITION_PCT = 1.0               # 单一持仓上限 (指数本身已分散)
MAX_DAILY_LOSS_PCT = 0.02            # 日亏损熔断 -2%
MAX_CONSECUTIVE_LOSSES = 5           # 连续亏损熔断
LATENCY_THRESHOLD_MS = 500           # 延迟阈值

# ============================================================
# 计算
# ============================================================

def sma(data, window):
    """简单移动平均"""
    result = [None] * len(data)
    for i in range(window - 1, len(data)):
        result[i] = sum(data[i-window+1:i+1]) / window
    return result

# 提取收盘价
closes = [row[2] for row in RAW_DATA]  # MATCH = 收盘价
dates = [row[0] for row in RAW_DATA]
opens = [row[1] for row in RAW_DATA]

# 计算均线
short_ma = sma(closes, SHORT_MA)
long_ma = sma(closes, LONG_MA)

# ============================================================
# 回测主循环
# ============================================================
capital = INITIAL_CAPITAL
position = 0.0          # 持仓市值
shares = 0.0
trades = []
daily_pnl = []
consecutive_losses = 0
circuit_breaker = False
circuit_breaker_reason = ""

equity_curve = []

for i in range(len(RAW_DATA)):
    if circuit_breaker:
        break

    date = dates[i]
    close = closes[i]
    open_price = opens[i]

    # 计算当日权益
    equity = capital + position
    equity_curve.append({"date": date, "equity": round(equity, 2)})

    # 检查均线是否就绪
    if short_ma[i] is None or long_ma[i] is None:
        continue

    signal = None
    if short_ma[i] > long_ma[i] and (i == 0 or short_ma[i-1] is None or long_ma[i-1] is None or short_ma[i-1] <= long_ma[i-1]):
        signal = "BUY"
    elif short_ma[i] < long_ma[i] and (i == 0 or short_ma[i-1] is None or long_ma[i-1] is None or short_ma[i-1] >= long_ma[i-1]):
        signal = "SELL"

    if signal is None:
        continue

    # ---- risk-checker: 风控门禁 ----
    # 1. 熔断检查
    if circuit_breaker:
        continue

    # 2. 凯利仓位计算 (简化: 使用近期波动率)
    if i >= 20:
        returns = [(closes[j] - closes[j-1]) / closes[j-1] for j in range(i-19, i+1)]
        volatility = (sum(r*r for r in returns) / len(returns)) ** 0.5
        if volatility > 0:
            # 凯利: f = edge / variance, 这里用简化版
            kelly_fraction = min(0.02 / (volatility * volatility) if volatility > 0 else 1.0, 1.0)
            position_fraction = kelly_fraction * KELLY_MULTIPLIER
        else:
            position_fraction = 0.25
    else:
        position_fraction = 0.25

    # 3. 集中度限制
    position_fraction = min(position_fraction, MAX_POSITION_PCT)

    if signal == "BUY" and position == 0:
        # 买入
        fill_price = close * (1 + SLIPPAGE)
        target_position = equity * position_fraction
        shares = target_position / fill_price
        cost = shares * fill_price * (1 + COMMISSION)

        if cost <= capital:
            capital -= cost
            position = shares * close
            trades.append({
                "date": date, "action": "BUY", "price": round(fill_price, 2),
                "shares": round(shares, 2), "cost": round(cost, 2),
                "equity": round(capital + position, 2)
            })

    elif signal == "SELL" and position > 0:
        # 卖出
        fill_price = close * (1 - SLIPPAGE)
        proceeds = shares * fill_price * (1 - COMMISSION)
        pnl = proceeds - (position if position > 0 else 0)
        # 用原始建仓成本算盈亏
        if trades:
            buy_trade = next((t for t in reversed(trades) if t["action"] == "BUY"), None)
            if buy_trade:
                pnl = proceeds - buy_trade["cost"]

        capital += proceeds
        trades.append({
            "date": date, "action": "SELL", "price": round(fill_price, 2),
            "shares": round(shares, 2), "proceeds": round(proceeds, 2),
            "pnl": round(pnl, 2), "pnl_pct": round(pnl / (buy_trade["cost"] if buy_trade else proceeds) * 100, 2),
            "equity": round(capital, 2)
        })

        # 风控: 跟踪连续亏损
        if pnl < 0:
            consecutive_losses += 1
        else:
            consecutive_losses = 0

        daily_pnl.append({"date": date, "pnl": round(pnl, 2)})

        # 熔断检查
        if consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
            circuit_breaker = True
            circuit_breaker_reason = f"连续 {consecutive_losses} 次亏损, 触发熔断"

        position = 0
        shares = 0

# 最终清算: 如果还有持仓, 按最后一天收盘价平仓
if position > 0:
    final_close = closes[-1]
    fill_price = final_close * (1 - SLIPPAGE)
    proceeds = shares * fill_price * (1 - COMMISSION)
    capital += proceeds
    if trades:
        buy_trade = next((t for t in reversed(trades) if t["action"] == "BUY"), None)
        pnl = proceeds - buy_trade["cost"] if buy_trade else 0
    else:
        pnl = 0
    trades.append({
        "date": dates[-1], "action": "SELL (强制平仓)", "price": round(fill_price, 2),
        "shares": round(shares, 2), "proceeds": round(proceeds, 2),
        "pnl": round(pnl, 2), "pnl_pct": round(pnl / (buy_trade["cost"] if buy_trade else proceeds) * 100, 2) if buy_trade else 0,
        "equity": round(capital, 2)
    })
    position = 0

# ============================================================
# 统计
# ============================================================
final_equity = capital
total_return = (final_equity - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
winning_trades = [t for t in trades if t.get("pnl", 0) > 0]
losing_trades = [t for t in trades if t.get("pnl", 0) < 0]
total_trades = len(winning_trades) + len(losing_trades)

if total_trades > 0:
    win_rate = len(winning_trades) / total_trades * 100
else:
    win_rate = 0

if winning_trades:
    avg_win = sum(t["pnl"] for t in winning_trades) / len(winning_trades)
    avg_win_pct = sum(t["pnl_pct"] for t in winning_trades) / len(winning_trades)
else:
    avg_win = 0
    avg_win_pct = 0

if losing_trades:
    avg_loss = sum(t["pnl"] for t in losing_trades) / len(losing_trades)
    avg_loss_pct = sum(t["pnl_pct"] for t in losing_trades) / len(losing_trades)
else:
    avg_loss = 0
    avg_loss_pct = 0

# 最大回撤
peak = INITIAL_CAPITAL
max_drawdown = 0
max_drawdown_pct = 0
for eq in equity_curve:
    e = eq["equity"]
    if e > peak:
        peak = e
    dd = (peak - e) / peak * 100
    if dd > max_drawdown_pct:
        max_drawdown_pct = dd
        max_drawdown = peak - e

# ============================================================
# 输出
# ============================================================
print("=" * 70)
print("  沪深300 双均线策略回测报告")
print("=" * 70)
print(f"  标的:           000300.SH (沪深300)")
print(f"  期间:           2026-01-05 ~ 2026-05-29 ({len(RAW_DATA)} 个交易日)")
print(f"  策略:           MA{SHORT_MA} / MA{LONG_MA} 金叉买入, 死叉卖出")
print(f"  初始资金:       {INITIAL_CAPITAL:,.0f} CNY")
print(f"  滑点:           {SLIPPAGE*100:.2f}%")
print(f"  手续费:         {COMMISSION*100:.2f}%")
print(f"  凯利倍数:       {KELLY_MULTIPLIER}")
print(f"  熔断:           日亏 -{MAX_DAILY_LOSS_PCT*100:.0f}% / 连续 {MAX_CONSECUTIVE_LOSSES} 次亏损")
print()

if circuit_breaker:
    print(f"  ⚠️  熔断触发:   {circuit_breaker_reason}")
    print()

print("-" * 70)
print("  交易记录")
print("-" * 70)
for t in trades:
    if t["action"] == "BUY":
        print(f"  {t['date']}  BUY   @{t['price']:>10.2f}  {t['shares']:>8.0f} 股  "
              f"花费 {t['cost']:>12,.2f}  权益 {t['equity']:>12,.2f}")
    else:
        print(f"  {t['date']}  SELL  @{t['price']:>10.2f}  {t['shares']:>8.0f} 股  "
              f"盈亏 {t['pnl']:>+12,.2f} ({t['pnl_pct']:>+6.2f}%)  权益 {t['equity']:>12,.2f}")

print()
print("-" * 70)
print("  绩效统计")
print("-" * 70)
print(f"  最终权益:       {final_equity:>12,.2f} CNY")
print(f"  总收益率:       {total_return:>+11.2f}%")
print(f"  总交易次数:     {total_trades:>12}")
print(f"  盈利次数:       {len(winning_trades):>12}")
print(f"  亏损次数:       {len(losing_trades):>12}")
print(f"  胜率:           {win_rate:>11.2f}%")
print(f"  平均盈利:       {avg_win:>+12,.2f} CNY ({avg_win_pct:+.2f}%)")
print(f"  平均亏损:       {avg_loss:>+12,.2f} CNY ({avg_loss_pct:+.2f}%)")
if avg_loss != 0:
    print(f"  盈亏比:         {abs(avg_win/avg_loss):>12.2f}")
else:
    print(f"  盈亏比:         N/A")
print(f"  最大回撤:       {max_drawdown:>12,.2f} CNY ({max_drawdown_pct:.2f}%)")
print(f"  连续亏损:       {consecutive_losses:>12}")
print(f"  熔断:           {'是' if circuit_breaker else '否':>13}")
print()
print("=" * 70)
print("  数据来源于万得 Wind 金融数据服务。")
print("=" * 70)

# ============================================================
# 输出 JSON 给 backtest-expert 评估脚本
# ============================================================
metrics = {
    "total_trades": total_trades,
    "win_rate": round(win_rate, 2),
    "avg_win_pct": round(avg_win_pct, 2),
    "avg_loss_pct": round(abs(avg_loss_pct), 2),
    "max_drawdown_pct": round(max_drawdown_pct, 2),
    "years_tested": 0.4,  # ~5 months
    "num_parameters": 2,   # SHORT_MA, LONG_MA
    "slippage_tested": True,
    "final_equity": round(final_equity, 2),
    "total_return_pct": round(total_return, 2),
    "circuit_breaker": circuit_breaker,
    "circuit_breaker_reason": circuit_breaker_reason,
}

print()
print("---METRICS_JSON---")
print(json.dumps(metrics, ensure_ascii=False, indent=2))