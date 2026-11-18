# SPDX-License-Identifier: MIT
# sector_data.py — 板块数据采集器
#
# 数据源降级链:
#   1. akshare ak.index_hist_sw() — 首选，免费但走东财 API，间歇封锁
#   2. Wind MCP index_data.get_index_kline — 次选，需 WIND_API_KEY，有额度限制
#   3. baostock 合成指数 — 兜底，用个股日线 + 行业分类加权聚合
#
# akshare 接口: ak.index_hist_sw(symbol='801010', period='day')
#   返回列: 'date', 'open', 'high', 'low', 'close', 'volume', 'amount', ...

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

import akshare as ak

# ── 项目路径 ────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "sector"

# ── Wind MCP CLI 路径 ──────────────────────────────────────────

_WIND_MCP_CLI = Path(
    os.environ.get(
        "WIND_MCP_CLI",
        str(Path.home() / ".agents" / "skills" / "wind-mcp-skill" / "scripts" / "cli.mjs"),
    )
)

# 东财 API 连续失败阈值（达到后切换到 Wind MCP）
_CONSECUTIVE_AK_FAIL_THRESHOLD = 3

# ── 申万 31 个一级行业代码映射 ───────────────────────────────────

SW_SECTOR_CODES: Dict[str, Dict[str, str]] = {
    "801010": {"name": "农林牧渔", "windcode": "801010.SI", "category": "cycle"},
    "801020": {"name": "采掘", "windcode": "801020.SI", "category": "cycle"},
    "801030": {"name": "化工", "windcode": "801030.SI", "category": "cycle"},
    "801040": {"name": "钢铁", "windcode": "801040.SI", "category": "cycle"},
    "801050": {"name": "有色金属", "windcode": "801050.SI", "category": "cycle"},
    "801080": {"name": "电子", "windcode": "801080.SI", "category": "growth"},
    "801110": {"name": "家用电器", "windcode": "801110.SI", "category": "consumer"},
    "801120": {"name": "食品饮料", "windcode": "801120.SI", "category": "consumer"},
    "801130": {"name": "纺织服装", "windcode": "801130.SI", "category": "consumer"},
    "801140": {"name": "轻工制造", "windcode": "801140.SI", "category": "consumer"},
    "801150": {"name": "医药生物", "windcode": "801150.SI", "category": "defense"},
    "801160": {"name": "公用事业", "windcode": "801160.SI", "category": "defense"},
    "801170": {"name": "交通运输", "windcode": "801170.SI", "category": "cycle"},
    "801180": {"name": "房地产", "windcode": "801180.SI", "category": "cycle"},
    "801200": {"name": "商业贸易", "windcode": "801200.SI", "category": "consumer"},
    "801210": {"name": "休闲服务", "windcode": "801210.SI", "category": "consumer"},
    "801230": {"name": "综合", "windcode": "801230.SI", "category": "other"},
    "801710": {"name": "建筑材料", "windcode": "801710.SI", "category": "cycle"},
    "801720": {"name": "建筑装饰", "windcode": "801720.SI", "category": "cycle"},
    "801730": {"name": "电气设备", "windcode": "801730.SI", "category": "growth"},
    "801740": {"name": "国防军工", "windcode": "801740.SI", "category": "defense"},
    "801750": {"name": "计算机", "windcode": "801750.SI", "category": "growth"},
    "801760": {"name": "传媒", "windcode": "801760.SI", "category": "growth"},
    "801770": {"name": "通信", "windcode": "801770.SI", "category": "growth"},
    "801780": {"name": "银行", "windcode": "801780.SI", "category": "financial"},
    "801790": {"name": "非银金融", "windcode": "801790.SI", "category": "financial"},
    "801880": {"name": "汽车", "windcode": "801880.SI", "category": "cycle"},
    "801890": {"name": "机械设备", "windcode": "801890.SI", "category": "cycle"},
    "801950": {"name": "石油石化", "windcode": "801950.SI", "category": "cycle"},
    "801960": {"name": "电力设备", "windcode": "801960.SI", "category": "growth"},
    "801970": {"name": "环保", "windcode": "801970.SI", "category": "defense"},
}

# 防御板块（risk_off 时允许）
DEFENSE_SECTORS = {"801150", "801160", "801120", "801780"}
DEFENSE_SECTOR_NAMES = ["医药生物", "公用事业", "食品饮料", "银行"]


# ── Wind MCP 数据采集 ───────────────────────────────────────────


def _fetch_wind_mcp_kline(windcode: str, begin_date: str, end_date: str) -> pd.DataFrame:
    """通过 Wind MCP index_data.get_index_kline 获取指数 K 线

    Args:
        windcode: Wind 代码，如 801080.SI
        begin_date: 起始日期 yyyyMMdd
        end_date: 结束日期 yyyyMMdd

    Returns:
        DataFrame 标准列: date, open, high, low, close, volume, amount, turnover
    """
    if not _WIND_MCP_CLI.exists():
        raise FileNotFoundError(f"Wind MCP CLI 不存在: {_WIND_MCP_CLI}")

    params = json.dumps({
        "windcode": windcode,
        "begin_date": begin_date,
        "end_date": end_date,
    })

    result = subprocess.run(
        ["node", str(_WIND_MCP_CLI), "call", "index_data", "get_index_kline", params],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )

    if result.returncode != 0:
        stderr = result.stderr[:200] if result.stderr else ""
        raise RuntimeError(f"Wind MCP CLI exit={result.returncode}: {stderr}")

    # 解析外层 JSON（CLI 包装格式）
    outer = json.loads(result.stdout)
    if outer.get("isError"):
        error_text = ""
        for c in outer.get("content", []):
            if c.get("type") == "text":
                error_text += c.get("text", "")
        raise RuntimeError(f"Wind MCP 返回错误: {error_text[:300]}")

    # 提取内层数据
    for c in outer.get("content", []):
        if c.get("type") == "text":
            inner = json.loads(c["text"])
            if inner.get("error"):
                raise RuntimeError(f"Wind MCP 数据错误: {inner['error']}")
            break
    else:
        raise RuntimeError("Wind MCP 返回格式异常：无 text content")

    data_block = inner.get("data", {})
    columns = [col["name"] for col in data_block.get("columns", [])]
    rows = data_block.get("rows", [])

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=columns)

    # Wind MCP 列映射: TIME→date, OPEN→open, MATCH→close, HIGH→high,
    #   LOW→low, TURNOVER→amount, VOLUME→volume, CHANGEHANDRATE→turnover
    col_map = {
        "TIME": "date",
        "OPEN": "open",
        "MATCH": "close",
        "HIGH": "high",
        "LOW": "low",
        "TURNOVER": "amount",
        "VOLUME": "volume",
        "CHANGEHANDRATE": "turnover",
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

    # 类型转换
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    for col in ["open", "close", "high", "low", "volume", "amount", "turnover"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df.dropna(subset=["open", "close", "high", "low"], how="all")


def _retry_akshare(func, max_retries: int = 3, base_delay: float = 2.0, **kwargs):
    """带指数退避的 akshare 重试包装器"""
    last_exc = None
    for attempt in range(max_retries):
        try:
            return func(**kwargs)
        except Exception as e:
            last_exc = e
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                print(f"  [RETRY {attempt+1}/{max_retries}] {e} — waiting {delay:.0f}s")
                time.sleep(delay)
    raise last_exc


# ── baostock 合成指数 ─────────────────────────────────────────────
# CSRC (证监会) 行业 → 申万一级行业 映射表
# 基于 baostock query_stock_industry() 返回的 84 个 CSRC 细类

_CSRC_TO_SW: Dict[str, str] = {}

def _build_csrc_sw_map():
    """构建 CSRC 代码 → SW 行业代码 映射"""
    if _CSRC_TO_SW:
        return _CSRC_TO_SW

    # 按 CSRC 代码前缀映射到 SW 31 个行业
    mapping = {
        # 农林牧渔(801010): A01-A05 + C13(农副食品加工)
        "A01": "801010", "A02": "801010", "A03": "801010",
        "A04": "801010", "A05": "801010", "C13": "801010",
        # 采掘(801020): B06-B12
        "B06": "801020", "B07": "801950",  # B07(石油开采)→石油石化
        "B08": "801040",  # B08(黑色矿采)→钢铁
        "B09": "801050", "B10": "801020", "B11": "801020",
        # 化工(801030): C26-C29
        "C26": "801030", "C28": "801030", "C29": "801030",
        # 钢铁(801040): C31
        "C31": "801040",
        # 有色金属(801050): C32-C33
        "C32": "801050", "C33": "801050",
        # 电子(801080): C39
        "C39": "801080",
        # 食品饮料(801120): C14-C15
        "C14": "801120", "C15": "801120",
        # 纺织服装(801130): C17-C19
        "C17": "801130", "C18": "801130", "C19": "801130",
        # 轻工制造(801140): C20-C24
        "C20": "801140", "C21": "801140", "C22": "801140",
        "C23": "801140", "C24": "801140",
        # 医药生物(801150): C27 + Q83-Q84
        "C27": "801150", "Q83": "801150", "Q84": "801150",
        # 公用事业(801160): D44-D46 + N76 + N78
        "D44": "801160", "D45": "801160", "D46": "801160",
        "N76": "801160", "N78": "801160",
        # 交通运输(801170): G53-G60
        "G53": "801170", "G54": "801170", "G55": "801170",
        "G56": "801170", "G58": "801170", "G59": "801170",
        "G60": "801170",
        # 房地产(801180): K70
        "K70": "801180",
        # 商业贸易(801200): F51-F52
        "F51": "801200", "F52": "801200",
        # 休闲服务(801210): H61-H62 + R88(体育)
        "H61": "801210", "H62": "801210", "R88": "801210",
        # 建筑材料(801710): C30
        "C30": "801710",
        # 建筑装饰(801720): E47-E50
        "E47": "801720", "E48": "801720", "E49": "801720",
        "E50": "801720",
        # 电气设备(801730): C38 (includes 家用电器 + 电力设备 as subset)
        "C38": "801730",
        # 国防军工(801740): C37
        "C37": "801740",
        # 计算机(801750): I64-I65
        "I64": "801750", "I65": "801750",
        # 传媒(801760): R86-R87
        "R86": "801760", "R87": "801760",
        # 通信(801770): I63
        "I63": "801770",
        # 银行(801780): J66
        "J66": "801780",
        # 非银金融(801790): J67-J69
        "J67": "801790", "J68": "801790", "J69": "801790",
        # 汽车(801880): C36
        "C36": "801880",
        # 机械设备(801890): C34-C35 + C40 + C43
        "C34": "801890", "C35": "801890", "C40": "801890",
        "C43": "801890",
        # 石油石化(801950): C25
        "C25": "801950",
        # 环保(801970): C42 + N77
        "C42": "801970", "N77": "801970",
        # 综合(801230): 兜底杂项
        "C41": "801230", "L71": "801230", "L72": "801230",
        "M73": "801230", "M74": "801230", "M75": "801230",
        "O81": "801230", "P83": "801230", "S91": "801230",
        "R89": "801230",
    }
    _CSRC_TO_SW.update(mapping)
    return _CSRC_TO_SW


def _extract_csrc_code(industry_str: str) -> str:
    """从 baostock industry 字符串提取 CSRC 代码前缀，如 'C39计算机...' → 'C39'"""
    if not industry_str or not isinstance(industry_str, str):
        return ""
    # CSRC 代码格式: 1字母 + 2数字
    for i in range(min(4, len(industry_str))):
        if i >= 2 and industry_str[i].isdigit():
            continue
        if i == 3 and industry_str[:i].isalnum():
            return industry_str[:i]
        if i >= 1 and industry_str[0].isalpha() and industry_str[1:3].isdigit():
            return industry_str[:3]
    # Fallback: try first 3 chars
    prefix = industry_str[:3]
    if prefix and prefix[0].isalpha() and prefix[1:].isdigit():
        return prefix
    return ""


def _fetch_baostock_synthetic(
    sw_code: str,
    begin_date: str,
    end_date: str,
    csv_dir: Optional[Path] = None,
) -> pd.DataFrame:
    """Tier 3 兜底: 用 baostock 个股日线 + CSRC→SW 行业映射自建合成指数

    流程:
      1. baostock query_stock_industry() → 全市场 CSRC 行业分类
      2. CSRC→SW 映射 → 按 31 个申万行业分组
      3. 读取 Qlib CSV 目录下的个股日线数据
      4. 按板块等权聚合 OHLCV → 合成指数

    Args:
        sw_code: 申万行业代码，如 801080
        begin_date: yyyyMMdd
        end_date: yyyyMMdd
        csv_dir: Qlib CSV 源目录（默认 D:/qlib/qlib_data/source/cn_1d）

    Returns:
        DataFrame 标准列: date, open, high, low, close, volume, amount
    """
    import baostock as bs

    if csv_dir is None:
        csv_dir = Path("D:/qlib/qlib_data/source/cn_1d")
    if not csv_dir.exists():
        raise FileNotFoundError(f"Qlib CSV 目录不存在: {csv_dir}")

    _build_csrc_sw_map()

    # Step 1: 获取全市场行业分类
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock 登录失败: {lg.error_msg}")

    try:
        rs = bs.query_stock_industry()
        industry_rows = []
        while rs.next():
            industry_rows.append(rs.get_row_data())
    finally:
        bs.logout()

    ind_df = pd.DataFrame(industry_rows, columns=rs.fields)
    ind_df["csrc_code"] = ind_df["industry"].apply(_extract_csrc_code)
    ind_df["sw_code"] = ind_df["csrc_code"].map(_CSRC_TO_SW)

    # Step 2: 筛选目标板块的成份股
    sector_stocks = ind_df[ind_df["sw_code"] == sw_code]["code"].tolist()
    if not sector_stocks:
        raise ValueError(f"baostock 中找不到映射到 {sw_code} 的个股")

    # baostock code 格式: sh.600004 → qlib symbol: sh600004
    symbols = {s.replace(".", ""): s for s in sector_stocks}

    # Step 3: 读取个股日线数据并聚合
    begin_dt = pd.to_datetime(begin_date, format="%Y%m%d")
    end_dt = pd.to_datetime(end_date, format="%Y%m%d")

    sector_daily: Dict[pd.Timestamp, List[Dict[str, float]]] = {}
    loaded = 0

    for qlib_sym, bs_sym in symbols.items():
        csv_path = csv_dir / f"{qlib_sym}.csv"
        if not csv_path.exists():
            continue
        try:
            stock_df = pd.read_csv(csv_path, parse_dates=["date"])
            stock_df = stock_df[
                (stock_df["date"] >= begin_dt) & (stock_df["date"] <= end_dt)
            ]
            for _, row in stock_df.iterrows():
                d = row["date"]
                if d not in sector_daily:
                    sector_daily[d] = []
                sector_daily[d].append({
                    "open": float(row.get("open", 0)),
                    "close": float(row.get("close", 0)),
                    "high": float(row.get("high", 0)),
                    "low": float(row.get("low", 0)),
                    "volume": float(row.get("volume", 0)),
                    "amount": float(row.get("amount", 0)),
                })
            loaded += 1
        except Exception:
            continue

    if not sector_daily:
        raise ValueError(f"板块 {sw_code} 无可用成分股数据 ({loaded}/{len(symbols)} loaded)")

    # Step 4: 等权聚合 + 基期归一化
    rows = []
    for d in sorted(sector_daily.keys()):
        stocks = sector_daily[d]
        n = len(stocks)
        if n < 3:  # 最少 3 只成分股
            continue
        rows.append({
            "date": d,
            "open": sum(s["open"] for s in stocks) / n,
            "close": sum(s["close"] for s in stocks) / n,
            "high": sum(s["high"] for s in stocks) / n,
            "low": sum(s["low"] for s in stocks) / n,
            "volume": sum(s["volume"] for s in stocks),
            "amount": sum(s["amount"] for s in stocks),
        })

    if not rows:
        raise ValueError(f"板块 {sw_code} 无合格日期 (min 3 stocks/day)")

    result = pd.DataFrame(rows)

    # 归一化: 以首日 close 为 1000 基点
    if len(result) > 0:
        base = result["close"].iloc[0]
        if base > 0:
            for col in ["open", "close", "high", "low"]:
                result[col] = result[col] / base * 1000.0
            # volume/amount 不归一化 — 保持原始总额

    result["turnover"] = np.nan
    return result.sort_values("date").reset_index(drop=True)


def _retry_akshare(func, max_retries: int = 3, base_delay: float = 2.0, **kwargs):
    """带指数退避的 akshare 重试包装器"""
    last_exc = None
    for attempt in range(max_retries):
        try:
            return func(**kwargs)
        except Exception as e:
            last_exc = e
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                print(f"  [RETRY {attempt+1}/{max_retries}] {e} — waiting {delay:.0f}s")
                time.sleep(delay)
    raise last_exc


class SectorDataCollector:
    """板块数据采集器

    数据源降级链:
      1. akshare ak.index_hist_sw() → 免费，走东财 API
      2. Wind MCP index_data.get_index_kline → 需 WIND_API_KEY
      3. baostock 合成指数（待实现）

    东财 API 连续失败 _CONSECUTIVE_AK_FAIL_THRESHOLD 次后自动切换。
    """

    # 全局降级状态（跨实例共享）
    _consecutive_ak_fails: int = 0
    _use_wind_mcp: bool = False

    def __init__(
        self,
        data_dir: Optional[Path] = None,
    ):
        self.data_dir = data_dir or DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def collect_sector_kline(
        self,
        sw_code: str,
        begin_date: str = "20100101",
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        """采集单个板块指数 K 线

        降级链: akshare → Wind MCP

        Args:
            sw_code: 申万行业代码，如 801010
            begin_date: 起始日期 yyyyMMdd
            end_date: 结束日期 yyyyMMdd

        Returns:
            DataFrame with standard columns: date, open, high, low, close, volume, amount
        """
        if end_date is None:
            end_date = datetime.now().strftime("%Y%m%d")

        sector_info = SW_SECTOR_CODES.get(sw_code)
        if sector_info is None:
            raise ValueError(f"Unknown sector code: {sw_code}")

        df = None

        # ── Tier 1: akshare ──
        if not SectorDataCollector._use_wind_mcp:
            try:
                df = self._fetch_akshare_sector(sw_code)
            except Exception as e:
                SectorDataCollector._consecutive_ak_fails += 1
                print(f"  ⚠️ akshare 失败 ({SectorDataCollector._consecutive_ak_fails}/{_CONSECUTIVE_AK_FAIL_THRESHOLD}): {e}")
                if SectorDataCollector._consecutive_ak_fails >= _CONSECUTIVE_AK_FAIL_THRESHOLD:
                    SectorDataCollector._use_wind_mcp = True
                    print(f"  🔄 切换到 Wind MCP（akshare 连续失败 {SectorDataCollector._consecutive_ak_fails} 次）")

        # ── Tier 2: Wind MCP ──
        if df is None and SectorDataCollector._use_wind_mcp:
            windcode = sector_info["windcode"]
            try:
                df = _fetch_wind_mcp_kline(windcode, begin_date, end_date)
                print(f"  ✅ Wind MCP: {windcode} → {len(df)} rows")
            except Exception as e:
                print(f"  ⚠️ Wind MCP 失败: {e}")

        # ── Tier 3: baostock 合成指数 ──
        if df is None or df.empty:
            try:
                df = _fetch_baostock_synthetic(sw_code, begin_date, end_date)
                n_stocks = len(df)  # rough indicator
                print(f"  🔧 baostock 合成: {sw_code} → {len(df)} rows")
            except Exception as e:
                print(f"  ❌ baostock 合成也失败: {e}")

        if df is None or df.empty:
            raise RuntimeError(
                f"所有数据源均无法获取 {sw_code} 数据 "
                f"(akshare_blocked={SectorDataCollector._use_wind_mcp})"
            )

        # 标准化 + 标记
        df["sw_code"] = sw_code
        df["sector_name"] = sector_info["name"]
        df["category"] = sector_info["category"]

        # 按日期范围过滤
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
            begin_dt = pd.to_datetime(begin_date, format="%Y%m%d")
            end_dt = pd.to_datetime(end_date, format="%Y%m%d")
            df = df[(df["date"] >= begin_dt) & (df["date"] <= end_dt)]
            df = df.sort_values("date").reset_index(drop=True)

        return df

    def _fetch_akshare_sector(self, sw_code: str) -> pd.DataFrame:
        """Tier 1: akshare index_hist_sw"""
        raw = _retry_akshare(
            ak.index_hist_sw,
            symbol=sw_code,
            period="day",
        )
        df = self._normalize_akshare_columns(raw)
        # akshare 成功 → 重置计数器
        SectorDataCollector._consecutive_ak_fails = 0
        return df

    def collect_all_sectors(
        self,
        begin_date: str = "20100101",
        sector_codes: Optional[List[str]] = None,
    ) -> Dict[str, pd.DataFrame]:
        """采集全部板块指数数据

        Args:
            begin_date: 起始日期
            sector_codes: 指定板块代码列表，默认全部 31 个

        Returns:
            {sw_code: DataFrame}
        """
        codes = sector_codes or list(SW_SECTOR_CODES.keys())
        results = {}

        for sw_code in codes:
            name = SW_SECTOR_CODES.get(sw_code, {}).get("name", "?")
            try:
                df = self.collect_sector_kline(sw_code, begin_date)
                results[sw_code] = df
                self._save_cache(sw_code, df)
                print(f"[OK] {sw_code} {name}: {len(df)} rows, {df['date'].min().date()} ~ {df['date'].max().date()}")
            except Exception as e:
                print(f"[FAIL] {sw_code} {name}: {e}")

        return results

    # ── 缓存管理 ──────────────────────────────────────────────

    def _save_cache(self, sw_code: str, df: pd.DataFrame):
        # Validate before saving: refuse to write all-zero data
        if "close" in df.columns and len(df) > 0:
            last_n = df["close"].tail(5)
            if (last_n == 0).all():
                print(f"  ⚠️ REFUSING to save {sw_code}: last 5 rows all zero (data fetch likely failed)")
                return
        path = self.data_dir / f"sector_{sw_code}.csv"
        df.to_csv(path, index=False)

    def load_cache(self, sw_code: str) -> Optional[pd.DataFrame]:
        path = self.data_dir / f"sector_{sw_code}.csv"
        if path.exists():
            return pd.read_csv(path, parse_dates=["date"])
        return None

    def load_all_cached(self) -> Dict[str, pd.DataFrame]:
        """加载所有已缓存的板块数据"""
        results = {}
        for sw_code in SW_SECTOR_CODES:
            df = self.load_cache(sw_code)
            if df is not None:
                results[sw_code] = df
        return results

    def build_price_matrix(self) -> pd.DataFrame:
        """构建 31 个板块的价格矩阵（close 列）"""
        all_data = self.load_all_cached()
        dfs = {}
        for sw_code, df in all_data.items():
            if "close" not in df.columns:
                continue
            temp_df = df.copy()
            if "date" in temp_df.columns:
                temp_df = temp_df.set_index("date")
            dfs[sw_code] = temp_df["close"]

        if not dfs:
            return pd.DataFrame()

        prices = pd.DataFrame(dfs)
        prices = prices.sort_index()
        return prices.ffill(limit=7)

    # ── 列标准化 ──────────────────────────────────────────────

    @staticmethod
    def _normalize_akshare_columns(df: pd.DataFrame) -> pd.DataFrame:
        """将 akshare index_hist_sw 返回的列名标准化

        akshare index_hist_sw 列名: '代码', '日期', '开盘', '最高', '最低', '收盘', '成交量', '成交额', ...
        标准化为: 'date', 'open', 'high', 'low', 'close', 'volume', 'amount'
        """
        col_map = {
            "代码": "sw_index_code",
            "日期": "date",
            "开盘": "open",
            "最高": "high",
            "最低": "low",
            "收盘": "close",
            "成交量": "volume",
            "成交额": "amount",
            "涨跌幅": "pct_change",
            "涨跌额": "change",
            "换手率": "turnover",
        }
        # 仅重命名存在的列
        rename = {k: v for k, v in col_map.items() if k in df.columns}
        df = df.rename(columns=rename)

        # 如果 'date' 不存在但 'index' 是 DatetimeIndex
        if "date" not in df.columns and isinstance(df.index, pd.DatetimeIndex):
            df = df.reset_index()
            if "index" in df.columns:
                df = df.rename(columns={"index": "date"})

        # 删除冗余的 sw_index_code（我们已有 sw_code）
        if "sw_index_code" in df.columns:
            df = df.drop(columns=["sw_index_code"])

        return df

    def get_defense_sectors(self) -> List[str]:
        """获取防御板块代码列表"""
        return list(DEFENSE_SECTORS)

    def get_sector_metadata(self) -> pd.DataFrame:
        """获取板块元数据 DataFrame"""
        rows = []
        for code, info in SW_SECTOR_CODES.items():
            rows.append({
                "sw_code": code,
                "name": info["name"],
                "windcode": info["windcode"],
                "category": info["category"],
            })
        return pd.DataFrame(rows)


# ── Qlib DataHandler 适配 ──────────────────────────────────────


class SectorDataHandler:
    """板块数据 Qlib DataHandler 适配器

    将板块数据转换为 Qlib 兼容的格式。
    用于 sector_model.py 中的模型训练。
    """

    def __init__(
        self,
        collector: Optional[SectorDataCollector] = None,
        instruments: str = "sw_sectors",
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        **kwargs,
    ):
        self.collector = collector or get_sector_collector()
        self.instruments = instruments
        self.start_time = start_time or "2010-01-01"
        self.end_time = end_time or datetime.now().strftime("%Y-%m-%d")
        self.kwargs = kwargs

    def get_price_matrix(self) -> pd.DataFrame:
        """获取价格矩阵"""
        return self.collector.build_price_matrix()

    def get_return_matrix(self) -> pd.DataFrame:
        """获取收益率矩阵"""
        prices = self.get_price_matrix()
        return prices.pct_change().dropna()

    def get_sector_names(self) -> Dict[str, str]:
        """获取板块代码 → 名称映射"""
        return {code: info["name"] for code, info in SW_SECTOR_CODES.items()}

    def get_sector_categories(self) -> Dict[str, str]:
        """获取板块代码 → 类别映射"""
        return {code: info["category"] for code, info in SW_SECTOR_CODES.items()}


# ── 模块级单例 ──────────────────────────────────────────────────

_collector: Optional[SectorDataCollector] = None


def get_sector_collector() -> SectorDataCollector:
    global _collector
    if _collector is None:
        _collector = SectorDataCollector()
    return _collector