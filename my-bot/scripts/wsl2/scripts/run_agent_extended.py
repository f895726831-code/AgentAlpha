#!/usr/bin/env python3
"""
LLM Agent 扩展运行 — 加载 GFlowNet 种子 + 放宽门槛 + 增加轮次

改进:
  1. 加载 GFlowNet v3 池 (64因子) 作为种子 → 避免重复发现
  2. pool_min_ic: 0.02 → 0.015
  3. 轮次: 30 → 60
  4. 双 Agent 不同温度并行探索
  5. 提示词中加入"禁止平凡$returns变换"约束
"""
import argparse, json, sys, time
from pathlib import Path
import torch

SCRIPT_DIR = Path(__file__).resolve().parent
FACTOR_LAB_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(FACTOR_LAB_DIR))

from agent import AgentConfig, run_agent, run_swarm, merge_swarm_results
from agent import evaluate_factors_with_ffo


def load_gflownet_pool():
    """加载 GFlowNet v3 因子池，过滤平凡 $returns 变换"""
    ckpt_path = FACTOR_LAB_DIR / "checkpoints" / "gfn_v3_clean_200ep.pt"
    if not ckpt_path.exists():
        print(f"WARN: GFlowNet checkpoint not found at {ckpt_path}")
        return [], []

    # 平凡 $returns 变换模式
    import re
    TRIVIAL_RETURNS_PATTERNS = [
        re.compile(r'^\$\w*returns\s*[\+\-\*\/]\s*\d+$'),  # $returns * N, $returns + N, etc
        re.compile(r'^\d+\s*[\+\-\*\/]\s*\$\w*returns$'),  # N * $returns
        re.compile(r'^\(\s*\$\w*returns\s*[\+\-\*\/]\s*\d+\s*\)$'),
        re.compile(r'^\(\s*\d+\s*[\+\-\*\/]\s*\$\w*returns\s*\)$'),
        re.compile(r'^[Ss]igned[Pp]ower\(\s*\$\w*returns\s*,\s*\d+\s*\)$'),  # SignedPower($returns, N)
        re.compile(r'^[Ss]cale\(\s*\$\w*returns\s*\)$'),  # Scale($returns)
        re.compile(r'^[Ss]cale\(\s*[Ss]cale\(\s*\$\w*returns\s*\)\s*\)$'),
        re.compile(r'^[Mm]in\(\s*\d+\s*,\s*\$\w*returns\s*\)$'),
        re.compile(r'^[Mm]in\(\s*\$\w*returns\s*,\s*\d+\s*\)$'),
        re.compile(r'^[Mm]ax\(\s*\d+\s*,\s*\$\w*returns\s*\)$'),
        re.compile(r'^[Mm]ax\(\s*\$\w*returns\s*,\s*\d+\s*\)$'),
        re.compile(r'^[Aa]bs\(\s*\$\w*returns\s*\)$'),
        re.compile(r'^[Ss]ign\(\s*\$\w*returns\s*\)$'),
        re.compile(r'^[Ll]og\(\s*\$\w*returns\s*\)$'),
        re.compile(r'^\(\s*\$\w*returns\s*\)$'),
    ]

    def is_trivial_returns(formula):
        clean = formula.strip().replace(' ', '')
        for pat in TRIVIAL_RETURNS_PATTERNS:
            if pat.match(clean):
                return True
        return False

    ckpt = torch.load(str(ckpt_path), map_location='cpu', weights_only=False)
    pool = ckpt.get('factor_pool', [])

    nontrivial = [(f, ic) for f, ic in pool if not is_trivial_returns(str(f))]
    trivial = [(f, ic) for f, ic in pool if is_trivial_returns(str(f))]

    print(f"Loaded {len(pool)} GFlowNet seed factors: "
          f"{len(nontrivial)} non-trivial, {len(trivial)} trivial $returns (filtered)")

    return nontrivial, trivial


def update_system_prompt():
    """返回增强版系统提示词 (加入反平凡变换约束)"""
    return """你是一位 Alpha 因子挖掘专家。你的任务是生成新颖、高预测能力的量化因子公式。

## FFO 操作符参考手册

### 数据字段 ($前缀)
- `$open` `$high` `$low` `$close` `$volume` `$vwap` `$returns`
- `$returns` = ($close - Delay($close, 1)) / Delay($close, 1)

### 截面操作符
| 操作符 | 签名 | 说明 |
|--------|------|------|
| `Rank(expr)` | 1 arg | 截面排名 0~1 |
| `Scale(expr)` | 1 arg | 截面 Z-score 标准化 |
| `Quantile(expr, N)` | 2 args | 截面分位数分组 |

### 时序操作符
| 操作符 | 签名 | 说明 |
|--------|------|------|
| `Ts_Mean(expr, d)` | 2 args | d日滚动均值 |
| `Ts_Std(expr, d)` | 2 args | d日滚动标准差 |
| `Ts_Max(expr, d)` | 2 args | d日滚动最大值 |
| `Ts_Min(expr, d)` | 2 args | d日滚动最小值 |
| `Ts_Rank(expr, d)` | 2 args | d日时序排名 |
| `Ts_Sum(expr, d)` | 2 args | d日滚动和 |
| `Ts_ArgMax(expr, d)` | 2 args | d日最大值位置 |
| `Ts_ArgMin(expr, d)` | 2 args | d日最小值位置 |

### 多变量时序
| 操作符 | 签名 | 说明 |
|--------|------|------|
| `Ts_Corr(x, y, d)` | 3 args | 滚动相关系数 |
| `Ts_Cov(x, y, d)` | 3 args | 滚动协方差 |

### 滞后/差分/数学
`Delay(expr, d)` `Delta(expr, d)` `Return(expr, d)` `If(cond, t, f)`
`Sign` `Abs` `Log` `SignedPower(x, a)` `Max(x, y)` `Min(x, y)`

### 中缀操作符: `+` `-` `*` `/` `>` `<` `>=` `<=`

### 常用窗口: 3, 5, 10, 20, 60, 120

## ⛔ 禁止项 — 以下模式已被充分覆盖，禁止再生成

1. **禁止纯 $returns 线性变换**: `$returns * N`, `$returns / N`, `$returns - N`, `$returns + N` 等 — 这些都是同一个信号
2. **禁止纯 $volume 线性变换**: `$volume * N`, `$volume / 10`, `N * $volume` 等
3. **禁止常量填充**: `Min($returns, 60)`, `Max(5, $volume)` 等无意义的常量裁剪
4. **禁止 Scale/Sign/Abs/Log($returns)** 的单独使用 — 太简单，已被覆盖

## ✅ 推荐方向 — 优先探索以下未被充分覆盖的区域

1. **多字段交互**: 组合 $close+$volume+$vwap+$high+$low，如 `Rank($vwap * $volume)`、`($high-$low)/$close`
2. **量价关系**: `Ts_Corr($close, $volume, N)` — 量价背离/共振
3. **波动率结构**: `Ts_Std($returns, 5) / Ts_Std($returns, 60)` — 短长期波动率比
4. **价格位置**: `($close - Ts_Min($low, 20)) / (Ts_Max($high, 20) - Ts_Min($low, 20))` — 相对高低点
5. **日内结构**: 利用 $open/$high/$low/$close 的日内关系
6. **VWAP 偏差**: `($close - $vwap) / $vwap` — 机构交易痕迹
7. **协方差信号**: `Ts_Cov($returns, $volume, N)` — 价量联动风险

## 因子设计原则
1. 每个因子至少包含 2 个不同的数据字段（跨 $close/$volume/$vwap/$high/$low/$open）
2. 优先使用未被覆盖的操作符：Ts_Corr, Ts_Cov, Ts_ArgMax, Ts_ArgMin
3. 2-5 个操作符最优，过度复杂反而容易过拟合
4. 每个因子的经济逻辑清晰（为什么这个因子能预测收益？）

## 输出格式
严格按以下 JSON 输出:
```json
{
  "thinking": "设计思路: 捕捉什么规律? 为什么选这些操作符? 预期IC方向?",
  "formulas": ["公式1", "公式2", "公式3", "公式4", "公式5"]
}
```"""


def main():
    parser = argparse.ArgumentParser(description="LLM Agent 扩展运行")
    parser.add_argument("--rounds", type=int, default=60)
    parser.add_argument("--agents", type=int, default=2)
    parser.add_argument("--pool-min-ic", type=float, default=0.015)
    parser.add_argument("--output", type=str, default="results/llm_agent_v3_extended.json")
    parser.add_argument("--data-dir", type=str, default="/mnt/d/qlib/qlib_data/cn_data")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--no-seeds", action="store_true", help="不注入 GFlowNet 种子，LLM 从零探索")
    args = parser.parse_args()

    # 加载 GFlowNet 种子（过滤平凡变换）—— 可选
    if args.no_seeds:
        gfn_nontrivial, gfn_trivial = [], []
        print("GFlowNet seeds disabled — LLM Agent explores from scratch")
    else:
        gfn_nontrivial, gfn_trivial = load_gflownet_pool()

    # 平凡 $returns 骨架黑名单 (FSA 预注册)
    TRIVIAL_SKELETONS_TO_BAN = [
        'BinOp(*)[returns,const]', 'BinOp(*)[const,returns]',
        'BinOp(+)[returns,const]', 'BinOp(+)[const,returns]',
        'BinOp(-)[returns,const]', 'BinOp(-)[const,returns]',
        'BinOp(/)[returns,const]', 'BinOp(/)[const,returns]',
        'SignedPower[returns,const]', 'Scale[returns]',
        'Scale[Scale[returns]]', 'Abs[returns]', 'Sign[returns]',
        'Log[returns]', 'Min[const,returns]', 'Min[returns,const]',
        'Max[const,returns]', 'Max[returns,const]',
    ]

    # 构建配置
    config = AgentConfig()
    config.n_rounds = args.rounds
    config.formulas_per_round = 5
    config.llm_model = "deepseek-chat"
    config.pool_min_ic = args.pool_min_ic
    config.recent_window = 10
    config.reflection_every = 15
    config.pool_max_size = 100
    config.eval_start = "2015-01-05"  # CSI500 指数启动日
    config.eval_end = "2024-12-31"
    config.eval_universe = "csi500"
    config.fsa_pre_ban = TRIVIAL_SKELETONS_TO_BAN

    # 注入增强版系统提示词
    import agent.prompts as prompts
    prompts.SYSTEM_PROMPT = update_system_prompt()
    # 更新 few-shot: 只用真正有效的因子模式
    prompts.FEW_SHOT_EXAMPLES = [
        {
            "formula": "Rank($vwap * $volume)",
            "intuition": "VWAP×成交量: VWAP加权成交量的截面排名。捕捉机构资金流向，IC≈-0.033。",
            "category": "量价关系",
        },
        {
            "formula": "($close - $vwap) / $vwap",
            "intuition": "VWAP偏离度: 收盘价相对VWAP的百分比偏离。正偏离=机构买入压力，负偏离=卖出压力。",
            "category": "微观结构",
        },
        {
            "formula": "Ts_Mean(($close - $open) / $open, 5) - Ts_Mean(($open - Delay($close, 1)) / Delay($close, 1), 5)",
            "intuition": "日内vs隔夜收益差: 日内动量减去隔夜跳空动量的5日均值差。捕捉盘中vs盘后信息差异。",
            "category": "日内结构",
        },
        {
            "formula": "Ts_Corr($close, $volume, 20)",
            "intuition": "量价相关性: 20日收盘价与成交量滚动相关。正相关=趋势健康，负相关=背离预警。",
            "category": "量价关系",
        },
        {
            "formula": "Ts_Std($returns, 5) / Ts_Std($returns, 60)",
            "intuition": "波动率比率: 短期波动/长期波动。高比率=波动聚集，低比率=均值回复。",
            "category": "波动率结构",
        },
        {
            "formula": "Min(Max($vwap, 20), Log($volume))",
            "intuition": "VWAP-成交量复合: VWAP下界约束后的成交量对数。捕捉受VWAP底限保护的流动性信号。",
            "category": "复合信号",
        },
        {
            "formula": "($close - Ts_Min($low, 20)) / (Ts_Max($high, 20) - Ts_Min($low, 20) + 0.001)",
            "intuition": "价格位置: 收盘价在20日高低区间的相对位置。类似 Stochastic Oscillator。",
            "category": "价格形态",
        },
    ]

    verbose = not args.quiet

    print("=" * 60)
    print("LLM Agent Extended — Enhanced Seed + Lower Threshold")
    print("=" * 60)
    print(f"  Seed:     {len(gfn_nontrivial)} GFlowNet non-trivial + {len(gfn_trivial)} trivial (filtered)")
    print(f"  FSA pre-ban: {len(TRIVIAL_SKELETONS_TO_BAN)} trivial $returns skeletons")
    print(f"  Rounds:   {args.rounds} × {args.agents} agents")
    print(f"  Pool min: |IC| >= {args.pool_min_ic}")
    print(f"  Eval:     2015-01-05 → 2024-12-31 (csi500)")
    print(f"  Prompt:   enhanced (anti-trivial + multi-field focus)")
    print()

    # 运行
    if args.agents == 1:
        result = run_agent(config=config, data_dir=args.data_dir, verbose=verbose)
        results = [result]
    else:
        config.parallel_agents = args.agents
        config.agent_temperature_min = 0.6
        config.agent_temperature_max = 1.0
        results = run_swarm(
            n_agents=args.agents,
            n_rounds=args.rounds,
            config=config,
            data_dir=args.data_dir,
            verbose=verbose,
        )

    # 注入非平凡 GFlowNet 种子到结果
    for r in results:
        pool = r.get('factor_pool', [])
        for formula, ic in gfn_nontrivial:
            if not any(f == formula for f, _ in pool):
                pool.append((formula, float(ic)))
        pool.sort(key=lambda x: -abs(x[1]))
        r['factor_pool'] = pool[:config.pool_max_size]

    merged = merge_swarm_results(results)

    if verbose:
        print(f"\n{'='*60}")
        print(f"Extended Swarm Summary")
        print(f"{'='*60}")
        print(f"Agents:     {args.agents} × {args.rounds} rounds")
        print(f"Pool:       {merged['pool_size']} unique factors (+ {len(gfn_nontrivial)} GFlowNet seeds)")
        print(f"Obs Pool:   {merged.get('obs_size', 0)} candidates")
        fsa = merged.get('fsa_stats', {})
        print(f"FSA banned: {fsa.get('banned_count', 0)} skeletons")
        print(f"Formulas:   {merged['total_formulas']} ({merged['total_valid']} valid)")
        print(f"Best:       {merged['best_formula']}")
        print(f"  |IC|={abs(merged['best_ic']):.4f}")
        print()
        print("Top 15 factors:")
        for i, (formula, ic) in enumerate(merged["pool"][:15], 1):
            print(f"  {i:2d}. {formula[:75]}  |IC|={abs(ic):.4f}")

    # FFO 完整评估 Top 20
    top_n = min(20, len(merged["pool"]))
    formulas_to_eval = [f for f, _ in merged["pool"][:top_n]]
    if verbose:
        print(f"\nRunning FFO evaluation on top {top_n} factors...")

    eval_results = evaluate_factors_with_ffo(
        formulas=formulas_to_eval,
        start="2015-01-05",
        end="2024-12-31",
        universe="csi500",
        data_dir=args.data_dir,
    )

    merged["ffo_evaluations"] = []
    for er in eval_results:
        merged["ffo_evaluations"].append(er.to_dict())
        if verbose and not er.error:
            print(f"  {er.formula[:70]}")
            print(f"    IC={er.ic_mean:.4f}, RankIC={er.rank_ic_mean:.4f}, ICIR={er.ic_ir:.2f}")

    # 保存
    output_path = FACTOR_LAB_DIR / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_data = {
        "config": config.to_dict(),
        "gflownet_nontrivial_count": len(gfn_nontrivial),
        "gflownet_trivial_filtered": len(gfn_trivial),
        "fsa_pre_ban_count": len(TRIVIAL_SKELETONS_TO_BAN),
        "swarm_summary": merged,
        "agent_results": [
            {
                "pool": r.get("factor_pool", []),
                "pool_stats": r.get("pool", {}),
                "observation_pool": r.get("observation_pool", []),
                "fsa_stats": r.get("fsa_stats", {}),
                "llm_usage": r.get("llm_usage", {}),
                "rounds": r.get("rounds_completed", 0),
            }
            for r in results
        ],
    }
    with open(output_path, "w") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False, default=str)
    print(f"\nSaved to: {output_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())