"""test_cli_model_info.py。

CLI model 子命令 e2e 测试。
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.e2e]


def test_model_info_runs(fcmd):
    """fcmd model info 应正常执行并输出模型信息。"""
    r = fcmd("model", "info")
    r.check_ok()
    assert r.stdout


def test_model_info_defaults(fcmd):
    """fcmd model info 应含关键信息字段。"""
    r = fcmd("model", "info")
    r.check_ok()
    out = r.stdout.lower()
    assert any(k in out for k in ("model", "version", "name"))
