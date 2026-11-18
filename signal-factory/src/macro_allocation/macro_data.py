# SPDX-License-Identifier: MIT
# macro_data.py — Layer 0 macro asset allocation data collection
#
# Data sources: akshare (free, no quota limits)
# Collects: 9 macro EDB indicators, 6 ETF k-lines, 2 benchmark index k-lines
# Output: data/macro_assets/
#
# Cache strategy:
#   - Daily data: reload if cache older than 24 hours
#   - Monthly data: reload if cache older than 7 days

from __future__ import annotations

import json
import time as _time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import akshare as ak

# ── Constants ─────────────────────────────────────────────────────
PROVIDER = "akshare"
DEFAULT_DATA_DIR = Path("d:/AgentAlpha/signal-factory/data/macro_assets")

# Wind CSV export → internal indicator key mapping
# Wind exports have metadata rows with indicator names in Chinese; we map
# those to the short keys that asset_scorer.py and the EDB cache use.
WIND_INDICATOR_NAME_MAP: Dict[str, str] = {
    "中债国债即期收益率:10年": "10Y国债收益率",
    "中债国债即期收益率:2年":  "2Y国债收益率",
    "中债国债即期收益率:5年":  "5Y国债收益率",
    "中债国债即期收益率:1年":  "1Y国债收益率",
}
_RETRY_COUNT = 3
_BACKOFF_BASE = 2  # seconds

# Cache TTL
CACHE_TTL_DAILY = timedelta(hours=24)       # daily data: reload if >24h old
CACHE_TTL_MONTHLY = timedelta(days=7)        # monthly data: reload if >7d old
CACHE_TTL_QUARTERLY = timedelta(days=14)     # quarterly data: reload if >14d old


# ── Asset Universe ────────────────────────────────────────────────

@dataclass
class AssetUniverse:
    """7 asset classes and their trading vehicles"""

    asset_id: str
    asset_name: str
    asset_category: str  # equity / commodity / bond / cash
    primary_etf: Optional[str] = None
    fallback_etf: Optional[str] = None
    underlying_index: Optional[str] = None
    proxy_data: Optional[str] = None
    currency: str = "CNY"

    @property
    def is_overseas(self) -> bool:
        return self.asset_id in ("nasdaq", "hk_tech", "nikkei", "india")

    @property
    def is_commodity(self) -> bool:
        return self.asset_id in ("oil", "gold")


ASSET_UNIVERSE: Dict[str, AssetUniverse] = {
    "oil": AssetUniverse(
        asset_id="oil",
        asset_name="华宝油气",
        asset_category="commodity",
        primary_etf="162411",
        fallback_etf="501018",
        proxy_data="WTI原油价格 x USD/CNY",
    ),
    "gold": AssetUniverse(
        asset_id="gold",
        asset_name="华安黄金ETF",
        asset_category="commodity",
        primary_etf="518880",
        fallback_etf="159937",
        proxy_data="伦敦金价格 x USD/CNY",
    ),
    "bond": AssetUniverse(
        asset_id="bond",
        asset_name="国债ETF",
        asset_category="bond",
        primary_etf="511010",
        fallback_etf="511260",
        proxy_data="中债10Y国债总回报指数",
    ),
    "nasdaq": AssetUniverse(
        asset_id="nasdaq",
        asset_name="纳指ETF",
        asset_category="equity",
        primary_etf="513100",
        fallback_etf="159941",
        underlying_index="NDX",
        proxy_data="NASDAQ-100 x USD/CNY",
    ),
    "hk_tech": AssetUniverse(
        asset_id="hk_tech",
        asset_name="恒生科技ETF",
        asset_category="equity",
        primary_etf="513130",
        underlying_index="HSTECH.HI",
        proxy_data="恒生科技指数 (HSTECH)",
    ),
    "a_share": AssetUniverse(
        asset_id="a_share",
        asset_name="沪深300ETF",
        asset_category="equity",
        primary_etf="159919",
        underlying_index="000300.SH",
        proxy_data="沪深300指数",
    ),
    "cash": AssetUniverse(
        asset_id="cash",
        asset_name="现金/回购",
        asset_category="cash",
        proxy_data="1年期定存利率",
    ),
    # ── v1.2 新增 4 类资产 ──
    "star_50": AssetUniverse(
        asset_id="star_50",
        asset_name="科创50ETF",
        asset_category="equity",
        primary_etf="588000",
        underlying_index="000688.SH",
        proxy_data="科创50指数",
    ),
    "nikkei": AssetUniverse(
        asset_id="nikkei",
        asset_name="日经225ETF",
        asset_category="equity",
        primary_etf="513880",
        fallback_etf="513520",
        underlying_index="N225",
        proxy_data="日经225指数 (JPY)",
    ),
    "india": AssetUniverse(
        asset_id="india",
        asset_name="印度基金LOF",
        asset_category="equity",
        primary_etf="164824",
        underlying_index="SENSEX",
        proxy_data="印度SENSEX指数 (INR)",
    ),
    "cb_bond": AssetUniverse(
        asset_id="cb_bond",
        asset_name="转债ETF",
        asset_category="hybrid",
        primary_etf="511380",
        proxy_data="中证转债指数",
    ),
}


# ── Helpers ───────────────────────────────────────────────────────

def _parse_cn_month(series: pd.Series) -> pd.Series:
    """Parse Chinese month strings like '2026年08月份' to datetime64[ns]."""
    return pd.to_datetime(
        series.astype(str).str.extract(r"(\d{4})\s*年\s*(\d{1,2})\s*月")[0]
        .str.cat(
            series.astype(str)
            .str.extract(r"(\d{4})\s*年\s*(\d{1,2})\s*月")[1]
            .str.zfill(2)
        )
        .apply(lambda x: f"{x[:4]}-{x[4:]}-01" if len(str(x)) == 6 else None),
        errors="coerce",
    )


def _retry_akshare(func: Callable, *args: Any, **kwargs: Any) -> Any:
    """Execute an akshare call with exponential backoff retry."""
    last_error: Optional[Exception] = None
    for attempt in range(_RETRY_COUNT):
        try:
            if attempt > 0:
                wait = _BACKOFF_BASE * (2**attempt)
                _time.sleep(wait)
            return func(*args, **kwargs)
        except Exception as e:
            last_error = e
            msg = str(e)[:200]
            if attempt < _RETRY_COUNT - 1:
                print(f"    Retry {attempt + 1}/{_RETRY_COUNT}: {msg}")
    raise last_error  # type: ignore[misc]


# ── Cache helpers ─────────────────────────────────────────────────

def _is_cache_fresh(path: Path, ttl: timedelta) -> bool:
    """Check if a cache file exists and is within TTL."""
    if not path.exists():
        return False
    mtime = datetime.fromtimestamp(path.stat().st_mtime)
    return (datetime.now() - mtime) < ttl


def _load_cache_or_none(path: Path, ttl: timedelta) -> Optional[pd.DataFrame]:
    """Load CSV cache if fresh, else return None."""
    if _is_cache_fresh(path, ttl):
        try:
            return pd.read_csv(path, parse_dates=["date"], index_col="date")
        except Exception:
            return None
    return None


def _parse_wind_manual_csv(csv_path: Path) -> Optional[pd.DataFrame]:
    """Parse a Wind-format CSV export into a date-indexed DataFrame.

    Wind exports use a metadata-prefixed format without standard column headers:

        Wind,
        指标名称,中债国债即期收益率:10年
        频率,日
        单位,%
        指标ID,M1001110
        来源,中债估值中心
        2026-09-22,1.7034
        2026-09-21,1.7072
        ...

    Returns a DataFrame with ``date`` index and a single column named per
    ``WIND_INDICATOR_NAME_MAP`` (or the CSV filename stem as fallback).
    Returns None if the file is not a recognised Wind format.
    """
    try:
        raw_text = csv_path.read_text(encoding="utf-8-sig")
    except (UnicodeDecodeError, OSError):
        return None

    lines = raw_text.strip().splitlines()
    # Wind format: first line is "Wind," (possibly empty second field)
    if not lines or not lines[0].strip().startswith("Wind"):
        return None

    # ── Parse metadata ────────────────────────────────────────────
    wind_indicator_name: Optional[str] = None
    data_start: int = -1
    date_re = __import__("re").compile(r"^\d{4}-\d{2}-\d{2}")

    for i, line in enumerate(lines):
        parts = line.split(",", 1)
        key = parts[0].strip()
        val = parts[1].strip() if len(parts) > 1 else ""

        if key == "指标名称":
            wind_indicator_name = val
        elif date_re.match(key):
            data_start = i
            break

    if data_start < 0:
        return None  # no data rows found

    # ── Determine our internal indicator key ─────────────────────
    col_name: Optional[str] = None
    if wind_indicator_name and wind_indicator_name in WIND_INDICATOR_NAME_MAP:
        col_name = WIND_INDICATOR_NAME_MAP[wind_indicator_name]
    else:
        # Fallback: use filename stem (READ.ME convention: {指标名}.csv)
        col_name = csv_path.stem

    # ── Parse data rows ──────────────────────────────────────────
    records: list[tuple] = []
    for line in lines[data_start:]:
        parts = line.split(",", 1)
        if not parts:
            continue
        try:
            d = pd.Timestamp(parts[0].strip())
            v = float(parts[1].strip()) if len(parts) > 1 else None
            if v is not None:
                records.append((d, v))
        except (ValueError, TypeError):
            continue

    if not records:
        return None

    df = pd.DataFrame(records, columns=["date", col_name])
    df = df.set_index("date").sort_index()
    df.index.name = "date"
    return df


# ═══════════════════════════════════════════════════════════════════
# MacroDataCollector
# ═══════════════════════════════════════════════════════════════════


class MacroDataCollector:
    """Collects all macro, ETF, and index data for Layer 0 asset allocation.

    Data sources: akshare (zero-cost, no quota limits)
    - 9 macro EDB indicators (PMI, CPI, PPI, M2, social financing, GDP, IP, bond yield, SHIBOR)
    - 6 ETF daily k-lines
    - 2 benchmark index k-lines (CSI 300, STAR 50)
    - Bonus: CSI 300 PE, WTI oil proxy, gold price

    Output to data/macro_assets/ directory with CSV caching.
    """

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = data_dir or DEFAULT_DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)

    # ══════════════════════════════════════════════════════════════
    # Macro EDB Indicators (9 core + 3 bonus)
    # ══════════════════════════════════════════════════════════════

    def _collect_pmi(self) -> pd.DataFrame:
        """Manufacturing PMI — ak.macro_china_pmi()"""
        df = _retry_akshare(ak.macro_china_pmi)
        df = df.rename(columns={
            "月份": "date",
            "制造业-指数": "制造业PMI",
            "制造业-同比增长": "制造业PMI_同比",
            "非制造业-指数": "非制造业PMI",
            "非制造业-同比增长": "非制造业PMI_同比",
        })
        df["date"] = _parse_cn_month(df["date"])
        df = df.dropna(subset=["date"])
        return df.set_index("date").sort_index()

    def _collect_cpi(self) -> pd.DataFrame:
        """CPI YoY — ak.macro_china_cpi()"""
        df = _retry_akshare(ak.macro_china_cpi)
        # Determine column layout from the returned DataFrame
        if "商品" in df.columns:
            cpi = df[df["商品"].str.contains("同比", na=False)].copy()
            if cpi.empty:
                cpi = df.copy()
            cpi["date"] = pd.to_datetime(cpi["日期"], errors="coerce")
            cpi["CPI同比"] = pd.to_numeric(cpi["今值"], errors="coerce")
        else:
            cpi = df.copy()
            date_col = next((c for c in cpi.columns if "日期" in c or "date" in c.lower()), cpi.columns[0])
            cpi["date"] = pd.to_datetime(cpi[date_col], errors="coerce")
            val_col = next((c for c in cpi.columns if c not in (date_col, "date")), cpi.columns[1])
            cpi["CPI同比"] = pd.to_numeric(cpi[val_col], errors="coerce")
        cpi = cpi.dropna(subset=["date"])
        return cpi.set_index("date")[["CPI同比"]].sort_index()

    def _collect_ppi(self) -> pd.DataFrame:
        """PPI YoY — ak.macro_china_ppi()"""
        df = _retry_akshare(ak.macro_china_ppi)
        if "商品" in df.columns:
            ppi = df[df["商品"].str.contains("同比", na=False)].copy()
            if ppi.empty:
                ppi = df.copy()
            ppi["date"] = pd.to_datetime(ppi["日期"], errors="coerce")
            ppi["PPI同比"] = pd.to_numeric(ppi["今值"], errors="coerce")
        else:
            ppi = df.copy()
            date_col = next((c for c in ppi.columns if "日期" in c or "date" in c.lower()), ppi.columns[0])
            ppi["date"] = pd.to_datetime(ppi[date_col], errors="coerce")
            val_col = next((c for c in ppi.columns if c not in (date_col, "date")), ppi.columns[1])
            ppi["PPI同比"] = pd.to_numeric(ppi[val_col], errors="coerce")
        ppi = ppi.dropna(subset=["date"])
        return ppi.set_index("date")[["PPI同比"]].sort_index()

    def _collect_m2(self) -> pd.DataFrame:
        """M2 money supply — ak.macro_china_money_supply()"""
        df = _retry_akshare(ak.macro_china_money_supply)
        df = df.rename(columns={
            "月份": "date",
            "货币和准货币(M2)-数量(亿元)": "M2_余额_亿",
            "货币和准货币(M2)-同比增长": "M2同比",
            "货币和准货币(M2)-环比增长": "M2环比",
            "货币(M1)-数量(亿元)": "M1_余额_亿",
            "货币(M1)-同比增长": "M1同比",
            "流通中现金(M0)-数量(亿元)": "M0_余额_亿",
            "流通中现金(M0)-同比增长": "M0同比",
        })
        df["date"] = _parse_cn_month(df["date"])
        df = df.dropna(subset=["date"])
        for c in ["M2同比", "M1同比", "M0同比"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        return df.set_index("date").sort_index()

    def _collect_social_financing(self) -> pd.DataFrame:
        """Aggregate social financing — ak.macro_china_shrzgm()"""
        df = _retry_akshare(ak.macro_china_shrzgm)
        df = df.rename(columns={
            "月份": "date",
            "社会融资规模增量": "社融增量_亿",
        })
        df["date"] = pd.to_datetime(df["date"], format="%Y%m", errors="coerce")
        df = df.dropna(subset=["date"])
        for c in df.columns:
            if c != "date":
                df[c] = pd.to_numeric(df[c], errors="coerce")
        return df.set_index("date").sort_index()

    def _collect_gdp(self) -> pd.DataFrame:
        """GDP YoY (quarterly) — ak.macro_china_gdp()"""
        df = _retry_akshare(ak.macro_china_gdp)
        if "商品" in df.columns:
            gdp = df[df["商品"].str.contains("同比", na=False)].copy()
            if gdp.empty:
                gdp = df.copy()
            gdp["date"] = pd.to_datetime(gdp["日期"], errors="coerce")
            gdp["GDP当季同比"] = pd.to_numeric(gdp["今值"], errors="coerce")
        else:
            gdp = df.copy()
            date_col = next((c for c in gdp.columns if "日期" in c or "date" in c.lower()), gdp.columns[0])
            gdp["date"] = pd.to_datetime(gdp[date_col], errors="coerce")
            val_col = next((c for c in gdp.columns if c not in (date_col, "date")), gdp.columns[1])
            gdp["GDP当季同比"] = pd.to_numeric(gdp[val_col], errors="coerce")
        gdp = gdp.dropna(subset=["date"])
        return gdp.set_index("date")[["GDP当季同比"]].sort_index()

    def _collect_industrial(self) -> pd.DataFrame:
        """Industrial production YoY — ak.macro_china_industrial_production()"""
        df = _retry_akshare(ak.macro_china_industrial_production)
        if "商品" in df.columns:
            ind = df[df["商品"].str.contains("同比", na=False)].copy()
            if ind.empty:
                ind = df.copy()
            ind["date"] = pd.to_datetime(ind["日期"], errors="coerce")
            ind["工业增加值同比"] = pd.to_numeric(ind["今值"], errors="coerce")
        else:
            ind = df.copy()
            date_col = next((c for c in ind.columns if "日期" in c or "date" in c.lower()), ind.columns[0])
            ind["date"] = pd.to_datetime(ind[date_col], errors="coerce")
            val_col = next((c for c in ind.columns if c not in (date_col, "date")), ind.columns[1])
            ind["工业增加值同比"] = pd.to_numeric(ind[val_col], errors="coerce")
        ind = ind.dropna(subset=["date"])
        return ind.set_index("date")[["工业增加值同比"]].sort_index()

    def _collect_bond_yield(self) -> pd.DataFrame:
        """10Y government bond yield — ak.bond_china_yield()"""
        df = _retry_akshare(ak.bond_china_yield)
        govt = df[df["曲线名称"].str.contains("国债", na=False)].copy()
        govt["date"] = pd.to_datetime(govt["日期"], errors="coerce")
        govt = govt.dropna(subset=["date"])
        govt["10Y国债收益率"] = pd.to_numeric(govt["10年"], errors="coerce")
        return govt.set_index("date")[["10Y国债收益率"]].sort_index()

    def _collect_shibor(self) -> pd.DataFrame:
        """SHIBOR overnight rate — ak.rate_interbank()"""
        df = _retry_akshare(ak.rate_interbank)
        # ak.rate_interbank returns columns: 报告日, Shibor-隔夜, Shibor-1周, ...
        try:
            df = df.rename(columns={"报告日": "date", "Shibor-隔夜": "SHIBOR_ON"})
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df.dropna(subset=["date"])
            df["SHIBOR_ON"] = pd.to_numeric(df["SHIBOR_ON"], errors="coerce")
            return df.set_index("date")[["SHIBOR_ON"]].sort_index()
        except Exception:
            # Fallback to macro_china_shibor_all if columns differ
            df2 = _retry_akshare(ak.macro_china_shibor_all)
            df2 = df2.rename(columns={"日期": "date", "O/N-定价": "SHIBOR_ON"})
            df2["date"] = pd.to_datetime(df2["date"], errors="coerce")
            df2 = df2.dropna(subset=["date"])
            df2["SHIBOR_ON"] = pd.to_numeric(df2["SHIBOR_ON"], errors="coerce")
            return df2.set_index("date")[["SHIBOR_ON"]].sort_index()

    # ── Bonus indicators ───────────────────────────────────────────

    def _collect_csi300_pe(self) -> pd.DataFrame:
        """CSI 300 TTM PE — bonus indicator."""
        df = _retry_akshare(ak.stock_index_pe_lg, symbol="沪深300")
        df = df.rename(columns={
            "日期": "date",
            "滚动市盈率": "沪深300PE_TTM",
            "等权滚动市盈率": "沪深300PE_等权",
            "滚动市盈率中位数": "沪深300PE_中位数",
        })
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"])
        for c in ["沪深300PE_TTM", "沪深300PE_等权", "沪深300PE_中位数"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        return df.set_index("date")[["沪深300PE_TTM"]].sort_index()

    def _collect_wti_oil(self) -> pd.DataFrame:
        """WTI oil price proxy — INE crude futures main contract."""
        try:
            df = _retry_akshare(ak.futures_main_sina, symbol="SC0")
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df.dropna(subset=["date"])
            close_col = next((c for c in ["close", "收盘价"] if c in df.columns), df.columns[1])
            df["WTI原油"] = pd.to_numeric(df[close_col], errors="coerce")
            return df.set_index("date")[["WTI原油"]].sort_index()
        except Exception:
            df = _retry_akshare(ak.macro_china_energy_index)
            df["date"] = pd.to_datetime(df["日期"], errors="coerce")
            df = df.dropna(subset=["date"])
            val_col = [c for c in df.columns if c != "date" and "日期" not in c][0]
            df["WTI原油"] = pd.to_numeric(df[val_col], errors="coerce")
            return df.set_index("date")[["WTI原油"]].sort_index()

    def _collect_gold(self) -> pd.DataFrame:
        """Shanghai gold benchmark — bonus indicator."""
        df = _retry_akshare(ak.spot_golden_benchmark_sge)
        df["date"] = pd.to_datetime(df["交易时间"], errors="coerce")
        df = df.dropna(subset=["date"])
        df["黄金价格"] = pd.to_numeric(df["早盘价"], errors="coerce")
        return df.set_index("date")[["黄金价格"]].sort_index()

    def _collect_us_bond_yields(self) -> pd.DataFrame:
        """US Treasury yields (2Y + 10Y) via Sina Finance.

        Uses bond_gb_us_sina which goes through Sina Finance (not Eastmoney),
        avoiding the ongoing Eastmoney IP blocking issue.

        Returns DataFrame with columns: us_2y_yield, us_10y_yield
        """
        import akshare as ak_local
        results: Dict[str, pd.Series] = {}

        for maturity, symbol, col_name in [
            ("2Y", "美国2年期国债", "us_2y_yield"),
            ("10Y", "美国10年期国债", "us_10y_yield"),
        ]:
            try:
                df = _retry_akshare(ak_local.bond_gb_us_sina, symbol=symbol)
                df["date"] = pd.to_datetime(df["date"], errors="coerce")
                df = df.dropna(subset=["date"])
                series = df.set_index("date")["close"].rename(col_name)
                series = pd.to_numeric(series, errors="coerce")
                results[col_name] = series
                _time.sleep(0.5)
            except Exception as e:
                print(f"  WARN US {maturity} yield collection failed: {e}")

        if not results:
            raise RuntimeError("US bond yield collection failed for all maturities")

        # Merge on date index
        df_out = pd.DataFrame(results).sort_index()
        df_out.index.name = "date"
        return df_out

    # ── Indicator registry ─────────────────────────────────────────

    def _get_indicator_sources(self) -> List[Tuple[str, str, timedelta, Callable]]:
        """Return (name, freq, cache_ttl, collector_method) for all indicators."""
        return [
            # 9 core EDB indicators
            ("制造业PMI", "month", CACHE_TTL_MONTHLY, self._collect_pmi),
            ("CPI同比", "month", CACHE_TTL_MONTHLY, self._collect_cpi),
            ("PPI同比", "month", CACHE_TTL_MONTHLY, self._collect_ppi),
            ("M2同比", "month", CACHE_TTL_MONTHLY, self._collect_m2),
            ("社会融资规模", "month", CACHE_TTL_MONTHLY, self._collect_social_financing),
            ("GDP当季同比", "quarter", CACHE_TTL_QUARTERLY, self._collect_gdp),
            ("工业增加值同比", "month", CACHE_TTL_MONTHLY, self._collect_industrial),
            ("10Y国债收益率", "daily", CACHE_TTL_DAILY, self._collect_bond_yield),
            ("SHIBOR隔夜", "daily", CACHE_TTL_DAILY, self._collect_shibor),
            # Bonus indicators
            ("沪深300PE_TTM", "daily", CACHE_TTL_DAILY, self._collect_csi300_pe),
            ("WTI原油", "daily", CACHE_TTL_DAILY, self._collect_wti_oil),
            ("黄金价格", "daily", CACHE_TTL_DAILY, self._collect_gold),
            # US Treasury yields (for gold valuation & macro context)
            ("美国国债收益率", "daily", CACHE_TTL_DAILY, self._collect_us_bond_yields),
        ]

    # ── Individual indicator cache paths ───────────────────────────

    def _indicator_cache_path(self, name: str) -> Path:
        safe = name.replace("/", "_").replace("\\", "_")
        return self.data_dir / f"edb_{safe}.csv"

    def _try_load_cached_indicator(self, name: str, ttl: timedelta) -> Optional[pd.DataFrame]:
        """Try to load a single indicator from its individual cache file."""
        path = self._indicator_cache_path(name)
        if _is_cache_fresh(path, ttl):
            try:
                df = pd.read_csv(path, parse_dates=["date"], index_col="date")
                if not df.empty:
                    return df
            except Exception:
                pass
        return None

    def _save_cached_indicator(self, name: str, df: pd.DataFrame):
        """Save a single indicator to its individual cache file."""
        path = self._indicator_cache_path(name)
        df.to_csv(path)

    # ── Main collection methods ────────────────────────────────────

    def collect_all_edb_indicators(self) -> pd.DataFrame:
        """Collect all macro indicators into a single DataFrame with date index.

        Uses per-indicator caching: monthly indicators reload if cache >7 days old,
        daily indicators reload if cache >24 hours old. If an individual data source
        fails, it is skipped gracefully (other indicators still collected).

        Returns:
            pd.DataFrame with date index and indicator columns.
        """
        print(f"[{PROVIDER}] Collecting macro EDB indicators...")
        all_frames: Dict[str, pd.DataFrame] = {}
        stats = {"collected": 0, "cached": 0, "failed": 0}

        for name, freq, ttl, method in self._get_indicator_sources():
            try:
                # Try cache first
                cached = self._try_load_cached_indicator(name, ttl)
                if cached is not None:
                    all_frames[name] = cached
                    stats["cached"] += 1
                    print(f"  CACHE {name}: {len(cached)} rows, {cached.index[0].strftime('%Y%m%d')} ~ {cached.index[-1].strftime('%Y%m%d')}")
                    continue

                # Collect fresh
                df = method()
                if df.empty:
                    print(f"  SKIP {name}: empty")
                    continue
                all_frames[name] = df
                self._save_cached_indicator(name, df)
                stats["collected"] += 1
                print(f"  OK {name}: {len(df)} rows, {df.index[0].strftime('%Y%m%d')} ~ {df.index[-1].strftime('%Y%m%d')}")
                _time.sleep(0.3)  # friendly rate limit
            except Exception as e:
                stats["failed"] += 1
                print(f"  FAIL {name}: {e}")
                # Try stale cache as last resort
                stale_path = self._indicator_cache_path(name)
                if stale_path.exists():
                    try:
                        fallback = pd.read_csv(stale_path, parse_dates=["date"], index_col="date")
                        if not fallback.empty:
                            all_frames[name] = fallback
                            print(f"  FALLBACK {name}: loaded stale cache ({len(fallback)} rows)")
                    except Exception:
                        pass

        if not all_frames:
            raise RuntimeError("No indicators collected! All data sources failed.")

        # Merge all on date index
        result: Optional[pd.DataFrame] = None
        for name, df in all_frames.items():
            if result is None:
                result = df
            else:
                result = result.join(df, how="outer")

        result = result.sort_index()
        result.index.name = "date"

        # Merge manual imports
        manual_dir = self.data_dir / "manual_import"
        if manual_dir.exists():
            for csv_file in sorted(manual_dir.glob("*.csv")):
                try:
                    # Try Wind format first, then standard CSV
                    manual = _parse_wind_manual_csv(csv_file)
                    if manual is not None:
                        fmt = "Wind"
                    else:
                        manual = pd.read_csv(csv_file, parse_dates=["date"], index_col="date")
                        fmt = "CSV"
                    for col in manual.columns:
                        col_clean = col.strip()
                        if col_clean not in result.columns:
                            result[col_clean] = manual[col]
                            print(f"  MERGED manual import ({fmt}): {csv_file.name} -> {col_clean} ({len(manual)} rows)")
                        else:
                            # Manual import always overrides stale auto-collected data
                            # for the latest observations (last N rows where manual has data)
                            result[col_clean] = result[col_clean].combine_first(manual[col])
                            print(f"  OVERRIDE {col_clean}: manual import ({fmt}) supersedes auto-collected ({len(manual)} rows)")
                except Exception as e:
                    print(f"  WARN manual import {csv_file.name}: {e}")

        # Save unified cache
        self._save_edb_cache(result)
        self._save_run_manifest(stats)
        print(f"  DONE: {stats['collected']} fresh, {stats['cached']} cached, {stats['failed']} failed, {len(all_frames)} merged")
        return result

    def get_latest_indicators(self) -> Dict[str, float]:
        """Get the latest available values for all macro indicators.

        Attempts from cache first, then collects fresh if needed.
        Returns a flat dict with indicator name -> latest value.
        """
        df = self.load_edb_cache()
        if df is None or df.empty:
            df = self.collect_all_edb_indicators()
        latest: Dict[str, float] = {}
        for col in df.columns:
            series = df[col].dropna()
            if not series.empty:
                latest[col] = float(series.iloc[-1])
        return latest

    # ══════════════════════════════════════════════════════════════
    # ETF K-line Data
    # ══════════════════════════════════════════════════════════════

    def collect_etf_kline(
        self,
        etf_code: str,
        start_date: str = "20100101",
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        """Collect single ETF daily k-line — akshare first, baostock fallback.

        Args:
            etf_code: ETF ticker (digits only), e.g. '518880'
            start_date: begin date in yyyyMMdd format
            end_date: end date in yyyyMMdd format, defaults to today

        Returns:
            pd.DataFrame with normalized columns: date, open, high, low, close, volume, amount, pct_change
        """
        if end_date is None:
            end_date = datetime.now().strftime("%Y%m%d")

        # ── Primary: akshare (Eastmoney) ────────────────────────
        try:
            df = _retry_akshare(
                ak.fund_etf_hist_em,
                symbol=etf_code,
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust="qfq",
            )
            return self._normalize_etf_kline(df, etf_code)
        except Exception as e:
            print(f"  WARN akshare ETF {etf_code}: {e}, trying baostock...")

        # ── Fallback: baostock ──────────────────────────────────
        return self._collect_etf_kline_baostock(etf_code, start_date, end_date)

    def _collect_etf_kline_baostock(
        self,
        etf_code: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """Collect ETF daily k-line via baostock (free, no quota)."""
        import baostock as bs

        # Map ETF code to baostock code: SH for 5xxxxx, SZ for 1xxxxx
        if etf_code.startswith("5") or etf_code.startswith("6"):
            bs_code = f"sh.{etf_code}"
        elif etf_code.startswith("1") or etf_code.startswith("0"):
            bs_code = f"sz.{etf_code}"
        else:
            bs_code = f"sh.{etf_code}"

        # Convert dates: yyyyMMdd → yyyy-MM-dd
        sd = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"
        ed = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}"

        lg = bs.login()
        if lg.error_code != "0":
            raise RuntimeError(f"baostock login failed: {lg.error_msg}")

        try:
            rs = bs.query_history_k_data_plus(
                bs_code,
                "date,open,high,low,close,volume,amount,pctChg",
                start_date=sd, end_date=ed, frequency="d",
            )
            if rs.error_code != "0":
                raise RuntimeError(f"baostock query failed: {rs.error_msg}")

            data_list = []
            while rs.next():
                data_list.append(rs.get_row_data())

            if not data_list:
                raise RuntimeError(f"baostock returned 0 rows for {bs_code}")

            df = pd.DataFrame(data_list, columns=rs.fields)
            df = df.rename(columns={
                "date": "date", "open": "open", "close": "close",
                "high": "high", "low": "low", "volume": "volume",
                "amount": "amount", "pctChg": "pct_change",
            })
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df.dropna(subset=["date"])
            for c in ["open", "high", "low", "close", "volume", "amount"]:
                if c in df.columns:
                    df[c] = pd.to_numeric(df[c], errors="coerce")
            return df.sort_values("date").reset_index(drop=True)
        finally:
            bs.logout()

    def collect_all_etfs(self, start_date: str = "20100101") -> Dict[str, pd.DataFrame]:
        """Collect all 6 ETF daily k-lines, returning a dict keyed by asset class.

        Graceful degradation: if one ETF fails, attempts fallback ETF, continues with others.
        """
        print(f"[{PROVIDER}] Collecting ETF k-line data...")
        results: Dict[str, pd.DataFrame] = {}
        for asset_id, asset in ASSET_UNIVERSE.items():
            if asset.primary_etf is None:
                continue
            cache_path = self.data_dir / f"etf_{asset_id}.csv"
            # Check cache
            if _is_cache_fresh(cache_path, CACHE_TTL_DAILY):
                try:
                    cached = pd.read_csv(cache_path, parse_dates=["date"])
                    if not cached.empty:
                        results[asset_id] = cached
                        print(f"  CACHE {asset.primary_etf} ({asset.asset_name}): {len(cached)} rows")
                        continue
                except Exception:
                    pass
            # Collect fresh
            try:
                df = self.collect_etf_kline(asset.primary_etf, start_date)
                results[asset_id] = df
                self._save_etf_cache(asset_id, df)
                print(f"  OK {asset.primary_etf} ({asset.asset_name}): {len(df)} rows")
                _time.sleep(1.0)
            except Exception as e:
                print(f"  FAIL {asset.primary_etf} ({asset.asset_name}): {e}")
                if asset.fallback_etf:
                    try:
                        _time.sleep(2)
                        df = self.collect_etf_kline(asset.fallback_etf, start_date)
                        results[asset_id] = df
                        self._save_etf_cache(asset_id, df)
                        print(f"  OK (fallback) {asset.fallback_etf}: {len(df)} rows")
                    except Exception as e2:
                        print(f"  FAIL fallback too: {e2}")
        return results

    # Backward-compatible alias
    collect_all_etf_data = collect_all_etfs

    def get_etf_latest_prices(self) -> Dict[str, Dict[str, Any]]:
        """Get latest price and daily change for all ETFs.

        Returns:
            Dict keyed by asset_id, each value is:
                {"price": float, "change_pct": float, "date": str, "asset_name": str}
        """
        results: Dict[str, Dict[str, Any]] = {}
        for asset_id, asset in ASSET_UNIVERSE.items():
            if asset.primary_etf is None:
                continue
            cache_path = self.data_dir / f"etf_{asset_id}.csv"
            try:
                if cache_path.exists():
                    df = pd.read_csv(cache_path, parse_dates=["date"])
                else:
                    df = self.collect_etf_kline(asset.primary_etf)
                if df.empty:
                    continue
                latest_row = df.sort_values("date").iloc[-1]
                results[asset_id] = {
                    "price": float(latest_row["close"]),
                    "change_pct": float(latest_row.get("pct_change", 0)),
                    "date": str(latest_row["date"].date()),
                    "asset_name": asset.asset_name,
                }
            except Exception as e:
                print(f"  FAIL get_etf_latest_prices {asset_id}: {e}")
        return results

    @staticmethod
    def _normalize_etf_kline(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """Normalize akshare fund_etf_hist_em output to standard OHLCV columns."""
        df = df.rename(columns={
            "日期": "date", "开盘": "open", "收盘": "close",
            "最高": "high", "最低": "low", "成交量": "volume",
            "成交额": "amount", "涨跌幅": "pct_change",
        })
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"])
        for c in ["open", "high", "low", "close", "volume", "amount"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        return df.sort_values("date").reset_index(drop=True)

    # ══════════════════════════════════════════════════════════════
    # Index K-line Data
    # ══════════════════════════════════════════════════════════════

    def collect_index_kline(
        self,
        symbol: str,
        begin_date: str = "20050101",
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        """Collect index daily k-line — akshare first, baostock fallback.

        Args:
            symbol: akshare index symbol, e.g. 'sh000300' or 'sh000688'
            begin_date: start date in yyyyMMdd
            end_date: end date in yyyyMMdd, defaults to today

        Returns:
            pd.DataFrame with columns: date, open, high, low, close, volume
        """
        if end_date is None:
            end_date = datetime.now().strftime("%Y%m%d")

        df = None

        # Tier 1: akshare
        try:
            df = _retry_akshare(ak.stock_zh_index_daily, symbol=symbol)
            df = df.rename(columns={
                "date": "date", "open": "open", "close": "close",
                "high": "high", "low": "low", "volume": "volume",
            })
        except Exception as e:
            print(f"  ⚠️ akshare index_kline 失败: {e}")

        # Tier 2: baostock fallback
        if df is None:
            try:
                import baostock as bs
                bs_code = symbol.replace("sh", "sh.").replace("sz", "sz.")
                bs_start = pd.to_datetime(begin_date).strftime("%Y-%m-%d")
                bs_end = pd.to_datetime(end_date).strftime("%Y-%m-%d")
                lg = bs.login()
                if lg.error_code == "0":
                    rs = bs.query_history_k_data_plus(
                        bs_code,
                        "date,open,close,high,low,volume",
                        start_date=bs_start,
                        end_date=bs_end,
                        frequency="d",
                        adjustflag="2",
                    )
                    rows = []
                    while rs.next():
                        rows.append(rs.get_row_data())
                    if rows:
                        df = pd.DataFrame(rows, columns=rs.fields)
                        df = df.rename(columns={
                            "date": "date", "open": "open", "close": "close",
                            "high": "high", "low": "low", "volume": "volume",
                        })
                    bs.logout()
                    if df is not None and not df.empty:
                        print(f"  ✅ baostock: {bs_code} → {len(df)} rows")
            except Exception as e2:
                print(f"  ⚠️ baostock index_kline 也失败: {e2}")

        if df is None or df.empty:
            raise RuntimeError(f"无法获取指数K线 {symbol}: akshare和baostock均失败")

        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"])
        for c in ["open", "high", "low", "close", "volume"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.sort_values("date")
        begin_dt = pd.to_datetime(begin_date)
        end_dt = pd.to_datetime(end_date)
        df = df[(df["date"] >= begin_dt) & (df["date"] <= end_dt)]
        return df.reset_index(drop=True)

    def collect_all_index_data(self, begin_date: str = "20050101") -> Dict[str, pd.DataFrame]:
        """Collect benchmark indices (CSI 300 + STAR 50 + Hang Seng).

        Results keyed by: 'a_share', 'star_50', 'hk_tech'
        """
        import akshare as ak  # local import for the fallback Hong Kong collector
        import time as _time_mod
        results: Dict[str, pd.DataFrame] = {}

        indices: List[Tuple[str, str, str, Optional[str]]] = [
            ("a_share", "sh000300", "沪深300", None),
            ("star_50", "sh000688", "科创50", None),
            ("hk_tech", "HSI", "恒生指数", "stock_hk_index_daily_em"),
        ]

        for key, symbol, label, source in indices:
            cache_path = self.data_dir / f"index_{key}.csv"
            # Check cache
            if _is_cache_fresh(cache_path, CACHE_TTL_DAILY):
                try:
                    cached = pd.read_csv(cache_path, parse_dates=["date"])
                    if not cached.empty:
                        results[key] = cached
                        print(f"  CACHE {symbol} ({label}): {len(cached)} rows")
                        continue
                except Exception:
                    pass
            # Collect fresh
            try:
                if source == "stock_hk_index_daily_em":
                    df = _retry_akshare(ak.stock_hk_index_daily_em, symbol=symbol)
                    df = df.rename(columns={"latest": "close"})
                    df["date"] = pd.to_datetime(df["date"], errors="coerce")
                else:
                    df = _retry_akshare(ak.stock_zh_index_daily, symbol=symbol)
                    df["date"] = pd.to_datetime(df["date"], errors="coerce")

                df = df.dropna(subset=["date"])
                for c in ["open", "high", "low", "close", "volume"]:
                    if c in df.columns:
                        df[c] = pd.to_numeric(df[c], errors="coerce")
                df = df.sort_values("date")
                begin_dt = pd.to_datetime(begin_date)
                df = df[df["date"] >= begin_dt]
                results[key] = df.reset_index(drop=True)
                self._save_index_cache(key, results[key])
                print(f"  OK {symbol} ({label}): {len(df)} rows")
                _time_mod.sleep(1.0)
            except Exception as e:
                print(f"  FAIL {symbol} ({label}): {e}")
        return results

    # ══════════════════════════════════════════════════════════════
    # Cache Management
    # ══════════════════════════════════════════════════════════════

    def _save_etf_cache(self, asset_id: str, df: pd.DataFrame):
        path = self.data_dir / f"etf_{asset_id}.csv"
        df.to_csv(path, index=False)

    def _save_index_cache(self, key: str, df: pd.DataFrame):
        path = self.data_dir / f"index_{key}.csv"
        df.to_csv(path, index=False)

    def _save_edb_cache(self, df: pd.DataFrame):
        path = self.data_dir / "edb_indicators.csv"
        df.to_csv(path)

    def _save_run_manifest(self, stats: dict):
        path = self.data_dir / "collection_manifest.json"
        manifest = {
            "timestamp": datetime.now().isoformat(),
            "provider": PROVIDER,
            "stats": stats,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

    def load_etf_cache(self, asset_id: str) -> Optional[pd.DataFrame]:
        path = self.data_dir / f"etf_{asset_id}.csv"
        if path.exists():
            return pd.read_csv(path, parse_dates=["date"])
        return None

    def load_edb_cache(self) -> Optional[pd.DataFrame]:
        path = self.data_dir / "edb_indicators.csv"
        if path.exists():
            return pd.read_csv(path, parse_dates=["date"], index_col="date")
        return None

    def load_all_cached_data(self) -> Dict[str, Any]:
        """Load all cached ETF + EDB data."""
        etf_data: Dict[str, pd.DataFrame] = {}
        for asset_id in ASSET_UNIVERSE:
            if asset_id == "cash":
                continue
            df = self.load_etf_cache(asset_id)
            if df is not None:
                etf_data[asset_id] = df
        edb = self.load_edb_cache()
        return {"etf_data": etf_data, "edb_indicators": edb}


# ── Module-level singleton ────────────────────────────────────────

_collector: Optional[MacroDataCollector] = None


def get_macro_collector(data_dir: Optional[Path] = None) -> MacroDataCollector:
    """Get or create the module-level MacroDataCollector singleton."""
    global _collector
    if _collector is None:
        _collector = MacroDataCollector(data_dir=data_dir)
    return _collector