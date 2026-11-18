"""
AgentAlpha ← 信号工厂 JSON 桥接脚本

从 signal-factory 读取 alpha 信号和因子数据，
复制到 AgentAlpha 的 data/output/ 目录供管线使用。

用法:
    python scripts/sync_signal_factory.py              # 同步最新信号
    python scripts/sync_signal_factory.py --date 2026-08-07  # 指定日期
    python scripts/sync_signal_factory.py --watch       # 持续监控（每 5 分钟）
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# Fix Windows GBK encoding for emoji output
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ─── 路径配置 ───

FM_TRADE_DIR = Path(__file__).resolve().parent.parent
# 确保 my-bot/ 在 sys.path 中，使 scripts.qlib 包可导入
if str(FM_TRADE_DIR) not in sys.path:
    sys.path.insert(0, str(FM_TRADE_DIR))
SIGNAL_FACTORY_DIR = Path("D:/AgentAlpha/signal-factory")
SIGNAL_FACTORY_SIGNALS = SIGNAL_FACTORY_DIR / "data/output/signals"
SIGNAL_FACTORY_ALPHA = SIGNAL_FACTORY_DIR / "data/output/alpha_signals.json"
SIGNAL_FACTORY_ALPHA_LATEST = SIGNAL_FACTORY_DIR / "data/output/alpha_signals_latest.json"
SIGNAL_FACTORY_ALPHA_CSI300 = SIGNAL_FACTORY_DIR / "data/output/alpha_signals_csi300.json"
SIGNAL_FACTORY_ALPHA_CSI300_LATEST = SIGNAL_FACTORY_DIR / "data/output/alpha_signals_latest_csi300.json"
SIGNAL_FACTORY_ALPHA_P1 = SIGNAL_FACTORY_DIR / "data/output/alpha_signals_p1.json"
FM_TRADE_OUTPUT = FM_TRADE_DIR / "data/output"


def sync_latest_signals(enrich_paper: bool = False) -> dict:
    """同步最新的信号工厂数据到 AgentAlpha

    Args:
        enrich_paper: 是否用论文因子增强 alpha 信号
    """
    FM_TRADE_OUTPUT.mkdir(parents=True, exist_ok=True)

    result = {
        "timestamp": datetime.now().isoformat(),
        "synced": [],
        "errors": [],
    }

    # 1. 同步 alpha_signals.json（ML 模型排名）
    if SIGNAL_FACTORY_ALPHA.exists():
        try:
            with open(SIGNAL_FACTORY_ALPHA, "r", encoding="utf-8") as f:
                alpha_data = json.load(f)

            # ── staleness gate ──
            # 区分设计滞后 (label horizon, ~5 交易日 ≈ 7 日历日) 与异常滞后
            # excess_lag_days = data_lag - design_lag - 2(weekend buffer)
            # excess_lag ≤ 0 → 正常；1-2 → 警告；>2 → 拒绝
            contract = alpha_data.get("@contract", {})
            staleness = contract.get("staleness", {})
            excess = staleness.get("excess_lag_days", -999)
            if excess < 0:
                # @contract 缺失或旧格式 → 旧格式信号，警告但允许（向后兼容）
                print("  ⚠️ alpha_signals.json 缺少 staleness 元数据 (旧格式)，无法检查新鲜度")
                rejected = False
            elif excess > 2:
                result["errors"].append(
                    f"alpha_signals.json 异常滞后 excess={excess}d "
                    f"(as_of={contract.get('as_of_date', '?')}, "
                    f"data_lag={staleness.get('data_lag_calendar_days', '?')}d, "
                    f"design={staleness.get('design_lag_trading_days', '?')}td)，拒绝同步"
                )
                rejected = True
            elif excess > 0:
                print(f"  ⚠️ alpha_signals.json 轻微滞后 (excess={excess}d)，继续同步")
                rejected = False
            else:
                rejected = False

            if not rejected:
                dest = FM_TRADE_OUTPUT / "alpha_signals.json"
                with open(dest, "w", encoding="utf-8") as f:
                    json.dump(alpha_data, f, ensure_ascii=False, indent=2)
                result["synced"].append({
                    "file": "alpha_signals.json",
                    "date": alpha_data.get("date", "unknown"),
                    "model": alpha_data.get("model", "unknown"),
                    "instruments": alpha_data.get("total_stocks", alpha_data.get("total_instruments", 0)),
                    "staleness": staleness,
                })
        except Exception as e:
            result["errors"].append(f"alpha_signals.json: {e}")
    else:
        result["errors"].append("alpha_signals.json 不存在（信号工厂尚未生成）")

    # 1.5. 同步 alpha_signals_latest.json（纯推断信号，最新日期）
    if SIGNAL_FACTORY_ALPHA_LATEST.exists():
        try:
            with open(SIGNAL_FACTORY_ALPHA_LATEST, "r", encoding="utf-8") as f:
                alpha_latest_data = json.load(f)

            dest = FM_TRADE_OUTPUT / "alpha_signals_latest.json"
            with open(dest, "w", encoding="utf-8") as f:
                json.dump(alpha_latest_data, f, ensure_ascii=False, indent=2)

            result["synced"].append({
                "file": "alpha_signals_latest.json",
                "date": alpha_latest_data.get("date", "unknown"),
                "model": alpha_latest_data.get("model", "unknown"),
                "instruments": alpha_latest_data.get("total_stocks", alpha_latest_data.get("total_instruments", 0)),
                "mode": "infer-only",
            })
        except Exception as e:
            result["errors"].append(f"alpha_signals_latest.json: {e}")
    # 注意：alpha_signals_latest.json 不存在不算错误，可能 --skip-infer 或尚未生成

    # 2. 同步 alpha_signals_csi300.json（CSI 300 ML 模型排名）
    if SIGNAL_FACTORY_ALPHA_CSI300.exists():
        try:
            with open(SIGNAL_FACTORY_ALPHA_CSI300, "r", encoding="utf-8") as f:
                alpha_csi300_data = json.load(f)

            dest = FM_TRADE_OUTPUT / "alpha_signals_csi300.json"
            with open(dest, "w", encoding="utf-8") as f:
                json.dump(alpha_csi300_data, f, ensure_ascii=False, indent=2)

            result["synced"].append({
                "file": "alpha_signals_csi300.json",
                "date": alpha_csi300_data.get("date", "unknown"),
                "model": alpha_csi300_data.get("model", "unknown"),
                "instruments": "csi300",
                "stocks": alpha_csi300_data.get("total_stocks", 0),
            })
        except Exception as e:
            result["errors"].append(f"alpha_signals_csi300.json: {e}")
    else:
        result["errors"].append("alpha_signals_csi300.json 不存在（CSI 300 信号尚未生成）")

    # 2.5. 同步 alpha_signals_latest_csi300.json（CSI 300 纯推断信号）
    if SIGNAL_FACTORY_ALPHA_CSI300_LATEST.exists():
        try:
            with open(SIGNAL_FACTORY_ALPHA_CSI300_LATEST, "r", encoding="utf-8") as f:
                alpha_csi300_latest_data = json.load(f)

            dest = FM_TRADE_OUTPUT / "alpha_signals_latest_csi300.json"
            with open(dest, "w", encoding="utf-8") as f:
                json.dump(alpha_csi300_latest_data, f, ensure_ascii=False, indent=2)

            result["synced"].append({
                "file": "alpha_signals_latest_csi300.json",
                "date": alpha_csi300_latest_data.get("date", "unknown"),
                "model": alpha_csi300_latest_data.get("model", "unknown"),
                "instruments": "csi300",
                "stocks": alpha_csi300_latest_data.get("total_stocks", 0),
                "mode": "infer-only",
            })
        except Exception as e:
            result["errors"].append(f"alpha_signals_latest_csi300.json: {e}")
    # 注意：alpha_signals_latest_csi300.json 不存在不算错误，可能 --skip-infer 或尚未生成

    # 2.6. 同步 alpha_signals_p1.json（Phase 1 因子共识信号）
    if SIGNAL_FACTORY_ALPHA_P1.exists():
        try:
            with open(SIGNAL_FACTORY_ALPHA_P1, "r", encoding="utf-8") as f:
                p1_data = json.load(f)

            dest = FM_TRADE_OUTPUT / "alpha_signals_p1.json"
            with open(dest, "w", encoding="utf-8") as f:
                json.dump(p1_data, f, ensure_ascii=False, indent=2)

            result["synced"].append({
                "file": "alpha_signals_p1.json",
                "date": p1_data.get("date", "unknown"),
                "model": p1_data.get("model", "unknown"),
                "instruments": p1_data.get("total_stocks", 0),
                "factors_used": p1_data.get("factors_used", 0),
                "coverage": p1_data.get("coverage_note", ""),
            })
        except Exception as e:
            result["errors"].append(f"alpha_signals_p1.json: {e}")
    # 注意：alpha_signals_p1.json 不存在不算错误，可能 Phase 1 尚未生成

    # 3. 同步最新的因子信号文件（因子级数据 + IC 验证）
    if SIGNAL_FACTORY_SIGNALS.exists():
        json_files = sorted(SIGNAL_FACTORY_SIGNALS.glob("*.json"), reverse=True)
        if json_files:
            latest = json_files[0]
            try:
                with open(latest, "r", encoding="utf-8") as f:
                    signal_data = json.load(f)

                dest = FM_TRADE_OUTPUT / f"signal_factory_{latest.name}"
                with open(dest, "w", encoding="utf-8") as f:
                    json.dump(signal_data, f, ensure_ascii=False, indent=2)

                # 也保存为 latest
                latest_dest = FM_TRADE_OUTPUT / "signal_factory_latest.json"
                with open(latest_dest, "w", encoding="utf-8") as f:
                    json.dump(signal_data, f, ensure_ascii=False, indent=2)

                factor_count = len(signal_data.get("factors", []))
                result["synced"].append({
                    "file": f"signal_factory_{latest.name}",
                    "date": latest.stem,
                    "factors": factor_count,
                })
            except Exception as e:
                result["errors"].append(f"signal_factory: {e}")

    # 3. 生成汇总报告
    summary = _build_summary(result)

    # 4. 论文因子增强（可选）
    if enrich_paper:
        try:
            from scripts.qlib.paper_factor_integration import PaperFactorIntegrator
            integrator = PaperFactorIntegrator()
            if integrator.is_available():
                alpha_date = summary.get("alpha_date", datetime.now().strftime("%Y-%m-%d"))
                enriched = integrator.enrich_alpha_signals(date=alpha_date)
                if enriched.get("paper_factor", {}).get("available"):
                    result["synced"].append({
                        "file": "paper_factor_enrichment",
                        "factors": enriched["paper_factor"].get("factor_count", 0),
                        "intersection": enriched["paper_factor"].get("intersection_count", 0),
                    })
                    summary["paper_factor"] = enriched.get("paper_factor", {})
            else:
                result["errors"].append("paper_factor: factor-lab 不可用（请确认 WSL2 factor-lab 已启动）")
        except Exception as e:
            result["errors"].append(f"paper_factor: {e}")

    summary_path = FM_TRADE_OUTPUT / "signal_factory_sync_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)

    # 写入 manifest.json 索引
    _write_manifest()

    return result


def _build_summary(sync_result: dict) -> dict:
    """生成同步汇总"""
    summary = {
        "last_sync": datetime.now().isoformat(),
        "status": "ok" if not sync_result["errors"] else "partial",
        "synced_files": sync_result["synced"],
        "errors": sync_result["errors"],
    }

    # 读取 alpha_signals 的 Top 5 — 优先 latest（excess_lag ≤ 0），fallback 到 standard
    for path_candidate in [
        FM_TRADE_OUTPUT / "alpha_signals_latest.json",
        FM_TRADE_OUTPUT / "alpha_signals.json",
    ]:
        if path_candidate.exists():
            with open(path_candidate, "r", encoding="utf-8") as f:
                alpha = json.load(f)
            contract = alpha.get("@contract", {})
            staleness = contract.get("staleness", {})
            excess = staleness.get("excess_lag_days", -999)
            # 优先选择无异常滞后的信号；fallback 到 standard（即使过期也接受）
            if excess <= 0 or path_candidate.name == "alpha_signals.json":
                summary["alpha_top_5"] = alpha.get("top_10", [])[:5]
                summary["alpha_model"] = alpha.get("model")
                summary["alpha_date"] = alpha.get("date")
                summary["alpha_source"] = path_candidate.name
                summary["alpha_excess_lag_days"] = excess
                break

    return summary


def get_alpha_signals() -> Optional[dict]:
    """读取 AgentAlpha 本地缓存的 alpha 信号（供管线使用）"""
    path = FM_TRADE_OUTPUT / "alpha_signals.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_top_symbols(n: int = 10) -> list:
    """获取 ML Top N 标的代码"""
    signals = get_alpha_signals()
    if signals is None:
        return []
    return [s["symbol"] for s in signals.get("top_50", [])[:n]]


def get_ml_intersection(agent_symbols: list) -> dict:
    """
    计算 Agent 选股与 ML 排名的交集。

    参数:
        agent_symbols: Agent 选股候选列表 (如 ['sh600519', 'sz000858'])

    返回:
        {
            "intersection": [...],   # A ∩ B (高置信度)
            "agent_only": [...],     # A diff B (中置信度)
            "ml_only": [...],        # B diff A (低置信度)
        }
    """
    signals = get_alpha_signals()
    if signals is None:
        return {"intersection": [], "agent_only": agent_symbols, "ml_only": []}

    ml_top_50 = set(s["symbol"] for s in signals.get("top_50", []))
    agent_set = set(agent_symbols)

    ml_only = []
    for s in signals.get("top_50", []):
        if s["symbol"] not in agent_set:
            ml_only.append(s)

    return {
        "intersection": list(agent_set & ml_top_50),
        "agent_only": list(agent_set - ml_top_50),
        "ml_only": ml_only[:10],  # 只返回前 10 个 ML 独有
    }


# ─── Layer 0 & 1 同步函数（2026-09-09 新增）───


# Layer 0 宏观资产配置同步路径
MACRO_ALLOCATION_SOURCE = SIGNAL_FACTORY_DIR / "data" / "output" / "asset_allocation.json"
MACRO_ALLOCATION_LATEST = SIGNAL_FACTORY_DIR / "data" / "output" / "asset_allocation_latest.json"
FM_TRADE_MACRO_ALLOCATION = FM_TRADE_DIR / "data" / "output" / "asset_allocation.json"

# Layer 1 板块信号同步路径
SECTOR_SIGNALS_SOURCE = SIGNAL_FACTORY_DIR / "data" / "output" / "sector_signals.json"
SECTOR_SIGNALS_LATEST = SIGNAL_FACTORY_DIR / "data" / "output" / "sector_signals_latest.json"
FM_TRADE_SECTOR_SIGNALS = FM_TRADE_DIR / "data" / "output" / "sector_signals.json"


def sync_macro_allocation() -> Optional[dict]:
    """同步 Layer 0 宏观资产配置信号"""
    result = {"file": "asset_allocation.json", "status": "no_source"}

    source = MACRO_ALLOCATION_LATEST if MACRO_ALLOCATION_LATEST.exists() else MACRO_ALLOCATION_SOURCE
    if not source.exists():
        return result

    try:
        with open(source, "r", encoding="utf-8") as f:
            data = json.load(f)

        FM_TRADE_MACRO_ALLOCATION.parent.mkdir(parents=True, exist_ok=True)
        with open(FM_TRADE_MACRO_ALLOCATION, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        result["status"] = "ok"
        result["regime"] = data.get("macro_regime", {}).get("label", "unknown")
        result["equity_weight"] = data.get("equity_weight")
        result["risk_off"] = data.get("risk_off_active", False)
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)

    return result


def sync_sector_signals() -> Optional[dict]:
    """同步 Layer 1 板块轮动信号"""
    result = {"file": "sector_signals.json", "status": "no_source"}

    source = SECTOR_SIGNALS_LATEST if SECTOR_SIGNALS_LATEST.exists() else SECTOR_SIGNALS_SOURCE
    if not source.exists():
        return result

    try:
        with open(source, "r", encoding="utf-8") as f:
            data = json.load(f)

        FM_TRADE_SECTOR_SIGNALS.parent.mkdir(parents=True, exist_ok=True)
        with open(FM_TRADE_SECTOR_SIGNALS, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        result["status"] = "ok"
        result["model"] = data.get("model_name", "unknown")
        result["mode"] = data.get("pipeline_mode", "normal")
        result["top_5"] = [s["sector_name"] for s in data.get("sector_signals", [])[:5]]
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)

    return result


def sync_all_layers(enrich_paper: bool = False) -> dict:
    """同步所有层信号（Layer 0 + Layer 1 + Layer 2）"""
    result = sync_latest_signals(enrich_paper=enrich_paper)

    # Layer 0
    macro_result = sync_macro_allocation()
    if macro_result:
        result["synced"].append(macro_result)
    elif macro_result:
        result.setdefault("errors", []).append(f"macro allocation failed: {macro_result}")

    # Layer 1
    sector_result = sync_sector_signals()
    if sector_result:
        result["synced"].append(sector_result)

    # 写入 manifest.json 索引
    _write_manifest()

    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description="AgentAlpha ← 信号工厂 JSON 桥接")
    parser.add_argument("--date", help="指定日期 (YYYY-MM-DD)")
    parser.add_argument("--watch", action="store_true", help="持续监控模式")
    parser.add_argument("--interval", type=int, default=300, help="监控间隔（秒）")
    parser.add_argument("--paper", action="store_true", help="启用论文因子增强")
    args = parser.parse_args()

    if args.watch:
        print(f"[监控] 每 {args.interval}s 检查信号工厂更新...")
        last_mtime = None
        while True:
            if SIGNAL_FACTORY_ALPHA.exists():
                mtime = SIGNAL_FACTORY_ALPHA.stat().st_mtime
                if last_mtime is None or mtime > last_mtime:
                    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 检测到更新，同步中...")
                    result = sync_latest_signals(enrich_paper=args.paper)
                    _print_result(result)
                    last_mtime = mtime
                else:
                    print(f"\r[{datetime.now().strftime('%H:%M:%S')}] 无变化", end="", flush=True)
            else:
                print(f"\r[{datetime.now().strftime('%H:%M:%S')}] 等待信号工厂生成...", end="", flush=True)
            time.sleep(args.interval)
    else:
        result = sync_latest_signals(enrich_paper=args.paper)
        # Layer 0 + Layer 1
        macro_r = sync_macro_allocation()
        if macro_r:
            result["synced"].append(macro_r)
        sector_r = sync_sector_signals()
        if sector_r:
            result["synced"].append(sector_r)
        _print_result(result)


def _print_result(result: dict):
    """打印同步结果"""
    print(f"\n[同步] {result['timestamp']}")
    for s in result.get("synced", []):
        print(f"  [OK] {s['file']}")
    for e in result.get("errors", []):
        print(f"  [WARN] {e}")

    # 打印 Top 5
    alpha_path = FM_TRADE_OUTPUT / "alpha_signals.json"
    if alpha_path.exists():
        with open(alpha_path, "r", encoding="utf-8") as f:
            alpha = json.load(f)
        print(f"\n  ML Alpha Top 5 ({alpha.get('model', '?')} @ {alpha.get('date', '?')}):")
        for i, s in enumerate(alpha.get("top_10", [])[:5], 1):
            print(f"  {i}. {s['symbol']}  alpha={s.get('alpha_score', '?'):.4f}" if isinstance(s.get('alpha_score'), float) else f"  {i}. {s['symbol']}")

    # 打印 Layer 1 板块信号
    sector_path = FM_TRADE_SECTOR_SIGNALS
    if sector_path.exists():
        with open(sector_path, "r", encoding="utf-8") as f:
            sector = json.load(f)
        print(f"\n  Layer 1 Sector Top-5 ({sector.get('model_name', '?')} @ {sector.get('date', '?')}):")
        for s in sector.get("top_5_selected", [])[:5]:
            print(f"  {s['rank']}. {s['sector_name']} ({s['sector_code']}) "
                  f"score={s['score']:.4f} [{s['category']}]")


# ─── manifest.json 索引（#7: 中期路线）───

MANIFEST_PATH = FM_TRADE_OUTPUT / "manifest.json"
MANIFEST_ARTIFACT_KEYS = [
    "alpha_signals", "alpha_signals_latest",
    "alpha_signals_csi300", "alpha_signals_latest_csi300",
    "alpha_signals_p1",
    "signal_factory_latest", "asset_allocation", "sector_signals",
]


def _read_artifact_meta(filepath: Path) -> dict:
    """从 artifact JSON 中提取元数据（兼容有/无 @contract）。"""
    if not filepath.exists():
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None

    contract = data.get("@contract", {})
    staleness = contract.get("staleness", {})
    excess = staleness.get("excess_lag_days", -999)

    # 确定状态
    if excess < 0:
        status = "no_contract"
    elif excess > 2:
        status = "stale"
    elif excess > 0:
        status = "warning"
    else:
        status = "fresh"

    return {
        "as_of_date": contract.get("as_of_date", data.get("date")),
        "generated_at": contract.get("generated_at", data.get("generated_at")),
        "staleness": staleness if staleness else None,
        "status": status,
        "universe": contract.get("universe"),
        "model": contract.get("model", data.get("model")),
        "instrument_count": data.get("total_stocks", data.get("total_instruments")),
        "file_size_bytes": filepath.stat().st_size,
    }


def _write_manifest():
    """扫描 FM_TRADE_OUTPUT 下所有 artifact，生成 manifest.json 索引。

    消费端（cron-morning-brief、signal_tracker）可一次读取此文件
    获得所有信号文件的新鲜度状态，无需逐个解析 JSON。
    """
    artifacts = {}
    for key in MANIFEST_ARTIFACT_KEYS:
        # 尝试匹配带 _latest / _csi300 后缀的文件
        if key == "alpha_signals":
            path = FM_TRADE_OUTPUT / "alpha_signals.json"
        elif key == "alpha_signals_latest":
            path = FM_TRADE_OUTPUT / "alpha_signals_latest.json"
        elif key == "alpha_signals_csi300":
            path = FM_TRADE_OUTPUT / "alpha_signals_csi300.json"
        elif key == "alpha_signals_latest_csi300":
            path = FM_TRADE_OUTPUT / "alpha_signals_latest_csi300.json"
        elif key == "alpha_signals_p1":
            path = FM_TRADE_OUTPUT / "alpha_signals_p1.json"
        elif key == "signal_factory_latest":
            path = FM_TRADE_OUTPUT / "signal_factory_latest.json"
        elif key == "asset_allocation":
            path = FM_TRADE_OUTPUT / "asset_allocation.json"
        elif key == "sector_signals":
            path = FM_TRADE_OUTPUT / "sector_signals.json"
        else:
            continue

        meta = _read_artifact_meta(path)
        if meta:
            artifacts[key] = {"path": path.name, **meta}

    manifest = {
        "manifest_version": "1.0.0",
        "updated": datetime.now().isoformat(),
        "artifacts": artifacts,
    }

    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    return manifest


def read_manifest() -> Optional[dict]:
    """读取 manifest.json 索引（供消费端使用）。"""
    if not MANIFEST_PATH.exists():
        return None
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    main()