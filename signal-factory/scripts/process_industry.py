"""
Process all Wind MCP batch results and save industry classification CSV.
This script expects batch JSON files in data/industry/batches/.
"""

import json
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

INSTRUMENTS_PATH = Path("D:/qlib/qlib_data/cn_data/instruments/csi500.txt")
OUTPUT_DIR = Path("signal-factory/data/industry")
BATCH_DIR = OUTPUT_DIR / "batches"
OUTPUT_PATH = OUTPUT_DIR / "sw_industry.csv"


def qlib_to_wind(symbol: str) -> str:
    if symbol.startswith("sh"):
        return symbol[2:] + ".SH"
    elif symbol.startswith("sz"):
        return symbol[2:] + ".SZ"
    return symbol


def main():
    # Read CSI 500 instruments
    symbols = []
    with open(INSTRUMENTS_PATH, "r", encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split("\t")
            if parts:
                symbols.append(parts[0])

    print(f"CSI 500 instruments: {len(symbols)}")

    # Load batch results
    industry_map = {}
    if BATCH_DIR.exists():
        for batch_file in sorted(BATCH_DIR.glob("batch_*.json")):
            with open(batch_file, "r", encoding="utf-8") as f:
                batch_data = json.load(f)
            for row in batch_data:
                industry_map[row[0]] = {"wind_code": row[0], "stock_name": row[1], "industry": row[2]}
            print(f"  Loaded {batch_file.name}: {len(batch_data)} stocks")

    print(f"Total industry map: {len(industry_map)}")

    # Save CSV
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write("qlib_symbol,wind_code,stock_name,industry\n")
        matched = 0
        for s in symbols:
            wc = qlib_to_wind(s)
            info = industry_map.get(wc, {})
            ind = info.get("industry", "")
            name = info.get("stock_name", "")
            if ind:
                matched += 1
            f.write(f"{s},{wc},{name},{ind}\n")

    print(f"Matched: {matched}/{len(symbols)}")
    print(f"Saved: {OUTPUT_PATH}")

    # Industry distribution
    industry_counts = {}
    for info in industry_map.values():
        ind = info.get("industry", "")
        if ind:
            industry_counts[ind] = industry_counts.get(ind, 0) + 1

    print(f"\nIndustry distribution ({len(industry_counts)} industries):")
    for ind, cnt in sorted(industry_counts.items(), key=lambda x: -x[1]):
        print(f"  {ind}: {cnt}")


if __name__ == "__main__":
    main()