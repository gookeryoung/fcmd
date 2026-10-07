"""fcmd.dsl.actions 内建动作注册表测试。

覆盖：
- Action 描述符与注册表 API（action/has_action/get_action/action_names）
- 重复注册报错
- 内建动作 setenv/writefile 的进程内执行语义（文件名批量操作原语语义
  见 test_cli_filedate/filerename/filelevel/folderback）
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from fcmd.dsl.actions import Action, action, action_names, get_action, has_action


# ---------------------------------------------------------------------- #
# 注册表 API
# ---------------------------------------------------------------------- #
class TestActionRegistry:
    """动作注册表 API 测试。"""

    def test_builtin_actions_registered(self) -> None:
        """内建动作已注册（setenv/writefile + 文件名批量操作原语）。"""
        builtin = {
            "setenv",
            "writefile",
            "dateprefix_add",
            "dateprefix_clear",
            "filerename_replace",
            "filerename_insert",
            "filerename_case",
            "filelevel_set",
            "folderback",
        }
        for name in builtin:
            assert has_action(name), name
            assert name in action_names()

    def test_unknown_action(self) -> None:
        """未注册动作 has_action 为 False，get_action 抛 KeyError。"""
        assert not has_action("nonexistent_action")
        with pytest.raises(KeyError, match="nonexistent_action"):
            get_action("nonexistent_action")

    def test_duplicate_registration(self) -> None:
        """同名动作重复注册报 ValueError（模块导入期编程错误）。"""
        registry = dict(_snapshot_actions())
        with pytest.raises(ValueError, match="重复注册"):

            @action("setenv")
            def _dup(name: str) -> None:  # pragma: no cover - 注册即报错，函数体不可达
                pass

        assert _snapshot_actions() == registry  # 注册失败不污染注册表

    def test_get_action_descriptor(self) -> None:
        """get_action 返回带 param_help 的 Action 描述符。"""
        act = get_action("setenv")
        assert isinstance(act, Action)
        assert act.name == "setenv"
        assert act.param_help["name"] == "环境变量名"


def _snapshot_actions() -> dict[str, Action]:
    """注册表快照（重复注册测试的污染检查）。"""
    from fcmd.dsl.actions import _ACTIONS

    return dict(_ACTIONS)


# ---------------------------------------------------------------------- #
# 内建动作执行语义
# ---------------------------------------------------------------------- #
class TestBuiltinActionImpls:
    """内建动作实现的进程内执行语义（与原 Python 模块行为一致）。"""

    def test_setenv_overwrite(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """default=False 覆盖已有值。"""
        monkeypatch.setenv("FCMD_TEST_ACTION_SETENV", "old")
        get_action("setenv").func(name="FCMD_TEST_ACTION_SETENV", value="new")
        assert os.environ["FCMD_TEST_ACTION_SETENV"] == "new"

    def test_setenv_default_keeps_existing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """default=True 不覆盖已有值。"""
        monkeypatch.setenv("FCMD_TEST_ACTION_SETENV", "old")
        get_action("setenv").func(name="FCMD_TEST_ACTION_SETENV", value="new", default=True)
        assert os.environ["FCMD_TEST_ACTION_SETENV"] == "old"

    def test_setenv_default_sets_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """default=True 且变量未设置时写入。"""
        monkeypatch.delenv("FCMD_TEST_ACTION_SETENV", raising=False)
        get_action("setenv").func(name="FCMD_TEST_ACTION_SETENV", value="new", default=True)
        assert os.environ["FCMD_TEST_ACTION_SETENV"] == "new"

    def test_writefile_utf8(self, tmp_path: Path) -> None:
        """writefile 默认 utf-8 写入。"""
        f = tmp_path / "note.txt"
        get_action("writefile").func(path=str(f), content="中文内容")
        assert f.read_text(encoding="utf-8") == "中文内容"

    def test_writefile_custom_encoding(self, tmp_path: Path) -> None:
        """writefile 支持自定义编码。"""
        f = tmp_path / "note.txt"
        get_action("writefile").func(path=str(f), content="内容", encoding="gbk")
        assert f.read_text(encoding="gbk") == "内容"

    def test_writefile_missing_parent_raises(self, tmp_path: Path) -> None:
        """父目录不存在时抛 OSError（与原版 Path.write_text 语义一致）。"""
        f = tmp_path / "missing_dir" / "note.txt"
        with pytest.raises(OSError):
            get_action("writefile").func(path=str(f), content="x")
