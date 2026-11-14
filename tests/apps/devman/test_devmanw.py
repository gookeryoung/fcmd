"""devmanw 测试：设置往返、列表口径、PATH 状态、按钮态与详情文案。

真实 Tk 窗口不在单测范围内（窗口类标 ``pragma: no cover``）；这里覆盖纯函数
与入口接线，保证 GUI 与 CLI 共用同一套机读契约。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from fcmd.apps.devman import devmanw
from fcmd.apps.devman._errors import DevmanError
from fcmd.core.toolkit import _TOOL_REGISTRY, run_tool


def _list_payload() -> dict[str, object]:
    return {
        "schema": "devman/list/1",
        "root": r"C:\devman",
        "tools": {
            "node": {
                "installed": [
                    {
                        "tool": "node",
                        "name": "22.23.5",
                        "version": "22.23.5",
                        "path": r"C:\devman\node\22.23.5",
                        "status": "ok",
                        "source": "https://nodejs.org/dist",
                        "installed_at": "2026-11-14T10:00:00+08:00",
                    },
                    {
                        "tool": "node",
                        "name": "20.11.1",
                        "version": "20.11.1",
                        "path": r"C:\devman\node\20.11.1",
                        "status": "incomplete",
                        "source": "https://nodejs.org/dist",
                        "installed_at": "",
                    },
                ],
                "active": "22.23.5",
                "incomplete": ["20.11.1"],
            },
            "python": {"installed": [], "active": None, "incomplete": []},
        },
        "shims": {"dir": r"C:\devman\bin", "on_path": True, "index": 2},
        "external": {},
        "markers": {},
        "warnings": [],
    }


def _versions_payload() -> dict[str, object]:
    return {
        "schema": "devman/versions/1",
        "tool": "node",
        "remote": True,
        "source": "https://nodejs.org/dist/index.json",
        "versions": [
            {"version": "22.23.5", "installed": True, "active": True, "lts": "Jod"},
            {"version": "23.11.0", "installed": False, "active": False, "lts": False},
        ],
        "warnings": [],
    }


def test_tool_registration() -> None:
    """devmanw 是独立工具，只注册一个无子命令的 GUI 入口。"""
    assert set(_TOOL_REGISTRY["devmanw"]) == {None}


def test_config_path_uses_fcmd_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FCMD_HOME", str(tmp_path))
    assert devmanw.config_path() == tmp_path / "ui" / "devmanw.json"


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    settings = devmanw.DevmanwSettings(geometry="1100x720+20+30", tool="python")
    devmanw.save_settings(settings, path)
    assert devmanw.load_settings(path) == settings


def test_load_missing_or_broken_returns_defaults(tmp_path: Path) -> None:
    assert devmanw.load_settings(tmp_path / "missing.json") == devmanw.DevmanwSettings()
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert devmanw.load_settings(broken) == devmanw.DevmanwSettings()


def test_settings_from_mapping_rejects_bad_values() -> None:
    settings = devmanw.settings_from_mapping({"geometry": "oops", "tool": "ruby"})
    assert settings == devmanw.DevmanwSettings()
    assert devmanw.settings_from_mapping({"geometry": "900x600", "tool": "python"}).geometry == "900x600"


def test_iid_roundtrip() -> None:
    assert devmanw.tool_iid("node") == "tool:node"
    assert devmanw.parse_iid("tool:node") == ("node", None)
    assert devmanw.version_iid("python", "cpython-3.12.15") == "python:cpython-3.12.15"
    assert devmanw.parse_iid("python:cpython-3.12.15") == ("python", "cpython-3.12.15")


def test_installed_rows_group_and_active() -> None:
    rows = devmanw.installed_rows(_list_payload())
    assert [row["kind"] for row in rows] == [
        "tool",
        "installed",
        "installed",
        "tool",
        "tool",
        "tool",
        "tool",
        "tool",
    ]
    assert rows[0]["text"] == "node（2 个已安装，活动 22.23.5）"
    assert rows[1]["active"] is True
    assert rows[1]["values"][0] == "完整"
    assert rows[1]["values"][1] == "当前活动"
    assert rows[2]["values"][0] == "半完成"
    assert rows[3]["text"] == "python（0 个已安装，活动 未设置）"


def test_available_rows_mark_installed_and_lts() -> None:
    rows = devmanw.available_rows(_versions_payload())
    assert [row["iid"] for row in rows] == ["avail:node:22.23.5", "avail:node:23.11.0"]
    assert rows[0]["values"] == ("已安装", "当前活动", "LTS")
    assert rows[1]["values"] == ("", "", "")


def test_path_snapshot_registered_and_index() -> None:
    snapshot = devmanw.path_snapshot(
        registry_path=r"C:\devman\bin;C:\Windows",
        entry=r"C:\devman\bin",
        recorded=[r"C:\devman\bin"],
        shadowed={"node": [r"C:\node\node.exe"]},
        shims={"on_path": True, "index": 0},
    )
    assert snapshot["registered"] is True
    assert snapshot["process_index"] == 0
    assert snapshot["needs_restart"] is False
    assert snapshot["shadowed"] == {"node": [r"C:\node\node.exe"]}


def test_path_snapshot_needs_restart_and_not_registered() -> None:
    snapshot = devmanw.path_snapshot(
        registry_path=r"C:\Windows",
        entry=r"C:\devman\bin",
        recorded=[],
        shadowed={},
        shims={"on_path": False, "index": None},
    )
    assert snapshot["registered"] is False
    assert snapshot["process_index"] is None
    assert snapshot["recorded"] == []
    assert snapshot["needs_restart"] is False


def test_path_lines_render_state() -> None:
    snapshot = devmanw.path_snapshot(
        registry_path=r"C:\devman\bin;C:\Windows",
        entry=r"C:\devman\bin",
        recorded=[r"C:\devman\bin"],
        shadowed={},
        shims={"on_path": False, "index": None},
    )
    lines = dict(devmanw.path_lines(snapshot))
    assert lines["用户 PATH"] == "已写入"
    assert "重开终端后生效" in lines["当前终端"]
    assert lines["写入记录"].startswith("1 条")
    assert lines["会遮蔽"] == "无"


def test_action_states_matrix() -> None:
    idle = devmanw.action_states(
        busy=False,
        has_installed=False,
        installed_is_active=False,
        has_available=False,
        has_tool_versions=False,
        path_registered=False,
    )
    assert idle == {
        "refresh": True,
        "refresh_versions": True,
        "install": False,
        "uninstall": False,
        "use": False,
        "upgrade": False,
        "init": True,
        "uninit": False,
    }
    selected = devmanw.action_states(
        busy=False,
        has_installed=True,
        installed_is_active=False,
        has_available=True,
        has_tool_versions=True,
        path_registered=True,
    )
    assert selected["install"] and selected["uninstall"] and selected["use"] and selected["upgrade"]
    assert selected["init"] is False and selected["uninit"] is True
    busy = devmanw.action_states(
        busy=True,
        has_installed=True,
        installed_is_active=False,
        has_available=True,
        has_tool_versions=True,
        path_registered=True,
    )
    assert not any(busy.values())


def test_detail_values_for_installed_available_and_tool() -> None:
    payload = _list_payload()
    snapshot = {
        **payload,
        "tool_roots": {"node": r"C:\devman\node", "python": r"C:\devman\python"},
    }
    rows = devmanw.installed_rows(payload)
    installed = devmanw.detail_values(rows[1], snapshot)
    assert installed["工具"] == "node"
    assert installed["状态"] == "完整"
    assert installed["可执行文件"] == r"C:\devman\node\22.23.5\node.exe"

    incomplete = devmanw.detail_values(rows[2], snapshot)
    assert "半完成" in incomplete["状态"]

    tool = devmanw.detail_values(rows[0], snapshot)
    assert tool["版本"] == "2 个已安装"
    assert tool["活动"] == "22.23.5"
    assert tool["来源"] == "—"

    available = devmanw.detail_values(devmanw.available_rows(_versions_payload())[1], snapshot, source="index.json")
    assert available["状态"] == "可安装"
    assert available["来源"] == "index.json"

    empty = devmanw.detail_values(None, snapshot)
    assert empty["版本"] == "—"
    assert empty["来源"] == "—"
    assert empty["版本目录"] == r"C:\devman"


def test_path_lines_on_path_without_index() -> None:
    snapshot: dict[str, Any] = {
        "registered": True,
        "process_on_path": True,
        "process_index": None,
        "entry": r"C:\devman\bin",
        "recorded": [],
        "shadowed": {},
    }
    lines = dict(devmanw.path_lines(snapshot))
    assert lines["当前终端"] == "在 PATH 中"
    assert lines["写入记录"] == "无"


def test_error_text_variants() -> None:
    assert devmanw._error_text(DevmanError("操作失败", "RUNTIME_ERROR", hint="稍后重试")) == "操作失败\n稍后重试"
    assert devmanw._error_text(DevmanError("操作失败", "RUNTIME_ERROR")) == "操作失败"
    assert devmanw._error_text(ValueError("bad")) == "bad"
    assert devmanw._error_text(ValueError()) == "ValueError"


def test_gui_entrypoints_share_one_window(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[object, str]] = []
    monkeypatch.setattr(
        devmanw._guiwindow,
        "run_window",
        lambda factory, title: calls.append((factory, title)),
    )

    assert devmanw.run_gui(str(tmp_path / "root")) is None
    assert devmanw.launch_cmd(str(tmp_path / "root")) is None
    assert run_tool("devmanw", []) == 0
    assert [title for _factory, title in calls] == ["开发环境管理"] * 3
    assert all(callable(factory) for factory, _title in calls)
