import json

# Reconstruct from original data read at conversation start + apply all updates

# Original active entries (pre-update, from conversation start)
# Then merge with new prices 10-21 through 10-26

original_active = [
    {"signal_id":"2026-10-14-sz301377","signal_date":"2026-10-14","symbol_raw":"sz301377","windcode":"301377.SZ","name":"鼎泰高科","alpha_score":0.090911,"rank":2,"source":"csi500","entry_price":272.54,"entry_price_date":"2026-10-15","daily_prices":[{"date":"2026-10-15","day":0,"close":272.54},{"date":"2026-10-16","day":1,"close":269.6},{"date":"2026-10-19","day":2,"close":261.7},{"date":"2026-10-20","day":3,"close":257.07}],"status":"active","added_at":"2026-10-14T18:00:47.632209"},
    {"signal_id":"2026-10-14-sz002409","signal_date":"2026-10-14","symbol_raw":"sz002409","windcode":"002409.SZ","name":"雅克科技","alpha_score":0.08425,"rank":3,"source":"csi500","entry_price":99.0,"entry_price_date":"2026-10-15","daily_prices":[{"date":"2026-10-15","day":0,"close":99.0},{"date":"2026-10-16","day":1,"close":98.01},{"date":"2026-10-19","day":2,"close":96.11},{"date":"2026-10-20","day":3,"close":97.25}],"status":"active","added_at":"2026-10-14T18:00:47.632209"},
    {"signal_id":"2026-10-14-sz301526","signal_date":"2026-10-14","symbol_raw":"sz301526","windcode":"301526.SZ","name":"国际复材","alpha_score":0.075429,"rank":4,"source":"csi500","entry_price":22.26,"entry_price_date":"2026-10-15","daily_prices":[{"date":"2026-10-15","day":0,"close":22.26},{"date":"2026-10-16","day":1,"close":21.86},{"date":"2026-10-19","day":2,"close":21.18},{"date":"2026-10-20","day":3,"close":21.79}],"status":"active","added_at":"2026-10-14T18:00:47.632209"},
    {"signal_id":"2026-10-14-sz300223","signal_date":"2026-10-14","symbol_raw":"sz300223","windcode":"300223.SZ","name":"君正股份","alpha_score":0.074827,"rank":5,"source":"csi500","entry_price":100.54,"entry_price_date":"2026-10-15","daily_prices":[{"date":"2026-10-15","day":0,"close":100.54},{"date":"2026-10-16","day":1,"close":100.52},{"date":"2026-10-19","day":2,"close":102.46},{"date":"2026-10-20","day":3,"close":103.55}],"status":"active","added_at":"2026-10-14T18:00:47.632209"},
    {"signal_id":"2026-10-15-sz300475","signal_date":"2026-10-15","symbol_raw":"sz300475","windcode":"300475.SZ","name":"香农芯创","alpha_score":0.177448,"rank":3,"source":"csi500","entry_price":111.7,"entry_price_date":"2026-10-16","daily_prices":[{"date":"2026-10-16","day":0,"close":111.7},{"date":"2026-10-19","day":1,"close":114.0},{"date":"2026-10-20","day":2,"close":114.44}],"status":"active","added_at":"2026-10-15T17:56:21.549258"},
    {"signal_id":"2026-10-15-sz301200","signal_date":"2026-10-15","symbol_raw":"sz301200","windcode":"301200.SZ","name":"大族数控","alpha_score":0.149126,"rank":4,"source":"csi500","entry_price":179.74,"entry_price_date":"2026-10-16","daily_prices":[{"date":"2026-10-16","day":0,"close":179.74},{"date":"2026-10-19","day":1,"close":175.23},{"date":"2026-10-20","day":2,"close":173.14}],"status":"active","added_at":"2026-10-15T17:56:21.549258"},
    {"signal_id":"2026-10-15-sz002945","signal_date":"2026-10-15","symbol_raw":"sz002945","windcode":"002945.SZ","name":"华林证券","alpha_score":0.113558,"rank":5,"source":"csi500","entry_price":14.99,"entry_price_date":"2026-10-16","daily_prices":[{"date":"2026-10-16","day":0,"close":14.99},{"date":"2026-10-19","day":1,"close":14.76},{"date":"2026-10-20","day":2,"close":14.55}],"status":"active","added_at":"2026-10-15T17:56:21.549258"},
    {"signal_id":"2026-10-16-sh688629","signal_date":"2026-10-16","symbol_raw":"sh688629","windcode":"688629.SH","name":"华丰科技","alpha_score":0.286381,"rank":2,"source":"csi500","entry_price":77.99,"entry_price_date":"2026-10-16","daily_prices":[{"date":"2026-10-16","day":0,"close":77.99},{"date":"2026-10-19","day":1,"close":77.7},{"date":"2026-10-20","day":2,"close":80.78}],"status":"active","added_at":"2026-10-16T16:42:35.840253"},
    {"signal_id":"2026-10-16-sh688347","signal_date":"2026-10-16","symbol_raw":"sh688347","windcode":"688347.SH","name":"华虹宏力","alpha_score":0.246852,"rank":3,"source":"csi500","entry_price":169.89,"entry_price_date":"2026-10-16","daily_prices":[{"date":"2026-10-16","day":0,"close":169.89},{"date":"2026-10-19","day":1,"close":166.63},{"date":"2026-10-20","day":2,"close":170.39}],"status":"active","added_at":"2026-10-16T16:42:35.840253"},
    {"signal_id":"2026-10-16-sh688172","signal_date":"2026-10-16","symbol_raw":"sh688172","windcode":"688172.SH","name":"燕东微","alpha_score":0.219861,"rank":5,"source":"csi500","entry_price":35.28,"entry_price_date":"2026-10-16","daily_prices":[{"date":"2026-10-16","day":0,"close":35.28},{"date":"2026-10-19","day":1,"close":34.47},{"date":"2026-10-20","day":2,"close":35.41}],"status":"active","added_at":"2026-10-16T16:42:35.840253"},
    {"signal_id":"2026-10-20-sz001309","signal_date":"2026-10-20","symbol_raw":"sz001309","windcode":"001309.SZ","name":"","alpha_score":0.25228,"rank":1,"source":"csi500","entry_price":None,"entry_price_date":None,"daily_prices":[],"status":"active","added_at":"2026-10-20T18:13:16.185377"},
    {"signal_id":"2026-10-20-sz300567","signal_date":"2026-10-20","symbol_raw":"sz300567","windcode":"300567.SZ","name":"","alpha_score":0.172148,"rank":4,"source":"csi500","entry_price":None,"entry_price_date":None,"daily_prices":[],"status":"active","added_at":"2026-10-20T18:13:16.185377"},
    {"symbol":"002966.SZ","windcode":"002966.SZ","name":"002966.SZ","source":"timi","strategy_type":"T2_pullback","confidence":0.7,"trigger_price":9.08,"stop_loss":8.8275,"take_profit":[9.988],"edge":3.596,"position_pct":5.31,"added_date":"2026-10-21","alpha_score":0.7,"entry_price":9.08,"status":"active"},
    {"signal_id":"2026-10-21-sz300115","signal_date":"2026-10-21","symbol_raw":"sz300115","windcode":"300115.SZ","name":"","alpha_score":0.316978,"rank":2,"source":"csi500","entry_price":None,"entry_price_date":None,"daily_prices":[],"status":"active","added_at":"2026-10-21T17:12:56.810296"},
    {"signal_id":"2026-10-21-sh688702","signal_date":"2026-10-21","symbol_raw":"sh688702","windcode":"688702.SH","name":"","alpha_score":0.274671,"rank":3,"source":"csi500","entry_price":None,"entry_price_date":None,"daily_prices":[],"status":"active","added_at":"2026-10-21T17:12:56.810296"},
    {"signal_id":"2026-10-21-sh600363","signal_date":"2026-10-21","symbol_raw":"sh600363","windcode":"600363.SH","name":"","alpha_score":0.211303,"rank":5,"source":"csi500","entry_price":None,"entry_price_date":None,"daily_prices":[],"status":"active","added_at":"2026-10-21T17:12:56.810296"},
    {"signal_id":"2026-10-22-sz002008","signal_date":"2026-10-22","symbol_raw":"sz002008","windcode":"002008.SZ","name":"","alpha_score":0.253323,"rank":3,"source":"csi500","entry_price":None,"entry_price_date":None,"daily_prices":[],"status":"active","added_at":"2026-10-22T18:20:31.468477"},
]

new_prices = {
    "301377.SZ": {"2026-10-21": 263.00, "2026-10-22": 244.00, "2026-10-23": 246.00, "2026-10-26": 232.30},
    "002409.SZ": {"2026-10-21": 96.80, "2026-10-22": 92.39, "2026-10-23": 94.47, "2026-10-26": 92.90},
    "301526.SZ": {"2026-10-21": 21.89, "2026-10-22": 20.61, "2026-10-23": 20.84, "2026-10-26": 20.30},
    "300223.SZ": {"2026-10-21": 104.48, "2026-10-22": 100.89, "2026-10-23": 105.40, "2026-10-26": 101.83},
    "300475.SZ": {"2026-10-21": 116.78, "2026-10-22": 111.87, "2026-10-23": 114.68, "2026-10-26": 111.36},
    "301200.SZ": {"2026-10-21": 173.00, "2026-10-22": 164.02, "2026-10-23": 166.22, "2026-10-26": 161.46},
    "002945.SZ": {"2026-10-21": 14.21, "2026-10-22": 13.96, "2026-10-23": 15.36, "2026-10-26": 15.59},
    "688629.SH": {"2026-10-21": 79.98, "2026-10-22": 75.51, "2026-10-23": 77.17, "2026-10-26": 77.09},
    "688347.SH": {"2026-10-21": 169.73, "2026-10-22": 163.89, "2026-10-23": 165.50, "2026-10-26": 158.99},
    "688172.SH": {"2026-10-21": 34.83, "2026-10-22": 33.28, "2026-10-23": 33.09, "2026-10-26": 32.58},
    "001309.SZ": {"2026-10-21": 233.81, "2026-10-22": 243.17, "2026-10-23": 251.00, "2026-10-26": 245.34},
    "300567.SZ": {"2026-10-21": 142.63, "2026-10-22": 134.04, "2026-10-23": 134.51, "2026-10-26": 130.33},
    "002966.SZ": {"2026-10-21": 9.08, "2026-10-22": 9.05, "2026-10-23": 9.24, "2026-10-26": 9.22},
    "300115.SZ": {"2026-10-21": 26.41, "2026-10-22": 25.78, "2026-10-23": 29.60, "2026-10-26": 28.89},
    "688702.SH": {"2026-10-21": 225.35, "2026-10-22": 212.00, "2026-10-23": 214.29, "2026-10-26": 211.04},
    "600363.SH": {"2026-10-21": 9.63, "2026-10-22": 8.67, "2026-10-23": 8.73, "2026-10-26": 8.93},
    "002008.SZ": {"2026-10-21": 64.10, "2026-10-22": 60.54, "2026-10-23": 61.89, "2026-10-26": 59.73},
}

names = {
    "001309.SZ": "德明利", "300567.SZ": "精测电子", "002966.SZ": "苏州银行",
    "300115.SZ": "长盈精密", "688702.SH": "盛科通信-U", "600363.SH": "ST联光",
    "002008.SZ": "大族激光",
}

def merge_daily_prices(existing, new_prices_dict, entry_date):
    by_date = {}
    for p in existing:
        by_date[p["date"]] = p["close"]
    for date_str, close in sorted(new_prices_dict.items()):
        if date_str >= entry_date:
            by_date[date_str] = close
    result = []
    for day, date_str in enumerate(sorted(by_date.keys())):
        result.append({"date": date_str, "day": day, "close": by_date[date_str]})
    return result

def calc_returns(daily_prices, entry_price):
    if not daily_prices or entry_price is None:
        return {"current_return_pct": 0.0, "max_return_pct": 0.0, "max_return_day": 0}
    current_close = daily_prices[-1]["close"]
    current_return = round((current_close / entry_price - 1) * 100, 2)
    max_return = 0.0
    max_day = 0
    for p in daily_prices:
        ret = round((p["close"] / entry_price - 1) * 100, 2)
        if ret > max_return:
            max_return = ret
            max_day = p["day"]
    return {"current_return_pct": current_return, "max_return_pct": max_return, "max_return_day": max_day}

for entry in original_active:
    wc = entry.get("windcode", "")
    if wc not in new_prices:
        continue

    # Fill missing name
    if not entry.get("name") or entry.get("name") == wc:
        if wc in names:
            entry["name"] = names[wc]

    # Handle TiMi signal
    if entry.get("source") == "timi":
        added_date = entry.get("added_date", "2026-10-21")
        existing = entry.get("daily_prices", [])
        entry["daily_prices"] = merge_daily_prices(existing, new_prices[wc], added_date)
        entry["name"] = names.get(wc, entry.get("name", wc))
        ep = entry.get("entry_price") or entry.get("trigger_price")
        if ep:
            entry["returns"] = calc_returns(entry["daily_prices"], ep)
        continue

    # Determine entry for signals without one
    if entry.get("entry_price") is None:
        entry_date_map = {
            "2026-10-20": "2026-10-21",
            "2026-10-21": "2026-10-22",
            "2026-10-22": "2026-10-23",
        }
        edate = entry_date_map.get(entry["signal_date"])
        if edate and edate in new_prices[wc]:
            entry["entry_price"] = new_prices[wc][edate]
            entry["entry_price_date"] = edate

    entry_price = entry.get("entry_price")
    entry_date = entry.get("entry_price_date")
    if entry_price is None or entry_date is None:
        continue

    existing = entry.get("daily_prices", [])
    entry["daily_prices"] = merge_daily_prices(existing, new_prices[wc], entry_date)
    entry["trading_day"] = len(entry["daily_prices"]) - 1 if entry["daily_prices"] else 0
    entry["returns"] = calc_returns(entry["daily_prices"], entry_price)

# Load history for stats
with open("D:/AgentAlpha/my-bot/data/signal_watchlist_history.json", "r", encoding="utf-8") as f:
    hist = json.load(f)

completed = hist.get("completed", [])
returns_5d = [c["return_5d"] for c in completed]
wins = [r for r in returns_5d if r > 0]

wl = {
    "version": "4.3-timi",
    "last_updated": "2026-10-26T18:30:00",
    "last_signal_date": "2026-10-26",
    "active": original_active,
    "stats": {
        "total_signals": len(completed),
        "avg_return_5d": round(sum(returns_5d) / len(returns_5d), 2),
        "win_rate_5d": round(len(wins) / len(returns_5d) * 100, 1),
        "max_return": max(returns_5d),
        "max_loss": min(returns_5d)
    }
}

with open("D:/AgentAlpha/my-bot/data/signal_watchlist.json", "w", encoding="utf-8") as f:
    json.dump(wl, f, ensure_ascii=False, indent=2)

print("Watchlist reconstructed and updated!")
print(f"Active: {len(wl['active'])}, Stats: {wl['stats']}")

# Validate
for e in wl["active"]:
    dp = e.get("daily_prices", [])
    if dp:
        dates = [p["date"] for p in dp]
        assert dates == sorted(dates), f"BAD ORDER: {e.get('windcode')} -> {dates}"
        days = [p["day"] for p in dp]
        assert days == list(range(len(days))), f"BAD DAYS: {e.get('windcode')} -> {days}"
print("All validations passed!")

for i, e in enumerate(wl["active"]):
    n = e.get("name") or e.get("windcode","?")
    w = e.get("windcode","?")
    s = e.get("source","?")
    ep = e.get("entry_price","-")
    r = e.get("returns",{}).get("current_return_pct",0)
    mr = e.get("returns",{}).get("max_return_pct",0)
    td = e.get("trading_day",0)
    print(f"  {i+1:2d}. {n:<10s} ({w}) [{s}] entry={str(ep):>8s} td={td} ret={r:>7.2f}% max={mr:>7.2f}%")