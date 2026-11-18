#!/usr/bin/env python3
"""
港股行情数据桥接 — akshare (主) → Wind MCP stock_data (降级)

通道优先级：
1. akshare stock_hk_spot_em / stock_hk_hist — 免费、无额度限制，但东财 CDN 偶尔阻断
2. Wind MCP stock_data.get_stock_price_indicators / get_stock_kline — 已验证支持港股（windcode 格式 00700.HK），需 API Key 和额度

背景：
- 2026-09-22 初版：以为 Wind MCP 不支持港股（实际是 fund_data 不支持个股，stock_data 支持）
- 2026-09-23 修正：新增 Wind MCP 降级通道，akshare 失败时自动切换

用途：
- 每日盘前/盘后从 portfolio.json 提取港股 watchlist，获取最新行情
- 输出 JSON 到 data/output/hk_quotes.json 供 cron 报告读取

使用：
  python scripts/hk_stock_data.py                    # 全量（portfolio 中所有港股）
  python scripts/hk_stock_data.py --codes 00700,01810  # 指定代码
  python scripts/hk_stock_data.py --days 5           # 最近 N 日 K 线
"""

import argparse
import json
import subprocess
import sys
import os
from datetime import datetime, timedelta
from pathlib import Path

# Windows GBK 编码兼容：强制 stdout 为 UTF-8
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── 依赖检查（akshare 必需，Wind MCP 为降级通道） ──────────────────────
_AKSHARE_OK = False
try:
    import akshare as ak
    import pandas as pd
    _AKSHARE_OK = True
except ImportError:
    pass  # 仍可尝试 Wind MCP

# ── 路径常量 ───────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "data" / "output"
HK_QUOTES_PATH = OUTPUT_DIR / "hk_quotes.json"

# 港股代码 → 中文名称静态映射（Wind MCP 快照接口不返回简称时的兜底）
HK_NAME_MAP = {
    "00700": "腾讯控股",
    "01810": "小米集团-W",
    "00020": "商汤-W",
    "09880": "优必选",
}

# Wind MCP CLI 路径
_WIND_SKILL_DIR = os.path.expandvars(
    os.path.join("%USERPROFILE%", ".agents", "skills", "wind-mcp-skill")
)
WIND_CLI = os.path.join(_WIND_SKILL_DIR, "scripts", "cli.mjs")


# ═══════════════════════════════════════════════════════════════════════════
# 代码提取
# ═══════════════════════════════════════════════════════════════════════════

def extract_hk_codes(portfolio_path: Path) -> list[str]:
    """从 portfolio.json 提取所有港股标的代码（去重，标准化为 5 位前导零）"""
    with open(portfolio_path, "r", encoding="utf-8") as f:
        pf = json.load(f)

    codes = set()
    for section in ["watchlist", "positions", "closed"]:
        for item in pf.get(section, []):
            code = item.get("code", "") or item.get("symbol", "")
            if code.endswith(".HK"):
                numeric = code.replace(".HK", "").lstrip("0") or "0"
                codes.add(f"{int(numeric):05d}")

    return sorted(codes)


# ═══════════════════════════════════════════════════════════════════════════
# Wind MCP 调用封装
# ═══════════════════════════════════════════════════════════════════════════

def _call_wind_mcp(server_type: str, tool_name: str, params: dict,
                   timeout: int = 30) -> dict:
    """调用 Wind MCP CLI，返回解析后的 data 字典；失败返回 {"error": ...}"""
    if not os.path.isfile(WIND_CLI):
        return {"error": f"Wind MCP CLI 不存在: {WIND_CLI}"}

    params_json = json.dumps(params, ensure_ascii=False)
    try:
        result = subprocess.run(
            ["node", WIND_CLI, "call", server_type, tool_name, params_json],
            capture_output=True, encoding="utf-8", timeout=timeout,
            cwd=_WIND_SKILL_DIR,
        )
    except subprocess.TimeoutExpired:
        return {"error": f"Wind MCP 超时 ({timeout}s)"}
    except FileNotFoundError:
        return {"error": "Node.js 不可用"}

    if result.returncode != 0:
        return {"error": f"Wind MCP exit {result.returncode}: {result.stderr[:200]}"}

    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"error": f"JSON 解析失败: {result.stdout[:200]}"}

    if response.get("isError"):
        err_text = ""
        content = response.get("content", [])
        if content:
            err_text = content[0].get("text", "")
        return {"error": err_text or "Wind MCP 返回错误"}

    # 解析 content[0].text → JSON
    content = response.get("content", [])
    if not content:
        return {"error": "Wind MCP 返回空 content"}

    text = content[0].get("text", "{}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {"error": f"content.text 解析失败: {text[:200]}"}

    if data.get("error"):
        return {"error": json.dumps(data["error"], ensure_ascii=False)}

    return data


# ═══════════════════════════════════════════════════════════════════════════
# 通道 1: akshare (主)
# ═══════════════════════════════════════════════════════════════════════════

def fetch_spot_akshare(codes: list[str]) -> dict:
    """通过 akshare stock_hk_spot_em 获取港股快照"""
    if not _AKSHARE_OK:
        return {"error": "akshare 不可用"}

    try:
        df = ak.stock_hk_spot_em()
    except Exception as e:
        return {"error": f"akshare stock_hk_spot_em 失败: {e}"}

    result = {"as_of": datetime.now().isoformat(), "source": "akshare", "stocks": {}}
    for code in codes:
        row = df[df["代码"] == code]
        if row.empty:
            result["stocks"][code] = {"error": "akshare: 未找到"}
            continue
        r = row.iloc[0]
        result["stocks"][code] = {
            "name": str(r.get("名称", "")),
            "price": float(r.get("最新价", 0) or 0),
            "change_pct": float(r.get("涨跌幅", 0) or 0),
            "change_amount": float(r.get("涨跌额", 0) or 0),
            "volume": float(r.get("成交量", 0) or 0),
            "turnover": float(r.get("成交额", 0) or 0),
            "high": float(r.get("最高价", 0) or 0),
            "low": float(r.get("最低价", 0) or 0),
            "open": float(r.get("今开", 0) or 0),
            "prev_close": float(r.get("昨收", 0) or 0),
        }
    return result


def fetch_history_akshare(codes: list[str], days: int = 5) -> dict:
    """通过 akshare stock_hk_hist 获取港股 K 线"""
    if not _AKSHARE_OK:
        return {"error": "akshare 不可用"}

    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=days + 5)).strftime("%Y%m%d")

    result = {"as_of": datetime.now().isoformat(), "source": "akshare", "stocks": {}}
    for code in codes:
        try:
            df = ak.stock_hk_hist(
                symbol=code, period="daily",
                start_date=start, end_date=end, adjust="qfq",
            )
            if df.empty:
                result["stocks"][code] = {"error": "akshare: 无数据"}
                continue

            recent = df.tail(days)
            result["stocks"][code] = {
                "name": str(recent.iloc[-1].get("名称", "")),
                "recent_days": [
                    {
                        "date": str(row["日期"]),
                        "open": float(row["开盘"]),
                        "high": float(row["最高"]),
                        "low": float(row["最低"]),
                        "close": float(row["收盘"]),
                        "volume": float(row["成交量"]),
                        "change_pct": float(row.get("涨跌幅", 0) or 0),
                    }
                    for _, row in recent.iterrows()
                ],
            }
        except Exception as e:
            result["stocks"][code] = {"error": f"akshare: {e}"}

    return result


# ═══════════════════════════════════════════════════════════════════════════
# 通道 2: Wind MCP stock_data (降级)
# ═══════════════════════════════════════════════════════════════════════════

# 港股代码 → Wind 代码后缀规则
# portfolio.json 用 .HK，Wind 也用 .HK
# 例外：部分港股 Wind 用 4 位代码（如 0700.HK），输入可能缺前导零
def _to_windcode(code: str) -> str:
    """将 portfolio 代码转为 Wind 标准 windcode（补齐 5 位前导零）"""
    numeric = code.lstrip("0") or "0"
    return f"{int(numeric):05d}.HK"


def _windcode_to_code(windcode: str) -> str:
    """将 Wind 返回的 windcode（如 '0700.HK'）转为标准化 5 位代码（'00700'）"""
    numeric = windcode.replace(".HK", "").lstrip("0") or "0"
    return f"{int(numeric):05d}"


def fetch_spot_wind(codes: list[str]) -> dict:
    """通过 Wind MCP stock_data.get_stock_price_indicators 获取港股快照

    一次调用查询所有标的（最多 50 个），返回与 akshare 相同结构。
    """
    windcodes = ",".join(_to_windcode(c) for c in codes)
    data = _call_wind_mcp("stock_data", "get_stock_price_indicators", {
        "windcode": windcodes,
        "indexes": "最新交易日,最新成交价,前收盘价,今日开盘价,今日最高价,"
                    "今日最低价,成交量,成交额,涨跌幅,换手率",
    })

    if data.get("error"):
        return {"error": data["error"], "source": "wind_mcp", "stocks": {}}

    result = {"as_of": datetime.now().isoformat(), "source": "wind_mcp", "stocks": {}}

    columns = [c["name"] for c in data.get("data", {}).get("columns", [])]
    rows = data.get("data", {}).get("rows", [])

    for row in rows:
        r = dict(zip(columns, row))
        code = _windcode_to_code(r.get("Wind代码", ""))

        price = float(r.get("最新成交价", 0) or 0)
        prev = float(r.get("前收盘价", 0) or 0)
        change_pct = float(r.get("涨跌幅", 0) or 0)

        result["stocks"][code] = {
            "name": HK_NAME_MAP.get(code, ""),
            "price": price,
            "change_pct": change_pct,
            "change_amount": round(price - prev, 3),
            "volume": float(r.get("成交量", 0) or 0),
            "turnover": float(r.get("成交额", 0) or 0),
            "high": float(r.get("今日最高价", 0) or 0),
            "low": float(r.get("今日最低价", 0) or 0),
            "open": float(r.get("今日开盘价", 0) or 0),
            "prev_close": prev,
        }

    # 标记 Wind 未返回的标的
    for code in codes:
        if code not in result["stocks"]:
            result["stocks"][code] = {"error": "wind_mcp: 未返回"}

    return result


def fetch_history_wind(codes: list[str], days: int = 5) -> dict:
    """通过 Wind MCP stock_data.get_stock_kline 获取港股 K 线

    每个标的单独调用（K 线不支持批量），串行执行。
    """
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days + 5)).strftime("%Y-%m-%d")

    result = {"as_of": datetime.now().isoformat(), "source": "wind_mcp", "stocks": {}}

    for code in codes:
        windcode = _to_windcode(code)
        data = _call_wind_mcp("stock_data", "get_stock_kline", {
            "windcode": windcode,
            "begin_date": start_date,
            "end_date": end_date,
            "period": "1d",
            "aftype": "0",
        })

        if "error" in data:
            result["stocks"][code] = {"error": f"wind_mcp: {data['error']}"}
            continue

        columns = [c["name"] for c in data.get("data", {}).get("columns", [])]
        rows = data.get("data", {}).get("rows", [])

        if not rows:
            result["stocks"][code] = {"error": "wind_mcp: 无数据"}
            continue

        recent = rows[-days:] if len(rows) > days else rows
        result["stocks"][code] = {
            "name": HK_NAME_MAP.get(code, ""),
            "recent_days": [
                {
                    "date": dict(zip(columns, row)).get("TIME", "")[:10],
                    "open": float(dict(zip(columns, row)).get("OPEN", 0) or 0),
                    "high": float(dict(zip(columns, row)).get("HIGH", 0) or 0),
                    "low": float(dict(zip(columns, row)).get("LOW", 0) or 0),
                    "close": float(dict(zip(columns, row)).get("MATCH", 0) or 0),
                    "volume": float(dict(zip(columns, row)).get("VOLUME", 0) or 0),
                    "turnover": float(dict(zip(columns, row)).get("TURNOVER", 0) or 0),
                }
                for row in recent
            ],
        }

    return result


# ═══════════════════════════════════════════════════════════════════════════
# 双通道调度器
# ═══════════════════════════════════════════════════════════════════════════

def fetch_spot(codes: list[str]) -> dict:
    """获取港股快照：akshare → Wind MCP 降级"""
    # 通道 1: akshare
    result = fetch_spot_akshare(codes)
    if "error" not in result:
        return result

    akshare_err = result["error"]
    print(f"  [WARN] akshare failed: {akshare_err}")

    # 通道 2: Wind MCP
    print(f"  -> fallback Wind MCP stock_data...")
    result = fetch_spot_wind(codes)
    if "error" not in result:
        print(f"  [OK] Wind MCP fallback succeeded")
        return result

    wind_err = result["error"]
    print(f"  [FAIL] Wind MCP also failed: {wind_err}")
    return {
        "as_of": datetime.now().isoformat(),
        "source": "none",
        "error": f"akshare: {akshare_err}; wind_mcp: {wind_err}",
        "stocks": {c: {"error": "all channels failed"} for c in codes},
    }


def fetch_history(codes: list[str], days: int = 5) -> dict:
    """获取港股 K 线：akshare → Wind MCP 降级"""
    result = fetch_history_akshare(codes, days)
    if "error" not in result:
        return result

    akshare_err = result["error"]
    print(f"  [WARN] akshare failed: {akshare_err}")

    print(f"  -> fallback Wind MCP stock_data...")
    result = fetch_history_wind(codes, days)
    if "error" not in result:
        print(f"  [OK] Wind MCP fallback succeeded")
        return result

    wind_err = result["error"]
    print(f"  [FAIL] Wind MCP also failed: {wind_err}")
    return {
        "as_of": datetime.now().isoformat(),
        "source": "none",
        "error": f"akshare: {akshare_err}; wind_mcp: {wind_err}",
        "stocks": {c: {"error": "all channels failed"} for c in codes},
    }


# ═══════════════════════════════════════════════════════════════════════════
# main
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="港股行情数据桥接（akshare → Wind MCP 双通道）")
    parser.add_argument("--codes", help="逗号分隔的港股代码（如 00700,01810）")
    parser.add_argument("--days", type=int, default=1, help="历史 K 线天数（默认 1=仅当日）")
    parser.add_argument("--output", help="输出 JSON 路径（默认 data/output/hk_quotes.json）")
    parser.add_argument("--history", action="store_true", help="获取 K 线历史（默认仅快照）")
    args = parser.parse_args()

    # ── 确定代码列表 ──
    if args.codes:
        codes = [c.strip() for c in args.codes.split(",")]
    else:
        pf_path = ROOT / "portfolio.json"
        if not pf_path.exists():
            print("ERROR: portfolio.json 不存在，请用 --codes 指定代码")
            sys.exit(1)
        codes = extract_hk_codes(pf_path)

    if not codes:
        print("INFO: 无港股标的，跳过")
        return

    print(f"[港股行情] 获取 {len(codes)} 只港股: {', '.join(codes)}")

    # ── 获取数据 ──
    if args.history:
        result = fetch_history(codes, args.days)
    else:
        result = fetch_spot(codes)

    # ── 保存 ──
    output_path = Path(args.output) if args.output else HK_QUOTES_PATH
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)

    # ── 摘要 ──
    source = result.get("source", "unknown")
    stocks = result.get("stocks", {})
    ok = sum(1 for v in stocks.values() if "error" not in v)
    failed_codes = [c for c, v in stocks.items() if "error" in v]

    if ok == len(stocks):
        print(f"[港股行情] OK {ok}/{len(stocks)} [{source}] -> {output_path}")
    elif ok > 0:
        print(f"[港股行情] PARTIAL {ok}/{len(stocks)} [{source}], failed: {failed_codes} -> {output_path}")
    else:
        print(f"[港股行情] FAIL all channels [{source}]: {failed_codes} -> {output_path}")
        sys.exit(2)

    for code, info in stocks.items():
        if "error" not in info:
            print(f"  {code} {info.get('name','')}: {info.get('price','?')} "
                  f"({info.get('change_pct',0):+.2f}%)")
        else:
            print(f"  {code} ERROR: {info['error']}")


if __name__ == "__main__":
    main()