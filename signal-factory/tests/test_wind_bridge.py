"""
WindBridge smoke tests — 验证核心数据桥功能
"""
import sys
import tempfile
from pathlib import Path

import pandas as pd
import pytest

# 确保项目根目录在 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.wind_bridge import WindBridge


class TestWindBridge:
    """WindBridge 基础功能 smoke test"""

    @pytest.fixture
    def bridge(self, tmp_path):
        csv_dir = tmp_path / "csv"
        bin_dir = tmp_path / "bin"
        csv_dir.mkdir()
        bin_dir.mkdir()
        return WindBridge(csv_dir=str(csv_dir), bin_dir=str(bin_dir))

    def test_init_creates_dirs(self, tmp_path):
        csv_dir = tmp_path / "csv"
        bin_dir = tmp_path / "bin"
        WindBridge(csv_dir=str(csv_dir), bin_dir=str(bin_dir))
        assert csv_dir.exists()
        assert bin_dir.exists()

    def test_normalize_symbol_existing_prefix(self, bridge):
        assert bridge._normalize_symbol("sh600519") == "sh600519"
        assert bridge._normalize_symbol("SH600519") == "sh600519"
        assert bridge._normalize_symbol("sz000001") == "sz000001"

    def test_normalize_symbol_infer_prefix(self, bridge):
        # 6 开头 → sh
        assert bridge._normalize_symbol("600519") == "sh600519"
        # 0 开头 → sz
        assert bridge._normalize_symbol("000001") == "sz000001"
        # 3 开头 → sz
        assert bridge._normalize_symbol("300750") == "sz300750"
        # 8 开头 → bj
        assert bridge._normalize_symbol("830799") == "bj830799"

    def test_infer_symbol_from_name(self, bridge):
        assert bridge._infer_symbol_from_name("sh600519_daily") == "sh600519"
        assert bridge._infer_symbol_from_name("kline_000001") == "sz000001"

    def test_parse_list_format_basic(self, bridge):
        data = [
            {"date": "2026-08-01", "open": 10.0, "high": 11.0, "low": 9.5, "close": 10.5, "volume": 1000000},
            {"date": "2026-08-02", "open": 10.5, "high": 11.5, "low": 10.0, "close": 11.0, "volume": 1200000},
        ]
        df = bridge._parse_list_format(data)
        assert df is not None
        assert len(df) == 2
        assert list(df.columns) == ["date", "open", "high", "low", "close", "volume"]
        assert df.iloc[0]["close"] == 10.5

    def test_parse_list_format_minimal(self, bridge):
        """Only 2 OHLC fields (open, close) should return None"""
        data = [
            {"date": "2026-08-01", "open": 10.0, "close": 10.5},
        ]
        df = bridge._parse_list_format(data)
        assert df is None  # 至少需要 3 个 OHLC 字段

    def test_parse_columns_rows_format(self, bridge):
        columns = [
            {"name": "TIME"}, {"name": "OPEN"}, {"name": "HIGH"},
            {"name": "LOW"}, {"name": "MATCH"}, {"name": "VOLUME"},
        ]
        rows = [["2026-08-01T00:00:00", "10.0", "11.0", "9.5", "10.5", "1000000"]]
        df = bridge._parse_columns_rows_format(columns, rows)
        assert df is not None
        assert len(df) == 1
        assert "close" in df.columns
        assert df.iloc[0]["close"] == 10.5

    def test_to_qlib_format(self, bridge):
        df = pd.DataFrame({
            "date": pd.to_datetime(["2026-08-01"]),
            "open": [10.0], "close": [10.5], "high": [11.0], "low": [9.5], "volume": [1000000],
        })
        result = bridge._to_qlib_format(df, "sh600519")
        assert "symbol" in result.columns
        assert "factor" in result.columns
        assert result.iloc[0]["factor"] == 1.0
        assert result.iloc[0]["symbol"] == "sh600519"

    def test_list_available_symbols_empty(self, bridge):
        assert bridge.list_available_symbols() == []

    def test_dump_to_bin_empty(self, bridge):
        result = bridge.dump_to_bin()
        assert result is False  # 无 CSV 数据应返回 False

    def test_get_symbol_data_missing(self, bridge):
        assert bridge.get_symbol_data("sh600519") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])