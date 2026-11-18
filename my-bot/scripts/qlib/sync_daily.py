"""
FM_trade 数据同步 CLI — 桥接层 (底层: quant-signal-factory)

从 QSF src/data/wind_bridge.py 导入，保持 FM_trade CLI 兼容。
调用方: run-daily-scan.ps1 (line 156)
"""

import json
import os
import sys
import argparse
from pathlib import Path
from datetime import datetime

# 确保 QSF 在 sys.path 中
_QSF_ROOT = Path("signal-factory")
if str(_QSF_ROOT) not in sys.path:
    sys.path.insert(0, str(_QSF_ROOT))


def load_config() -> dict:
    """加载配置（兼容 FM_trade 接口）"""
    from src.data.config_loader import get_config
    return get_config()


def is_qlib_enabled(config: dict = None) -> bool:
    """检查 Qlib 是否启用"""
    if config is None:
        config = load_config()
    return config.get("qlib", {}).get("enabled", True)


def get_qlib_config(config: dict = None) -> dict:
    """获取 Qlib 配置节"""
    if config is None:
        config = load_config()
    return config.get("qlib", {})


def run_sync(force: bool = False, no_dump: bool = False) -> None:
    """执行数据同步"""
    from src.data.wind_bridge import WindBridge
    from src.data.config_loader import get_wind_json_dir, get_qlib_csv_dir, get_qlib_bin_dir

    wind_dir = get_wind_json_dir()
    csv_dir = get_qlib_csv_dir()
    bin_dir = get_qlib_bin_dir()

    bridge = WindBridge(csv_dir=csv_dir, bin_dir=bin_dir)
    result = bridge.sync(wind_json_dir=wind_dir, force=force, dump_bin=not no_dump)

    print(f"同步完成: {result.get('synced', 0)} 新文件, {result.get('skipped', 0)} 跳过")


def show_status() -> None:
    """显示数据状态"""
    from src.data.wind_bridge import get_wind_data_summary
    from src.data.config_loader import get_wind_json_dir, get_qlib_csv_dir, get_qlib_bin_dir

    wind_dir = get_wind_json_dir()
    csv_dir = get_qlib_csv_dir()
    bin_dir = get_qlib_bin_dir()

    print(f"Wind JSON 目录: {wind_dir}")
    print(f"Qlib CSV 目录: {csv_dir}")
    print(f"Qlib Bin 目录: {bin_dir}")

    try:
        summary = get_wind_data_summary(wind_dir)
        print(f"Wind 文件数: {summary.get('total_files', 0)}")
    except Exception as e:
        print(f"Wind 数据状态: {e}")

    # 检查 CSV 文件
    csv_path = Path(csv_dir)
    if csv_path.exists():
        csv_files = list(csv_path.glob("*.csv"))
        print(f"CSV 文件数: {len(csv_files)}")

    # 检查 Bin 文件
    bin_path = Path(bin_dir) / "features"
    if bin_path.exists():
        bin_dirs = [d for d in bin_path.iterdir() if d.is_dir()]
        print(f"Bin 标的总数: {len(bin_dirs)}")


def main():
    parser = argparse.ArgumentParser(description="FM_trade 数据同步 (QSF 桥接)")
    parser.add_argument("--force", action="store_true", help="强制全量同步")
    parser.add_argument("--no-dump", action="store_true", help="仅转换 CSV，不 dump bin")
    parser.add_argument("--status", action="store_true", help="显示数据状态")
    parser.add_argument("--verify", action="store_true", help="验证数据")
    args = parser.parse_args()

    if args.status:
        show_status()
        return

    if args.verify:
        from scripts.qlib.data_bridge import verify_qlib_data
        ok = verify_qlib_data()
        print(f"数据验证: {'通过' if ok else '失败'}")
        sys.exit(0 if ok else 1)

    if not is_qlib_enabled():
        print("Qlib 未启用，跳过同步")
        return

    run_sync(force=args.force, no_dump=args.no_dump)


if __name__ == "__main__":
    main()