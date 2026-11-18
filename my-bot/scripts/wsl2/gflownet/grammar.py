"""
GFlowNet 因子表达式语法 — Token 词汇表 + 有效性检查 + 公式重建

设计: 前缀表达式 (Prefix Notation)

  前缀表达式按先序遍历编码 AST:
    Rank(Ts_Mean($close, 10))  →  [BOS, Rank, Ts_Mean, $close, 10, EOS]

  每个 token 有 arity (子节点数):
    - Leaf (field/constant): arity=0, 不创建新槽位
    - Operator: arity=k, 消耗 1 个槽位，创建 k 个新槽位

  状态跟踪:
    - stack_depth: 当前需要填充的槽位数
    - BOS 时 stack_depth=1 (需要一个根表达式)
    - 添加 arity=k 的 token: stack_depth = stack_depth - 1 + k
    - stack_depth=0 时表达式完整

  有效性约束:
    - 每步只允许不会导致 stack_depth 超过剩余步数的 token
    - STOP 只在 stack_depth=0 时有效
    - 常量只在非位置0时有效 (不能以常量开始表达式)
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict
import re


# ═══════════════════════════════════════════════════════════════
# Token 定义
# ═══════════════════════════════════════════════════════════════

@dataclass
class TokenDef:
    """单个 token 的元数据"""
    idx: int           # 在动作空间中的索引
    name: str          # 显示名称
    label: str         # 简短标签 (用于公式输出)
    arity: int         # 子节点数 (prefix grammar)
    token_type: str    # "field", "const", "unary", "param", "binary_val",
                       # "ternary", "arith", "compare", "logic", "special"
    formula_prefix: str = ""  # 函数名前缀 (如 "Ts_Mean")，空字符串表示中缀操作符


# ─── 特殊 token ───

PAD_TOKEN = TokenDef(0, "PAD", "", 0, "special")
BOS_TOKEN = TokenDef(1, "BOS", "", 1, "special")  # arity=1: 需要一个根表达式
EOS_TOKEN = TokenDef(2, "EOS", "", 0, "special")


# ─── 字段 (arity=0, push to stack) ───

FIELD_TOKENS = [
    TokenDef(3,  "$open",          "open",          0, "field"),
    TokenDef(4,  "$high",          "high",          0, "field"),
    TokenDef(5,  "$low",           "low",           0, "field"),
    TokenDef(6,  "$close",         "close",         0, "field"),
    TokenDef(7,  "$volume",        "volume",        0, "field"),
    TokenDef(8,  "$vwap",          "vwap",          0, "field"),
    TokenDef(9,  "$returns",       "returns",       0, "field"),
]

# ─── 数值常量 (arity=0) ───

CONST_TOKENS = [
    TokenDef(10, "5",   "5",   0, "const"),
    TokenDef(11, "10",  "10",  0, "const"),
    TokenDef(12, "20",  "20",  0, "const"),
    TokenDef(13, "30",  "30",  0, "const"),
    TokenDef(14, "60",  "60",  0, "const"),
    TokenDef(15, "120", "120", 0, "const"),
    TokenDef(16, "250", "250", 0, "const"),
]

# ─── 一元操作符 (arity=1, 仅数据子节点) ───

UNARY_OP_TOKENS = [
    TokenDef(17, "Rank",     "Rank",     1, "unary",     "Rank"),
    TokenDef(18, "Scale",    "Scale",    1, "unary",     "Scale"),
    TokenDef(19, "Sign",     "Sign",     1, "unary",     "Sign"),
    TokenDef(20, "Abs",      "Abs",      1, "unary",     "Abs"),
    TokenDef(21, "Log",      "Log",      1, "unary",     "Log"),
]

# ─── 带参数操作符 (arity=2, 数据子节点 + 参数子节点) ───

PARAM_OP_TOKENS = [
    TokenDef(22, "Ts_Mean",    "Ts_Mean",    2, "param",    "Ts_Mean"),
    TokenDef(23, "Ts_Std",     "Ts_Std",     2, "param",    "Ts_Std"),
    TokenDef(24, "Ts_Max",     "Ts_Max",     2, "param",    "Ts_Max"),
    TokenDef(25, "Ts_Min",     "Ts_Min",     2, "param",    "Ts_Min"),
    TokenDef(26, "Ts_Rank",    "Ts_Rank",    2, "param",    "Ts_Rank"),
    TokenDef(27, "Ts_Sum",     "Ts_Sum",     2, "param",    "Ts_Sum"),
    TokenDef(28, "Ts_ArgMax",  "Ts_ArgMax",  2, "param",    "Ts_ArgMax"),
    TokenDef(29, "Ts_ArgMin",  "Ts_ArgMin",  2, "param",    "Ts_ArgMin"),
    TokenDef(30, "Delay",      "Delay",      2, "param",    "Delay"),
    TokenDef(31, "Delta",      "Delta",      2, "param",    "Delta"),
    TokenDef(32, "Return",     "Return",     2, "param",    "Return"),
    TokenDef(33, "Quantile",   "Quantile",   2, "param",    "Quantile"),
    TokenDef(34, "SignedPower","SignedPower",2, "param",    "SignedPower"),
]

# ─── 二元值操作符 (arity=2, 两个数据子节点) ───

BINARY_VAL_TOKENS = [
    TokenDef(35, "Max",  "Max",  2, "binary_val", "Max"),
    TokenDef(36, "Min",  "Min",  2, "binary_val", "Min"),
]

# ─── 三元操作符 (arity=3) ───

TERNARY_OP_TOKENS = [
    TokenDef(37, "If",       "If",       3, "ternary", "If"),
    TokenDef(38, "Ts_Corr",  "Ts_Corr",  3, "ternary", "Ts_Corr"),
    TokenDef(39, "Ts_Cov",   "Ts_Cov",   3, "ternary", "Ts_Cov"),
]

# ─── 算术/比较/逻辑操作符 (arity=2, 中缀语法) ───

INFIX_OP_TOKENS = [
    TokenDef(40, "+",  "+",  2, "arith",   ""),   # 中缀操作符 prefix 为空
    TokenDef(41, "-",  "-",  2, "arith",   ""),
    TokenDef(42, "*",  "*",  2, "arith",   ""),
    TokenDef(43, "/",  "/",  2, "arith",   ""),
    TokenDef(44, ">",  ">",  2, "compare", ""),
    TokenDef(45, "<",  "<",  2, "compare", ""),
    TokenDef(46, ">=", ">=", 2, "compare", ""),
    TokenDef(47, "<=", "<=", 2, "compare", ""),
    TokenDef(48, "==", "==", 2, "compare", ""),
    TokenDef(49, "!=", "!=", 2, "compare", ""),
    TokenDef(50, "&",  "&",  2, "logic",   ""),
    TokenDef(51, "|",  "|",  2, "logic",   ""),
]

# ─── 终止符 ───

STOP_TOKEN = TokenDef(52, "STOP", "", 0, "special")


# ═══════════════════════════════════════════════════════════════
# 词汇表构建
# ═══════════════════════════════════════════════════════════════

ALL_TOKENS = (
    [PAD_TOKEN, BOS_TOKEN, EOS_TOKEN]
    + FIELD_TOKENS
    + CONST_TOKENS
    + UNARY_OP_TOKENS
    + PARAM_OP_TOKENS
    + BINARY_VAL_TOKENS
    + TERNARY_OP_TOKENS
    + INFIX_OP_TOKENS
    + [STOP_TOKEN]
)

# 动作 token (GFlowNet 可选择的操作)
ACTION_TOKENS = (
    FIELD_TOKENS
    + CONST_TOKENS
    + UNARY_OP_TOKENS
    + PARAM_OP_TOKENS
    + BINARY_VAL_TOKENS
    + TERNARY_OP_TOKENS
    + INFIX_OP_TOKENS
    + [STOP_TOKEN]
)

N_ACTIONS = len(ACTION_TOKENS)   # 54
VOCAB_SIZE = len(ALL_TOKENS)      # 57 (含 PAD, BOS, EOS)

# 快速查找表
TOKEN_BY_IDX: Dict[int, TokenDef] = {t.idx: t for t in ALL_TOKENS}
TOKEN_BY_NAME: Dict[str, TokenDef] = {t.name: t for t in ALL_TOKENS}

# 特殊索引
PAD_IDX = PAD_TOKEN.idx
BOS_IDX = BOS_TOKEN.idx
EOS_IDX = EOS_TOKEN.idx
STOP_IDX = STOP_TOKEN.idx

# Action idx (0..N_ACTIONS-1) → token idx (在 ALL_TOKENS 中的位置)
ACTION_IDX_TO_TOKEN = [t.idx for t in ACTION_TOKENS]
# Token idx → action idx (反向映射)
TOKEN_IDX_TO_ACTION = {t.idx: i for i, t in enumerate(ACTION_TOKENS)}

# 属性组
FIELD_IDS = {t.idx for t in FIELD_TOKENS}
CONST_IDS = {t.idx for t in CONST_TOKENS}
LEAF_IDS = FIELD_IDS | CONST_IDS
OPERATOR_IDS = {t.idx for t in ALL_TOKENS if t.arity > 0 and t.token_type != "special"}

# 比较操作符组 (token_type="compare") — v2.3 默认禁用
COMPARISON_IDS = {t.idx for t in ALL_TOKENS if t.token_type == "compare"}
# 全局开关: 是否允许比较操作符 (v2.3+ 默认 False)
ALLOW_COMPARISON_OPS = False


# ═══════════════════════════════════════════════════════════════
# 有效性检查
# ═══════════════════════════════════════════════════════════════

def get_valid_actions(stack_depth: int, seq_len: int, max_len: int = 25) -> List[int]:
    """
    返回当前状态下所有合法的 action token 索引列表。

    参数:
        stack_depth: 当前栈深度 (还需填充的槽位数)
        seq_len:     当前序列长度 (不包括 BOS)
        max_len:     最大序列长度

    返回:
        合法 action 的 token index 列表
    """
    if stack_depth < 0:
        return []

    remaining = max_len - seq_len

    # Expression complete: only STOP is valid
    if stack_depth == 0:
        return [STOP_IDX]

    # Must stop? No — could add more tokens
    # Can't stop? Correct — STOP only when stack_depth == 0

    valid = []

    # Maximum stack increase = max arity (3) - 1
    # We need: stack_depth + arity - 1 <= remaining
    # So we can handle at most remaining more slots
    # The worst case is we add operators that increase the stack

    for token in ACTION_TOKENS:
        if token.idx == STOP_IDX:
            continue  # STOP only valid when stack_depth == 0

        # After adding this token: new_stack = stack_depth - 1 + token.arity
        new_stack = stack_depth - 1 + token.arity

        # Can we fill the new stack within remaining steps?
        # In the worst case, every subsequent token has arity 0 (consumes 1 slot)
        # So we need: new_stack <= remaining - 1 (one step for each remaining slot)

        # More precise: after this step, we have (remaining - 1) steps left
        # The fastest way to reduce stack is with arity=0 tokens:
        #   each reduces stack by 1
        # The slowest way is with max-arity tokens:
        #   each changes stack by (max_arity - 1)
        # For guaranteed completion, worst case: all remaining are arity=0
        #   new_stack <= remaining - 1  →  stack_depth - 1 + arity <= remaining - 1
        #                                →  stack_depth + arity <= remaining + 1
        if new_stack > remaining:
            continue  # Not enough steps to fill slots

        # Additional constraint: first token (seq_len=0) must not be a constant
        if seq_len == 0 and token.token_type == "const":
            continue

        # Additional constraint: must have at least one field reference
        # (enforced later during formula validation)

        # Additional constraint: avoid empty expression
        if seq_len == 0 and token.token_type == "special":
            continue

        # v2.3: filter comparison operators when disabled
        if not ALLOW_COMPARISON_OPS and token.token_type == "compare":
            continue

        valid.append(token.idx)

    return valid


def get_valid_action_mask(
    stack_depth: int, seq_len: int, max_len: int = 25
) -> List[bool]:
    """
    返回 action 空间的布尔掩码，True=合法。

    返回:
        bool list of length N_ACTIONS
    """
    valid_ids = set(get_valid_actions(stack_depth, seq_len, max_len))
    return [t.idx in valid_ids for t in ACTION_TOKENS]


def get_valid_actions_as_tensor(
    stack_depth: int, seq_len: int, max_len: int = 25
):
    """
    返回 action 掩码的 PyTorch tensor 形式。

    返回:
        torch.BoolTensor of shape (N_ACTIONS,)
    """
    import torch
    mask = get_valid_action_mask(stack_depth, seq_len, max_len)
    return torch.tensor(mask, dtype=torch.bool)


def update_stack_depth(stack_depth: int, token_idx: int) -> int:
    """
    执行 token 后更新栈深度。

    stack_depth' = stack_depth - 1 + arity
    (消耗当前槽位，创建 arity 个新槽位)
    """
    token = TOKEN_BY_IDX.get(token_idx)
    if token is None:
        return stack_depth
    return stack_depth - 1 + token.arity


# ═══════════════════════════════════════════════════════════════
# 公式重建: 前缀序列 → 公式字符串
# ═══════════════════════════════════════════════════════════════

class FormulaBuilder:
    """
    从前缀 token 序列递归重建公式字符串。

    算法: 递归下降
      - 读取下一个 token
      - 如果是 leaf (arity=0): 返回 token.label
      - 如果是 operator (arity=k):
          递归读取 k 个子节点
          格式化为公式字符串
    """

    def __init__(self):
        self._cursor = 0
        self._tokens = []

    def build(self, token_indices: List[int]) -> str:
        """
        从前缀 token 序列构建公式字符串。

        参数:
            token_indices: 不包括 BOS/EOS 的 token index 列表

        返回:
            公式字符串，如 "Rank(Ts_Mean($close, 10))"
        """
        self._cursor = 0
        self._tokens = token_indices
        return self._parse_expr()

    def _parse_expr(self) -> str:
        """递归解析一个表达式子节点"""
        if self._cursor >= len(self._tokens):
            raise ValueError(f"前缀序列不足: cursor={self._cursor}, len={len(self._tokens)}")

        token_idx = self._tokens[self._cursor]
        token = TOKEN_BY_IDX.get(token_idx)
        if token is None:
            raise ValueError(f"未知 token 索引: {token_idx}")

        self._cursor += 1

        # Leaf: 直接返回
        if token.arity == 0:
            if token.token_type == "field":
                return token.name  # $close not close
            return token.label

        # Operator: 递归读取子节点
        children = []
        for _ in range(token.arity):
            child = self._parse_expr()
            children.append(child)

        # 格式化为公式
        if token.formula_prefix:
            # 函数调用: OpName(arg0, arg1, ...)
            return f"{token.formula_prefix}({', '.join(children)})"
        else:
            # 中缀操作符: (left op right)
            if len(children) == 2:
                return f"({children[0]} {token.label} {children[1]})"
            else:
                # 回退: 前缀表示
                return f"{token.label}({', '.join(children)})"


def sequence_to_formula(token_indices: List[int]) -> str:
    """从前缀 token 序列构建公式字符串 (便捷函数)"""
    builder = FormulaBuilder()
    return builder.build(token_indices)


# ═══════════════════════════════════════════════════════════════
# 公式解析: 公式字符串 → 前缀序列 (用于反序列化)
# ═══════════════════════════════════════════════════════════════

def _tokenize_formula(formula: str) -> List[Tuple[str, str]]:
    """
    将公式字符串分词。

    返回: [(type, value), ...]
      type: "field", "number", "name", "lparen", "rparen", "comma", "op"
    """
    tokens = []

    # 简单的基于正则的 tokenizer
    spec = [
        ("FIELD",   r"\$(?:open|high|low|close|volume|vwap|returns|roe|eps|bps|gross_margin)"),
        ("NUMBER",  r"\d+\.?\d*"),
        ("NAME",    r"[A-Za-z_][A-Za-z0-9_]*"),
        ("LPAREN",  r"\("),
        ("RPAREN",  r"\)"),
        ("COMMA",   r","),
        ("OP",      r"[+\-*/<>]=|!=|[<>=&|+\-*/]"),
        ("SKIP",    r"\s+"),
    ]

    pattern = "|".join(f"(?P<{name}>{regex})" for name, regex in spec)
    for m in re.finditer(pattern, formula):
        kind = m.lastgroup
        if kind == "SKIP":
            continue
        tokens.append((kind, m.group()))

    return tokens


def formula_to_sequence(formula: str) -> Optional[List[int]]:
    """
    将公式字符串转换为前缀 token 序列。

    使用简化的递归下降解析器。

    返回:
        token index 列表 (不包括 BOS/EOS)，或 None (解析失败)
    """

    class _Parser:
        def __init__(self, tokens):
            self.tokens = tokens
            self.pos = 0

        def peek(self):
            if self.pos < len(self.tokens):
                return self.tokens[self.pos]
            return None

        def consume(self):
            tok = self.peek()
            if tok:
                self.pos += 1
            return tok

        def expect(self, kind, value=None):
            tok = self.peek()
            if not tok:
                raise SyntaxError(f"Expected {kind}, got EOF")
            if tok[0] != kind:
                raise SyntaxError(f"Expected {kind}, got {tok[0]}({tok[1]})")
            if value is not None and tok[1] != value:
                raise SyntaxError(f"Expected '{value}', got '{tok[1]}'")
            return self.consume()

        def parse(self) -> List[int]:
            return self._parse_expr()

        def _parse_expr(self) -> List[int]:
            tok = self.peek()
            if not tok:
                raise SyntaxError("Unexpected EOF")

            if tok[0] == "FIELD":
                self.consume()
                field_name = tok[1]
                t = TOKEN_BY_NAME.get(field_name)
                if t is None:
                    raise SyntaxError(f"Unknown field: {field_name}")
                return [t.idx]

            if tok[0] == "NUMBER":
                self.consume()
                # Parse number value
                value = tok[1]
                # Check if it matches any const token
                t = TOKEN_BY_NAME.get(value)
                if t and t.token_type == "const":
                    return [t.idx]
                # Treat unknown numbers as the closest const token
                # Fallback: use the number as-is (might not be in vocab)
                # For MVP, just use the raw number index
                raise SyntaxError(f"Number {value} not in vocabulary")

            if tok[0] == "LPAREN":
                self.consume()  # (
                inner = self._parse_expr()
                # Check for infix op
                peeked = self.peek()
                if peeked and peeked[0] == "OP":
                    op_tok = self.consume()
                    op_name = op_tok[1]
                    right = self._parse_expr()
                    self.expect("RPAREN")
                    t = TOKEN_BY_NAME.get(op_name)
                    if t and t.token_type in ("arith", "compare", "logic"):
                        return [t.idx] + inner + right
                    raise SyntaxError(f"Unexpected infix operator: {op_name}")
                self.expect("RPAREN")
                return inner

            if tok[0] == "NAME":
                name = tok[1]
                # Check if it's a known function
                t = TOKEN_BY_NAME.get(name)
                if t and t.arity > 0:
                    self.consume()
                    self.expect("LPAREN")
                    children = []
                    children.extend(self._parse_expr())
                    while self.peek() and self.peek()[0] == "COMMA":
                        self.consume()
                        children.extend(self._parse_expr())
                    self.expect("RPAREN")

                    if len(children) != t.arity:
                        raise SyntaxError(
                            f"{name} expects {t.arity} children, got {len(children)}"
                        )
                    return [t.idx] + children

                raise SyntaxError(f"Unknown function: {name}")

            if tok[0] == "OP":
                # Unary minus
                if tok[1] == "-":
                    self.consume()
                    operand = self._parse_expr()
                    # Treat as: 0 - operand
                    zero = TOKEN_BY_NAME.get("0")
                    minus = TOKEN_BY_NAME.get("-")
                    if zero and minus:
                        return [minus.idx] + (zero_idx_sequence() if zero else []) + operand
                    raise SyntaxError("Cannot handle unary minus without '0' token")

            raise SyntaxError(f"Unexpected token: {tok}")

    try:
        tokens = _tokenize_formula(formula)
        parser = _Parser(tokens)
        return parser.parse()
    except (SyntaxError, ValueError) as e:
        return None


def zero_idx_sequence() -> List[int]:
    """返回 '0' token 的前缀序列 (用于一元负号展开)"""
    # 0 is not in our const vocabulary; use 5 as a small number
    t = TOKEN_BY_NAME.get("5")
    return [t.idx] if t else []


# ═══════════════════════════════════════════════════════════════
# 序列有效性验证
# ═══════════════════════════════════════════════════════════════

def validate_sequence(token_indices: List[int]) -> Tuple[bool, str]:
    """
    验证前缀 token 序列是否是有效的完整表达式。

    返回:
        (is_valid, error_message)
    """
    if not token_indices:
        return False, "空序列"

    stack = 0

    # Simulate BOS
    stack += BOS_TOKEN.arity  # stack = 1

    for i, tidx in enumerate(token_indices):
        token = TOKEN_BY_IDX.get(tidx)
        if token is None:
            return False, f"位置 {i}: 未知 token {tidx}"

        if stack <= 0:
            return False, f"位置 {i}: 栈为空，表达式已经完整"

        stack = stack - 1 + token.arity

    if stack > 0:
        return False, f"序列结束时有 {stack} 个未填充的槽位"

    # At least one field reference
    if not any(t in FIELD_IDS for t in token_indices):
        return False, "缺少字段引用"

    # At least one operator (prevent bare data fields like $vwap)
    if not any(t in OPERATOR_IDS for t in token_indices):
        return False, "缺少操作符"

    return True, ""


def validate_and_build(token_indices: List[int]) -> Tuple[bool, str]:
    """
    验证序列并构建公式字符串。一站式的序列→公式转换。

    返回:
        (is_valid, formula_or_error_message)
    """
    valid, err = validate_sequence(token_indices)
    if not valid:
        return False, err

    try:
        formula = sequence_to_formula(token_indices)
        return True, formula
    except Exception as e:
        return False, f"公式构建失败: {e}"


# ═══════════════════════════════════════════════════════════════
# 调试辅助
# ═══════════════════════════════════════════════════════════════

def token_sequence_to_names(indices: List[int]) -> List[str]:
    """Token 索引列表 → token 名称列表"""
    return [TOKEN_BY_IDX.get(i, TokenDef(i, f"?{i}", "?", 0, "unknown")).name
            for i in indices]


def print_token_summary():
    """打印词汇表摘要"""
    print(f"Vocabulary: {VOCAB_SIZE} tokens ({N_ACTIONS} actions)")
    print(f"  Fields:       {len(FIELD_TOKENS)}: {[t.name for t in FIELD_TOKENS]}")
    print(f"  Constants:    {len(CONST_TOKENS)}: {[t.name for t in CONST_TOKENS]}")
    print(f"  Unary Ops:    {len(UNARY_OP_TOKENS)}: {[t.name for t in UNARY_OP_TOKENS]}")
    print(f"  Param Ops:    {len(PARAM_OP_TOKENS)}: {[t.name for t in PARAM_OP_TOKENS]}")
    print(f"  Binary Vals:  {len(BINARY_VAL_TOKENS)}: {[t.name for t in BINARY_VAL_TOKENS]}")
    print(f"  Ternary Ops:  {len(TERNARY_OP_TOKENS)}: {[t.name for t in TERNARY_OP_TOKENS]}")
    print(f"  Infix Ops:    {len(INFIX_OP_TOKENS)}: {[t.name for t in INFIX_OP_TOKENS]}")
    print(f"  Special:      STOP={STOP_IDX}, PAD={PAD_IDX}, BOS={BOS_IDX}, EOS={EOS_IDX}")