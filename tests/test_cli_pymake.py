"""pymake 工具测试。

验证 ``fcmd.cli.dev.pymake`` 模块通过 ``@fx.tool`` 装饰器注册的子命令集合：
- 单 cmd 任务（b/sync/c/t/tf/ts/lint/bumpmi/bumpma/doc/tox）
- cmd + needs 混合任务（cov/bump）
- 聚合任务（chk/tc/push/upload）
- 内部 hidden job（fmt/fmtc/pyrefly_check/git_add_all/git_push/git_push_tags/twine_publish）
- CLI 调度（dry-run 验证执行计划）
"""

from __future__ import annotations

import sys

import pytest

import fcmd as fx
import fcmd.cli.dev.pymake  # 导入触发 @fx.tool 注册
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

    def test_tool_aliases(self) -> None:
        """pymake 应注册 pm 别名。"""
        assert fcmd.cli.dev.pymake.__tool_aliases__ == ["pm"]

    def test_visible_subcommands(self) -> None:
        """可见子命令应包含核心构建/测试/检查/发布命令。"""
        subs = fx.list_subcommands("pymake")
        for name in (
            "b",
            "sync",
            "c",
            "t",
            "tn",
            "tf",
            "ts",
            "cov",
            "lint",
            "chk",
            "tc",
            "bumpmi",
            "bumpma",
            "bump",
            "doc",
            "tox",
            "push",
            "upload",
        ):
            assert name in subs, f"可见子命令应包含 {name!r}"

    def test_hidden_subcommands_excluded(self) -> None:
        """hidden 子命令不应出现在可见列表中。"""
        subs = fx.list_subcommands("pymake")
        for name in (
            "fmt",
            "fmtc",
            "pyrefly_check",
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
            "fmt",
            "fmtc",
            "pyrefly_check",
            "git_add_all",
            "git_push",
            "git_push_tags",
            "twine_publish",
        ):
            assert name in subs, f"hidden 子命令 {name!r} 应在 include_hidden=True 时出现"


# ---------------------------------------------------------------------- #
# 单 cmd 任务验证
# ---------------------------------------------------------------------- #
class TestPymakeCmdTasks:
    """单 cmd 任务（无 needs）的 cmd 内容验证。"""

    @pytest.mark.parametrize(
        ("sub", "cmd_fragment"),
        [
            ("b", "uv"),
            ("sync", "uv"),
            ("c", "gitt"),
            ("t", "pytest"),
            ("tn", "pytest"),
            ("tf", "pytest"),
            ("ts", "pytest"),
            ("lint", "ruff"),
            ("fmt", "ruff"),
            ("fmtc", "ruff"),
            ("bumpmi", "bump-my-version"),
            ("bumpma", "bump-my-version"),
            ("tox", "tox"),
            ("doc", "sphinx-build"),
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

    def test_c_cmd_is_gitt_clean(self) -> None:
        """c 应为 gitt c（cmd 任务，非 fn 任务）。"""
        spec = get_tool("pymake", "c")
        assert spec.cmd == ("gitt", "c")
        assert spec.needs == ()

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

    def test_tn_cmd_uses_parallel(self) -> None:
        """tn 应使用 -n 8 并行执行。"""
        spec = get_tool("pymake", "tn")
        assert spec.cmd is not None
        assert "-n" in spec.cmd
        assert "8" in spec.cmd
        assert "not slow" in spec.cmd

    def test_tf_cmd_has_x_flag(self) -> None:
        """tf 应有 -x（首个失败即停止）。"""
        spec = get_tool("pymake", "tf")
        assert spec.cmd is not None
        assert "-x" in spec.cmd

    def test_ts_cmd_uses_slow_marker(self) -> None:
        """ts 应使用 -m slow。"""
        spec = get_tool("pymake", "ts")
        assert spec.cmd is not None
        assert "-m" in spec.cmd
        assert "slow" in spec.cmd
        assert "not slow" not in spec.cmd

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
        assert spec.hidden is True

    def test_fmtc_cmd_has_check(self) -> None:
        """fmtc 应有 --check（仅检查不修改）。"""
        spec = get_tool("pymake", "fmtc")
        assert spec.cmd is not None
        assert "--check" in spec.cmd
        assert spec.hidden is True

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

    def test_doc_cmd_uses_sphinx_build(self) -> None:
        """doc 应执行 sphinx-build html。"""
        spec = get_tool("pymake", "doc")
        assert spec.cmd is not None
        assert "sphinx-build" in spec.cmd
        assert "-b" in spec.cmd
        assert "html" in spec.cmd

    def test_tox_cmd_uses_uvx(self) -> None:
        """tox 应通过 uvx 调用。"""
        spec = get_tool("pymake", "tox")
        assert spec.cmd is not None
        assert "uvx" in spec.cmd
        assert "tox" in spec.cmd


# ---------------------------------------------------------------------- #
# cmd + needs 混合任务验证
# ---------------------------------------------------------------------- #
class TestPymakeHybridTasks:
    """cmd + needs 混合任务验证（既有命令又有依赖）。"""

    def test_cov_cmd_has_coverage_flags(self) -> None:
        """cov 应包含 --cov=fcmd 与 --cov-fail-under=95。"""
        spec = get_tool("pymake", "cov")
        assert spec.cmd is not None
        assert "--cov=fcmd" in spec.cmd
        assert "--cov-fail-under=95" in spec.cmd
        assert spec.hidden is False

    def test_cov_needs_c(self) -> None:
        """cov 应依赖 c（先清理）。"""
        spec = get_tool("pymake", "cov")
        assert "c" in spec.needs

    def test_bump_cmd_uses_patch(self) -> None:
        """bump 应使用 patch。"""
        spec = get_tool("pymake", "bump")
        assert spec.cmd is not None
        assert "patch" in spec.cmd

    def test_bump_has_no_needs(self) -> None:
        """bump 是纯 cmd 任务（无依赖）。"""
        spec = get_tool("pymake", "bump")
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

    def test_git_add_all_cmd_and_needs_chk(self) -> None:
        """git_add_all 应为 git add -A，并依赖 chk（先通过类型检查）。"""
        spec = get_tool("pymake", "git_add_all")
        assert spec.cmd == ("git", "add", "-A")
        assert spec.needs == ("chk",)
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
# 聚合任务验证
# ---------------------------------------------------------------------- #
class TestPymakeAggregateJobs:
    """聚合任务（有 needs 无 cmd）验证。"""

    @pytest.mark.parametrize(
        ("sub", "expected_needs"),
        [
            ("chk", ("pyrefly_check", "lint", "fmt", "tf")),
            ("tc", ("pyrefly_check", "lint", "fmt")),
            ("push", ("chk", "c", "git_push", "git_push_tags")),
            ("upload", ("twine_publish",)),
        ],
    )
    def test_aggregate_needs(self, sub: str, expected_needs: tuple[str, ...]) -> None:
        """聚合任务的 needs 应包含所有预期依赖。"""
        spec = get_tool("pymake", sub)
        for dep in expected_needs:
            assert dep in spec.needs, f"{sub} 应依赖 {dep!r}: {spec.needs}"

    @pytest.mark.parametrize("sub", ["chk", "tc", "push", "upload"])
    def test_aggregate_has_no_cmd(self, sub: str) -> None:
        """聚合任务应无 cmd。"""
        spec = get_tool("pymake", sub)
        assert spec.cmd is None, f"{sub} 应为聚合任务（无 cmd）"

    @pytest.mark.parametrize("sub", ["chk", "tc", "push"])
    def test_aggregate_strategy_is_thread(self, sub: str) -> None:
        """chk/tc/push 应使用 thread 策略（依赖可并行）。"""
        spec = get_tool("pymake", sub)
        assert spec.strategy == "thread"


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
        # tc 依赖 pyrefly_check + lint + fmt
        assert "lint" in out
        assert "pyrefly_check" in out
        assert "fmt" in out

    def test_pymake_chk_dry_run(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd pymake chk --dry-run 打印聚合执行计划（含 tf）。"""
        code = run_tool("pymake", ["chk", "--dry-run"])
        assert code == 0
        out = capsys.readouterr().out
        assert "Dry run" in out
        # chk 依赖 pyrefly_check + lint + fmt + tf
        assert "lint" in out
        assert "pyrefly_check" in out
        assert "fmt" in out
        assert "tf" in out

    def test_pymake_bump_dry_run(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd pymake bump --dry-run 打印版本升级执行计划（单任务）。"""
        code = run_tool("pymake", ["bump", "--dry-run"])
        assert code == 0
        out = capsys.readouterr().out
        assert "Dry run" in out
        # bump 是纯 cmd 任务（无依赖），执行计划仅含 bump
        assert "bump" in out

    def test_pymake_cov_dry_run(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd pymake cov --dry-run 打印覆盖率测试执行计划。"""
        code = run_tool("pymake", ["cov", "--dry-run"])
        assert code == 0
        out = capsys.readouterr().out
        assert "Dry run" in out
        # cov 直接是 cmd 任务，依赖 c
        assert "cov" in out
        assert "c" in out

    def test_pymake_push_dry_run(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd pymake push --dry-run 打印推送执行计划。"""
        code = run_tool("pymake", ["push", "--dry-run"])
        assert code == 0
        out = capsys.readouterr().out
        assert "Dry run" in out
        assert "git_push" in out
        assert "git_push_tags" in out

    def test_pymake_upload_dry_run(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd pymake upload --dry-run 打印 PyPI 发布执行计划。"""
        code = run_tool("pymake", ["upload", "--dry-run"])
        assert code == 0
        out = capsys.readouterr().out
        assert "Dry run" in out
        assert "twine_publish" in out

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
# main() 入口测试
# ---------------------------------------------------------------------- #
class TestPymakeMain:
    """``pymake.main()`` 入口测试。"""

    def test_main_dry_run_exits_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """main() 通过 SystemExit(0) 退出（--dry-run）。"""
        monkeypatch.setattr(sys, "argv", ["pymake", "t", "--dry-run"])
        with pytest.raises(SystemExit) as exc_info:
            fcmd.cli.dev.pymake.main()
        assert exc_info.value.code == 0

    def test_main_unknown_subcommand_exits_nonzero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """main() 未知子命令通过 SystemExit(1) 退出。"""
        monkeypatch.setattr(sys, "argv", ["pymake", "unknown_subcommand"])
        with pytest.raises(SystemExit) as exc_info:
            fcmd.cli.dev.pymake.main()
        assert exc_info.value.code == 1
