import sys, json, urllib.request

payload = json.dumps({
    "formula": "($close - Delay($close, 5)) / Delay($close, 5)",
    "start": "2025-01-01",
    "end": "2026-01-01",
    "universe": "csi500",
}).encode()

req = urllib.request.Request(
    "http://localhost:8700/ffo/evaluate",
    data=payload,
    headers={"Content-Type": "application/json"},
)

with urllib.request.urlopen(req, timeout=60) as resp:
    d = json.loads(resp.read())

if "report_json" in d:
    r = d["report_json"]
    ic = r["ic"]
    ls = r["long_short"]
    print("OK: IC={:.4f} IR={:.3f} LS={:.2%} days={} err={}".format(
        ic["mean"], ic["ir"], ls["spread"], r["n_days"], r.get("error", "none")))
else:
    print("UNEXPECTED:", json.dumps(d, indent=2)[:300])