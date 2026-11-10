"""``mirrortool`` 一级入口测试：注册形态与参数透传。

镜像探测/测速的实现语义由 ``tests/tools/dev/test_cli_envdev.py``
（``TestCheckCernetMirrors`` 等）覆盖；本文件只验证一级入口已注册为
单命令工具，且与 ``envdev mirror`` 共用 :mod:`fcmd.tools.dev._envdev_mirrors`
的同一份实现（不复制逻辑）。
"""

from __future__ import annotations

import sys
from typing import Any

import pytest

import fcmd.core.shared.net
import fcmd.tools.dev._envdev_mirrors as mirrors_mod
import fcmd.tools.dev.mirrortool
from fcmd.core.shared.net import UrlProbe, probe_score, unreachable_latency
from fcmd.core.toolkit import _TOOL_REGISTRY, list_subcommands


# ---------------------------------------------------------------------- #
# 测试辅助
# ---------------------------------------------------------------------- #
def _fake_check_urls(probe: dict[str, tuple[bool, float]]) -> Any:
    """按 ``{url: (可达, 延迟)}`` 构造 ``check_urls`` 替身（可达优先 + 延迟升序）。"""

    def fake(urls: list[str], timeout: float = 5.0, workers: int = 8) -> list[tuple[str, bool, float]]:
        results = [(url, *probe[url]) for url in urls if url in probe]
        return sorted(results, key=lambda r: (not r[1], r[2]))

    return fake


def _fake_probe_mirrors(probe: dict[str, tuple[bool, float, float]]) -> Any:
    """按 ``{url: (可达, 延迟ms, 吞吐KB/s)}`` 构造 ``probe_mirrors`` 替身。"""

    def fake(  # noqa: PLR0913, PLR0917
        urls: list[str],
        timeout: float = 5.0,
        workers: int = 8,
        user_agent: str = "",
        sample_bytes: int = 131072,
        allow_html: bool = False,
    ) -> list[UrlProbe]:
        results = []
        for url in urls:
            if url not in probe:
                continue
            ok, latency, throughput = probe[url]
            results.append(UrlProbe(url, ok, latency, throughput, 131072 if ok else 0, "" if ok else "不可达"))
        return sorted(results, key=lambda p: (not p.ok, -probe_score(p), p.latency_ms))

    return fake


def _run_main(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> int:
    """以 ``argv`` 调用 ``mirrortool.main()``，返回退出码。"""
    monkeypatch.setattr(sys, "argv", ["mirrortool", *argv])
    with pytest.raises(SystemExit) as exc_info:
        fcmd.tools.dev.mirrortool.main()
    code = exc_info.value.code
    return 0 if code is None else int(code)


# ---------------------------------------------------------------------- #
# 注册形态
# ---------------------------------------------------------------------- #
class TestMirrortoolRegistration:
    """``mirrortool`` 以单命令工具注册（无子命令）。"""

    def test_registered_as_single_command_tool(self) -> None:
        assert "mirrortool" in _TOOL_REGISTRY
        assert list_subcommands("mirrortool", include_hidden=True) == []

    def test_help_lists_service_choices(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``--help`` 暴露服务选项（argparse choices 来自注解 Literal）。"""
        monkeypatch.setattr(sys, "argv", ["mirrortool", "--help"])
        with pytest.raises(SystemExit) as exc_info:
            fcmd.tools.dev.mirrortool.main()
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "--service" in out
        assert "maven" in out


# ---------------------------------------------------------------------- #
# 参数透传到镜像域实现
# ---------------------------------------------------------------------- #
class TestMirrortoolCli:
    """入口参数经 ``check_cernet_mirrors`` 透传，行为与 ``envdev mirror`` 一致。"""

    def test_probe_builtin_sites(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """``--source builtin`` 探测内置站点列表，全部可达返回 0。"""
        probe = dict.fromkeys(mirrors_mod._CERNET_MIRROR_SITES.values(), (True, 10.0))
        monkeypatch.setattr("fcmd.tools.dev._envdev_mirrors.check_urls", _fake_check_urls(probe))

        assert _run_main(monkeypatch, ["--source", "builtin"]) == 0
        out = capsys.readouterr().out
        assert "教育网镜像站点探测" in out
        assert "help.mirrors.cernet.edu.cn" in out

    def test_all_unreachable_returns_one(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """全部站点不可达时实现返回 1（与 ``envdev mirror`` 同一实现，CLI 层按 Python 任务语义仍退出 0）。"""
        probe = {url: (False, unreachable_latency()) for url in mirrors_mod._CERNET_MIRROR_SITES.values()}
        monkeypatch.setattr("fcmd.tools.dev._envdev_mirrors.check_urls", _fake_check_urls(probe))

        assert fcmd.tools.dev.mirrortool.mirror(source="builtin") == 1
        assert "全部镜像站点不可达" in capsys.readouterr().out

    def test_service_apply_delegates_to_mirror_setup(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``--service pip --apply`` 把推荐镜像交给 pip 配置函数并提示已应用。"""
        applied: list[str] = []
        pip_urls = mirrors_mod._PIP_PROBE_URLS
        probe = dict.fromkeys(pip_urls.values(), (True, 100.0, 50.0))
        probe[pip_urls["nju"]] = (True, 12.0, 2000.0)
        monkeypatch.setattr("fcmd.tools.dev._envdev_mirrors.probe_mirrors", _fake_probe_mirrors(probe))
        monkeypatch.setitem(mirrors_mod._MIRROR_SERVICE_APPLY, "pip", applied.append)

        assert _run_main(monkeypatch, ["--service", "pip", "--apply"]) == 0
        assert applied == ["nju"]
        assert "已应用 pip 镜像: nju" in capsys.readouterr().out
