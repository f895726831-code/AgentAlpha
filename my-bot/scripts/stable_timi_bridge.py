"""
STABLE ↔ TiMi Regime Bridge — STABLE 输出 → TiMi 策略编译参数。

职责:
  1. 读取 STABLE 的 mode_override + macro_quadrant
  2. 读取 TiMi 市场技术状态 (RSI/MA 广度)
  3. 读取 El Niño ENSO 状态 (enso_data.json)
  4. 综合输出 TiMi 编译参数 (regime/bias/mode/推荐策略)

双模式:
  - 规则矩阵 (默认): 7-entry 决策矩阵，确定性、零延迟
  - LLM 驱动 (--llm): DeepSeek Agent 自主决策，矩阵作为 fallback

决策矩阵 (规则模式):
  STABLE mode | 技术状态    | El Niño? | TiMi mode | TiMi bias       | 推荐策略
  ────────────┼────────────┼──────────┼───────────┼─────────────────┼──────────
  risk_off    | 任意        | -        | risk_off  | left_priority   | 全部停用
  normal      | 恐慌(>70%超卖)| -      | normal    | left_priority   | T3+T2
  normal      | 中性        | active   | el_nino   | balanced        | T1+T2+VCP+T3+T4+theme(农林牧渔)
  normal      | 中性        | inactive | normal    | balanced        | 全部5策略
  normal      | 贪婪(>50%超买)| -      | normal    | right_priority  | T1+VCP+T4
  risk_on     | 任意        | active   | el_nino   | right_priority  | T1+VCP+T4+T5+theme(农林牧渔/化工)
  risk_on     | 任意        | inactive | normal    | right_priority  | T1+VCP+T4+T5
  el_nino     | 任意        | active   | el_nino   | balanced        | T1+T2+VCP+T3+T4+theme(农林牧渔) (不依赖STABLE)"""

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_OUTPUT = PROJECT_ROOT / "data" / "output"
# HMM-Macro 分歧状态文件（由 signal-factory 管线写入）
HMM_DIVERGENCE_STATE = Path("D:/AgentAlpha/signal-factory/data/macro_assets/hmm_divergence_state.json")


# ── .env 自动加载 ──────────────────────────────────────────────────────────

def _load_dotenv():
    """从 my-bot/.env 加载 DEEPSEEK_API_KEY"""
    if os.environ.get("DEEPSEEK_API_KEY"):
        return
    candidates = [
        PROJECT_ROOT / ".env",
        PROJECT_ROOT / "scripts" / "wsl2" / ".env",
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


class StableTiMiBridge:
    """STABLE 宏观判档 → TiMi 策略编译参数"""

    # ── 决策矩阵 ──
    # (stable_mode, technical_state, el_nino_active) → (timi_mode, timi_bias, active_strategies, regime_label)
    DECISION_MATRIX = {
        ("risk_off", "any", False): {
            "mode": "risk_off", "bias": "left_priority", "regime": "风险回避",
            "active": [], "reason": "STABLE HMM 判风险回避 — 禁止开仓",
        },
        ("risk_off", "any", True): {
            "mode": "risk_off", "bias": "left_priority", "regime": "风险回避",
            "active": [], "reason": "STABLE HMM 判风险回避 — El Niño 也无法覆盖，禁止开仓",
        },
        ("normal", "panic", False): {
            "mode": "normal", "bias": "left_priority", "regime": "恐慌",
            "active": ["T3_oversold_bounce", "T2_pullback"],
            "reason": "技术面极端恐慌但 STABLE 未判 risk_off — 左侧选择性入场",
        },
        ("normal", "panic", True): {
            "mode": "el_nino", "bias": "left_priority", "regime": "恐慌+厄尔尼诺",
            "active": ["T3_oversold_bounce", "T2_pullback"],
            "reason": "恐慌+El Niño — 仅左侧策略，不追涨农业",
        },
        ("normal", "neutral", False): {
            "mode": "normal", "bias": "balanced", "regime": "中性",
            "active": ["T1_breakout", "T2_pullback", "VCP_breakout",
                       "T3_oversold_bounce", "T4_golden_cross"],
            "reason": "技术面中性 + STABLE 正常 — 全策略运行",
        },
        ("normal", "neutral", True): {
            "mode": "el_nino", "bias": "balanced", "regime": "中性+厄尔尼诺",
            "active": ["T1_breakout", "T2_pullback", "VCP_breakout",
                       "T3_oversold_bounce", "T4_golden_cross", "theme"],
            "reason": "技术面中性 + El Niño 活跃 — 全策略 + 农业/化工主题",
        },
        ("normal", "greedy", False): {
            "mode": "normal", "bias": "right_priority", "regime": "进攻",
            "active": ["T1_breakout", "VCP_breakout", "T4_golden_cross"],
            "reason": "技术面强势 + STABLE 正常 — 动量策略优先",
        },
        ("normal", "greedy", True): {
            "mode": "el_nino", "bias": "right_priority", "regime": "进攻+厄尔尼诺",
            "active": ["T1_breakout", "VCP_breakout", "T4_golden_cross", "theme"],
            "reason": "技术面强势 + El Niño — 动量策略 + 农业/化工主题",
        },
        ("risk_on", "any", False): {
            "mode": "normal", "bias": "right_priority", "regime": "进攻",
            "active": ["T1_breakout", "VCP_breakout", "T4_golden_cross", "T5_earnings"],
            "reason": "STABLE 判风险偏好 — 动量+基本面策略全开",
        },
        ("risk_on", "any", True): {
            "mode": "el_nino", "bias": "right_priority", "regime": "进攻+厄尔尼诺",
            "active": ["T1_breakout", "VCP_breakout", "T4_golden_cross", "T5_earnings", "theme"],
            "reason": "STABLE 风险偏好 + El Niño 活跃 — 动量+基本面+农业/化工主题",
        },
        # el_nino 作为独立 mode（不依赖 STABLE，直接检测 ENSO 数据）
        ("el_nino", "any", True): {
            "mode": "el_nino", "bias": "balanced", "regime": "厄尔尼诺主题",
            "active": ["T1_breakout", "T2_pullback", "VCP_breakout",
                       "T3_oversold_bounce", "T4_golden_cross", "theme"],
            "reason": "El Niño 主题模式 — 全策略 + 农业/化工/化肥主题优先",
        },
    }

    # ── panic 策略的紧缩参数 ──
    PANIC_PARAMS = {
        "T3_oversold_bounce": {
            "rsi_period": 7,
            "rsi_oversold": 20,
            "volume_shrink_ratio": 0.5,
            "stop_loss_pct": 5.0,
            "take_profit_pct": 10.0,
        },
        "T2_pullback": {
            "trend_period": 60,
            "ma_period": 20,
            "pullback_pct_min": 3.0,
            "pullback_pct_max": 12.0,
            "volume_shrink_ratio": 0.5,
            "stop_loss_pct": 5.0,
            "take_profit_pct": 8.0,
        },
    }

    def __init__(self, data_dir: Path = None):
        self.data_dir = data_dir or DATA_OUTPUT

    def load_stable(self) -> Dict[str, Any]:
        """加载 STABLE 输出"""
        path = self.data_dir / "asset_allocation.json"
        if not path.exists():
            return {}

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        mode_override = data.get("mode_override", {})
        macro = data.get("macro_quadrant", {})

        return {
            "hmm_mode": mode_override.get("mode", "normal"),
            "hmm_note": mode_override.get("note", ""),
            "macro_quadrant": macro.get("quadrant", ""),
            "macro_quadrant_cn": macro.get("quadrant_label_cn", ""),
            "macro_confidence": macro.get("confidence", 0),
            "summary": data.get("summary", ""),
            # 从 target_weights 推断实际风险态度
            "bond_weight": data.get("target_weights", {}).get("bond", 0),
            "equity_weight": sum(
                data.get("target_weights", {}).get(k, 0)
                for k in ["a_share", "star_50", "nasdaq", "hstech", "nikkei", "india"]
            ),
        }

    def load_technical(self) -> Dict[str, Any]:
        """加载市场技术状态"""
        path = self.data_dir / "timi_market_data.json"
        if not path.exists():
            return {"state": "unknown", "n_stocks": 0}

        with open(path, "r", encoding="utf-8") as f:
            md = json.load(f)

        stocks = md.get("market_data", [])
        n = len(stocks)
        if n == 0:
            return {"state": "unknown", "n_stocks": 0}

        bullish = sum(1 for s in stocks if s.get("close", 0) > s.get("ma_20", 0) > s.get("ma_60", 0))
        bearish = sum(1 for s in stocks if s.get("close", 0) < s.get("ma_60", 0))
        oversold = sum(1 for s in stocks if s.get("rsi_14", 50) < 30)
        overbought = sum(1 for s in stocks if s.get("rsi_14", 50) > 70)
        avg_rsi = round(sum(s.get("rsi_14", 50) for s in stocks) / n, 1)

        # 技术状态分类
        oversold_ratio = oversold / n
        overbought_ratio = overbought / n
        bearish_ratio = bearish / n

        if oversold_ratio > 0.6 and bearish_ratio > 0.7:
            state = "panic"
        elif overbought_ratio > 0.4 and avg_rsi > 65:
            state = "greedy"
        else:
            state = "neutral"

        return {
            "state": state,
            "n_stocks": n,
            "bullish": bullish,
            "bearish": bearish,
            "oversold": oversold,
            "overbought": overbought,
            "avg_rsi": avg_rsi,
            "oversold_ratio": round(oversold_ratio, 3),
            "overbought_ratio": round(overbought_ratio, 3),
            "bearish_ratio": round(bearish_ratio, 3),
        }

    def load_divergence(self) -> Dict[str, Any]:
        """加载 HMM-Macro 分歧状态"""
        if not HMM_DIVERGENCE_STATE.exists():
            return {"diverged": False, "level": "normal", "divergence_weeks": 0}

        try:
            with open(HMM_DIVERGENCE_STATE, "r", encoding="utf-8") as f:
                state = json.load(f)
            return {
                "diverged": state.get("diverged", False),
                "level": state.get("level", "normal"),
                "divergence_weeks": state.get("divergence_weeks", 0),
                "hmm_regime": state.get("hmm_regime", ""),
                "macro_regime": state.get("macro_regime", ""),
                "risk_weight_multiplier": state.get("risk_weight_multiplier", 1.0),
                "last_checked": state.get("last_checked", ""),
            }
        except Exception:
            return {"diverged": False, "level": "normal", "divergence_weeks": 0}

    def load_enso(self) -> Dict[str, Any]:
        """加载 El Niño ENSO 状态"""
        enso_path = self.data_dir / "enso_data.json"
        if not enso_path.exists():
            return {"active": False, "classification": "unknown"}

        try:
            with open(enso_path, "r", encoding="utf-8") as f:
                enso = json.load(f)

            cls = enso.get("classification", {})
            sig = enso.get("trading_signals", {})

            return {
                "active": sig.get("active", False),
                "mode": sig.get("mode", "normal"),
                "classification": cls.get("classification", "unknown"),
                "strength": cls.get("strength", "unknown"),
                "latest_oni": cls.get("latest_oni", 0),
                "peak_oni": cls.get("peak_oni", 0),
                "trend": cls.get("trend", "unknown"),
                "consecutive_warm_months": cls.get("consecutive_warm_months", 0),
                "alerts": sig.get("alerts", []),
                "strategies": sig.get("strategies", {}),
                "fetched_at": enso.get("metadata", {}).get("fetched_at", ""),
            }
        except Exception:
            return {"active": False, "classification": "unknown"}

    def classify(self, stable: Dict = None, technical: Dict = None,
                 use_llm: bool = False) -> Dict[str, Any]:
        """
        综合 STABLE + 技术状态 → TiMi 编译参数。

        Args:
            stable: load_stable() 输出
            technical: load_technical() 输出
            use_llm: True = LLM Agent 决策 (失败时 fallback 到规则矩阵)

        Returns:
            {
                "regime": "恐慌",
                "bias": "left_priority",
                "mode": "normal",
                "active_strategies": ["T3_oversold_bounce", "T2_pullback"],
                "reason": "...",
                "source": "rule_matrix" | "llm_agent" | "llm_fallback",
                "details": {...}
            }
        """
        stable = stable or self.load_stable()
        technical = technical or self.load_technical()
        enso = self.load_enso()

        # ── HMM-Macro 分歧检查 ──
        divergence = self.load_divergence()
        hmm_mode = stable.get("hmm_mode", "normal")

        # Check for ENSO-driven mode override
        el_nino_active = enso.get("active", False)
        if el_nino_active and enso.get("classification") == "super_el_nino":
            print(f"[Bridge] 🌊 El Niño 超强事件检测: ONI={enso.get('latest_oni', 0)}°C, "
                  f"mode={enso.get('mode', 'unknown')}, trend={enso.get('trend', 'unknown')}")
            # Force el_nino as the effective mode (but risk_off still takes priority)
            if hmm_mode != "risk_off":
                hmm_mode = "el_nino"

        if divergence.get("diverged") and divergence.get("level") == "alert":
            # 分歧 >2 周: HMM 与宏观基本面长期背离，回退到 neutral 模式
            old_mode = hmm_mode
            hmm_mode = "normal"  # 不盲从 HMM，保守中性
            print(f"[Bridge] ⚠️ HMM-Macro divergence alert ({divergence.get('divergence_weeks', 0)}w): "
                  f"HMM={divergence.get('hmm_regime')}, Macro={divergence.get('macro_regime')} — "
                  f"overriding {old_mode} → {hmm_mode}")
        elif divergence.get("diverged"):
            print(f"[Bridge] ⚠️ HMM-Macro divergence warning "
                  f"({divergence.get('divergence_weeks', 0)}w): "
                  f"HMM={divergence.get('hmm_regime')}, Macro={divergence.get('macro_regime')}")

        if use_llm and os.environ.get("DEEPSEEK_API_KEY"):
            try:
                result = self._classify_llm(stable, technical)
                if result is not None:
                    return result
            except Exception:
                pass  # LLM 失败 → fall through 到规则矩阵

        # ── 规则矩阵 (默认 + LLM fallback) ──
        tech_state = technical.get("state", "neutral")

        # 使用 3-tuple key: (hmm_mode, tech_state, el_nino_active)
        key = (hmm_mode, tech_state, el_nino_active)
        if key not in self.DECISION_MATRIX:
            key = (hmm_mode, "any", el_nino_active)
        if key not in self.DECISION_MATRIX:
            key = (hmm_mode, tech_state, False)
        if key not in self.DECISION_MATRIX:
            key = (hmm_mode, "any", False)
        if key not in self.DECISION_MATRIX:
            key = ("normal", "neutral", False)

        decision = self.DECISION_MATRIX[key]

        result = {
            "regime": decision["regime"],
            "bias": decision["bias"],
            "mode": decision["mode"],
            "active_strategies": decision["active"],
            "reason": decision["reason"],
            "source": "rule_matrix",
            "details": {
                "stable_mode": hmm_mode,
                "stable_mode_original": stable.get("hmm_mode", "normal"),
                "macro_quadrant": stable.get("macro_quadrant_cn", ""),
                "macro_confidence": stable.get("macro_confidence", 0),
                "technical_state": tech_state,
                "avg_rsi": technical.get("avg_rsi", 50),
                "bearish_ratio": technical.get("bearish_ratio", 0),
                "oversold_ratio": technical.get("oversold_ratio", 0),
                "bond_weight": stable.get("bond_weight", 0),
                "equity_weight": stable.get("equity_weight", 0),
                "divergence": {
                    "diverged": divergence.get("diverged", False),
                    "level": divergence.get("level", "normal"),
                    "weeks": divergence.get("divergence_weeks", 0),
                    "risk_weight_multiplier": divergence.get("risk_weight_multiplier", 1.0),
                },
                "el_nino": {
                    "active": enso.get("active", False),
                    "classification": enso.get("classification", "unknown"),
                    "latest_oni": enso.get("latest_oni", 0),
                    "trend": enso.get("trend", "unknown"),
                },
            },
        }

        # 如果 LLM 尝试了但失败了，记录
        if use_llm:
            result["source"] = "llm_fallback"
            result["reason"] += " (LLM 不可用，回退规则矩阵)"

        return result

    def _classify_llm(self, stable: Dict, technical: Dict) -> Optional[Dict[str, Any]]:
        """
        LLM Agent 决策 — 调用 DeepSeek 输出策略编译参数。

        输入 STABLE 宏观判档 + 技术面数据 → LLM 推理 → JSON 决策。
        超时 30s，失败返回 None（触发规则矩阵 fallback）。
        """
        import openai

        # ── 构建上下文 ──
        tech_state = technical.get("state", "neutral")
        hmm_mode = stable.get("hmm_mode", "normal")

        context = {
            "stable": {
                "hmm_mode": hmm_mode,
                "note": stable.get("hmm_note", ""),
                "macro_quadrant": stable.get("macro_quadrant_cn", ""),
                "macro_confidence": stable.get("macro_confidence", 0),
                "bond_weight_pct": round(stable.get("bond_weight", 0) * 100, 1),
                "equity_weight_pct": round(stable.get("equity_weight", 0) * 100, 1),
            },
            "technical": {
                "state": tech_state,
                "n_stocks": technical.get("n_stocks", 0),
                "avg_rsi": technical.get("avg_rsi", 50),
                "oversold_ratio": technical.get("oversold_ratio", 0),
                "overbought_ratio": technical.get("overbought_ratio", 0),
                "bearish_ratio": technical.get("bearish_ratio", 0),
                "bullish_count": technical.get("bullish", 0),
            },
        }

        # ── 构建 prompt ──
        system_prompt = """你是 AgentAlpha v5.0 的策略编译 Agent。你基于 STABLE HMM 宏观判档、
TiMi 市场技术面数据、以及 El Niño ENSO 状态，输出当天应激活的策略组合和参数。

## 可用策略
- T1_breakout: 量确认突破（动量策略，适合进攻/贪婪市场）
- T2_pullback: 回调低吸（反转策略，适合恐慌/左侧市场）
- VCP_breakout: 波动率收缩突破（动量策略）
- T3_oversold_bounce: RSI超卖反弹（反转策略，适合恐慌市场）
- T4_golden_cross: MA金叉（趋势策略）
- T5_earnings: 财报驱动（事件策略，适合财报季）
- T6_capital: 资金流入（资金流策略）
- theme: 主题驱动（板块轮动策略，El Niño 模式下优先农林牧渔/化工/化肥主题）

## 决策逻辑

### STABLE HMM 判档
- risk_off: 市场风险回避。全部策略停用，禁止开仓。bias=left_priority
- risk_on: 市场风险偏好。动量+基本面策略全开。bias=right_priority
- normal: 正常市场。由技术面状态决定策略选择

### El Niño 模式 (el_nino)
- 当 ENSO 状态为 super_el_nino 时，激活 theme 策略用于农业/化工板块
- 不影响 risk_off 的最高优先级（风险回避时仍禁止开仓）
- 农业上游板块（种业/糖业/橡胶/化肥）有结构性做多机会

### 技术面状态
- panic（恐慌）: >70%标的低于MA60 + >60%标的RSI<30。只用反转策略 T3+T2。bias=left_priority
- greedy（贪婪）: >40%标的RSI>70 + 平均RSI>65。只用动量策略 T1+VCP+T4。bias=right_priority
- neutral（中性）: 全策略运行。bias=balanced

### panic 模式参数紧缩
T3_oversold_bounce: rsi_oversold=20, stop_loss_pct=5.0, take_profit_pct=10.0
T2_pullback: stop_loss_pct=5.0, take_profit_pct=8.0, pullback_pct_max=12.0

## 输出格式
严格输出 JSON（无 markdown 标记）:
{
  "regime": "恐慌|进攻|中性|风险回避|厄尔尼诺主题",
  "bias": "left_priority|balanced|right_priority",
  "mode": "normal|risk_off|el_nino",
  "active_strategies": ["T1_breakout", "T2_pullback", ...],
  "reason": "决策理由（中文，≤3句）"
}"""

        user_prompt = f"""## 当前环境

STABLE HMM 判档: {hmm_mode}
宏观象限: {context['stable']['macro_quadrant']} (置信度={context['stable']['macro_confidence']})
债券权重: {context['stable']['bond_weight_pct']}%
权益权重: {context['stable']['equity_weight_pct']}%

技术状态: {tech_state}
标的数: {context['technical']['n_stocks']}
平均 RSI: {context['technical']['avg_rsi']}
超卖比例: {context['technical']['oversold_ratio']}
超买比例: {context['technical']['overbought_ratio']}
空头比例: {context['technical']['bearish_ratio']}
多头数: {context['technical']['bullish_count']}

请输出今日策略编译 JSON。"""

        # ── 调用 LLM ──
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        client = openai.OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com/v1",
            timeout=30,
        )

        try:
            resp = client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=512,
                response_format={"type": "json_object"},
            )
            content = resp.choices[0].message.content or ""
        except Exception:
            return None  # API 调用失败 → fallback

        # ── 解析响应 ──
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            m = re.search(r"```(?:json)?\s*([\s\S]*?)```", content)
            if m:
                try:
                    data = json.loads(m.group(1))
                except json.JSONDecodeError:
                    return None
            else:
                # 从 { 到 } 提取
                start = content.find("{")
                end = content.rfind("}")
                if start >= 0 and end > start:
                    try:
                        data = json.loads(content[start:end + 1])
                    except json.JSONDecodeError:
                        return None
                else:
                    return None

        # ── 验证 + 构建输出 ──
        valid_strategies = {
            "T1_breakout", "T2_pullback", "VCP_breakout",
            "T3_oversold_bounce", "T4_golden_cross",
            "T5_earnings", "T6_capital", "theme",
        }
        active = [
            s for s in data.get("active_strategies", [])
            if s in valid_strategies
        ]

        regime = data.get("regime", "中性")
        bias = data.get("bias", "balanced")
        if bias not in ("left_priority", "balanced", "right_priority"):
            bias = "balanced"
        mode = data.get("mode", "normal")
        if mode not in ("normal", "risk_off", "el_nino"):
            mode = "normal"

        return {
            "regime": regime,
            "bias": bias,
            "mode": mode,
            "active_strategies": active if active else [],
            "reason": data.get("reason", "LLM Agent 决策"),
            "source": "llm_agent",
            "details": {
                "stable_mode": hmm_mode,
                "macro_quadrant": stable.get("macro_quadrant_cn", ""),
                "macro_confidence": stable.get("macro_confidence", 0),
                "technical_state": tech_state,
                "avg_rsi": technical.get("avg_rsi", 50),
                "bearish_ratio": technical.get("bearish_ratio", 0),
                "oversold_ratio": technical.get("oversold_ratio", 0),
                "bond_weight": stable.get("bond_weight", 0),
                "equity_weight": stable.get("equity_weight", 0),
            },
        }

    def generate_strategy_json(self, classification: Dict = None) -> Dict:
        """
        生成完整的 LLM 策略编译 JSON (可直接喂给 compile_timi_strategies.py)。

        Args:
            classification: classify() 的输出。如果 None, 自动运行 classify()。

        Returns:
            完整的 LLM JSON (regime, bias, mode, strategies[], filters, meta)
        """
        if classification is None:
            classification = self.classify()

        active_set = set(classification["active_strategies"])
        all_strategies = [
            "T1_breakout", "T2_pullback", "VCP_breakout",
            "T3_oversold_bounce", "T4_golden_cross",
            "T5_earnings", "T6_capital", "theme",
        ]

        strategies = []
        for stype in all_strategies:
            active = stype in active_set
            params = {}

            # 恐慌模式下使用紧缩参数
            if active and classification["regime"] == "恐慌":
                params = self.PANIC_PARAMS.get(stype, {})

            strategies.append({
                "type": stype,
                "active": active,
                "params": params,
            })

        return {
            "regime": classification["regime"],
            "bias": classification["bias"],
            "mode": classification["mode"],
            "strategies": strategies,
            "filters": {
                "exclude_st": True,
                "exclude_new_listings_days": 60,
            },
            "meta": {
                "compiled_by": "stable-timi-bridge",
                "timestamp": datetime.now().isoformat(),
                "reasoning": classification["reason"],
                "bridge_details": classification["details"],
            },
        }

    def generate(self, use_llm: bool = False) -> Dict:
        """一站式: 加载数据 → 分类 → 生成策略 JSON"""
        stable = self.load_stable()
        technical = self.load_technical()
        classification = self.classify(stable, technical, use_llm=use_llm)
        strategy_json = self.generate_strategy_json(classification)

        # 附加诊断信息 (不给编译器, 给运维看)
        strategy_json["_diagnostics"] = {
            "stable": stable,
            "technical": technical,
            "el_nino": self.load_enso(),
            "decision_source": classification.get("source", "rule_matrix"),
            "decision_key": (stable.get("hmm_mode", "?"), technical.get("state", "?"), self.load_enso().get("active", False)),
        }

        return strategy_json


# ─── CLI ────────────────────────────────────────────────────────────────────────

def main():
    import argparse
    import subprocess
    import tempfile

    parser = argparse.ArgumentParser(
        description="STABLE ↔ TiMi Regime Bridge — 生成策略编译 JSON"
    )
    parser.add_argument("--output", type=str, default=None,
                        help="输出 JSON 路径 (stdout if omitted)")
    parser.add_argument("--diagnostics", action="store_true",
                        help="附带 STABLE + 技术面诊断信息")
    parser.add_argument("--compile", action="store_true",
                        help="输出后直接编译 (pipe to compile_timi_strategies.py)")
    parser.add_argument("--llm", action="store_true",
                        help="LLM Agent 决策 (失败时自动回退规则矩阵)")
    args = parser.parse_args()

    bridge = StableTiMiBridge()
    strategy_json = bridge.generate(use_llm=args.llm)

    if not args.diagnostics:
        strategy_json.pop("_diagnostics", None)

    output_str = json.dumps(strategy_json, indent=2, ensure_ascii=False)

    if args.compile:
        today = datetime.now().strftime("%Y-%m-%d")
        tmp_path = Path(tempfile.gettempdir()) / f"timi_bridge_{today}.json"
        tmp_path.write_text(output_str, encoding="utf-8")

        compiler_script = str(PROJECT_ROOT / "scripts" / "compile_timi_strategies.py")
        result = subprocess.run(
            ["python", compiler_script, "--file", str(tmp_path), "--date", today],
            capture_output=True, text=True, encoding="utf-8",
        )
        print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        tmp_path.unlink(missing_ok=True)
        sys.exit(result.returncode)
    elif args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(output_str, encoding="utf-8")
        print(f"✅ 已写入: {args.output}")
    else:
        print(output_str)


if __name__ == "__main__":
    main()