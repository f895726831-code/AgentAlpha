"""
Signal Watchlist — ML 信号追踪系统

每日取 ML 集成模型 Top 5 信号纳入观察池，通过 Wind MCP 获取行情，
追踪 5 个交易日内的模拟收益。

用法:
    python scripts/signal_watchlist.py                  # 完整周期: init + update + report
    python scripts/signal_watchlist.py --init           # 仅初始化新信号
    python scripts/signal_watchlist.py --update         # 仅更新价格
    python scripts/signal_watchlist.py --report-only    # 仅生成报告
    python scripts/signal_watchlist.py --status         # 查看当前状态
"""

import json
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# ─── 路径配置 ───

FM_TRADE_DIR = Path(__file__).resolve().parent.parent
SIGNAL_FILE = FM_TRADE_DIR / "data" / "output" / "alpha_signals_latest.json"
WATCHLIST_FILE = FM_TRADE_DIR / "data" / "signal_watchlist.json"
WATCHLIST_HISTORY_FILE = FM_TRADE_DIR / "data" / "signal_watchlist_history.json"
TRADING_CALENDAR_FILE = FM_TRADE_DIR / "src" / "data" / "trading-calendar-2026.json"
REPORTS_DIR = FM_TRADE_DIR / "reports" / "signals"
WIND_CLI_ROOT = Path("%USERPROFILE%/.claude/skills/wind-mcp-skill")
WIND_CLI_SCRIPT = "scripts/cli.mjs"

TRACKING_DAYS = 5
TOP_N = 5


# ─── 符号转换 ───

def convert_symbol(raw: str) -> str:
    """sh600095 → 600095.SH, sz000785 → 000785.SZ"""
    if raw.startswith("sh"):
        return f"{raw[2:]}.SH"
    elif raw.startswith("sz"):
        return f"{raw[2:]}.SZ"
    else:
        raise ValueError(f"Unknown symbol format: {raw}")


# ─── 交易日历 ───

def load_trading_calendar() -> dict:
    """加载交易日历"""
    with open(TRADING_CALENDAR_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def is_trading_day(date_str: str, calendar: dict) -> bool:
    """判断是否为交易日"""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    weekday = dt.weekday()  # 0=Mon, 6=Sun
    closed = set(calendar.get("closed_dates", []))

    if date_str in closed:
        return False

    # 周末
    if weekday >= 5:
        # 检查是否为调休补班
        makeup = set()
        for item in calendar.get("makeup_trading_days", []):
            if isinstance(item, str):
                makeup.add(item)
            elif isinstance(item, dict) and "date" in item:
                makeup.add(item["date"])
        for d in calendar.get("special_trading_dates", []):
            makeup.add(d)
        return date_str in makeup

    return True


def get_trading_days(calendar: dict, start_date: str, count: int) -> list:
    """获取从 start_date 开始的 count 个交易日（含 start_date）"""
    result = []
    current = datetime.strptime(start_date, "%Y-%m-%d")
    while len(result) < count:
        date_str = current.strftime("%Y-%m-%d")
        if is_trading_day(date_str, calendar):
            result.append(date_str)
        current += timedelta(days=1)
    return result


# ─── Wind MCP 桥接 ───

# Windows 上 Python subprocess 直接调 node 会丢失 process.stdout.write 的输出。
# 必须通过 bash 脚本文件执行（bash script.sh 有效，bash -c "node ..." 无效）。
# bash 脚本中必须使用相对路径（cd 后执行），绝对路径会导致 @ 文件解析失败。
BASH_EXE = "D:/Git/usr/bin/bash.exe"
WIND_MCP_SCRIPTS_DIR = WIND_CLI_ROOT / "scripts"


def call_wind_mcp(windcodes: list[str], indexes: str = None) -> dict:
    """
    调用 Wind MCP CLI 批量查询行情。

    Args:
        windcodes: Wind 代码列表，如 ["600095.SH", "000785.SZ"]
        indexes: 指标字段，逗号分隔。默认：最新成交价,涨跌幅,中文简称,5日涨跌幅

    Returns:
        {windcode: {name: str, close: float, change_pct: float, change_5d: float}}
    """
    if not windcodes:
        return {}

    if indexes is None:
        indexes = "中文简称,最新成交价,涨跌幅,5日涨跌幅"

    import uuid
    req_id = uuid.uuid4().hex[:8]

    # 写入参数文件到 wind-mcp-skill/scripts/ 目录
    req_filename = f"request-swt-{req_id}.json"
    req_file = WIND_MCP_SCRIPTS_DIR / req_filename
    with open(req_file, "w", encoding="utf-8") as f:
        json.dump({"windcode": ",".join(windcodes), "indexes": indexes}, f, ensure_ascii=False)

    # 写入 bash 脚本到 AgentAlpha/data/ 目录
    sh_filename = f"_run_swt_{req_id}.sh"
    sh_file = FM_TRADE_DIR / "data" / sh_filename
    unix_sh = f"/d/AgentAlpha/my-bot/data/{sh_filename}"
    sh_content = (
        f"#!/bin/bash\n"
        f"cd /c/Users/%USERPROFILE%/.claude/skills/wind-mcp-skill\n"
        f"node scripts/cli.mjs call stock_data get_stock_price_indicators @scripts/{req_filename}\n"
    )
    with open(sh_file, "w", encoding="utf-8", newline="\n") as f:
        f.write(sh_content)

    try:
        result = subprocess.run(
            [BASH_EXE, unix_sh],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60
        )
    except subprocess.TimeoutExpired:
        print(f"  [WARN] Wind MCP timeout for {len(windcodes)} codes")
        return {}
    except Exception as e:
        print(f"  [WARN] Wind MCP subprocess error: {e}")
        return {}
    finally:
        # 清理临时文件
        req_file.unlink(missing_ok=True)
        sh_file.unlink(missing_ok=True)

    if result.returncode != 0:
        try:
            err_data = json.loads(result.stdout or "{}")
            err_code = err_data.get("error", {}).get("code", "unknown")
            print(f"  [WARN] Wind MCP error: {err_code}")
        except Exception:
            print(f"  [WARN] Wind MCP exit={result.returncode}: {result.stderr[:200]}")
        return {}

    return _parse_wind_response(result.stdout, indexes)


def _parse_wind_response(stdout: str, indexes: str) -> dict:
    """
    解析 Wind MCP CLI 响应。
    按列位置解析（不依赖列名，因为 bash 管道可能导致中文乱码）。

    列顺序即 indexes 参数顺序 + Wind代码（最后一列）:
      indexes="中文简称,最新成交价,涨跌幅,5日涨跌幅"
      列0=中文简称, 列1=最新成交价, 列2=涨跌幅, 列3=5日涨跌幅, 列4=Wind代码

    Returns:
        {windcode: {name: str, close: float, change_pct: float, change_5d: float}}
    """
    try:
        raw = json.loads(stdout)
    except json.JSONDecodeError:
        print(f"  [WARN] Wind MCP response is not valid JSON")
        return {}

    # MCP 信封: {content: [{type: "text", text: "..."}]}
    content = raw.get("content", [])
    if not content:
        return {}

    text = content[0].get("text", "{}")
    try:
        inner = json.loads(text)
    except json.JSONDecodeError:
        print(f"  [WARN] Wind MCP inner content is not JSON")
        return {}

    data = inner.get("data", {})
    rows = data.get("rows", [])
    if not rows:
        return {}

    # 列位置: indexes 参数顺序 + Wind代码（最后一列）
    idx_names = indexes.split(",")
    # 建立索引名 → 列位置映射
    col_map = {}
    for i, name in enumerate(idx_names):
        col_map[name.strip()] = i
    # Wind代码始终在最后一列（如果 indexes 中没包含的话）
    col_map["Wind代码"] = len(idx_names)

    result = {}
    for row in rows:
        if len(row) <= col_map["Wind代码"]:
            continue

        windcode = str(row[col_map["Wind代码"]]).strip() if row[col_map["Wind代码"]] else "unknown"
        entry = {}

        # 提取名称
        name_idx = col_map.get("中文简称")
        if name_idx is not None and name_idx < len(row):
            entry["name"] = str(row[name_idx]) if row[name_idx] else ""

        # 提取价格
        close_idx = col_map.get("最新成交价")
        if close_idx is not None and close_idx < len(row):
            try:
                entry["close"] = float(row[close_idx])
            except (ValueError, TypeError):
                entry["close"] = None
        else:
            entry["close"] = None

        # 提取涨跌幅
        change_idx = col_map.get("涨跌幅")
        if change_idx is not None and change_idx < len(row):
            try:
                entry["change_pct"] = float(row[change_idx])
            except (ValueError, TypeError):
                entry["change_pct"] = None
        else:
            entry["change_pct"] = None

        # 提取 5 日涨跌幅
        change5d_idx = col_map.get("5日涨跌幅")
        if change5d_idx is not None and change5d_idx < len(row):
            try:
                entry["change_5d"] = float(row[change5d_idx])
            except (ValueError, TypeError):
                entry["change_5d"] = None
        else:
            entry["change_5d"] = None

        result[windcode] = entry

    return result


def query_prices_with_retry(windcodes: list[str], max_retries: int = 2) -> dict:
    """带重试的价格查询，失败时逐只回退"""
    for attempt in range(max_retries):
        result = call_wind_mcp(windcodes)
        if result:
            return result
        if attempt < max_retries - 1:
            time.sleep(2 ** attempt)

    # 全部失败，逐只重试
    print(f"  [WARN] Batch query failed, falling back to individual queries...")
    results = {}
    for code in windcodes:
        time.sleep(0.5)  # 避免触发频率限制
        single = call_wind_mcp([code])
        if single:
            results.update(single)
        else:
            results[code] = None  # 标记为不可用
    return results


# ─── 数据加载/保存 ───

def load_watchlist() -> dict:
    """加载信号观察池"""
    if WATCHLIST_FILE.exists():
        with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"version": "1.0", "last_updated": "", "last_signal_date": "", "active": []}


def save_watchlist(data: dict):
    """保存信号观察池"""
    WATCHLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_history() -> dict:
    """加载已完成信号历史"""
    if WATCHLIST_HISTORY_FILE.exists():
        with open(WATCHLIST_HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"version": "1.0", "completed": []}


def save_history(data: dict):
    """保存已完成信号历史"""
    WATCHLIST_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(WATCHLIST_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def read_signals() -> Optional[dict]:
    """读取 alpha_signals_latest.json"""
    if not SIGNAL_FILE.exists():
        print(f"  [WARN] Signal file not found: {SIGNAL_FILE}")
        return None
    with open(SIGNAL_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


# ─── 核心逻辑 ───

def add_new_signals(watchlist: dict, calendar: dict) -> int:
    """
    读取最新信号，将 Top 5 加入 active。
    去重：同一 windcode 已在 active 中 → 跳过（保留现有追踪）。
    返回新增数量。
    """
    signals = read_signals()
    if not signals:
        return 0

    signal_date = signals.get("date", "")
    if not signal_date:
        return 0

    # 如果这个 signal_date 已经处理过，跳过
    if watchlist.get("last_signal_date") == signal_date:
        print(f"  Signal date {signal_date} already processed, skipping init")
        return 0

    top_10 = signals.get("top_10", [])
    if not top_10:
        print(f"  No signals in top_10")
        return 0

    top_n = top_10[:TOP_N]

    # 确定信号来源
    source = "csi500"  # alpha_signals_latest.json 默认是 CSI 500
    if "csi300" in signals.get("model", "").lower():
        source = "csi300"

    # 收集已有 active 的 windcode
    active_codes = {s["windcode"] for s in watchlist.get("active", [])}

    added = 0
    for sig in top_n:
        raw_symbol = sig.get("symbol", "")
        try:
            windcode = convert_symbol(raw_symbol)
        except ValueError:
            print(f"  [WARN] Cannot convert symbol: {raw_symbol}")
            continue

        if windcode in active_codes:
            print(f"  Skip {windcode}: already in active tracking")
            continue

        entry = {
            "signal_id": f"{signal_date}-{raw_symbol}",
            "signal_date": signal_date,
            "symbol_raw": raw_symbol,
            "windcode": windcode,
            "name": "",
            "alpha_score": sig.get("alpha_score", 0),
            "rank": sig.get("rank", 0),
            "source": source,
            "entry_price": None,
            "entry_price_date": None,
            "trading_day": 0,
            "daily_prices": [],
            "returns": {
                "current_return_pct": 0.0,
                "max_return_pct": 0.0,
                "max_return_day": 0,
            },
            "status": "active",
            "added_at": datetime.now().isoformat(),
        }
        watchlist["active"].append(entry)
        active_codes.add(windcode)
        added += 1
        print(f"  + [{sig.get('rank')}] {windcode} (alpha={sig.get('alpha_score', 0):.4f})")

    if added > 0:
        watchlist["last_signal_date"] = signal_date

    return added


def update_prices(watchlist: dict, calendar: dict) -> int:
    """
    更新所有 active 条目的价格。
    - 首次查询：填充 name 和 entry_price
    - 后续查询：追加当日收盘价到 daily_prices
    返回成功更新的条目数。
    """
    active = watchlist.get("active", [])
    if not active:
        return 0

    today = datetime.now().strftime("%Y-%m-%d")

    # 检查今天是否为交易日
    if not is_trading_day(today, calendar):
        print(f"  {today} is not a trading day, skipping price update")
        return 0

    # 收集需要查询的 windcode
    windcodes = [s["windcode"] for s in active]
    print(f"  Querying Wind MCP for {len(windcodes)} stocks...")

    prices = query_prices_with_retry(windcodes)

    if not prices:
        print(f"  [WARN] Wind MCP returned no data, skipping update")
        return 0

    updated = 0
    for entry in active:
        wc = entry["windcode"]
        price_data = prices.get(wc)

        if price_data is None:
            print(f"  [WARN] No price data for {wc}")
            continue

        close_price = price_data.get("close")
        if close_price is None:
            print(f"  [WARN] No close price for {wc} (may be suspended)")
            continue

        # 填充名称
        if not entry.get("name"):
            entry["name"] = price_data.get("name", "")

        # 首次填充入场价
        if entry.get("entry_price") is None:
            entry["entry_price"] = close_price
            entry["entry_price_date"] = today
            entry["daily_prices"].append({
                "date": today,
                "day": 0,
                "close": close_price,
            })
            print(f"  Entry price: {wc} = {close_price}")
            updated += 1
            continue

        # 检查今天是否已记录
        recorded_dates = {p["date"] for p in entry.get("daily_prices", [])}
        if today in recorded_dates:
            # 更新今日收盘价（可能盘中运行过）
            for p in entry["daily_prices"]:
                if p["date"] == today:
                    p["close"] = close_price
                    break
            updated += 1
            continue

        # 追加新交易日价格
        day = len(entry["daily_prices"])
        entry["daily_prices"].append({
            "date": today,
            "day": day,
            "close": close_price,
        })
        entry["trading_day"] = day

        # 更新收益
        entry_price = entry["entry_price"]
        if entry_price and entry_price > 0:
            current_return = (close_price - entry_price) / entry_price
            entry["returns"]["current_return_pct"] = round(current_return * 100, 2)

            # 更新最大收益
            all_returns = []
            for p in entry["daily_prices"]:
                if p.get("close") and entry_price > 0:
                    r = (p["close"] - entry_price) / entry_price
                    all_returns.append((r, p["day"]))

            if all_returns:
                max_r, max_day = max(all_returns, key=lambda x: x[0])
                entry["returns"]["max_return_pct"] = round(max_r * 100, 2)
                entry["returns"]["max_return_day"] = max_day

        print(f"  Day {day}: {wc} = {close_price} ({entry['returns']['current_return_pct']:+.2f}%)")
        updated += 1

    return updated


def complete_entries(watchlist: dict, history_data: dict) -> int:
    """
    结算满 5 天的条目，移入历史。
    返回结算数量。
    """
    active = watchlist.get("active", [])
    completed = []
    remaining = []

    for entry in active:
        if entry.get("trading_day", 0) >= TRACKING_DAYS:
            # 结算
            daily = entry.get("daily_prices", [])
            entry_price = entry.get("entry_price")

            if entry_price and daily and entry_price > 0:
                final_close = daily[-1]["close"]
                entry["exit_price"] = final_close
                entry["return_5d"] = round((final_close - entry_price) / entry_price * 100, 2)

                # 计算最大收益和最大回撤
                all_returns = []
                for p in daily:
                    if p.get("close") and entry_price > 0:
                        r = (p["close"] - entry_price) / entry_price * 100
                        all_returns.append(r)
                if all_returns:
                    entry["max_return_5d"] = round(max(all_returns), 2)
                    entry["max_drawdown_5d"] = round(min(all_returns), 2)

            entry["status"] = "completed"
            entry["completed_date"] = datetime.now().strftime("%Y-%m-%d")
            completed.append(entry)
            print(f"  Completed: {entry['windcode']} return_5d={entry.get('return_5d', 'N/A')}%")
        else:
            remaining.append(entry)

    if completed:
        watchlist["active"] = remaining
        history_data["completed"].extend(completed)

    return len(completed)


def update_stats(watchlist: dict, history_data: dict):
    """更新累计统计"""
    all_completed = history_data.get("completed", [])
    total = len(all_completed)

    if total == 0:
        watchlist["stats"] = {
            "total_signals": 0,
            "avg_return_5d": 0,
            "win_rate_5d": 0,
        }
        return

    returns = [c.get("return_5d", 0) for c in all_completed if c.get("return_5d") is not None]
    wins = sum(1 for r in returns if r > 0)

    watchlist["stats"] = {
        "total_signals": total,
        "avg_return_5d": round(sum(returns) / len(returns), 2) if returns else 0,
        "win_rate_5d": round(wins / len(returns) * 100, 1) if returns else 0,
        "max_return": round(max(returns), 2) if returns else 0,
        "max_loss": round(min(returns), 2) if returns else 0,
    }


# ─── 报告生成 ───

def generate_report(watchlist: dict, history_data: dict) -> str:
    """生成 Markdown 报告，返回报告路径"""
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"{today}-signal-tracker.md"

    active = watchlist.get("active", [])
    stats = watchlist.get("stats", {})
    completed = history_data.get("completed", [])

    lines = [
        f"# ML 信号追踪报告",
        f"",
        f"> 生成时间: {now} | 数据来源: Wind MCP | 追踪周期: {TRACKING_DAYS} 交易日",
        f"",
    ]

    # ─── 活跃追踪 ───
    lines.append(f"## 活跃追踪 (Active)")
    lines.append(f"")
    if active:
        lines.append(f"| # | 代码 | 名称 | 信号日 | Alpha | 入场价 | 现价 | 涨跌幅 | 持有天数 |")
        lines.append(f"|---|------|------|--------|-------|--------|------|--------|----------|")
        for i, entry in enumerate(active, 1):
            wc = entry["windcode"]
            name = entry.get("name", "?")
            sig_date = entry["signal_date"][-5:]  # MM-DD
            alpha = entry.get("alpha_score", 0)
            entry_price = entry.get("entry_price")
            ep_str = f"{entry_price:.2f}" if entry_price else "N/A"
            daily = entry.get("daily_prices", [])
            current_price = daily[-1]["close"] if daily else None
            cp_str = f"{current_price:.2f}" if current_price else "N/A"
            ret = entry["returns"].get("current_return_pct", 0)
            ret_str = f"{ret:+.2f}%" if ret else "0.00%"
            day = entry.get("trading_day", 0)
            lines.append(
                f"| {i} | {wc} | {name} | {sig_date} | {alpha:.4f} | {ep_str} | {cp_str} | {ret_str} | {day}/{TRACKING_DAYS} |")
    else:
        lines.append("暂无活跃追踪信号。")
    lines.append("")

    # ─── 今日新纳入 ───
    today_added = [s for s in active if s.get("signal_date") == today]
    if today_added:
        lines.append(f"## 今日新纳入")
        lines.append(f"")
        lines.append(f"| # | 代码 | 名称 | Alpha | 排名 | 来源 |")
        lines.append(f"|---|------|------|-------|------|------|")
        for entry in today_added:
            lines.append(
                f"| {entry['rank']} | {entry['windcode']} | {entry.get('name', '?')} | {entry['alpha_score']:.4f} | {entry['rank']} | {entry.get('source', 'csi500').upper()} |")
        lines.append("")

    # ─── 今日结算 ───
    today_completed = [c for c in completed if c.get("completed_date") == today]
    if today_completed:
        lines.append(f"## 今日结算 (满 {TRACKING_DAYS} 日)")
        lines.append(f"")
        lines.append(f"| 代码 | 名称 | 信号日 | 入场价 | 出场价 | 5日收益 | 最大收益 | 最大回撤 |")
        lines.append(f"|------|------|--------|--------|--------|---------|----------|----------|")
        for entry in today_completed:
            lines.append(
                f"| {entry['windcode']} | {entry.get('name', '?')} | {entry['signal_date']} | "
                f"{entry.get('entry_price', 'N/A')} | {entry.get('exit_price', 'N/A')} | "
                f"{entry.get('return_5d', 0):+.2f}% | {entry.get('max_return_5d', 0):+.2f}% | "
                f"{entry.get('max_drawdown_5d', 0):+.2f}% |")
        lines.append("")

    # ─── 最近完成 ───
    recent = completed[-10:] if len(completed) > 10 else completed
    if recent:
        lines.append(f"## 最近完成 (最近 {len(recent)} 条)")
        lines.append(f"")
        lines.append(f"| 代码 | 名称 | 信号日 | 5日收益 | 最大收益 | 最大回撤 | 完成日 |")
        lines.append(f"|------|------|--------|---------|----------|----------|--------|")
        for entry in reversed(recent):
            lines.append(
                f"| {entry['windcode']} | {entry.get('name', '?')} | {entry['signal_date']} | "
                f"{entry.get('return_5d', 0):+.2f}% | {entry.get('max_return_5d', 0):+.2f}% | "
                f"{entry.get('max_drawdown_5d', 0):+.2f}% | {entry.get('completed_date', '?')} |")
        lines.append("")

    # ─── 累计统计 ───
    lines.append(f"## 累计统计")
    lines.append(f"")
    lines.append(f"| 指标 | 数值 |")
    lines.append(f"|------|------|")
    lines.append(f"| 总信号数 | {stats.get('total_signals', 0)} |")
    lines.append(f"| 平均 5 日收益 | {stats.get('avg_return_5d', 0):+.2f}% |")
    lines.append(f"| 胜率 (正收益) | {stats.get('win_rate_5d', 0)}% |")
    lines.append(f"| 最大单笔收益 | {stats.get('max_return', 0):+.2f}% |")
    lines.append(f"| 最大单笔亏损 | {stats.get('max_loss', 0):+.2f}% |")
    lines.append(f"")
    lines.append(f"---")
    lines.append(f"")
    lines.append(f"> SIGNAL_TRACKER_COMPLETE")

    report_text = "\n".join(lines)
    report_path.write_text(report_text, encoding="utf-8")
    print(f"  Report: {report_path}")
    return str(report_path)


# ─── CLI 入口 ───

def cmd_status():
    """打印当前状态"""
    watchlist = load_watchlist()
    history = load_history()

    active = watchlist.get("active", [])
    completed = history.get("completed", [])
    stats = watchlist.get("stats", {})

    print(f"\n=== Signal Watchlist Status ===")
    print(f"Last updated: {watchlist.get('last_updated', 'never')}")
    print(f"Last signal date: {watchlist.get('last_signal_date', 'none')}")
    print(f"Active tracking: {len(active)}")
    for s in active:
        print(f"  [{s['trading_day']}/{TRACKING_DAYS}d] {s['windcode']} ({s.get('name', '?')}) "
              f"entry={s.get('entry_price')} ret={s['returns'].get('current_return_pct', 0):+.2f}%")
    print(f"Completed: {len(completed)}")
    print(f"Stats: {json.dumps(stats, ensure_ascii=False)}")


def cmd_init(calendar: dict):
    """仅初始化新信号"""
    watchlist = load_watchlist()
    added = add_new_signals(watchlist, calendar)
    if added > 0:
        watchlist["last_updated"] = datetime.now().isoformat()
        save_watchlist(watchlist)
        print(f"  Added {added} new signals")
    else:
        print(f"  No new signals to add")


def cmd_update(calendar: dict):
    """仅更新价格"""
    watchlist = load_watchlist()
    history = load_history()

    updated = update_prices(watchlist, calendar)
    completed = complete_entries(watchlist, history)
    update_stats(watchlist, history)

    watchlist["last_updated"] = datetime.now().isoformat()
    save_watchlist(watchlist)
    save_history(history)

    print(f"  Updated: {updated}, Completed: {completed}")


def cmd_report():
    """仅生成报告"""
    watchlist = load_watchlist()
    history = load_history()
    path = generate_report(watchlist, history)
    print(f"Report generated: {path}")


def cmd_full(calendar: dict):
    """完整周期：init + update + report"""
    watchlist = load_watchlist()
    history = load_history()

    # 1. 初始化新信号
    added = add_new_signals(watchlist, calendar)

    # 2. 更新价格
    updated = update_prices(watchlist, calendar)

    # 3. 结算完成条目
    completed = complete_entries(watchlist, history)

    # 4. 更新统计
    update_stats(watchlist, history)

    # 5. 保存
    watchlist["last_updated"] = datetime.now().isoformat()
    save_watchlist(watchlist)
    save_history(history)

    print(f"  Summary: +{added} new, {updated} updated, {completed} completed")

    # 6. 生成报告
    generate_report(watchlist, history)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Signal Watchlist — ML 信号追踪")
    parser.add_argument("--init", action="store_true", help="仅初始化新信号")
    parser.add_argument("--update", action="store_true", help="仅更新价格")
    parser.add_argument("--report-only", action="store_true", help="仅生成报告")
    parser.add_argument("--status", action="store_true", help="查看当前状态")
    args = parser.parse_args()

    calendar = load_trading_calendar()

    if args.status:
        cmd_status()
    elif args.init and not args.update:
        cmd_init(calendar)
    elif args.update and not args.init:
        cmd_update(calendar)
    elif args.report_only:
        cmd_report()
    else:
        # 默认：完整周期
        cmd_full(calendar)


if __name__ == "__main__":
    main()