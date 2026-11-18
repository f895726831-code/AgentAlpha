"""
AST 级 PIT 泄漏检测器

使用 Python AST（抽象语法树）对因子/策略代码进行静态分析，
检测正则表达式难以精确判断的未来函数模式。

与 pit_handler.py 的关系:
  - pit_handler.detect_pit_violation() → 正则快速扫描（第一道防线）
  - leakage_checker.analyze_ast()      → AST 深度分析（第二道防线）

AST 检测能力:
  1. 向前索引: 在循环体内检测 `x[i + k]` (k > 0) 模式
  2. 负向 shift: `series.shift(-k)` 调用
  3. 前瞻切片: `x[i+k : i+m]` 在循环中使用
  4. 全区间统计: 无边界限制的 `.max()/.min()` 调用
  5. 变量名推断: 追踪 `look_ahead` 等可疑变量名
  6. 滚动前移: `.rolling().mean().shift(-k)` 链式调用
"""

import ast
import re
import sys
from typing import Dict, List, Optional, Set, Tuple

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


class PITLeakageVisitor(ast.NodeVisitor):
    """AST 遍历器，检测 Point-in-Time 违规模式。"""

    def __init__(self, source_lines: List[str], strategy_name: str = "unknown"):
        self.source_lines = source_lines
        self.strategy_name = strategy_name
        self.violations: List[Dict] = []

        # 状态跟踪
        self._in_loop = False
        self._loop_variables: Set[str] = set()  # 循环中使用的索引变量名
        self._suspicious_vars: Set[str] = set()   # 可疑变量名（look_ahead 等）
        self._in_docstring = False

    # ─── 辅助方法 ───

    def _add_violation(self, node: ast.AST, pattern_name: str, severity: str, suggestion: str, detail: str = ""):
        """记录一个违规"""
        self.violations.append({
            "line": node.lineno,
            "col": node.col_offset,
            "code": self.source_lines[node.lineno - 1].strip()[:120] if node.lineno <= len(self.source_lines) else "",
            "pattern_name": pattern_name,
            "severity": severity,
            "suggestion": suggestion,
            "detail": detail,
            "strategy": self.strategy_name,
            "detector": "ast",
        })

    def _is_positive_int(self, node: ast.AST) -> Optional[int]:
        """检查节点是否为正常量整数 (返回 None 表示不能确定)"""
        # 正数常量: 3
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            return node.value
        # 一元负号: -3 → 负数
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            if isinstance(node.operand, ast.Constant) and isinstance(node.operand.value, int):
                return -node.operand.value
        return None

    def _is_forward_offset(self, node: ast.AST, loop_var: str) -> bool:
        """
        检查表达式是否为 `loop_var + positive_int` 的形式。
        例如: i + 1, i + 3, j + 5
        """
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            # i + N
            if isinstance(node.left, ast.Name) and node.left.id == loop_var:
                return self._is_positive_int(node.right) is not None
            # N + i
            if isinstance(node.right, ast.Name) and node.right.id == loop_var:
                return self._is_positive_int(node.left) is not None
        return False

    def _has_forward_offset(self, slice_node: ast.AST, loop_var: str) -> bool:
        """检查切片中是否包含向前偏移"""
        if isinstance(slice_node, ast.BinOp):
            return self._is_forward_offset(slice_node, loop_var)
        return False

    # ─── 访问器 ───

    def visit_FunctionDef(self, node: ast.FunctionDef):
        """进入函数定义时检查 docstring"""
        # 处理 docstring
        if (node.body and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)):
            # 跳过 docstring
            self.generic_visit(node.body[0])
            for stmt in node.body[1:]:
                self.visit(stmt)
        else:
            self.generic_visit(node)

    def visit_For(self, node: ast.For):
        """检测 for 循环中的向前索引"""
        # 提取循环变量名
        loop_var = None
        if isinstance(node.target, ast.Name):
            loop_var = node.target.id

        prev_in_loop = self._in_loop
        prev_loop_vars = self._loop_variables.copy()

        self._in_loop = True
        if loop_var:
            self._loop_variables.add(loop_var)

        # 遍历循环体
        for stmt in node.body:
            self.visit(stmt)

        self._in_loop = prev_in_loop
        self._loop_variables = prev_loop_vars

    def visit_Subscript(self, node: ast.Subscript):
        """检测下标访问中的向前索引"""
        if not self._in_loop:
            self.generic_visit(node)
            return

        # 检测 x[i + k] 模式 (k > 0)
        for loop_var in self._loop_variables:
            if isinstance(node.slice, ast.BinOp) and isinstance(node.slice.op, ast.Add):
                if self._is_forward_offset(node.slice, loop_var):
                    val = self._is_positive_int(node.slice.right) if isinstance(node.slice.left, ast.Name) and node.slice.left.id == loop_var else self._is_positive_int(node.slice.left)
                    if val is not None and val > 0:
                        self._add_violation(
                            node,
                            "AST 向前索引",
                            "critical",
                            f"在循环体内使用 {loop_var} + {val} 索引访问了未来数据。"
                            f"应改为 {loop_var} - 1 或使用 shift(1) 回看过去数据。",
                            f"表达式: {loop_var} + {val}",
                        )

            # 检测 x[i + k : i + m] 前瞻切片
            if isinstance(node.slice, ast.Slice):
                if self._has_forward_offset(node.slice.lower, loop_var):
                    self._add_violation(
                        node,
                        "AST 前瞻切片",
                        "critical",
                        "切片下界使用了向前偏移，会在信号时刻访问未来数据。"
                        "确认条件应改为在信号发出前完成，或使用触价即入策略。",
                        f"切片: {ast.unparse(node.slice)}",
                    )

        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        """检测方法调用中的未来函数"""
        self._check_shift_call(node)
        self._check_rolling_shift(node)
        self._check_unbounded_stat(node)
        self.generic_visit(node)

    def _check_shift_call(self, node: ast.Call):
        """检测 series.shift(-k) 调用"""
        if not isinstance(node.func, ast.Attribute):
            return
        if node.func.attr != "shift":
            return
        if not node.args:
            return
        val = self._is_positive_int(node.args[0])
        if val is not None and val < 0:
            self._add_violation(
                node,
                "AST 负向 shift",
                "critical",
                f"shift({val}) 使用了未来数据。shift 的参数应为正数（回看）或 0。",
                f"shift({val}) 将未来 {abs(val)} 期的数据拉到了当前时刻",
            )

    def _check_rolling_shift(self, node: ast.Call):
        """检测 .rolling().mean().shift(-k) 链式调用"""
        if not isinstance(node.func, ast.Attribute):
            return
        if node.func.attr != "shift":
            return
        if not node.args:
            return
        val = self._is_positive_int(node.args[0])
        if val is None or val >= 0:
            return

        # 检查链式调用: xxx.rolling().mean().shift(-k)
        if isinstance(node.func.value, ast.Call):
            inner = node.func.value
            if isinstance(inner.func, ast.Attribute) and inner.func.attr == "mean":
                if isinstance(inner.func.value, ast.Call):
                    inner2 = inner.func.value
                    if isinstance(inner2.func, ast.Attribute) and inner2.func.attr == "rolling":
                        self._add_violation(
                            node,
                            "AST 滚动前移",
                            "critical",
                            "rolling().mean().shift(-k) 将 t+1 时刻的均值放到了 t 时刻。"
                            "应使用 rolling().mean() 或 rolling().mean().shift(1)。",
                            f"链式调用: rolling() → mean() → shift({val})",
                        )

    def _check_unbounded_stat(self, node: ast.Call):
        """检测无边界限制的 .max()/.min() 调用"""
        if not isinstance(node.func, ast.Attribute):
            return
        if node.func.attr not in ("max", "min"):
            return

        # 检查是否在循环体内
        if not self._in_loop:
            return

        # 如果调用对象是 simple name (如 close.max())，标记为警告
        if isinstance(node.func.value, ast.Name):
            self._add_violation(
                node,
                "AST 全区间统计",
                "warning",
                f"对全区间使用 .{node.func.attr}() 可能引入未来信息。"
                f"确认是否应限制在当前索引之前: df.iloc[:i].{node.func.attr}()。",
                f"调用: {node.func.value.id}.{node.func.attr}() 在循环体内",
            )

    def visit_Assign(self, node: ast.Assign):
        """检测可疑变量名赋值"""
        suspicious_patterns = [
            "look_ahead", "lookahead", "peek_forward", "peek",
            "future_data", "future_price", "future_return",
            "next_day", "next_price", "next_close",
        ]

        for target in node.targets:
            if isinstance(target, ast.Name):
                for pattern in suspicious_patterns:
                    if pattern in target.id.lower():
                        self._suspicious_vars.add(target.id)
                        self._add_violation(
                            node,
                            "AST 前瞻变量名",
                            "high",
                            f"变量名 '{target.id}' 暗示使用了未来数据。请检查该变量的赋值和使用逻辑。",
                            f"变量: {target.id}",
                        )
                        break

        self.generic_visit(node)

    def visit_Name(self, node: ast.Name):
        """检测可疑变量的使用"""
        if node.id in self._suspicious_vars and self._in_loop:
            # 在循环内使用可疑变量 → 高概率未来函数
            self._add_violation(
                node,
                "AST 前瞻变量使用",
                "critical",
                f"在循环体内使用了前瞻变量 '{node.id}'。"
                "确保该变量仅基于过去数据计算，而非未来数据。",
                f"变量: {node.id} 在循环内使用",
            )
        self.generic_visit(node)


def analyze_ast(
    code: str,
    strategy_name: str = "unknown",
    strict: bool = True,
) -> Tuple[bool, List[Dict], List[Dict]]:
    """
    对代码进行 AST 级 PIT 泄漏分析。

    参数:
        code: Python 源代码
        strategy_name: 策略名称
        strict: True=发现任何违规即返回未通过

    返回:
        (passed, ast_violations, parse_errors):
          - passed: 是否通过
          - ast_violations: AST 检测到的违规列表
          - parse_errors: 语法解析错误列表
    """
    # 尝试解析 AST
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, [], [{
            "line": e.lineno or 0,
            "error": str(e.msg),
            "detail": f"SyntaxError at line {e.lineno}: {e.msg}",
        }]

    source_lines = code.split("\n")

    # 运行 AST 分析
    visitor = PITLeakageVisitor(source_lines, strategy_name)
    visitor.visit(tree)

    violations = visitor.violations

    if strict:
        passed = len(violations) == 0
    else:
        passed = not any(v["severity"] == "critical" for v in violations)

    return passed, violations, []


def check_factor_code(
    factor_code: str,
    factor_name: str = "unknown",
) -> Dict:
    """
    对单个因子代码进行完整的 PIT 合规检查。

    组合使用:
      1. AST 静态分析 (本模块)
      2. 正则模式匹配 (pit_handler.detect_pit_violation)

    返回:
        {
            passed: bool,
            ast: {passed, violations},
            regex: {passed, violations},
            combined_violations: [...],
            summary: str,
        }
    """
    from src.data.pit_handler import detect_pit_violation

    # AST 分析
    ast_passed, ast_violations, ast_errors = analyze_ast(factor_code, factor_name, strict=False)

    # 正则分析
    regex_violations = detect_pit_violation(factor_code, factor_name)

    # 合并去重
    seen_lines = set()
    combined = []
    for v in ast_violations + regex_violations:
        line_key = (v["line"], v["pattern_name"])
        if line_key not in seen_lines:
            seen_lines.add(line_key)
            combined.append(v)

    ast_critical = [v for v in ast_violations if v["severity"] == "critical"]
    regex_critical = [v for v in regex_violations if v["severity"] == "critical"]

    passed = len(ast_critical) == 0 and len(regex_critical) == 0

    summary_parts = []
    if ast_violations:
        summary_parts.append(f"AST: {len(ast_violations)} 个违规 ({len(ast_critical)} critical)")
    if regex_violations:
        summary_parts.append(f"Regex: {len(regex_violations)} 个违规 ({len(regex_critical)} critical)")
    if ast_errors:
        summary_parts.append(f"解析错误: {len(ast_errors)}")

    return {
        "passed": passed,
        "factor_name": factor_name,
        "ast": {
            "passed": len(ast_critical) == 0,
            "violations": ast_violations,
            "critical_count": len(ast_critical),
            "errors": ast_errors,
        },
        "regex": {
            "passed": len(regex_critical) == 0,
            "violations": regex_violations,
            "critical_count": len(regex_critical),
        },
        "combined_violations": combined,
        "total_violations": len(combined),
        "summary": " | ".join(summary_parts) if summary_parts else "PIT 合规检查通过",
    }


def batch_check_factors(
    factors: List[Dict],
    verbose: bool = False,
) -> Dict:
    """
    批量为因子列表进行 PIT 合规检查。

    参数:
        factors: 因子列表，每个因子需包含 id, name, code
        verbose: 是否打印详细信息

    返回:
        {
            passed: int,
            failed: int,
            total: int,
            results: {factor_id: check_result},
            failed_factors: [...],
        }
    """
    results = {}
    failed_factors = []

    for f in factors:
        fid = f.get("id", "unknown")
        code = f.get("code", "")

        if not code:
            results[fid] = {
                "passed": True,
                "factor_name": f.get("name", fid),
                "summary": "无代码可检查",
            }
            continue

        check = check_factor_code(code, f.get("name", fid))
        results[fid] = check

        if not check["passed"]:
            failed_factors.append({
                "id": fid,
                "name": f.get("name", "unknown"),
                "violations": check["total_violations"],
                "summary": check["summary"],
            })

        if verbose and not check["passed"]:
            print(f"  ❌ {fid} ({f.get('name', 'unknown')}): {check['summary']}")

    return {
        "passed": len(results) - len(failed_factors),
        "failed": len(failed_factors),
        "total": len(results),
        "results": results,
        "failed_factors": failed_factors,
    }


def generate_ast_report(code: str, strategy_name: str = "unknown") -> str:
    """
    生成可读的 AST 分析报告。

    返回 Markdown 格式的字符串。
    """
    passed, violations, errors = analyze_ast(code, strategy_name)

    lines = [
        f"# PIT 泄漏分析报告: {strategy_name}",
        f"",
        f"**结果**: {'✅ 通过' if passed else '❌ 未通过'}",
        f"**AST 违规**: {len(violations)} 个",
        f"**解析错误**: {len(errors)} 个",
        f"",
    ]

    if errors:
        lines.append("## 解析错误")
        for e in errors:
            lines.append(f"- 行 {e['line']}: {e['error']}")
        lines.append("")

    if violations:
        critical = [v for v in violations if v["severity"] == "critical"]
        high = [v for v in violations if v["severity"] == "high"]
        warning = [v for v in violations if v["severity"] == "warning"]

        if critical:
            lines.append("## 🔴 Critical 违规")
            for v in critical:
                lines.append(f"### 行 {v['line']}: {v['pattern_name']}")
                lines.append(f"```python")
                lines.append(f"{v['code']}")
                lines.append(f"```")
                lines.append(f"**建议**: {v['suggestion']}")
                if v.get("detail"):
                    lines.append(f"**细节**: {v['detail']}")
                lines.append("")

        if high:
            lines.append("## 🟡 High 违规")
            for v in high:
                lines.append(f"- 行 {v['line']}: **{v['pattern_name']}** — {v['suggestion']}")
            lines.append("")

        if warning:
            lines.append("## 🟢 Warning")
            for v in warning:
                lines.append(f"- 行 {v['line']}: **{v['pattern_name']}** — {v['suggestion']}")
            lines.append("")

    return "\n".join(lines)