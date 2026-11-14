"""真实子进程执行器：成功 / 找不到 / 超时 / 其他 OSError。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from fcmd.apps.devman import _runner
from fcmd.apps.devman._errors import DevmanError


def test_run_captures_stdout_and_stderr() -> None:
    result = _runner.SubprocessRunner().run(
        [sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr)"]
    )
    assert result.returncode == 0
    assert "out" in result.stdout
    assert "err" in result.stderr


def test_run_returns_nonzero_without_raising() -> None:
    result = _runner.SubprocessRunner().run([sys.executable, "-c", "raise SystemExit(3)"])
    assert result.returncode == 3


def test_run_passes_env() -> None:
    result = _runner.SubprocessRunner().run(
        [sys.executable, "-c", "import os; print(os.environ.get('DEVMAN_TEST', ''))"],
        env={"DEVMAN_TEST": "yes"},
    )
    assert result.stdout.strip() == "yes"


def test_run_missing_executable() -> None:
    with pytest.raises(DevmanError) as info:
        _runner.SubprocessRunner().run(["definitely-not-a-real-command-xyz"])
    assert "找不到可执行文件" in str(info.value)


def test_run_timeout() -> None:
    with pytest.raises(DevmanError) as info:
        _runner.SubprocessRunner().run([sys.executable, "-c", "import time; time.sleep(5)"], timeout_s=1)
    assert "命令超时" in str(info.value)


def test_run_os_error(tmp_path: Path) -> None:
    directory = tmp_path / "as-executable"
    directory.mkdir()
    with pytest.raises(DevmanError) as info:
        _runner.SubprocessRunner().run([str(directory)])
    assert "失败" in str(info.value)
