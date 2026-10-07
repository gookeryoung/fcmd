"""folderback 工具测试（DSL 内建动作声明 commands/folderback.toml）。

验证 ``fcmd folderback`` 的 DSL action 迁移语义：
- 工具注册（单命令 DSL 工具，内置声明）
- 声明契约：``__dsl_action__`` 标记、无 cmd（fn 任务形态）
- 执行语义：zip 备份、时间戳命名、目标目录自动创建、旧备份清理
- 失败语义：源目录缺失 → 任务失败汇总 + 退出码 1（行为变化：原版打印
  提示后退出码 0）
- dry-run 不执行
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from fcmd.apis._tool_args import ToolSpec
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli._discovery import ensure_tools_discovered

ensure_tools_discovered()  # 幂等：注册内置 DSL 命令（含 folderback）


# ---------------------------------------------------------------------- #
# 注册与声明验证
# ---------------------------------------------------------------------- #
class TestFolderbackRegistration:
    """folderback 经内置 DSL（action 原语）注册。"""

    def test_registered_as_dsl_single_command(self) -> None:
        """folderback 注册为内置 DSL 单命令工具。"""
        assert "folderback" in _TOOL_REGISTRY
        assert set(_TOOL_REGISTRY["folderback"]) == {None}

    def test_action_contract(self) -> None:
        """合成函数携带 __dsl_action__ 标记，cmd 为 None，无 message 契约。"""
        spec: ToolSpec = _TOOL_REGISTRY["folderback"][None]
        assert spec.cmd is None  # fn 任务形态（进程内动作，无子进程命令）
        assert getattr(spec.func, "__dsl_action__", None) == "folderback"
        assert not getattr(spec.func, "__dsl_empty_body__", False)
        assert not hasattr(spec.func, "__dsl_message__")  # 完成消息含运行时路径，动作内输出

    def test_signature_from_action_impl(self) -> None:
        """CLI 参数 schema 拷贝自动作实现签名：src/dst/max_zip 均为选项。"""
        spec = _TOOL_REGISTRY["folderback"][None]
        params = list(spec.func.__signature__.parameters)  # type: ignore[attr-defined]
        assert params == ["src", "dst", "max_zip"]
        assert spec.func.__signature__.parameters["max_zip"].default == 5  # type: ignore[attr-defined]


# ---------------------------------------------------------------------- #
# 执行语义
# ---------------------------------------------------------------------- #
class TestFolderbackRun:
    """``fcmd folderback`` 执行语义。"""

    def test_backup_creates_zip(self, tmp_path: Path) -> None:
        """备份源目录生成 zip（含源目录文件）。"""
        src = tmp_path / "proj"
        src.mkdir()
        (src / "a.txt").write_text("hello", encoding="utf-8")
        dst = tmp_path / "backup"
        assert run_tool("folderback", ["--src", str(src), "--dst", str(dst)]) == 0
        zips = list(dst.glob("proj_*.zip"))
        assert len(zips) == 1
        with zipfile.ZipFile(zips[0]) as zf:
            assert len(zf.namelist()) >= 1

    def test_backup_creates_missing_dst(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """目标目录缺失时自动创建并打印提示。"""
        src = tmp_path / "proj"
        src.mkdir()
        (src / "a.txt").write_text("x", encoding="utf-8")
        dst = tmp_path / "new_backup"
        assert run_tool("folderback", ["--src", str(src), "--dst", str(dst)]) == 0
        assert dst.is_dir()
        assert "创建目标文件夹" in capsys.readouterr().out

    def test_backup_missing_src_fails(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """源目录缺失 → 任务失败汇总 + 退出码 1（行为变化：原版打印后退出码 0）。"""
        assert run_tool("folderback", ["--src", str(tmp_path / "nope"), "--dst", str(tmp_path / "b")]) == 1
        assert "失败" in capsys.readouterr().out

    def test_old_backups_cleaned(self, tmp_path: Path) -> None:
        """旧备份清理：超出 max_zip 时删除最旧的。"""
        src = tmp_path / "proj"
        src.mkdir()
        (src / "a.txt").write_text("x", encoding="utf-8")
        dst = tmp_path / "backup"
        dst.mkdir()
        # 预置 3 个旧备份（文件名尾部时间戳决定新旧排序）
        for date in ("20260101_000000", "20260102_000000", "20260103_000000"):
            (dst / f"proj_{date}.zip").write_bytes(b"PK\x05\x06")
        assert run_tool("folderback", ["--src", str(src), "--dst", str(dst), "--max-zip", "2"]) == 0
        names = {p.name for p in dst.glob("*.zip")}
        assert len(names) == 2
        assert "proj_20260101_000000.zip" not in names
        assert "proj_20260102_000000.zip" not in names

    def test_dry_run_skips_execution(self, tmp_path: Path) -> None:
        """--dry-run 打印计划不执行：不创建备份。"""
        src = tmp_path / "proj"
        src.mkdir()
        dst = tmp_path / "backup"
        assert run_tool("folderback", ["--src", str(src), "--dst", str(dst), "--dry-run"]) == 0
        assert not dst.exists()
