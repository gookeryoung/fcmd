"""filescan 测试隔离：持久化目录默认指向临时 HOME。

扫描缓存与 GUI 设置都落在 ``${FCMD_HOME:-~/.fcmd}`` 下，用例默认改为
``tmp_path``，避免读写真实用户目录；需要验证默认路径的用例自行覆盖。
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_fcmd_home(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    """把 ``FCMD_HOME`` 指向用例专属临时目录。

    不能直接用 ``tmp_path``：多数用例把它当作被扫描的根目录，缓存文件落在根下
    会改变 ``files_seen`` 与后缀跳过计数。
    """
    monkeypatch.setenv("FCMD_HOME", str(tmp_path_factory.mktemp("fcmd_home")))
