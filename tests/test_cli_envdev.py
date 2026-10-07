"""envdev 工具测试。

验证 ``fcmd.cli.dev.envdev`` 模块：
- 工具注册（分组入口 lang/app/check/all 公开，细粒度步骤命令隐藏）
- setup_python_env / setup_python_mirror / setup_conda_mirror Python 环境
- _setup_rust_mirror / _download_rustup / _install_rust_toolchain / setup_rust_env Rust 工具链
- _setup_bun_mirror / _install_bun / setup_js_env JavaScript 工具链
- setup_all_env 一键编排
- setup_lang_env / setup_app_env 分组路由分发
- check_env 环境检测（工具链 + 镜像源）
- setup_linux_system_mirror / install_linux_qt_libs / install_linux_fonts / install_linux_docker Linux 专用
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any
from urllib.error import URLError

import pytest

import fcmd as fx
import fcmd.cli.dev.envdev
import fcmd.cli.dev.envdev_core
import fcmd.cli.dev.envdev_go
import fcmd.dsl.actions.net
from fcmd.apis.toolkit import _TOOL_REGISTRY
from fcmd.models import CommandResult


# ============================================================================ #
# 测试辅助
# ============================================================================ #
def _recording_run(calls: list[list[str]]) -> Any:
    """创建记录调用的 fake ``run_command`` 函数，返回成功结果。"""

    def run(cmd: list[str], *, capture: bool = False, check: bool = False) -> CommandResult:
        calls.append(cmd)
        return CommandResult(cmd=list(cmd), returncode=0, stdout="", stderr="")

    return run


@pytest.fixture(autouse=True)
def _fake_persist_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """将 envdev.persist_env 替换为只更新 os.environ 的假实现。

    避免测试在 Windows 上真正写入注册表 ``HKCU\\Environment``（污染真实环境），
    同时保留 ``os.environ`` 更新以维持既有断言。返回记录字典供断言持久化调用。
    """
    recorded: dict[str, str] = {}

    def fake_persist(name: str, value: str) -> None:
        recorded[name] = value
        os.environ[name] = value

    monkeypatch.setattr("fcmd.cli.dev.envdev.persist_env", fake_persist)
    return recorded


# ============================================================================ #
# 注册验证
# ============================================================================ #
class TestToolsRegistration:
    """envdev 工具注册验证。"""

    def test_all_tools_registered(self) -> None:
        """envdev 应在 _TOOL_REGISTRY 中注册。"""
        for name in ("envdev",):
            assert name in _TOOL_REGISTRY, f"工具 {name!r} 未注册"

    def test_envdev_public_subcommands(self) -> None:
        """envdev 公开子命令应注册（分组入口 lang/app/check/all/mirror）。"""
        subs = fx.list_subcommands("envdev")
        assert subs == ["all", "app", "check", "lang", "mirror"]

    def test_envdev_hidden_subcommands(self) -> None:
        """envdev 隐藏子命令应注册（镜像源/下载/安装明细步骤 + Linux 专用）。"""
        subs = fx.list_subcommands("envdev", include_hidden=True)
        for name in (
            "setup-python",
            "setup-conda",
            "setup-rust",
            "download-rustup",
            "install-rust",
            "setup-bun",
            "install-bun",
            "setup-linux-mirror",
            "install-openssh",
            "install-qt-libs",
            "install-fonts",
            "install-docker",
            "setup-docker-mirror",
            "uninstall-gnome-remote",
            "install-xfce",
            "install-xrdp",
            "configure-lightdm",
            "remote",
        ):
            assert name in subs, f"隐藏子命令 {name!r} 未注册"


# ============================================================================ #
# envdev 测试
# ============================================================================ #
class TestEnvdev:
    """envdev 工具测试。"""

    def test_setup_python_mirror(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        _fake_persist_env: dict[str, str],
    ) -> None:
        """配置 Python 镜像源（持久化环境变量 + 写入 pip 配置文件）。"""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.delenv("PIP_INDEX_URL", raising=False)
        monkeypatch.delenv("UV_INDEX_URL", raising=False)

        fcmd.cli.dev.envdev.setup_python_mirror("aliyun")
        captured = capsys.readouterr()
        assert "Python 镜像源已配置" in captured.out
        # 环境变量已持久化（persist_env 被调用）且当前进程可见
        assert _fake_persist_env["PIP_INDEX_URL"] == "https://mirrors.aliyun.com/pypi/simple/"
        assert os.environ["PIP_INDEX_URL"] == "https://mirrors.aliyun.com/pypi/simple/"
        assert "UV_INDEX_URL" in _fake_persist_env
        assert "UV_INDEX_URL" in os.environ
        # 配置文件已写入
        if sys.platform.startswith("linux"):
            config_path = tmp_path / ".pip" / "pip.conf"
        else:
            config_path = tmp_path / "pip" / "pip.ini"
        assert config_path.exists()
        assert "aliyun" in config_path.read_text(encoding="utf-8")

    def test_setup_python_unknown_mirror(self, capsys: pytest.CaptureFixture[str]) -> None:
        """未知 Python 镜像源打印提示。"""
        fcmd.cli.dev.envdev.setup_python_mirror("unknown")
        captured = capsys.readouterr()
        assert "未知" in captured.out

    def test_setup_conda_mirror(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """配置 Conda 镜像源（写入 ~/.condarc）。"""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        fcmd.cli.dev.envdev.setup_conda_mirror("ustc")
        captured = capsys.readouterr()
        assert "Conda 镜像源已配置" in captured.out
        condarc = tmp_path / ".condarc"
        assert condarc.exists()
        assert "ustc" in condarc.read_text(encoding="utf-8")

    def test_setup_conda_unknown_mirror(self, capsys: pytest.CaptureFixture[str]) -> None:
        """未知 Conda 镜像源打印提示。"""
        fcmd.cli.dev.envdev.setup_conda_mirror("unknown")
        captured = capsys.readouterr()
        assert "未知" in captured.out

    def test_setup_rust_mirror(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """配置 Rust 镜像源（rustup 环境变量 + cargo config + sccache 目录）。"""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # _RUST_SCCACHE_DIR 是模块级常量，导入时已求值，需单独 mock
        monkeypatch.setattr("fcmd.cli.dev.envdev._RUST_SCCACHE_DIR", tmp_path / ".cargo" / "sccache")
        monkeypatch.delenv("RUSTUP_DIST_SERVER", raising=False)
        fcmd.cli.dev.envdev._setup_rust_mirror("tsinghua")
        captured = capsys.readouterr()
        assert "Rust rustup 镜像源已配置: tsinghua" in captured.out
        assert "Rust cargo 镜像源已配置" in captured.out
        assert os.environ["RUSTUP_DIST_SERVER"] == "https://mirrors.tuna.tsinghua.edu.cn/rustup"
        config_path = tmp_path / ".cargo" / "config.toml"
        assert config_path.exists()
        assert "tsinghua" in config_path.read_text(encoding="utf-8")
        # sccache 目录已创建
        assert (tmp_path / ".cargo" / "sccache").is_dir()

    def test_setup_rust_unknown_mirror(self, capsys: pytest.CaptureFixture[str]) -> None:
        """未知 Rust 镜像源打印提示。"""
        fcmd.cli.dev.envdev._setup_rust_mirror("unknown")
        captured = capsys.readouterr()
        assert "未知" in captured.out

    def test_download_rustup_already_installed(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """rustup 已安装时跳过下载。"""
        monkeypatch.setattr("fcmd.cli.dev.envdev.shutil.which", lambda _: "/usr/bin/rustup")
        fcmd.cli.dev.envdev._download_rustup()
        captured = capsys.readouterr()
        assert "已安装" in captured.out

    def test_download_rustup_windows(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Windows 下载 rustup-init.exe。"""
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr("fcmd.cli.dev.envdev.shutil.which", lambda _: None)

        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.dev.envdev.run_command", _recording_run(calls))

        fcmd.cli.dev.envdev._download_rustup()
        captured = capsys.readouterr()
        assert "rustup-init.exe" in captured.out
        assert any("powershell" in c[0] for c in calls)

    def test_download_rustup_linux(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """Linux 下载 rustup-init.sh。"""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr("fcmd.cli.dev.envdev.shutil.which", lambda _: None)

        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.dev.envdev.run_command", _recording_run(calls))

        fcmd.cli.dev.envdev._download_rustup()
        captured = capsys.readouterr()
        assert "rustup-init.sh" in captured.out
        assert any("curl" in c[0] for c in calls)

    def test_install_rust_no_rustup(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """rustup 未安装时跳过工具链安装。"""
        monkeypatch.setattr("fcmd.cli.dev.envdev.shutil.which", lambda _: None)
        fcmd.cli.dev.envdev._install_rust_toolchain("stable")
        captured = capsys.readouterr()
        assert "未安装" in captured.out

    def test_install_rust_success(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """rustup 已安装时调用 rustup toolchain install。"""
        monkeypatch.setattr("fcmd.cli.dev.envdev.shutil.which", lambda _: "/usr/bin/rustup")

        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.dev.envdev.run_command", _recording_run(calls))

        fcmd.cli.dev.envdev._install_rust_toolchain("nightly")
        captured = capsys.readouterr()
        assert "nightly 安装完成" in captured.out
        assert calls == [["rustup", "toolchain", "install", "nightly"]]

    def test_setup_rust_env_orchestration(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """setup_rust_env 依次调用镜像源/下载/安装三个步骤。"""
        calls: list[str] = []
        monkeypatch.setattr("fcmd.cli.dev.envdev._setup_rust_mirror", lambda m: calls.append(f"mirror:{m}"))
        monkeypatch.setattr("fcmd.cli.dev.envdev._download_rustup", lambda: calls.append("download"))
        monkeypatch.setattr("fcmd.cli.dev.envdev._install_rust_toolchain", lambda v: calls.append(f"install:{v}"))

        fcmd.cli.dev.envdev.setup_rust_env(mirror="ustc", rust_version="nightly")
        assert calls == ["mirror:ustc", "download", "install:nightly"]
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_setup_python_env_orchestration(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """setup_python_env 依次调用 pip 与 Conda 镜像源配置。"""
        calls: list[str] = []
        monkeypatch.setattr("fcmd.cli.dev.envdev.setup_python_mirror", lambda m: calls.append(f"pip:{m}"))
        monkeypatch.setattr("fcmd.cli.dev.envdev.setup_conda_mirror", lambda m: calls.append(f"conda:{m}"))

        fcmd.cli.dev.envdev.setup_python_env(mirror="ustc")
        assert calls == ["pip:ustc", "conda:ustc"]
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_setup_all_env_orchestration(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """setup_all_env 依次调用各语言一键命令与 Linux 专用步骤。"""
        calls: list[str] = []
        monkeypatch.setattr("fcmd.cli.dev.envdev.setup_python_env", lambda m: calls.append(f"python:{m}"))
        monkeypatch.setattr("fcmd.cli.dev.envdev.setup_js_env", lambda: calls.append("js"))
        monkeypatch.setattr("fcmd.cli.dev.envdev.setup_rust_env", lambda m, v: calls.append(f"rust:{m}:{v}"))
        monkeypatch.setattr("fcmd.cli.dev.envdev.setup_go_env", lambda **_kw: calls.append("go"))
        monkeypatch.setattr("fcmd.cli.dev.envdev.setup_java_env", lambda m: calls.append(f"java:{m}"))
        monkeypatch.setattr("fcmd.cli.dev.envdev.setup_node_env", lambda **_kw: calls.append("node"))
        monkeypatch.setattr("fcmd.cli.dev.envdev.setup_linux_system_mirror", lambda: calls.append("linux-mirror"))
        monkeypatch.setattr("fcmd.cli.dev.envdev.install_linux_qt_libs", lambda: calls.append("qt"))
        monkeypatch.setattr("fcmd.cli.dev.envdev.install_linux_fonts", lambda: calls.append("fonts"))
        monkeypatch.setattr("fcmd.cli.dev.envdev.install_linux_docker", lambda: calls.append("docker"))

        fcmd.cli.dev.envdev.setup_all_env(mirror="tsinghua", rust_version="nightly")
        assert calls == [
            "python:tsinghua",
            "js",
            "rust:tsinghua:nightly",
            "go",
            "java:tsinghua",
            "node",
            "linux-mirror",
            "qt",
            "fonts",
            "docker",
        ]
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_setup_bun_mirror(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """配置 Bun 镜像源（设置环境变量 + 写入 bunfig.toml）。"""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.delenv("BUN_CONFIG_REGISTRY", raising=False)

        fcmd.cli.dev.envdev._setup_bun_mirror()
        captured = capsys.readouterr()
        assert "Bun 镜像源已配置" in captured.out
        assert os.environ["BUN_CONFIG_REGISTRY"] == "https://registry.npmmirror.com"
        config_path = tmp_path / ".bunfig.toml"
        assert config_path.exists()
        assert "registry.npmmirror.com" in config_path.read_text(encoding="utf-8")

    def test_install_bun_already_installed(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """bun 已安装时跳过安装。"""
        monkeypatch.setattr("fcmd.cli.dev.envdev.shutil.which", lambda _: "/usr/bin/bun")
        fcmd.cli.dev.envdev._install_bun()
        captured = capsys.readouterr()
        assert "已安装" in captured.out

    def test_install_bun_windows(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """Windows 提示使用 PowerShell 安装。"""
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr("fcmd.cli.dev.envdev.shutil.which", lambda _: None)

        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.dev.envdev.run_command", _recording_run(calls))

        fcmd.cli.dev.envdev._install_bun()
        captured = capsys.readouterr()
        assert "PowerShell" in captured.out
        assert calls == []

    def test_install_bun_linux(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """Linux 先安装系统依赖再下载并安装 bun。"""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr("fcmd.cli.dev.envdev.shutil.which", lambda _: None)

        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.dev.envdev.run_command", _recording_run(calls))

        fcmd.cli.dev.envdev._install_bun()
        captured = capsys.readouterr()
        assert "Bun.js 安装完成" in captured.out
        # apt update + apt install + curl|bash
        assert any("apt" in c and "update" in c for c in calls)
        assert any("apt" in c and "install" in c for c in calls)
        assert any(c[0] == "bash" for c in calls)

    def test_setup_js_env_orchestration(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """setup_js_env 依次调用镜像源配置与安装。"""
        calls: list[str] = []
        monkeypatch.setattr("fcmd.cli.dev.envdev._setup_bun_mirror", lambda: calls.append("mirror"))
        monkeypatch.setattr("fcmd.cli.dev.envdev._install_bun", lambda: calls.append("install"))

        fcmd.cli.dev.envdev.setup_js_env()
        assert calls == ["mirror", "install"]

    def test_linux_mirror_non_linux(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """非 Linux 平台调用 setup_linux_system_mirror 打印提示。"""
        monkeypatch.setattr(sys, "platform", "win32")
        fcmd.cli.dev.envdev.setup_linux_system_mirror()
        captured = capsys.readouterr()
        assert "仅在 Linux" in captured.out

    def test_install_qt_libs_non_linux(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """非 Linux 平台调用 install_linux_qt_libs 打印提示。"""
        monkeypatch.setattr(sys, "platform", "darwin")
        fcmd.cli.dev.envdev.install_linux_qt_libs()
        captured = capsys.readouterr()
        assert "仅在 Linux" in captured.out

    def test_install_fonts_non_linux(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """非 Linux 平台调用 install_linux_fonts 打印提示。"""
        monkeypatch.setattr(sys, "platform", "win32")
        fcmd.cli.dev.envdev.install_linux_fonts()
        captured = capsys.readouterr()
        assert "仅在 Linux" in captured.out

    def test_install_docker_non_linux(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """非 Linux 平台调用 install_linux_docker 打印提示。"""
        monkeypatch.setattr(sys, "platform", "darwin")
        fcmd.cli.dev.envdev.install_linux_docker()
        captured = capsys.readouterr()
        assert "仅在 Linux" in captured.out

    def test_install_qt_libs_linux(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """Linux 平台调用 apt install 安装 Qt 依赖。"""
        monkeypatch.setattr(sys, "platform", "linux")
        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.dev.envdev.run_command", _recording_run(calls))

        fcmd.cli.dev.envdev.install_linux_qt_libs()
        captured = capsys.readouterr()
        assert "Qt 依赖库安装完成" in captured.out
        assert any("apt" in c and "install" in c for c in calls)

    def test_install_fonts_linux(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """Linux 平台调用 apt install 安装中文字体。"""
        monkeypatch.setattr(sys, "platform", "linux")
        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.dev.envdev.run_command", _recording_run(calls))

        fcmd.cli.dev.envdev.install_linux_fonts()
        captured = capsys.readouterr()
        assert "中文字体安装完成" in captured.out
        assert any("fonts-noto-cjk" in c for c in calls)

    def test_install_docker_linux(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """Linux 平台调用 apt install docker + usermod。"""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr("fcmd.cli.dev.envdev.getpass.getuser", lambda: "testuser")
        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.dev.envdev.run_command", _recording_run(calls))

        fcmd.cli.dev.envdev.install_linux_docker()
        captured = capsys.readouterr()
        assert "Docker 安装完成" in captured.out
        assert any("docker-compose-v2" in c for c in calls)
        assert any("usermod" in c for c in calls)

    def test_setup_python_mirror_linux(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Linux 平台配置 Python 镜像源写入 .pip/pip.conf。"""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.delenv("PIP_INDEX_URL", raising=False)

        fcmd.cli.dev.envdev.setup_python_mirror("tsinghua")
        captured = capsys.readouterr()
        assert "Python 镜像源已配置" in captured.out
        config_path = tmp_path / ".pip" / "pip.conf"
        assert config_path.exists()
        assert "tsinghua" in config_path.read_text(encoding="utf-8")

    def test_setup_linux_system_mirror_already_configured(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Linux 上已配置国内镜像时跳过。"""
        monkeypatch.setattr(sys, "platform", "linux")

        def fake_read_text(self: Path, *args: Any, **kwargs: Any) -> str:
            return "deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ focal main"

        monkeypatch.setattr(Path, "read_text", fake_read_text)

        fcmd.cli.dev.envdev.setup_linux_system_mirror()
        captured = capsys.readouterr()
        assert "已配置" in captured.out

    def test_setup_linux_system_mirror_not_configured(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Linux 上未配置国内镜像时下载并安装。"""
        monkeypatch.setattr(sys, "platform", "linux")

        def fake_read_text(self: Path, *args: Any, **kwargs: Any) -> str:
            raise OSError("file not found")

        monkeypatch.setattr(Path, "read_text", fake_read_text)

        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.dev.envdev.run_command", _recording_run(calls))

        fcmd.cli.dev.envdev.setup_linux_system_mirror()
        captured = capsys.readouterr()
        assert "下载" in captured.out
        assert "安装" in captured.out
        assert len(calls) == 2  # 下载 + 安装

    def test_setup_linux_system_mirror_no_mirror_in_content(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Linux 上 apt 文件无国内镜像时下载并安装（覆盖 any() 为 False 的分支）。"""
        monkeypatch.setattr(sys, "platform", "linux")

        def fake_read_text(self: Path, *args: Any, **kwargs: Any) -> str:
            return "deb http://archive.ubuntu.com/ubuntu/ focal main"

        monkeypatch.setattr(Path, "read_text", fake_read_text)

        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.dev.envdev.run_command", _recording_run(calls))

        fcmd.cli.dev.envdev.setup_linux_system_mirror()
        captured = capsys.readouterr()
        assert "下载" in captured.out
        assert len(calls) == 2  # 下载 + 安装


# ============================================================================ #
# 分组路由（lang / app）测试
# ============================================================================ #
class TestGroupRouters:
    """setup_lang_env / setup_app_env 分组路由分发测试。"""

    def test_lang_dispatch_all_languages(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """setup_lang_env 按 language 分发到对应语言一键命令并透传参数。"""
        calls: list[tuple[str, ...]] = []
        monkeypatch.setattr("fcmd.cli.dev.envdev.setup_python_env", lambda m: calls.append(("python", m)))
        monkeypatch.setattr("fcmd.cli.dev.envdev.setup_js_env", lambda: calls.append(("js",)))
        monkeypatch.setattr("fcmd.cli.dev.envdev.setup_rust_env", lambda m, v: calls.append(("rust", m, v)))
        monkeypatch.setattr("fcmd.cli.dev.envdev.setup_go_env", lambda m, g: calls.append(("go", m, g)))
        monkeypatch.setattr("fcmd.cli.dev.envdev.setup_java_env", lambda m, s: calls.append(("java", m, s)))
        monkeypatch.setattr("fcmd.cli.dev.envdev.setup_node_env", lambda n: calls.append(("node", n)))

        fcmd.cli.dev.envdev.setup_lang_env(language="python", mirror="tsinghua")
        fcmd.cli.dev.envdev.setup_lang_env(language="js")
        fcmd.cli.dev.envdev.setup_lang_env(language="rust", mirror="ustc", rust_version="nightly")
        fcmd.cli.dev.envdev.setup_lang_env(language="go", mirror="goproxy", install_gvm=True)
        fcmd.cli.dev.envdev.setup_lang_env(language="java", mirror="huaweicloud", install_sdkman=True)
        fcmd.cli.dev.envdev.setup_lang_env(language="node", install_nvm=True)

        assert calls == [
            ("python", "tsinghua"),
            ("js",),
            ("rust", "ustc", "nightly"),
            ("go", "goproxy", True),
            ("java", "huaweicloud", True),
            ("node", True),
        ]
        assert capsys.readouterr().out == ""

    def test_lang_auto_passthrough(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """mirror=auto 时原样透传，由各语言一键命令内部按服务探测选优。"""
        calls: list[tuple[str, ...]] = []
        monkeypatch.setattr("fcmd.cli.dev.envdev.setup_python_env", lambda m: calls.append(("python", m)))
        monkeypatch.setattr("fcmd.cli.dev.envdev.setup_rust_env", lambda m, v: calls.append(("rust", m, v)))
        monkeypatch.setattr("fcmd.cli.dev.envdev.setup_go_env", lambda m, g: calls.append(("go", m, g)))

        fcmd.cli.dev.envdev.setup_lang_env(language="python")
        fcmd.cli.dev.envdev.setup_lang_env(language="rust")
        fcmd.cli.dev.envdev.setup_lang_env(language="go")

        assert calls == [("python", "auto"), ("rust", "auto", "stable"), ("go", "auto", False)]
        assert capsys.readouterr().out == ""

    def test_app_dispatch_all_targets(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """setup_app_env 按 target 分发到对应应用/系统项命令。"""
        calls: list[str] = []
        monkeypatch.setattr("fcmd.cli.dev.envdev.setup_linux_system_mirror", lambda: calls.append("linux-mirror"))
        monkeypatch.setattr("fcmd.cli.dev.envdev.install_linux_qt_libs", lambda: calls.append("qt-libs"))
        monkeypatch.setattr("fcmd.cli.dev.envdev.install_linux_fonts", lambda: calls.append("fonts"))
        monkeypatch.setattr("fcmd.cli.dev.envdev.install_linux_docker", lambda: calls.append("docker"))
        monkeypatch.setattr("fcmd.cli.dev.envdev.setup_docker_mirror", lambda: calls.append("docker-mirror"))
        monkeypatch.setattr("fcmd.cli.dev.envdev.install_linux_openssh", lambda: calls.append("openssh"))
        monkeypatch.setattr("fcmd.cli.dev.envdev.setup_linux_remote", lambda: calls.append("remote"))

        for target in ("linux-mirror", "qt-libs", "fonts", "docker", "docker-mirror", "openssh", "remote"):
            fcmd.cli.dev.envdev.setup_app_env(target=target)  # type: ignore[arg-type]

        assert calls == ["linux-mirror", "qt-libs", "fonts", "docker", "docker-mirror", "openssh", "remote"]
        assert capsys.readouterr().out == ""


# ============================================================================ #
# 环境检测（check）测试
# ============================================================================ #
class TestCheckEnv:
    """check_env 环境检测测试。"""

    def test_check_all_ok(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """工具链齐全 + 镜像源环境变量全部设置时返回 0。"""
        monkeypatch.setattr("fcmd.cli.dev.envdev.shutil.which", lambda _: "/usr/bin/tool")
        for var, _, _ in fcmd.cli.dev.envdev._ENV_VAR_CHECKS:
            monkeypatch.setenv(var, "https://mirror.example.com")

        rc = fcmd.cli.dev.envdev.check_env()
        assert rc == 0
        assert "全部环境检测通过" in capsys.readouterr().out

    def test_check_missing_reports_failures(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """工具缺失 + 镜像源未设置时返回 1 并汇总未通过项。"""
        monkeypatch.setattr("fcmd.cli.dev.envdev.shutil.which", lambda _: None)
        for var, _, _ in fcmd.cli.dev.envdev._ENV_VAR_CHECKS:
            monkeypatch.delenv(var, raising=False)

        rc = fcmd.cli.dev.envdev.check_env()
        assert rc == 1
        out = capsys.readouterr().out
        assert "缺失" in out
        assert "未通过" in out
        assert "fcmd envdev all" in out


# ============================================================================ #
# OpenSSH + 远程桌面 测试
# ============================================================================ #
class TestOpensshAndRemote:
    """install_linux_openssh / setup_linux_remote 及 remote 细粒度步骤测试。"""

    # ---------- install_linux_openssh ---------- #
    def test_install_openssh_non_linux(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """非 Linux 平台调用 install_linux_openssh 打印提示。"""
        monkeypatch.setattr(sys, "platform", "darwin")
        fcmd.cli.dev.envdev.install_linux_openssh()
        captured = capsys.readouterr()
        assert "仅在 Linux" in captured.out

    def test_install_openssh_already_installed(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Linux 上 sshd 已安装时跳过 apt，仅 enable + now。"""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr("fcmd.cli.dev.envdev.shutil.which", lambda _: "/usr/sbin/sshd")

        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.dev.envdev.run_command", _recording_run(calls))

        fcmd.cli.dev.envdev.install_linux_openssh()
        captured = capsys.readouterr()
        assert "已安装" in captured.out
        assert len(calls) == 1
        assert calls[0] == ["sudo", "systemctl", "enable", "--now", "ssh"]

    def test_install_openssh_not_installed(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Linux 上 sshd 未安装时 apt update + install + enable。"""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr("fcmd.cli.dev.envdev.shutil.which", lambda _: None)

        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.dev.envdev.run_command", _recording_run(calls))

        fcmd.cli.dev.envdev.install_linux_openssh()
        captured = capsys.readouterr()
        assert "OpenSSH Server 已启用" in captured.out
        assert any("apt" in c and "update" in c for c in calls)
        assert any("openssh-server" in c for c in calls)
        assert calls[-1] == ["sudo", "systemctl", "enable", "--now", "ssh"]

    # ---------- _uninstall_gnome_remote_desktop ---------- #
    def test_uninstall_gnome_remote_non_linux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """非 Linux 平台静默跳过（无输出）。"""
        monkeypatch.setattr(sys, "platform", "win32")
        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.dev.envdev.run_command", _recording_run(calls))
        fcmd.cli.dev.envdev._uninstall_gnome_remote_desktop()
        assert calls == []

    def test_uninstall_gnome_remote_not_installed(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Linux 上 gnome-remote-desktop 未安装时跳过。"""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr("fcmd.cli.dev.envdev.shutil.which", lambda _: None)
        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.dev.envdev.run_command", _recording_run(calls))

        fcmd.cli.dev.envdev._uninstall_gnome_remote_desktop()
        captured = capsys.readouterr()
        assert "未安装" in captured.out
        assert calls == []

    def test_uninstall_gnome_remote_installed(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Linux 上 gnome-remote-desktop 已安装时 disable + purge + autoremove。"""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(
            "fcmd.cli.dev.envdev.shutil.which",
            lambda _: "/usr/libexec/gnome-remote-desktop-daemon",
        )
        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.dev.envdev.run_command", _recording_run(calls))

        fcmd.cli.dev.envdev._uninstall_gnome_remote_desktop()
        assert any("disable" in c and "gnome-remote-desktop" in c for c in calls)
        assert any("purge" in c and "gnome-remote-desktop" in c for c in calls)
        assert any("autoremove" in c for c in calls)

    # ---------- _install_xfce_desktop ---------- #
    def test_install_xfce_non_linux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """非 Linux 平台静默跳过。"""
        monkeypatch.setattr(sys, "platform", "darwin")
        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.dev.envdev.run_command", _recording_run(calls))
        fcmd.cli.dev.envdev._install_xfce_desktop()
        assert calls == []

    def test_install_xfce_linux(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Linux 上安装 Xfce + xorgxrdp，写入 ~/.xsession。"""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.dev.envdev.run_command", _recording_run(calls))

        fcmd.cli.dev.envdev._install_xfce_desktop()
        captured = capsys.readouterr()
        assert "已写入" in captured.out
        assert any("apt" in c and "update" in c for c in calls)
        assert any("xfce4" in c for c in calls)
        xsession = tmp_path / ".xsession"
        assert xsession.exists()
        assert xsession.read_text(encoding="utf-8") == "xfce4-session\n"

    # ---------- _install_xrdp ---------- #
    def test_install_xrdp_non_linux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """非 Linux 平台静默跳过。"""
        monkeypatch.setattr(sys, "platform", "win32")
        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.dev.envdev.run_command", _recording_run(calls))
        fcmd.cli.dev.envdev._install_xrdp()
        assert calls == []

    def test_install_xrdp_already_installed(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Linux 上 xrdp 已安装时跳过 apt，只 adduser + enable。"""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr("fcmd.cli.dev.envdev.shutil.which", lambda _: "/usr/sbin/xrdp")
        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.dev.envdev.run_command", _recording_run(calls))

        fcmd.cli.dev.envdev._install_xrdp()
        captured = capsys.readouterr()
        assert "已安装" in captured.out
        assert any(c[0] == "sudo" and c[1] == "adduser" for c in calls)
        assert any("enable" in c and "xrdp" in c for c in calls)

    def test_install_xrdp_not_installed(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Linux 上 xrdp 未安装时 apt install + adduser + enable。"""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr("fcmd.cli.dev.envdev.shutil.which", lambda _: None)
        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.dev.envdev.run_command", _recording_run(calls))

        fcmd.cli.dev.envdev._install_xrdp()
        captured = capsys.readouterr()
        assert "xrdp 已启用" in captured.out
        assert any("install" in c and "xrdp" in c for c in calls)

    # ---------- _configure_lightdm ---------- #
    def test_configure_lightdm_non_linux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """非 Linux 平台静默跳过。"""
        monkeypatch.setattr(sys, "platform", "darwin")
        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.dev.envdev.run_command", _recording_run(calls))
        fcmd.cli.dev.envdev._configure_lightdm()
        assert calls == []

    def test_configure_lightdm_linux(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """Linux 上 debconf + dpkg-reconfigure + restart lightdm。"""
        monkeypatch.setattr(sys, "platform", "linux")
        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.dev.envdev.run_command", _recording_run(calls))

        fcmd.cli.dev.envdev._configure_lightdm()
        captured = capsys.readouterr()
        assert "lightdm 已配置" in captured.out
        assert any("debconf-set-selections" in " ".join(c) for c in calls)
        assert any("restart" in c and "lightdm" in c for c in calls)

    # ---------- setup_linux_remote 编排 ---------- #
    def test_setup_remote_non_linux(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """非 Linux 平台调用 setup_linux_remote 打印提示。"""
        monkeypatch.setattr(sys, "platform", "win32")
        fcmd.cli.dev.envdev.setup_linux_remote()
        captured = capsys.readouterr()
        assert "仅在 Linux" in captured.out

    def test_setup_remote_orchestration(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Linux 上 setup_linux_remote 依次调用四个细粒度步骤。"""
        monkeypatch.setattr(sys, "platform", "linux")
        calls: list[str] = []
        monkeypatch.setattr(
            "fcmd.cli.dev.envdev._uninstall_gnome_remote_desktop",
            lambda: calls.append("uninstall"),
        )
        monkeypatch.setattr("fcmd.cli.dev.envdev._install_xfce_desktop", lambda: calls.append("xfce"))
        monkeypatch.setattr("fcmd.cli.dev.envdev._install_xrdp", lambda: calls.append("xrdp"))
        monkeypatch.setattr("fcmd.cli.dev.envdev._configure_lightdm", lambda: calls.append("lightdm"))

        fcmd.cli.dev.envdev.setup_linux_remote()
        assert calls == ["uninstall", "xfce", "xrdp", "lightdm"]
        captured = capsys.readouterr()
        assert "远程桌面配置完成" in captured.out


# ============================================================================ #
# 镜像站点探测（mirror）测试
# ============================================================================ #
def _fake_check_urls(
    probe: dict[str, tuple[bool, float]],
) -> Any:
    """根据 ``{url: (可达, 延迟)}`` 构造 check_urls 替身。

    返回与 check_urls 相同签名的函数：仅对请求中的 URL 生成结果，
    按可达优先 + 延迟升序排序。
    """

    def fake(urls: list[str], timeout: float = 5.0, workers: int = 8) -> list[tuple[str, bool, float]]:
        results = [(url, *probe[url]) for url in urls if url in probe]
        return sorted(results, key=lambda r: (not r[1], r[2]))

    return fake


class TestCheckCernetMirrors:
    """check_cernet_mirrors 教育网镜像站点探测测试。"""

    def test_probe_mixed(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """混合可达性输出按速度排序，返回 0。"""
        sites = fcmd.cli.dev.envdev._CERNET_MIRROR_SITES
        urls = list(sites.values())
        probe = {url: (True, 50.0 * (len(urls) - i)) for i, url in enumerate(urls)}
        probe[urls[3]] = (False, fcmd.dsl.actions.net.unreachable_latency())
        monkeypatch.setattr("fcmd.cli.dev.envdev.check_urls", _fake_check_urls(probe))

        rc = fcmd.cli.dev.envdev.check_cernet_mirrors(source="builtin")
        assert rc == 0
        out = capsys.readouterr().out
        assert "教育网镜像站点探测" in out
        assert "help.mirrors.cernet.edu.cn" in out
        assert "可达" in out

    def test_probe_all_unreachable(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """全部不可达时返回 1 并提示检查网络。"""
        probe = {
            url: (False, fcmd.dsl.actions.net.unreachable_latency())
            for url in fcmd.cli.dev.envdev._CERNET_MIRROR_SITES.values()
        }
        monkeypatch.setattr("fcmd.cli.dev.envdev.check_urls", _fake_check_urls(probe))

        rc = fcmd.cli.dev.envdev.check_cernet_mirrors(source="builtin")
        assert rc == 1
        assert "全部镜像站点不可达" in capsys.readouterr().out

    def test_probe_dynamic_source(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """source=auto 使用动态拉取的站点列表。"""
        dynamic_sites = {"tuna": "https://mirrors.tuna.example.edu.cn", "pku": "https://mirrors.pku.example.edu.cn"}
        monkeypatch.setattr("fcmd.cli.dev.envdev.fetch_mirrorz_sites", lambda timeout: dict(dynamic_sites))
        probe = dict.fromkeys(dynamic_sites.values(), (True, 10.0))
        monkeypatch.setattr("fcmd.cli.dev.envdev.check_urls", _fake_check_urls(probe))

        rc = fcmd.cli.dev.envdev.check_cernet_mirrors()
        assert rc == 0
        out = capsys.readouterr().out
        assert "动态拉取 MirrorZ：2 个站点" in out
        assert "动态列表" in out
        for url in dynamic_sites.values():
            assert url in out

    def test_probe_auto_fallback_to_builtin(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """动态拉取失败时回退内置列表探测。"""
        monkeypatch.setattr("fcmd.cli.dev.envdev.fetch_mirrorz_sites", lambda timeout: None)
        probe = dict.fromkeys(fcmd.cli.dev.envdev._CERNET_MIRROR_SITES.values(), (True, 10.0))
        monkeypatch.setattr("fcmd.cli.dev.envdev.check_urls", _fake_check_urls(probe))

        rc = fcmd.cli.dev.envdev.check_cernet_mirrors()
        assert rc == 0
        out = capsys.readouterr().out
        assert "动态拉取失败，回退内置列表" in out
        assert "内置列表" in out


class _FakeHTTPResponse:
    """最小 HTTP 响应替身（支持上下文管理器）。"""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> _FakeHTTPResponse:
        return self

    def __exit__(self, *args: object) -> bool:
        return False


class TestFetchMirrorzSites:
    """fetch_mirrorz_sites 动态拉取测试。"""

    def _patch_responses(self, monkeypatch: pytest.MonkeyPatch, payloads: list[bytes]) -> None:
        """按顺序回放假响应（对应入口页 -> JS 包的抓取序列）。

        urlopen 在 fetch_mirrorz_sites 内惰性导入（避免工具发现期加载
        ssl 链），patch 须指向定义处 ``urllib.request.urlopen``。
        """
        responses = [_FakeHTTPResponse(p) for p in payloads]
        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout: responses.pop(0))

    def test_parse_sites(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """解析内嵌站点元数据：去重、剔除门户、还原 JS 转义。"""
        html = b"<html><script type=module src=/mirrorz.abc.js></script></html>"
        js = (
            b'var a=JSON.parse(\'{"url":"https://mirrors.a.example.edu.cn","abbr":"EXA"}\');'
            b'var b=JSON.parse(\'{"url":"https://mirrors.b.example.edu.cn","abbr":"EXB"}\');'
            b'var c=JSON.parse(\'{"url":"https://mirrors.a.example.edu.cn","abbr":"EXA.NEO"}\');'
            b'var d=JSON.parse(\'{"url":"https://mirrors.cernet.edu.cn","abbr":"PORTAL"}\');'
            b'var e=JSON.parse(\'{"url":"https://mirrors.c.example.edu.cn","abbr":"EX\\\'C"}\');'
        )
        self._patch_responses(monkeypatch, [html, js])

        sites = fcmd.cli.dev.envdev.fetch_mirrorz_sites()
        assert sites == {
            "exa": "https://mirrors.a.example.edu.cn",
            "exb": "https://mirrors.b.example.edu.cn",
            "ex'c": "https://mirrors.c.example.edu.cn",
        }

    def test_html_without_js_reference(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """入口页无 JS 包引用时返回 None。"""
        self._patch_responses(monkeypatch, [b"<html><body>hello</body></html>"])
        assert fcmd.cli.dev.envdev.fetch_mirrorz_sites() is None

    def test_js_without_site_blobs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """JS 包无站点片段时返回 None。"""
        html = b'<html><script type=module src="/mirrorz.abc.js"></script></html>'
        self._patch_responses(monkeypatch, [html, b"var x=1;"])
        assert fcmd.cli.dev.envdev.fetch_mirrorz_sites() is None

    def test_network_error_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """网络错误返回 None（调用方回退内置列表）。"""

        def _boom(req: object, timeout: float) -> object:
            raise URLError("connection refused")

        monkeypatch.setattr("urllib.request.urlopen", _boom)
        assert fcmd.cli.dev.envdev.fetch_mirrorz_sites() is None


# ============================================================================ #
# 镜像自动选优（按服务，lang mirror=auto）测试
# ============================================================================ #
class TestAutoMirror:
    """镜像按服务自动选优测试（pip/conda/rustup/cargo 等服务地址不同，独立探测）。"""

    def test_auto_selects_fastest(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        _fake_persist_env: dict[str, str],
    ) -> None:
        """默认 auto 时 pip 与 conda 各自探测自身服务地址并独立选用最快可达镜像。"""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pip_urls = fcmd.cli.dev.envdev._PIP_INDEX_URLS
        conda_urls = fcmd.cli.dev.envdev._CONDA_PROBE_URLS
        probe = {
            url: (False, fcmd.dsl.actions.net.unreachable_latency()) for url in {**pip_urls, **conda_urls}.values()
        }
        # pip 服务：tsinghua 最快可达；conda 服务：ustc 最快可达（验证独立选优）
        probe[pip_urls["tsinghua"]] = (True, 20.0)
        probe[pip_urls["aliyun"]] = (True, 80.0)
        probe[conda_urls["ustc"]] = (True, 30.0)
        probe[conda_urls["aliyun"]] = (True, 90.0)
        monkeypatch.setattr("fcmd.dsl.actions.net.check_urls", _fake_check_urls(probe))

        fcmd.cli.dev.envdev.setup_lang_env(language="python")
        out = capsys.readouterr().out
        assert "[pip 镜像自动选优]" in out
        assert "[conda 镜像自动选优]" in out
        assert "已选用 pip 镜像: tsinghua" in out
        assert "已选用 conda 镜像: ustc" in out
        assert _fake_persist_env["PIP_INDEX_URL"] == pip_urls["tsinghua"]
        condarc = tmp_path / ".condarc"
        assert condarc.exists()
        assert "ustc" in condarc.read_text(encoding="utf-8")

    def test_auto_fallback_when_all_unreachable(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        _fake_persist_env: dict[str, str],
    ) -> None:
        """全部镜像不可达时各服务分别回退各自默认镜像。"""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        probe = {
            url: (False, fcmd.dsl.actions.net.unreachable_latency())
            for url in {**fcmd.cli.dev.envdev._PIP_INDEX_URLS, **fcmd.cli.dev.envdev._CONDA_PROBE_URLS}.values()
        }
        monkeypatch.setattr("fcmd.dsl.actions.net.check_urls", _fake_check_urls(probe))

        fcmd.cli.dev.envdev.setup_lang_env(language="python")
        out = capsys.readouterr().out
        assert "已选用 pip 镜像: aliyun" in out
        assert "已选用 conda 镜像: aliyun" in out
        assert _fake_persist_env["PIP_INDEX_URL"] == "https://mirrors.aliyun.com/pypi/simple/"

    def test_rust_auto_independent_per_service(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        _fake_persist_env: dict[str, str],
    ) -> None:
        """rust auto 时 rustup 与 cargo 分别探测各自服务地址并独立选优。"""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr("fcmd.cli.dev.envdev._RUST_SCCACHE_DIR", tmp_path / ".cargo" / "sccache")
        monkeypatch.setattr("fcmd.cli.dev.envdev._download_rustup", lambda: None)
        monkeypatch.setattr("fcmd.cli.dev.envdev._install_rust_toolchain", lambda v: None)

        rustup_urls = fcmd.cli.dev.envdev._RUSTUP_PROBE_URLS
        cargo_urls = fcmd.cli.dev.envdev._CARGO_PROBE_URLS
        probe = {
            url: (False, fcmd.dsl.actions.net.unreachable_latency()) for url in {**rustup_urls, **cargo_urls}.values()
        }
        # rustup 服务：tsinghua 最快；cargo 服务：aliyun 最快（选优结果不同）
        probe[rustup_urls["tsinghua"]] = (True, 20.0)
        probe[rustup_urls["aliyun"]] = (True, 50.0)
        probe[cargo_urls["aliyun"]] = (True, 30.0)
        probe[cargo_urls["tsinghua"]] = (True, 60.0)
        monkeypatch.setattr("fcmd.dsl.actions.net.check_urls", _fake_check_urls(probe))

        fcmd.cli.dev.envdev.setup_lang_env(language="rust")
        out = capsys.readouterr().out
        assert "已选用 rustup 镜像: tsinghua" in out
        assert "已选用 cargo 镜像: aliyun" in out
        assert os.environ["RUSTUP_DIST_SERVER"] == rustup_urls["tsinghua"]
        config = (tmp_path / ".cargo" / "config.toml").read_text(encoding="utf-8")
        assert "replace-with = 'aliyun'" in config

    def test_go_auto_selects_fastest(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """go auto 时探测 GOPROXY 服务地址并选用最快可达镜像。"""
        monkeypatch.setattr("fcmd.cli.dev.envdev_go.persist_env", lambda n, v: os.environ.update({n: v}))
        go_urls = fcmd.cli.dev.envdev_go._GO_PROXY_PROBE_URLS
        probe = {url: (False, fcmd.dsl.actions.net.unreachable_latency()) for url in go_urls.values()}
        probe[go_urls["ustc"]] = (True, 25.0)
        monkeypatch.setattr("fcmd.dsl.actions.net.check_urls", _fake_check_urls(probe))

        fcmd.cli.dev.envdev.setup_go_env()
        out = capsys.readouterr().out
        assert "已选用 go 镜像: ustc" in out
        assert os.environ["GOPROXY"] == "https://mirrors.ustc.edu.cn/goproxy/,direct"

    def test_explicit_mirror_skips_probe(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        _fake_persist_env: dict[str, str],
    ) -> None:
        """显式指定镜像时不触发探测。"""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        called: list[list[str]] = []

        def fake(urls: list[str], timeout: float = 5.0, workers: int = 8) -> list[tuple[str, bool, float]]:
            called.append(list(urls))
            return []

        monkeypatch.setattr("fcmd.dsl.actions.net.check_urls", fake)

        fcmd.cli.dev.envdev.setup_lang_env(language="python", mirror="ustc")
        assert called == []
        assert "自动选优" not in capsys.readouterr().out
        assert _fake_persist_env["PIP_INDEX_URL"] == "https://pypi.mirrors.ustc.edu.cn/simple/"

    def test_js_skips_probe(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """js 镜像固定 npmmirror，auto 时不触发探测。"""
        called: list[list[str]] = []

        def fake(urls: list[str], timeout: float = 5.0, workers: int = 8) -> list[tuple[str, bool, float]]:
            called.append(list(urls))
            return []

        monkeypatch.setattr("fcmd.dsl.actions.net.check_urls", fake)
        monkeypatch.setattr("fcmd.cli.dev.envdev._setup_bun_mirror", lambda: None)
        monkeypatch.setattr("fcmd.cli.dev.envdev._install_bun", lambda: None)

        fcmd.cli.dev.envdev.setup_lang_env(language="js")
        assert called == []


class TestResolveMirror:
    """resolve_mirror 解析逻辑单元测试。"""

    def test_explicit_passthrough(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """显式镜像名原样返回，不触发探测。"""
        called: list[list[str]] = []

        def fake(urls: list[str], timeout: float = 5.0, workers: int = 8) -> list[tuple[str, bool, float]]:
            called.append(list(urls))
            return []

        monkeypatch.setattr("fcmd.dsl.actions.net.check_urls", fake)

        result = fcmd.cli.dev.envdev_core.resolve_mirror("pip", "ustc", {"a": "https://a"}, "aliyun")
        assert result == "ustc"
        assert called == []
        assert capsys.readouterr().out == ""

    def test_auto_fallback_when_empty_results(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """探测结果为空（全部不可达）时回退默认镜像。"""
        monkeypatch.setattr("fcmd.dsl.actions.net.check_urls", lambda urls, timeout=5.0, workers=8: [])

        result = fcmd.cli.dev.envdev_core.resolve_mirror("conda", "auto", {"ustc": "https://u"}, "aliyun")
        assert result == "aliyun"
        out = capsys.readouterr().out
        assert "[conda 镜像自动选优]" in out
        assert "已选用 conda 镜像: aliyun" in out


# ============================================================================ #
# Docker 镜像加速预探测测试
# ============================================================================ #
class TestDockerMirrorProbe:
    """setup_docker_mirror 加速源预探测测试。"""

    def test_writes_reachable_sorted(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """仅写入可达加速源并按速度排序。"""
        monkeypatch.setattr(sys, "platform", "linux")
        daemon_path = tmp_path / "etc" / "docker" / "daemon.json"
        monkeypatch.setattr("fcmd.cli.dev.envdev._DOCKER_DAEMON_PATH", daemon_path)

        candidates = fcmd.cli.dev.envdev._DOCKER_REGISTRY_MIRRORS
        probe = {candidates[0]: (True, 90.0), candidates[1]: (True, 30.0), candidates[2]: (False, 999.0)}
        monkeypatch.setattr("fcmd.cli.dev.envdev.check_urls", _fake_check_urls(probe))

        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.dev.envdev.run_command", _recording_run(calls))

        fcmd.cli.dev.envdev.setup_docker_mirror()
        out = capsys.readouterr().out
        assert "探测结果" in out
        assert daemon_path.exists()
        content = daemon_path.read_text(encoding="utf-8")
        assert content.index(candidates[1]) < content.index(candidates[0])
        assert candidates[2] not in content
        assert any("systemctl" in " ".join(c) for c in calls)

    def test_all_unreachable_keeps_full_list(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """全部不可达时保留完整候选列表。"""
        monkeypatch.setattr(sys, "platform", "linux")
        daemon_path = tmp_path / "etc" / "docker" / "daemon.json"
        monkeypatch.setattr("fcmd.cli.dev.envdev._DOCKER_DAEMON_PATH", daemon_path)

        probe = dict.fromkeys(fcmd.cli.dev.envdev._DOCKER_REGISTRY_MIRRORS, (False, 999.0))
        monkeypatch.setattr("fcmd.cli.dev.envdev.check_urls", _fake_check_urls(probe))
        monkeypatch.setattr("fcmd.cli.dev.envdev.run_command", _recording_run([]))

        fcmd.cli.dev.envdev.setup_docker_mirror()
        out = capsys.readouterr().out
        assert "全部不可达" in out
        content = daemon_path.read_text(encoding="utf-8")
        for url in fcmd.cli.dev.envdev._DOCKER_REGISTRY_MIRRORS:
            assert url in content
