#!/usr/bin/env python3
"""
TiMi LLM 编译器 CLI — LLM Agent 输出策略 JSON → compiler → serializer → disk

离线编译流程:
  1. LLM Agent 读取市场全景 → 输出策略配置 JSON
  2. 本脚本读取 JSON → StrategyCompiler.compile()
  3. StrategySerializer.save() → data/strategies/{date}.json
  4. 次日 timi_daily.py 自动加载最新策略

用法:
    python scripts/compile_timi_strategies.py                    # 从 stdin 读取 LLM JSON
    python scripts/compile_timi_strategies.py --file config.json # 从文件读取
    python scripts/compile_timi_strategies.py --dry-run          # 仅编译不保存

LLM JSON 输入格式 (由 LLM Agent 输出):
{
  "regime": "恐慌",
  "bias": "left_priority",
  "mode": "normal",
  "strategies": [
    {"type": "T3_oversold_bounce", "active": true, "params": {...}},
    {"type": "T2_pullback", "active": true, "params": {...}},
    {"type": "T1_breakout", "active": false, "params": {}}
  ],
  "filters": {"exclude_st": true, "exclude_new_listings_days": 60},
  "meta": {"compiled_by": "claude-opus", "reasoning": "..."}
}
"""

import json
import sys
from datetime import datetime
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from strategies.compiler import StrategyCompiler
from strategies.serializer import StrategySerializer


def load_market_context() -> dict:
    """加载当前市场全景，供 LLM 参考（调试用）"""
    context = {}

    # STABLE regime
    alloc_path = PROJECT_ROOT / "data/output/asset_allocation.json"
    if alloc_path.exists():
        with open(alloc_path, encoding="utf-8") as f:
            alloc = json.load(f)
        context["macro"] = {
            "quadrant": alloc.get("macro_quadrant", {}).get("quadrant_label_cn", "?"),
            "confidence": alloc.get("macro_quadrant", {}).get("confidence", 0),
            "mode": alloc.get("mode_override", {}).get("mode", "normal"),
            "summary": alloc.get("summary", ""),
        }

    # Sector signals
    sec_path = PROJECT_ROOT / "data/output/sector_signals.json"
    if sec_path.exists():
        with open(sec_path, encoding="utf-8") as f:
            sec = json.load(f)
        context["sector"] = {
            "date": sec.get("date", ""),
            "top_5": [
                {"name": s["sector_name"], "score": s["score"], "category": s["category"]}
                for s in sec.get("top_5_selected", [])[:5]
            ],
        }

    # Market technical state
    md_path = PROJECT_ROOT / "data/output/timi_market_data.json"
    if md_path.exists():
        with open(md_path, encoding="utf-8") as f:
            md = json.load(f)
        stocks = md.get("market_data", [])
        n = len(stocks)
        if n > 0:
            context["technical"] = {
                "n_stocks": n,
                "bullish": sum(1 for s in stocks if s.get("close", 0) > s.get("ma_20", 0) > s.get("ma_60", 0)),
                "bearish": sum(1 for s in stocks if s.get("close", 0) < s.get("ma_60", 0)),
                "oversold": sum(1 for s in stocks if s.get("rsi_14", 50) < 30),
                "overbought": sum(1 for s in stocks if s.get("rsi_14", 50) > 70),
                "avg_rsi": round(sum(s.get("rsi_14", 50) for s in stocks) / n, 1),
            }

    return context


def main():
    import argparse
    parser = argparse.ArgumentParser(description="TiMi LLM 策略编译器")
    parser.add_argument("--file", type=str, default=None,
                        help="LLM JSON 配置文件路径 (stdin if omitted)")
    parser.add_argument("--dry-run", action="store_true",
                        help="仅编译不保存到 disk")
    parser.add_argument("--show-context", action="store_true",
                        help="显示当前市场全景（供 LLM 参考）")
    parser.add_argument("--date", type=str, default=datetime.now().strftime("%Y-%m-%d"),
                        help="策略日期")
    args = parser.parse_args()

    # ── 市场全景 ──
    if args.show_context:
        context = load_market_context()
        print(json.dumps(context, indent=2, ensure_ascii=False))
        return 0

    # ── 读取 LLM JSON ──
    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            llm_json = json.load(f)
    else:
        llm_json = json.load(sys.stdin)

    # ── 编译 ──
    compiler = StrategyCompiler()
    result = compiler.compile(llm_json)

    # ── 验证 ──
    validation_warnings = compiler.validate(result)
    result.warnings.extend(validation_warnings)

    # ── 输出编译结果 ──
    print(f"\n{'='*60}")
    print(f"TiMi 策略编译器 — {args.date}")
    print(f"{'='*60}")
    print(f"Regime: {result.regime}")
    print(f"Bias:   {result.bias}")
    print(f"Mode:   {result.mode}")
    print(f"Meta:   {result.meta.get('compiled_by', '?')} "
          f"@ {result.meta.get('timestamp', '?')}")
    print()

    active = result.active_strategies
    inactive = [k for k, v in result.strategies.items() if not v.params.active]

    print(f"活跃策略 ({len(active)}):")
    for stype, strategy in active.items():
        params = strategy.params.filters
        key_params = {k: v for k, v in params.items()
                      if v != strategy.DEFAULT_FILTERS.get(k)}
        param_str = ", ".join(f"{k}={v}" for k, v in list(key_params.items())[:5])
        print(f"  ✓ {stype}: {param_str or '默认参数'}")

    if inactive:
        print(f"\n停用策略 ({len(inactive)}):")
        for stype in inactive:
            print(f"  ✗ {stype}")

    if result.warnings:
        print(f"\n⚠ 警告 ({len(result.warnings)}):")
        for w in result.warnings:
            print(f"  • {w}")

    if result.errors:
        print(f"\n❌ 错误 ({len(result.errors)}):")
        for e in result.errors:
            print(f"  • {e}")
        return 1

    # ── 序列化 ──
    if args.dry_run:
        print(f"\n[Dry-run] 未保存到 disk")
        return 0

    serializer = StrategySerializer(storage_dir=PROJECT_ROOT / "data" / "strategies")
    # 注入扩展 meta 到 result
    result.meta["reasoning"] = result.meta.get("reasoning", "")
    result.meta["market_regime"] = result.regime
    result.meta["technical_state"] = load_market_context().get("technical", {})
    output_path = serializer.serialize(result, date_str=args.date)

    print(f"\n✅ 已部署: {output_path}")
    print(f"  次日 timi_daily.py 将自动加载此策略")

    return 0


if __name__ == "__main__":
    sys.exit(main())