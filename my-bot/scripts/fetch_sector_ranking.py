#!/usr/bin/env python3
"""
fetch_sector_ranking.py — 申万31行业当日排名预生成

使用 akshare stock_board_industry_summary_ths（同花顺端点，不走东财）
获取行业板块当日涨跌排名，映射到申万 31 行业，输出 JSON。

替代失效的 WebSearch 行业排名方案。

用法:
  python scripts/fetch_sector_ranking.py                       # 输出并写入 _sector_ranking.json
  python scripts/fetch_sector_ranking.py --no-output            # 仅打印
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

# ── 路径 ────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR_OUTPUT = PROJECT_ROOT / "data" / "output"

# ── 同花顺行业名称 → 申万行业代码映射 ────────────────────────────
# THS industry_summary_ths 返回 ~90 个行业（含细分），这里映射到 31 个申万一级

THS_TO_SW_MAP: Dict[str, str] = {
    # 上游周期
    "石油矿业开采": "801020",   # 采掘
    "煤炭开采":     "801020",
    "有色金属":     "801050",
    "钢铁":         "801040",
    "化工":         "801030",
    "化学制品":     "801030",
    "化学原料":     "801030",
    "塑料":         "801030",
    "橡胶":         "801030",
    "化学纤维":     "801030",
    "建筑材料":     "801710",
    "水泥":         "801710",
    "玻璃":         "801710",
    # 中游制造
    "电气设备":     "801730",
    "电源设备":     "801730",
    "高低压设备":   "801730",
    "通用机械":     "801730",   # 机械设备 → 电气设备 (closest)
    "专用设备":     "801730",
    "仪器仪表":     "801730",
    "金属制品":     "801730",
    "国防军工":     "801740",
    "航天装备":     "801740",
    "航空装备":     "801740",
    "地面兵装":     "801740",
    "船舶制造":     "801740",
    "建筑装饰":     "801720",
    "基础建设":     "801720",
    "房屋建设":     "801720",
    # 下游消费
    "汽车整车":     "801880",   # 汽车
    "汽车零部件":   "801880",
    "家用电器":     "801110",
    "食品饮料":     "801120",
    "食品加工":     "801120",
    "饮料制造":     "801120",
    "白酒":         "801120",
    "纺织服装":     "801130",
    "服装家纺":     "801130",
    "纺织制造":     "801130",
    "轻工制造":     "801140",
    "造纸":         "801140",
    "包装印刷":     "801140",
    "家用轻工":     "801140",
    "医药生物":     "801150",
    "化学制药":     "801150",
    "中药":         "801150",
    "生物制品":     "801150",
    "医疗器械":     "801150",
    "医药商业":     "801150",
    "商业贸易":     "801200",
    "零售":         "801200",
    "贸易":         "801200",
    "休闲服务":     "801210",
    "旅游":         "801210",
    "酒店餐饮":     "801210",
    # TMT / 成长
    "电子":         "801080",
    "半导体":       "801080",
    "元件":         "801080",
    "光学光电子":   "801080",
    "电子制造":     "801080",
    "计算机":       "801750",   # 计算机
    "计算机应用":   "801750",
    "计算机设备":   "801750",
    "软件服务":     "801750",
    "通信":         "801770",   # 通信
    "通信设备":     "801770",
    "通信运营":     "801770",
    "传媒":         "801760",   # 传媒
    "文化传媒":     "801760",
    "平面媒体":     "801760",
    "互联网传媒":   "801760",
    # 金融地产
    "银行":         "801780",   # 银行
    "非银金融":     "801790",   # 非银金融
    "证券":         "801790",
    "保险":         "801790",
    "多元金融":     "801790",
    "房地产":       "801180",
    "房地产开发":   "801180",
    "园区开发":     "801180",
    # 公用事业 / 防御
    "公用事业":     "801160",
    "电力":         "801160",
    "水务":         "801160",
    "燃气":         "801160",
    "环保":         "801160",
    "环保工程":     "801160",
    "交通运输":     "801170",
    "港口航运":     "801170",
    "公路铁路":     "801170",
    "物流":         "801170",
    "机场航运":     "801170",
    # 农林牧渔
    "农林牧渔":     "801010",
    "种植业":       "801010",
    "养殖业":       "801010",
    "农产品加工":   "801010",
    "饲料":         "801010",
    "渔业":         "801010",
    "种植业与林业": "801010",
    "农化制品":     "801030",   # 化肥 → 化工
    # 综合
    "综合":         "801230",
    # ── 补充映射（提升覆盖率 22→28）──
    # 801020 采掘
    "煤炭开采加工": "801020",
    "油气开采及服务": "801020",
    # 801050 有色金属
    "小金属":       "801050",
    "工业金属":     "801050",
    "贵金属":       "801050",
    "能源金属":     "801050",
    "金属新材料":   "801050",
    "非金属材料":   "801710",   # 建筑材料
    # 801110 家用电器
    "白色家电":     "801110",
    "黑色家电":     "801110",
    "小家电":       "801110",
    "厨卫电器":     "801110",
    "家电零部件":   "801110",
    # 801210 休闲服务
    "旅游及酒店":   "801210",
    "教育":         "801210",
    "美容护理":     "801210",
    "其他社会服务": "801210",
    "互联网电商":   "801200",   # 商业贸易
    # 801740 国防军工
    "军工电子":     "801740",
    "军工装备":     "801740",
    # 801890 机械设备
    "通用设备":     "801890",
    "工程机械":     "801890",
    "自动化设备":   "801890",
    "轨交设备":     "801890",
    "电机":         "801890",
    "风电设备":     "801730",   # 电气设备 (新能源)
    "光伏设备":     "801730",
    "电池":         "801730",
    "电网设备":     "801730",
    "其他电源设备": "801730",
    # Additional mappings
    "环保设备":     "801160",   # 公用事业 (环保)
    "环境治理":     "801160",
    "汽车服务及其他": "801880",  # 汽车
    "IT服务":       "801750",  # 计算机
    "软件开发":     "801750",
    "消费电子":     "801080",  # 电子
    "其他电子":     "801080",
    "电子化学品":   "801080",
    "影视院线":     "801760",  # 传媒
    "游戏":         "801760",
    "通信服务":     "801770",  # 通信
    "通信设备":     "801770",
    "石油加工贸易":  "801030",  # 化工（石油化工）
    "塑料制品":     "801030",
    "橡胶制品":     "801030",
    "家居用品":     "801140",  # 轻工制造
    "食品加工制造": "801120",  # 食品饮料
    "医疗服务":     "801150",  # 医药生物
    "公路铁路运输": "801170",  # 交通运输
    "公路铁路":     "801170",
    "物流":         "801170",
}

# ── 申万行业展示信息 ────────────────────────────────────────────

SW_SECTOR_INFO: Dict[str, Dict[str, str]] = {
    "801010": {"name": "农林牧渔", "category": "cycle"},
    "801020": {"name": "采掘",     "category": "cycle"},
    "801030": {"name": "化工",     "category": "cycle"},
    "801040": {"name": "钢铁",     "category": "cycle"},
    "801050": {"name": "有色金属", "category": "cycle"},
    "801080": {"name": "电子",     "category": "growth"},
    "801110": {"name": "家用电器", "category": "consumer"},
    "801120": {"name": "食品饮料", "category": "consumer"},
    "801130": {"name": "纺织服装", "category": "consumer"},
    "801140": {"name": "轻工制造", "category": "consumer"},
    "801150": {"name": "医药生物", "category": "defense"},
    "801160": {"name": "公用事业", "category": "defense"},
    "801170": {"name": "交通运输", "category": "cycle"},
    "801180": {"name": "房地产",   "category": "cycle"},
    "801200": {"name": "商业贸易", "category": "consumer"},
    "801210": {"name": "休闲服务", "category": "consumer"},
    "801230": {"name": "综合",     "category": "other"},
    "801710": {"name": "建筑材料", "category": "cycle"},
    "801720": {"name": "建筑装饰", "category": "cycle"},
    "801730": {"name": "电气设备", "category": "growth"},
    "801740": {"name": "国防军工", "category": "defense"},
    "801750": {"name": "计算机",   "category": "growth"},
    "801760": {"name": "传媒",     "category": "growth"},
    "801770": {"name": "通信",     "category": "growth"},
    "801780": {"name": "银行",     "category": "finance"},
    "801790": {"name": "非银金融", "category": "finance"},
    "801880": {"name": "汽车",     "category": "consumer"},
    "801890": {"name": "机械设备", "category": "growth"},
}


def fetch_ths_sector_data() -> pd.DataFrame:
    """Call stock_board_industry_summary_ths, return raw DataFrame."""
    import akshare as ak
    return ak.stock_board_industry_summary_ths()


def aggregate_to_sw_level1(raw: pd.DataFrame) -> List[Dict[str, Any]]:
    """Map THS sub-industries to SW level-1, aggregate by simple average of chg_pct.

    Returns list of sector dicts sorted by chg_pct descending.
    """
    cols = list(raw.columns)
    # Column discovery: 排名, 板块名称, 涨跌幅, 总成交量, 总成交额, 上涨家数, ...
    name_col = cols[1]   # 板块名称
    chg_col = cols[2]    # 涨跌幅
    up_col = cols[5] if len(cols) > 5 else None   # 上涨家数
    down_col = cols[6] if len(cols) > 6 else None  # 下跌家数
    vol_col = cols[3] if len(cols) > 3 else None    # 成交量
    amt_col = cols[4] if len(cols) > 4 else None    # 成交额
    leader_col = cols[9] if len(cols) > 9 else None  # 领涨股

    # Aggregate: group by SW code, average chg_pct
    sw_groups: Dict[str, Dict[str, Any]] = {}

    for _, row in raw.iterrows():
        ths_name = str(row[name_col]).strip()
        sw_code = THS_TO_SW_MAP.get(ths_name)
        if sw_code is None:
            continue  # unmapped sub-industry, skip

        try:
            chg_pct = float(row[chg_col])
        except (ValueError, TypeError):
            chg_pct = 0.0

        up_count = int(row[up_col]) if up_col and pd.notna(row.get(up_col, 0)) else 0
        down_count = int(row[down_col]) if down_col and pd.notna(row.get(down_col, 0)) else 0
        vol = float(row[vol_col]) if vol_col and pd.notna(row.get(vol_col, 0)) else 0.0
        amt = float(row[amt_col]) if amt_col and pd.notna(row.get(amt_col, 0)) else 0.0
        leader = str(row[leader_col]) if leader_col and pd.notna(row.get(leader_col, "")) else ""

        if sw_code not in sw_groups:
            sw_groups[sw_code] = {
                "sw_code": sw_code,
                "chg_sum": 0.0,
                "count": 0,
                "up_count": 0,
                "down_count": 0,
                "vol_sum": 0.0,
                "amt_sum": 0.0,
                "sub_sectors": [],
            }

        g = sw_groups[sw_code]
        g["chg_sum"] += chg_pct
        g["count"] += 1
        g["up_count"] += up_count
        g["down_count"] += down_count
        g["vol_sum"] += vol
        g["amt_sum"] += amt
        g["sub_sectors"].append({"name": ths_name, "chg_pct": round(chg_pct, 2), "leader": leader})

    # Build output list
    sectors: List[Dict[str, Any]] = []
    for sw_code, g in sw_groups.items():
        info = SW_SECTOR_INFO.get(sw_code, {"name": sw_code, "category": "other"})
        avg_chg = round(g["chg_sum"] / g["count"], 2)
        sectors.append({
            "sw_code": sw_code,
            "name": info["name"],
            "category": info["category"],
            "chg_pct": avg_chg,
            "chg_direction": "up" if avg_chg > 0 else ("down" if avg_chg < 0 else "flat"),
            "sub_count": g["count"],
            "up_count": g["up_count"],
            "down_count": g["down_count"],
            "total_volume": round(g["vol_sum"], 0),
            "total_amount": round(g["amt_sum"], 0),
            "sub_sectors": sorted(g["sub_sectors"], key=lambda x: x["chg_pct"], reverse=True),
        })

    # Sort by chg_pct descending
    sectors.sort(key=lambda x: x["chg_pct"], reverse=True)

    # Assign ranks
    for i, s in enumerate(sectors):
        s["rank"] = i + 1

    return sectors


def build_sector_ranking_json() -> Dict[str, Any]:
    """Main: fetch THS sector data and build output JSON."""
    print("[fetch_sector_ranking] 获取 31 行业板块排名 (akshare THS endpoint)...")

    try:
        raw = fetch_ths_sector_data()
        print(f"  OK: {len(raw)} sub-industries from THS")
    except Exception as e:
        print(f"  FAIL: {e}")
        raise

    sectors = aggregate_to_sw_level1(raw)

    mapped_codes = {s["sw_code"] for s in sectors}
    missing_codes = set(SW_SECTOR_INFO.keys()) - mapped_codes

    top_3 = [s["name"] for s in sectors[:3]]
    bottom_3 = [s["name"] for s in sectors[-3:]]

    result = {
        "data_date": datetime.now().strftime("%Y-%m-%d"),
        "generated_at": datetime.now().isoformat(),
        "source": "akshare stock_board_industry_summary_ths (同花顺)",
        "sector_count": len(sectors),
        "sw_codes_mapped": len(mapped_codes),
        "sw_codes_missing": sorted(missing_codes) if missing_codes else [],
        "top_3": top_3,
        "bottom_3": bottom_3,
        "sectors": sectors,
    }

    return result


def fetch_and_save(output_path: Optional[Path] = None) -> Dict[str, Any]:
    """Main entry."""
    result = build_sector_ranking_json()

    print(f"  SW sectors mapped: {result['sw_codes_mapped']}/28")
    if result["sw_codes_missing"]:
        print(f"  Missing: {', '.join(result['sw_codes_missing'])}")
    print(f"  Top 3: {', '.join(result['top_3'])}")
    print(f"  Bottom 3: {', '.join(result['bottom_3'])}")

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"  Saved: {output_path}")

    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Fetch 31-sector daily ranking")
    parser.add_argument("--no-output", action="store_true", help="Print only, don't save")
    args = parser.parse_args()

    output_path = None if args.no_output else (DATA_DIR_OUTPUT / "_sector_ranking.json")
    result = fetch_and_save(output_path=output_path)

    # Print table
    print()
    print(f"  {'Rank':<5} {'Sector':<10} {'Chg%':>8}  Dir  Sub  {'Category'}")
    print("  " + "-" * 52)
    for s in result["sectors"]:
        d = "+" if s["chg_pct"] > 0 else ("-" if s["chg_pct"] < 0 else " ")
        print(f"  {s['rank']:<5} {s['name']:<10} {d}{abs(s['chg_pct']):>7.2f}%  {s['chg_direction']:<4} {s['sub_count']:<4} {s['category']}")


if __name__ == "__main__":
    main()