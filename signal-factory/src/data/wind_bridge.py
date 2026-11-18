"""
Wind MCP → Qlib 数据桥 (已弃用 Wind MCP，现为通用 CSV→bin 转换器)

2026-09-14: Wind MCP 自动化管线已移除。本模块名称保留历史原因，
但功能是通用的 CSV→bin 格式转换器，不依赖 Wind MCP。

从 CSV 数据中提取行情数据，转换为 Qlib 可读取的 CSV/bin 格式。

设计原则:
  1. 格式兼容: 自动识别多种 JSON 输出格式
  2. 增量同步: 只添加新数据，不重复写入已有数据
  3. 符号推断: 从文件名或数据内容自动推断标的代码
  4. 配置驱动: 所有路径从 config/data.yaml 读取

用法:
    bridge = WindBridge()
    result = bridge.sync("/path/to/data/json")
    bridge.dump_to_bin()

    # 或从配置加载
    bridge = WindBridge.from_config("config/data.yaml")
"""

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd


# ─── Wind MCP 数据格式定义 ───

# Wind MCP 各 skill 输出的 JSON 格式可能不同，这里做兼容映射
WIND_FORMAT_KEYS = {
    "ohlcv": ["data", "result", "kline", "daily", "ohlcv", "prices"],
    "symbol": ["symbol", "code", "wind_code", "instrument"],
    "date": ["date", "trade_date", "datetime", "time", "TIME"],
    "open": ["open", "open_price", "OPEN"],
    "high": ["high", "high_price", "HIGH"],
    "low": ["low", "low_price", "LOW"],
    "close": ["close", "close_price", "CLOSE", "MATCH"],
    "volume": ["volume", "vol", "VOLUME", "turnover_vol"],
    "amount": ["amount", "amt", "turnover", "TURNOVER", "value"],
}

# 标的代码前缀推断规则
SYMBOL_PREFIX_RULES = [
    # (pattern, prefix, description)
    (re.compile(r"^(6\d{5})$"), "sh", "上交所主板"),
    (re.compile(r"^(9\d{5})$"), "sh", "上交所科创板+主板"),
    (re.compile(r"^(0\d{5})$"), "sz", "深交所主板"),
    (re.compile(r"^(3\d{5})$"), "sz", "深交所创业板"),
    (re.compile(r"^(8\d{5})$"), "bj", "北交所"),
    (re.compile(r"^(00\d{4})$"), "sz", "深交所旧代码"),
    (re.compile(r"^(sh|SH)\d{6}$"), "", "已有交易所前缀"),
    (re.compile(r"^(sz|SZ)\d{6}$"), "", "已有交易所前缀"),
    (re.compile(r"^(bj|BJ)\d{6}$"), "", "已有交易所前缀"),
]


class WindBridge:
    """
    Wind MCP → Qlib 数据桥。

    负责将 Wind MCP 的 JSON 输出转换为 Qlib 所需的 CSV 格式，
    并支持增量同步和 bin 格式转换。

    用法:
        bridge = WindBridge()
        result = bridge.sync("/path/to/wind/json")
        print(f"同步了 {result['synced_count']} 只标的")
    """

    def __init__(
        self,
        csv_dir: Optional[str] = None,
        bin_dir: Optional[str] = None,
        log_dir: Optional[str] = None,
    ):
        from src.data.config_loader import get_qlib_csv_dir, get_qlib_bin_dir

        self.csv_dir = Path(csv_dir or get_qlib_csv_dir())
        self.bin_dir = Path(bin_dir or get_qlib_bin_dir())
        self.log_dir = Path(log_dir) if log_dir else None

        # 确保目录存在
        self.csv_dir.mkdir(parents=True, exist_ok=True)
        self.bin_dir.mkdir(parents=True, exist_ok=True)

        # 同步日志
        self.sync_log: List[Dict] = []

    @classmethod
    def from_config(cls, config_path: str = "config/data.yaml") -> "WindBridge":
        """从配置文件中加载 WindBridge 配置"""
        import yaml
        from src.data.config_loader import get_qlib_csv_dir, get_qlib_bin_dir

        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        data_config = config.get("data", {})
        qlib_config = data_config.get("qlib", {})

        return cls(
            csv_dir=qlib_config.get("csv_dir") or get_qlib_csv_dir(),
            bin_dir=qlib_config.get("bin_dir") or get_qlib_bin_dir(),
        )

    # ─── 公开 API ───

    def sync(
        self,
        wind_json_dir: str,
        symbols: Optional[List[str]] = None,
        force: bool = False,
        dump_bin: bool = True,
    ) -> Dict:
        """
        完整同步流程: Wind JSON → CSV → (可选) bin。

        参数:
            wind_json_dir: Wind MCP JSON 输出目录
            symbols: 要同步的标的列表（None = 全部）
            force: 是否强制全量同步（默认增量）
            dump_bin: 是否在同步后自动转换为 bin 格式

        返回:
            {
                date: str,
                synced_count: int,
                skipped_count: int,
                errors: [str, ...],
                status: "ok" | "warning" | "error",
                bin_dumped: bool,
                symbols: [str, ...],
                duration: float,
            }
        """
        start = time.time()
        result = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "synced_count": 0,
            "skipped_count": 0,
            "errors": [],
            "status": "ok",
            "bin_dumped": False,
            "symbols": [],
        }

        wind_path = Path(wind_json_dir)
        if not wind_path.exists():
            result["status"] = "error"
            result["errors"].append(f"Wind JSON 目录不存在: {wind_json_dir}")
            return result

        # 发现 JSON 文件
        json_files = self.discover_wind_files(wind_json_dir, symbols)
        if not json_files:
            result["status"] = "warning"
            result["errors"].append(f"Wind JSON 目录为空: {wind_json_dir}")
            return result

        # 逐个处理
        for jf in json_files:
            try:
                symbol, df = self.process_wind_file(jf)
                if symbol is None or df is None or df.empty:
                    result["skipped_count"] += 1
                    continue

                # 写入 CSV
                csv_path = self.csv_dir / f"{symbol}.csv"
                df = self._to_qlib_format(df, symbol)

                if csv_path.exists() and not force:
                    existing = self._read_csv(csv_path)
                    df = self._merge_incremental(existing, df)

                df.to_csv(csv_path, index=False)
                result["synced_count"] += 1
                result["symbols"].append(symbol)

                self._log_sync(symbol, "success", str(jf))

            except Exception as e:
                result["errors"].append(f"{jf.name}: {e}")
                self._log_sync("unknown", "error", str(jf), str(e))

        # 转换为 bin
        if dump_bin and result["synced_count"] > 0:
            try:
                self.dump_to_bin()
                result["bin_dumped"] = True
            except Exception as e:
                result["errors"].append(f"bin 转换失败: {e}")

        result["duration"] = round(time.time() - start, 1)
        return result

    def process_wind_file(self, filepath: Path) -> Tuple[Optional[str], Optional[pd.DataFrame]]:
        """
        处理单个 Wind JSON 文件。

        返回:
            (symbol, dataframe) 或 (None, None) 表示失败
        """
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        symbol = self.extract_symbol(filepath.stem, data)
        df = self.extract_ohlcv(data)

        return symbol, df

    def discover_wind_files(
        self,
        wind_dir: str,
        symbols: Optional[List[str]] = None,
    ) -> List[Path]:
        """
        扫描 Wind JSON 目录，发现可用的数据文件。

        参数:
            wind_dir: Wind JSON 目录
            symbols: 过滤的标的列表（None = 全部）

        返回:
            文件路径列表（按修改时间排序）
        """
        wind_path = Path(wind_dir)
        if not wind_path.exists():
            return []

        # 收集所有 JSON 文件
        all_files = list(wind_path.glob("*.json"))
        if not all_files:
            return []

        # 如果指定了标的，按文件名过滤
        if symbols:
            filtered = []
            for f in all_files:
                for s in symbols:
                    clean = s.lower().replace("sh", "").replace("sz", "").replace("bj", "")
                    if clean in f.stem.lower():
                        filtered.append(f)
                        break
            return sorted(filtered, key=lambda f: f.stat().st_mtime)

        return sorted(all_files, key=lambda f: f.stat().st_mtime)

    def extract_symbol(self, filename: str, data: dict) -> str:
        """
        从文件名或数据中提取标准化的标的代码。

        格式: sh600519, sz000001, bj8xxxxx

        推断逻辑:
          1. 数据中的 symbol/code/wind_code 字段
          2. 文件名中的 6 位数字
          3. 数字前缀推断交易所
        """
        # 1) 从数据字段中提取
        for key in WIND_FORMAT_KEYS["symbol"]:
            if key in data:
                val = str(data[key])
                if val:
                    return self._normalize_symbol(val)

        # 递归搜索嵌套值
        for inner_key in ("data", "result", "meta", "info"):
            if inner_key in data and isinstance(data[inner_key], dict):
                for key in WIND_FORMAT_KEYS["symbol"]:
                    if key in data[inner_key]:
                        val = str(data[inner_key][key])
                        if val:
                            return self._normalize_symbol(val)

        # 2) 从文件名推断
        return self._infer_symbol_from_name(filename)

    def extract_ohlcv(self, data: dict) -> Optional[pd.DataFrame]:
        """
        从 Wind JSON 数据中提取 OHLCV DataFrame。

        支持多种 Wind MCP 输出格式:
          - 列表格式: [{date, open, high, low, close, volume}, ...]
          - 嵌套格式: {data: {kline: [...]}, result: {daily: [...]}}
          - 分列格式: {date: [...], open: [...], ...}

        返回:
            DataFrame with columns [open, high, low, close, volume], index=datetime
            或 None（无法解析时）
        """
        # Step 1: 定位内层数据
        inner = data
        for key in WIND_FORMAT_KEYS["ohlcv"]:
            if key in data:
                inner = data[key]
                break

        if inner is None:
            return None

        # Step 2: 列表格式 — 最常用
        if isinstance(inner, list) and len(inner) > 0:
            return self._parse_list_format(inner)

        # Step 3: 嵌套字典格式
        if isinstance(inner, dict):
            for key in ("kline", "daily", "ohlcv", "prices", "bars", "candles"):
                if key in inner and isinstance(inner[key], list) and len(inner[key]) > 0:
                    return self._parse_list_format(inner[key])

            # Step 4: Wind MCP columns/rows 格式
            # {columns: [{name: "TIME", type: "string"}, ...], rows: [[...], ...]}
            if "columns" in inner and "rows" in inner:
                return self._parse_columns_rows_format(inner["columns"], inner["rows"])

        return None

    def dump_to_bin(self, incremental: bool = False, new_market: str = None) -> bool:
        """
        将 CSV 目录中的所有数据转换为 Qlib bin 格式。

        手动实现 bin 转换，兼容所有 qlib 版本。
        格式: features/<symbol>/<field>.day.bin (numpy float32 array)

        参数:
            incremental: True = 只处理新增的 CSV，保留已有日历和 bin 文件
            new_market: 增量模式下，新股票写入哪个 market 文件（如 "csi500"）

        返回:
            True 表示成功
        """
        import numpy as np

        csv_dir = self.csv_dir
        bin_dir = self.bin_dir
        bin_dir.mkdir(parents=True, exist_ok=True)

        cal_dir = bin_dir / "calendars"
        inst_dir = bin_dir / "instruments"
        features_dir = bin_dir / "features"
        cal_dir.mkdir(parents=True, exist_ok=True)
        inst_dir.mkdir(parents=True, exist_ok=True)
        features_dir.mkdir(parents=True, exist_ok=True)

        fields = ["open", "close", "high", "low", "volume", "amount", "factor", "turn", "vwap", "change"]

        if incremental:
            # ─── 增量模式 ───
            # 1. 读取现有日历
            cal_path = cal_dir / "day.txt"
            if not cal_path.exists():
                print("警告: 日历文件不存在，回退到全量模式")
                return self.dump_to_bin(incremental=False)

            with open(cal_path, "r", encoding="utf-8") as f:
                old_calendar = [line.strip() for line in f if line.strip()]
            old_calendar_set = set(old_calendar)
            old_len = len(old_calendar)

            # 2. 扫描所有 CSV，找出新日期和新股票
            all_csv_dates = set()
            csv_list = sorted(csv_dir.glob("*.csv"))
            for csv_path in csv_list:
                try:
                    df = pd.read_csv(csv_path, parse_dates=["date"])
                    all_csv_dates.update(
                        pd.Timestamp(d).strftime("%Y-%m-%d") for d in df["date"]
                    )
                except Exception:
                    continue

            new_dates = sorted(all_csv_dates - old_calendar_set)

            # 3. 找出已有 bin 的股票
            existing_bin = set()
            for feat_dir in features_dir.iterdir():
                if feat_dir.is_dir() and (feat_dir / "close.day.bin").exists():
                    existing_bin.add(feat_dir.name)

            # 4. 找出真正的新股票（无 bin 目录）
            new_symbols = []
            for csv_path in csv_list:
                symbol = csv_path.stem
                if symbol not in existing_bin:
                    new_symbols.append(symbol)

            if not new_dates and not new_symbols:
                print("增量模式: 数据已是最新，无需更新")
                return True

            # 5. 合并日历
            new_calendar = old_calendar + new_dates
            new_len = len(new_calendar)
            new_date_to_idx = {d: i for i, d in enumerate(new_calendar)}

            # 写入新日历
            with open(cal_path, "w", encoding="utf-8") as f:
                for d in new_calendar:
                    f.write(d + "\n")

            if new_dates:
                print(f"增量模式: 日历 +{len(new_dates)} 天 ({new_dates[0]} ~ {new_dates[-1]})")

            # 6. 扩展已有 bin 文件（填充新日期数据）
            if new_dates and existing_bin:
                updated_bin = 0
                M = len(new_dates)

                for symbol in sorted(existing_bin):
                    feat_dir = features_dir / symbol
                    csv_path = csv_dir / f"{symbol}.csv"

                    # 从 CSV 读取新日期数据
                    csv_new_data = {}
                    if csv_path.exists():
                        try:
                            df = pd.read_csv(csv_path, parse_dates=["date"])
                            for _, row in df.iterrows():
                                date_str = pd.Timestamp(row["date"]).strftime("%Y-%m-%d")
                                if date_str in new_dates:
                                    csv_new_data[date_str] = row
                        except Exception:
                            pass

                    # 扩展每个字段的 bin 文件
                    for field in fields:
                        bin_path = feat_dir / f"{field}.day.bin"
                        if not bin_path.exists():
                            continue

                        # 读取现有 bin
                        old_data = np.fromfile(bin_path, dtype="<f")
                        start_idx = int(old_data[0])
                        old_arr = old_data[1:]  # length = old_len

                        # 扩展数组
                        new_arr = np.concatenate([old_arr, np.full(M, np.nan, dtype=np.float32)])

                        # 填充新数据
                        for date_str, row in csv_new_data.items():
                            idx = new_date_to_idx[date_str]
                            if field in row.index and pd.notna(row[field]):
                                new_arr[idx] = float(row[field])

                        # 写回 bin（start_index 固定为 0，与全量模式一致）
                        bin_data = np.hstack([np.float32(0), new_arr]).astype("<f")
                        bin_data.tofile(str(bin_path))

                    updated_bin += 1

                print(f"增量模式: 更新 {updated_bin} 只已有标的的 bin 文件")

            # 7. 处理真正的新股票（无 bin 目录的 CSV）
            if new_symbols:
                print(f"增量模式: 新增 {len(new_symbols)} 只标的")
                processed = 0
                for csv_path in sorted(csv_dir.glob("*.csv")):
                    symbol = csv_path.stem
                    if symbol not in new_symbols:
                        continue

                    try:
                        df = pd.read_csv(csv_path, parse_dates=["date"])
                        df = df.sort_values("date")

                        feat_dir = features_dir / symbol
                        feat_dir.mkdir(parents=True, exist_ok=True)

                        first_date = df["date"].min()
                        first_date_str = pd.Timestamp(first_date).strftime("%Y-%m-%d")
                        start_idx = new_date_to_idx.get(first_date_str, 0)

                        for field in fields:
                            if field not in df.columns:
                                continue
                            arr = np.full(new_len, np.nan, dtype=np.float32)
                            for _, row in df.iterrows():
                                date_str = pd.Timestamp(row["date"]).strftime("%Y-%m-%d")
                                idx = new_date_to_idx.get(date_str)
                                if idx is not None:
                                    arr[idx] = float(row[field]) if pd.notna(row[field]) else np.nan
                            bin_data = np.hstack([np.float32(0), arr]).astype("<f")
                            bin_data.tofile(str(feat_dir / f"{field}.day.bin"))

                        processed += 1
                        if processed % 50 == 0:
                            print(f"  转换进度: {processed}/{len(new_symbols)}")

                    except Exception as e:
                        print(f"  转换失败 [{symbol}]: {e}")

                # 更新 instruments 文件
                all_path = inst_dir / "all.txt"
                existing_inst = set()
                if all_path.exists():
                    with open(all_path, "r", encoding="utf-8") as f:
                        for line in f:
                            if line.strip() and not line.startswith("symbol"):
                                parts = line.strip().split("\t")
                                if parts:
                                    existing_inst.add(parts[0])

                with open(all_path, "a", encoding="utf-8") as f:
                    for symbol in new_symbols:
                        if symbol not in existing_inst:
                            csv_path = csv_dir / f"{symbol}.csv"
                            if csv_path.exists():
                                df = pd.read_csv(csv_path, parse_dates=["date"])
                                start = df["date"].min()
                                end = df["date"].max()
                                f.write(f"{symbol}\t{start}\t{end}\n")

                # 更新 market 文件
                if new_market:
                    market_path = inst_dir / f"{new_market}.txt"
                    with open(market_path, "w", encoding="utf-8") as f:
                        for symbol in sorted(new_symbols):
                            csv_path = csv_dir / f"{symbol}.csv"
                            if csv_path.exists():
                                df = pd.read_csv(csv_path, parse_dates=["date"])
                                start = df["date"].min()
                                end = df["date"].max()
                                f.write(f"{symbol}\t{start}\t{end}\n")
                    print(f"已创建 {market_path} ({len(new_symbols)} 只标的)")

            # 8. 更新已有标的的 instruments 日期范围
            if new_dates:
                all_path = inst_dir / "all.txt"
                if all_path.exists():
                    # 重新读取并更新所有标的的 end_date
                    inst_lines = {}
                    with open(all_path, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if line and not line.startswith("symbol"):
                                parts = line.split("\t")
                                if parts:
                                    inst_lines[parts[0]] = line

                    max_date = new_calendar[-1]
                    with open(all_path, "w", encoding="utf-8") as f:
                        for symbol in sorted(inst_lines.keys()):
                            parts = inst_lines[symbol].split("\t")
                            csv_path = csv_dir / f"{symbol}.csv"
                            if csv_path.exists():
                                try:
                                    df = pd.read_csv(csv_path, parse_dates=["date"])
                                    end = df["date"].max()
                                    f.write(f"{symbol}\t{parts[1]}\t{end}\n")
                                except Exception:
                                    f.write(inst_lines[symbol] + "\n")
                            else:
                                f.write(inst_lines[symbol] + "\n")

            # 9. 更新 market 文件（csi500, csi300 等）的日期范围
            #    从更新后的 all.txt 读取最新日期范围
            all_inst = {}
            with open(all_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("symbol"):
                        parts = line.split("\t")
                        if len(parts) >= 3:
                            all_inst[parts[0]] = (parts[1], pd.Timestamp(parts[2]))
            for market_path in inst_dir.glob("*.txt"):
                if market_path.name == "all.txt":
                    continue
                _update_market_file(market_path, all_inst)

            print(f"增量转换完成: 日历 {old_len}→{new_len}, 新标的 {len(new_symbols)} 只")
            return True

        # ─── 全量模式 ───
        # 收集所有 CSV 数据
        all_dates = set()
        symbol_data: Dict[str, pd.DataFrame] = {}

        for csv_path in sorted(csv_dir.glob("*.csv")):
            symbol = csv_path.stem
            df = pd.read_csv(csv_path, parse_dates=["date"])
            df = df.sort_values("date")
            symbol_data[symbol] = df
            all_dates.update(df["date"].tolist())

        if not symbol_data:
            return False

        # 构建交易日历
        calendar = sorted(all_dates)
        with open(cal_dir / "day.txt", "w", encoding="utf-8") as f:
            for d in calendar:
                f.write(pd.Timestamp(d).strftime("%Y-%m-%d") + "\n")

        # 构建 instruments 列表 (all.txt)
        symbol_info = {}  # symbol -> (start, end)
        with open(inst_dir / "all.txt", "w", encoding="utf-8") as f:
            for symbol in sorted(symbol_data.keys()):
                df = symbol_data[symbol]
                start = df["date"].min()
                end = df["date"].max()
                f.write(f"{symbol}\t{start}\t{end}\n")
                symbol_info[symbol] = (start, end)

        # 更新所有 market 文件（csi500, csi300 等）的日期范围
        for market_path in inst_dir.glob("*.txt"):
            if market_path.name == "all.txt":
                continue
            _update_market_file(market_path, symbol_info)

        # 写入特征数据
        date_to_idx = {pd.Timestamp(d).strftime("%Y-%m-%d"): i for i, d in enumerate(calendar)}

        for symbol, df in symbol_data.items():
            feat_dir = features_dir / symbol
            feat_dir.mkdir(parents=True, exist_ok=True)

            # 计算该 symbol 在 calendar 中的起始索引
            first_date = df["date"].min()
            start_idx = date_to_idx[pd.Timestamp(first_date).strftime("%Y-%m-%d")]

            for field in fields:
                if field not in df.columns:
                    continue
                arr = np.full(len(calendar), np.nan, dtype=np.float32)
                for _, row in df.iterrows():
                    idx = date_to_idx[pd.Timestamp(row["date"]).strftime("%Y-%m-%d")]
                    arr[idx] = float(row[field]) if pd.notna(row[field]) else np.nan
                # Qlib bin 格式: [start_index(float32), data(float32 array)]
                # 注意: Qlib 的 read_bin 将 data 数组解释为从 start_index 开始，
                # 而非从 calendar[0] 开始。因此 start_index 必须为 0，让 data 数组
                # 覆盖完整的 calendar 范围（未上市日期为 NaN）。
                bin_data = np.hstack([np.float32(0), arr]).astype("<f")
                bin_data.tofile(str(feat_dir / f"{field}.day.bin"))

        return True



    def list_available_symbols(self) -> List[str]:
        """列出 CSV 目录中已有的标的"""
        symbols = []
        for f in self.csv_dir.glob("*.csv"):
            symbols.append(f.stem)
        return sorted(symbols)

    def get_symbol_data(
        self,
        symbol: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> Optional[pd.DataFrame]:
        """
        读取指定标的的已有 CSV 数据。

        参数:
            symbol: 标的代码
            start: 起始日期 (None = 全部)
            end: 结束日期 (None = 全部)

        返回:
            DataFrame 或 None
        """
        csv_path = self.csv_dir / f"{symbol}.csv"
        if not csv_path.exists():
            return None

        df = self._read_csv(csv_path)

        if start:
            df = df[df["date"] >= pd.Timestamp(start)]
        if end:
            df = df[df["date"] <= pd.Timestamp(end)]

        return df

    def get_sync_log(self, limit: int = 50) -> List[Dict]:
        """获取最近 N 条同步日志"""
        return self.sync_log[-limit:]

    # ─── 内部方法 ───

    def _normalize_symbol(self, symbol: str) -> str:
        """标准化标的代码为 sh600519 格式"""
        symbol = symbol.lower().strip()

        # 已有前缀
        if any(symbol.startswith(p) for p in ("sh", "sz", "bj")):
            return symbol

        # 数字格式 → 推断前缀
        for pattern, prefix, _ in SYMBOL_PREFIX_RULES:
            if pattern.match(symbol):
                if prefix:
                    return f"{prefix}{symbol}"
                return symbol

        return symbol

    def _infer_symbol_from_name(self, filename: str) -> str:
        """从文件名推断标的代码"""
        name = filename.replace(".json", "")

        # 查找 6 位数字
        for match in re.finditer(r"\d{6}", name):
            code = match.group()
            return self._normalize_symbol(code)

        return name

    def _parse_list_format(self, items: list) -> Optional[pd.DataFrame]:
        """解析列表格式的 OHLCV 数据，返回 [date, open, high, low, close, volume] DataFrame"""
        df = pd.DataFrame(items)

        if df.empty:
            return None

        # 标准化列名
        df = self._normalize_columns(df)

        # 查找并重命名日期列
        date_col = None
        for col in WIND_FORMAT_KEYS["date"]:
            if col in df.columns:
                date_col = col
                break

        if date_col:
            df["date"] = pd.to_datetime(df[date_col])
            if date_col != "date":
                df = df.drop(columns=[date_col])

        # 提取 OHLCV 列
        ohlcv_cols = {}
        col_map = {
            "open": WIND_FORMAT_KEYS["open"],
            "high": WIND_FORMAT_KEYS["high"],
            "low": WIND_FORMAT_KEYS["low"],
            "close": WIND_FORMAT_KEYS["close"],
            "volume": WIND_FORMAT_KEYS["volume"],
        }

        for target, candidates in col_map.items():
            for c in candidates:
                if c in df.columns:
                    ohlcv_cols[c] = target
                    break

        if len(ohlcv_cols) < 3:
            return None  # 至少需要 OHLC 中的 3 个

        # 重命名 OHLCV 列
        df = df.rename(columns=ohlcv_cols)

        # 构建输出：date, open, high, low, close, volume
        out_cols = ["date", "open", "high", "low", "close"]
        available = [c for c in out_cols if c in df.columns]
        result = df[available].copy()

        # 确保 volume 列存在
        if "volume" in df.columns:
            result["volume"] = df["volume"]
        else:
            result["volume"] = 0

        # 转换为数值
        for col in ["open", "high", "low", "close", "volume"]:
            if col in result.columns:
                result[col] = pd.to_numeric(result[col], errors="coerce")

        return result.dropna(subset=["close"])

        return df.dropna(subset=["close"])

    def _parse_columns_rows_format(self, columns: list, rows: list) -> Optional[pd.DataFrame]:
        """
        解析 Wind MCP columns/rows 格式。

        Wind MCP 返回格式:
          columns: [{"name": "TIME", "type": "string"}, {"name": "OPEN", ...}, ...]
          rows: [["2026-06-22T...", "1186.24", ...], ...]

        返回:
            DataFrame with columns [date, open, high, low, close, volume]
            date 为 date 类型（非 index），方便后续格式化为 Qlib CSV
        """
        # 提取列名
        col_names = [c["name"] if isinstance(c, dict) else str(c) for c in columns]

        # 构建 DataFrame
        df = pd.DataFrame(rows, columns=col_names)

        if df.empty:
            return None

        # Wind MCP 列名 → OHLCV 标准列名映射
        wind_column_map = {
            "TIME": "date",
            "OPEN": "open",
            "HIGH": "high",
            "LOW": "low",
            "MATCH": "close",       # 成交价 = 收盘价（日K）
            "CLOSE": "close",
            "VOLUME": "volume",
            "TURNOVER": "amount",   # 成交额
        }

        # 重命名
        df = df.rename(columns=wind_column_map)

        # 解析日期列（Wind 返回带时区的 ISO 时间，截取日期部分避免混合时区问题）
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"].astype(str).str[:10])
            df["date"] = pd.to_datetime(df["date"])

        # 提取 OHLCV 列
        ohlcv_cols = ["date", "open", "high", "low", "close"]
        available = [c for c in ohlcv_cols if c in df.columns]

        if len(available) < 4:  # 至少需要 date + 3 个 OHLC
            return None

        result = df[available].copy()

        # 确保 volume 列存在
        if "volume" in df.columns:
            result["volume"] = df["volume"]
        else:
            result["volume"] = 0

        # 转换为数值
        for col in ["open", "high", "low", "close", "volume"]:
            if col in result.columns:
                result[col] = pd.to_numeric(result[col], errors="coerce")

        return result.dropna(subset=["close"])

    def _normalize_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """标准化列名：去除空格、前缀、统一小写"""
        rename = {}
        for col in df.columns:
            clean = str(col).strip().lower()
            if clean != col:
                rename[col] = clean
        return df.rename(columns=rename)

    def _to_qlib_format(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """
        将内部 DataFrame 转换为 Qlib CSV 标准格式。

        Qlib 格式: symbol, date, open, close, high, low, volume, factor

        NOTE: factor 当前硬编码为 1.0。输入数据（akshare qfq / Wind 前复权）
        已是前复权价，即 close 存储的是调整后价格。
        Qlib 约定: $close / $factor = 真实成交价。
        当前的 factor=1.0 意味着 $close 直接使用前复权价。
        这对横向 alpha 排序影响很小，但会导致回测 P&L 不可复现
        （每次分红送转后历史价格被重写）。
        TODO: 改为 akshare 双抓（qfq + 不复权）→ factor = qfq_close / raw_close
        → 存 raw_close + 正确 factor。参见 Phase 2 重建计划。
        """
        df = df.copy()
        df["symbol"] = symbol
        df["factor"] = 1.0
        # Qlib 标准列顺序
        qlib_cols = ["symbol", "date", "open", "close", "high", "low", "volume", "factor"]
        return df[qlib_cols]

    def _read_csv(self, path: Path) -> pd.DataFrame:
        """读取已有 Qlib CSV 文件"""
        return pd.read_csv(path, parse_dates=["date"])

    def _merge_incremental(self, existing: pd.DataFrame, new_df: pd.DataFrame) -> pd.DataFrame:
        """增量合并：按 date 列去重，只添加新日期的数据"""
        new_dates = set(new_df["date"]) - set(existing["date"])
        if len(new_dates) == 0:
            return existing
        new_rows = new_df[new_df["date"].isin(new_dates)]
        merged = pd.concat([existing, new_rows], ignore_index=True)
        return merged.sort_values("date").reset_index(drop=True)

    def _log_sync(self, symbol: str, status: str, source: str, error: str = ""):
        """记录同步日志"""
        self.sync_log.append({
            "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "symbol": symbol,
            "status": status,
            "source": source,
            "error": error,
        })
        # 保留最近 1000 条
        if len(self.sync_log) > 1000:
            self.sync_log = self.sync_log[-1000:]

def _update_market_file(market_path: Path, symbol_info: Dict[str, Tuple]) -> None:
    """更新 market 文件中的标的日期范围。"""
    if not market_path.exists():
        return

    lines = []
    with open(market_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("symbol"):
                continue
            parts = line.split("\t")
            if len(parts) >= 3:
                symbol = parts[0]
                if symbol in symbol_info:
                    start, end = symbol_info[symbol]
                    parts[2] = pd.Timestamp(end).strftime("%Y-%m-%d %H:%M:%S")
                lines.append("\t".join(parts))

    with open(market_path, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")


# ─── 便捷函数 ───


def sync_wind_to_qlib(
    wind_json_dir: str,
    csv_dir: Optional[str] = None,
    bin_dir: Optional[str] = None,
    symbols: Optional[List[str]] = None,
    force: bool = False,
) -> Dict:
    """
    便捷函数：同步 Wind JSON 数据到 Qlib 格式。

    这是 WindBridge.sync() 的简化包装，供 CLI 和外部调用使用。
    """
    from src.data.config_loader import get_qlib_csv_dir, get_qlib_bin_dir

    bridge = WindBridge(
        csv_dir=csv_dir or get_qlib_csv_dir(),
        bin_dir=bin_dir or get_qlib_bin_dir(),
    )
    return bridge.sync(wind_json_dir, symbols=symbols, force=force)


def get_wind_data_summary(wind_json_dir: str) -> Dict:
    """
    扫描 Wind JSON 目录，返回数据摘要。

    返回:
        {
            file_count: int,
            symbols: [str, ...],
            date_range: [start, end] | None,
            total_size_mb: float,
        }
    """
    from pathlib import Path

    wind_path = Path(wind_json_dir)
    if not wind_path.exists():
        return {"file_count": 0, "symbols": [], "date_range": None, "total_size_mb": 0}

    files = list(wind_path.glob("*.json"))
    if not files:
        return {"file_count": 0, "symbols": [], "date_range": None, "total_size_mb": 0}

    bridge = WindBridge()

    symbols = []
    total_size = 0
    for f in files:
        total_size += f.stat().st_size
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            sym = bridge.extract_symbol(f.stem, data)
            if sym:
                symbols.append(sym)
        except Exception:
            pass

    return {
        "file_count": len(files),
        "symbols": sorted(set(symbols)),
        "date_range": None,  # 需解析每个文件的数据才能确定
        "total_size_mb": round(total_size / (1024 * 1024), 2),
    }