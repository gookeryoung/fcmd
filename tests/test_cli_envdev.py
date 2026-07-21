"""envdev 工具测试。

验证 ``fcmd.cli.envdev`` 模块：
- 工具注册
- setup_python_mirror / setup_conda_mirror / setup_rust_mirror 镜像源配置
- download_rustup_script / install_rust_toolchain Rust 工具链
- setup_linux_system_mirror / install_linux_qt_libs / install_linux_fonts / install_linux_docker Linux 专用
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

import fcmd as fx
import fcmd.cli.envdev
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


# ============================================================================ #
# 注册验证
# ============================================================================ #
class TestToolsRegistration:
    """envdev 工具注册验证。"""

    def test_all_tools_registered(self) -> None:
        """envdev 应在 _TOOL_REGISTRY 中注册。"""
        for name in ("envdev",):
            assert name in _TOOL_REGISTRY, f"工具 {name!r} 未注册"

    def test_envdev_subcommands(self) -> None:
        """envdev 应有 9 个子命令。"""
        subs = fx.list_subcommands("envdev")
        for name in (
            "setup-python",
            "setup-conda",
            "setup-rust",
            "download-rustup",
            "install-rust",
            "setup-linux-mirror",
            "install-qt-libs",
            "install-fonts",
            "install-docker",
        ):
            assert name in subs, f"子命令 {name!r} 未注册"


# ============================================================================ #
# envdev 测试
# ============================================================================ #
class TestEnvdev:
    """envdev 工具测试。"""

    def test_setup_python_mirror(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """配置 Python 镜像源（设置环境变量 + 写入 pip 配置文件）。"""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.delenv("PIP_INDEX_URL", raising=False)
        monkeypatch.delenv("UV_INDEX_URL", raising=False)

        fcmd.cli.envdev.setup_python_mirror("aliyun")
        captured = capsys.readouterr()
        assert "Python 镜像源已配置" in captured.out
        assert os.environ["PIP_INDEX_URL"] == "https://mirrors.aliyun.com/pypi/simple/"
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
        fcmd.cli.envdev.setup_python_mirror("unknown")
        captured = capsys.readouterr()
        assert "未知" in captured.out

    def test_setup_conda_mirror(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """配置 Conda 镜像源（写入 ~/.condarc）。"""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        fcmd.cli.envdev.setup_conda_mirror("ustc")
        captured = capsys.readouterr()
        assert "Conda 镜像源已配置" in captured.out
        condarc = tmp_path / ".condarc"
        assert condarc.exists()
        assert "ustc" in condarc.read_text(encoding="utf-8")

    def test_setup_conda_unknown_mirror(self, capsys: pytest.CaptureFixture[str]) -> None:
        """未知 Conda 镜像源打印提示。"""
        fcmd.cli.envdev.setup_conda_mirror("unknown")
        captured = capsys.readouterr()
        assert "未知" in captured.out

    def test_setup_rust_mirror(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """配置 Rust 镜像源（设置环境变量 + 写入 cargo config）。"""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # _RUST_SCCACHE_DIR 是模块级常量，导入时已求值，需单独 mock
        monkeypatch.setattr("fcmd.cli.envdev._RUST_SCCACHE_DIR", tmp_path / ".cargo" / "sccache")
        monkeypatch.delenv("RUSTUP_DIST_SERVER", raising=False)
        fcmd.cli.envdev.setup_rust_mirror("tsinghua")
        captured = capsys.readouterr()
        assert "Rust 镜像源已配置" in captured.out
        assert os.environ["RUSTUP_DIST_SERVER"] == "https://mirrors.tuna.tsinghua.edu.cn/rustup"
        config_path = tmp_path / ".cargo" / "config.toml"
        assert config_path.exists()
        assert "tsinghua" in config_path.read_text(encoding="utf-8")
        # sccache 目录已创建
        assert (tmp_path / ".cargo" / "sccache").is_dir()

    def test_setup_rust_unknown_mirror(self, capsys: pytest.CaptureFixture[str]) -> None:
        """未知 Rust 镜像源打印提示。"""
        fcmd.cli.envdev.setup_rust_mirror("unknown")
        captured = capsys.readouterr()
        assert "未知" in captured.out

    def test_download_rustup_already_installed(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """rustup 已安装时跳过下载。"""
        monkeypatch.setattr("fcmd.cli.envdev.shutil.which", lambda _: "/usr/bin/rustup")
        fcmd.cli.envdev.download_rustup_script()
        captured = capsys.readouterr()
        assert "已安装" in captured.out

    def test_download_rustup_windows(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Windows 下载 rustup-init.exe。"""
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr("fcmd.cli.envdev.shutil.which", lambda _: None)

        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.envdev.run_command", _recording_run(calls))

        fcmd.cli.envdev.download_rustup_script()
        captured = capsys.readouterr()
        assert "rustup-init.exe" in captured.out
        assert any("powershell" in c[0] for c in calls)

    def test_download_rustup_linux(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """Linux 下载 rustup-init.sh。"""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr("fcmd.cli.envdev.shutil.which", lambda _: None)

        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.envdev.run_command", _recording_run(calls))

        fcmd.cli.envdev.download_rustup_script()
        captured = capsys.readouterr()
        assert "rustup-init.sh" in captured.out
        assert any("curl" in c[0] for c in calls)

    def test_install_rust_no_rustup(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """rustup 未安装时跳过工具链安装。"""
        monkeypatch.setattr("fcmd.cli.envdev.shutil.which", lambda _: None)
        fcmd.cli.envdev.install_rust_toolchain("stable")
        captured = capsys.readouterr()
        assert "未安装" in captured.out

    def test_install_rust_success(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """rustup 已安装时调用 rustup toolchain install。"""
        monkeypatch.setattr("fcmd.cli.envdev.shutil.which", lambda _: "/usr/bin/rustup")

        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.envdev.run_command", _recording_run(calls))

        fcmd.cli.envdev.install_rust_toolchain("nightly")
        captured = capsys.readouterr()
        assert "nightly 安装完成" in captured.out
        assert calls == [["rustup", "toolchain", "install", "nightly"]]

    def test_linux_mirror_non_linux(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """非 Linux 平台调用 setup_linux_system_mirror 打印提示。"""
        monkeypatch.setattr(sys, "platform", "win32")
        fcmd.cli.envdev.setup_linux_system_mirror()
        captured = capsys.readouterr()
        assert "仅在 Linux" in captured.out

    def test_install_qt_libs_non_linux(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """非 Linux 平台调用 install_linux_qt_libs 打印提示。"""
        monkeypatch.setattr(sys, "platform", "darwin")
        fcmd.cli.envdev.install_linux_qt_libs()
        captured = capsys.readouterr()
        assert "仅在 Linux" in captured.out

    def test_install_fonts_non_linux(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """非 Linux 平台调用 install_linux_fonts 打印提示。"""
        monkeypatch.setattr(sys, "platform", "win32")
        fcmd.cli.envdev.install_linux_fonts()
        captured = capsys.readouterr()
        assert "仅在 Linux" in captured.out

    def test_install_docker_non_linux(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """非 Linux 平台调用 install_linux_docker 打印提示。"""
        monkeypatch.setattr(sys, "platform", "darwin")
        fcmd.cli.envdev.install_linux_docker()
        captured = capsys.readouterr()
        assert "仅在 Linux" in captured.out

    def test_install_qt_libs_linux(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """Linux 平台调用 apt install 安装 Qt 依赖。"""
        monkeypatch.setattr(sys, "platform", "linux")
        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.envdev.run_command", _recording_run(calls))

        fcmd.cli.envdev.install_linux_qt_libs()
        captured = capsys.readouterr()
        assert "Qt 依赖库安装完成" in captured.out
        assert any("apt" in c and "install" in c for c in calls)

    def test_install_fonts_linux(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """Linux 平台调用 apt install 安装中文字体。"""
        monkeypatch.setattr(sys, "platform", "linux")
        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.envdev.run_command", _recording_run(calls))

        fcmd.cli.envdev.install_linux_fonts()
        captured = capsys.readouterr()
        assert "中文字体安装完成" in captured.out
        assert any("fonts-noto-cjk" in c for c in calls)

    def test_install_docker_linux(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """Linux 平台调用 apt install docker + usermod。"""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr("fcmd.cli.envdev.getpass.getuser", lambda: "testuser")
        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.envdev.run_command", _recording_run(calls))

        fcmd.cli.envdev.install_linux_docker()
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

        fcmd.cli.envdev.setup_python_mirror("tsinghua")
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

        fcmd.cli.envdev.setup_linux_system_mirror()
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
        monkeypatch.setattr("fcmd.cli.envdev.run_command", _recording_run(calls))

        fcmd.cli.envdev.setup_linux_system_mirror()
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
        monkeypatch.setattr("fcmd.cli.envdev.run_command", _recording_run(calls))

        fcmd.cli.envdev.setup_linux_system_mirror()
        captured = capsys.readouterr()
        assert "下载" in captured.out
        assert len(calls) == 2  # 下载 + 安装
