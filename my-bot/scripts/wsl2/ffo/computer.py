"""
FFO 因子计算机 — 将 AST 求值为因子值

遍历 AST，对 Qlib 市场数据执行计算。

核心流程:
  1. 解析公式 → AST (parser.parse)
  2. 加载数据 → MultiIndex DataFrame (data_loader.load_qlib_data)
  3. AST 求值 → date × instrument matrix (computer.evaluate)
  4. 计算 IC → 评估指标 (evaluator)

处理不同类型节点:
  - FieldRef:    从数据中提取对应字段的 panel matrix
  - Number:       返回标量广播为 matrix
  - BinOp:        两个 matrix 的逐元素运算
  - UnaryOp:      单目运算
  - FuncCall:     调用注册的操作符
"""

import numpy as np
import pandas as pd
from typing import Any, Dict, Optional

from .parser import FieldRef, Number, BinOp, UnaryOp, FuncCall
from .data_loader import get_field_matrix
from .operators import OPERATOR_REGISTRY, call_operator


# ─── AST 求值器 ───

class FactorComputer:
    """因子公式 AST 求值器"""

    def __init__(self, data: pd.DataFrame):
        """
        参数:
            data: MultiIndex (date, instrument) DataFrame, columns = features
        """
        self.data = data
        self._cache: Dict[str, pd.DataFrame] = {}  # field_name → panel matrix

    def _get_field(self, name: str) -> pd.DataFrame:
        """获取字段的 panel matrix (date × instrument)"""
        name_lower = name.lower()
        if name_lower not in self._cache:
            self._cache[name_lower] = get_field_matrix(self.data, name_lower)
        return self._cache[name_lower]

    def evaluate(self, ast: Any) -> pd.DataFrame:
        """
        递归求值 AST，返回 date × instrument 的面板数据。

        返回:
            pd.DataFrame with index=date, columns=instrument
        """
        if isinstance(ast, FieldRef):
            return self._get_field(ast.name)

        elif isinstance(ast, Number):
            # 标量广播
            sample_field = list(self.data.columns)[0]
            template = get_field_matrix(self.data, sample_field)
            return pd.DataFrame(ast.value, index=template.index, columns=template.columns)

        elif isinstance(ast, UnaryOp):
            operand = self.evaluate(ast.operand)
            if ast.op == "-":
                return -operand
            raise ValueError(f"不支持的一元操作符: {ast.op}")

        elif isinstance(ast, BinOp):
            left = self.evaluate(ast.left)
            right = self.evaluate(ast.right)

            # 对齐索引
            left, right = left.align(right, join="inner")

            op = ast.op
            if op == "+":
                return left + right
            elif op == "-":
                return left - right
            elif op == "*":
                return left * right
            elif op == "/":
                return left / right.replace(0, np.nan)
            elif op == ">":
                return left > right
            elif op == "<":
                return left < right
            elif op == ">=":
                return left >= right
            elif op == "<=":
                return left <= right
            elif op == "==":
                return left == right
            elif op == "!=":
                return left != right
            elif op == "&":
                return left & right
            elif op == "|":
                return left | right
            else:
                raise ValueError(f"不支持的二元操作符: {op}")

        elif isinstance(ast, FuncCall):
            return self._eval_funcall(ast)

        else:
            raise TypeError(f"不支持的 AST 节点类型: {type(ast)}")

    def _eval_funcall(self, node: FuncCall) -> pd.DataFrame:
        """求值函数调用"""
        name = node.name.lower()

        # 特殊函数: If(cond, t, f)
        if name == "if":
            if len(node.args) != 3:
                raise ValueError(f"If() 需要 3 个参数，得到 {len(node.args)}")
            cond = self.evaluate(node.args[0])
            true_val = self.evaluate(node.args[1])
            false_val = self.evaluate(node.args[2])
            cond, true_val = cond.align(true_val, join="inner")
            cond, false_val = cond.align(false_val, join="inner")
            result = true_val.where(cond.astype(bool), false_val)
            return result

        # 特殊函数: Max(x, y), Min(x, y)
        if name in ("max", "min"):
            if len(node.args) < 2:
                raise ValueError(f"{name}() 需要至少 2 个参数")
            left = self.evaluate(node.args[0])
            right = self.evaluate(node.args[1])
            left, right = left.align(right, join="inner")
            if name == "max":
                return pd.DataFrame(
                    np.maximum(left.values, right.values),
                    index=left.index, columns=left.columns
                )
            else:
                return pd.DataFrame(
                    np.minimum(left.values, right.values),
                    index=left.index, columns=left.columns
                )

        # 特殊函数: Ts_Corr(x, y, d), Ts_Cov(x, y, d)
        if name in ("ts_corr", "ts_cov"):
            if len(node.args) < 3:
                raise ValueError(f"{name}() 需要 3 个参数: x, y, d")
            x = self.evaluate(node.args[0])
            y = self.evaluate(node.args[1])
            d = self._eval_arg_value(node.args[2])
            x, y = x.align(y, join="inner")

            def _rolling_corr(row_x, row_y):
                return row_x.rolling(window=d, min_periods=max(1, d//2)).corr(row_y)

            def _rolling_cov(row_x, row_y):
                return row_x.rolling(window=d, min_periods=max(1, d//2)).cov(row_y)

            result = pd.DataFrame(index=x.index, columns=x.columns)
            for col in x.columns:
                if name == "ts_corr":
                    result[col] = _rolling_corr(x[col], y[col])
                else:
                    result[col] = _rolling_cov(x[col], y[col])
            return result

        # 标准操作符: op(expr, arg1, arg2, ...)
        if len(node.args) == 0:
            raise ValueError(f"{name}() 需要至少 1 个参数")

        data_arg = self.evaluate(node.args[0])
        numeric_args = [self._eval_arg_value(a) for a in node.args[1:]]

        if name in OPERATOR_REGISTRY:
            return call_operator(name, data_arg, *numeric_args)
        else:
            # 尝试常见别名
            alias_map = {
                "indneutralize": "scale",  # 简化: 行业中性化暂不实现
                "decaylinear": "ts_mean",
                "sma": "ts_mean",
                "ema": "ts_mean",
            }
            if name in alias_map:
                return call_operator(alias_map[name], data_arg, *numeric_args)
            raise ValueError(f"未注册的操作符: '{name}'。可用: {list(OPERATOR_REGISTRY.keys())}")

    def _eval_arg_value(self, arg: Any) -> float:
        """求值函数参数（标量）"""
        if isinstance(arg, Number):
            return arg.value
        if isinstance(arg, FieldRef):
            # 参数是字段引用 —— 不常见，返回字符串
            return arg.name
        # 尝试求值为 DataFrame 然后取标量
        val = self.evaluate(arg)
        if isinstance(val, pd.DataFrame):
            if val.size == 1:
                return float(val.iloc[0, 0])
            return val  # 返回 DataFrame 用于 Ts_Corr 等
        return float(val)