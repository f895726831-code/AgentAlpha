"""Test that injected Phase 1 factors are readable by Qlib."""
import sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Directly test reading a p1_ .day.bin file
FEATURES_DIR = Path("D:/qlib/qlib_data/cn_data/features")
CALENDAR = Path("D:/qlib/qlib_data/cn_data/calendars/day.txt")

with open(CALENDAR) as f:
    dates = [line.strip() for line in f if line.strip()]
n_days = len(dates)

# Pick a CSI500 stock
stock = "sh600004"

# List p1_ files for this stock
p1_files = sorted((FEATURES_DIR / stock).glob("p1_*.day.bin"))
p1_names = [f.stem for f in p1_files]

print(f"Stock: {stock}")
print(f"Calendar: {n_days} days")
print(f"P1 factors: {len(p1_names)}")
print(f"First 3: {p1_names[:3]}")
print()

# Read one factor
for name in p1_names[:3]:
    fp = FEATURES_DIR / stock / f"{name}.bin"
    print(f"    File: {fp}")
    data = np.fromfile(fp, dtype="<f")
    start_idx = int(data[0])
    values = data[1:]

    # Reconstruct full array
    result = np.full(n_days, np.nan, dtype=np.float32)
    end_idx = min(start_idx + len(values), n_days)
    result[start_idx:end_idx] = values[:end_idx - start_idx]

    valid = (~np.isnan(result)).sum()
    print(f"  {name[:60]}")
    print(f"    start_idx={start_idx}, valid_days={valid}/{n_days}")
    print(f"    first_valid_date={dates[start_idx]}, value={result[start_idx]:.6f}")
    print(f"    last_valid_date={dates[end_idx-1]}, value={result[end_idx-1]:.6f}")
    print()

# Now test via Qlib API
print("--- Testing via Qlib API ---")
try:
    from ffo.data_loader import load_qlib_data

    # Try loading one p1_ field
    # The Qlib field name is the filename without .bin
    # File: p1__close_....day.bin → field: $p1__close_....day
    field_name = f"${p1_names[0]}"
    print(f"Loading field: {field_name}")

    df = load_qlib_data(
        universe="csi500",
        start="2015-01-05",
        fields=["$close", field_name],
    )
    print(f"Shape: {df.shape}, columns: {list(df.columns)}")

    # Check if p1 field has data
    col_lower = field_name.lstrip("$").lower()
    if col_lower in df.columns:
        n_valid = df[col_lower].notna().sum()
        print(f"Valid values: {n_valid}/{len(df)} ({100*n_valid/len(df):.1f}%)")
        print(f"Sample: {df[col_lower].dropna().head(3)}")
    else:
        print(f"Column not found. Available: {list(df.columns)[:10]}")

except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()