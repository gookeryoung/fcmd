"""bumpversion 工具测试。

验证 ``fcmd.cli.bumpversion`` 模块：
- 工具注册
- 辅助函数（模式匹配与替换）
- 版本号读取/写入
- 单文件版本递增
- 项目级版本递增
- 异常分支
- CLI 调度
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import fcmd as fx
import fcmd.cli.bumpversion
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli.bumpversion import (
    _build_replacement_string,
    _get_pattern_for_file,
    _read_version,
    _write_version_to_file,
    bump_file_version,
    bump_project_version,
)
from fcmd.models import CommandResult, Version


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
    """bumpversion 工具的注册验证。"""

    def test_all_tools_registered(self) -> None:
        """bumpversion 应在 _TOOL_REGISTRY 中注册。"""
        for name in ("bumpversion",):
            assert name in _TOOL_REGISTRY, f"工具 {name!r} 未注册"

    def test_bumpversion_single_command(self) -> None:
        """bumpversion 是单命令工具。"""
        assert fx.list_subcommands("bumpversion") == []


# ============================================================================ #
# bumpversion 测试
# ============================================================================ #
class TestBumpversionHelpers:
    """bumpversion 辅助函数测试。"""

    def test_get_pattern_pyproject(self) -> None:
        """_get_pattern_for_file 对 pyproject.toml 返回对应正则。"""
        pattern = _get_pattern_for_file("pyproject.toml")
        assert pattern is not None

    def test_get_pattern_init(self) -> None:
        """_get_pattern_for_file 对 __init__.py 返回对应正则。"""
        pattern = _get_pattern_for_file("__init__.py")
        assert pattern is not None

    def test_get_pattern_other_file(self) -> None:
        """_get_pattern_for_file 对其他文件返回 None。"""
        assert _get_pattern_for_file("setup.py") is None
        assert _get_pattern_for_file("README.md") is None

    def test_build_replacement_pyproject_double_quote(self) -> None:
        """_build_replacement_string 保留 pyproject.toml 双引号格式。"""
        original = 'version = "1.2.3"'
        result = _build_replacement_string(original, "1.2.4", "pyproject.toml")
        assert result == 'version = "1.2.4"'

    def test_build_replacement_pyproject_single_quote(self) -> None:
        """_build_replacement_string 保留单引号格式。"""
        original = "version = '1.2.3'"
        result = _build_replacement_string(original, "1.2.4", "pyproject.toml")
        assert result == "version = '1.2.4'"

    def test_build_replacement_init(self) -> None:
        """_build_replacement_string 对 __init__.py 用 __version__ key。"""
        original = '__version__ = "1.2.3"'
        result = _build_replacement_string(original, "1.2.4", "__init__.py")
        assert result == '__version__ = "1.2.4"'

    def test_build_replacement_with_indent(self) -> None:
        """_build_replacement_string 保留缩进。"""
        original = '  version = "1.2.3"'
        result = _build_replacement_string(original, "1.2.4", "pyproject.toml")
        assert result == '  version = "1.2.4"'


class TestReadVersion:
    """_read_version 测试。"""

    def test_read_pyproject(self, tmp_path: Path) -> None:
        """从 pyproject.toml 读取版本号。"""
        f = tmp_path / "pyproject.toml"
        f.write_text('[project]\nname = "test"\nversion = "1.2.3"\n', encoding="utf-8")
        assert _read_version(f) == Version(1, 2, 3)

    def test_read_init(self, tmp_path: Path) -> None:
        """从 __init__.py 读取版本号。"""
        f = tmp_path / "__init__.py"
        f.write_text('"""模块。"""\n__version__ = "0.1.0"\n', encoding="utf-8")
        assert _read_version(f) == Version(0, 1, 0)

    def test_read_no_version(self, tmp_path: Path) -> None:
        """文件中无版本号返回 None。"""
        f = tmp_path / "pyproject.toml"
        f.write_text('[project]\nname = "test"\n', encoding="utf-8")
        assert _read_version(f) is None

    def test_read_unsupported_file(self, tmp_path: Path) -> None:
        """不支持的文件类型返回 None。"""
        f = tmp_path / "setup.py"
        f.write_text('version = "1.0.0"\n', encoding="utf-8")
        assert _read_version(f) is None

    def test_read_with_prerelease(self, tmp_path: Path) -> None:
        """读取带预发布标记的版本号。"""
        f = tmp_path / "pyproject.toml"

        f.write_text('version = "1.2.3-alpha.1"\n', encoding="utf-8")
        assert _read_version(f) == Version(1, 2, 3, prerelease="alpha.1")

    def test_read_with_build_metadata(self, tmp_path: Path) -> None:
        """读取带构建元数据的版本号。"""
        f = tmp_path / "pyproject.toml"
        f.write_text('version = "1.2.3+build.1"\n', encoding="utf-8")
        assert _read_version(f) == Version(1, 2, 3, buildmetadata="build.1")

    def test_read_nonexistent_file(self) -> None:
        """文件不存在返回 None。"""
        assert _read_version(Path("nonexistent.toml")) is None

    def test_read_invalid_encoding(self, tmp_path: Path) -> None:
        """无效编码返回 None。"""

        f = tmp_path / "pyproject.toml"
        f.write_bytes(b'\xff\xfeversion = "1.0.0"')
        assert _read_version(f) is None


class TestWriteVersionToFile:
    """_write_version_to_file 测试。"""

    def test_write_pyproject(self, tmp_path: Path) -> None:
        """写入 pyproject.toml 版本号。"""
        f = tmp_path / "pyproject.toml"
        f.write_text('version = "1.2.3"\n', encoding="utf-8")
        assert _write_version_to_file(f, "1.2.4")
        content = f.read_text(encoding="utf-8")
        assert 'version = "1.2.4"' in content

    def test_write_init(self, tmp_path: Path) -> None:
        """写入 __init__.py 版本号。"""
        f = tmp_path / "__init__.py"
        f.write_text('__version__ = "0.1.0"\n', encoding="utf-8")
        assert _write_version_to_file(f, "0.2.0")
        content = f.read_text(encoding="utf-8")
        assert '__version__ = "0.2.0"' in content

    def test_write_preserves_other_content(self, tmp_path: Path) -> None:
        """写入时保留其他内容。"""
        f = tmp_path / "pyproject.toml"
        original = '[project]\nname = "test"\nversion = "1.2.3"\ndescription = "desc"\n'
        f.write_text(original, encoding="utf-8")
        _write_version_to_file(f, "1.2.4")
        content = f.read_text(encoding="utf-8")
        assert 'name = "test"' in content
        assert 'description = "desc"' in content
        assert 'version = "1.2.4"' in content

    def test_write_unsupported_file(self, tmp_path: Path) -> None:
        """不支持的文件类型返回 False。"""
        f = tmp_path / "setup.py"
        f.write_text('version = "1.0.0"\n', encoding="utf-8")
        assert not _write_version_to_file(f, "1.0.1")


class TestBumpFileVersion:
    """bump_file_version 测试。"""

    def test_bump_pyproject_patch(self, tmp_path: Path) -> None:
        """bump pyproject.toml patch 版本。"""
        f = tmp_path / "pyproject.toml"
        f.write_text('version = "1.2.3"\n', encoding="utf-8")
        new_version = bump_file_version(f, "patch")
        assert new_version == "1.2.4"
        assert 'version = "1.2.4"' in f.read_text(encoding="utf-8")

    def test_bump_init_minor(self, tmp_path: Path) -> None:
        """bump __init__.py minor 版本。"""
        f = tmp_path / "__init__.py"
        f.write_text('__version__ = "1.2.3"\n', encoding="utf-8")
        new_version = bump_file_version(f, "minor")
        assert new_version == "1.3.0"

    def test_bump_no_version(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """文件中无版本号返回 None。"""
        f = tmp_path / "pyproject.toml"
        f.write_text('[project]\nname = "test"\n', encoding="utf-8")
        result = bump_file_version(f, "patch")
        assert result is None
        out = capsys.readouterr().out
        assert "未找到版本号模式" in out


class TestBumpProjectVersion:
    """bump_project_version 测试。"""

    def test_bump_project_patch(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """bump_project_version patch 递增项目版本号。"""
        monkeypatch.chdir(tmp_path)
        # 创建 pyproject.toml 和 __init__.py
        (tmp_path / "pyproject.toml").write_text('version = "0.1.0"\n', encoding="utf-8")
        src_dir = tmp_path / "src" / "mypkg"
        src_dir.mkdir(parents=True)
        (src_dir / "__init__.py").write_text('__version__ = "0.1.0"\n', encoding="utf-8")

        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.bumpversion.run_command", _recording_run(calls))

        result = bump_project_version(part="patch")
        assert result == "0.1.1"
        out = capsys.readouterr().out
        assert "基准版本: 0.1.0" in out
        assert "新版本: 0.1.1" in out
        # 验证 git add + commit + tag 被调用
        assert any(c[:2] == ["git", "add"] for c in calls)
        assert any(c[:2] == ["git", "commit"] for c in calls)
        assert any(c[:2] == ["git", "tag"] for c in calls)
        # 验证文件已更新
        assert 'version = "0.1.1"' in (tmp_path / "pyproject.toml").read_text(encoding="utf-8")

    def test_bump_project_no_tag(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """bump_project_version no_tag=True 不创建 tag。"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text('version = "1.0.0"\n', encoding="utf-8")

        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.bumpversion.run_command", _recording_run(calls))

        result = bump_project_version(part="minor", no_tag=True)
        assert result == "1.1.0"
        # 不应有 git tag 命令
        assert not any(c[:2] == ["git", "tag"] for c in calls)

    def test_bump_project_major(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """bump_project_version major 递增。"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text('version = "1.2.3"\n', encoding="utf-8")

        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.bumpversion.run_command", _recording_run(calls))

        result = bump_project_version(part="major")
        assert result == "2.0.0"

    def test_bump_project_no_files(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """无版本号文件时返回 None。"""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("fcmd.cli.bumpversion.run_command", _success_run)
        result = bump_project_version(part="patch")
        assert result is None
        out = capsys.readouterr().out
        assert "未找到包含版本号的文件" in out

    def test_bump_project_invalid_part(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """无效 part 值返回 None。"""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("fcmd.cli.bumpversion.run_command", _success_run)
        # bypass type checker
        result = bump_project_version(part="invalid")  # type: ignore[arg-type]
        assert result is None
        out = capsys.readouterr().out
        assert "无效的版本部分" in out

    def test_bump_project_ignores_venv(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """bump_project_version 排除 .venv 目录。"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text('version = "1.0.0"\n', encoding="utf-8")
        # .venv 中的 __init__.py 应被忽略
        venv_dir = tmp_path / ".venv" / "lib" / "site-packages" / "otherpkg"
        venv_dir.mkdir(parents=True)
        (venv_dir / "__init__.py").write_text('__version__ = "9.9.9"\n', encoding="utf-8")

        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.bumpversion.run_command", _recording_run(calls))

        result = bump_project_version(part="patch")
        # 基准版本应为 1.0.0，而非 .venv 中的 9.9.9
        assert result == "1.0.1"

    def test_bump_project_takes_max_version(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """bump_project_version 取所有文件版本号最大值作为基准。"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text('version = "1.0.0"\n', encoding="utf-8")
        src_dir = tmp_path / "src" / "mypkg"
        src_dir.mkdir(parents=True)
        # __init__.py 版本号更高
        (src_dir / "__init__.py").write_text('__version__ = "2.5.3"\n', encoding="utf-8")

        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.bumpversion.run_command", _recording_run(calls))

        result = bump_project_version(part="patch")
        # 基准为 2.5.3，新版本为 2.5.4
        assert result == "2.5.4"

    def test_bumpversion_via_run_tool(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """fcmd bumpversion --part patch 通过 run_tool 调用。"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text('version = "0.1.0"\n', encoding="utf-8")
        monkeypatch.setattr("fcmd.cli.bumpversion.run_command", _success_run)
        code = run_tool("bumpversion", ["--part", "patch"])
        assert code == 0
        out = capsys.readouterr().out
        assert "新版本: 0.1.1" in out


# ============================================================================ #
# bumpversion 异常分支补充测试
# ============================================================================ #
class TestBumpversionErrorBranches:
    """bumpversion 异常分支补充测试。"""

    def test_write_version_read_failure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """_write_version_to_file 读取失败时返回 False。"""
        f = tmp_path / "pyproject.toml"
        f.write_text('version = "1.0.0"\n', encoding="utf-8")

        def raise_os_error(*args: Any, **kwargs: Any) -> str:
            raise OSError("read error")

        monkeypatch.setattr(Path, "read_text", raise_os_error)
        assert not _write_version_to_file(f, "1.0.1")
        out = capsys.readouterr().out
        assert "读取文件" in out

    def test_write_version_write_failure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """_write_version_to_file 写入失败时返回 False。"""
        f = tmp_path / "pyproject.toml"
        f.write_text('version = "1.0.0"\n', encoding="utf-8")

        original_read = Path.read_text

        def selective_read(self: Path, *args: Any, **kwargs: Any) -> str:
            return original_read(self, *args, **kwargs)

        def raise_os_error(*args: Any, **kwargs: Any) -> int:
            raise OSError("write error")

        monkeypatch.setattr(Path, "read_text", selective_read)
        monkeypatch.setattr(Path, "write_text", raise_os_error)
        assert not _write_version_to_file(f, "1.0.1")
        out = capsys.readouterr().out
        assert "更新文件" in out

    def test_bump_project_no_readable_version(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """bump_project_version 文件存在但都读不到版本号时返回 None。"""
        monkeypatch.chdir(tmp_path)
        # pyproject.toml 存在但无 version 字段
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n', encoding="utf-8")
        monkeypatch.setattr("fcmd.cli.bumpversion.run_command", _success_run)
        result = bump_project_version(part="patch")
        assert result is None
        out = capsys.readouterr().out
        assert "未能从任何文件读取版本号" in out
