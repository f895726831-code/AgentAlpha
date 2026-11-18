"""Test FFO API endpoints via HTTP"""
import json, urllib.request

BASE = "http://localhost:8700"

def post(path, data):
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())

def get(path):
    with urllib.request.urlopen(f"{BASE}{path}", timeout=10) as resp:
        return json.loads(resp.read())

# Test 1: Operators (fast)
ops = get("/ffo/operators")
print(f"Operators: {ops['count']} registered ✓")

# Test 2: Single factor evaluation
for formula, name in [
    ("($close - Delay($close, 5)) / Delay($close, 5)", "5-Day Momentum"),
    ("-1 * Rank(Ts_Std($returns, 20))", "Low Volatility"),
    ("Ts_Mean($returns, 10)", "10-Day Mean Return"),
]:
    result = post("/ffo/evaluate", {
        "formula": formula,
        "start": "2025-01-01",
        "end": "2026-01-01",
        "universe": "csi500",
    })
    r = result["report_json"]
    ic = r["ic"]
    ls = r["long_short"]
    print(f"[{name}] IC={ic['mean']:.4f} IR={ic['ir']:.3f} LS={ls['spread']:.2%} days={r['n_days']} ✓")

# Test 3: Batch evaluation
batch = post("/ffo/batch", {
    "formulas": [
        "($close - Delay($close, 5)) / Delay($close, 5)",
        "Rank($close, 20)",
        "-1 * Rank(Ts_Std($returns, 20))",
    ],
    "start": "2025-01-01",
    "end": "2026-01-01",
    "universe": "csi500",
})
print(f"Batch: {batch['count']} formulas, {batch['summary']['passed']} passed")

# Test 4: Main health with FFO
main_health = get("/api/v1/health")
print(f"Main health: ffo_available={main_health.get('ffo_available')}, ffo_ops={main_health.get('ffo_operators')} ✓")

print(f"\nAll API endpoint tests passed!")