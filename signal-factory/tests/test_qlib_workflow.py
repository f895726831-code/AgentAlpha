"""Qlib workflow integration smoke test.

This test runs the RD-Agent qlib factor template end-to-end. It is skipped by
default because it requires:

* a working ``qlib`` installation,
* the ``rdagent`` package (with its qlib scenario templates), and
* local qlib data (``~/.qlib/qlib_data/cn_data`` by default).

Set ``RUN_QLIB_INTEGRATION=1`` to enable it.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_QLIB_INTEGRATION") != "1",
    reason="set RUN_QLIB_INTEGRATION=1 to run the qlib integration test",
)


def _template_dir() -> Path:
    rdagent = pytest.importorskip("rdagent")
    base = Path(rdagent.__file__).resolve().parent
    return base / "scenarios" / "qlib" / "experiment" / "factor_template"


def test_qlib_workflow_smoke(tmp_path: Path) -> None:
    pytest.importorskip("qlib")

    template = _template_dir()
    conf_src = template / "conf_baseline.yaml"
    read_src = template / "read_exp_res.py"
    if not conf_src.exists():
        pytest.skip(f"rdagent qlib template not found: {template}")

    shutil.copy(conf_src, tmp_path / "conf.yaml")
    if read_src.exists():
        shutil.copy(read_src, tmp_path / "read_exp_res.py")

    os.environ["MLFLOW_ALLOW_FILE_STORE"] = "true"
    qrun_code = "from qlib.cli.run import workflow; workflow('conf.yaml')"
    result = subprocess.run(
        [sys.executable, "-c", qrun_code],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, (result.stdout[-2000:] + "\n" + result.stderr[-2000:])
