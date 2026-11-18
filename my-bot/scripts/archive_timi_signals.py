#!/usr/bin/env python3
"""
TiMi Signal Archiver — 每日归档 TiMi 信号并更新 signal_watchlist。

解决两个问题:
  1. TiMi/LLM overlap 验证需要多日数据 — 归档到 timi_signals/{date}.json
  2. TiMi 信号应进入 signal_watchlist 供 trade-planner 使用

用法:
    python scripts/archive_timi_signals.py                     # 归档今日信号
    python scripts/archive_timi_signals.py --update-watchlist  # 归档 + 更新 watchlist
    python scripts/archive_timi_signals.py --date 2026-10-20   # 指定日期
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "data" / "output"
ARCHIVE_DIR = OUTPUT_DIR / "timi_signals"
WATCHLIST_PATH = PROJECT_ROOT / "data" / "signal_watchlist.json"
WATCHLIST_HISTORY = PROJECT_ROOT / "data" / "signal_watchlist_history.json"


def load_timi_signals(date_str: str | None = None) -> Optional[dict]:
    """加载当日 TiMi 信号"""
    path = OUTPUT_DIR / "timi_signals.json"
    if not path.exists():
        print(f"[archive] timi_signals.json 不存在")
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def archive_signals(signals: dict, date_str: str) -> Path:
    """归档到 timi_signals/{date}.json"""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archive_path = ARCHIVE_DIR / f"timi_signals_{date_str}.json"

    archive_data = {
        "date": date_str,
        "archived_at": datetime.now().isoformat(),
        "source": signals.get("source", "unknown"),
        "signal_count": len(signals.get("signals", [])),
        "position_count": len(signals.get("positions", [])),
        "signals": signals.get("signals", []),
        "positions": signals.get("positions", []),
        "execution_summary": signals.get("execution_summary", {}),
        "latency_ms": signals.get("latency_ms", 0),
    }

    with open(archive_path, "w", encoding="utf-8") as f:
        json.dump(archive_data, f, ensure_ascii=False, indent=2)

    return archive_path


def update_watchlist(signals: dict, date_str: str) -> dict:
    """
    将 TiMi 信号注入 signal_watchlist。

    策略:
      - TiMi 信号作为高置信度候选加入 watchlist
      - 标记 source="timi" 区别于 source="agent" 的 LLM 选股
      - 不覆盖已有的人工选股条目
    """
    positions = signals.get("positions", [])
    signal_list = signals.get("signals", [])

    if not signal_list:
        return {"added": 0, "updated": 0, "reason": "no_signals"}

    # 加载现有 watchlist
    if WATCHLIST_PATH.exists():
        with open(WATCHLIST_PATH, "r", encoding="utf-8") as f:
            wl = json.load(f)
    else:
        wl = {"active": [], "version": "1.0", "last_updated": ""}

    active = wl.get("active", [])
    existing_symbols = {e.get("windcode", e.get("symbol", "")) for e in active}

    # 构建信号索引 (symbol → position)
    pos_map = {}
    for pos in positions:
        pos_map[pos["symbol"]] = pos

    added = 0
    updated = 0

    for sig in signal_list:
        sym = sig["symbol"]
        pos = pos_map.get(sym, {})

        entry = {
            "symbol": sym,
            "windcode": sym,
            "name": sig.get("metadata", {}).get("name", sym),
            "source": "timi",
            "strategy_type": sig.get("strategy_type", ""),
            "confidence": sig.get("confidence", 0),
            "trigger_price": sig.get("trigger_price", 0),
            "stop_loss": sig.get("stop_loss", 0),
            "take_profit": sig.get("take_profit", []),
            "edge": sig.get("edge", 0),
            "position_pct": pos.get("pct_of_portfolio", 0),
            "added_date": date_str,
            "alpha_score": sig.get("confidence", 0),  # 用 confidence 作为 alpha proxy
            "entry_price": sig.get("trigger_price", 0),
            "status": "active",
        }

        if sym in existing_symbols:
            # 更新已有条目
            for i, e in enumerate(active):
                if e.get("windcode", e.get("symbol", "")) == sym:
                    if e.get("source") == "timi":
                        active[i] = {**e, **entry}
                        updated += 1
                    break
        else:
            active.append(entry)
            added += 1

    # 保存
    wl["active"] = active
    wl["last_updated"] = datetime.now().isoformat()
    wl["version"] = "4.3-timi"

    with open(WATCHLIST_PATH, "w", encoding="utf-8") as f:
        json.dump(wl, f, ensure_ascii=False, indent=2)

    # 追加历史
    _append_history(date_str, signal_list)

    return {"added": added, "updated": updated, "total_active": len(active)}


def _append_history(date_str: str, signal_list: list):
    """追加 signal_watchlist_history"""
    if WATCHLIST_HISTORY.exists():
        with open(WATCHLIST_HISTORY, "r", encoding="utf-8") as f:
            history = json.load(f)
    else:
        history = []

    if isinstance(history, list):
        history.append({
            "date": date_str,
            "source": "timi",
            "signals": [
                {
                    "symbol": s["symbol"],
                    "strategy": s.get("strategy_type", ""),
                    "confidence": s.get("confidence", 0),
                    "trigger_price": s.get("trigger_price", 0),
                }
                for s in signal_list
            ],
        })

    with open(WATCHLIST_HISTORY, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def get_archive_stats() -> dict:
    """获取归档统计"""
    if not ARCHIVE_DIR.exists():
        return {"total_days": 0, "total_signals": 0, "avg_per_day": 0}

    files = sorted(ARCHIVE_DIR.glob("timi_signals_*.json"))
    total_signals = 0
    days_with_signals = 0

    for f in files:
        with open(f, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        n = data.get("signal_count", 0)
        total_signals += n
        if n > 0:
            days_with_signals += 1

    return {
        "total_days": len(files),
        "days_with_signals": days_with_signals,
        "total_signals": total_signals,
        "avg_per_day": round(total_signals / max(len(files), 1), 1),
        "date_range": f"{files[0].stem.replace('timi_signals_', '')} → {files[-1].stem.replace('timi_signals_', '')}" if files else "N/A",
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="TiMi 信号归档器")
    parser.add_argument("--date", default=None, help="日期 (YYYY-MM-DD)")
    parser.add_argument("--update-watchlist", action="store_true",
                        help="同时更新 signal_watchlist")
    parser.add_argument("--stats", action="store_true",
                        help="显示归档统计")
    args = parser.parse_args()

    date_str = args.date or datetime.now().strftime("%Y-%m-%d")

    if args.stats:
        stats = get_archive_stats()
        print(json.dumps(stats, indent=2, ensure_ascii=False))
        return 0

    # 加载信号
    signals = load_timi_signals(date_str)
    if signals is None:
        print("[archive] ⚠ 无 TiMi 信号可供归档")
        return 1

    n_signals = len(signals.get("signals", []))
    print(f"[archive] {n_signals} 个 TiMi 信号 ({date_str})")

    # 归档
    archive_path = archive_signals(signals, date_str)
    print(f"[archive] 已归档: {archive_path}")

    # 更新 watchlist
    if args.update_watchlist:
        result = update_watchlist(signals, date_str)
        print(f"[archive] Watchlist: +{result['added']} 新增, "
              f"~{result['updated']} 更新, "
              f"共 {result['total_active']} 标的")

    # 统计
    stats = get_archive_stats()
    print(f"[archive] 累计: {stats['total_days']}d, "
          f"{stats['total_signals']} 信号, "
          f"日均 {stats['avg_per_day']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())