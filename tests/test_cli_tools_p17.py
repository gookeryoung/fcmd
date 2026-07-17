"""P17 新工具测试：setenv / reseticoncache / sshcopyid / screenshot / envdev / gittool isub。

验证 ``fcmd.cli`` 包下 5 个新工具与 1 个新子命令：
- ``setenv``：设置环境变量（单命令，default 模式）
- ``reseticoncache``：重置 Windows 图标缓存（单命令，平台守卫）
- ``sshcopyid``：部署 SSH 公钥（单命令，公钥读取 + sshpass 调用）
- ``screenshot``：跨平台截图（full/area 子命令，路径生成 + 平台派发）
- ``envdev``：开发环境镜像源配置（setup-python/conda/rust + Linux 专用）
- ``gittool isub``：初始化子目录 Git 仓库
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import fcmd as fx
import fcmd.cli.envdev
import fcmd.cli.gittool
import fcmd.cli.reseticoncache
import fcmd.cli.screenshot
import fcmd.cli.setenv
import fcmd.cli.sshcopyid
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.models import CommandResult


# ============================================================================ #
# 测试辅助
# ============================================================================ #
def _success_run(cmd: list[str], *, capture: bool = False, check: bool = False) -> CommandResult:
    """总是返回成功结果的 fake ``run_command`` 函数。"""
    return CommandResult(cmd=list(cmd), returncode=0, stdout="", stderr="")


def _fail_run(cmd: list[str], *, capture: bool = False, check: bool = False) -> CommandResult:
    """总是返回失败结果的 fake ``run_command`` 函数。"""
    return CommandResult(cmd=list(cmd), returncode=1, stdout="", stderr="error")


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
    """5 个新工具 + 1 个新子命令的注册验证。"""

    def test_all_tools_registered(self) -> None:
        """5 个新工具应在 _TOOL_REGISTRY 中注册。"""
        for name in ("setenv", "reseticoncache", "sshcopyid", "screenshot", "envdev"):
            assert name in _TOOL_REGISTRY, f"工具 {name!r} 未注册"

    def test_setenv_single_command(self) -> None:
        """setenv 是单命令工具。"""
        assert fx.list_subcommands("setenv") == []

    def test_reseticoncache_single_command(self) -> None:
        """reseticoncache 是单命令工具。"""
        assert fx.list_subcommands("reseticoncache") == []

    def test_sshcopyid_single_command(self) -> None:
        """sshcopyid 是单命令工具。"""
        assert fx.list_subcommands("sshcopyid") == []

    def test_screenshot_subcommands(self) -> None:
        """screenshot 应有 full/area 子命令。"""
        subs = fx.list_subcommands("screenshot")
        assert "full" in subs
        assert "area" in subs

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

    def test_gittool_isub_registered(self) -> None:
        """gittool 应有 isub 子命令。"""
        subs = fx.list_subcommands("gittool")
        assert "isub" in subs


# ============================================================================ #
# setenv 测试
# ============================================================================ #
class TestSetenv:
    """setenv 工具测试。"""

    def test_setenv_overwrite(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """default=False 时覆盖已有值。"""
        monkeypatch.setenv("FCMD_TEST_SETENV", "old")
        fcmd.cli.setenv.setenv_run("FCMD_TEST_SETENV", "new")
        assert os.environ["FCMD_TEST_SETENV"] == "new"

    def test_setenv_default_skip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """default=True 时不覆盖已有值。"""
        monkeypatch.setenv("FCMD_TEST_SETENV", "old")
        fcmd.cli.setenv.setenv_run("FCMD_TEST_SETENV", "new", default=True)
        assert os.environ["FCMD_TEST_SETENV"] == "old"

    def test_setenv_default_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """default=True 且变量未设置时设置值。"""
        monkeypatch.delenv("FCMD_TEST_SETENV", raising=False)
        fcmd.cli.setenv.setenv_run("FCMD_TEST_SETENV", "new", default=True)
        assert os.environ["FCMD_TEST_SETENV"] == "new"

    def test_setenv_via_run_tool(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """通过 run_tool 调用 setenv。"""
        monkeypatch.delenv("FCMD_TEST_SETENV_CLI", raising=False)
        run_tool("setenv", ["FCMD_TEST_SETENV_CLI", "cli_val"])
        assert os.environ["FCMD_TEST_SETENV_CLI"] == "cli_val"


# ============================================================================ #
# reseticoncache 测试
# ============================================================================ #
class TestResetIconCache:
    """reseticoncache 工具测试。"""

    def test_non_windows_skip(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """非 Windows 平台打印提示并跳过。"""
        monkeypatch.setattr(sys, "platform", "linux")
        fcmd.cli.reseticoncache.reset_icon_cache_run()
        captured = capsys.readouterr()
        assert "仅在 Windows" in captured.out

    def test_windows_no_localappdata(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """Windows 但 LOCALAPPDATA 未设置时提示并跳过。"""
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        fcmd.cli.reseticoncache.reset_icon_cache_run()
        captured = capsys.readouterr()
        assert "LOCALAPPDATA" in captured.out

    def test_windows_calls_commands(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Windows 下调用 taskkill/del/start 命令序列。"""
        monkeypatch.setattr(sys, "platform", "win32")
        local_appdata = tmp_path / "AppData" / "Local"
        local_appdata.mkdir(parents=True)
        (local_appdata / "IconCache.db").write_text("fake")
        explorer_dir = local_appdata / "Microsoft" / "Windows" / "Explorer"
        explorer_dir.mkdir(parents=True)
        monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))

        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.reseticoncache.run_command", _recording_run(calls))

        fcmd.cli.reseticoncache.reset_icon_cache_run()
        captured = capsys.readouterr()
        assert "图标缓存已重置" in captured.out
        # 应调用 taskkill、del（IconCache.db）、del（iconcache*）、start explorer
        assert any("taskkill" in c for c in calls)
        assert any("start" in c and "explorer.exe" in c for c in calls)


# ============================================================================ #
# sshcopyid 测试
# ============================================================================ #
class TestSshCopyId:
    """sshcopyid 工具测试。"""

    def test_pubkey_not_exists(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """公钥文件不存在时打印提示。"""
        fcmd.cli.sshcopyid.ssh_copy_id(
            hostname="host", username="user", password="pass", keypath="/nonexistent/key.pub"
        )
        captured = capsys.readouterr()
        assert "公钥文件不存在" in captured.out

    def test_success_deploy(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """成功部署公钥。"""
        key_file = tmp_path / "id_rsa.pub"
        key_file.write_text("ssh-rsa AAAAB3NzaC1yc2E test@example.com\n")

        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.sshcopyid.run_command", _recording_run(calls))

        fcmd.cli.sshcopyid.ssh_copy_id(hostname="host", username="user", password="pass", keypath=str(key_file))
        captured = capsys.readouterr()
        assert "已部署" in captured.out
        assert len(calls) == 1
        assert "sshpass" in calls[0][0]

    def test_failed_deploy(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """部署失败时提示手动执行。"""
        key_file = tmp_path / "id_rsa.pub"
        key_file.write_text("ssh-rsa AAAAB3NzaC1yc2E test@example.com\n")
        monkeypatch.setattr("fcmd.cli.sshcopyid.run_command", _fail_run)

        fcmd.cli.sshcopyid.ssh_copy_id(hostname="host", username="user", password="pass", keypath=str(key_file))
        captured = capsys.readouterr()
        assert "手动执行" in captured.out


# ============================================================================ #
# screenshot 测试
# ============================================================================ #
class TestScreenshot:
    """screenshot 工具测试。"""

    def test_get_screenshot_path_auto(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """自动生成带时间戳的文件名。"""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        path = fcmd.cli.screenshot.get_screenshot_path(None)
        assert path.parent == tmp_path / "Pictures" / "screenshots"
        assert path.name.startswith("screenshot_")
        assert path.suffix == ".png"

    def test_get_screenshot_path_named(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """指定文件名时使用指定名称。"""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        path = fcmd.cli.screenshot.get_screenshot_path("custom.png")
        assert path.name == "custom.png"

    def test_full_screenshot_windows(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Windows 全屏截图调用 PowerShell。"""
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.screenshot.run_command", _recording_run(calls))

        fcmd.cli.screenshot.take_screenshot_full()
        captured = capsys.readouterr()
        assert "截图已保存" in captured.out
        assert any("powershell" in c[0] for c in calls)

    def test_area_screenshot_macos(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """macOS 区域截图调用 screencapture -i。"""
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.screenshot.run_command", _recording_run(calls))

        fcmd.cli.screenshot.take_screenshot_area()
        captured = capsys.readouterr()
        assert "截图已保存" in captured.out
        assert any("screencapture" in c and "-i" in c for c in calls)

    def test_full_screenshot_linux_gnome(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Linux 全屏截图优先 gnome-screenshot。"""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.screenshot.run_command", _recording_run(calls))

        fcmd.cli.screenshot.take_screenshot_full()
        # gnome-screenshot 成功时不调用 scrot
        assert any("gnome-screenshot" in c for c in calls)
        assert not any("scrot" in c for c in calls)

    def test_full_screenshot_linux_scrot_fallback(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Linux gnome-screenshot 失败时回退 scrot。"""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        call_count = {"n": 0}

        def fake_run(cmd: list[str], *, capture: bool = False, check: bool = False) -> CommandResult:
            call_count["n"] += 1
            # 第一次调用 gnome-screenshot 失败，第二次 scrot 成功
            if call_count["n"] == 1:
                return CommandResult(cmd=list(cmd), returncode=1, stdout="", stderr="")
            return CommandResult(cmd=list(cmd), returncode=0, stdout="", stderr="")

        monkeypatch.setattr("fcmd.cli.screenshot.run_command", fake_run)

        fcmd.cli.screenshot.take_screenshot_full()
        assert call_count["n"] == 2  # gnome-screenshot + scrot

    def test_full_screenshot_macos(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """macOS 全屏截图调用 screencapture -x。"""
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.screenshot.run_command", _recording_run(calls))

        fcmd.cli.screenshot.take_screenshot_full()
        captured = capsys.readouterr()
        assert "截图已保存" in captured.out
        assert any("screencapture" in c and "-x" in c for c in calls)

    def test_area_screenshot_windows(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Windows 区域截图退化为全屏（PowerShell）。"""
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.screenshot.run_command", _recording_run(calls))

        fcmd.cli.screenshot.take_screenshot_area()
        captured = capsys.readouterr()
        assert "截图已保存" in captured.out
        assert any("powershell" in c[0] for c in calls)

    def test_area_screenshot_linux(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Linux 区域截图调用 gnome-screenshot -a。"""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.screenshot.run_command", _recording_run(calls))

        fcmd.cli.screenshot.take_screenshot_area()
        captured = capsys.readouterr()
        assert "截图已保存" in captured.out
        assert any("gnome-screenshot" in c and "-a" in c for c in calls)


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


# ============================================================================ #
# gittool isub 测试
# ============================================================================ #
class TestGittoolIsub:
    """gittool isub 子命令测试。"""

    def test_isub_no_subdirs(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """无子目录时打印提示。"""
        monkeypatch.chdir(tmp_path)
        fcmd.cli.gittool.git_init_sub_dirs()
        captured = capsys.readouterr()
        assert "无子目录" in captured.out

    def test_isub_with_subdirs(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """有子目录时对每个子目录调用 git init/add/commit。"""
        (tmp_path / "proj_a").mkdir()
        (tmp_path / "proj_b").mkdir()
        (tmp_path / "file.txt").write_text("not a dir")
        monkeypatch.chdir(tmp_path)

        calls: list[tuple[list[str], Path]] = []

        def fake_run(
            cmd: list[str],
            *,
            capture_output: bool = False,
            check: bool = False,
            text: bool = False,
            cwd: Path | None = None,
        ) -> subprocess.CompletedProcess[str]:
            calls.append((cmd, cwd or Path.cwd()))
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr("fcmd.cli.gittool.subprocess.run", fake_run)

        fcmd.cli.gittool.git_init_sub_dirs()
        captured = capsys.readouterr()
        assert "已初始化: proj_a" in captured.out
        assert "已初始化: proj_b" in captured.out
        # 每个子目录 3 次 git 命令，共 6 次
        assert len(calls) == 6
        # 验证 cwd 正确设置
        proj_a_cwd = tmp_path / "proj_a"
        proj_a_calls = [c for c in calls if c[1] == proj_a_cwd]
        assert len(proj_a_calls) == 3  # init + add + commit
