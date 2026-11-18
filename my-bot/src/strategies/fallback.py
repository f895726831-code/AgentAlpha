"""
LLM 回退处理器 — 异常触发时唤醒 signal-gen / trade-planner。

回退流程:
  1. 异常检测器触发 → FallbackHandler.invoke()
  2. 收集当前上下文 (signals, positions, market state)
  3. 调用 DeepSeek API (或 mock) 进行决策
  4. 缓存异常特征 → disk (供下次离线编译参考)

设计原则:
  - 回退是安全网，不是主路 — 正常情况下不调用
  - LLM 超时 30s，失败退回到 mock 模式（不阻塞主流程）
  - 缓存异常特征数据，供后续离线编译改进模板
  - 支持 mock 模式用于测试

用法:
    from strategies.fallback import FallbackHandler

    handler = FallbackHandler()
    result = handler.invoke(anomalies, context)
"""

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


# ── .env 自动加载 ──────────────────────────────────────────────────────────

def _load_dotenv():
    """从 my-bot/.env 和 scripts/wsl2/.env 加载环境变量"""
    if os.environ.get("DEEPSEEK_API_KEY"):
        return
    candidates = [
        Path(__file__).resolve().parent.parent.parent / ".env",     # my-bot/.env
        Path(__file__).resolve().parent.parent.parent / "scripts" / "wsl2" / ".env",
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


@dataclass
class FallbackResult:
    """LLM 回退结果"""
    decision: str                   # "execute" | "hold" | "abort"
    signals: List[Dict] = field(default_factory=list)
    positions: List[Dict] = field(default_factory=list)
    reasoning: str = ""
    source: str = "llm_fallback"    # "llm_fallback" | "mock" | "cached"
    cached_anomaly_id: str = ""     # 关联的异常缓存 ID


class FallbackHandler:
    """
    LLM 回退处理器。

    当异常检测器发现异常时:
      1. 收集异常特征 + 当前市场上下文
      2. 调用 LLM Agent (或 mock) 进行决策
      3. 缓存异常数据，供离线编译阶段分析

    缓存路径: data/anomalies/YYYY-MM-DD_HHMMSS.json
    """

    DEFAULT_CACHE_DIR = Path("data/anomalies")
    MAX_CACHE_FILES = 50  # 保留最近 50 条异常记录

    def __init__(
        self,
        cache_dir: Path = None,
        mock_mode: bool = True,
        default_decision: str = "hold",
        llm_model: str = "deepseek-chat",
        llm_base_url: str = "https://api.deepseek.com/v1",
        llm_timeout: int = 30,
        llm_max_retries: int = 2,
    ):
        """
        Args:
            cache_dir: 异常缓存目录
            mock_mode: True = 使用模拟 LLM 响应 (测试/降级)
            default_decision: mock 模式下的默认决策
            llm_model: LLM 模型名称 (mock_mode=False 时使用)
            llm_base_url: LLM API 地址
            llm_timeout: LLM 调用超时 (秒)
            llm_max_retries: LLM 调用最大重试次数
        """
        self.cache_dir = cache_dir or self.DEFAULT_CACHE_DIR
        self.mock_mode = mock_mode
        self.default_decision = default_decision
        self.llm_model = llm_model
        self.llm_base_url = llm_base_url
        self.llm_timeout = llm_timeout
        self.llm_max_retries = llm_max_retries
        self._llm_client = None  # 延迟初始化

    def _get_llm_client(self):
        """延迟初始化 OpenAI 客户端（仅非 mock 模式）"""
        if self._llm_client is None:
            import openai
            api_key = os.environ.get("DEEPSEEK_API_KEY", "")
            self._llm_client = openai.OpenAI(
                api_key=api_key,
                base_url=self.llm_base_url,
                timeout=self.llm_timeout,
            )
        return self._llm_client

    def invoke(
        self,
        anomalies: List,
        context: Dict[str, Any],
        force_llm: bool = False,
    ) -> FallbackResult:
        """
        触发 LLM 回退。

        Args:
            anomalies: 异常列表 (来自 AnomalyDetector.check())
            context: 当前上下文 (signals, positions, market state 等)
            force_llm: 即使 mock_mode=True 也走真实 LLM

        Returns:
            FallbackResult (LLM 决策)
        """
        # ── 缓存异常数据 ──
        anomaly_id = self._cache_anomaly(anomalies, context)

        # ── 决策 ──
        if self.mock_mode and not force_llm:
            return self._mock_invoke(anomalies, context, anomaly_id)
        else:
            return self._llm_invoke(anomalies, context, anomaly_id)

    def _mock_invoke(
        self, anomalies, context: Dict, anomaly_id: str
    ) -> FallbackResult:
        """模拟 LLM 响应 — 根据异常严重度决定决策"""
        has_critical = any(a.severity == "CRITICAL" for a in anomalies)

        if has_critical:
            decision = "abort"
            reasoning = f"检测到 CRITICAL 异常 ({len(anomalies)} 个)，拒绝执行。建议检查数据源和策略编译结果。"
        else:
            # WARNING / INFO: 持有现有仓位，不新建仓
            decision = self.default_decision
            reasoning = f"检测到 {len(anomalies)} 个非严重异常，保持 {decision}。"

        return FallbackResult(
            decision=decision,
            signals=context.get("signals", []),
            positions=context.get("positions", []),
            reasoning=reasoning,
            source="mock",
            cached_anomaly_id=anomaly_id,
        )

    def _llm_invoke(
        self, anomalies, context: Dict, anomaly_id: str
    ) -> FallbackResult:
        """
        真实 LLM 调用 — 唤醒 signal-gen / trade-planner Agent。

        调用 DeepSeek API，传入异常上下文，输出 JSON 决策。
        失败时自动退回到 mock 模式（不阻塞主流程）。
        """
        import openai

        # ── 构建 prompt ──
        anomaly_msgs = "\n".join(
            f"[{a.severity}] {a.code}: {a.message}" for a in anomalies
        )

        signals_context = context.get("signals", [])
        positions_context = context.get("positions", [])

        system_prompt = """你是 FM_trade v5.0 的应急决策 Agent。当 TiMi 策略引擎检测到异常时，
你作为 LLM 回退层被激活。你需要基于异常上下文做出应急决策。

## 输出格式
必须输出纯 JSON（无 markdown 代码块标记）:
{
  "decision": "execute|hold|abort",
  "signals": [],
  "positions": [],
  "reasoning": "决策理由"
}

## 决策规则
- "abort": 检测到 CRITICAL 异常（SIGNAL_FLOOD等），拒绝全部执行，现有仓位不变
- "hold": 检测到 WARNING 异常，保留现有仓位但不新建仓
- "execute": 判断异常为误报或已缓解，允许继续执行

## 约束
- 不要生成新信号（回退层不具备因子发现能力）
- 不要修改现有仓位（回退层不具备风控覆盖能力）
- 简短 reasoning，不超过 3 句中文"""

        user_prompt = f"""## 异常回退

检测到以下异常，请对当前环境进行综合判断:

{anomaly_msgs}

## 上下文
- bias={context.get('bias')}
- mode={context.get('mode')}
- tick_count={context.get('tick_count')}
- signal_count={len(signals_context)}
- position_count={len(positions_context)}

## 现有信号摘要
{json.dumps(signals_context[:5] if signals_context else [], indent=2, ensure_ascii=False)}

请输出应急决策 JSON。"""

        # ── 调用 LLM ──
        last_error = None
        for attempt in range(self.llm_max_retries):
            try:
                client = self._get_llm_client()
                resp = client.chat.completions.create(
                    model=self.llm_model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.3,       # 低温度 → 确定性决策
                    max_tokens=512,
                    response_format={"type": "json_object"},
                )
                content = resp.choices[0].message.content or ""

                # ── 解析响应 ──
                result = self._parse_llm_response(content, context, anomaly_id)
                return result

            except openai.APIConnectionError as e:
                last_error = e
                if attempt < self.llm_max_retries - 1:
                    time.sleep(2 ** attempt)
            except openai.RateLimitError as e:
                last_error = e
                if attempt < self.llm_max_retries - 1:
                    time.sleep(5 * (2 ** attempt))
            except openai.APIError as e:
                last_error = e
                if attempt < self.llm_max_retries - 1:
                    time.sleep(2 ** attempt)
            except Exception as e:
                last_error = e
                break  # 非 API 错误不重试

        # ── LLM 调用失败 → 退回到 mock ──
        anomaly_msgs_short = "\n".join(
            f"[{a.severity}] {a.code}" for a in anomalies
        )
        return FallbackResult(
            decision="hold",
            signals=context.get("signals", []),
            positions=context.get("positions", []),
            reasoning=(
                f"LLM 回退调用失败 ({type(last_error).__name__}: {last_error})，"
                f"降级为 mock hold。异常摘要: {anomaly_msgs_short}"
            ),
            source="llm_fallback",
            cached_anomaly_id=anomaly_id,
        )

    def _parse_llm_response(
        self, content: str, context: Dict, anomaly_id: str
    ) -> FallbackResult:
        """
        解析 LLM 响应 JSON → FallbackResult。

        稳健解析：先直接 json.loads，失败后用正则提取
        """
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            # 从 ```json ... ``` 代码块提取
            m = re.search(r"```(?:json)?\s*([\s\S]*?)```", content)
            if m:
                try:
                    data = json.loads(m.group(1))
                except json.JSONDecodeError:
                    data = {}
            else:
                # 从第一个 { 到最后一个 }
                start = content.find("{")
                end = content.rfind("}")
                if start >= 0 and end > start:
                    try:
                        data = json.loads(content[start:end + 1])
                    except json.JSONDecodeError:
                        data = {}
                else:
                    data = {}

        decision = data.get("decision", "hold")
        if decision not in ("execute", "hold", "abort"):
            decision = "hold"

        reasoning = data.get("reasoning", "LLM 回退决策（响应解析成功）")

        return FallbackResult(
            decision=decision,
            signals=data.get("signals", context.get("signals", [])),
            positions=data.get("positions", context.get("positions", [])),
            reasoning=reasoning,
            source="llm_fallback",
            cached_anomaly_id=anomaly_id,
        )

    def _cache_anomaly(self, anomalies: List, context: Dict) -> str:
        """缓存异常数据到 disk"""
        anomaly_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")

        cache_data = {
            "anomaly_id": anomaly_id,
            "timestamp": datetime.now().isoformat(),
            "anomalies": [
                {
                    "code": a.code,
                    "severity": a.severity,
                    "message": a.message,
                    "data": a.data,
                }
                for a in anomalies
            ],
            "context": {
                "bias": context.get("bias"),
                "mode": context.get("mode"),
                "tick_count": context.get("tick_count"),
                "signal_count": len(context.get("signals", [])),
                "position_count": len(context.get("positions", [])),
            },
        }

        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path = self.cache_dir / f"{anomaly_id}.json"

            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, indent=2, ensure_ascii=False)

            # 清理旧缓存
            self._cleanup_old_caches()

        except OSError:
            pass  # 缓存写入失败不阻塞主流程

        return anomaly_id

    def _cleanup_old_caches(self) -> None:
        """清理超过 MAX_CACHE_FILES 的旧缓存文件"""
        try:
            files = sorted(
                self.cache_dir.glob("*.json"),
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )
            for old_file in files[self.MAX_CACHE_FILES:]:
                old_file.unlink(missing_ok=True)
        except OSError:
            pass

    def load_anomaly(self, anomaly_id: str) -> Optional[Dict]:
        """加载已缓存的异常"""
        cache_path = self.cache_dir / f"{anomaly_id}.json"
        if not cache_path.exists():
            return None
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)