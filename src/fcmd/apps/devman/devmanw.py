"""devmanw - devman 的 Qt 窗口。

窗口只做编排：安装 / 卸载 / 切换 / 升级 / PATH 集成全部调用
:mod:`fcmd.apps.devman._ops`（与 ``fcmd devman`` 共用的机读契约），GUI 不重写
任何安装逻辑；耗时操作都在后台线程执行，事件经信号桥回投界面。

设置文件：``${FCMD_HOME:-~/.fcmd}/ui/devmanw.json``。
"""

from __future__ import annotations

import ntpath
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fcmd
from fcmd.apps.devman import _env
from fcmd.apps.devman._errors import DevmanError
from fcmd.apps.devman._paths import TOOLS
from fcmd.apps.devman._toolchain import executable_name
from fcmd.gui import config as _guiconfig

__all__ = [
    "MIN_HEIGHT",
    "MIN_WIDTH",
    "SETTINGS_NAME",
    "DevmanwSettings",
    "action_states",
    "available_rows",
    "config_path",
    "detail_values",
    "installed_rows",
    "load_settings",
    "main",
    "parse_iid",
    "path_lines",
    "path_snapshot",
    "save_settings",
    "settings_from_mapping",
    "settings_to_mapping",
    "tool_iid",
    "version_iid",
]

SETTINGS_NAME = "devmanw"
DEFAULT_GEOMETRY = "1080x720"
MIN_WIDTH = 920
MIN_HEIGHT = 620

_DESCRIPTION = "devman 的 Qt 图形界面：按工具查看已安装 / 活动 / 可用版本，安装、卸载、切换、升级与 PATH 集成"

_DETAIL_FIELDS: tuple[str, ...] = ("工具", "版本", "状态", "活动", "版本目录", "可执行文件", "来源", "安装时间")
"""详情区字段顺序；缺失值统一渲染成 ``—``。"""


# ---------------------------------------------------------------------- #
# 设置持久化
# ---------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class DevmanwSettings:
    geometry: str | None = None
    tool: str = "node"


def settings_to_mapping(settings: DevmanwSettings) -> dict[str, object]:
    return {"geometry": settings.geometry, "tool": settings.tool}


def settings_from_mapping(raw: Mapping[str, Any]) -> DevmanwSettings:
    tool = raw.get("tool")
    return DevmanwSettings(
        geometry=_guiconfig.valid_geometry(raw.get("geometry") if isinstance(raw.get("geometry"), str) else None),
        tool=tool if isinstance(tool, str) and tool in TOOLS else "node",
    )


def config_path() -> Path:
    """返回设置文件路径（``${FCMD_HOME:-~/.fcmd}/ui/devmanw.json``）。"""
    return _guiconfig.config_path(SETTINGS_NAME)


def load_settings(path: Path | None = None) -> DevmanwSettings:
    raw = _guiconfig.read_settings(path or config_path())
    return DevmanwSettings() if raw is None else settings_from_mapping(raw)


def save_settings(settings: DevmanwSettings, path: Path | None = None) -> None:
    _guiconfig.write_settings(settings_to_mapping(settings), path or config_path())


# ---------------------------------------------------------------------- #
# 列表行（纯函数，便于单测）
# ---------------------------------------------------------------------- #
def tool_iid(tool: str) -> str:
    return f"tool:{tool}"


def version_iid(tool: str, name: str) -> str:
    return f"{tool}:{name}"


def parse_iid(iid: str) -> tuple[str, str | None]:
    """``tool:node`` → ``(node, None)``；``node:22.23.5`` → ``(node, 22.23.5)``。"""
    prefix, _, rest = iid.partition(":")
    return (rest, None) if prefix == "tool" else (prefix, rest)


def installed_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """已安装树的行：每个工具一个分组节点，下面是版本节点。"""
    rows: list[dict[str, Any]] = []
    tools = payload.get("tools") or {}
    for tool in TOOLS:
        data = tools.get(tool) or {}
        installed = data.get("installed") or []
        active = data.get("active")
        rows.append(
            {
                "kind": "tool",
                "iid": tool_iid(tool),
                "tool": tool,
                "text": f"{tool}（{len(installed)} 个已安装，活动 {active or '未设置'}）",
                "values": ("", "", ""),
            }
        )
        for item in installed:
            is_active = item.get("name") == active
            rows.append(
                {
                    "kind": "installed",
                    "iid": version_iid(tool, item["name"]),
                    "tool": tool,
                    "name": item["name"],
                    "version": item["version"],
                    "path": item.get("path", ""),
                    "status": item.get("status", "ok"),
                    "source": item.get("source") or "",
                    "installed_at": item.get("installed_at") or "",
                    "active": is_active,
                    "text": ("● " if is_active else "   ") + item["version"],
                    "values": (
                        "完整" if item.get("status") == "ok" else "半完成",
                        "当前活动" if is_active else "",
                        item.get("source") or "",
                    ),
                }
            )
    return rows


def available_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """可用版本树的行（按版本升序，保持与 ``versions_payload`` 一致）。"""
    tool = payload.get("tool") or ""
    rows: list[dict[str, Any]] = []
    for item in payload.get("versions") or []:
        rows.append(
            {
                "kind": "available",
                "iid": f"avail:{tool}:{item['version']}",
                "tool": tool,
                "version": item["version"],
                "installed": bool(item.get("installed")),
                "active": bool(item.get("active")),
                "lts": item.get("lts") or "",
                "key": item.get("key") or "",
                "text": item["version"],
                "values": (
                    "已安装" if item.get("installed") else "",
                    "当前活动" if item.get("active") else "",
                    "LTS" if item.get("lts") else "",
                ),
            }
        )
    return rows


def path_snapshot(
    *,
    registry_path: str | None,
    entry: str,
    recorded: Sequence[str],
    shadowed: Mapping[str, Sequence[str]],
    shims: Mapping[str, Any],
) -> dict[str, Any]:
    """汇总 PATH 集成状态：注册表是否已写入、当前进程顺位、记录条目与遮蔽项。"""
    registered = _env.path_entry_present(registry_path, entry)
    on_path = bool(shims.get("on_path"))
    return {
        "entry": entry,
        "registered": registered,
        "recorded": [item for item in recorded if item],
        "shadowed": {tool: list(items) for tool, items in shadowed.items()},
        "process_on_path": on_path,
        "process_index": shims.get("index") if on_path else None,
        "needs_restart": registered and not on_path,
    }


def path_lines(snapshot: Mapping[str, Any]) -> list[tuple[str, str]]:
    """PATH 面板的「字段 / 值」：注册表状态、进程顺位、记录条目、遮蔽项。"""
    registered = "已写入" if snapshot.get("registered") else "未写入"
    index = snapshot.get("process_index")
    if snapshot.get("process_on_path"):
        current = f"顺位 {int(index) + 1}" if isinstance(index, int) else "在 PATH 中"
    else:
        current = "不在当前终端 PATH（重开终端后生效）" if snapshot.get("registered") else "不在 PATH 中"
    recorded = snapshot.get("recorded") or []
    recorded_text = f"{len(recorded)} 条（devman 可精确回滚）" if recorded else "无"
    shadowed = snapshot.get("shadowed") or {}
    shadow_text = "；".join(f"{tool}: {', '.join(items)}" for tool, items in shadowed.items()) if shadowed else "无"
    return [
        ("用户 PATH", registered),
        ("当前终端", current),
        ("条目", str(snapshot.get("entry") or "")),
        ("写入记录", recorded_text),
        ("会遮蔽", shadow_text),
    ]


def action_states(  # noqa: PLR0913 - 按钮可用性由选中项与运行状态共同决定
    *,
    busy: bool,
    has_installed: bool,
    installed_is_active: bool,
    has_available: bool,
    has_tool_versions: bool,
    path_registered: bool,
) -> dict[str, bool]:
    """按钮可用性：任务进行中全部禁用；其余按选中项与 PATH 状态联动。"""
    idle = not busy
    return {
        "refresh": idle,
        "refresh_versions": idle,
        "install": idle and has_available,
        "uninstall": idle and has_installed,
        "use": idle and has_installed and not installed_is_active,
        "upgrade": idle and has_tool_versions,
        "init": idle and not path_registered,
        "uninit": idle and path_registered,
    }


def detail_values(
    row: Mapping[str, Any] | None,
    snapshot: Mapping[str, Any],
    *,
    source: str | None = None,
) -> dict[str, str]:
    """详情区字段值；未选中时给出根目录与 PATH 概览。"""
    path_state = path_lines(snapshot)
    if row is None:
        return {
            "工具": "—",
            "版本": "—",
            "状态": "选择左侧列表中已安装或可用的版本",
            "活动": "—",
            "版本目录": str(snapshot.get("root") or ""),
            "可执行文件": "—",
            "来源": "—",
            "安装时间": "—",
        }
    tool = str(row.get("tool") or "")
    if row.get("kind") == "tool":
        tools = snapshot.get("tools") or {}
        data = tools.get(tool) or {}
        installed = data.get("installed") or []
        return {
            "工具": tool,
            "版本": f"{len(installed)} 个已安装",
            "状态": "PATH " + path_state[0][1],
            "活动": data.get("active") or "未设置",
            "版本目录": str(snapshot.get("tool_roots", {}).get(tool, "")),
            "可执行文件": "—",
            "来源": "—",
            "安装时间": "—",
        }
    if row.get("kind") == "available":
        return {
            "工具": tool,
            "版本": str(row.get("version") or ""),
            "状态": "已安装" if row.get("installed") else "可安装",
            "活动": "是" if row.get("active") else "否",
            "版本目录": str(row.get("key") or "安装后显示"),
            "可执行文件": "—",
            "来源": source or "—",
            "安装时间": "—",
        }
    name = str(row.get("name") or "")
    tool_root = str(snapshot.get("tool_roots", {}).get(tool, ""))
    version_dir = str(row.get("path") or (Path(tool_root) / name if tool_root else ""))
    exe = executable_name(tool)
    return {
        "工具": tool,
        "版本": str(row.get("version") or ""),
        "状态": "完整" if row.get("status") == "ok" else "半完成（不可切换，需重装）",
        "活动": "是" if row.get("active") else "否",
        "版本目录": version_dir,
        # 展示的是 Windows 安装目录，用 ntpath 拼接以保持反斜杠（主机平台无关）。
        "可执行文件": ntpath.join(version_dir, exe),
        "来源": str(row.get("source") or "—"),
        "安装时间": str(row.get("installed_at") or "—"),
    }


def _error_text(exc: BaseException) -> str:
    if isinstance(exc, DevmanError):
        return f"{exc.message}\n{exc.hint}" if exc.hint else exc.message
    return str(exc) or exc.__class__.__name__


# ---------------------------------------------------------------------- #
# GUI 入口
# ---------------------------------------------------------------------- #
def run_gui(root_dir: str = "") -> None:
    """启动 Qt 窗口（高 DPI 与主题由 ``gui.qt.app`` 统一处理）。"""
    try:
        from fcmd.apps.devman._devmanw_qt import run_qt
    except ImportError as exc:  # pragma: no cover - 安装完整时不会触发
        raise SystemExit("未安装 Qt 前端：pip install 'PySide6-Essentials'") from exc

    run_qt(root_dir)


@fcmd.tool("devmanw", help="打开 devman 图形界面（Qt）", description=_DESCRIPTION, param_help={"root": "devman 根目录"})
def launch_cmd(root: str = "") -> None:
    """打开 devman 图形界面。"""
    run_gui(root)


@fcmd.main("devmanw")
def main() -> None:
    """``devmanw`` 命令入口。"""
    launch_cmd()
