"""
执行现实性门禁模块 — Execution Gate for Trade Realism

负责在回测下单前进行 A 股市场的交易约束检查:
  1. ST/停牌过滤 — 加载 st_calendar.csv，ST 日/停牌日禁止交易
  2. 涨跌停门禁 — 涨停封死不能买，跌停封死不能卖
  3. 成交量约束 — 持仓不超过日成交额的 X%

所有检查都是可选且可配置的，默认全部开启。
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Set, Tuple

import pandas as pd


# ── 配置 ─────────────────────────────────────────────────────────

@dataclass
class RealismConfig:
    """执行现实性检查的配置，可按需开关各项约束。

    属性:
        check_st_suspended: 是否检查 ST/停牌过滤
        check_price_limits: 是否检查涨跌停门禁
        check_amount_constraint: 是否检查成交量占比约束
        st_calendar_path: st_calendar.csv 路径
        max_amount_pct: 持仓占日成交额的最大比例
        limit_epsilon: 涨跌停判定容差 (bp)
        min_fee: 最低佣金 (元)
    """
    check_st_suspended: bool = True
    check_price_limits: bool = True
    check_amount_constraint: bool = True
    st_calendar_path: str = "data/instruments/st_calendar.csv"
    max_amount_pct: float = 0.01  # 1%
    limit_epsilon: float = 0.005  # 5bp 容差
    min_fee: float = 5.0


# ── 板块涨跌停幅度 ─────────────────────────────────────────────

def get_limit_ratio(symbol: str, is_st: bool = False) -> float:
    """根据标的代码和 ST 状态返回涨跌停幅度。

    规则:
      - ST 股: 5%
      - 科创板 (sh688xxx): 20%
      - 创业板 (sz30xxxx): 20%
      - 北交所 (bjxxxxxx): 30%
      - 主板/中小板: 10%
    """
    if is_st:
        return 0.05
    if symbol.startswith("sh688") or symbol.startswith("sz30"):
        return 0.20
    if symbol.startswith("bj"):
        return 0.30
    return 0.10


# ── ST/停牌日历加载 ─────────────────────────────────────────────

def load_st_calendar(
    path: str,
    project_root: Optional[Path] = None,
) -> Dict[str, Set[str]]:
    """加载 ST/停牌日历，返回 {date_str: {symbol, ...}}。

    日历格式 (st_calendar.csv):
        symbol,date,is_st,tradestatus
        sh600711,2025-01-02,1,1    # ST + 可交易
        sh600095,2025-03-17,0,0    # 正常 + 停牌

    过滤规则:
      - is_st == 1 → 该标的完全排除 (当天不交易)
      - tradestatus == 0 → 该标的当天停牌 (当天不交易)

    返回的 dict 中，value 是所有应排除的 symbol 集合。
    没有记录的日期不出现在 dict 中（即无排除）。
    """
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent.parent
    calendar_path = project_root / path

    if not calendar_path.exists():
        return {}

    df = pd.read_csv(calendar_path, parse_dates=["date"])
    df["date_str"] = df["date"].dt.strftime("%Y-%m-%d")

    excluded: Dict[str, Set[str]] = {}
    for _, row in df.iterrows():
        date_key = row["date_str"]
        symbol = row["symbol"]
        is_st = int(row["is_st"])
        tradestatus = int(row["tradestatus"])

        if is_st == 1 or tradestatus == 0:
            if date_key not in excluded:
                excluded[date_key] = set()
            excluded[date_key].add(symbol)

    return excluded


def get_st_skip_dates(
    st_calendar: Dict[str, Set[str]],
    symbol: str,
) -> Set[str]:
    """从 ST 日历中提取某标的的排除日期集合。

    参数:
        st_calendar: load_st_calendar() 的返回值
        symbol: Qlib 格式标的代码 (如 sh600711)

    返回:
        该标的需要排除的日期字符串集合 (YYYY-MM-DD)
    """
    skip_dates: Set[str] = set()
    for date_str, symbols in st_calendar.items():
        if symbol in symbols:
            skip_dates.add(date_str)
    return skip_dates


# ── 涨跌停标记计算 ─────────────────────────────────────────────

def build_limit_maps(
    ohlcv_df: pd.DataFrame,
    symbol: str,
    limit_ratio: float = 0.10,
    epsilon: float = 0.005,
) -> Tuple[Dict[str, bool], Dict[str, bool]]:
    """从 OHLCV 数据预计算涨跌停封死标记。

    判断逻辑:
      - 涨停封死: close 接近 prev_close*(1+limit_ratio) 且 low 也接近涨停价
        → 全天高位，盘口封死，无法买入
      - 跌停封死: close 接近 prev_close*(1-limit_ratio) 且 high 也接近跌停价
        → 全天低位，盘口封死，无法卖出

    参数:
        ohlcv_df: DataFrame, index=datetime, columns=[open, high, low, close]
        symbol: 标的代码（用于日志）
        limit_ratio: 涨跌停幅度 (0.10 = 10%)
        epsilon: 判定容差 (0.005 = 5bp)

    返回:
        (limit_up_map, limit_down_map): 两个 {date_str: bool} 字典
        值为 True 表示该日涨停/跌停封死
    """
    if ohlcv_df is None or ohlcv_df.empty:
        return {}, {}

    df = ohlcv_df.copy()
    # 确保有必要的列
    required = ["close"]
    for col in required:
        if col not in df.columns:
            return {}, {}

    has_high = "high" in df.columns
    has_low = "low" in df.columns

    # 计算前一日收盘价
    df["prev_close"] = df["close"].shift(1)

    limit_up_map: Dict[str, bool] = {}
    limit_down_map: Dict[str, bool] = {}

    up_target = 1.0 + limit_ratio - epsilon
    down_target = 1.0 - limit_ratio + epsilon

    for idx, row in df.iterrows():
        prev_close = row.get("prev_close")
        if pd.isna(prev_close) or prev_close <= 0:
            continue

        date_str = str(idx)[:10] if hasattr(idx, "strftime") else str(idx)[:10]
        close = row["close"]

        # 涨停判定
        if close >= prev_close * up_target:
            if has_low:
                low = row["low"]
                if low >= prev_close * up_target:
                    # close 和 low 都接近涨停 → 封死
                    limit_up_map[date_str] = True
            else:
                # 没有 high/low 数据时，仅用 close 判定（弱判定）
                limit_up_map[date_str] = True

        # 跌停判定
        if close <= prev_close * down_target:
            if has_high:
                high = row["high"]
                if high <= prev_close * down_target:
                    # close 和 high 都接近跌停 → 封死
                    limit_down_map[date_str] = True
            else:
                limit_down_map[date_str] = True

    return limit_up_map, limit_down_map


# ── 成交量约束 ─────────────────────────────────────────────────

def compute_amount_cap(
    amount: float,
    price: float,
    max_pct: float = 0.01,
) -> int:
    """根据日成交额和最大占比计算可买股数上限。

    参数:
        amount: 当日成交额 (元)
        price: 成交价
        max_pct: 最大占比 (0.01 = 1%)

    返回:
        最大可买股数（整数）
    """
    if not amount or amount <= 0 or not price or price <= 0:
        return 0
    max_value = amount * max_pct
    return int(max_value / price)


# ── 主门禁检查 ─────────────────────────────────────────────────

class ExecutionGate:
    """执行现实性门禁 — 封装所有交易约束检查。

    用法:
        gate = ExecutionGate(config, project_root)
        gate.load(symbol, ohlcv_df)

        # 在 generate_trade_decision() 中:
        if gate.is_st_blocked(date_str):
            return TradeDecisionWO([], self)
        if gate.is_limit_up_blocked(date_str):
            return TradeDecisionWO([], self)
    """

    def __init__(
        self,
        config: RealismConfig,
        project_root: Optional[Path] = None,
    ):
        self.config = config
        if project_root is None:
            project_root = Path(__file__).resolve().parent.parent.parent
        self.project_root = project_root

        # 延迟加载
        self._st_calendar: Optional[Dict[str, Set[str]]] = None
        self._st_skip_dates: Set[str] = set()
        self._limit_up_map: Dict[str, bool] = {}
        self._limit_down_map: Dict[str, bool] = {}
        self._loaded: bool = False
        self._symbol: str = ""

    # ── 加载 ─────────────────────────────────────────────────

    def load(
        self,
        symbol: str,
        ohlcv_df: Optional[pd.DataFrame] = None,
        is_st: bool = False,
    ):
        """加载门禁数据（在每个回测开始时调用一次）。

        参数:
            symbol: 标的代码 (sh600000)
            ohlcv_df: OHLCV DataFrame (可选，涨跌停检查需要)
            is_st: 该标的是否为 ST 股 (用于确定涨跌停幅度)
        """
        self._symbol = symbol

        # 1. ST/停牌过滤
        if self.config.check_st_suspended:
            if self._st_calendar is None:
                self._st_calendar = load_st_calendar(
                    self.config.st_calendar_path,
                    self.project_root,
                )
            self._st_skip_dates = get_st_skip_dates(
                self._st_calendar, symbol
            )

        # 2. 涨跌停标记
        if self.config.check_price_limits and ohlcv_df is not None:
            limit_ratio = get_limit_ratio(symbol, is_st=is_st)
            self._limit_up_map, self._limit_down_map = build_limit_maps(
                ohlcv_df, symbol,
                limit_ratio=limit_ratio,
                epsilon=self.config.limit_epsilon,
            )

        self._loaded = True

    # ── 查询接口 ─────────────────────────────────────────────

    def is_st_blocked(self, date_str: str) -> bool:
        """检查某个日期是否因 ST/停牌被禁止交易。"""
        if not self.config.check_st_suspended:
            return False
        return date_str in self._st_skip_dates

    def is_limit_up_blocked(self, date_str: str) -> bool:
        """检查某个日期是否因涨停封死被禁止买入。"""
        if not self.config.check_price_limits:
            return False
        return self._limit_up_map.get(date_str, False)

    def is_limit_down_blocked(self, date_str: str) -> bool:
        """检查某个日期是否因跌停封死被禁止卖出。"""
        if not self.config.check_price_limits:
            return False
        return self._limit_down_map.get(date_str, False)

    def cap_shares(
        self,
        shares: int,
        amount: float,
        price: float,
    ) -> int:
        """对买入股数施加成交量占比约束。

        参数:
            shares: 原本打算买入的股数
            amount: 当日成交额 (元)
            price: 成交价

        返回:
            约束后的可买股数
        """
        if not self.config.check_amount_constraint:
            return shares
        max_shares = compute_amount_cap(
            amount, price, self.config.max_amount_pct
        )
        if max_shares > 0:
            return min(shares, max_shares)
        return shares

    @property
    def skip_dates(self) -> Set[str]:
        """返回 ST/停牌排除日期集合。"""
        return self._st_skip_dates

    @property
    def limit_up_map(self) -> Dict[str, bool]:
        """返回涨停封死标记字典。"""
        return self._limit_up_map

    @property
    def limit_down_map(self) -> Dict[str, bool]:
        """返回跌停封死标记字典。"""
        return self._limit_down_map