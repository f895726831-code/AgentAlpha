"""
factor-lab API 客户端

Windows 端 quant-signal-factory 通过 localhost:8700 调用 WSL2 factor-lab 的
RD-Agent 因子挖掘和论文因子挖掘能力。

factor-lab 启动后，WSL2 端口会自动转发到 Windows localhost。

用法:
    from src.factor_lab_client import FactorLabClient

    client = FactorLabClient()

    # 健康检查
    health = client.health_check()

    # 触发因子挖掘（异步）
    task = client.mine_factors(instruments=["sh600519"], date="2026-08-19")

    # 等待完成
    result = client.wait_for_task(task["task_id"], timeout=600)

    # 获取活跃因子
    factors = client.list_factors()
"""

import json
import os
import subprocess
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def _ensure_wsl_running():
    """确保 WSL2 Ubuntu-22.04 虚拟机在运行。

    WSL2 默认在无活动连接 8 秒后休眠。此函数通过 ping 确保 VM 活着，
    同时触发 systemd 拉起因 VM 重启而停止的服务。
    """
    try:
        subprocess.run(
            ["wsl", "-d", "Ubuntu-22.04", "--", "bash", "-c", "echo ok"],
            capture_output=True, text=True, timeout=10, encoding="utf-8", errors="replace",
        )
    except Exception:
        pass  # WSL 不可用时静默失败，后续 wsl curl 会报告具体错误


def _wsl_curl(path: str, method: str = "GET", body: Optional[dict] = None, timeout: int = 30) -> dict:
    """通过 WSL curl 发送 HTTP 请求，绕过 Windows 端 localhost 转发问题。

    当公司防火墙阻止 WSL2 localhost 端口转发时使用。
    自动确保 WSL2 虚拟机在运行（如已停止则启动它）。
    """
    # 确保 WSL2 虚拟机在运行
    _ensure_wsl_running()

    url = f"http://localhost:8700{path}"
    cmd = ["wsl", "bash", "-c"]
    curl_cmd = f"curl -s -m {timeout} -X {method} '{url}' -H 'Content-Type: application/json'"
    if body is not None:
        data_str = json.dumps(body).replace("'", "'\\''")
        curl_cmd += f" -d '{data_str}'"
    cmd.append(curl_cmd)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5, encoding="utf-8", errors="replace")
        stdout = result.stdout.strip()
        if result.returncode != 0 or not stdout:
            raise RuntimeError(f"wsl curl failed (exit={result.returncode}): {result.stderr[:200]}")
        return json.loads(stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"wsl curl returned non-JSON: {stdout[:200]}")
    except FileNotFoundError:
        raise ConnectionError("wsl 不可用，请确认 WSL2 已安装且 Ubuntu-22.04 可运行")


class FactorLabClient:
    """factor-lab API 客户端"""

    def __init__(self, base_url: str = "http://localhost:8700"):
        self.base_url = base_url.rstrip("/")
        self._timeout = 30  # HTTP 请求超时（秒）

    # ─── 底层 HTTP ───

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        """发送 HTTP 请求，先尝试直接连接，失败后走 wsl curl 代理。"""
        # Tier 1: 直接 HTTP（Windows localhost → WSL2 转发）
        try:
            url = f"{self.base_url}{path}"
            data = json.dumps(body).encode("utf-8") if body else None

            req = urllib.request.Request(url, data=data, method=method)
            req.add_header("Content-Type", "application/json")
            req.add_header("Accept", "application/json")

            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            # 连接被拒绝或超时 → 尝试 wsl curl 回退
            pass

        # Tier 2: wsl curl 代理（绕过 Windows 防火墙）
        try:
            return _wsl_curl(path, method, body, self._timeout)
        except Exception as wsl_err:
            raise ConnectionError(
                f"无法连接 factor-lab ({self.base_url}): "
                f"直接连接和 wsl curl 均失败。确认 WSL2 已启动且 factor-lab 运行中。\n"
                f"  wsl curl error: {wsl_err}"
            )

    def _get(self, path: str) -> dict:
        return self._request("GET", path)

    def _post(self, path: str, body: dict) -> dict:
        return self._request("POST", path, body)

    # ─── 健康检查 ───

    def health_check(self) -> dict:
        """
        检查 factor-lab 是否可用

        Returns:
            {"status": "ok", "qlib_ok": True, "rd_agent_available": True, ...}
        """
        return self._get("/api/v1/health")

    def is_available(self) -> bool:
        """快速检查 factor-lab 是否可用"""
        try:
            result = self.health_check()
            return result.get("status") == "ok"
        except Exception:
            return False

    # ─── 因子挖掘 ───

    def mine_factors(
        self,
        instruments: Optional[List[str]] = None,
        date: Optional[str] = None,
        max_rounds: int = 5,
        config: str = "config/rd_agent.yaml",
    ) -> dict:
        """
        触发 RD-Agent 因子挖掘（异步任务）

        Args:
            instruments: 标的列表，None=全部
            date: 目标日期，None=今天
            max_rounds: 最大挖掘轮数
            config: RD-Agent 配置文件路径

        Returns:
            {"task_id": "mine_20260819_...", "status": "pending"}
        """
        body = {
            "instruments": instruments,
            "date": date or datetime.now().strftime("%Y-%m-%d"),
            "max_rounds": max_rounds,
            "config": config,
        }
        return self._post("/api/v1/factors/mine", body)

    # ─── 论文因子挖掘 ───

    def mine_papers(
        self,
        mode: str = "extract",
        max_papers: int = 5,
        date: Optional[str] = None,
        config: str = "config/papers.yaml",
    ) -> dict:
        """
        触发论文因子挖掘（异步任务）

        Args:
            mode: 挖掘模式 ("extract" | "validate")
            max_papers: 最多处理论文数
            date: 目标日期
            config: 配置文件路径

        Returns:
            {"task_id": "paper_20260819_...", "status": "pending"}
        """
        body = {
            "mode": mode,
            "max_papers": max_papers,
            "date": date or datetime.now().strftime("%Y-%m-%d"),
            "config": config,
        }
        return self._post("/api/v1/papers/mine", body)

    # ─── 任务管理 ───

    def get_task(self, task_id: str) -> dict:
        """
        查询任务状态

        Returns:
            {"task_id": "...", "status": "pending|running|done|failed", "progress": 0.5, "result": {...}}
        """
        return self._get(f"/api/v1/tasks/{task_id}")

    def wait_for_task(
        self,
        task_id: str,
        timeout: int = 600,
        poll_interval: float = 3.0,
        on_progress: Optional[callable] = None,
    ) -> dict:
        """
        轮询等待任务完成

        Args:
            task_id: 任务 ID
            timeout: 超时时间（秒）
            poll_interval: 轮询间隔（秒）
            on_progress: 进度回调 callback(progress: float, status: str)

        Returns:
            任务的最终 result dict

        Raises:
            TimeoutError: 超时
            RuntimeError: 任务失败
        """
        start = time.time()
        last_progress = -1.0

        while True:
            elapsed = time.time() - start
            if elapsed > timeout:
                raise TimeoutError(f"任务 {task_id} 超时 ({timeout}s)")

            task = self.get_task(task_id)
            status = task.get("status")
            progress = task.get("progress", 0.0)

            # 进度回调
            if on_progress and progress != last_progress:
                on_progress(progress, status)
                last_progress = progress

            if status == "done":
                return task.get("result", {})
            elif status == "failed":
                raise RuntimeError(f"任务 {task_id} 失败: {task.get('error', '未知错误')}")

            time.sleep(poll_interval)

    # ─── 因子查询 ───

    def list_factors(self) -> List[dict]:
        """获取活跃因子列表"""
        return self._get("/api/v1/factors")

    def get_factor(self, factor_id: str) -> dict:
        """获取单个因子详情"""
        return self._get(f"/api/v1/factors/{factor_id}")

    # ─── 因子计算 ───

    def compute_factors(
        self,
        date: str,
        factor_ids: Optional[List[str]] = None,
        instruments: str = "all",
    ) -> dict:
        """
        计算因子信号值

        Args:
            date: 目标日期
            factor_ids: 因子 ID 列表，None=全部活跃因子
            instruments: 标的范围 ("all" | 逗号分隔列表)
        """
        body = {
            "factor_ids": factor_ids,
            "date": date,
            "instruments": instruments,
        }
        return self._post("/api/v1/factors/compute", body)

    def combine_factors(
        self,
        date: str,
        factor_ids: Optional[List[str]] = None,
        method: str = "ic_weighted",
        top_k: int = 50,
        timeout: int = 600,
    ) -> dict:
        """
        多因子合成 + 排名

        注意：此操作计算所有因子对全市场标的的信号值，耗时较长（26 因子约 5-7 分钟）。

        Args:
            date: 目标日期
            factor_ids: 因子 ID 列表
            method: 合成方法 ("ic_weighted" | "equal_weight" | "rank_ensemble")
            top_k: 返回 top N 标的
            timeout: 请求超时（秒），默认 600s
        """
        body = {
            "factor_ids": factor_ids,
            "method": method,
            "top_k": top_k,
            "date": date,
        }
        # combine_factors 是重操作，使用更长的 timeout
        saved_timeout = self._timeout
        self._timeout = timeout
        try:
            return self._post("/api/v1/factors/combine", body)
        finally:
            self._timeout = saved_timeout


# ─── 便捷函数 ───

def get_client() -> FactorLabClient:
    """获取默认客户端实例"""
    return FactorLabClient()


def run_paper_mining_sync(
    max_papers: int = 5,
    timeout: int = 1200,
    on_progress: Optional[callable] = None,
) -> dict:
    """
    同步执行论文因子挖掘（触发 + 等待完成）

    便捷函数，用于在 daily pipeline 中调用。
    """
    client = get_client()
    task = client.mine_papers(max_papers=max_papers)
    return client.wait_for_task(task["task_id"], timeout=timeout, on_progress=on_progress)