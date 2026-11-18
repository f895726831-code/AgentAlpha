"""
论文因子集成模块

从 factor-lab API 获取论文因子信号，与 ML alpha 信号合成增强排名。

用法:
    from scripts.qlib.paper_factor_integration import PaperFactorIntegrator

    integrator = PaperFactorIntegrator()
    enriched = integrator.enrich_alpha_signals("2026-08-19")
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# 确保 quant-signal-factory 在 sys.path 中
_QSF_ROOT = Path("signal-factory")
if str(_QSF_ROOT) not in sys.path:
    sys.path.insert(0, str(_QSF_ROOT))

from src.factor_lab_client import FactorLabClient

# FM_trade 路径
FM_TRADE_DIR = Path(__file__).resolve().parent.parent.parent
FM_TRADE_OUTPUT = FM_TRADE_DIR / "data" / "output"


class PaperFactorIntegrator:
    """
    论文因子集成器：从 factor-lab 获取论文因子，与 ML alpha 合成。

    合成策略:
      - 论文因子排名独立于 ML alpha（不同方法论）
      - 取两者排名交集做高置信度推荐
      - 加权平均: 0.7 * ML_alpha_rank + 0.3 * paper_factor_rank
    """

    def __init__(self, factor_lab_url: str = "http://localhost:8700"):
        self.client = FactorLabClient(base_url=factor_lab_url)
        self.ml_weight = 0.7  # ML alpha 权重
        self.paper_weight = 0.3  # 论文因子权重

    def is_available(self) -> bool:
        """检查 factor-lab 是否可用"""
        return self.client.is_available()

    def fetch_paper_factors(self, date: str) -> Optional[dict]:
        """
        从 factor-lab 获取论文因子合成排名。

        Returns:
            {"rankings": [{"symbol": "sh600519", "score": 0.85}, ...], "factor_count": 5}
            or None if factor-lab 不可用
        """
        try:
            health = self.client.health_check()
            if not health.get("status") == "ok":
                return None

            factor_count = health.get("factors_count", 0)
            if factor_count == 0:
                print("[paper-factor] factor-lab 无活跃因子，跳过")
                return None

            # 获取因子合成排名
            result = self.client.combine_factors(
                date=date,
                method="ic_weighted",
                top_k=100,
                timeout=600,  # combine 计算 26 因子 × 全市场需 5-7 分钟
            )
            return result

        except Exception as e:
            print(f"[paper-factor] factor-lab 调用失败: {e}")
            return None

    def enrich_alpha_signals(
        self,
        date: str,
        alpha_signals: Optional[dict] = None,
        output_file: Optional[str] = None,
    ) -> dict:
        """
        用论文因子增强 ML alpha 信号。

        Args:
            date: 目标日期
            alpha_signals: 现有的 alpha_signals.json 内容，None=从文件读取
            output_file: 输出文件路径，None=默认路径

        Returns:
            增强后的 alpha 信号 dict
        """
        # 读取现有 alpha 信号
        if alpha_signals is None:
            alpha_path = FM_TRADE_OUTPUT / "alpha_signals.json"
            if alpha_path.exists():
                with open(alpha_path, "r", encoding="utf-8") as f:
                    alpha_signals = json.load(f)
            else:
                print("[paper-factor] alpha_signals.json 不存在，无法增强")
                return {"error": "alpha_signals.json not found"}

        # 获取论文因子
        paper_result = self.fetch_paper_factors(date)
        if paper_result is None:
            print("[paper-factor] 论文因子不可用，返回原始 ML alpha")
            alpha_signals["paper_factor"] = {"available": False}
            return alpha_signals

        # 解析论文因子排名
        paper_rankings = paper_result.get("rankings", [])
        if not paper_rankings:
            print("[paper-factor] 论文因子无排名结果")
            alpha_signals["paper_factor"] = {"available": True, "rankings": []}
            return alpha_signals

        # 构建论文因子分数映射 {symbol: paper_score}
        paper_scores = {}
        for r in paper_rankings:
            sym = r.get("symbol", "")
            score = r.get("score", 0)
            if sym:
                paper_scores[sym] = score

        # 合并 ML alpha 和论文因子
        ml_top = alpha_signals.get("top_50", [])
        enriched_top = []

        for item in ml_top:
            sym = item["symbol"]
            ml_score = item.get("alpha_score", 0)

            # 论文因子调整
            paper_score = paper_scores.get(sym, 0)
            combined_score = (
                self.ml_weight * ml_score
                + self.paper_weight * paper_score
            )

            enriched_top.append({
                **item,
                "paper_factor_score": round(paper_score, 6),
                "combined_score": round(combined_score, 6),
                "paper_factor_boost": paper_score > 0,
            })

        # 按 combined_score 重新排序
        enriched_top.sort(key=lambda x: x["combined_score"], reverse=True)

        # 更新排名
        for i, item in enumerate(enriched_top):
            item["combined_rank"] = i + 1

        # 计算交集（ML top 50 中也有论文因子信号的）
        intersection = [
            item for item in enriched_top
            if item["paper_factor_boost"]
        ]

        # 更新输出
        alpha_signals["paper_factor"] = {
            "available": True,
            "factor_count": paper_result.get("factor_count", 0),
            "method": paper_result.get("method", "ic_weighted"),
            "intersection_count": len(intersection),
            "intersection_top_10": [
                {
                    "symbol": item["symbol"],
                    "ml_rank": item["rank"],
                    "combined_rank": item["combined_rank"],
                    "paper_score": item["paper_factor_score"],
                    "combined_score": item["combined_score"],
                }
                for item in intersection[:10]
            ],
        }

        alpha_signals["top_50"] = enriched_top
        alpha_signals["top_10"] = enriched_top[:10]
        alpha_signals["model"] = f"{alpha_signals.get('model', 'ensemble')}+paper"
        alpha_signals["enriched_at"] = datetime.now().isoformat()

        # 保存
        if output_file:
            output_path = Path(output_file)
        else:
            output_path = FM_TRADE_OUTPUT / "alpha_signals.json"

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(alpha_signals, f, ensure_ascii=False, indent=2)

        print(f"[paper-factor] 增强完成:")
        print(f"  论文因子: {paper_result.get('factor_count', 0)} 个")
        print(f"  交集标的: {len(intersection)} 只")
        print(f"  输出: {output_path}")

        return alpha_signals

    def get_paper_factor_summary(self, date: str) -> dict:
        """获取论文因子概览（供 cron 报告使用）"""
        paper_result = self.fetch_paper_factors(date)
        if paper_result is None:
            return {"available": False}

        rankings = paper_result.get("rankings", [])
        return {
            "available": True,
            "date": date,
            "factor_count": paper_result.get("factor_count", 0),
            "method": paper_result.get("method", "ic_weighted"),
            "top_5": [
                {"symbol": r.get("symbol"), "score": r.get("score")}
                for r in rankings[:5]
            ],
        }


# ─── CLI ───

def main():
    import argparse

    parser = argparse.ArgumentParser(description="论文因子集成 — factor-lab → FM_trade")
    parser.add_argument("--date", help="目标日期 (YYYY-MM-DD)，默认今天")
    parser.add_argument("--alpha-file", help="alpha_signals.json 路径")
    parser.add_argument("--output", help="输出文件路径")
    parser.add_argument("--summary", action="store_true", help="仅输出摘要")
    parser.add_argument("--dry-run", action="store_true", help="不保存文件")

    args = parser.parse_args()
    date = args.date or datetime.now().strftime("%Y-%m-%d")

    integrator = PaperFactorIntegrator()

    if not integrator.is_available():
        print("[paper-factor] ❌ factor-lab 不可用")
        print("请确认 WSL2 factor-lab 已启动: wsl bash /home/user/factor-lab/start.sh")
        sys.exit(1)

    if args.summary:
        summary = integrator.get_paper_factor_summary(date)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    # 读取 alpha 信号
    alpha_signals = None
    if args.alpha_file:
        with open(args.alpha_file, "r", encoding="utf-8") as f:
            alpha_signals = json.load(f)

    output_file = None if args.dry_run else args.output
    result = integrator.enrich_alpha_signals(
        date=date,
        alpha_signals=alpha_signals,
        output_file=output_file,
    )

    if not args.dry_run:
        print(json.dumps(result.get("paper_factor", {}), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()