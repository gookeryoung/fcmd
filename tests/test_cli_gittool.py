"""gittool 工具测试。

验证 ``fcmd.cli.gittool`` 模块：
- 工具注册与 cmd 子命令规格（clean/c/p/pl）
- 状态查询（has_files / not_has_git_repo）
- 提交（a / i 子命令）
- 推送/拉取（p / pl 子命令规格）
- isub 子命令（初始化子目录 Git 仓库）
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import fcmd.cli.gittool  # 触发 @fx.tool 注册
from fcmd.apis.toolkit import run_tool
from fcmd.cli.gittool import EXCLUDE_CMDS, EXCLUDE_DIRS, has_files, not_has_git_repo


# ---------------------------------------------------------------------- #
# gittool 工具测试
# ---------------------------------------------------------------------- #
class TestGittool:
    """``gittool`` 工具测试。"""

    def test_exclude_cmds_format(self) -> None:
        """EXCLUDE_CMDS 展开为 -e dir1 -e dir2 ... 格式。"""
        # 应为偶数长度，每对以 -e 开头
        assert len(EXCLUDE_CMDS) == len(EXCLUDE_DIRS) * 2
        for i in range(0, len(EXCLUDE_CMDS), 2):
            assert EXCLUDE_CMDS[i] == "-e"
            assert EXCLUDE_CMDS[i + 1] in EXCLUDE_DIRS

    def test_not_has_git_repo_true(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """非 git 目录返回 True。"""
        monkeypatch.chdir(tmp_path)
        assert not_has_git_repo() is True

    def test_not_has_git_repo_false(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """git 仓库目录返回 False。"""
        monkeypatch.chdir(tmp_path)
        subprocess.run(["git", "init"], check=True, capture_output=True)
        assert not_has_git_repo() is False

    def test_has_files_clean(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """干净的 git 仓库返回 False。"""
        monkeypatch.chdir(tmp_path)
        subprocess.run(["git", "init"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "test"], check=True, capture_output=True)
        assert has_files() is False

    def test_has_files_dirty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """有未提交更改返回 True。"""
        monkeypatch.chdir(tmp_path)
        subprocess.run(["git", "init"], check=True, capture_output=True)
        (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
        assert has_files() is True

    def test_gittool_a_no_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """gittool a 没有文件时打印提示。"""
        monkeypatch.chdir(tmp_path)
        subprocess.run(["git", "init"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "test"], check=True, capture_output=True)
        code = run_tool("gittool", ["a"])
        assert code == 0
        out = capsys.readouterr().out
        assert "没有文件需要提交" in out

    def test_gittool_a_commit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """gittool a 添加并提交文件。"""
        monkeypatch.chdir(tmp_path)
        subprocess.run(["git", "init"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "test"], check=True, capture_output=True)
        (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
        code = run_tool("gittool", ["a", "--message", "test commit"])
        assert code == 0
        # 验证提交成功
        result = subprocess.run(["git", "log", "--oneline"], capture_output=True, text=True, check=True)
        assert "test commit" in result.stdout

    def test_gittool_i_init(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """gittool i 初始化并提交。"""
        monkeypatch.chdir(tmp_path)
        # CI 环境可能未配置全局 git user，通过环境变量设置（不依赖 .git/config）
        monkeypatch.setenv("GIT_AUTHOR_NAME", "test")
        monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@test.com")
        monkeypatch.setenv("GIT_COMMITTER_NAME", "test")
        monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@test.com")
        (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
        code = run_tool("gittool", ["i"])
        assert code == 0
        # 验证仓库已初始化且提交成功
        assert (tmp_path / ".git").is_dir()
        result = subprocess.run(["git", "log", "--oneline"], capture_output=True, text=True, check=True)
        assert "init commit" in result.stdout

    def test_gittool_i_existing_repo_no_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """gittool i 在已有仓库且无更改时打印提示。"""
        monkeypatch.chdir(tmp_path)
        subprocess.run(["git", "init"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "test"], check=True, capture_output=True)
        code = run_tool("gittool", ["i"])
        assert code == 0
        out = capsys.readouterr().out
        assert "没有文件需要提交" in out

    def test_gittool_a_via_run_tool_default_message(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """gittool a 使用默认提交信息。"""
        monkeypatch.chdir(tmp_path)
        subprocess.run(["git", "init"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "test"], check=True, capture_output=True)
        (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
        code = run_tool("gittool", ["a"])
        assert code == 0
        result = subprocess.run(["git", "log", "--oneline"], capture_output=True, text=True, check=True)
        assert "chore: update" in result.stdout


# ---------------------------------------------------------------------- #
# gittool cmd 子命令验证
# ---------------------------------------------------------------------- #
class TestGittoolCmdSpecs:
    """gittool 的 cmd 类型子命令规格验证。"""

    def test_clean_is_hidden_cmd(self) -> None:
        """clean 是 hidden 的 cmd 类型子命令。"""
        from fcmd.apis.toolkit import _TOOL_REGISTRY

        spec = _TOOL_REGISTRY["gittool"]["clean"]
        assert spec.cmd is not None
        assert "git" in spec.cmd
        assert "clean" in spec.cmd
        assert "-xfd" in spec.cmd
        assert spec.hidden is True

    def test_c_needs_clean(self) -> None:
        """c 子命令依赖 clean。"""
        from fcmd.apis.toolkit import _TOOL_REGISTRY

        spec = _TOOL_REGISTRY["gittool"]["c"]
        assert "clean" in spec.needs

    def test_p_is_cmd(self) -> None:
        """p 是 cmd 类型子命令。"""
        from fcmd.apis.toolkit import _TOOL_REGISTRY

        spec = _TOOL_REGISTRY["gittool"]["p"]
        assert spec.cmd is not None
        assert "push" in spec.cmd

    def test_pl_is_cmd(self) -> None:
        """pl 是 cmd 类型子命令。"""
        from fcmd.apis.toolkit import _TOOL_REGISTRY

        spec = _TOOL_REGISTRY["gittool"]["pl"]
        assert spec.cmd is not None
        assert "pull" in spec.cmd


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
