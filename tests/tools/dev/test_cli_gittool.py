"""gittool 工具测试（普通 Python 工具模块）。

验证 ``gittool`` 工具：
- 工具注册与 cmd 子命令规格（clean/c/ca/p/pl）
- 提交（a / i 子命令：守卫 no-op 与链式提交）
- 隐藏守卫链规格（_init/_add/_commit 的 hidden/needs）
- isub 子命令（初始化子目录 Git 仓库）
"""

from __future__ import annotations

import inspect
import subprocess
from pathlib import Path

import pytest

import fcmd.tools.dev.gittool  # 触发 @fx.tool 注册（isub/main）
from fcmd.cli._discovery import _TOOL_ALIASES, ensure_tools_discovered
from fcmd.core.toolkit import _TOOL_REGISTRY, run_tool

# 触发工具发现：Python 模块扫描（幂等）
ensure_tools_discovered()


# ---------------------------------------------------------------------- #
# gittool 工具测试
# ---------------------------------------------------------------------- #
class TestGittool:
    """``gittool`` 工具测试。"""

    def test_aliases(self) -> None:
        """gitt / git 别名均指向 gittool。"""
        assert _TOOL_ALIASES["gitt"] == "gittool"
        assert _TOOL_ALIASES["git"] == "gittool"

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
        """gittool a 无更改时提交守卫 no-op（不产生提交）。"""
        monkeypatch.chdir(tmp_path)
        subprocess.run(["git", "init"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "test"], check=True, capture_output=True)
        code = run_tool("gittool", ["a"])
        assert code == 0
        head = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], check=False, capture_output=True)
        assert head.returncode != 0

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
        """gittool i 在已有仓库且无更改时提交守卫 no-op（不产生提交）。"""
        monkeypatch.chdir(tmp_path)
        subprocess.run(["git", "init"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "test"], check=True, capture_output=True)
        code = run_tool("gittool", ["i"])
        assert code == 0
        head = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], check=False, capture_output=True)
        assert head.returncode != 0

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
        from fcmd.core.toolkit import _TOOL_REGISTRY

        spec = _TOOL_REGISTRY["gittool"]["clean"]
        assert spec.cmd is not None
        assert "git" in spec.cmd
        assert "clean" in spec.cmd
        assert "-xfd" in spec.cmd
        assert spec.hidden is True

    def test_c_needs_clean(self) -> None:
        """c 子命令依赖 clean。"""
        from fcmd.core.toolkit import _TOOL_REGISTRY

        spec = _TOOL_REGISTRY["gittool"]["c"]
        assert "clean" in spec.needs

    def test_p_is_cmd(self) -> None:
        """p 是 cmd 类型子命令。"""
        from fcmd.core.toolkit import _TOOL_REGISTRY

        spec = _TOOL_REGISTRY["gittool"]["p"]
        assert spec.cmd is not None
        assert "push" in spec.cmd

    def test_pl_is_cmd(self) -> None:
        """pl 是 cmd 类型子命令。"""
        from fcmd.core.toolkit import _TOOL_REGISTRY

        spec = _TOOL_REGISTRY["gittool"]["pl"]
        assert spec.cmd is not None
        assert "pull" in spec.cmd

    def test_ca_is_cmd_without_exclude(self) -> None:
        """ca 是 cmd 类型子命令，直接 git clean -xfd . 不含 -e 排除。"""
        from fcmd.core.toolkit import _TOOL_REGISTRY

        spec = _TOOL_REGISTRY["gittool"]["ca"]
        assert spec.cmd is not None
        assert spec.cmd == ("git", "clean", "-xfd", ".")
        assert "-e" not in spec.cmd
        assert spec.hidden is not True  # ca 不是隐藏命令


# ============================================================================ #
# gittool 步骤链规格（a/i）
# ============================================================================ #
class TestGittoolChain:
    """gittool a/i 的步骤声明（steps / preconditions）与行为验证。"""

    def test_init_is_hidden_fn_with_internal_create(self) -> None:
        """_init 是 hidden fn 子命令，create 是内部参数（不进 a/i 命令行）。"""
        spec = _TOOL_REGISTRY["gittool"]["_init"]
        assert spec.cmd is None
        assert spec.hidden is True
        assert spec.func.__name__ == "git_init"
        assert inspect.signature(spec.func).parameters["create"].default is False

    def test_add_and_commit_are_plain_steps(self) -> None:
        """_add / _commit 不再各自写 needs，顺序由聚合入口的 steps 声明。"""
        add = _TOOL_REGISTRY["gittool"]["_add"]
        commit = _TOOL_REGISTRY["gittool"]["_commit"]
        assert add.needs == ()
        assert commit.needs == ()
        assert add.cmd == ("git", "add", ".")
        assert add.hidden is True
        assert commit.hidden is True
        assert "message" in inspect.signature(commit.func).parameters

    def test_aggregate_a_steps_and_precondition(self) -> None:
        """a 只做 add + commit，并用 preconditions 要求已在 Git 仓库内。"""
        spec = _TOOL_REGISTRY["gittool"]["a"]
        assert spec.cmd is None
        assert spec.aggregate is True
        assert spec.needs == ()
        assert [item.task for item in spec.steps] == ["_add", "_commit"]
        assert [check.__name__ for check in spec.preconditions] == ["_require_repo"]
        params = inspect.signature(spec.func).parameters
        assert "message" in params
        assert "init" not in params

    def test_aggregate_i_steps_inject_create_only_to_init(self) -> None:
        """i 的 steps 才注入 create=True，且它只属于 _init 这一步。"""
        spec = _TOOL_REGISTRY["gittool"]["i"]
        assert spec.aggregate is True
        assert spec.needs == ()
        steps = [(item.task, dict(item.kwargs)) for item in spec.steps]
        assert steps == [("_init", {"create": True}), ("_add", {}), ("_commit", {})]
        assert "init" not in inspect.signature(spec.func).parameters

    def test_a_has_no_internal_init_flag(self) -> None:
        """a/i 的命令行只有 message，内部 create 开关不泄漏。"""
        for subcommand in ("a", "i"):
            params = inspect.signature(_TOOL_REGISTRY["gittool"][subcommand].func).parameters
            assert set(params) == {"message"}

    def test_a_init_flag_is_gone(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """a --init 已移除（内部 create 只由 i 的 steps 注入）。"""
        monkeypatch.chdir(tmp_path)
        code = run_tool("gittool", ["a", "--init"])
        assert code != 0
        assert not (tmp_path / ".git").exists()

    def test_a_requires_repo(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """gittool a 在非仓库目录直接报错，不会静默创建 .git。"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
        code = run_tool("gittool", ["a"])
        assert code != 0
        assert not (tmp_path / ".git").exists()

    def test_a_in_repo_subdir_does_not_create_nested_repo(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """在仓库子目录执行 a 时提交到外层仓库，不会 git init 出嵌套仓库。"""
        subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=tmp_path, check=True, capture_output=True)
        subdir = tmp_path / "sub"
        subdir.mkdir()
        (subdir / "a.txt").write_text("hello", encoding="utf-8")
        monkeypatch.chdir(subdir)
        code = run_tool("gittool", ["a"])
        assert code == 0
        assert not (subdir / ".git").exists(), "不应在子目录创建嵌套仓库"
        result = subprocess.run(["git", "log", "--oneline"], cwd=tmp_path, capture_output=True, text=True, check=True)
        assert "chore: update" in result.stdout


# ============================================================================ #
# gittool isub 测试
# ============================================================================ #
class TestGittoolIsub:
    """gittool isub 子命令测试。"""

    def test_isub_no_subdirs(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """无子目录（含只有隐藏目录）时打印提示。"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()
        fcmd.tools.dev.gittool.git_init_sub_dirs()
        captured = capsys.readouterr()
        assert "无子目录" in captured.out or "没有可初始化的子目录" in captured.out
        # .git / .venv 这类隐藏目录不被当成待初始化项目
        assert not (tmp_path / ".git" / ".git").exists()

    def test_isub_with_subdirs(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """有子目录时对每个子目录调用 git init/add/status/commit。"""
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
            stdout = "A  a.txt" if cmd[:2] == ["git", "status"] else ""
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=stdout, stderr="")

        monkeypatch.setattr("fcmd.tools.dev.gittool.subprocess.run", fake_run)

        fcmd.tools.dev.gittool.git_init_sub_dirs()
        captured = capsys.readouterr()
        assert "已初始化: proj_a" in captured.out
        assert "已初始化: proj_b" in captured.out
        # 每个子目录 4 次 git 命令（init / add / status / commit）
        assert len(calls) == 8
        proj_a_cwd = tmp_path / "proj_a"
        assert len([c for c in calls if c[1] == proj_a_cwd]) == 4

    def test_isub_skips_existing_repo(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """已存在 .git 的子目录不再重复 init / 提交。"""
        existing = tmp_path / "old_proj"
        (existing / ".git").mkdir(parents=True)
        (existing / "a.txt").write_text("hello", encoding="utf-8")
        (tmp_path / "new_proj").mkdir()
        monkeypatch.chdir(tmp_path)

        calls: list[Path] = []

        def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            cwd = kwargs.get("cwd")
            assert isinstance(cwd, Path)
            calls.append(cwd)
            stdout = "A  a.txt" if cmd[:2] == ["git", "status"] else ""
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=stdout, stderr="")

        monkeypatch.setattr("fcmd.tools.dev.gittool.subprocess.run", fake_run)
        fcmd.tools.dev.gittool.git_init_sub_dirs()
        captured = capsys.readouterr()
        assert "跳过: old_proj" in captured.out
        assert existing not in calls
        assert "已初始化: new_proj" in captured.out

    def test_isub_reports_failure(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """init 失败时不再打印「已初始化」，而是抛出汇总错误。"""
        (tmp_path / "bad_proj").mkdir()
        monkeypatch.chdir(tmp_path)

        def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args=cmd, returncode=128, stdout="", stderr="fatal: boom")

        monkeypatch.setattr("fcmd.tools.dev.gittool.subprocess.run", fake_run)
        with pytest.raises(RuntimeError, match="bad_proj"):
            fcmd.tools.dev.gittool.git_init_sub_dirs()
        captured = capsys.readouterr()
        assert "已初始化: bad_proj" not in captured.out
