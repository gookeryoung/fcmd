"""P9 新工具测试：piptool / taskkill / folderback。

验证 ``fcmd.cli`` 包下 3 个参考 pyflowx 实现的工具：
- ``piptool``：pip 包管理（i/u/r/d/up/f 子命令）
- ``taskkill``：进程终止（单命令，跨平台）
- ``folderback``：文件夹备份（单命令）
"""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest

import fcmd as fx
import fcmd.cli.folderback
import fcmd.cli.piptool
import fcmd.cli.taskkill
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli.folderback import backup_folder, remove_old_backups, zip_target
from fcmd.cli.piptool import (
    _expand_wildcard_packages,
    _filter_protected_packages,
    _get_installed_packages,
    pip_download,
    pip_freeze,
    pip_install,
    pip_reinstall,
    pip_uninstall,
    pip_upgrade,
)
from fcmd.cli.taskkill import kill_process, taskkill_run
from fcmd.models import CommandResult


# ============================================================================ #
# 测试辅助：创建 fake run_command 函数（避免 lambda ARG005）
# ============================================================================ #
def _fake_run(result: CommandResult) -> Any:
    """创建总是返回 ``result`` 的 fake ``run_command`` 函数。"""

    def run(cmd: list[str], *, capture: bool = False, check: bool = False) -> CommandResult:
        return result

    return run


def _recording_run(calls: list[list[str]]) -> Any:
    """创建记录调用的 fake ``run_command`` 函数，返回成功结果。"""

    def run(cmd: list[str], *, capture: bool = False, check: bool = False) -> CommandResult:
        calls.append(cmd)
        return CommandResult(cmd=list(cmd), returncode=0, stdout="", stderr="")

    return run


def _success_run(cmd: list[str], *, capture: bool = False, check: bool = False) -> CommandResult:
    """总是返回成功结果的 fake ``run_command`` 函数。"""
    return CommandResult(cmd=list(cmd), returncode=0, stdout="", stderr="")


def _recording_subprocess_run(calls: list[list[str]]) -> Any:
    """创建记录调用的 fake ``subprocess.run`` 函数。"""

    def run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args[0])
        return subprocess.CompletedProcess(args[0], 0, "", "")

    return run


def _subprocess_run_success(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """总是返回成功结果的 fake ``subprocess.run`` 函数。"""
    return subprocess.CompletedProcess(args[0], 0, "", "")


# ============================================================================ #
# 注册验证
# ============================================================================ #
class TestToolsRegistration:
    """3 个新工具的注册验证。"""

    def test_all_tools_registered(self) -> None:
        """3 个新工具应在 _TOOL_REGISTRY 中注册。"""
        for name in ("piptool", "taskkill", "folderback"):
            assert name in _TOOL_REGISTRY, f"工具 {name!r} 未注册"

    def test_piptool_subcommands(self) -> None:
        """piptool 应有 i/u/r/d/up/f 子命令。"""
        subs = fx.list_subcommands("piptool")
        for name in ("i", "u", "r", "d", "up", "f"):
            assert name in subs, f"子命令 {name!r} 未注册"

    def test_taskkill_single_command(self) -> None:
        """taskkill 是单命令工具。"""
        assert fx.list_subcommands("taskkill") == []

    def test_folderback_single_command(self) -> None:
        """folderback 是单命令工具。"""
        assert fx.list_subcommands("folderback") == []


# ============================================================================ #
# piptool 测试
# ============================================================================ #
class TestPiptoolHelpers:
    """piptool 辅助函数测试。"""

    def test_filter_protected_packages_keeps_safe(self) -> None:
        """_filter_protected_packages 保留非保护包。"""
        result = _filter_protected_packages(["requests", "flask"])
        assert result == ["requests", "flask"]

    def test_filter_protected_packages_removes_protected(self, capsys: pytest.CaptureFixture[str]) -> None:
        """_filter_protected_packages 过滤受保护包并打印提示。"""
        result = _filter_protected_packages(["requests", "fcmd", "flask"])
        assert "fcmd" not in result
        assert "requests" in result
        assert "flask" in result
        out = capsys.readouterr().out
        assert "fcmd" in out

    def test_filter_protected_packages_case_insensitive(self, capsys: pytest.CaptureFixture[str]) -> None:
        """_filter_protected_packages 大小写不敏感。"""
        result = _filter_protected_packages(["FCMD", "Requests"])
        assert "FCMD" not in result
        assert "Requests" in result

    def test_get_installed_packages(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_get_installed_packages 解析 pip list 输出。"""
        fake_result = CommandResult(
            cmd=["pip", "list"],
            returncode=0,
            stdout="requests==2.31.0\nflask==3.0.0\n",
            stderr="",
        )
        monkeypatch.setattr("fcmd.cli.piptool.run_command", _fake_run(fake_result))
        result = _get_installed_packages()
        assert "requests" in result
        assert "flask" in result

    def test_get_installed_packages_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_get_installed_packages 空输出返回空列表。"""
        fake_result = CommandResult(cmd=["pip", "list"], returncode=0, stdout="", stderr="")
        monkeypatch.setattr("fcmd.cli.piptool.run_command", _fake_run(fake_result))
        assert _get_installed_packages() == []

    def test_expand_wildcard_no_pattern(self) -> None:
        """_expand_wildcard_packages 无通配符时返回原列表。"""
        assert _expand_wildcard_packages("requests") == ["requests"]

    def test_expand_wildcard_with_pattern(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_expand_wildcard_packages 展开通配符。"""
        monkeypatch.setattr(
            "fcmd.cli.piptool._get_installed_packages",
            lambda: ["requests", "flask", "django"],
        )
        result = _expand_wildcard_packages("f*")
        assert "flask" in result
        assert "requests" not in result


class TestPiptoolCommands:
    """piptool CLI 子命令测试。"""

    def test_pip_install(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """pip_install 调用 pip install。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(
            "fcmd.cli.piptool.run_command",
            _recording_run(calls),
        )
        pip_install(["requests", "flask"])
        assert calls[0] == ["pip", "install", "requests", "flask"]
        out = capsys.readouterr().out
        assert "安装完成" in out

    def test_pip_uninstall_protected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """pip_uninstall 跳过受保护包。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(
            "fcmd.cli.piptool.run_command",
            _recording_run(calls),
        )
        monkeypatch.setattr("fcmd.cli.piptool._expand_wildcard_packages", lambda p: [p])
        pip_uninstall(["fcmd"])
        # 受保护包应跳过，不调用 pip uninstall
        assert not any("uninstall" in " ".join(c) for c in calls)
        out = capsys.readouterr().out
        assert "受保护" in out

    def test_pip_uninstall_normal(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """pip_uninstall 正常卸载。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(
            "fcmd.cli.piptool.run_command",
            _recording_run(calls),
        )
        monkeypatch.setattr("fcmd.cli.piptool._expand_wildcard_packages", lambda p: [p])
        pip_uninstall(["requests"])
        assert calls[0] == ["pip", "uninstall", "-y", "requests"]

    def test_pip_reinstall_all_protected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """pip_reinstall 全是受保护包时跳过。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(
            "fcmd.cli.piptool.run_command",
            _recording_run(calls),
        )
        pip_reinstall(["fcmd"])
        assert not calls
        out = capsys.readouterr().out
        assert "受保护" in out

    def test_pip_reinstall_normal(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """pip_reinstall 正常重装。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(
            "fcmd.cli.piptool.run_command",
            _recording_run(calls),
        )
        pip_reinstall(["requests"])
        assert calls[0] == ["pip", "uninstall", "-y", "requests"]
        assert calls[1] == ["pip", "install", "requests"]

    def test_pip_reinstall_offline(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """pip_reinstall 离线模式添加 --no-index。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(
            "fcmd.cli.piptool.run_command",
            _recording_run(calls),
        )
        pip_reinstall(["requests"], offline=True)
        assert calls[1] == [
            "pip",
            "install",
            "--no-index",
            "--find-links",
            ".",
            "requests",
        ]

    def test_pip_download(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """pip_download 下载到 packages 目录。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(
            "fcmd.cli.piptool.run_command",
            _recording_run(calls),
        )
        pip_download(["requests"])
        assert calls[0] == ["pip", "download", "requests", "-d", "packages"]

    def test_pip_download_offline(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """pip_download 离线模式。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(
            "fcmd.cli.piptool.run_command",
            _recording_run(calls),
        )
        pip_download(["requests"], offline=True)
        assert "--no-index" in calls[0]
        assert "--find-links" in calls[0]

    def test_pip_upgrade(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """pip_upgrade 升级 pip。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(
            "fcmd.cli.piptool.run_command",
            _recording_run(calls),
        )
        pip_upgrade()
        assert calls[0] == ["python", "-m", "pip", "install", "--upgrade", "pip"]
        out = capsys.readouterr().out
        assert "升级完成" in out

    def test_pip_freeze(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """pip_freeze 导出依赖到 requirements.txt。"""
        monkeypatch.chdir(tmp_path)
        fake_result = CommandResult(
            cmd=["pip", "freeze"],
            returncode=0,
            stdout="requests==2.31.0\nflask==3.0.0\n",
            stderr="",
        )
        monkeypatch.setattr("fcmd.cli.piptool.run_command", _fake_run(fake_result))
        pip_freeze()
        content = (tmp_path / "requirements.txt").read_text(encoding="utf-8")
        assert "requests==2.31.0" in content
        out = capsys.readouterr().out
        assert "requirements.txt" in out


class TestPiptoolRunTool:
    """piptool 通过 run_tool 集成测试。"""

    def test_pip_i_via_run_tool(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """fcmd piptool i <packages> 通过 run_tool 调用。"""
        monkeypatch.setattr(
            "fcmd.cli.piptool.run_command",
            _success_run,
        )
        code = run_tool("piptool", ["i", "requests"])
        assert code == 0
        out = capsys.readouterr().out
        assert "安装完成" in out

    def test_pip_up_via_run_tool(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """fcmd piptool up 通过 run_tool 调用。"""
        monkeypatch.setattr(
            "fcmd.cli.piptool.run_command",
            _success_run,
        )
        code = run_tool("piptool", ["up"])
        assert code == 0
        out = capsys.readouterr().out
        assert "升级完成" in out


# ============================================================================ #
# taskkill 测试
# ============================================================================ #
class TestTaskkill:
    """taskkill 工具测试。"""

    def test_kill_process_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """kill_process 返回 0 表示终止信号已发送。"""
        monkeypatch.setattr("fcmd.cli.taskkill.subprocess.run", _subprocess_run_success)
        assert kill_process("chrome.exe") == 0

    def test_kill_process_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """kill_process 返回 1 表示未找到匹配进程。"""

        def run_not_found(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args[0], 1, "", "")

        monkeypatch.setattr("fcmd.cli.taskkill.subprocess.run", run_not_found)
        assert kill_process("nonexistent") == 1

    def test_kill_process_windows_cmd(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Windows 下 kill_process 用 taskkill。"""
        monkeypatch.setattr(sys, "platform", "win32")
        captured: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.taskkill.subprocess.run", _recording_subprocess_run(captured))
        kill_process("chrome.exe")
        assert captured[0][0] == "taskkill"
        assert "/f" in captured[0]
        assert "/im" in captured[0]
        assert "chrome.exe*" in captured[0]

    def test_kill_process_linux_cmd(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Linux 下 kill_process 用 pkill。"""
        monkeypatch.setattr(sys, "platform", "linux")
        captured: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.taskkill.subprocess.run", _recording_subprocess_run(captured))
        kill_process("python")
        assert captured[0][0] == "pkill"
        assert "-f" in captured[0]
        assert "python*" in captured[0]

    def test_taskkill_run_multiple(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """taskkill_run 批量终止进程。"""
        monkeypatch.setattr("fcmd.cli.taskkill.kill_process", lambda *_: 0)
        taskkill_run(["chrome.exe", "python"])
        out = capsys.readouterr().out
        assert "chrome.exe" in out
        assert "python" in out
        assert "已发送终止信号" in out

    def test_taskkill_run_not_found(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """taskkill_run 未找到进程时打印提示。"""
        monkeypatch.setattr("fcmd.cli.taskkill.kill_process", lambda *_: 1)
        taskkill_run(["nonexistent"])
        out = capsys.readouterr().out
        assert "未找到匹配进程" in out

    def test_taskkill_via_run_tool(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """fcmd taskkill <names> 通过 run_tool 调用。"""
        monkeypatch.setattr("fcmd.cli.taskkill.kill_process", lambda *_: 0)
        code = run_tool("taskkill", ["chrome.exe"])
        assert code == 0
        out = capsys.readouterr().out
        assert "chrome.exe" in out


# ============================================================================ #
# folderback 测试
# ============================================================================ #
class TestFolderback:
    """folderback 工具测试。"""

    def test_remove_old_backups_no_files(self, tmp_path: Path) -> None:
        """remove_old_backups 无备份文件时不操作。"""
        remove_old_backups("test", tmp_path, max_zip=3)
        # 无异常即通过

    def test_remove_old_backups_under_limit(self, tmp_path: Path) -> None:
        """remove_old_backups 备份数不超过限制时不删除。"""
        for i in range(3):
            (tmp_path / f"test_2023010{i}_120000.zip").write_text("", encoding="utf-8")
        remove_old_backups("test", tmp_path, max_zip=5)
        zips = list(tmp_path.glob("*.zip"))
        assert len(zips) == 3

    def test_remove_old_backups_over_limit(self, tmp_path: Path) -> None:
        """remove_old_backups 超出限制时删除最旧的。"""
        for i in range(5):
            (tmp_path / f"test_2023010{i}_120000.zip").write_text("", encoding="utf-8")
        remove_old_backups("test", tmp_path, max_zip=2)
        zips = sorted(tmp_path.glob("*.zip"))
        assert len(zips) == 2
        # 保留最新的两个（03 和 04）
        names = [z.name for z in zips]
        assert "test_20230103_120000.zip" in names
        assert "test_20230104_120000.zip" in names

    def test_zip_target_creates_zip(self, tmp_path: Path) -> None:
        """zip_target 创建 zip 文件。"""
        src = tmp_path / "project"
        src.mkdir()
        (src / "a.txt").write_text("hello", encoding="utf-8")
        (src / "subdir").mkdir()
        (src / "subdir" / "b.txt").write_text("world", encoding="utf-8")
        dst = tmp_path / "backup"
        dst.mkdir()

        zip_target(src, dst, max_zip=5)

        zips = list(dst.glob("*.zip"))
        assert len(zips) == 1
        # 验证 zip 内容
        with zipfile.ZipFile(zips[0]) as zf:
            names = zf.namelist()
            assert any("a.txt" in n for n in names)
            assert any("b.txt" in n for n in names)

    def test_backup_folder_src_not_exist(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """backup_folder 源不存在时打印提示。"""
        backup_folder(src=str(tmp_path / "nonexistent"), dst=str(tmp_path / "backup"))
        out = capsys.readouterr().out
        assert "不存在" in out

    def test_backup_folder_creates_dst(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """backup_folder 自动创建目标目录。"""
        src = tmp_path / "project"
        src.mkdir()
        (src / "a.txt").write_text("hello", encoding="utf-8")
        dst = tmp_path / "backup"

        backup_folder(src=str(src), dst=str(dst), max_zip=3)

        out = capsys.readouterr().out
        assert "创建目标文件夹" in out
        assert "备份完成" in out
        zips = list(dst.glob("*.zip"))
        assert len(zips) == 1

    def test_backup_folder_default_src(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """backup_folder 默认备份当前目录。"""
        src = tmp_path / "project"
        src.mkdir()
        (src / "a.txt").write_text("hello", encoding="utf-8")
        monkeypatch.chdir(src)
        dst = tmp_path / "backup"

        backup_folder(dst=str(dst), max_zip=3)

        out = capsys.readouterr().out
        assert "备份完成" in out

    def test_backup_folder_via_run_tool(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """fcmd folderback 通过 run_tool 调用。"""
        src = tmp_path / "project"
        src.mkdir()
        (src / "a.txt").write_text("hello", encoding="utf-8")
        dst = tmp_path / "backup"

        code = run_tool("folderback", ["--src", str(src), "--dst", str(dst), "--max-zip", "3"])
        assert code == 0
        out = capsys.readouterr().out
        assert "备份完成" in out
