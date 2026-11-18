"""
FFO 表达式解析器 — 将因子公式字符串解析为 AST

支持的语法:
  - 字段引用:    $open, $high, $low, $close, $volume, $vwap, $returns
  - 算术运算:    +, -, *, /
  - 比较运算:    >, <, >=, <=, ==, !=
  - 逻辑运算:    &, |
  - 函数调用:    FuncName(args)
  - 数字字面量:   1.5, -0.5, 20
  - 括号分组:    (expr)

不支持:
  - 隐式乘法 (需要显式 *)
  - 三元条件 (使用 If(cond, t, f))

AST 节点类型:
  - FieldRef(name)       — 字段引用 $close
  - Number(value)        — 数字字面量
  - BinOp(left, op, right) — 二元运算
  - UnaryOp(op, operand) — 一元运算 (-expr)
  - FuncCall(name, args) — 函数调用
"""

import re
from dataclasses import dataclass, field
from typing import Any, List, Optional, Union


# ─── AST 节点定义 ───

@dataclass
class FieldRef:
    name: str  # "close", "open", etc. (不带 $ 前缀)

@dataclass
class Number:
    value: float

@dataclass
class BinOp:
    left: Any
    op: str  # "+", "-", "*", "/", ">", "<", ">=", "<=", "==", "!=", "&", "|"
    right: Any

@dataclass
class UnaryOp:
    op: str  # "-"
    operand: Any

@dataclass
class FuncCall:
    name: str  # "Rank", "Delay", "Ts_Mean", etc.
    args: List[Any] = field(default_factory=list)


# ─── Token 定义 ───

TOKEN_SPEC = [
    ("NUMBER",   r"\d+\.?\d*"),
    ("FIELD",    r"\$(?:open|high|low|close|volume|vwap|returns|vwap|amount|turn|adjclose|roe|eps|bps|gross_margin)"),
    ("FUNC",     r"[A-Za-z_][A-Za-z0-9_]*"),
    ("LPAREN",   r"\("),
    ("RPAREN",   r"\)"),
    ("COMMA",    r","),
    ("PLUS",     r"\+"),
    ("MINUS",    r"-"),
    ("STAR",     r"\*"),
    ("SLASH",    r"/"),
    ("EQEQ",     r"=="),
    ("NEQ",      r"!="),
    ("GTE",      r">="),
    ("LTE",      r"<="),
    ("GT",       r">"),
    ("LT",       r"<"),
    ("AND",      r"&"),
    ("PIPE",     r"\|"),
    ("WHITESPACE", r"[ \t\n\r]+"),
    ("MISMATCH", r"."),
]

def tokenize(expr: str) -> List[tuple]:
    """将表达式字符串拆分为 token 列表"""
    tokens = []
    pos = 0
    tok_re = re.compile("|".join(f"(?P<{name}>{pattern})" for name, pattern in TOKEN_SPEC))

    for m in tok_re.finditer(expr):
        kind = m.lastgroup
        value = m.group()
        if kind == "WHITESPACE":
            continue
        elif kind == "MISMATCH":
            raise SyntaxError(f"非法字符 '{value}' 位置 {m.start()}: {expr}")
        tokens.append((kind, value))
    return tokens


# ─── 递归下降解析器 ───

class Parser:
    """递归下降解析器"""

    def __init__(self, tokens: List[tuple]):
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> Optional[tuple]:
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return None

    def consume(self):
        tok = self.peek()
        if tok:
            self.pos += 1
        return tok

    def expect(self, kind: str, value: str = None):
        tok = self.peek()
        if not tok:
            raise SyntaxError(f"期望 {kind}，但表达式已结束")
        if tok[0] != kind:
            raise SyntaxError(f"期望 {kind}，但得到 {tok[0]}({tok[1]})")
        if value is not None and tok[1] != value:
            raise SyntaxError(f"期望 '{value}'，但得到 '{tok[1]}'")
        return self.consume()

    def parse(self) -> Any:
        """入口: expression"""
        return self.parse_or()

    # 优先级: || > && > == != > < <= > >= > + - > * / > unary > atom
    # 简化: | > & > 比较 > 加减 > 乘除 > 一元 > 原子

    def parse_or(self) -> Any:
        """或运算 (|)，最低优先级"""
        node = self.parse_and()
        while self.peek() and self.peek()[0] == "PIPE":
            self.consume()
            node = BinOp(node, "|", self.parse_and())
        return node

    def parse_and(self) -> Any:
        """与运算 (&)"""
        node = self.parse_comparison()
        while self.peek() and self.peek()[0] == "AND":
            self.consume()
            node = BinOp(node, "&", self.parse_comparison())
        return node

    def parse_comparison(self) -> Any:
        """比较运算"""
        node = self.parse_additive()
        tok = self.peek()
        if tok and tok[0] in ("EQEQ", "NEQ", "GTE", "LTE", "GT", "LT"):
            self.consume()
            op_map = {"EQEQ": "==", "NEQ": "!=", "GTE": ">=", "LTE": "<=", "GT": ">", "LT": "<"}
            return BinOp(node, op_map[tok[0]], self.parse_additive())
        return node

    def parse_additive(self) -> Any:
        """加减运算"""
        node = self.parse_multiplicative()
        while self.peek() and self.peek()[0] in ("PLUS", "MINUS"):
            tok = self.consume()
            op = "+" if tok[0] == "PLUS" else "-"
            node = BinOp(node, op, self.parse_multiplicative())
        return node

    def parse_multiplicative(self) -> Any:
        """乘除运算"""
        node = self.parse_unary()
        while self.peek() and self.peek()[0] in ("STAR", "SLASH"):
            tok = self.consume()
            op = "*" if tok[0] == "STAR" else "/"
            node = BinOp(node, op, self.parse_unary())
        return node

    def parse_unary(self) -> Any:
        """一元运算 (-expr)"""
        if self.peek() and self.peek()[0] == "MINUS":
            self.consume()
            return UnaryOp("-", self.parse_atom())
        return self.parse_atom()

    def parse_atom(self) -> Any:
        """原子: 数字 | 字段 | 函数调用 | 括号表达式"""
        tok = self.peek()
        if not tok:
            raise SyntaxError("表达式不完整")

        if tok[0] == "NUMBER":
            self.consume()
            return Number(float(tok[1]))

        if tok[0] == "FIELD":
            self.consume()
            return FieldRef(tok[1][1:])  # 去掉 $ 前缀

        if tok[0] == "FUNC":
            name = tok[1]
            self.consume()
            # 检查是否是函数调用
            if self.peek() and self.peek()[0] == "LPAREN":
                self.consume()  # (
                args = []
                if self.peek() and self.peek()[0] != "RPAREN":
                    args.append(self.parse())
                    while self.peek() and self.peek()[0] == "COMMA":
                        self.consume()
                        args.append(self.parse())
                self.expect("RPAREN")
                return FuncCall(name, args)
            else:
                raise SyntaxError(f"'{name}' 后需要 '(' 进行函数调用")

        if tok[0] == "LPAREN":
            self.consume()
            node = self.parse()
            self.expect("RPAREN")
            return node

        raise SyntaxError(f"意外的 token: {tok[0]}({tok[1]})")


def parse(formula: str) -> Any:
    """解析因子公式，返回 AST"""
    tokens = tokenize(formula)
    parser = Parser(tokens)
    ast = parser.parse()
    if parser.peek():
        tok = parser.peek()
        raise SyntaxError(f"解析后仍有剩余 token: {tok[0]}({tok[1]})")
    return ast


def ast_to_string(node: Any) -> str:
    """将 AST 转回可读字符串（用于调试）"""
    if isinstance(node, FieldRef):
        return f"${node.name}"
    if isinstance(node, Number):
        return str(node.value)
    if isinstance(node, BinOp):
        return f"({ast_to_string(node.left)} {node.op} {ast_to_string(node.right)})"
    if isinstance(node, UnaryOp):
        return f"(-{ast_to_string(node.operand)})"
    if isinstance(node, FuncCall):
        args = ", ".join(ast_to_string(a) for a in node.args)
        return f"{node.name}({args})"
    return str(node)