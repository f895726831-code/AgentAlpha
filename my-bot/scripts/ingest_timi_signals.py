"""
TiMi 信号摄入器 — 将 TiMi 信号转换为信号管线标准格式。

读取 TiMi 信号 JSON → 转换为 SignalRecord 格式 → 写入 signals.jsonl。
使 TiMi 信号成为信号追踪/回测管线的一等公民（source='timi'）。

用法:
    python scripts/ingest_timi_signals.py                      # 摄入 timi_signals.json
    python scripts/ingest_timi_signals.py --source midday     # 摄入 timi_signals_midday.json
    python scripts/ingest_timi_signals.py --dry-run           # 仅打印，不写入
"""

import json
import sys
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SIGNALS_JSONL = PROJECT_ROOT / "data" / "signals.jsonl"


def load_timi_signals(source: str = "daily") -> Optional[Dict[str, Any]]:
    """加载 TiMi 信号 JSON"""
    if source == "midday":
        path = PROJECT_ROOT / "data" / "output" / "timi_signals_midday.json"
    else:
        path = PROJECT_ROOT / "data" / "output" / "timi_signals.json"

    if not path.exists():
        return None

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def tiMi_to_signal_record(
    signal: Dict[str, Any],
    session_date: str,
) -> Dict[str, Any]:
    """
    TiMi signal → SignalRecord 格式。

    TiMi signal 字段:
      symbol, strategy_type, direction, confidence, trigger_price,
      stop_loss, take_profit, edge, win_rate

    SignalRecord 字段:
      signal_id, ticker, generated_at, session_date, signal,
      confidence, signal_price, indicators, rationale,
      gap_analysis, volume_check, executed_at, execution_price,
      delay_minutes, delay_sessions, first_trigger_date,
      verified_at, next_day_open, next_day_close, next_day_pct,
      verdict, source, notes
    """
    direction = signal.get("direction", "HOLD")
    signal_type = direction  # TiMi 使用 BUY/SELL/HOLD，与 SignalRecord 一致

    # 构建 rationale
    strategy = signal.get("strategy_type", "unknown")
    trigger = signal.get("trigger_price", 0)
    stop = signal.get("stop_loss", 0)
    target = signal.get("take_profit", 0)
    edge = signal.get("edge", 0)
    win_rate = signal.get("win_rate", 0)

    rationale = (
        f"TiMi {strategy}: trigger={trigger}, stop={stop}, "
        f"target={target}, edge={edge:.1%}, win_rate={win_rate:.1%}"
    )

    return {
        "signal_id": f"sig-timi-{uuid.uuid4()}",
        "ticker": signal.get("symbol", ""),
        "generated_at": datetime.now().isoformat(),
        "session_date": session_date,
        "signal": signal_type,
        "confidence": int(signal.get("confidence", 0) * 100),
        "signal_price": signal.get("trigger_price", 0),
        "indicators": [f"timi:{strategy}"],
        "rationale": rationale,
        "gap_analysis": {
            "gap_direction": "none",
            "gap_pct": None,
            "gap_risk": "none",
        },
        "volume_check": {
            "v_reversal_detected": False,
            "v_reversal_magnitude_pct": None,
            "volume_vs_5d_avg": "normal",
            "stop_loss_override": False,
            "stop_loss_override_reason": None,
        },
        "executed_at": None,
        "execution_price": None,
        "delay_minutes": None,
        "delay_sessions": None,
        "first_trigger_date": session_date,
        "verified_at": None,
        "next_day_open": None,
        "next_day_close": None,
        "next_day_pct": None,
        "verdict": "pending",
        "source": "timi",
        "notes": json.dumps({
            "stop_loss": signal.get("stop_loss"),
            "take_profit": signal.get("take_profit"),
            "edge": edge,
            "win_rate": win_rate,
            "strategy_type": strategy,
        }, ensure_ascii=False),
    }


def ingest(signals_data: Dict[str, Any], dry_run: bool = False) -> int:
    """摄入 TiMi 信号到 signals.jsonl"""
    signals = signals_data.get("signals", [])
    if not signals:
        print("[TiMi Ingest] no signals to ingest")
        return 0

    today = date.today().isoformat()
    records = [tiMi_to_signal_record(s, today) for s in signals]

    if dry_run:
        print(f"[TiMi Ingest] DRY RUN — would ingest {len(records)} signals:")
        for r in records:
            print(f"  {r['ticker']} {r['signal']} conf={r['confidence']} "
                  f"price={r['signal_price']} [{r['indicators'][0]}]")
        return len(records)

    # 追加写入 JSONL
    SIGNALS_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with open(SIGNALS_JSONL, "a", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"[TiMi Ingest] ingested {len(records)} signals → {SIGNALS_JSONL}")
    return len(records)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="TiMi 信号摄入器")
    parser.add_argument("--source", type=str, default="daily",
                        choices=["daily", "midday"],
                        help="信号来源: daily=盘前, midday=盘中 (default: daily)")
    parser.add_argument("--dry-run", action="store_true",
                        help="仅打印，不写入 signals.jsonl")
    args = parser.parse_args()

    data = load_timi_signals(args.source)
    if data is None:
        print(f"[TiMi Ingest] no signals file for source={args.source}, skipping")
        return

    if not data.get("success"):
        source = data.get("source", "unknown")
        print(f"[TiMi Ingest] TiMi result not successful (source={source}), skipping")
        return

    count = ingest(data, dry_run=args.dry_run)
    if count > 0:
        print(f"[TiMi Ingest] done: {count} TiMi signals ingested")


if __name__ == "__main__":
    main()