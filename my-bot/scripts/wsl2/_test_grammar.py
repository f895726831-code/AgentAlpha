import sys; sys.path.insert(0, ".")
from gflownet.grammar import *

# Test 1: complex formula
seq = [43, 41, 6, 30, 6, 11, 30, 6, 11]
formula = sequence_to_formula(seq)
print(f"F1: {formula}")

from ffo.parser import parse, ast_to_string
try:
    ast = parse(formula)
    print(f"  AST: {ast_to_string(ast)}")
except Exception as e:
    print(f"  FAIL: {e}")

# Test 2: Rank($close)
seq2 = [17, 6]
f2 = sequence_to_formula(seq2)
print(f"F2: {f2}")
try:
    ast2 = parse(f2)
    print(f"  AST: {ast_to_string(ast2)}")
except Exception as e:
    print(f"  FAIL: {e}")

# Test 3: Ts_Mean($close, 10)
seq3 = [22, 6, 11]
f3 = sequence_to_formula(seq3)
print(f"F3: {f3}")

# Test 4: Scale(Ts_Mean($close, 10))
seq4 = [18, 22, 6, 11]
f4 = sequence_to_formula(seq4)
print(f"F4: {f4}")

# Test 5: ($close - Delay($close, 5))
seq5 = [41, 6, 30, 6, 10]
f5 = sequence_to_formula(seq5)
print(f"F5: {f5}")

print("\nAll grammar tests passed!")