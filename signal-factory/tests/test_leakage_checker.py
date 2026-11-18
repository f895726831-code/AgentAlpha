"""
PIT 泄漏检测器测试

测试 detect_pit_violation 对各种未来函数模式的检测能力。
"""

import pytest
from src.data.pit_handler import detect_pit_violation, validate_strategy_pit


class TestLeakageDetection:
    """未来函数检测测试"""

    def test_detect_forward_indexing(self):
        """检测向前索引: close.iloc[i+1]"""
        code = """
close = data["close"]
for i in range(30, len(data)):
    if close.iloc[i + 3] > 60:
        signal[i] = 1
"""
        violations = detect_pit_violation(code, "test")
        critical = [v for v in violations if v["severity"] == "critical"]
        assert len(critical) >= 1, "应检测到向前索引"
        assert any("向前索引" in v["pattern_name"] for v in critical)

    def test_detect_negative_shift(self):
        """检测负向 shift: close.shift(-1)"""
        code = """
factor = close.shift(-1) - close
"""
        violations = detect_pit_violation(code, "test")
        critical = [v for v in violations if v["severity"] == "critical"]
        assert len(critical) >= 1, "应检测到负向 shift"
        assert any("shift" in v["pattern_name"] for v in critical)

    def test_detect_look_ahead_slice(self):
        """检测前瞻切片: close.iloc[i+1:i+4]"""
        code = """
look_ahead = min(i + 3 + 1, len(data))
if close.iloc[i + 1 : look_ahead].min() > 60:
    signal[i] = 1
"""
        violations = detect_pit_violation(code, "test")
        critical = [v for v in violations if v["severity"] == "critical"]
        assert len(critical) >= 1, "应检测到前瞻切片"
        pattern_names = [v["pattern_name"] for v in violations]
        assert any("前瞻切片" in p or "前瞻变量" in p for p in pattern_names)

    def test_detect_look_ahead_variable(self):
        """检测前瞻变量名: look_ahead"""
        code = """
look_ahead = i + 3
if close[look_ahead] > 60:
    signal[i] = 1
"""
        violations = detect_pit_violation(code, "test")
        assert len(violations) >= 1, "应检测到前瞻变量名"

    def test_detect_rolling_shift_negative(self):
        """检测滚动均值前移: rolling().mean().shift(-1)"""
        code = """
ma = close.rolling(20).mean().shift(-1)
"""
        violations = detect_pit_violation(code, "test")
        critical = [v for v in violations if v["severity"] == "critical"]
        assert len(critical) >= 1, "应检测到滚动均值前移"

    def test_clean_code_passes(self):
        """验证干净的代码通过检测"""
        code = """
close = data["close"]
ma = close.rolling(20).mean()
signal = pd.Series(0, index=data.index)
for i in range(20, len(data)):
    if close.iloc[i] > ma.iloc[i]:
        signal.iloc[i] = 1
"""
        violations = detect_pit_violation(code, "clean_strategy")
        critical = [v for v in violations if v["severity"] == "critical"]
        assert len(critical) == 0, f"干净代码不应有 critical 违规: {critical}"

    def test_comments_are_ignored(self):
        """验证注释行被忽略"""
        code = """
# 注释: close.iloc[i + 3] 是未来函数，但这里只是注释
# 另一个注释: look_ahead = 5
close = data["close"]
signal = pd.Series(0, index=data.index)
"""
        violations = detect_pit_violation(code, "test")
        # 注释行应被忽略，不应有任何违规
        critical = [v for v in violations if v["severity"] == "critical"]
        assert len(critical) == 0, f"注释行不应被检测: {critical}"


class TestStrategyValidation:
    """策略 PIT 验证测试"""

    def test_validate_clean_strategy(self):
        """验证干净策略通过"""
        code = """
close = data["close"]
ma = close.rolling(20).mean()
signal = pd.Series(0, index=data.index)
for i in range(20, len(data)):
    if close.iloc[i] > ma.iloc[i]:
        signal.iloc[i] = 1
"""
        passed, violations = validate_strategy_pit(code, "clean", strict=True)
        assert passed, f"干净策略应通过: {violations}"

    def test_validate_leaky_strategy(self):
        """验证泄漏策略被拦截"""
        code = """
close = data["close"]
for i in range(30, len(data)):
    if close.iloc[i + 3] > 60:
        signal.iloc[i] = 1
"""
        passed, violations = validate_strategy_pit(code, "leaky", strict=True)
        assert not passed, "泄漏策略应被拦截"

    def test_validate_non_strict_mode(self):
        """验证非严格模式：仅 critical 拦截"""
        code = """
# 全区间统计可能有问题但不一定是泄漏
close = data["close"]
max_price = close.max()
min_price = close.min()
"""
        passed, violations = validate_strategy_pit(code, "test", strict=False)
        assert passed, "非严格模式下，仅 warning 应通过"


class TestBreakout60Fix:
    """验证 breakout_60 修复后的信号生成"""

    def test_fixed_breakout_60_no_look_ahead(self):
        """修复后的 breakout_60 不应使用未来数据"""
        from src.backtest.signal_generator import generate_breakout_60
        import inspect

        source = inspect.getsource(generate_breakout_60)
        violations = detect_pit_violation(source, "breakout_60_fixed")
        critical = [v for v in violations if v["severity"] == "critical"]
        assert len(critical) == 0, f"修复后的 breakout_60 不应有 critical 违规: {critical}"

    def test_limit_up_chase_fixed(self):
        """修复后的 limit_up_chase 不应假设完美成交"""
        from src.backtest.signal_generator import generate_limit_up_chase
        import inspect

        source = inspect.getsource(generate_limit_up_chase)
        # 不应使用 low 作为入场价（原始代码的 bug）
        assert "low.iloc[i]" not in source or "open_p.iloc[i]" in source, \
            "修复后的 limit_up_chase 应使用开盘价而非最低价"


class TestASTLeakageChecker:
    """AST 级泄漏检测器测试 — 超越正则的能力"""

    def test_ast_forward_index_in_loop(self):
        """AST 检测: 循环内向前索引"""
        from src.data.leakage_checker import analyze_ast

        code = """
close = data["close"]
for i in range(30, len(data)):
    if close.iloc[i + 5] > 60:
        signal[i] = 1
"""
        passed, violations, errors = analyze_ast(code, "test")
        assert not passed, "AST 应检测到循环内向前索引"
        assert any("向前索引" in v["pattern_name"] for v in violations)

    def test_ast_negative_shift_detected(self):
        """AST 检测: shift(-3)"""
        from src.data.leakage_checker import analyze_ast

        code = """
factor = close.shift(-3) - close
"""
        passed, violations, errors = analyze_ast(code, "test")
        assert not passed, "AST 应检测到 shift(-3)"
        assert any("shift" in v["pattern_name"] for v in violations)

    def test_ast_rolling_shift_chain(self):
        """AST 检测: rolling().mean().shift(-1) 链式调用"""
        from src.data.leakage_checker import analyze_ast

        code = """
ma = close.rolling(20).mean().shift(-1)
"""
        passed, violations, errors = analyze_ast(code, "test")
        assert not passed, "AST 应检测到滚动前移"
        assert any("滚动" in v["pattern_name"] for v in violations)

    def test_ast_clean_shift_passes(self):
        """AST 确认: shift(1) 不触发"""
        from src.data.leakage_checker import analyze_ast

        code = """
factor = close - close.shift(1)
"""
        passed, violations, errors = analyze_ast(code, "test")
        assert passed, f"shift(1) 不应触发违规: {violations}"

    def test_ast_suspicious_var_name(self):
        """AST 检测: 前瞻变量名 look_ahead"""
        from src.data.leakage_checker import analyze_ast

        code = """
look_ahead = i + 3
if close[look_ahead] > 60:
    signal[i] = 1
"""
        passed, violations, errors = analyze_ast(code, "test")
        # 变量名检测 + 在条件中使用
        assert any("前瞻变量" in v["pattern_name"] for v in violations), \
            "应检测到 look_ahead 变量名"

    def test_ast_clean_code_passes(self):
        """AST 确认: 干净代码通过"""
        from src.data.leakage_checker import analyze_ast

        code = """
close = data["close"]
ma = close.rolling(20).mean()
signal = pd.Series(0, index=data.index)
for i in range(20, len(data)):
    if close.iloc[i] > ma.iloc[i]:
        signal.iloc[i] = 1
"""
        passed, violations, errors = analyze_ast(code, "clean")
        assert passed, f"干净代码应通过: {violations}"

    def test_check_factor_code_combined(self):
        """组合检测: AST + Regex 双重验证"""
        from src.data.leakage_checker import check_factor_code

        code = """
close = data["close"]
for i in range(30, len(data)):
    if close.iloc[i + 3] > 60:
        signal.iloc[i] = 1
"""
        result = check_factor_code(code, "leaky_factor")
        assert not result["passed"], "组合检测应拦截泄漏代码"
        # 应同时有 AST 和 Regex 结果
        assert result["ast"]["critical_count"] >= 1, "AST 应检测到"
        assert result["regex"]["critical_count"] >= 1, "Regex 应检测到"

    def test_syntax_error_handled(self):
        """AST 解析错误应优雅处理"""
        from src.data.leakage_checker import analyze_ast

        code = "def broken("  # 语法错误
        passed, violations, errors = analyze_ast(code, "broken")
        assert not passed, "语法错误应返回未通过"
        assert len(errors) > 0, "应报告解析错误"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])