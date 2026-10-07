"""gittool 工具测试。

验证 ``gittool`` 工具（clean/c/ca/p/pl 与链式 a/i 由 ``src/fcmd/commands/gittool.toml``
DSL 声明，与 Python 模块 isub 合并注册；a/i 经 when 守卫链式内部子命令
``_init``/``_add``/``_commit`` 编排）：
- 工具注册与 cmd 子命令规格（clean/c/ca/p/pl）
- 提交（a / i 子命令：守卫跳过与链式提交）
- DSL 链式规格（_init/_add/_commit 的 hidden/needs/守卫/豁免）
- isub 子命令（初始化子目录 Git 仓库）
"""

from __future__ import annotations

import inspect
import subprocess
from pathlib import Path

import pytest

import fcmd.cli.dev.gittool  # 触发 @fx.tool 注册（isub/main）
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli._discovery import ensure_tools_discovered

# 触发工具发现：Python 模块扫描 + DSL 声明注册（幂等）
ensure_tools_discovered()


# ---------------------------------------------------------------------- #
# gittool 工具测试
# ---------------------------------------------------------------------- #
class TestGittool:
    """``gittool`` 工具测试。"""

    def test_clean_cmd_excludes_dirs(self) -> None:
        """clean 的 cmd 展开为 -e dir1 -e dir2 ...（排除编辑器/项目缓存目录）。"""
        cmd = _TOOL_REGISTRY["gittool"]["clean"].cmd
        assert cmd is not None
        assert cmd[:3] == ("git", "clean", "-xfd")
        # -e 成对出现，排除目录含 .venv / node_modules 等缓存
        excludes = [cmd[i + 1] for i, item in enumerate(cmd) if item == "-e"]
        assert len(excludes) == len(cmd) - 3 - len(excludes)
        assert ".venv" in excludes
        assert "node_modules" in excludes
        assert ".git" in excludes

    def test_gittool_a_no_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """gittool a 无更改时守卫跳过提交，提示条件不满足。"""
        monkeypatch.chdir(tmp_path)
        subprocess.run(["git", "init"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "test"], check=True, capture_output=True)
        code = run_tool("gittool", ["a"])
        assert code == 0
        out = capsys.readouterr().out
        assert "条件不满足" in out

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
        """gittool i 在已有仓库且无更改时守卫跳过提交，提示条件不满足。"""
        monkeypatch.chdir(tmp_path)
        subprocess.run(["git", "init"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "test"], check=True, capture_output=True)
        code = run_tool("gittool", ["i"])
        assert code == 0
        out = capsys.readouterr().out
        assert "条件不满足" in out

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

    def test_gittool_ca_removes_excluded_dirs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """gittool ca 会清理 .venv 等排除目录（ca 不保留任何排除项）。"""
        monkeypatch.chdir(tmp_path)
        subprocess.run(["git", "init"], check=True, capture_output=True)
        # 放入排除目录中的一个（.venv）以及一个普通未跟踪文件
        (tmp_path / ".venv").mkdir()
        (tmp_path / ".venv" / "pyvenv.cfg").write_text("home = /usr/bin", encoding="utf-8")
        (tmp_path / "extra.log").write_text("junk", encoding="utf-8")
        assert (tmp_path / ".venv").is_dir()
        assert (tmp_path / "extra.log").is_file()
        code = run_tool("gittool", ["ca"])
        assert code == 0
        # 两个都应该被删除
        assert not (tmp_path / ".venv").exists(), "ca 应删除 .venv 排除目录"
        assert not (tmp_path / "extra.log").exists(), "ca 应删除普通未跟踪文件"


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

    def test_ca_is_cmd_without_exclude(self) -> None:
        """ca 是 cmd 类型子命令，直接 git clean -xfd . 不含 -e 排除。"""
        from fcmd.apis.toolkit import _TOOL_REGISTRY

        spec = _TOOL_REGISTRY["gittool"]["ca"]
        assert spec.cmd is not None
        assert spec.cmd == ("git", "clean", "-xfd", ".")
        assert "-e" not in spec.cmd
        assert spec.hidden is not True  # ca 不是隐藏命令


# ============================================================================ #
# gittool DSL 链式规格（a/i 守卫链）
# ============================================================================ #
class TestGittoolDslChain:
    """gittool a/i 的链式内部子命令（_init/_add/_commit）规格与行为验证。"""

    def test_init_guard_hidden(self) -> None:
        """_init 是 hidden 子命令，when 路径探针守卫（.git 缺失时才执行）。"""
        spec = _TOOL_REGISTRY["gittool"]["_init"]
        assert spec.cmd == ("git", "init")
        assert spec.hidden is True
        when = spec.func.__dsl_when__  # type: ignore[missing-attribute]
        assert when.path == ".git"
        assert when.expect == "missing"

    def test_add_needs_init_and_exempt(self) -> None:
        """_add 依赖 _init 且豁免上游跳过（仓库已存在时仍可暂存）。"""
        spec = _TOOL_REGISTRY["gittool"]["_add"]
        assert "_init" in spec.needs
        assert spec.cmd == ("git", "add", ".")
        assert spec.allow_upstream_skip is True
        assert spec.hidden is True

    def test_commit_guard_needs_add(self) -> None:
        """_commit 依赖 _add，when 命令探针守卫（有更改才提交）。"""
        spec = _TOOL_REGISTRY["gittool"]["_commit"]
        assert "_add" in spec.needs
        assert spec.cmd == ("git", "commit", "-m", "{message}")
        assert spec.allow_upstream_skip is True
        assert spec.hidden is True
        when = spec.func.__dsl_when__  # type: ignore[missing-attribute]
        assert when.cmd == "git status --porcelain"
        assert when.expect == "nonempty"

    def test_aggregate_a_needs_commit_with_message(self) -> None:
        """a 是聚合命令（needs _commit、无 cmd），声明 message 参数供链内插值。"""
        spec = _TOOL_REGISTRY["gittool"]["a"]
        assert spec.cmd is None
        assert "_commit" in spec.needs
        assert "message" in inspect.signature(spec.func).parameters

    def test_a_inits_fresh_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """gittool a 在全新目录（无仓库）也会 init + 提交（链式共享 _init）。"""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("GIT_AUTHOR_NAME", "test")
        monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@test.com")
        monkeypatch.setenv("GIT_COMMITTER_NAME", "test")
        monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@test.com")
        (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
        code = run_tool("gittool", ["a"])
        assert code == 0
        assert (tmp_path / ".git").is_dir()
        result = subprocess.run(["git", "log", "--oneline"], capture_output=True, text=True, check=True)
        assert "chore: update" in result.stdout


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
        fcmd.cli.dev.gittool.git_init_sub_dirs()
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

        monkeypatch.setattr("fcmd.cli.dev.gittool.subprocess.run", fake_run)

        fcmd.cli.dev.gittool.git_init_sub_dirs()
        captured = capsys.readouterr()
        assert "已初始化: proj_a" in captured.out
        assert "已初始化: proj_b" in captured.out
        # 每个子目录 3 次 git 命令，共 6 次
        assert len(calls) == 6
        # 验证 cwd 正确设置
        proj_a_cwd = tmp_path / "proj_a"
        proj_a_calls = [c for c in calls if c[1] == proj_a_cwd]
        assert len(proj_a_calls) == 3  # init + add + commit
