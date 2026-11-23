"""gui 测试的公共隔离：窗口冒烟不碰真实宿主机外部环境。"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _hermetic_vllm() -> Any:
    """llmw 窗口构造会探测 vLLM 平台，而探测走 ``wsl -d <实例>``（等于启动实例）。"""
    from tests.helpers import forbid_wsl_distro_start, hermetic_vllm_platform

    restore_platform = hermetic_vllm_platform()
    restore_guard = forbid_wsl_distro_start()
    yield
    restore_guard()
    restore_platform()
