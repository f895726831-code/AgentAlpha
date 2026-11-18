"""
主题扩展脚本 — 从识别主题到全市场候选发现。

核心功能:
  1. 获取主题成分股（主: akshare 东财概念板块 / 备: 全市场关键词搜索）
  2. LLM 业务纯度评分（DeepSeek/Claude 评估每只股票与主题的业务关联度）
  3. 技术面 + 基本面过滤
  4. 输出候选列表，准备进入 Gate 1 回测关

集成路径:
  theme_expand.py → Top N 候选 → 手动审阅 → Gate 1 回测 → 通过后加入自选池
  cron: 周度扫描（周六） + 事件驱动（a-share-primary-theme-identification 触发）

上限控制:
  - 最多 5 个活跃主题
  - 每主题最多 10 候选
  - 总计最多 50 候选（硬上限）

用法:
  python scripts/theme_expand.py --theme "短剧游戏"
  python scripts/theme_expand.py --theme "低空经济" --max-candidates 5
  python scripts/theme_expand.py --scan-active  # 扫描所有活跃主题
  python scripts/theme_expand.py --theme "机器人" --dry-run  # 仅展示，不更新文件
"""

import argparse
import json
import os
import sys
import time
import warnings
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# 密钥加载（复用 wsl2/agent/llm_client.py 的 .env 加载逻辑）
# ---------------------------------------------------------------------------

def _load_dotenv():
    """Auto-load .env from project directories (if not already set)."""
    if os.environ.get("DEEPSEEK_API_KEY"):
        return
    candidates = [
        Path(__file__).resolve().parent.parent / "scripts" / "wsl2" / ".env",
        Path(__file__).resolve().parent.parent / ".env",
        Path.cwd() / ".env",
    ]
    for env_path in candidates:
        if env_path.exists():
            try:
                with open(env_path) as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            if k.strip() not in os.environ:
                                os.environ[k.strip()] = v.strip()
            except Exception:
                pass

_load_dotenv()

# ---------------------------------------------------------------------------
# 路径配置
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent  # my-bot/
DATA_DIR = PROJECT_ROOT / "data"
THEME_CANDIDATES_FILE = DATA_DIR / "theme_candidates.json"
THEME_ARCHIVE_DIR = DATA_DIR / "theme_archive"
WATCHLIST_FILE = DATA_DIR / "signal_watchlist.json"
RISK_POLICY_FILE = DATA_DIR / "risk-policy.json"

# ---------------------------------------------------------------------------
# 上限常量
# ---------------------------------------------------------------------------
MAX_ACTIVE_THEMES = 5
MAX_CANDIDATES_PER_THEME = 10
MAX_TOTAL_CANDIDATES = 50

# ---------------------------------------------------------------------------
# 主题 → 申万行业映射 (东财端点不可用时的第一降级)
# ---------------------------------------------------------------------------
THEME_TO_SHENWAN = {
    "短剧游戏": ["801760"],           # 传媒
    "短剧": ["801760"],               # 传媒
    "影视": ["801760"],               # 传媒
    "游戏": ["801760", "801750"],     # 传媒 + 计算机
    "人工智能": ["801750", "801080", "801770"],  # 计算机 + 电子 + 通信
    "AI": ["801750", "801080", "801770"],
    "机器人": ["801890", "801080"],   # 机械设备 + 电子
    "低空经济": ["801890", "801080", "801170"],  # 机械设备 + 电子 + 国防军工
    "新能源": ["801730", "801030"],   # 电气设备 + 化工
    "半导体": ["801080"],             # 电子
    "数据要素": ["801750", "801760"], # 计算机 + 传媒
    "华为产业链": ["801750", "801080", "801770"],
    "消费电子": ["801080"],           # 电子
    "创新药": ["801150"],             # 医药生物
    "光伏": ["801730"],               # 电气设备
    "锂电池": ["801030", "801730"],   # 化工 + 电气设备
    "军工": ["801170"],               # 国防军工
    "汽车": ["801880"],               # 汽车
    "食品饮料": ["801120"],           # 食品饮料
    "银行": ["801780"],               # 银行
}

# 申万行业代码 → 名称
SHENWAN_NAMES = {
    "801760": "传媒",
    "801750": "计算机",
    "801080": "电子",
    "801770": "通信",
    "801890": "机械设备",
    "801730": "电气设备",
    "801030": "化工",
    "801170": "国防军工",
    "801150": "医药生物",
    "801120": "食品饮料",
    "801880": "汽车",
    "801780": "银行",
    "801140": "轻工制造",
    "801180": "房地产",
    "801050": "有色金属",
    "801020": "采掘",
    "801710": "建筑材料",
    "801110": "家用电器",
    "801130": "纺织服装",
    "801200": "商业贸易",
    "801210": "休闲服务",
    "801160": "公用事业",
    "801790": "非银金融",
    "801040": "钢铁",
    "801230": "综合",
}

# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class ThemeCandidate:
    """主题候选标的"""
    code: str               # 股票代码，如 "002343.SZ"
    name: str               # 股票名称
    purity_score: float     # 业务纯度评分 (1-5, LLM 评估)
    purity_reason: str      # 纯度评分理由 (LLM 输出)
    market_cap: float       # 总市值（亿元）
    pe_ttm: float           # PE(TTM)，负值表示亏损
    industry: str           # 申万一级行业
    sector_code: str        # 概念板块代码（东财）
    in_watchlist: bool      # 是否已在自选池
    discovered_at: str      # 发现时间 ISO 8601
    status: str = "pending" # pending | gate1_passed | gate1_failed | gate2_passed | archived

@dataclass
class ActiveTheme:
    """活跃主题"""
    name: str               # 主题名称
    concept_code: str       # 东财概念板块代码
    concept_name: str       # 东财概念板块名称
    first_identified: str   # 首次识别时间
    last_updated: str       # 最后更新时间
    active: bool = True     # 是否活跃
    candidates: list[ThemeCandidate] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 数据获取层
# ---------------------------------------------------------------------------

def get_all_a_stocks() -> pd.DataFrame:
    """获取全部 A 股列表（名称 + 代码）。"""
    try:
        import akshare as ak
        df = ak.stock_info_a_code_name()
        df = df.rename(columns={"代码": "code", "名称": "name"})
        df["code"] = df["code"].astype(str).str.zfill(6)
        return df
    except Exception as e:
        print(f"[WARN] stock_info_a_code_name 失败: {e}")
        return pd.DataFrame()


def get_concept_board_list() -> pd.DataFrame:
    """
    获取东方财富全部概念板块列表。

    Returns:
        DataFrame with columns: 板块代码, 板块名称, 涨跌幅, ...
        失败返回空 DataFrame。
    """
    try:
        import akshare as ak
        df = ak.stock_board_concept_name_em()
        return df
    except Exception as e:
        print(f"[WARN] 无法获取东财概念板块列表: {e}")
        return pd.DataFrame()


def get_concept_constituents(concept_code: str) -> pd.DataFrame:
    """
    获取指定概念板块的成分股。

    Args:
        concept_code: 东财概念板块代码，如 "BK0728"

    Returns:
        DataFrame with columns: 代码, 名称, 涨跌幅, 最新价, ...
        失败返回空 DataFrame。
    """
    try:
        import akshare as ak
        df = ak.stock_board_concept_cons_em(symbol=concept_code)
        return df
    except Exception as e:
        print(f"[WARN] 无法获取概念板块 {concept_code} 成分股: {e}")
        return pd.DataFrame()


def find_concept_code(theme_name: str, concept_df: pd.DataFrame) -> Optional[str]:
    """
    根据主题名称模糊匹配东财概念板块代码。

    匹配策略:
      1. 精确匹配
      2. 包含匹配（主题名 in 板块名 或 板块名 in 主题名）
      3. 关键词拆分匹配
    """
    if concept_df.empty:
        return None

    names = concept_df["板块名称"].tolist()
    codes = concept_df["板块代码"].tolist()

    # 1. 精确匹配
    for i, name in enumerate(names):
        if name == theme_name:
            return codes[i]

    # 2. 包含匹配
    for i, name in enumerate(names):
        if theme_name in name or name in theme_name:
            return codes[i]

    # 3. 关键词拆分匹配（取 theme_name 中长度 ≥2 的关键词）
    keywords = [k for k in theme_name if len(k) >= 2]
    for i, name in enumerate(names):
        match_count = sum(1 for k in keywords if k in name)
        if match_count >= len(keywords) * 0.6:  # 60% 关键词匹配
            return codes[i]

    return None


def keyword_search_stocks(keywords: list[str], all_stocks: pd.DataFrame) -> pd.DataFrame:
    """
    当东财概念板块不可用时，用关键词在全市场搜索相关标的。

    搜索范围: 股票名称 + 申万行业分类（如果可用）

    Args:
        keywords: 搜索关键词列表，如 ["短剧", "影视", "网剧"]
        all_stocks: 全量 A 股列表

    Returns:
        匹配的股票 DataFrame
    """
    if all_stocks.empty:
        return pd.DataFrame()

    mask = pd.Series(False, index=all_stocks.index)
    for kw in keywords:
        # 搜索名称
        mask |= all_stocks["name"].str.contains(kw, na=False)

    return all_stocks[mask].copy()


def get_shenwan_industry_stocks(sw_codes: list[str]) -> pd.DataFrame:
    """
    从申万行业指数获取成分股（东财端点不可用时的第一降级）。

    使用 akshare index_component_sw() — 此接口走中证指数而非东财，
    不受东财封锁影响。

    Args:
        sw_codes: 申万行业代码列表，如 ["801760", "801750"]

    Returns:
        DataFrame with columns: code, name, industry, weight
        失败返回空 DataFrame
    """
    try:
        import akshare as ak
    except ImportError:
        return pd.DataFrame()

    all_components = []
    for sw_code in sw_codes:
        try:
            df = ak.index_component_sw(symbol=sw_code)
            if df.empty:
                continue
            # 列名: 序号, 证券代码, 证券名称, 计入权重, 计入日期
            col_map = {
                "证券代码": "code", "证券名称": "name",
                "计入权重": "weight", "计入日期": "entry_date",
            }
            df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
            df["code"] = df["code"].astype(str).str.zfill(6)
            df["industry"] = SHENWAN_NAMES.get(sw_code, sw_code)
            df["sw_code"] = sw_code
            all_components.append(df)
        except Exception as e:
            print(f"  [WARN] 申万行业 {sw_code} ({SHENWAN_NAMES.get(sw_code, '?')}) 获取失败: {e}")

    if not all_components:
        return pd.DataFrame()

    result = pd.concat(all_components, ignore_index=True)
    # 去重（同一股票可能属于多个行业）
    result = result.drop_duplicates(subset=["code"], keep="first")
    print(f"  申万行业成分股: {len(result)} 只 (覆盖 {len(sw_codes)} 个行业)")
    return result


# ---------------------------------------------------------------------------
# LLM 纯度评分层
# ---------------------------------------------------------------------------

def build_purity_prompt(theme_name: str, candidates: list[dict]) -> str:
    """
    构建 LLM 纯度评分 prompt。

    要求 LLM 对每只候选股输出:
      - purity_score: 1-5 的业务关联度评分
      - purity_reason: 一句话理由
    """
    candidate_lines = []
    for i, c in enumerate(candidates):
        candidate_lines.append(
            f"{i+1}. {c['code']} {c['name']} | 行业: {c.get('industry', '未知')} | "
            f"市值: {c.get('market_cap', 'N/A')}亿"
        )

    prompt = f"""你是A股行业研究员。评估以下候选股与主题"{theme_name}"的业务关联度。

主题"{theme_name}"包含两个子方向，两类标的同等重要：
  a) 内容制作方：影视/IP/网文公司，制作发行短剧内容，短剧爆发直接转化为收入和利润
  b) 互动游戏方：游戏公司，将短剧与互动游戏融合（短剧+游戏/互动叙事），开发短剧互动游戏产品

评分标准:
- 5分: 核心主业直接受益于该主题，业绩弹性大（内容方：短剧收入占比显著；游戏方：已上线短剧互动游戏产品）
- 4分: 主业与该主题高度相关，有明确布局（内容方：已宣布短剧战略布局；游戏方：短剧+游戏融合产品在研）
- 3分: 业务涉及该主题，但非核心驱动
- 2分: 间接关联，概念属性为主
- 1分: 无实质业务关联，仅概念标签

候选列表:
{chr(10).join(candidate_lines)}

请按以下 JSON 格式输出（只输出 JSON，不要其他内容）:
```json
[
  {{"code": "000001.SZ", "name": "某某股份", "purity_score": 4, "purity_reason": "一句话理由"}},
  ...
]
```

按 purity_score 从高到低排序。只评估你了解其业务的股票，对不熟悉的标注 purity_score=0 并说明"不了解该标的"。
"""
    return prompt


def score_purity_with_llm(
    theme_name: str,
    candidates: list[dict],
    model: str = "deepseek",
    batch_size: int = 40,
) -> list[dict]:
    """
    调用 LLM 对候选股做业务纯度评分。

    大候选池自动分批评分（每批 {batch_size} 只），合并结果。

    调用链:
      1. openai 包 + DeepSeek (deepseek-chat)
      2. requests 直连 DeepSeek API
      3. 启发式降级

    Args:
        theme_name: 主题名称
        candidates: 候选股列表
        model: "deepseek" 或 "claude"
        batch_size: 每批最大候选数

    Returns:
        带 purity_score 和 purity_reason 的候选列表，按纯度降序排列
    """
    if not candidates:
        return []

    # 分批处理
    if len(candidates) <= batch_size:
        batches = [candidates]
    else:
        batches = [
            candidates[i:i + batch_size]
            for i in range(0, len(candidates), batch_size)
        ]
        print(f"  分批评分: {len(batches)} 批 × ~{batch_size} 只")

    all_scored = []
    for bi, batch in enumerate(batches):
        batch_label = f"批次 {bi+1}/{len(batches)}" if len(batches) > 1 else ""
        prompt = build_purity_prompt(theme_name, batch)
        raw = _call_llm_api(prompt, model, batch_label)

        if raw is None:
            # 单批失败 → 启发式降级
            all_scored.extend(_heuristic_purity_score(theme_name, batch))
            continue

        # 解析 JSON
        scored = _parse_llm_json(raw, theme_name, batch)
        all_scored.extend(scored)

    if not all_scored:
        return []

    # 去重（按 code）
    seen = set()
    unique = []
    for item in all_scored:
        if item["code"] not in seen:
            seen.add(item["code"])
            unique.append(item)

    # 按纯度降序排列
    unique.sort(key=lambda x: x.get("purity_score", 0), reverse=True)
    return unique


def _call_llm_api(prompt: str, model: str, label: str = "") -> Optional[str]:
    """调用 LLM API，返回原始响应文本。失败返回 None。"""
    import os

    def _try_openai_chat():
        from openai import OpenAI
        if model == "deepseek":
            api_key = os.getenv("DEEPSEEK_API_KEY", "")
            if not api_key:
                raise ValueError("DEEPSEEK_API_KEY 未设置")
            client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com/v1")
            response = client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=4000,
            )
            return response.choices[0].message.content
        else:
            api_key = os.getenv("ANTHROPIC_API_KEY", "")
            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY 未设置")
            client = OpenAI(api_key=api_key, base_url="https://api.anthropic.com/v1")
            response = client.chat.completions.create(
                model="claude-sonnet-4-20250514",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=4000,
            )
            return response.choices[0].message.content

    def _try_requests_direct():
        import requests as req
        api_key = os.getenv("DEEPSEEK_API_KEY", "")
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY 未设置")
        resp = req.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 4000,
            },
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    prefix = f"{label}: " if label else ""
    for llm_label, fn in [
        ("openai+DeepSeek", _try_openai_chat),
        ("requests+DeepSeek", _try_requests_direct),
    ]:
        try:
            result = fn()
            print(f"  [OK] {prefix}LLM 调用成功 ({llm_label})")
            return result
        except ImportError as e:
            print(f"  [WARN] {prefix}{llm_label}: 缺少依赖 ({e})")
        except Exception as e:
            print(f"  [WARN] {prefix}{llm_label}: {e}")

    return None


def _parse_llm_json(raw: str, theme_name: str, batch: list[dict]) -> list[dict]:
    """解析 LLM JSON 输出，失败退回启发式评分。"""
    try:
        # 提取 JSON 块
        json_str = raw
        if "```json" in json_str:
            json_str = json_str.split("```json")[1].split("```")[0]
        elif "```" in json_str:
            json_str = json_str.split("```")[1].split("```")[0]

        # 修复常见截断：补全最后一个不完整的对象
        json_str = json_str.strip()
        if json_str.endswith(","):
            json_str = json_str[:-1]
        if not json_str.endswith("]"):
            # 尝试找到最后一个完整对象
            last_complete = json_str.rfind('"}')
            if last_complete > 0:
                json_str = json_str[:last_complete + 2] + "\n]"

        scored = json.loads(json_str)

        if not isinstance(scored, list):
            raise ValueError(f"Expected list, got {type(scored)}")

        result = []
        for item in scored:
            if item.get("purity_score", 0) >= 2:
                result.append(item)
        return result

    except (json.JSONDecodeError, ValueError, IndexError) as e:
        print(f"  [WARN] LLM JSON 解析失败: {e}")
        print(f"  原始输出(截断): {raw[:200]}...")
        return _heuristic_purity_score(theme_name, batch)


def _heuristic_purity_score(theme_name: str, candidates: list[dict]) -> list[dict]:
    """
    本地启发式评分（LLM 不可用时的降级方案）。

    评分逻辑:
      - 行业匹配（申万行业 = 主题对应行业）：基准 2 分
      - 名称关键词匹配：+1-2 分
      - 行业指数权重前 20%：+0.5 分（龙头溢价）
      - 最终 clamp 到 [1, 5]

    注意：启发式评分远不如 LLM 准确，尤其在行业内部区分纯度时。
    仅用于 LLM 不可用时的紧急降级。
    """
    keywords = _get_theme_keywords(theme_name)
    sw_codes = _get_theme_shenwan_codes(theme_name)
    sw_names = [SHENWAN_NAMES.get(c, "") for c in sw_codes]

    result = []
    for c in candidates:
        name = c.get("name", "")
        industry = c.get("industry", "")
        weight = c.get("weight", 0) or 0

        # 基准分：行业匹配 +2，否则 +1
        score = 2.0 if industry in sw_names else 1.0

        # 名称关键词加分
        name_matches = sum(1 for kw in keywords if kw in name)
        score += min(2, name_matches)

        # 行业权重加分（前 20% +0.5）
        if weight > 0 and weight >= 0.5:
            score += 0.5

        score = min(5, max(1, int(score)))

        reasons = []
        if industry in sw_names:
            reasons.append(f"行业: {industry}")
        if name_matches > 0:
            reasons.append(f"名称匹配 {name_matches} 个关键词")
        if weight >= 0.5:
            reasons.append(f"权重 {weight:.2f}%")

        reason_str = "; ".join(reasons) if reasons else "仅名称匹配"

        result.append({
            "code": c["code"],
            "name": c["name"],
            "purity_score": score,
            "purity_reason": f"[启发式] {reason_str}",
            "weight": weight,
        })

    # 多键排序：纯度 desc, 权重 desc
    result.sort(key=lambda x: (x["purity_score"], x.get("weight", 0) or 0), reverse=True)
    return result


# ---------------------------------------------------------------------------
# 过滤与整合层
# ---------------------------------------------------------------------------

def load_existing_watchlist() -> set:
    """加载当前自选池代码集合。"""
    if not WATCHLIST_FILE.exists():
        return set()
    try:
        with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return {item.get("code", "") for item in data}
        if isinstance(data, dict):
            return set(data.keys())
        return set()
    except Exception:
        return set()


def load_existing_candidates() -> dict[str, ActiveTheme]:
    """加载已有的主题候选池。"""
    if not THEME_CANDIDATES_FILE.exists():
        return {}
    try:
        with open(THEME_CANDIDATES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        result = {}
        for t in data.get("themes", []):
            theme = ActiveTheme(**t)
            theme.candidates = [ThemeCandidate(**c) for c in t.get("candidates", [])]
            result[theme.name] = theme
        return result
    except Exception as e:
        print(f"[WARN] 加载已有候选池失败: {e}")
        return {}


def save_theme_candidates(themes: dict[str, ActiveTheme]):
    """保存主题候选池到文件。"""
    THEME_CANDIDATES_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "updated_at": datetime.now().isoformat(),
        "config": {
            "max_active_themes": MAX_ACTIVE_THEMES,
            "max_candidates_per_theme": MAX_CANDIDATES_PER_THEME,
            "max_total_candidates": MAX_TOTAL_CANDIDATES,
        },
        "themes": [],
    }
    for theme in themes.values():
        t = asdict(theme)
        t["candidates"] = [asdict(c) for c in theme.candidates]
        data["themes"].append(t)

    with open(THEME_CANDIDATES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[OK] 主题候选已保存到 {THEME_CANDIDATES_FILE}")


def add_to_watchlist(candidate: ThemeCandidate) -> bool:
    """
    将通过 Gate 1 的候选加入自选池。

    Returns True 如果成功添加或已存在。
    """
    watchlist = []
    if WATCHLIST_FILE.exists():
        try:
            with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
                watchlist = json.load(f)
        except Exception:
            watchlist = []

    existing_codes = {item.get("code", "") for item in watchlist if isinstance(item, dict)}

    if candidate.code in existing_codes:
        print(f"  [SKIP] {candidate.code} {candidate.name} 已在自选池中")
        return True

    # 标准化代码格式（6位数字）
    code_clean = candidate.code.replace(".SZ", "").replace(".SH", "").replace(".BJ", "")

    watchlist.append({
        "code": candidate.code,
        "name": candidate.name,
        "added_at": datetime.now().isoformat(),
        "source": f"theme_expansion:{candidate.purity_reason[:30]}",
        "purity_score": candidate.purity_score,
    })

    with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
        json.dump(watchlist, f, ensure_ascii=False, indent=2)

    print(f"  [ADDED] {candidate.code} {candidate.name} → 自选池 (纯度: {candidate.purity_score}/5)")
    return True


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def expand_theme(
    theme_name: str,
    max_candidates: int = MAX_CANDIDATES_PER_THEME,
    use_llm: bool = True,
    llm_model: str = "deepseek",
    dry_run: bool = False,
) -> list[ThemeCandidate]:
    """
    执行主题扩展主流程。

    步骤:
      1. 获取概念板块成分股（或关键词搜索降级）
      2. LLM 业务纯度评分
      3. 过滤 + 排序
      4. 存储候选池

    Args:
        theme_name: 主题名称，如 "短剧游戏"
        max_candidates: 最多返回的候选数
        use_llm: 是否使用 LLM 评分
        llm_model: LLM 模型选择
        dry_run: 仅展示，不更新任何文件

    Returns:
        候选列表，按纯度降序排列
    """
    print(f"\n{'='*60}")
    print(f"  主题扩展: {theme_name}")
    print(f"{'='*60}")

    # Step 1: 获取成分股 (三级降级: 东财概念 → 申万行业 → 关键词)
    print("\n[Step 1] 获取主题成分股...")

    concept_df = get_concept_board_list()
    concept_code = find_concept_code(theme_name, concept_df)

    stocks_df = pd.DataFrame()
    source_label = ""
    source_tier = ""

    if concept_code:
        print(f"  概念板块: {concept_code}")
        stocks_df = get_concept_constituents(concept_code)
        if not stocks_df.empty:
            source_label = f"东财概念板块 {concept_code}"
            source_tier = "concept_board"
            print(f"  获取 {len(stocks_df)} 只成分股 (来源: {source_label})")
    else:
        print(f"  东财概念板块不可用，降级到申万行业")

    if stocks_df.empty:
        # 降级 1: 申万行业指数
        sw_codes = _get_theme_shenwan_codes(theme_name)
        if sw_codes:
            sw_names = [SHENWAN_NAMES.get(c, c) for c in sw_codes]
            print(f"  申万行业: {', '.join(sw_names)} ({', '.join(sw_codes)})")
            stocks_df = get_shenwan_industry_stocks(sw_codes)
            if not stocks_df.empty:
                source_label = f"申万行业 ({', '.join(sw_names)})"
                source_tier = "shenwan"
        else:
            print(f"  无申万行业映射，降级到关键词搜索")

    if stocks_df.empty:
        # 降级 2: 关键词搜索
        keywords = [theme_name] + _get_theme_keywords(theme_name)
        all_stocks = get_all_a_stocks()
        stocks_df = keyword_search_stocks(keywords, all_stocks)
        source_label = f"关键词搜索 ({', '.join(keywords[:3])})"
        source_tier = "keyword"
        print(f"  关键词搜索获取 {len(stocks_df)} 只候选 (来源: {source_label})")

    if stocks_df.empty:
        print("[FAIL] 三级数据源全部不可用，未获取到任何候选标的")
        return []

    # 数据源标注
    print(f"  数据源层级: {source_tier} ({source_label})")

    # Step 2: 预处理候选列表
    print("\n[Step 2] 预处理候选...")

    # 统一列名
    col_map = {
        "代码": "code", "名称": "name",
        "最新价": "price", "涨跌幅": "pct_change",
        "总市值": "market_cap", "市盈率-动态": "pe_ttm",
    }
    stocks_df = stocks_df.rename(columns={k: v for k, v in col_map.items() if k in stocks_df.columns})

    # 确保 code 列存在且标准化
    if "code" in stocks_df.columns:
        stocks_df["code"] = stocks_df["code"].astype(str).str.zfill(6)
    else:
        print("[FAIL] 缺少股票代码列")
        return []

    # 加载自选池做交叉检查
    watchlist_codes = load_existing_watchlist()

    # 构建 LLM 评分输入
    candidates_input = []
    for _, row in stocks_df.iterrows():
        code = str(row["code"]).zfill(6)
        # 确定交易所后缀
        if code.startswith("688"):
            full_code = f"{code}.SH"
        elif code.startswith(("300", "301")):
            full_code = f"{code}.SZ"
        elif code.startswith(("8", "4", "9")):
            full_code = f"{code}.BJ"
        elif code.startswith("6"):
            full_code = f"{code}.SH"
        else:
            full_code = f"{code}.SZ"

        candidates_input.append({
            "code": full_code,
            "name": str(row.get("name", "")),
            "industry": str(row.get("industry", "")),
            "market_cap": round(float(row.get("market_cap", 0)) / 1e8, 1) if pd.notna(row.get("market_cap")) else None,
            "weight": float(row.get("weight", 0)) if pd.notna(row.get("weight")) else 0,
            "in_watchlist": full_code in watchlist_codes or code in watchlist_codes,
        })

    # Step 3: LLM 纯度评分
    print(f"\n[Step 3] 业务纯度评分 ({len(candidates_input)} 只候选)...")

    if use_llm:
        print(f"  使用模型: {llm_model}")
        scored = score_purity_with_llm(theme_name, candidates_input, model=llm_model)
    else:
        scored = _heuristic_purity_score(theme_name, candidates_input)

    # 过滤低纯度（<2分）和"不了解"的
    scored = [s for s in scored if s.get("purity_score", 0) >= 2]

    # 按纯度降序 + 已自选排后
    scored.sort(key=lambda x: (x.get("in_watchlist", False), -x.get("purity_score", 0)))

    # 截取 Top N
    scored = scored[:max_candidates]

    print(f"  有效候选: {len(scored)} 只 (纯度 ≥ 2)")

    # Step 4: 构建 ThemeCandidate 对象
    print("\n[Step 4] 构建候选列表...")

    now = datetime.now().isoformat()
    candidates = []
    for item in scored:
        c = ThemeCandidate(
            code=item["code"],
            name=item["name"],
            purity_score=item["purity_score"],
            purity_reason=item.get("purity_reason", ""),
            market_cap=item.get("market_cap", 0) or 0,
            pe_ttm=item.get("pe_ttm", 0) or 0,
            industry=item.get("industry", ""),
            sector_code=concept_code or "keyword_search",
            in_watchlist=item.get("in_watchlist", False),
            discovered_at=now,
            status="pending",
        )
        candidates.append(c)

    # Step 5: 更新主题候选池
    print("\n[Step 5] 更新候选池...")

    existing_themes = load_existing_candidates()

    # 上限检查
    active_count = sum(1 for t in existing_themes.values() if t.active)
    if active_count >= MAX_ACTIVE_THEMES and theme_name not in existing_themes:
        print(f"  [WARN] 活跃主题已达上限 ({MAX_ACTIVE_THEMES})，{theme_name} 将标记为非活跃")

    total_candidates = sum(len(t.candidates) for t in existing_themes.values())
    if total_candidates + len(candidates) > MAX_TOTAL_CANDIDATES:
        print(f"  [WARN] 总候选将超过上限 ({MAX_TOTAL_CANDIDATES})，截断至上限")
        allowed = MAX_TOTAL_CANDIDATES - total_candidates
        candidates = candidates[:max(0, allowed)]

    if theme_name in existing_themes:
        existing_themes[theme_name].last_updated = now
        existing_themes[theme_name].candidates = candidates
    else:
        existing_themes[theme_name] = ActiveTheme(
            name=theme_name,
            concept_code=concept_code or "",
            concept_name=source_label,
            first_identified=now,
            last_updated=now,
            active=active_count < MAX_ACTIVE_THEMES,
            candidates=candidates,
        )

    if not dry_run:
        save_theme_candidates(existing_themes)
    else:
        print("  [DRY-RUN] 不保存文件")

    # Step 6: 打印结果
    print(f"\n{'='*60}")
    print(f"  扩展结果: {theme_name}")
    print(f"{'='*60}")
    print(f"{'#':<3} {'代码':<12} {'名称':<10} {'纯度':<5} {'市值(亿)':<10} {'自选':<5} {'理由'}")
    print("-" * 75)
    for i, c in enumerate(candidates):
        wl = "✓" if c.in_watchlist else "-"
        mc = f"{c.market_cap:.0f}" if c.market_cap else "N/A"
        print(f"{i+1:<3} {c.code:<12} {c.name:<10} {c.purity_score}/5{'':<2} {mc:<10} {wl:<5} {c.purity_reason[:40]}")
    print("-" * 75)
    print(f"数据来源: {source_label} | LLM: {llm_model if use_llm else '启发式'} | 候选: {len(candidates)}")
    print(f"自选池内: {sum(1 for c in candidates if c.in_watchlist)} | 新发现: {sum(1 for c in candidates if not c.in_watchlist)}")

    return candidates


def _get_theme_shenwan_codes(theme_name: str) -> list[str]:
    """获取主题对应的申万行业代码。"""
    # 精确匹配
    if theme_name in THEME_TO_SHENWAN:
        return THEME_TO_SHENWAN[theme_name]
    # 模糊匹配
    for key, codes in THEME_TO_SHENWAN.items():
        if key in theme_name or theme_name in key:
            return codes
    return []


def _get_theme_keywords(theme_name: str) -> list[str]:
    """获取主题的扩展搜索关键词。"""
    KEYWORD_MAP = {
        "短剧游戏": ["短剧", "影视", "网剧", "内容制作", "IP运营", "MCN"],
        "短剧": ["短剧", "影视", "网剧", "传媒"],
        "AI": ["人工智能", "AI", "大模型", "智能"],
        "机器人": ["机器人", "减速器", "伺服", "机器视觉", "自动化"],
        "低空经济": ["无人机", "低空", "飞行器", "eVTOL", "通航"],
        "新能源": ["光伏", "风电", "锂电", "储能"],
        "半导体": ["芯片", "晶圆", "封测", "光刻"],
        "人工智能": ["AI", "人工智能", "算法", "大模型", "深度学习"],
        "数据要素": ["数据", "数据确权", "数据交易", "数据安全"],
        "华为产业链": ["华为", "鸿蒙", "欧拉", "昇腾", "鲲鹏"],
        "消费电子": ["手机", "耳机", "智能穿戴", "AR", "VR"],
    }
    return KEYWORD_MAP.get(theme_name, [theme_name])


def scan_active_themes(use_llm: bool = True, dry_run: bool = False):
    """扫描所有活跃主题，更新候选池。"""
    existing = load_existing_candidates()
    active = [t for t in existing.values() if t.active]

    if not active:
        print("没有活跃主题需要扫描。请先用 --theme 创建主题。")
        return

    print(f"扫描 {len(active)} 个活跃主题...")
    total_new = 0
    for theme in active:
        candidates = expand_theme(
            theme.name,
            max_candidates=MAX_CANDIDATES_PER_THEME,
            use_llm=use_llm,
            dry_run=dry_run,
        )
        new_count = sum(1 for c in candidates if not c.in_watchlist)
        total_new += new_count

    print(f"\n[完成] 共扫描 {len(active)} 个主题，新发现 {total_new} 个候选")


def promote_to_watchlist(theme_name: str):
    """将通过 Gate 1 的候选加入自选池。"""
    existing = load_existing_candidates()
    if theme_name not in existing:
        print(f"主题 '{theme_name}' 不在候选池中")
        return

    theme = existing[theme_name]
    passed = [c for c in theme.candidates if c.status == "gate1_passed"]

    if not passed:
        print(f"主题 '{theme_name}' 没有通过 Gate 1 的候选")
        return

    print(f"添加 {len(passed)} 只候选到自选池:")
    for c in passed:
        add_to_watchlist(c)
        c.in_watchlist = True

    save_theme_candidates(existing)


# ---------------------------------------------------------------------------
# Gate 1 桥接: 脚本 ↔ backtester agent
# ---------------------------------------------------------------------------

GATE1_JOBS_DIR = DATA_DIR / "gate1_jobs"


def gate1_prepare(theme_name: str):
    """
    为指定主题的 pending 候选生成 Gate 1 回测任务文件。

    输出格式为 JSON，供 backtester agent 消费。每个候选一个文件。
    backtester agent 执行后在文件中追加 verdict 字段即可。
    """
    existing = load_existing_candidates()
    if theme_name not in existing:
        print(f"主题 '{theme_name}' 不在候选池中")
        return

    theme = existing[theme_name]
    pending = [c for c in theme.candidates if c.status == "pending"]

    if not pending:
        print(f"主题 '{theme_name}' 没有 pending 状态的候选（可能已全部评估）")
        return

    GATE1_JOBS_DIR.mkdir(parents=True, exist_ok=True)
    batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    jobs = []

    for c in pending:
        job = {
            "batch_id": batch_id,
            "theme": theme_name,
            "code": c.code,
            "name": c.name,
            "purity_score": c.purity_score,
            "purity_reason": c.purity_reason,
            "industry": c.industry,
            "created_at": datetime.now().isoformat(),
            "verdict": None,           # backtester agent 填写: deploy/refine/abandon
            "verdict_reason": None,    # 裁决理由
            "backtest_report": None,   # 回测报告路径或摘要
            "reviewed_at": None,
        }
        jobs.append(job)

    # 写汇总文件（所有 pending 候选在一个文件中，方便批量处理）
    summary_file = GATE1_JOBS_DIR / f"gate1_{theme_name}_{batch_id}.json"
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump({"batch_id": batch_id, "theme": theme_name, "jobs": jobs}, f, ensure_ascii=False, indent=2)

    print(f"\n[Gate 1 准备] 主题: {theme_name}")
    print(f"  生成 {len(jobs)} 个回测任务 → {summary_file}")
    print(f"\n  下一步: 将文件提交给 backtester agent 评估")
    print(f"  backtester agent 在每个 job 中填写:")
    print(f"    verdict: 'deploy' | 'refine' | 'abandon'")
    print(f"    verdict_reason: 裁决理由")
    print(f"    backtest_report: 回测报告摘要")
    print(f"\n  完成后运行:")
    print(f"    python scripts/theme_expand.py --gate1-apply {theme_name} {summary_file.name}")
    print(f"    python scripts/theme_expand.py --promote {theme_name}")

    # 打印 backtester prompt 模板
    print(f"\n{'='*60}")
    print(f"  Backtester Agent Prompt 模板")
    print(f"{'='*60}")
    for j in jobs[:3]:  # 只展示前 3 个
        print(f"""
## {j['code']} {j['name']}
- 主题: {theme_name} | 纯度: {j['purity_score']}/5
- 理由: {j['purity_reason']}
- 行业: {j['industry']}
- 任务: 对 {j['code']} {j['name']} 执行 Gate 1 五维回测（样本外留出/复杂度惩罚/滚动窗口），
  输出 Deploy/Refine/Abandon 判決。入场策略优先使用与主题相关的技术形态
  （如突破/回调），回测区间建议近 3 年。
""")
    if len(jobs) > 3:
        print(f"  ... 还有 {len(jobs) - 3} 个候选，详见 {summary_file}")


def gate1_apply(theme_name: str, verdict_filename: str):
    """
    读取 backtester agent 填写的裁决文件，更新候选状态。

    Args:
        theme_name: 主题名称
        verdict_filename: Gate 1 裁决文件名（在 gate1_jobs/ 目录下）
    """
    verdict_path = GATE1_JOBS_DIR / verdict_filename
    if not verdict_path.exists():
        print(f"裁决文件不存在: {verdict_path}")
        return

    with open(verdict_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    existing = load_existing_candidates()
    if theme_name not in existing:
        print(f"主题 '{theme_name}' 不在候选池中")
        return

    theme = existing[theme_name]
    candidate_map = {c.code: c for c in theme.candidates}

    results = {"deploy": 0, "refine": 0, "abandon": 0, "skipped": 0}
    for job in data.get("jobs", []):
        code = job["code"]
        verdict = job.get("verdict")
        if not verdict:
            results["skipped"] += 1
            continue

        if code not in candidate_map:
            print(f"  [SKIP] {code} 不在候选池中")
            results["skipped"] += 1
            continue

        c = candidate_map[code]
        if verdict == "deploy":
            c.status = "gate1_passed"
            results["deploy"] += 1
        elif verdict == "refine":
            c.status = "gate1_failed"
            results["refine"] += 1
        elif verdict == "abandon":
            c.status = "gate1_failed"
            results["abandon"] += 1

    save_theme_candidates(existing)

    print(f"\n[Gate 1 裁决] 主题: {theme_name}")
    print(f"  Deploy (通过):   {results['deploy']} → 运行 --promote {theme_name} 加入自选池")
    print(f"  Refine (改进):   {results['refine']}")
    print(f"  Abandon (放弃):  {results['abandon']}")
    print(f"  Skipped (未评估): {results['skipped']}")


def gate1_status(theme_name: str = None):
    """查看 Gate 1 进度。"""
    existing = load_existing_candidates()
    themes_to_show = [theme_name] if theme_name else list(existing.keys())

    if not themes_to_show:
        print("没有活跃主题")
        return

    for name in themes_to_show:
        if name not in existing:
            print(f"主题 '{name}' 不存在")
            continue
        theme = existing[name]
        total = len(theme.candidates)
        passed = sum(1 for c in theme.candidates if c.status == "gate1_passed")
        failed = sum(1 for c in theme.candidates if c.status == "gate1_failed")
        pending = sum(1 for c in theme.candidates if c.status == "pending")
        print(f"\n{name}: {total} 候选 | [PASS] Gate1通过:{passed} | [FAIL] 未过:{failed} | [PEND] 待评估:{pending}")
        if pending > 0:
            print(f"  运行 --gate1-prepare {name} 生成回测任务文件")


# ---------------------------------------------------------------------------
# P0: 数据源健康检查
# ---------------------------------------------------------------------------

def check_data_source_health(timeout: int = 10) -> dict:
    """
    快速检查三级数据源的可用性，避免运行时空等。

    用 signal.alarm 做不到跨平台，改用 thread + timeout join。

    Returns:
        {"concept_board": bool, "shenwan": bool, "keyword": bool}
    """
    import threading

    health = {"concept_board": False, "shenwan": False, "keyword": False}

    def _check_concept():
        try:
            import akshare as ak
            df = ak.stock_board_concept_name_em()
            if not df.empty:
                health["concept_board"] = True
        except Exception:
            pass

    def _check_shenwan():
        try:
            import akshare as ak
            df = ak.index_component_sw(symbol="801760")
            if not df.empty:
                health["shenwan"] = True
        except Exception:
            pass

    def _check_keyword():
        try:
            import akshare as ak
            df = ak.stock_info_a_code_name()
            if not df.empty:
                health["keyword"] = True
        except Exception:
            pass

    threads = [
        threading.Thread(target=_check_concept, daemon=True),
        threading.Thread(target=_check_shenwan, daemon=True),
        threading.Thread(target=_check_keyword, daemon=True),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=timeout)

    return health


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="主题扩展 — 从主题识别到全市场候选发现 + Gate 1 回测桥接",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python scripts/theme_expand.py --theme "短剧游戏"
  python scripts/theme_expand.py --theme "低空经济" --max-candidates 5 --dry-run
  python scripts/theme_expand.py --scan-active
  python scripts/theme_expand.py --gate1-prepare "短剧游戏"
  python scripts/theme_expand.py --gate1-apply "短剧游戏" gate1_短剧游戏_20261023.json
  python scripts/theme_expand.py --gate1-status
  python scripts/theme_expand.py --promote "短剧游戏"
  python scripts/theme_expand.py --health
        """,
    )
    parser.add_argument("--theme", type=str, help="主题名称")
    parser.add_argument("--scan-active", action="store_true", help="扫描所有活跃主题")
    parser.add_argument("--promote", type=str, metavar="THEME", help="将指定主题通过 Gate 1 的候选加入自选池")
    parser.add_argument("--gate1-prepare", type=str, metavar="THEME", help="为指定主题的 pending 候选生成 Gate 1 回测任务文件")
    parser.add_argument("--gate1-apply", type=str, nargs=2, metavar=("THEME", "FILE"),
                        help="读取 backtester agent 填写的裁决文件，更新候选状态")
    parser.add_argument("--gate1-status", type=str, nargs="?", const="__all__", metavar="THEME",
                        help="查看 Gate 1 评估进度（不指定主题则显示全部）")
    parser.add_argument("--health", action="store_true", help="检查三级数据源可用性")
    parser.add_argument("--max-candidates", type=int, default=MAX_CANDIDATES_PER_THEME,
                        help=f"每主题最大候选数 (默认: {MAX_CANDIDATES_PER_THEME})")
    parser.add_argument("--no-llm", action="store_true", help="禁用 LLM，使用启发式评分")
    parser.add_argument("--model", type=str, default="deepseek", choices=["deepseek", "claude"],
                        help="LLM 模型 (默认: deepseek)")
    parser.add_argument("--dry-run", action="store_true", help="仅展示，不更新文件")

    args = parser.parse_args()

    # P0: 健康检查
    if args.health:
        health = check_data_source_health()
        print("数据源健康检查:")
        for tier, ok in health.items():
            icon = "[OK]" if ok else "[XX]"
            label = {"concept_board": "L1 东财概念板块", "shenwan": "L2 申万行业指数", "keyword": "L3 关键词搜索"}
            print(f"  {icon} {label.get(tier, tier)}: {'可用' if ok else '不可用'}")
        if not health["concept_board"]:
            print("\n  东财端点当前 BLOCKED，主题扩展将自动降级到 L2 申万行业。")
        return

    # P1: Gate 1 桥接
    if args.gate1_prepare:
        gate1_prepare(args.gate1_prepare)
        return

    if args.gate1_apply:
        theme_name, verdict_file = args.gate1_apply
        gate1_apply(theme_name, verdict_file)
        return

    if args.gate1_status:
        gate1_status(None if args.gate1_status == "__all__" else args.gate1_status)
        return

    if args.promote:
        promote_to_watchlist(args.promote)
        return

    if args.scan_active:
        scan_active_themes(use_llm=not args.no_llm, dry_run=args.dry_run)
        return

    if args.theme:
        expand_theme(
            theme_name=args.theme,
            max_candidates=args.max_candidates,
            use_llm=not args.no_llm,
            llm_model=args.model,
            dry_run=args.dry_run,
        )
        return

    parser.print_help()


if __name__ == "__main__":
    main()