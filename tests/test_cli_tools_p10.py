"""P10 新工具测试：filelevel / bumpversion / autofmt。

验证 ``fcmd.cli`` 包下 3 个参考 pyflowx 实现的工具：
- ``filelevel``：文件等级重命名（单子命令 set）
- ``bumpversion``：版本号自动管理（单命令，两阶段策略）
- ``autofmt``：代码格式化与检查（fmt/lint 子命令）
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

import fcmd as fx
import fcmd.cli.autofmt
import fcmd.cli.bumpversion
import fcmd.cli.filelevel
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli.autofmt import fmt, lint
from fcmd.cli.bumpversion import (
    _build_replacement_string,
    _calculate_new_version,
    _get_pattern_for_file,
    _read_version_tuple,
    _write_version_to_file,
    bump_file_version,
    bump_project_version,
)
from fcmd.cli.filelevel import (
    process_file_level,
    process_files_level,
    remove_marks,
)


# ============================================================================ #
# 测试辅助：创建 fake _run 函数（避免 lambda ARG005）
# ============================================================================ #
def _recording_run(calls: list[list[str]]) -> Any:
    """创建记录调用的 fake ``_run`` 函数，返回成功结果。"""

    def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    return run


def _success_run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """总是返回成功结果的 fake ``_run`` 函数。"""
    return subprocess.CompletedProcess(cmd, 0, "", "")


# ============================================================================ #
# 注册验证
# ============================================================================ #
class TestToolsRegistration:
    """3 个新工具的注册验证。"""

    def test_all_tools_registered(self) -> None:
        """3 个新工具应在 _TOOL_REGISTRY 中注册。"""
        for name in ("filelevel", "bumpversion", "autofmt"):
            assert name in _TOOL_REGISTRY, f"工具 {name!r} 未注册"

    def test_filelevel_subcommands(self) -> None:
        """filelevel 应有 set 子命令。"""
        subs = fx.list_subcommands("filelevel")
        assert "set" in subs

    def test_bumpversion_single_command(self) -> None:
        """bumpversion 是单命令工具。"""
        assert fx.list_subcommands("bumpversion") == []

    def test_autofmt_subcommands(self) -> None:
        """autofmt 应有 fmt/lint 子命令。"""
        subs = fx.list_subcommands("autofmt")
        assert "fmt" in subs
        assert "lint" in subs


# ============================================================================ #
# filelevel 测试
# ============================================================================ #
class TestRemoveMarks:
    """remove_marks 函数测试。"""

    def test_remove_single_mark(self) -> None:
        """移除单个括号包裹的标记。"""
        assert remove_marks("file(PUB).txt", ["PUB"]) == "file.txt"

    def test_remove_multiple_marks(self) -> None:
        """移除多个标记。"""
        assert remove_marks("file(PUB)(NOR).txt", ["PUB", "NOR"]) == "file.txt"

    def test_remove_mark_with_different_brackets(self) -> None:
        """支持多种括号类型。"""
        assert remove_marks("file[PUB].txt", ["PUB"]) == "file.txt"
        assert remove_marks("file_PUB_.txt", ["PUB"]) == "file.txt"
        assert remove_marks("file【PUB】.txt", ["PUB"]) == "file.txt"

    def test_remove_mark_not_in_brackets(self) -> None:
        """裸标记（无括号包裹）不移除。"""
        assert remove_marks("filePUB.txt", ["PUB"]) == "filePUB.txt"

    def test_remove_mark_not_found(self) -> None:
        """标记不存在时原样返回。"""
        assert remove_marks("file.txt", ["PUB"]) == "file.txt"

    def test_remove_mark_at_boundary(self) -> None:
        """标记在边界时安全处理。"""
        # 标记在开头，左侧无括号
        assert remove_marks("PUB_file.txt", ["PUB"]) == "PUB_file.txt"


class TestProcessFileLevel:
    """process_file_level 函数测试。"""

    def test_set_level_1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """设置等级 1 (PUB)。"""
        f = tmp_path / "report.pdf"
        f.write_text("content")
        process_file_level(f, level=1)
        out = capsys.readouterr().out
        assert "重命名" in out
        assert (tmp_path / "report(PUB).pdf").exists()
        assert not f.exists()

    def test_set_level_2(self, tmp_path: Path) -> None:
        """设置等级 2 (INT)。"""
        f = tmp_path / "report.pdf"
        f.write_text("content")
        process_file_level(f, level=2)
        assert (tmp_path / "report(INT).pdf").exists()

    def test_set_level_3(self, tmp_path: Path) -> None:
        """设置等级 3 (CON)。"""
        f = tmp_path / "report.pdf"
        f.write_text("content")
        process_file_level(f, level=3)
        assert (tmp_path / "report(CON).pdf").exists()

    def test_set_level_4(self, tmp_path: Path) -> None:
        """设置等级 4 (CLA)。"""
        f = tmp_path / "report.pdf"
        f.write_text("content")
        process_file_level(f, level=4)
        assert (tmp_path / "report(CLA).pdf").exists()

    def test_clear_level(self, tmp_path: Path) -> None:
        """等级 0 清除已有标记。"""
        f = tmp_path / "report(PUB).pdf"
        f.write_text("content")
        process_file_level(f, level=0)
        assert (tmp_path / "report.pdf").exists()

    def test_replace_existing_level(self, tmp_path: Path) -> None:
        """已有等级时替换为新等级。"""
        f = tmp_path / "report(PUB).pdf"
        f.write_text("content")
        process_file_level(f, level=3)
        assert (tmp_path / "report(CON).pdf").exists()
        assert not (tmp_path / "report(PUB)(CON).pdf").exists()

    def test_invalid_level_high(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """无效等级（过高）打印提示。"""
        f = tmp_path / "report.pdf"
        f.write_text("content")
        process_file_level(f, level=99)
        out = capsys.readouterr().out
        assert "无效的等级" in out
        assert f.exists()  # 文件未被重命名

    def test_invalid_level_negative(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """无效等级（负数）打印提示。"""
        f = tmp_path / "report.pdf"
        f.write_text("content")
        process_file_level(f, level=-1)
        out = capsys.readouterr().out
        assert "无效的等级" in out

    def test_file_not_exists(self, capsys: pytest.CaptureFixture[str]) -> None:
        """文件不存在时打印提示。"""
        process_file_level(Path("nonexistent.pdf"), level=1)
        out = capsys.readouterr().out
        assert "文件不存在" in out

    def test_no_change_when_already_correct(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """文件名无变化时不重命名。"""
        f = tmp_path / "report.pdf"
        f.write_text("content")
        process_file_level(f, level=0)
        out = capsys.readouterr().out
        assert "重命名" not in out
        assert f.exists()


class TestProcessFilesLevel:
    """process_files_level 批量处理测试。"""

    def test_batch_set_level(self, tmp_path: Path) -> None:
        """批量设置等级。"""
        f1 = tmp_path / "a.pdf"
        f2 = tmp_path / "b.pdf"
        f1.write_text("1")
        f2.write_text("2")
        process_files_level([f1, f2], level=2)
        assert (tmp_path / "a(INT).pdf").exists()
        assert (tmp_path / "b(INT).pdf").exists()

    def test_batch_clear_level(self, tmp_path: Path) -> None:
        """批量清除等级。"""
        f1 = tmp_path / "a(PUB).pdf"
        f2 = tmp_path / "b(CON).pdf"
        f1.write_text("1")
        f2.write_text("2")
        process_files_level([f1, f2], level=0)
        assert (tmp_path / "a.pdf").exists()
        assert (tmp_path / "b.pdf").exists()

    def test_filelevel_via_run_tool(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """fcmd filelevel set <files> --level 2 通过 run_tool 调用。"""
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "doc.pdf"
        f.write_text("content")
        code = run_tool("filelevel", ["set", "doc.pdf", "--level", "2"])
        assert code == 0
        assert (tmp_path / "doc(INT).pdf").exists()


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

    def test_calculate_new_version_patch(self) -> None:
        """patch 递增。"""
        assert _calculate_new_version(1, 2, 3, "patch") == "1.2.4"

    def test_calculate_new_version_minor(self) -> None:
        """minor 递增，patch 归零。"""
        assert _calculate_new_version(1, 2, 3, "minor") == "1.3.0"

    def test_calculate_new_version_major(self) -> None:
        """major 递增，minor 和 patch 归零。"""
        assert _calculate_new_version(1, 2, 3, "major") == "2.0.0"

    def test_calculate_new_version_zero_version(self) -> None:
        """版本 0.0.0 递增。"""
        assert _calculate_new_version(0, 0, 0, "patch") == "0.0.1"
        assert _calculate_new_version(0, 0, 0, "minor") == "0.1.0"
        assert _calculate_new_version(0, 0, 0, "major") == "1.0.0"

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


class TestReadVersionTuple:
    """_read_version_tuple 测试。"""

    def test_read_pyproject(self, tmp_path: Path) -> None:
        """从 pyproject.toml 读取版本号。"""
        f = tmp_path / "pyproject.toml"
        f.write_text('[project]\nname = "test"\nversion = "1.2.3"\n', encoding="utf-8")
        assert _read_version_tuple(f) == (1, 2, 3)

    def test_read_init(self, tmp_path: Path) -> None:
        """从 __init__.py 读取版本号。"""
        f = tmp_path / "__init__.py"
        f.write_text('"""模块。"""\n__version__ = "0.1.0"\n', encoding="utf-8")
        assert _read_version_tuple(f) == (0, 1, 0)

    def test_read_no_version(self, tmp_path: Path) -> None:
        """文件中无版本号返回 None。"""
        f = tmp_path / "pyproject.toml"
        f.write_text('[project]\nname = "test"\n', encoding="utf-8")
        assert _read_version_tuple(f) is None

    def test_read_unsupported_file(self, tmp_path: Path) -> None:
        """不支持的文件类型返回 None。"""
        f = tmp_path / "setup.py"
        f.write_text('version = "1.0.0"\n', encoding="utf-8")
        assert _read_version_tuple(f) is None

    def test_read_with_prerelease(self, tmp_path: Path) -> None:
        """读取带预发布标记的版本号。"""
        f = tmp_path / "pyproject.toml"
        f.write_text('version = "1.2.3-alpha.1"\n', encoding="utf-8")
        assert _read_version_tuple(f) == (1, 2, 3)

    def test_read_with_build_metadata(self, tmp_path: Path) -> None:
        """读取带构建元数据的版本号。"""
        f = tmp_path / "pyproject.toml"
        f.write_text('version = "1.2.3+build.1"\n', encoding="utf-8")
        assert _read_version_tuple(f) == (1, 2, 3)

    def test_read_nonexistent_file(self) -> None:
        """文件不存在返回 None。"""
        assert _read_version_tuple(Path("nonexistent.toml")) is None

    def test_read_invalid_encoding(self, tmp_path: Path) -> None:
        """无效编码返回 None。"""
        f = tmp_path / "pyproject.toml"
        f.write_bytes(b'\xff\xfeversion = "1.0.0"')
        assert _read_version_tuple(f) is None


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
        monkeypatch.setattr("fcmd.cli.bumpversion._run", _recording_run(calls))

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
        monkeypatch.setattr("fcmd.cli.bumpversion._run", _recording_run(calls))

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
        monkeypatch.setattr("fcmd.cli.bumpversion._run", _recording_run(calls))

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
        monkeypatch.setattr("fcmd.cli.bumpversion._run", _success_run)
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
        monkeypatch.setattr("fcmd.cli.bumpversion._run", _success_run)
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
        monkeypatch.setattr("fcmd.cli.bumpversion._run", _recording_run(calls))

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
        monkeypatch.setattr("fcmd.cli.bumpversion._run", _recording_run(calls))

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
        monkeypatch.setattr("fcmd.cli.bumpversion._run", _success_run)
        code = run_tool("bumpversion", ["--part", "patch"])
        assert code == 0
        out = capsys.readouterr().out
        assert "新版本: 0.1.1" in out


# ============================================================================ #
# autofmt 测试
# ============================================================================ #
class TestAutofmt:
    """autofmt 工具测试。"""

    def test_fmt_default_target(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """fmt 默认目标为当前目录。"""
        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.autofmt._run", _recording_run(calls))
        fmt()
        assert calls[0] == ["ruff", "format", "."]
        out = capsys.readouterr().out
        assert "ruff format 完成" in out

    def test_fmt_with_target(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """fmt 指定目标路径。"""
        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.autofmt._run", _recording_run(calls))
        fmt("src")
        assert calls[0] == ["ruff", "format", "src"]

    def test_lint_default_no_fix(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """lint 默认不自动修复。"""
        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.autofmt._run", _recording_run(calls))
        lint()
        assert calls[0] == ["ruff", "check", "."]
        assert "--fix" not in calls[0]

    def test_lint_with_fix(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """lint --fix 添加 --fix --unsafe-fixes。"""
        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.autofmt._run", _recording_run(calls))
        lint("src", fix=True)
        assert "ruff" in calls[0]
        assert "check" in calls[0]
        assert "src" in calls[0]
        assert "--fix" in calls[0]
        assert "--unsafe-fixes" in calls[0]

    def test_lint_with_target(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """lint 指定目标路径。"""
        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.autofmt._run", _recording_run(calls))
        lint("tests")
        assert calls[0] == ["ruff", "check", "tests"]

    def test_fmt_via_run_tool(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """fcmd autofmt fmt 通过 run_tool 调用。"""
        monkeypatch.setattr("fcmd.cli.autofmt._run", _success_run)
        code = run_tool("autofmt", ["fmt"])
        assert code == 0
        out = capsys.readouterr().out
        assert "ruff format 完成" in out

    def test_lint_via_run_tool(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """fcmd autofmt lint --target src --fix 通过 run_tool 调用。"""
        monkeypatch.setattr("fcmd.cli.autofmt._run", _success_run)
        code = run_tool("autofmt", ["lint", "--target", "src", "--fix"])
        assert code == 0
        out = capsys.readouterr().out
        assert "ruff check 完成" in out

    def test_run_calls_subprocess(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_run 内部调用 subprocess.run（check=False, text=True）。"""
        import fcmd.cli.autofmt as autofmt_mod

        captured: dict[str, Any] = {}

        def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(autofmt_mod.subprocess, "run", fake_run)
        result = autofmt_mod._run(["echo", "hi"])
        assert result.returncode == 0
        assert captured["cmd"] == ["echo", "hi"]
        assert captured["kwargs"]["check"] is False
        assert captured["kwargs"]["text"] is True


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
        monkeypatch.setattr("fcmd.cli.bumpversion._run", _success_run)
        result = bump_project_version(part="patch")
        assert result is None
        out = capsys.readouterr().out
        assert "未能从任何文件读取版本号" in out

    def test_run_calls_subprocess(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_run 内部调用 subprocess.run（check=False, capture_output=True, text=True）。"""
        import fcmd.cli.bumpversion as bumpversion_mod

        captured: dict[str, Any] = {}

        def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(bumpversion_mod.subprocess, "run", fake_run)
        result = bumpversion_mod._run(["git", "status"])
        assert result.returncode == 0
        assert captured["cmd"] == ["git", "status"]
        assert captured["kwargs"]["check"] is False
        assert captured["kwargs"]["capture_output"] is True
        assert captured["kwargs"]["text"] is True


# ============================================================================ #
# filelevel 循环分支补充测试
# ============================================================================ #
class TestFilelevelBranches:
    """filelevel 循环分支补充测试。"""

    def test_process_files_level_empty_list(self) -> None:
        """process_files_level 空列表不报错。"""
        process_files_level([], level=1)  # 不抛异常即可

    def test_process_files_level_mixed_existence(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """process_files_level 部分文件不存在时跳过。"""
        f1 = tmp_path / "exists.pdf"
        f1.write_text("content")
        f2 = tmp_path / "missing.pdf"
        process_files_level([f1, f2], level=2)
        out = capsys.readouterr().out
        assert "文件不存在" in out
        assert (tmp_path / "exists(INT).pdf").exists()
