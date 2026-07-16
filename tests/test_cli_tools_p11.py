"""P11 新工具测试：clr / packtool。

验证 ``fcmd.cli`` 包下 2 个参考 pyflowx 实现的工具：
- ``clr``：跨平台清屏（单命令）
- ``packtool``：Python 打包工具（src/deps/wheel/embed/zip/clean 子命令）
"""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest

import fcmd as fx
import fcmd.cli.clr
import fcmd.cli.packtool
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli.clr import clear_screen
from fcmd.cli.packtool import (
    _normalize_arch,
    clean_build_dir,
    create_zip_package,
    install_embed_python,
    pack_dependencies,
    pack_source,
    pack_wheel,
)
from fcmd.models import CommandResult


# ============================================================================ #
# 测试辅助：创建 fake run_command 函数（避免 lambda ARG005）
# ============================================================================ #
def _recording_run(calls: list[list[str]]) -> Any:
    """创建记录调用的 fake ``run_command`` 函数，返回成功结果。"""

    def run(cmd: list[str], *, capture: bool = False, check: bool = False) -> CommandResult:
        calls.append(cmd)
        return CommandResult(cmd=list(cmd), returncode=0, stdout="", stderr="")

    return run


def _success_run(cmd: list[str], *, capture: bool = False, check: bool = False) -> CommandResult:
    """总是返回成功结果的 fake ``run_command`` 函数。"""
    return CommandResult(cmd=list(cmd), returncode=0, stdout="", stderr="")


# ============================================================================ #
# 注册验证
# ============================================================================ #
class TestToolsRegistration:
    """2 个新工具的注册验证。"""

    def test_all_tools_registered(self) -> None:
        """2 个新工具应在 _TOOL_REGISTRY 中注册。"""
        for name in ("clr", "packtool"):
            assert name in _TOOL_REGISTRY, f"工具 {name!r} 未注册"

    def test_clr_single_command(self) -> None:
        """clr 是单命令工具。"""
        assert fx.list_subcommands("clr") == []

    def test_packtool_subcommands(self) -> None:
        """packtool 应有 6 个子命令。"""
        subs = fx.list_subcommands("packtool")
        for name in ("src", "deps", "wheel", "embed", "zip", "clean"):
            assert name in subs, f"子命令 {name!r} 未注册"


# ============================================================================ #
# clr 测试
# ============================================================================ #
class TestClr:
    """clr 工具测试。"""

    def test_clear_screen_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Windows 下 clear_screen 调用 cls（shell=True）。"""
        monkeypatch.setattr(sys, "platform", "win32")
        captured: list[tuple[Any, dict[str, Any]]] = []

        def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            captured.append((cmd, kwargs))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr("fcmd.cli.clr.subprocess.run", fake_run)
        result = clear_screen()
        assert result == 0
        # Windows 传字符串 cmd="cls" + shell=True
        assert captured[0][0] == "cls"
        assert captured[0][1].get("shell") is True

    def test_clear_screen_linux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Linux 下 clear_screen 调用 clear（shell=False）。"""
        monkeypatch.setattr(sys, "platform", "linux")
        captured: list[tuple[Any, dict[str, Any]]] = []

        def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            captured.append((cmd, kwargs))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr("fcmd.cli.clr.subprocess.run", fake_run)
        result = clear_screen()
        assert result == 0
        # Linux 传字符串 cmd="clear" + shell=False
        assert captured[0][0] == "clear"
        assert captured[0][1].get("shell") is False

    def test_clear_screen_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """clear_screen 失败时返回非零退出码（不抛异常）。"""
        monkeypatch.setattr(sys, "platform", "linux")

        def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(cmd, 1, "", "")

        monkeypatch.setattr("fcmd.cli.clr.subprocess.run", fake_run)
        assert clear_screen() == 1

    def test_clear_screen_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """clear_screen 命令未找到时抛 RuntimeError（含命令名）。"""
        monkeypatch.setattr(sys, "platform", "linux")

        def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            raise FileNotFoundError(2, "系统找不到指定的文件。")

        monkeypatch.setattr("fcmd.cli.clr.subprocess.run", fake_run)
        with pytest.raises(RuntimeError, match="清屏命令未找到: clear"):
            clear_screen()

    def test_clr_via_run_tool(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """fcmd clr 通过 run_tool 调用（fn 任务，mock subprocess.run）。"""

        def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr("fcmd.cli.clr.subprocess.run", fake_run)
        code = run_tool("clr", [])
        assert code == 0


# ============================================================================ #
# packtool 测试
# ============================================================================ #
class TestPacktoolSource:
    """packtool src 子命令测试。"""

    def test_pack_source_with_src_dir(self, tmp_path: Path) -> None:
        """pack_source 项目有 src/ 子目录时复制 src/。"""
        project_dir = tmp_path / "myproject"
        src_dir = project_dir / "src" / "mypkg"
        src_dir.mkdir(parents=True)
        (src_dir / "__init__.py").write_text("content")
        (src_dir / "module.py").write_text("code")

        output_dir = tmp_path / "output"
        pack_source(project_dir, output_dir)

        # 验证源码已复制到 output/src/myproject/src/mypkg/
        copied = output_dir / "src" / "myproject" / "src" / "mypkg"
        assert (copied / "__init__.py").exists()
        assert (copied / "module.py").exists()

    def test_pack_source_without_src_dir(self, tmp_path: Path) -> None:
        """pack_source 项目无 src/ 子目录时复制散文件。"""
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()
        (project_dir / "main.py").write_text("code")
        (project_dir / "utils.py").write_text("utils")

        output_dir = tmp_path / "output"
        pack_source(project_dir, output_dir)

        copied = output_dir / "src" / "myproject"
        assert (copied / "main.py").exists()
        assert (copied / "utils.py").exists()

    def test_pack_source_ignores_cache(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """pack_source 跳过 __pycache__ 等缓存目录。"""
        project_dir = tmp_path / "myproject"
        src_dir = project_dir / "src" / "mypkg"
        src_dir.mkdir(parents=True)
        (src_dir / "__init__.py").write_text("content")
        cache_dir = src_dir / "__pycache__"
        cache_dir.mkdir()
        (cache_dir / "module.cpython-310.pyc").write_text("cache")

        output_dir = tmp_path / "output"
        pack_source(project_dir, output_dir)

        copied = output_dir / "src" / "myproject" / "src" / "mypkg"
        assert (copied / "__init__.py").exists()
        # __pycache__ 应被忽略
        assert not (copied / "__pycache__").exists()

    def test_pack_source_via_run_tool(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """fcmd packtool src 通过 run_tool 调用。"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "main.py").write_text("code")
        code = run_tool("packtool", ["src"])
        assert code == 0
        out = capsys.readouterr().out
        assert "源码打包完成" in out


class TestPacktoolDeps:
    """packtool deps 子命令测试。"""

    def test_pack_dependencies(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """pack_dependencies 调用 pip install --target。"""
        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.packtool.run_command", _recording_run(calls))

        lib_dir = tmp_path / "libs"
        pack_dependencies(["requests", "flask"], lib_dir)
        assert calls[0][:3] == ["pip", "install", "--target"]
        assert str(lib_dir) in calls[0]
        assert "requests" in calls[0]
        assert "flask" in calls[0]
        out = capsys.readouterr().out
        assert "依赖打包完成" in out

    def test_pack_dependencies_via_run_tool(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """fcmd packtool deps <packages> 通过 run_tool 调用。"""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("fcmd.cli.packtool.run_command", _success_run)
        code = run_tool("packtool", ["deps", "requests"])
        assert code == 0


class TestPacktoolWheel:
    """packtool wheel 子命令测试。"""

    def test_pack_wheel(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """pack_wheel 调用 pip wheel。"""
        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.packtool.run_command", _recording_run(calls))

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        output_dir = tmp_path / "dist"
        pack_wheel(project_dir, output_dir)
        assert calls[0][:2] == ["pip", "wheel"]
        assert "--no-deps" in calls[0]
        assert str(output_dir) in calls[0]
        out = capsys.readouterr().out
        assert "Wheel 打包完成" in out


class TestPacktoolEmbed:
    """packtool embed 子命令测试。"""

    def test_normalize_arch_amd64(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_normalize_arch 返回 amd64。"""
        monkeypatch.setattr("fcmd.cli.packtool.platform.machine", lambda: "x86_64")
        assert _normalize_arch() == "amd64"

    def test_normalize_arch_arm64(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_normalize_arch 返回 arm64。"""
        monkeypatch.setattr("fcmd.cli.packtool.platform.machine", lambda: "aarch64")
        assert _normalize_arch() == "arm64"

    def test_install_embed_python(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """install_embed_python 下载并解压嵌入式 Python。"""
        monkeypatch.chdir(tmp_path)

        # 创建 fake zip 文件
        cache_path = tmp_path / ".cache" / "pypack" / "python-3.10.11-embed-amd64.zip"
        cache_path.parent.mkdir(parents=True)
        with zipfile.ZipFile(cache_path, "w") as zf:
            zf.writestr("python.exe", b"fake")
            zf.writestr("python310.dll", b"fake")

        # mock urllib.request.urlretrieve（cache 已存在，不会被调用）
        def fake_urlretrieve(url: str, filename: Any) -> Any:
            raise AssertionError(f"不应调用 urlretrieve，但调用了: {url}")

        monkeypatch.setattr("fcmd.cli.packtool.urllib.request.urlretrieve", fake_urlretrieve)
        monkeypatch.setattr("fcmd.cli.packtool._normalize_arch", lambda: "amd64")

        output_dir = tmp_path / "python"
        install_embed_python("3.10", output_dir)
        assert (output_dir / "python.exe").exists()
        assert (output_dir / "python310.dll").exists()
        out = capsys.readouterr().out
        assert "嵌入式 Python 安装完成" in out

    def test_install_embed_python_downloads(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """install_embed_python cache 不存在时下载。"""
        monkeypatch.chdir(tmp_path)

        # 创建 fake zip 内容
        zip_content = tmp_path / "fake.zip"
        with zipfile.ZipFile(zip_content, "w") as zf:
            zf.writestr("python.exe", b"fake")

        # mock urlretrieve 写入 fake zip
        def fake_urlretrieve(url: str, filename: Any) -> Any:
            import shutil

            shutil.copy(zip_content, filename)
            return filename

        monkeypatch.setattr("fcmd.cli.packtool.urllib.request.urlretrieve", fake_urlretrieve)
        monkeypatch.setattr("fcmd.cli.packtool._normalize_arch", lambda: "amd64")

        output_dir = tmp_path / "python"
        install_embed_python("3.11", output_dir)
        assert (output_dir / "python.exe").exists()
        out = capsys.readouterr().out
        assert "正在下载" in out
        assert "下载完成" in out


class TestPacktoolZip:
    """packtool zip 子命令测试。"""

    def test_create_zip_package(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """create_zip_package 创建 zip 文件。"""
        source_dir = tmp_path / "src"
        source_dir.mkdir()
        (source_dir / "a.txt").write_text("a")
        (source_dir / "sub").mkdir()
        (source_dir / "sub" / "b.txt").write_text("b")

        output_file = tmp_path / "out" / "package.zip"
        create_zip_package(source_dir, output_file)
        assert output_file.exists()

        # 验证 zip 内容
        with zipfile.ZipFile(output_file, "r") as zf:
            names = zf.namelist()
            assert "a.txt" in names
            assert "sub/b.txt" in names

        out = capsys.readouterr().out
        assert "ZIP 打包完成" in out

    def test_create_zip_empty_dir(
        self,
        tmp_path: Path,
    ) -> None:
        """create_zip_package 空目录创建空 zip。"""
        source_dir = tmp_path / "empty"
        source_dir.mkdir()
        output_file = tmp_path / "empty.zip"
        create_zip_package(source_dir, output_file)
        assert output_file.exists()
        with zipfile.ZipFile(output_file, "r") as zf:
            assert zf.namelist() == []


class TestPacktoolClean:
    """packtool clean 子命令测试。"""

    def test_clean_existing_dir(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """clean_build_dir 清理已存在的目录。"""
        build_dir = tmp_path / ".pypack"
        build_dir.mkdir()
        (build_dir / "file.txt").write_text("content")

        clean_build_dir(build_dir)
        assert not build_dir.exists()
        out = capsys.readouterr().out
        assert "清理完成" in out

    def test_clean_nonexistent_dir(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """clean_build_dir 目录不存在时打印提示。"""
        build_dir = tmp_path / "nonexistent"
        clean_build_dir(build_dir)
        out = capsys.readouterr().out
        assert "目录不存在" in out

    def test_clean_via_run_tool(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """fcmd packtool clean 通过 run_tool 调用。"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".pypack").mkdir()
        code = run_tool("packtool", ["clean"])
        assert code == 0
        out = capsys.readouterr().out
        assert "清理完成" in out


# ============================================================================ #
# packtool 补充分支测试
# ============================================================================ #
class TestPacktoolBranches:
    """packtool 补充分支测试。"""

    def test_normalize_arch_unknown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_normalize_arch 未知架构返回原值。"""
        monkeypatch.setattr("fcmd.cli.packtool.platform.machine", lambda: "riscv64")
        assert _normalize_arch() == "riscv64"

    def test_pack_source_with_subdir_in_flat_project(
        self,
        tmp_path: Path,
    ) -> None:
        """pack_source 无 src/ 时复制散文件中的子目录。"""
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()
        (project_dir / "main.py").write_text("code")
        sub_dir = project_dir / "subpackage"
        sub_dir.mkdir()
        (sub_dir / "mod.py").write_text("sub")

        output_dir = tmp_path / "output"
        pack_source(project_dir, output_dir)

        copied = output_dir / "src" / "myproject"
        assert (copied / "main.py").exists()
        assert (copied / "subpackage" / "mod.py").exists()
