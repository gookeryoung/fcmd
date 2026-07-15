"""pymake 模块测试（@fx.tool 注册验证 + CLI 调度）。

验证 ``fcmd.cli.pymake`` 模块通过 ``@fx.tool`` 装饰器注册的子命令集合：
- 单任务别名（b/sync/c/t/tf/lint/fmt/fmtc/bumpmi/bumpma/doc/tox）
- 内部 hidden job（pyrefly_check/test_coverage/bumpversion/git_add_all/git_push/git_push_tags/twine_publish）
- 聚合 job（tc/cov/bump/p/pb/all）
- CLI 调度（dry-run 验证执行计划）
- fn 任务（c 清理函数）实际执行
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import fcmd as fx
import fcmd.cli.pymake  # 导入触发 @fx.tool 注册
from fcmd.apis.toolkit import _TOOL_REGISTRY, get_tool, run_tool
from fcmd.cli.main import FcmdApp


# ---------------------------------------------------------------------- #
# @fx.tool 注册验证
# ---------------------------------------------------------------------- #
class TestPymakeRegistration:
    """``pymake`` 模块 ``@fx.tool`` 注册验证。"""

    def test_pymake_registered(self) -> None:
        """pymake 应在 _TOOL_REGISTRY 中注册。"""
        assert "pymake" in _TOOL_REGISTRY

    def test_visible_subcommands(self) -> None:
        """可见子命令应包含核心构建/测试/检查命令。"""
        subs = fx.list_subcommands("pymake")
        for name in (
            "b",
            "sync",
            "c",
            "t",
            "tf",
            "lint",
            "fmt",
            "fmtc",
            "bumpmi",
            "bumpma",
            "doc",
            "tox",
            "tc",
            "cov",
            "bump",
            "p",
            "pb",
            "all",
        ):
            assert name in subs, f"可见子命令应包含 {name!r}"

    def test_hidden_subcommands_excluded(self) -> None:
        """hidden 子命令不应出现在可见列表中。"""
        subs = fx.list_subcommands("pymake")
        for name in (
            "pyrefly_check",
            "test_coverage",
            "bumpversion",
            "git_add_all",
            "git_push",
            "git_push_tags",
            "twine_publish",
        ):
            assert name not in subs, f"hidden 子命令 {name!r} 不应出现在可见列表"

    def test_hidden_subcommands_included_with_flag(self) -> None:
        """include_hidden=True 时 hidden 子命令应出现。"""
        subs = fx.list_subcommands("pymake", include_hidden=True)
        for name in (
            "pyrefly_check",
            "test_coverage",
            "bumpversion",
            "git_add_all",
            "git_push",
            "git_push_tags",
            "twine_publish",
        ):
            assert name in subs, f"hidden 子命令 {name!r} 应在 include_hidden=True 时出现"


# ---------------------------------------------------------------------- #
# 单任务别名 cmd 验证
# ---------------------------------------------------------------------- #
class TestPymakeCmdTasks:
    """单任务别名（有 cmd）的 cmd 内容验证。"""

    @pytest.mark.parametrize(
        ("sub", "cmd_fragment"),
        [
            ("b", "uv"),
            ("sync", "uv"),
            ("t", "pytest"),
            ("tf", "pytest"),
            ("lint", "ruff"),
            ("fmt", "ruff"),
            ("fmtc", "ruff"),
            ("bumpmi", "bump-my-version"),
            ("bumpma", "bump-my-version"),
            ("tox", "tox"),
        ],
    )
    def test_cmd_has_expected_fragment(self, sub: str, cmd_fragment: str) -> None:
        """单任务 cmd 应包含预期命令片段。"""
        spec = get_tool("pymake", sub)
        assert spec.cmd is not None, f"{sub} 应有 cmd"
        assert cmd_fragment in spec.cmd, f"{sub}.cmd 应包含 {cmd_fragment!r}: {spec.cmd}"

    def test_b_cmd_is_uv_build(self) -> None:
        """b 应为 uv build。"""
        spec = get_tool("pymake", "b")
        assert spec.cmd == ("uv", "build")

    def test_sync_cmd_uses_extra_dev(self) -> None:
        """sync 应使用 --extra dev。"""
        spec = get_tool("pymake", "sync")
        assert spec.cmd is not None
        assert "--extra" in spec.cmd
        assert "dev" in spec.cmd

    def test_t_cmd_excludes_slow(self) -> None:
        """t 应排除 slow 标记。"""
        spec = get_tool("pymake", "t")
        assert spec.cmd is not None
        assert "-m" in spec.cmd
        assert "not slow" in spec.cmd

    def test_tf_cmd_has_x_flag(self) -> None:
        """tf 应有 -x（首个失败即停止）。"""
        spec = get_tool("pymake", "tf")
        assert spec.cmd is not None
        assert "-x" in spec.cmd

    def test_lint_cmd_has_fix(self) -> None:
        """lint 应有 --fix。"""
        spec = get_tool("pymake", "lint")
        assert spec.cmd is not None
        assert "--fix" in spec.cmd

    def test_fmt_cmd_no_check(self) -> None:
        """fmt 不应有 --check（实际格式化）。"""
        spec = get_tool("pymake", "fmt")
        assert spec.cmd is not None
        assert "format" in spec.cmd
        assert "--check" not in spec.cmd

    def test_fmtc_cmd_has_check(self) -> None:
        """fmtc 应有 --check（仅检查不修改）。"""
        spec = get_tool("pymake", "fmtc")
        assert spec.cmd is not None
        assert "--check" in spec.cmd

    def test_bumpmi_cmd_uses_minor(self) -> None:
        """bumpmi 应使用 minor。"""
        spec = get_tool("pymake", "bumpmi")
        assert spec.cmd is not None
        assert "minor" in spec.cmd

    def test_bumpma_cmd_uses_major(self) -> None:
        """bumpma 应使用 major。"""
        spec = get_tool("pymake", "bumpma")
        assert spec.cmd is not None
        assert "major" in spec.cmd

    def test_c_is_fn_task_not_cmd(self) -> None:
        """c 是 fn 任务（无 cmd，有函数体）。"""
        spec = get_tool("pymake", "c")
        assert spec.cmd is None
        assert spec.needs == ()


# ---------------------------------------------------------------------- #
# hidden job 验证
# ---------------------------------------------------------------------- #
class TestPymakeHiddenJobs:
    """内部 hidden job 验证。"""

    def test_pyrefly_check_cmd(self) -> None:
        """pyrefly_check 应执行 pyrefly check。"""
        spec = get_tool("pymake", "pyrefly_check")
        assert spec.cmd is not None
        assert "pyrefly" in spec.cmd
        assert spec.hidden is True

    def test_test_coverage_cmd(self) -> None:
        """test_coverage 应使用 --cov=fcmd --cov-fail-under=95。"""
        spec = get_tool("pymake", "test_coverage")
        assert spec.cmd is not None
        assert "--cov=fcmd" in spec.cmd
        assert "--cov-fail-under=95" in spec.cmd
        assert spec.hidden is True

    def test_test_coverage_needs_c(self) -> None:
        """test_coverage 应依赖 c（先清理）。"""
        spec = get_tool("pymake", "test_coverage")
        assert "c" in spec.needs

    def test_bumpversion_cmd_uses_patch(self) -> None:
        """bumpversion 应使用 patch。"""
        spec = get_tool("pymake", "bumpversion")
        assert spec.cmd is not None
        assert "patch" in spec.cmd
        assert spec.hidden is True

    def test_bumpversion_needs_git_add_all(self) -> None:
        """bumpversion 应依赖 git_add_all。"""
        spec = get_tool("pymake", "bumpversion")
        assert "git_add_all" in spec.needs

    def test_git_add_all_needs_tc(self) -> None:
        """git_add_all 应依赖 tc（先通过类型检查）。"""
        spec = get_tool("pymake", "git_add_all")
        assert "tc" in spec.needs
        assert spec.hidden is True

    def test_git_push_cmd(self) -> None:
        """git_push 应为 git push。"""
        spec = get_tool("pymake", "git_push")
        assert spec.cmd == ("git", "push")
        assert spec.hidden is True

    def test_git_push_tags_cmd(self) -> None:
        """git_push_tags 应为 git push --tags。"""
        spec = get_tool("pymake", "git_push_tags")
        assert spec.cmd == ("git", "push", "--tags")
        assert spec.hidden is True

    def test_twine_publish_cmd(self) -> None:
        """twine_publish 应执行 twine upload。"""
        spec = get_tool("pymake", "twine_publish")
        assert spec.cmd is not None
        assert "twine" in spec.cmd
        assert "upload" in spec.cmd
        assert spec.hidden is True


# ---------------------------------------------------------------------- #
# 聚合 job 验证
# ---------------------------------------------------------------------- #
class TestPymakeAggregateJobs:
    """聚合任务（有 needs 无 cmd 无函数逻辑）验证。"""

    @pytest.mark.parametrize(
        ("sub", "expected_needs"),
        [
            ("tc", ("c", "pyrefly_check", "lint")),
            ("cov", ("test_coverage",)),
            ("bump", ("bumpversion",)),
            ("p", ("c", "git_push", "git_push_tags")),
            ("pb", ("twine_publish",)),
            ("all", ("c", "b", "t", "tc")),
        ],
    )
    def test_aggregate_needs(self, sub: str, expected_needs: tuple[str, ...]) -> None:
        """聚合任务的 needs 应包含所有预期依赖。"""
        spec = get_tool("pymake", sub)
        for dep in expected_needs:
            assert dep in spec.needs, f"{sub} 应依赖 {dep!r}: {spec.needs}"

    @pytest.mark.parametrize("sub", ["tc", "cov", "bump", "p", "pb", "all"])
    def test_aggregate_has_no_cmd(self, sub: str) -> None:
        """聚合任务应无 cmd。"""
        spec = get_tool("pymake", sub)
        assert spec.cmd is None, f"{sub} 应为聚合任务（无 cmd）"

    def test_tc_strategy_is_thread(self) -> None:
        """tc 应使用 thread 策略（c/pyrefly/lint 可并行）。"""
        spec = get_tool("pymake", "tc")
        assert spec.strategy == "thread"

    def test_p_strategy_is_thread(self) -> None:
        """p 应使用 thread 策略（push + push tags 可并行）。"""
        spec = get_tool("pymake", "p")
        assert spec.strategy == "thread"

    def test_all_strategy_is_dependency(self) -> None:
        """all 应使用 dependency 策略（最大化并行）。"""
        spec = get_tool("pymake", "all")
        assert spec.strategy == "dependency"


# ---------------------------------------------------------------------- #
# CLI 调度测试（dry-run，不执行真实命令）
# ---------------------------------------------------------------------- #
class TestPymakeCliDispatch:
    """``fcmd pymake`` CLI 调度测试（不执行真实命令）。"""

    def test_pymake_no_subcommand_lists(self) -> None:
        """fcmd pymake 列出子命令，返回 0。"""
        app = FcmdApp(["pymake"])
        assert app.run() == 0

    def test_pymake_t_dry_run(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd pymake t --dry-run 打印执行计划。"""
        code = run_tool("pymake", ["t", "--dry-run"])
        assert code == 0
        out = capsys.readouterr().out
        assert "Dry run" in out
        assert "t" in out

    def test_pymake_tc_dry_run(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd pymake tc --dry-run 打印聚合执行计划。"""
        code = run_tool("pymake", ["tc", "--dry-run"])
        assert code == 0
        out = capsys.readouterr().out
        assert "Dry run" in out
        # tc 依赖 c + pyrefly_check + lint
        assert "c" in out
        assert "lint" in out
        assert "pyrefly_check" in out

    def test_pymake_all_dry_run(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd pymake all --dry-run 打印全套流程执行计划。"""
        code = run_tool("pymake", ["all", "--dry-run"])
        assert code == 0
        out = capsys.readouterr().out
        assert "Dry run" in out
        # all 依赖 c + b + t + tc
        for name in ("b", "c", "t", "tc"):
            assert name in out, f"all 执行计划应包含 {name!r}"

    def test_pymake_bump_dry_run(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd pymake bump --dry-run 打印版本升级执行计划。"""
        code = run_tool("pymake", ["bump", "--dry-run"])
        assert code == 0
        out = capsys.readouterr().out
        assert "Dry run" in out
        # bump → bumpversion → git_add_all → tc → (c, pyrefly_check, lint)
        assert "bumpversion" in out

    def test_pymake_cov_dry_run(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd pymake cov --dry-run 打印覆盖率测试执行计划。"""
        code = run_tool("pymake", ["cov", "--dry-run"])
        assert code == 0
        out = capsys.readouterr().out
        assert "Dry run" in out
        assert "test_coverage" in out

    def test_pymake_p_dry_run(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd pymake p --dry-run 打印推送执行计划。"""
        code = run_tool("pymake", ["p", "--dry-run"])
        assert code == 0
        out = capsys.readouterr().out
        assert "Dry run" in out
        assert "git_push" in out

    def test_pymake_unknown_subcommand(self) -> None:
        """fcmd pymake unknown 返回 FAILURE。"""
        code = run_tool("pymake", ["unknown_subcommand"])
        assert code == 1

    def test_pymake_b_dry_run_via_app(self) -> None:
        """FcmdApp 路由 pymake b --dry-run 返回 0。"""
        app = FcmdApp(["pymake", "b", "--dry-run"])
        assert app.run() == 0

    def test_pm_alias_works(self) -> None:
        """pm 别名路由到 pymake。"""
        app = FcmdApp(["pm", "t", "--dry-run"])
        assert app.run() == 0


# ---------------------------------------------------------------------- #
# fn 任务实际执行测试（c 清理函数）
# ---------------------------------------------------------------------- #
class TestPymakeCleanFn:
    """``c`` fn 任务的实际执行测试（验证清理逻辑）。"""

    def test_c_removes_pycache_dirs(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """c 应清理 src/tests 下的 __pycache__ 目录。"""
        src_pycache = tmp_path / "src" / "__pycache__"
        tests_pycache = tmp_path / "tests" / "__pycache__"
        src_pycache.mkdir(parents=True)
        tests_pycache.mkdir(parents=True)
        (src_pycache / "x.pyc").write_text("")
        (tests_pycache / "y.pyc").write_text("")
        monkeypatch.chdir(tmp_path)
        fcmd.cli.pymake.c()
        assert not src_pycache.exists()
        assert not tests_pycache.exists()

    def test_c_removes_build_and_cache_dirs(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """c 应清理 build/dist/htmlcov 等缓存目录。"""
        for d in ("build", "dist", "htmlcov", ".tox", ".ruff_cache", ".pyrefly_cache", ".mypy_cache", ".pytest_cache"):
            (tmp_path / d).mkdir()
        monkeypatch.chdir(tmp_path)
        fcmd.cli.pymake.c()
        for d in ("build", "dist", "htmlcov", ".tox", ".ruff_cache", ".pyrefly_cache", ".mypy_cache", ".pytest_cache"):
            assert not (tmp_path / d).exists(), f"{d} 应被清理"

    def test_c_removes_egg_info(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """c 应清理 src/tests 下的 *.egg-info。"""
        egg = tmp_path / "src" / "fcmd.egg-info"
        egg.mkdir(parents=True)
        (egg / "PKG-INFO").write_text("")
        monkeypatch.chdir(tmp_path)
        fcmd.cli.pymake.c()
        assert not egg.exists()

    def test_c_idempotent_on_clean_dir(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """c 在已清理的目录上运行不应报错。"""
        monkeypatch.chdir(tmp_path)
        fcmd.cli.pymake.c()  # 不抛异常即可


# ---------------------------------------------------------------------- #
# main() 入口测试
# ---------------------------------------------------------------------- #
class TestPymakeMain:
    """``pymake.main()`` 入口测试。"""

    def test_main_dry_run_exits_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """main() 通过 SystemExit(0) 退出（--dry-run）。"""
        monkeypatch.setattr(sys, "argv", ["pymake", "t", "--dry-run"])
        with pytest.raises(SystemExit) as exc_info:
            fcmd.cli.pymake.main()
        assert exc_info.value.code == 0

    def test_main_unknown_subcommand_exits_nonzero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """main() 未知子命令通过 SystemExit(1) 退出。"""
        monkeypatch.setattr(sys, "argv", ["pymake", "unknown_subcommand"])
        with pytest.raises(SystemExit) as exc_info:
            fcmd.cli.pymake.main()
        assert exc_info.value.code == 1
