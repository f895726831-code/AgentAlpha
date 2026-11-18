#!/usr/bin/env python3
"""
Quant Signal Factory — 主流水线

每日盘后自动运行:
  1. 数据同步（确保 Qlib bin 数据是最新的）
  2. 因子挖掘（factor-lab API → RD-Agent + DeepSeek-V4）
  3. 策略回测（Qlib backtest）
  4. Gate 1 评分
  5. 信号导出（FM_trade JSON 契约）

用法:
  python -m scripts.run_pipeline                # 完整流水线
  python -m scripts.run_pipeline --skip-mining   # 跳过因子挖掘
  python -m scripts.run_pipeline --date 2026-08-07  # 指定日期
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List

# Fix Windows GBK encoding for emoji output
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 添加项目根目录到 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _load_available_symbols(universe: str = "all") -> List[str]:
    """从 Qlib instruments 加载指定 universe 的可用标的（排除指数）。

    Universe 选择:
      - "csi300": CSI 300 成分股 (csi300.txt)
      - "csi500": CSI 500 成分股 (csi500.txt)
      - "all":    全量标的 (all.txt)

    返回:
        ["sh600519", "sz000001", ...]
    """
    from src.data.config_loader import get_qlib_instruments_dir
    inst_dir = Path(get_qlib_instruments_dir())

    # 选择正确的 instruments 文件
    universe_file_map = {
        "csi300": inst_dir / "csi300.txt",
        "csi500": inst_dir / "csi500.txt",
        "all": inst_dir / "all.txt",
    }
    inst_path = universe_file_map.get(universe, inst_dir / "all.txt")

    if not inst_path.exists():
        # 回退到 all.txt
        inst_path = inst_dir / "all.txt"

    if not inst_path.exists():
        return ["sh600519", "sz000001", "sh600036", "sz000858", "sh600000",
                "sh601318", "sz300750", "sh688981", "sh600276", "sh601012"]

    symbols = []
    with open(inst_path, "r", encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split("\t")
            if parts and not parts[0].isdigit():  # 排除纯数字代码（旧格式）
                sym = parts[0]
                # 排除指数
                if sym in ("sh000300", "sz000300", "000300"):
                    continue
                symbols.append(sym)
    return sorted(set(symbols))


def main():
    parser = argparse.ArgumentParser(description="Quant Signal Factory — 主流水线")
    parser.add_argument("--date", default=None, help="运行日期 (YYYY-MM-DD)，默认今天")
    parser.add_argument("--skip-sync", action="store_true", help="跳过数据同步")
    parser.add_argument("--skip-mining", action="store_true", help="跳过因子挖掘")
    parser.add_argument("--skip-backtest", action="store_true", help="跳过回测")
    parser.add_argument("--universe", default="csi500", choices=["csi300", "csi500", "all"],
                    help="标的 universe: csi300 / csi500 / all (默认: csi500)")
    parser.add_argument("--symbols", nargs="+", default=None, help="手动指定标的（覆盖 --universe）")
    parser.add_argument("--strategy", default="pullback_low", help="回测策略")
    parser.add_argument("--config-dir", default="config", help="配置文件目录")
    parser.add_argument("--output-dir", default="data/output", help="输出目录")
    parser.add_argument("--paper-mining", action="store_true", help="启用论文驱动因子挖掘（Stage 2b）")
    parser.add_argument("--paper-max", type=int, default=5, help="论文挖掘最多处理论文数")
    parser.add_argument("--skip-sector", action="store_true", help="跳过板块轮动信号生成")
    parser.add_argument("--verbose", action="store_true", help="详细日志")
    args = parser.parse_args()

    run_date = args.date or datetime.now().strftime("%Y-%m-%d")
    config_dir = Path(args.config_dir)
    output_dir = Path(args.output_dir)

    # 解析 universe：手动 --symbols 覆盖 --universe
    if args.symbols:
        universe_symbols = args.symbols
        universe_name = "manual"
    else:
        universe_symbols = _load_available_symbols(args.universe)
        universe_name = args.universe

    print(f"{'='*60}")
    print(f"  Quant Signal Factory — 每日流水线")
    print(f"  日期: {run_date}")
    print(f"  Universe: {universe_name} ({len(universe_symbols)} 只)")
    print(f"  时间: {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*60}\n")

    results = {
        "date": run_date,
        "stages": {},
        "warnings": [],
        "errors": [],
    }

    total_start = time.time()

    # ─── Stage 1: 数据同步 ───
    if not args.skip_sync:
        print("─" * 40)
        print("[Stage 1/4] 数据同步")
        stage_start = time.time()

        try:
            from src.data.pit_handler import PITDataHandler
            handler = PITDataHandler()
            handler._init_qlib()
            print(f"  ✅ Qlib 初始化成功")
            results["stages"]["sync"] = {"status": "ok", "duration": round(time.time() - stage_start, 1)}
        except Exception as e:
            print(f"  ❌ Qlib 初始化失败: {e}")
            results["stages"]["sync"] = {"status": "error", "error": str(e)}
            results["errors"].append(f"数据同步失败: {e}")
            if not args.skip_mining and not args.skip_backtest:
                print("  数据同步失败，后续阶段无法继续。")
                sys.exit(1)
    else:
        print("[Stage 1/4] 数据同步 — 已跳过")
        results["stages"]["sync"] = {"status": "skipped"}

    # ─── Stage 2: 因子挖掘 ───
    if not args.skip_mining:
        print("─" * 40)
        print("[Stage 2/4] 因子挖掘 (factor-lab API → RD-Agent + DeepSeek-V4)")
        stage_start = time.time()

        try:
            from src.factor_lab_client import FactorLabClient

            client = FactorLabClient()

            # 检查 factor-lab 可用性
            health = client.health_check()
            if not health.get("status") == "ok":
                print(f"  ⚠ factor-lab 不可用: {health}")
                print("  请确认 WSL2 factor-lab 已启动: bash start.sh")
                results["stages"]["mining"] = {"status": "error", "error": "factor-lab 不可用"}
                results["errors"].append("因子挖掘失败: factor-lab 不可用")
                factor_results = {"factors": [], "evaluations": {}, "pit_results": {}, "summary": {"total_generated": 0, "pit_passed": 0, "ic_filtered": 0}}
            else:
                print(f"  ✅ factor-lab 已连接 (qlib={health.get('qlib_ok')}, rd_agent={health.get('rd_agent_available')})")

                # 触发异步因子挖掘
                task = client.mine_factors(
                    instruments=universe_symbols,
                    date=run_date,
                    max_rounds=5,
                )
                print(f"  任务已提交: {task['task_id']}")

                # 轮询等待完成
                result = client.wait_for_task(
                    task["task_id"],
                    timeout=1800,
                    on_progress=lambda p, s: print(f"  进度: {p:.0%} ({s})") if p > 0 else None,
                )

                factor_results = result
                summary = result.get("summary", {})
                print(f"  ✅ 生成因子: {summary.get('total_generated', 0)} → "
                      f"PIT 通过: {summary.get('pit_passed', 0)} → "
                      f"最终: {summary.get('ic_filtered', 0)}")
                for f in result.get("factors", []):
                    print(f"     {f['id']}: {f['name']} ({f.get('source', 'unknown')}, PIT={'✅' if f.get('pit_verified') else '❌'})")

                results["stages"]["mining"] = {
                    "status": "ok",
                    "duration": round(time.time() - stage_start, 1),
                    "factors": summary,
                }
        except Exception as e:
            print(f"  ❌ 因子挖掘失败: {e}")
            import traceback
            traceback.print_exc()
            results["stages"]["mining"] = {"status": "error", "error": str(e)}
            results["errors"].append(f"因子挖掘失败: {e}")
            factor_results = {"factors": [], "evaluations": {}, "pit_results": {}, "summary": {"total_generated": 0, "pit_passed": 0, "ic_filtered": 0}}
    else:
        print("[Stage 2/4] 因子挖掘 — 已跳过")
        factor_results = {"factors": [], "evaluations": {}, "pit_results": {}, "summary": {"total_generated": 0, "pit_passed": 0, "ic_filtered": 0}}
        results["stages"]["mining"] = {"status": "skipped"}

    # ─── Stage 2b: 论文驱动因子挖掘（可选） ───
    if args.paper_mining:
        print("─" * 40)
        print("[Stage 2b/4] 论文驱动因子挖掘 (factor-lab API → PaperMiner)")
        stage_start = time.time()

        try:
            from src.factor_lab_client import FactorLabClient

            client = FactorLabClient()

            task = client.mine_papers(
                mode="extract",
                max_papers=args.paper_max,
                date=run_date,
            )
            print(f"  任务已提交: {task['task_id']}")

            result = client.wait_for_task(
                task["task_id"],
                timeout=1200,
                on_progress=lambda p, s: print(f"  进度: {p:.0%} ({s})") if p > 0 else None,
            )

            paper_summary = result.get("summary", {})
            paper_factors_all = result.get("factors", [])
            print(f"  ✅ 论文因子: {paper_summary.get('papers_processed', 0)} 篇论文 → "
                  f"{paper_summary.get('factors_extracted', 0)} 因子 → "
                  f"PIT: {paper_summary.get('pit_passed', 0)} → "
                  f"IC: {paper_summary.get('ic_passed', 0)} → "
                  f"入库: {paper_summary.get('stored', 0)}")

            # 合并因子到 factor_results
            if paper_factors_all:
                factor_results["factors"].extend(paper_factors_all)
                factor_results["paper_summary"] = paper_summary

            results["stages"]["paper_mining"] = {
                "status": "ok",
                "duration": round(time.time() - stage_start, 1),
                "summary": paper_summary,
            }
        except Exception as e:
            print(f"  ❌ 论文因子挖掘失败: {e}")
            import traceback
            traceback.print_exc()
            results["stages"]["paper_mining"] = {"status": "error", "error": str(e)}
            results["errors"].append(f"论文因子挖掘失败: {e}")

    # ─── Stage 3: 策略回测 ───
    if not args.skip_backtest:
        print("─" * 40)
        print("[Stage 3/4] 策略回测 (Qlib + Gate 1 评分)")
        stage_start = time.time()

        backtest_results = None
        gate1_results = None

        try:
            from src.backtest.adapter import init_qlib, run_backtest
            from src.backtest.signal_generator import get_signal_from_qlib, get_strategy_info
            from src.backtest.scorer import score_gate1
            from src.backtest.execution_gate import RealismConfig

            init_qlib()

            # 执行现实性门禁（ST/停牌 + 涨跌停 + 成交量约束）
            realism_config = RealismConfig(
                check_st_suspended=True,
                check_price_limits=True,
                check_amount_constraint=True,
            )
            print(f"  执行门禁: ST/停牌✅ 涨跌停✅ 成交量约束✅")

            # 标的列表：使用解析好的 universe_symbols
            symbols = universe_symbols
            # 如果太多，限制数量
            if len(symbols) > 300:
                max_symbols = int(os.environ.get("MAX_BACKTEST_SYMBOLS", str(len(symbols))))
                if max_symbols > 0 and len(symbols) > max_symbols:
                    symbols = symbols[:max_symbols]
            strategy = args.strategy

            print(f"  策略: {strategy}")
            print(f"  标的: {len(symbols)} 只")

            all_backtests = []
            all_gate1 = []

            for symbol in symbols:
                try:
                    # 生成信号
                    signal = get_signal_from_qlib(
                        symbol, strategy,
                        start_time="2020-01-01",
                        end_time=run_date,
                    )
                    buy_count = int((signal == 1).sum())
                    if buy_count == 0:
                        if args.verbose:
                            print(f"  {symbol}: 无信号")
                        continue

                    # 回测（带执行现实性门禁）
                    bt = run_backtest(
                        symbol, strategy, signal,
                        start_time="2020-01-01",
                        end_time=run_date,
                        realism_config=realism_config,
                        verbose=args.verbose,
                    )

                    # Gate 1 评分
                    info = get_strategy_info(strategy)
                    g1 = score_gate1(
                        bt,
                        param_count=info["param_count"],
                        pit_safe=info.get("pit_fixed", False),
                    )

                    all_backtests.append(bt)
                    all_gate1.append(g1)

                    print(f"  {symbol}: {bt['summary']['total_trades']}笔, "
                          f"胜率{bt['summary']['win_rate']}%, "
                          f"PF={bt['summary']['profit_factor']}, "
                          f"Gate1={g1['adjusted_score']:.0f} → {g1['verdict']}")

                except Exception as e:
                    print(f"  {symbol}: ❌ {e}")
                    continue

            # 保存回测结果
            if all_backtests:
                bt_output = output_dir / "backtests" / f"{run_date}_{strategy}.json"
                bt_output.parent.mkdir(parents=True, exist_ok=True)
                with open(bt_output, "w", encoding="utf-8") as f:
                    json.dump({"date": run_date, "strategy": strategy, "results": all_backtests, "gate1": all_gate1},
                              f, ensure_ascii=False, indent=2, default=str)

                backtest_results = all_backtests[0] if all_backtests else None
                gate1_results = all_gate1[0] if all_gate1 else None

                print(f"  ✅ 回测完成: {len(all_backtests)}/{len(symbols)} 只标的")
                results["stages"]["backtest"] = {
                    "status": "ok",
                    "duration": round(time.time() - stage_start, 1),
                    "tested": len(all_backtests),
                    "strategy": strategy,
                }
            else:
                print(f"  ⚠ 无可用回测结果")
                results["stages"]["backtest"] = {"status": "no_data"}

        except Exception as e:
            print(f"  ❌ 回测失败: {e}")
            import traceback
            traceback.print_exc()
            results["stages"]["backtest"] = {"status": "error", "error": str(e)}
            results["errors"].append(f"回测失败: {e}")
    else:
        print("[Stage 3/4] 策略回测 — 已跳过")
        backtest_results = None
        gate1_results = None
        results["stages"]["backtest"] = {"status": "skipped"}

    # ─── Stage 4: 信号导出 ───
    print("─" * 40)
    print("[Stage 4/4] 信号导出 (FM_trade JSON 契约)")
    stage_start = time.time()

    try:
        from src.export.fm_trade_bridge import generate_contract, save_contract

        # 判断 PIT 状态
        pit_results = factor_results.get("pit_results", {})
        if pit_results:
            pit_passed = all(r.get("passed", False) for r in pit_results.values())
        else:
            factors = factor_results.get("factors", [])
            pit_passed = all(f.get("pit_verified", False) for f in factors) if factors else False

        # 组合回测结果
        bt_with_gate1 = None
        if backtest_results:
            bt_with_gate1 = dict(backtest_results)
            if gate1_results:
                bt_with_gate1["gate1"] = gate1_results

        contract = generate_contract(
            factor_results=factor_results,
            backtest_results=bt_with_gate1,
            pit_verified=pit_passed,
            data_coverage={
                "universe": universe_name,
                "stocks": len(universe_symbols),
                "start": "2020-01-01",
                "end": run_date,
            },
        )

        output_path = save_contract(contract, str(output_dir / "signals"))
        print(f"  ✅ 契约已保存: {output_path}")
        print(f"     因子数: {len(contract['factors'])}")
        print(f"     PIT 验证: {'✅ 通过' if contract['meta']['pit_verified'] else '❌ 未通过'}")
        if contract["warnings"]:
            for w in contract["warnings"]:
                print(f"     {w}")

        results["stages"]["export"] = {
            "status": "ok",
            "duration": round(time.time() - stage_start, 1),
            "output": str(output_path),
        }
    except Exception as e:
        print(f"  ❌ 导出失败: {e}")
        results["stages"]["export"] = {"status": "error", "error": str(e)}
        results["errors"].append(f"导出失败: {e}")

    # ─── Stage 5: 板块轮动信号 ───
    if not args.skip_sector:
        print("─" * 40)
        print("[Stage 5/5] 板块轮动信号 (Layer 1 ML Top-5 div)")
        stage_start = time.time()

        try:
            from scripts.generate_sector_signals import generate as generate_sector

            sector_output = generate_sector(args.date)
            if sector_output:
                print(f"  ✅ 板块信号已生成: {len(sector_output['sector_signals'])} 个板块")
                print(f"     Top-5: {', '.join(s['sector_name'] for s in sector_output['top_5_selected'])}")
                results["stages"]["sector"] = {
                    "status": "ok",
                    "duration": round(time.time() - stage_start, 1),
                    "top_5": [s["sector_name"] for s in sector_output["top_5_selected"]],
                    "model_ic": sector_output["model_performance"],
                }
            else:
                print(f"  ❌ 板块信号生成失败")
                results["stages"]["sector"] = {"status": "error", "error": "generate_sector returned None"}
        except Exception as e:
            print(f"  ❌ 板块信号失败: {e}")
            import traceback
            traceback.print_exc()
            results["stages"]["sector"] = {"status": "error", "error": str(e)}
    else:
        print("[Stage 5/5] 板块轮动信号 — 已跳过")
        results["stages"]["sector"] = {"status": "skipped"}
    total_time = time.time() - total_start
    print(f"\n{'='*60}")
    print(f"  流水线完成")
    print(f"  总耗时: {total_time:.0f} 秒 ({total_time/60:.1f} 分钟)")
    print(f"  错误: {len(results['errors'])}")
    print(f"  警告: {len(results['warnings'])}")
    print(f"{'='*60}")

    # 保存流水线日志
    log_output = output_dir / "logs" / f"{run_date}_pipeline.json"
    log_output.parent.mkdir(parents=True, exist_ok=True)
    with open(log_output, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)

    return results


if __name__ == "__main__":
    main()