"""
FM_trade JSON 契约生成器

将信号工厂的输出转换为 FM_trade 可读取的标准化 JSON 契约。
这是两个项目之间的唯一耦合点。

契约结构:
  {
    "meta": {...},           # 元信息（日期、版本、验证状态）
    "factors": [...],        # 因子列表（含 IC、PIT 状态）
    "rankings": {...},       # 排名数据（共识 + 按因子）
    "backtest_summary": {..},# 回测汇总（预验证结果）
    "warnings": [...]        # 警告信息
  }
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


def generate_contract(
    factor_results: dict,
    backtest_results: Optional[dict] = None,
    pit_verified: bool = True,
    data_coverage: Optional[dict] = None,
) -> dict:
    """
    生成 FM_trade 可读的 JSON 契约。

    参数:
        factor_results: RD-Agent 因子挖掘结果
        backtest_results: 回测结果（可选，有则复用）
        pit_verified: 全局 PIT 验证状态
        data_coverage: 数据覆盖信息

    返回:
        FM_trade JSON 契约
    """
    contract = {
        "meta": _build_meta(factor_results, pit_verified, data_coverage),
        "factors": _build_factors(factor_results),
        "rankings": _build_rankings(factor_results, backtest_results),
        "backtest_summary": _build_backtest_summary(backtest_results),
        "warnings": _build_warnings(factor_results, backtest_results, pit_verified),
    }

    return contract


def _build_meta(factor_results: dict, pit_verified: bool, data_coverage: Optional[dict]) -> dict:
    summary = factor_results.get("summary", {})
    return {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "version": "1.0.0",
        "engine": "quant-signal-factory",
        "pit_verified": pit_verified,
        "data_coverage": data_coverage or {},
        "factor_count": summary.get("ic_filtered", 0),
        "total_generated": summary.get("total_generated", 0),
        "model": summary.get("model", "unknown"),
    }


def _build_factors(factor_results: dict) -> List[dict]:
    """构建因子列表"""
    factors = factor_results.get("factors", [])
    evaluations = factor_results.get("evaluations", {})

    result = []
    for f in factors:
        entry = {
            "id": f["id"],
            "name": f["name"],
            "source": f.get("source", "unknown"),
            "description": f.get("description", ""),
            "ic_mean_30d": evaluations.get(f["id"], {}).get("ic_mean"),
            "ic_ir_30d": evaluations.get(f["id"], {}).get("ic_ir"),
            "pit_verified": f.get("pit_verified", False),
            "ic_verified": f.get("ic_verified", True),
            "last_trained": datetime.now().strftime("%Y-%m-%d"),
        }

        # 论文血统字段（source="paper" 时输出）
        if f.get("source") == "paper":
            entry["paper_id"] = f.get("paper_id")
            entry["paper_title"] = f.get("paper_title")
            entry["paper_section"] = f.get("paper_section")
            entry["paper_page"] = f.get("paper_page")
            if f.get("caveats"):
                entry["caveats"] = f.get("caveats")

        result.append(entry)

    return result


def _build_rankings(factor_results: dict, backtest_results: Optional[dict] = None) -> dict:
    """构建排名数据"""
    factors = factor_results.get("factors", [])
    evaluations = factor_results.get("evaluations", {})

    # 按 IC 排序因子
    sorted_factors = sorted(
        factors,
        key=lambda f: abs(f.get("ic_mean", 0) or 0),
        reverse=True,
    )

    # 共识排名：从回测结果中提取 Gate 1 得分排名
    consensus = _build_consensus_ranking(backtest_results, sorted_factors)

    # 按因子排名：计算每个因子对各标的的信号值
    by_factor = _build_per_factor_rankings(factor_results, backtest_results)

    rankings = {
        "consensus": consensus,
        "by_factor": by_factor,
        "top_factors": [
            {"id": f["id"], "name": f["name"], "ic_mean": f.get("ic_mean")}
            for f in sorted_factors[:10]
        ],
    }

    return rankings


def _build_consensus_ranking(backtest_results: Optional[dict], sorted_factors: list) -> list:
    """构建共识排名：使用 Gate 1 得分 + 因子 IC 权重"""
    ranking = []

    # 如果回测结果中包含多个标的的 Gate 1 评分，按评分排序
    if backtest_results:
        gate1 = backtest_results.get("gate1", {})
        if gate1 and gate1.get("adjusted_score") is not None:
            symbol = backtest_results.get("meta", {}).get("symbol", "")
            ranking.append({
                "symbol": symbol,
                "score": gate1.get("adjusted_score", 0),
                "verdict": gate1.get("verdict", "Unknown"),
                "top_contributors": [f["name"] for f in sorted_factors[:3]],
            })

    # 如果无回测结果，用因子 IC 作为排名依据
    if not ranking and sorted_factors:
        ranking.append({
            "note": "无回测数据，以下为因子 IC 排名",
            "top_factor": sorted_factors[0]["name"] if sorted_factors else "none",
            "factor_count": len(sorted_factors),
        })

    return ranking


def _build_per_factor_rankings(factor_results: dict, backtest_results: Optional[dict]) -> dict:
    """构建每个因子的标的排名"""
    by_factor = {}

    signals = factor_results.get("factor_signals", {})
    if signals:
        # 有实际信号数据，按因子分组排名
        for factor_id, stock_signals in signals.items():
            sorted_stocks = sorted(stock_signals.items(), key=lambda x: x[1], reverse=True)
            by_factor[factor_id] = [
                {"symbol": s, "value": round(v, 4)} for s, v in sorted_stocks[:20]
            ]
    else:
        # 无信号数据，用因子元信息填充
        factors = factor_results.get("factors", [])
        for f in factors:
            by_factor[f["id"]] = {
                "name": f["name"],
                "ic_mean": f.get("ic_mean"),
                "note": "需运行因子信号计算以获取标的排名",
            }

    return by_factor


def _build_backtest_summary(backtest_results: Optional[dict]) -> Optional[dict]:
    """构建回测汇总"""
    if not backtest_results:
        return None

    return {
        "status": backtest_results.get("meta", {}).get("status", "unknown"),
        "total_trades": backtest_results.get("summary", {}).get("total_trades", 0),
        "win_rate": backtest_results.get("summary", {}).get("win_rate", 0),
        "profit_factor": backtest_results.get("summary", {}).get("profit_factor", 0),
        "sharpe_ratio": backtest_results.get("summary", {}).get("sharpe_ratio", 0),
        "max_drawdown": backtest_results.get("summary", {}).get("max_drawdown", 0),
        "verdict": backtest_results.get("summary", {}).get("verdict", "Abandon"),
        "gate1_score": backtest_results.get("gate1", {}).get("adjusted_score"),
    }


def _build_warnings(
    factor_results: dict,
    backtest_results: Optional[dict],
    pit_verified: bool,
) -> List[str]:
    """构建警告信息"""
    warnings = []

    if not pit_verified:
        warnings.append("⚠ 全局 PIT 验证未通过，部分因子可能存在未来函数")

    pit_results = factor_results.get("pit_results", {})
    failed_pit = [fid for fid, r in pit_results.items() if not r.get("passed")]
    if failed_pit:
        warnings.append(f"⚠ 以下因子未通过 PIT 验证，已自动排除: {', '.join(failed_pit)}")

    if backtest_results and backtest_results.get("meta", {}).get("status") == "error":
        warnings.append(f"⚠ 回测失败: {backtest_results.get('error', '未知错误')}")

    # 因子数量警告
    factor_count = len(factor_results.get("factors", []))
    if factor_count == 0:
        warnings.append("⚠ 无可用因子，请检查数据源和 PIT 验证配置")
    elif factor_count < 3:
        warnings.append(f"⚠ 可用因子较少 ({factor_count} 个)，信号可能不够多样化")

    return warnings


def save_contract(contract: dict, output_dir: str = "data/output/signals") -> Path:
    """保存契约到文件"""
    date_str = contract["meta"]["date"]
    output_path = Path(output_dir) / f"{date_str}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(contract, f, ensure_ascii=False, indent=2, default=str)

    return output_path


def load_contract(date_str: str, signal_dir: str = "data/output/signals") -> Optional[dict]:
    """加载指定日期的契约（供 FM_trade 使用）"""
    filepath = Path(signal_dir) / f"{date_str}.json"
    if not filepath.exists():
        return None

    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def get_latest_contract(signal_dir: str = "data/output/signals") -> Optional[dict]:
    """获取最新的契约文件"""
    signal_path = Path(signal_dir)
    if not signal_path.exists():
        return None

    files = sorted(signal_path.glob("*.json"), reverse=True)
    if not files:
        return None

    with open(files[0], "r", encoding="utf-8") as f:
        return json.load(f)