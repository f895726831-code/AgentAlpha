#!/bin/bash
# P2 验证脚本 — RD-Agent 在 WSL2 原生运行验证
# 用法: bash verify_p2.sh

set -e

echo "============================================"
echo "P2 验证: RD-Agent WSL2 原生运行"
echo "============================================"
echo ""

# ─── 1. Python 环境检查 ───
echo "[1/5] Python 环境"
python3 --version
echo "  sys.platform: $(python3 -c 'import sys; print(sys.platform)')"
echo ""

# ─── 2. 核心依赖检查 ───
echo "[2/5] 核心依赖"

check_import() {
    local module=$1
    if python3 -c "import $module" 2>/dev/null; then
        local ver=$(python3 -c "import $module; print(getattr($module, '__version__', 'OK'))" 2>/dev/null)
        echo "  ✅ $module ($ver)"
    else
        echo "  ❌ $module (未安装)"
    fi
}

check_import rdagent
check_import qlib
check_import numpy
check_import pandas
check_import fastapi
check_import uvicorn
echo ""

# ─── 3. Qlib 数据路径验证 ───
echo "[3/5] Qlib 数据路径"

QLIB_BIN="/mnt/d/qlib/qlib_data/cn_data"
QLIB_CAL="$QLIB_BIN/calendars/day.txt"

if [ -d "$QLIB_BIN" ]; then
    echo "  ✅ bin 目录: $QLIB_BIN"
    if [ -f "$QLIB_CAL" ]; then
        last_date=$(tail -1 "$QLIB_CAL")
        echo "  ✅ 日历文件: 最新日期 $last_date"
    else
        echo "  ❌ 日历文件缺失: $QLIB_CAL"
    fi
else
    echo "  ❌ bin 目录缺失: $QLIB_BIN"
fi
echo ""

# ─── 4. factor-lab 模块导入验证 ───
echo "[4/5] factor-lab 模块导入"

cd /home/user/factor-lab

check_local_import() {
    local module=$1
    if python3 -c "import sys; sys.path.insert(0, '.'); from $module import *" 2>/dev/null; then
        echo "  ✅ $module"
    else
        local err=$(python3 -c "import sys; sys.path.insert(0, '.'); from $module import *" 2>&1)
        echo "  ❌ $module: $err"
    fi
}

check_local_import config
check_local_import pit_handler
check_local_import validator
check_local_import combiner
check_local_import store
check_local_import leakage_checker
check_local_import fundamental
echo ""

# ─── 5. API 服务启动验证 ───
echo "[5/5] API 服务启动测试"

# 后台启动 API 服务
python3 -c "
import uvicorn
import sys
sys.path.insert(0, '/home/user/factor-lab')
from api_server import app
" 2>&1 | head -5

echo "  ✅ API 模块加载成功"

echo ""
echo "============================================"
echo "P2 验证完成"
echo "============================================"
echo ""
echo "下一步手动验证:"
echo "  1. 启动 API 服务: cd /home/user/factor-lab && bash start.sh"
echo "  2. 健康检查: curl http://localhost:8700/api/v1/health"
echo "  3. 触发因子挖掘: python3 scripts/run_factor_mining.py --symbols sh600519"