"""
factor-lab API Server — FastAPI 服务

提供因子研发能力的 REST API，运行在 WSL2 内，端口 8700。
Windows 端通过 localhost:8700 访问。

端点:
  GET  /api/v1/health              — 健康检查
  POST /api/v1/factors/mine        — 触发 RD-Agent 因子挖掘
  POST /api/v1/papers/mine         — 触发论文因子挖掘
  GET  /api/v1/tasks/{task_id}     — 查询任务状态
  GET  /api/v1/factors              — 获取活跃因子列表
  GET  /api/v1/factors/{factor_id}  — 获取单个因子详情
  POST /api/v1/factors/compute      — 计算因子信号值
  POST /api/v1/factors/combine      — 多因子合成 + 排名
"""

import json
import os
import sys
import threading
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# 确保 factor-lab 目录在 sys.path 中
FACTOR_LAB_DIR = Path(__file__).resolve().parent
if str(FACTOR_LAB_DIR) not in sys.path:
    sys.path.insert(0, str(FACTOR_LAB_DIR))

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn

# ─── 应用初始化 ───

app = FastAPI(
    title="factor-lab API",
    description="RD-Agent + 论文因子挖掘服务",
    version="1.0.0",
)

# ─── 后台任务管理 ───

_tasks: Dict[str, Dict[str, Any]] = {}
_tasks_lock = threading.Lock()


def _create_task(task_type: str) -> str:
    """创建后台任务并返回 task_id"""
    task_id = f"{task_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    with _tasks_lock:
        _tasks[task_id] = {
            "task_id": task_id,
            "type": task_type,
            "status": "pending",
            "progress": 0.0,
            "created_at": datetime.now().isoformat(),
            "result": None,
            "error": None,
        }
    return task_id


def _update_task(task_id: str, **kwargs):
    """更新任务状态"""
    with _tasks_lock:
        if task_id in _tasks:
            _tasks[task_id].update(kwargs)


def _run_factor_mining(task_id: str, params: dict):
    """后台执行 RD-Agent 因子挖掘"""
    try:
        _update_task(task_id, status="running", progress=0.1)

        from rd_runner import RDAgentRunner

        config_path = params.get("config", "config/rd_agent.yaml")
        runner = RDAgentRunner(str(FACTOR_LAB_DIR / config_path))

        _update_task(task_id, progress=0.3)

        result = runner.run_mining(
            instruments=params.get("instruments"),
            start_time=params.get("start_time", "2020-01-01"),
            end_time=params.get("end_time", datetime.now().strftime("%Y-%m-%d")),
        )

        _update_task(task_id, progress=0.9)

        _update_task(
            task_id,
            status="done",
            progress=1.0,
            result={
                "summary": result.get("summary", {}),
                "factor_count": len(result.get("factors", [])),
                "factors": [
                    {
                        "id": f["id"],
                        "name": f["name"],
                        "source": f.get("source", "unknown"),
                        "ic_mean": f.get("ic_mean"),
                    }
                    for f in result.get("factors", [])
                ],
            },
        )
    except Exception as e:
        _update_task(
            task_id,
            status="failed",
            error=str(e),
            error_traceback=traceback.format_exc(),
        )


def _run_paper_mining(task_id: str, params: dict):
    """后台执行论文因子挖掘"""
    try:
        _update_task(task_id, status="running", progress=0.1)

        from paper_miner import PaperMiner

        config_path = params.get("config", "config/papers.yaml")
        miner = PaperMiner(str(FACTOR_LAB_DIR / config_path))

        _update_task(task_id, progress=0.3)

        result = miner.run_mining(
            max_papers=params.get("max_papers", 5),
            mode=params.get("mode", "extract"),
        )

        _update_task(task_id, progress=0.9)

        _update_task(
            task_id,
            status="done",
            progress=1.0,
            result={
                "summary": result.get("summary", {}),
                "factor_count": len(result.get("factors", [])),
                "papers_processed": result.get("papers_processed", 0),
            },
        )
    except Exception as e:
        _update_task(
            task_id,
            status="failed",
            error=str(e),
            error_traceback=traceback.format_exc(),
        )


# ─── 请求/响应模型 ───

class FactorMineRequest(BaseModel):
    instruments: Optional[List[str]] = None
    date: Optional[str] = None
    max_rounds: int = 5
    config: str = "config/rd_agent.yaml"


class PaperMineRequest(BaseModel):
    mode: str = "extract"
    max_papers: int = 5
    date: Optional[str] = None
    config: str = "config/papers.yaml"


class FactorComputeRequest(BaseModel):
    factor_ids: Optional[List[str]] = None
    date: str
    instruments: str = "all"


class FactorCombineRequest(BaseModel):
    factor_ids: Optional[List[str]] = None
    method: str = "ic_weighted"
    top_k: int = 50
    date: str


# ─── API 端点 ───


@app.get("/api/v1/health")
async def health_check():
    """健康检查"""
    health = {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "python": sys.version,
    }

    # Qlib 可用性
    try:
        import qlib
        from config import get_qlib_bin_dir
        bin_dir = get_qlib_bin_dir()
        health["qlib_ok"] = Path(bin_dir).exists()
        health["qlib_data"] = bin_dir
    except Exception as e:
        health["qlib_ok"] = False
        health["qlib_error"] = str(e)

    # RD-Agent 可用性
    try:
        import rdagent
        health["rd_agent_available"] = True
        health["rd_agent_version"] = getattr(rdagent, "__version__", "unknown")
    except ImportError:
        health["rd_agent_available"] = False

    # DeepSeek API
    health["deepseek_ok"] = bool(os.environ.get("DEEPSEEK_API_KEY"))

    # 活跃因子数
    try:
        from store import FactorStore
        store = FactorStore()
        factors = store.list_active()
        health["factors_count"] = len(factors)
    except Exception:
        health["factors_count"] = 0

    return health


@app.post("/api/v1/factors/mine")
async def trigger_factor_mining(req: FactorMineRequest):
    """触发 RD-Agent 因子挖掘（异步）"""
    task_id = _create_task("mine")
    params = {
        "instruments": req.instruments,
        "max_rounds": req.max_rounds,
        "config": req.config,
        "start_time": "2020-01-01",
        "end_time": req.date or datetime.now().strftime("%Y-%m-%d"),
    }

    thread = threading.Thread(
        target=_run_factor_mining,
        args=(task_id, params),
        daemon=True,
    )
    thread.start()

    return {"task_id": task_id, "status": "pending"}


@app.post("/api/v1/papers/mine")
async def trigger_paper_mining(req: PaperMineRequest):
    """触发论文因子挖掘（异步）"""
    task_id = _create_task("paper")
    params = {
        "mode": req.mode,
        "max_papers": req.max_papers,
        "config": req.config,
    }

    thread = threading.Thread(
        target=_run_paper_mining,
        args=(task_id, params),
        daemon=True,
    )
    thread.start()

    return {"task_id": task_id, "status": "pending"}


@app.get("/api/v1/tasks/{task_id}")
async def get_task_status(task_id: str):
    """查询任务状态"""
    with _tasks_lock:
        task = _tasks.get(task_id)

    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    return task


@app.get("/api/v1/factors")
async def list_factors():
    """获取活跃因子列表"""
    try:
        from store import FactorStore
        store = FactorStore()
        factors = store.list_active()

        return {
            "factors": factors,
            "count": len(factors),
            "updated_at": datetime.now().isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/factors/{factor_id}")
async def get_factor(factor_id: str):
    """获取单个因子详情"""
    try:
        from store import FactorStore
        store = FactorStore()
        factor = store.get(factor_id)

        if factor is None:
            raise HTTPException(status_code=404, detail=f"Factor not found: {factor_id}")

        return factor
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/factors/compute")
async def compute_factors(req: FactorComputeRequest):
    """计算因子在指定日期的全市场信号值"""
    try:
        from store import FactorStore
        from validator import _compute_ic_pandas_impl

        store = FactorStore()

        if req.factor_ids:
            factors = [store.get(fid) for fid in req.factor_ids]
            factors = [f for f in factors if f is not None]
        else:
            factors = store.list_active()

        if not factors:
            return {"signals": {}, "date": req.date, "factor_count": 0}

        instruments = ["csi300"] if req.instruments == "all" else [req.instruments]
        signals = {}

        for f in factors:
            fid = f["id"]
            try:
                result = _compute_ic_pandas_impl(f, instruments, req.date, req.date)
                if result and not result.get("error"):
                    signals[fid] = result.get("ic_mean")
            except Exception:
                signals[fid] = None

        return {
            "signals": signals,
            "date": req.date,
            "factor_count": len(factors),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/factors/combine")
async def combine_factors(req: FactorCombineRequest):
    """多因子合成 + 排名"""
    try:
        from store import FactorStore
        from combiner import FactorCombiner

        store = FactorStore()

        if req.factor_ids:
            factors = [store.get(fid) for fid in req.factor_ids]
            factors = [f for f in factors if f is not None]
        else:
            factors = store.list_active()

        if not factors:
            return {"rankings": [], "method": req.method, "date": req.date}

        combiner = FactorCombiner()
        rankings = combiner.combine(
            factors=factors,
            method=req.method,
            top_k=req.top_k,
            date=req.date,
        )

        return {
            "rankings": rankings,
            "method": req.method,
            "date": req.date,
            "factor_count": len(factors),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── 启动入口 ───

if __name__ == "__main__":
    port = int(os.environ.get("FACTOR_LAB_PORT", "8700"))
    uvicorn.run(app, host="0.0.0.0", port=port)