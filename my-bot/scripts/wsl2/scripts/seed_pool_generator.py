"""
seed_pool_generator.py — 从 factor_registry.json 生成动态种子池

将 47 个已验证因子按 |IC| 排序 → AST 去重 → 多样性格雷欣选择 → 输出 seed_pool.json。

用法:
    python seed_pool_generator.py                          # 从 registry 生成
    python seed_pool_generator.py --top 40                 # 指定种子数上限
    python seed_pool_generator.py --registry PATH          # 指定 registry 路径
"""

import json
import re
import hashlib
import sys
from pathlib import Path
from collections import Counter

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ─── 环境自适应 ───
_FACTORY_PATH = Path(__file__).resolve().parent.parent  # scripts/wsl2/
sys.path.insert(0, str(_FACTORY_PATH))

from factory.dedup import normalize_formula, canonical_formula

# 尝试加载 FFO（WSL2 环境），Windows 回退到字符串规范化
_FFO_AVAILABLE = False
try:
    from ffo.parser import parse as ffo_parse, ast_to_string
    _FFO_AVAILABLE = True
except ImportError:
    pass

# ─── 配置 ───
DEFAULT_REGISTRY = Path("D:/AgentAlpha/my-bot/scripts/wsl2/data/factor_registry.json")
DEFAULT_OUTPUT = Path("D:/AgentAlpha/my-bot/scripts/wsl2/gflownet/seed_pool.json")
MIN_IC = 0.015
TARGET_SEEDS = 30

# ─── 解析模式 ───
FIELD_PAT = re.compile(r"\$(close|open|high|low|volume|vwap|returns)")
OP_PAT = re.compile(
    r"\b(Rank|Scale|Sign|Abs|Log|Ts_Mean|Ts_Std|Ts_Max|Ts_Min|"
    r"Ts_Rank|Ts_Sum|Ts_ArgMax|Ts_ArgMin|Delay|Delta|Return|"
    r"Quantile|SignedPower|Max|Min|If|Ts_Corr|Ts_Cov)\b"
    r"|(?<![a-zA-Z0-9_$])[+\-*/](?![+\-*/])"  # 算术操作符
)
COMPARISON_OPS = {">", "<", ">=", "<=", "==", "!="}

# GFlowNet 词汇表中的合法字段
GFN_FIELDS = {"open", "high", "low", "close", "volume", "vwap", "returns"}

# 匹配任何 $field 引用（用于检测非法字段）
ANY_FIELD_PAT = re.compile(r"\$(\w+)")


def validate_formula(formula: str) -> bool:
    """验证公式有效性。

    WSL2: 使用 FFO parser 严格验证。
    Windows: 使用 gflownet grammar 验证 + dedup normalize 不报错。
    """
    if _FFO_AVAILABLE:
        try:
            ffo_parse(formula)
            return True
        except Exception:
            return False

    # Windows 回退: 语法检查（tokenize + 基本结构）
    try:
        # 1. normalize 不崩
        norm = normalize_formula(formula)
        # 2. 至少有一个字段和一个操作符
        if not FIELD_PAT.search(formula):
            return False
        if not OP_PAT.search(formula):
            return False
        # 3. 括号平衡
        if formula.count("(") != formula.count(")"):
            return False
        return True
    except Exception:
        return False


def ast_canonical_hash(formula: str) -> str:
    """因子的规范哈希。

    WSL2: FFO AST → ast_to_string → MD5（严格结构等价）。
    Windows: factory.dedup.canonical_formula → MD5（字符串规范化 + 交换律）。
    """
    if _FFO_AVAILABLE:
        try:
            ast = ffo_parse(formula)
            canonical = ast_to_string(ast)
            return hashlib.md5(canonical.encode()).hexdigest()
        except Exception:
            pass

    # Windows 回退
    canonical = canonical_formula(formula)
    return hashlib.md5(canonical.encode()).hexdigest()


def extract_candidates(registry_path: Path) -> list:
    """从 registry 提取种子候选（含字段和操作符解析）。"""
    with open(registry_path, "r", encoding="utf-8") as f:
        reg = json.load(f)

    candidates = []
    for key, f in reg.get("factors", {}).items():
        formula = f.get("formula", "")
        ic = f.get("ic_mean", 0)
        if not formula or abs(ic) < MIN_IC:
            continue

        fields = set(FIELD_PAT.findall(formula))
        ops = set(OP_PAT.findall(formula))

        # 跳过高比较操作符因子
        if ops & COMPARISON_OPS:
            continue

        # 只保留 GFlowNet 词汇表内的字段
        if not fields.issubset(GFN_FIELDS):
            continue

        # 双重检查：扫描所有 $field 引用，拒绝非法字段
        all_fields_in_formula = set(ANY_FIELD_PAT.findall(formula))
        if not all_fields_in_formula.issubset(GFN_FIELDS):
            continue

        # 公式有效性检查
        if not validate_formula(formula):
            continue

        candidates.append({
            "formula": formula,
            "ic_mean": round(abs(ic), 6),
            "fields": sorted(fields),
            "operators": sorted(ops),
            "channel": f.get("source_channel", "unknown"),
            "complexity": f.get("complexity", 0),
        })

    return candidates


def dedup_by_ast(candidates: list) -> list:
    """AST 结构去重 — 相同规范哈希的只保留 |IC| 最大的。"""
    seen: dict = {}
    for c in candidates:
        h = ast_canonical_hash(c["formula"])
        if h not in seen or c["ic_mean"] > seen[h]["ic_mean"]:
            seen[h] = c
    return sorted(seen.values(), key=lambda x: -x["ic_mean"])


def greedy_diverse_selection(candidates: list, target: int) -> list:
    """
    格雷欣多样性格雷因选择:
    - 前 15 名无条件入选（保证高 IC）
    - 之后只选引入新 field×operator 组合的因子
    - 最多 target 个种子
    """
    selected = []
    covered_combos: set = set()

    for c in candidates:
        if len(selected) >= target:
            break

        combos = set(f"{f} x {o}" for f in c["fields"] for o in c["operators"])
        new_combos = combos - covered_combos

        if len(selected) < 15 or len(new_combos) > 0:
            selected.append(c)
            covered_combos |= combos

    return selected


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate GFlowNet seed pool from factor registry")
    parser.add_argument("--top", type=int, default=TARGET_SEEDS, help=f"Max seeds (default: {TARGET_SEEDS})")
    parser.add_argument("--registry", type=str, default=str(DEFAULT_REGISTRY), help="Registry JSON path")
    parser.add_argument("--output", type=str, default=str(DEFAULT_OUTPUT), help="Output JSON path")
    parser.add_argument("--dry-run", action="store_true", help="Print without saving")
    args = parser.parse_args()

    registry_path = Path(args.registry)
    if not registry_path.exists():
        print(f"ERROR: Registry not found: {registry_path}")
        sys.exit(1)

    print(f"Loading: {registry_path}")
    candidates = extract_candidates(registry_path)
    print(f"  Candidates (IC >= {MIN_IC}, valid FFO, no comparison ops): {len(candidates)}")

    deduped = dedup_by_ast(candidates)
    print(f"  After AST dedup: {len(deduped)}")

    selected = greedy_diverse_selection(deduped, args.top)
    print(f"  Selected for seed pool: {len(selected)}")

    # ─── 覆盖度统计 ───
    all_fields = set()
    all_ops = set()
    all_combos = set()
    for s in selected:
        all_fields.update(s["fields"])
        all_ops.update(s["operators"])
        for f in s["fields"]:
            for o in s["operators"]:
                all_combos.add(f"{f} x {o}")

    print(f"\n  Field coverage ({len(all_fields)}): {sorted(all_fields)}")
    print(f"  Operator coverage ({len(all_ops)}): {sorted(all_ops)}")
    print(f"  Unique field x op combos: {len(all_combos)}")

    # ─── Top/Bottom IC ───
    print(f"\n  Top 10 seeds:")
    for i, s in enumerate(selected[:10]):
        print(f"    {i+1:2d}. |IC|={s['ic_mean']:.4f}  [{','.join(s['fields'])}]  {s['formula'][:70]}")

    print(f"\n  Last 5 seeds (diversity picks):")
    for i, s in enumerate(selected[-5:]):
        print(f"    {len(selected)-4+i:2d}. |IC|={s['ic_mean']:.4f}  [{','.join(s['fields'])}]  {s['formula'][:70]}")

    # ─── 构建输出 ───
    output = {
        "version": "1.0",
        "generated_at": "2026-11-11",
        "source": str(registry_path),
        "total_seeds": len(selected),
        "field_coverage": sorted(all_fields),
        "operator_coverage": sorted(all_ops),
        "field_op_combos": len(all_combos),
        "seeds": [
            {
                "formula": s["formula"],
                "ic_mean": s["ic_mean"],
                "fields": s["fields"],
                "operators": s["operators"],
            }
            for s in selected
        ],
    }

    if args.dry_run:
        print(f"\n  Dry run — not saved")
    else:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"\n  Saved: {output_path} ({output_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()