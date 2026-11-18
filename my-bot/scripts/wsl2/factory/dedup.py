"""
AST 语义因子去重 — 字符串规范化 + AST 结构等价检测

两层策略:
  Layer 1 (字符串规范化): 移除空白 → 小写 → 归一化数字
  Layer 2 (AST 结构等价): 递归比较 AST 节点, 处理交换律/结合律/恒等变换

用法:
    from factory.dedup import FactorDeduplicator

    dedup = FactorDeduplicator()
    unique = dedup.deduplicate([("Rank($close)", 0.04, "agent"),
                                 ("rank( $close )", 0.03, "gflownet")])
    # → [("Rank($close)", 0.04, "agent")]  # 保留 |IC| 最大的
"""

import re
from typing import Any, Dict, List, Optional, Set, Tuple


# ═══════════════════════════════════════════════════════════
# 字符串规范化
# ═══════════════════════════════════════════════════════════

def normalize_formula(formula: str) -> str:
    """
    规范化因子公式字符串。

    规则:
      1. 移除所有空白
      2. 函数名小写 (Rank, Ts_Mean, If, ...)
      3. 归一化数字格式 (5.0 → 5, 0.001 → 0.001)
      4. 字段名小写 ($Close → $close)
      5. 移除多余的括号 (Rank((x)) → Rank(x))

    不会改变语义等价性。
    """
    s = formula.strip()

    # 移除空白
    s = re.sub(r'\s+', '', s)

    # 字段名小写
    s = re.sub(r'\$(\w+)', lambda m: '$' + m.group(1).lower(), s)

    # 函数名小写 (匹配后跟括号的标识符)
    s = re.sub(r'([A-Za-z_][A-Za-z0-9_]*)(\()',
               lambda m: m.group(1).lower() + m.group(2), s)

    # 归一化数字: 去掉整数末尾的 .0
    s = re.sub(r'(?<!\d)(\d+)\.0(?!\d)', r'\1', s)

    # 移除冗余双层括号: ((x)) → (x) (递归)
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r'\(\(([^()]*)\)\)', r'(\1)', s)

    return s


# ═══════════════════════════════════════════════════════════
# AST 结构比较
# ═══════════════════════════════════════════════════════════

def _get_ast(formula: str) -> Optional[Any]:
    """安全获取 AST, 解析失败返回 None"""
    try:
        from ffo.parser import parse as ffo_parse
        return ffo_parse(formula)
    except Exception:
        return None


def _flatten_associative(node: Any, op: str) -> List[Any]:
    """
    展平嵌套的交换/结合操作符。

    例如: BinOp(BinOp(a, +, b), +, c) → [a, b, c]
    """
    from ffo.parser import BinOp

    if isinstance(node, BinOp) and node.op == op:
        return (_flatten_associative(node.left, op)
                + _flatten_associative(node.right, op))
    return [node]


def _ast_equals(node1: Any, node2: Any) -> bool:
    """
    递归比较两棵 AST 是否结构等价。

    处理的等价性:
      - 交换律: a+b ↔ b+a, a*b ↔ b*a
      - 节点类型必须一致
      - 子节点递归比较
    """
    from ffo.parser import FieldRef, Number, BinOp, UnaryOp, FuncCall

    # 类型必须一致
    if type(node1) is not type(node2):
        return False

    # FieldRef: 比较字段名
    if isinstance(node1, FieldRef):
        return node1.name.lower() == node2.name.lower()

    # Number: 比较数值
    if isinstance(node1, Number):
        return abs(node1.value - node2.value) < 1e-10

    # UnaryOp: 比较操作符 + 操作数
    if isinstance(node1, UnaryOp):
        return (node1.op == node2.op
                and _ast_equals(node1.operand, node2.operand))

    # BinOp: 比较操作符 + 左右子节点
    if isinstance(node1, BinOp):
        if node1.op != node2.op:
            return False

        # 交换律 + 结合律: + 和 * 的展平比较
        if node1.op in ('+', '*'):
            # 展平嵌套的同操作符节点
            flat1 = _flatten_associative(node1, node1.op)
            flat2 = _flatten_associative(node2, node2.op)

            if len(flat1) != len(flat2):
                return False

            # 尝试一对一匹配 (每个 flat1 节点匹配一个未使用的 flat2 节点)
            used = [False] * len(flat2)
            for item1 in flat1:
                matched = False
                for j, item2 in enumerate(flat2):
                    if not used[j] and _ast_equals(item1, item2):
                        used[j] = True
                        matched = True
                        break
                if not matched:
                    return False
            return True

        # 非交换操作符: 严格顺序
        return (_ast_equals(node1.left, node2.left)
                and _ast_equals(node1.right, node2.right))

    # FuncCall: 比较函数名 + 所有参数
    if isinstance(node1, FuncCall):
        if node1.name.lower() != node2.name.lower():
            return False
        if len(node1.args) != len(node2.args):
            return False
        return all(_ast_equals(a1, a2)
                   for a1, a2 in zip(node1.args, node2.args))

    return False


def _ast_hash(node: Any) -> int:
    """
    计算 AST 的结构哈希 (用于快速分组)。

    交换律处理: + 和 * 的子节点排序后再哈希 (消除顺序影响)。
    """
    from ffo.parser import FieldRef, Number, BinOp, UnaryOp, FuncCall

    if isinstance(node, FieldRef):
        return hash(('field', node.name.lower()))

    if isinstance(node, Number):
        return hash(('num', round(node.value, 10)))

    if isinstance(node, UnaryOp):
        return hash(('unary', node.op, _ast_hash(node.operand)))

    if isinstance(node, BinOp):
        left_h = _ast_hash(node.left)
        right_h = _ast_hash(node.right)
        # 交换律: + 和 * 的子节点排序
        if node.op in ('+', '*'):
            key = tuple(sorted([left_h, right_h]))
        else:
            key = (left_h, right_h)
        return hash(('binop', node.op, key))

    if isinstance(node, FuncCall):
        args_hash = tuple(_ast_hash(a) for a in node.args)
        return hash(('func', node.name.lower(), args_hash))

    return hash(str(type(node)))


def canonical_formula(formula: str) -> str:
    """
    生成因子的规范形式 — 交换律操作符的操作数按字母排序。

    目的: 使 a+b 和 b+a 归一化到相同的字符串, 用于快速分组。
    """
    try:
        from ffo.parser import parse as ffo_parse
        ast = ffo_parse(formula)
        return _canonical_ast_string(ast)
    except Exception:
        return normalize_formula(formula)


def _canonical_ast_string(node: Any) -> str:
    """递归生成 AST 的规范字符串表示"""
    from ffo.parser import FieldRef, Number, BinOp, UnaryOp, FuncCall

    if isinstance(node, FieldRef):
        return f"${node.name.lower()}"

    if isinstance(node, Number):
        v = node.value
        if v == int(v):
            return str(int(v))
        return f"{v:.6g}"

    if isinstance(node, UnaryOp):
        return f"(-{_canonical_ast_string(node.operand)})"

    if isinstance(node, BinOp):
        left = _canonical_ast_string(node.left)
        right = _canonical_ast_string(node.right)
        # 交换律: 排序左/右操作数
        if node.op in ('+', '*'):
            parts = sorted([left, right])
            return f"({parts[0]}{node.op}{parts[1]})"
        else:
            return f"({left}{node.op}{right})"

    if isinstance(node, FuncCall):
        name = node.name.lower()
        args = ", ".join(_canonical_ast_string(a) for a in node.args)
        return f"{name}({args})"

    return str(node)


# ═══════════════════════════════════════════════════════════
# L2 结构去重 — 参数分桶
# ═══════════════════════════════════════════════════════════

# 参数桶: 同结构 + 同参数桶 → 相同结构指纹
_PARAM_BUCKETS = [
    (2, 5), (10, 20), (30, 60), (120, 250)
]


def _bucketize(value: float) -> str:
    """将数值参数映射到桶标签"""
    for lo, hi in _PARAM_BUCKETS:
        if lo <= value <= hi:
            return f"b{lo}_{hi}"
    return f"v{int(value)}"


def structural_fingerprint(formula: str) -> str:
    """
    生成因子的结构指纹 — 参数分桶后规范化。

    v4 新增: 解决 SignedPower($returns, 20) 和 SignedPower($returns, 10)
    不同的 AST hash 但结构相同的问题。

    例:
        SignedPower($returns, 20) → "SignedPower|$returns|b10_20"
        SignedPower($returns, 10) → "SignedPower|$returns|b10_20"  (相同!)
        Ts_Mean($close, 60)      → "Ts_Mean|$close|b30_60"
        Rank($volume)            → "Rank|$volume"

    返回:
        结构指纹字符串，解析失败返回空字符串。
    """
    try:
        from ffo.parser import parse as ffo_parse
        ast = ffo_parse(formula)
        return _ast_to_fingerprint(ast)
    except Exception:
        return ""


def _ast_to_fingerprint(node) -> str:
    """递归将 AST 转换为结构指纹 (参数分桶)"""
    from ffo.parser import FieldRef, Number, BinOp, UnaryOp, FuncCall

    if isinstance(node, FieldRef):
        return f"${node.name.lower()}"

    if isinstance(node, Number):
        return _bucketize(node.value)

    if isinstance(node, UnaryOp):
        return f"(neg {_ast_to_fingerprint(node.operand)})"

    if isinstance(node, BinOp):
        left = _ast_to_fingerprint(node.left)
        right = _ast_to_fingerprint(node.right)
        # 交换律: 排序子节点
        if node.op in ('+', '*'):
            return f"({min(left, right)} {node.op} {max(left, right)})"
        return f"({left} {node.op} {right})"

    if isinstance(node, FuncCall):
        name = node.name.lower()
        args = " ".join(_ast_to_fingerprint(a) for a in node.args)
        return f"{name}|{args}"

    return str(node)


# ═══════════════════════════════════════════════════════════
# 去重器
# ═══════════════════════════════════════════════════════════

class FactorDeduplicator:
    """
    因子去重器 — 两层策略。

    用法:
        dedup = FactorDeduplicator()
        result = dedup.deduplicate(pool)

    pool 格式: [(formula, ic, channel), ...]
    返回格式: [(formula, ic, channel), ...] — 去重后, 保留 |IC| 最大
    """

    def __init__(self):
        self._parse_cache: Dict[str, Optional[Any]] = {}

    # ── 公共 API ──

    def normalize(self, formula: str) -> str:
        """字符串规范化 (公开方法)"""
        return normalize_formula(formula)

    def are_equivalent(self, f1: str, f2: str) -> bool:
        """
        检测两个公式是否语义等价。

        步骤:
          1. 规范化字符串比较
          2. AST 结构比较
        """
        # 快速路径: 规范化后相同
        n1 = normalize_formula(f1)
        n2 = normalize_formula(f2)
        if n1 == n2:
            return True

        # AST 比较
        ast1 = self._get_cached_ast(f1)
        ast2 = self._get_cached_ast(f2)

        if ast1 is None or ast2 is None:
            return False

        return _ast_equals(ast1, ast2)

    def deduplicate(
        self,
        pool: List[Tuple[str, float, str]],
        mode: str = "smart",
    ) -> List[Tuple[str, float, str]]:
        """
        去重因子池。

        参数:
            pool: [(formula, ic, channel), ...]
            mode: "simple" (仅字符串), "smart" (字符串 + AST), "strict" (仅 AST)

        返回:
            去重后的因子列表, 按 |IC| 降序排列
        """
        if mode == "simple":
            return self._dedup_simple(pool)
        elif mode == "strict":
            return self._dedup_strict(pool)
        else:
            return self._dedup_smart(pool)

    def deduplicate_pairs(
        self,
        pool: List[Tuple[str, float]],
    ) -> List[Tuple[str, float]]:
        """
        简化接口: 去重 (formula, ic) 对的列表 (无 channel 标签)。
        """
        tagged = [(f, ic, "") for f, ic in pool]
        result = self.deduplicate(tagged, mode="smart")
        return [(f, ic) for f, ic, _ in result]

    # ── 内部方法 ──

    def _get_cached_ast(self, formula: str) -> Optional[Any]:
        """获取缓存的 AST"""
        if formula not in self._parse_cache:
            self._parse_cache[formula] = _get_ast(formula)
        return self._parse_cache[formula]

    def _dedup_simple(
        self,
        pool: List[Tuple[str, float, str]],
    ) -> List[Tuple[str, float, str]]:
        """仅基于字符串规范化的去重"""
        groups: Dict[str, List[Tuple[str, float, str]]] = {}
        for formula, ic, channel in pool:
            key = normalize_formula(formula)
            if key not in groups:
                groups[key] = []
            groups[key].append((formula, ic, channel))

        result = []
        for items in groups.values():
            # 保留 |IC| 最大的
            best = max(items, key=lambda x: abs(x[1]))
            result.append(best)

        result.sort(key=lambda x: -abs(x[1]))
        return result

    def _dedup_strict(
        self,
        pool: List[Tuple[str, float, str]],
    ) -> List[Tuple[str, float, str]]:
        """仅基于 AST 等价性的去重 (O(n²))"""
        if len(pool) <= 1:
            return pool

        # 分组: 每个公式属于哪个等价类
        n = len(pool)
        parent = list(range(n))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(i, j):
            root_i, root_j = find(i), find(j)
            if root_i != root_j:
                parent[root_j] = root_i

        for i in range(n):
            for j in range(i + 1, n):
                if self.are_equivalent(pool[i][0], pool[j][0]):
                    union(i, j)

        # 每组保留 |IC| 最大的
        groups: Dict[int, List[int]] = {}
        for i in range(n):
            root = find(i)
            if root not in groups:
                groups[root] = []
            groups[root].append(i)

        result = []
        for indices in groups.values():
            best_idx = max(indices, key=lambda i: abs(pool[i][1]))
            result.append(pool[best_idx])

        result.sort(key=lambda x: -abs(x[1]))
        return result

    def _dedup_smart(
        self,
        pool: List[Tuple[str, float, str]],
    ) -> List[Tuple[str, float, str]]:
        """
        智能去重: 先用 canonical_formula 分组 → 组内 AST 比较
        → AST 哈希跨组合并。
        """
        n = len(pool)
        if n <= 1:
            return pool

        parent = list(range(n))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(i, j):
            root_i, root_j = find(i), find(j)
            if root_i != root_j:
                parent[root_j] = root_i

        # Step 1: canonical formula 分组 (处理交换律)
        str_groups: Dict[str, List[int]] = {}
        ast_hashes: Dict[int, str] = {}  # index → AST hash key

        for i, (formula, _, _) in enumerate(pool):
            key = canonical_formula(formula)
            if key not in str_groups:
                str_groups[key] = []
            str_groups[key].append(i)

            # 预计算 AST 哈希 (用于跨组检测)
            ast = self._get_cached_ast(formula)
            if ast is not None:
                h = _ast_hash(ast)
                ast_hashes[i] = f"ast:{h}"

        # Step 2: 组内 AST 比较
        for indices in str_groups.values():
            for a in range(len(indices)):
                for b in range(a + 1, len(indices)):
                    i, j = indices[a], indices[b]
                    if self.are_equivalent(pool[i][0], pool[j][0]):
                        union(i, j)

        # Step 3: AST 哈希跨组检测 (处理 canonical_formula 无法归一的结合律等)
        # 相同 AST 哈希的因子合并
        hash_groups: Dict[str, List[int]] = {}
        for i, h in ast_hashes.items():
            if h not in hash_groups:
                hash_groups[h] = []
            hash_groups[h].append(i)

        for indices in hash_groups.values():
            for a in range(len(indices)):
                for b in range(a + 1, len(indices)):
                    union(indices[a], indices[b])

        # Step 4: 每组保留 |IC| 最大的
        groups: Dict[int, List[int]] = {}
        for i in range(n):
            root = find(i)
            if root not in groups:
                groups[root] = []
            groups[root].append(i)

        result = []
        for indices in groups.values():
            best_idx = max(indices, key=lambda i: abs(pool[i][1]))
            result.append(pool[best_idx])

        result.sort(key=lambda x: -abs(x[1]))
        return result


# ═══════════════════════════════════════════════════════════
# 便捷函数
# ═══════════════════════════════════════════════════════════

# 模块级单例
_default_dedup: Optional[FactorDeduplicator] = None


def get_deduplicator() -> FactorDeduplicator:
    """获取全局去重器实例"""
    global _default_dedup
    if _default_dedup is None:
        _default_dedup = FactorDeduplicator()
    return _default_dedup


def are_equivalent(f1: str, f2: str) -> bool:
    """便捷函数: 两个公式是否语义等价"""
    return get_deduplicator().are_equivalent(f1, f2)


def deduplicate_pool(
    pool: List[Tuple[str, float, str]],
    mode: str = "smart",
) -> List[Tuple[str, float, str]]:
    """便捷函数: 去重因子池"""
    return get_deduplicator().deduplicate(pool, mode=mode)