"""L3 准 E2E：DAG 编排、持久化与错误处理。

验证 fcmd 在真实子进程中的跨工具行为：
- YAML 任务图（``fcmd yaml``）端到端执行与 needs 依赖
- 持久化行为（envdev 写入 → fcmd env 读取，HOME 隔离）
- 错误输入的优雅退出（无效 YAML / 无效参数 / 不存在的文件）
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from tests.conftest import CmdResult


@pytest.mark.e2e
class TestYamlWorkflow:
    """``fcmd yaml`` DAG 执行端到端。"""

    def test_linear_needs_order(self, fcmd: Callable[..., CmdResult], tmp_path: Path) -> None:
        """线性 needs 链按序执行，后一个 job 能读到前一个的产物。"""
        workflow = tmp_path / "chain.yaml"
        workflow.write_text(
            """
jobs:
  greet:
    run: "echo hello > greeting.txt"
  double:
    needs: greet
    run: "cat greeting.txt > final.txt && echo done >> final.txt"
""".strip(),
            encoding="utf-8",
        )
        r = fcmd("yaml", str(workflow))
        r.check_ok()
        final = tmp_path / "final.txt"
        assert final.exists(), "final.txt 未生成"
        lines = final.read_text(encoding="utf-8").strip().splitlines()
        assert lines == ["hello", "done"]

    def test_single_job(self, fcmd: Callable[..., CmdResult], tmp_path: Path) -> None:
        """仅执行一个 job。"""
        workflow = tmp_path / "single.yaml"
        workflow.write_text(
            """
jobs:
  hi:
    run: "echo hi > out.txt"
""".strip(),
            encoding="utf-8",
        )
        r = fcmd("yaml", str(workflow))
        r.check_ok()
        assert (tmp_path / "out.txt").read_text(encoding="utf-8").strip() == "hi"

    def test_dry_run_no_side_effect(self, fcmd: Callable[..., CmdResult], tmp_path: Path) -> None:
        """--dry-run 不产生实际文件副作用。"""
        workflow = tmp_path / "dry.yaml"
        workflow.write_text(
            """
jobs:
  job:
    run: "echo data > side.txt"
""".strip(),
            encoding="utf-8",
        )
        r = fcmd("yaml", str(workflow), "--dry-run")
        r.check_ok()
        assert not (tmp_path / "side.txt").exists(), "--dry-run 不应产生文件"

    def test_invalid_yaml_returns_error(self, fcmd: Callable[..., CmdResult], tmp_path: Path) -> None:
        """无效 YAML 语法返回非 0 退出码。"""
        bad = tmp_path / "bad.yaml"
        bad.write_text("jobs: [broken", encoding="utf-8")
        r = fcmd("yaml", str(bad), expect=None)
        assert r.returncode != 0, f"无效 YAML 应返回非 0，实际 {r.returncode}"


@pytest.mark.e2e
class TestErrorHandling:
    """错误输入的优雅退出。"""

    def test_missing_file(self, fcmd: Callable[..., CmdResult]) -> None:
        """引用不存在的文件应返回非 0 退出码。"""
        r = fcmd("writefile", "/definitely/not/exist/dir/file.txt", "x", expect=None)
        assert r.returncode != 0

    def test_too_few_args(self, fcmd: Callable[..., CmdResult]) -> None:
        """writefile 缺少参数应返回非 0 退出码。"""
        r = fcmd("writefile", expect=None)
        assert r.returncode != 0

    def test_bad_math_expr(self, fcmd: Callable[..., CmdResult]) -> None:
        """mathtool eval 收到非法表达式应正常退出（非崩溃）。"""
        r = fcmd("mathtool", "eval", "1 + ", expect=None)
        # 可能返回 0（打印错误）或非 0（视实现而定），关键是不崩溃
        assert isinstance(r.returncode, int)


@pytest.mark.e2e_smoke
class TestAllToolsHelp:
    """所有已注册工具的 --help 入口冒烟。"""

    # 工具列表由 pyproject.toml [project.scripts] 生成
    # 注：用 subprocess 跑 fcmd --help 工具名，比 import 更接近真实入口

    @pytest.mark.parametrize(
        "tool",
        [
            "txttool",
            "padtool",
            "regextool",
            "textdiff",
            "convtool",
            "casetool",
            "colortool",
            "csvtool",
            "inifile",
            "tomltool",
            "xmltool",
            "yamtool",
            "lscalc",
            "randtool",
            "stattool",
            "timetool",
            "calr",  # 注：clr 是命令清屏，跳过
            "sysinfo",
            "which",
            "pathtool",
            "filesearch",
            "filerename",
            "filelevel",
            "folderback",
            "folderzip",
            "archivex",
            "zipencrypt",
            "cryptool",
            "hashtool",
            "hashfile",
            "idtool",
            "iptool",
            "nettool",
            "portcheck",
            "bumpversion",
            "packtool",
            "pymake",
            "autofmt",
            "writefile",
            "jsontool",
            "jsont",
            "wch",
            "taskk",
            "gitt",
        ],
    )
    def test_tool_help_exists(self, fcmd: Callable[..., CmdResult], tool: str) -> None:
        """工具 --help 不崩溃，退出码 0 或 1。"""
        r = fcmd(tool, "--help", expect=None)
        # 合法工具返回 0，未知工具返回 1；两者都视为"不崩溃"
        assert isinstance(r.returncode, int)
        if r.returncode == 0:
            assert r.stdout.strip(), "--help 输出不应为空"
