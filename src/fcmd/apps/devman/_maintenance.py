"""可运维性：磁盘占用、完整性校验与修复、锁文件导出与恢复。

本模块只依赖目录布局与已安装快照，不联网；``restore`` 的安装编排留在
:mod:`fcmd.apps.devman._ops`，避免与安装流水线形成循环依赖。
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from fcmd.apps.devman import _env, _state, _toolchain
from fcmd.apps.devman._errors import invalid_argument
from fcmd.apps.devman._paths import TOOLS, Layout

__all__ = [
    "LOCK_SCHEMA",
    "export_lock",
    "leftovers",
    "load_lock",
    "lock_plan",
    "path_size",
    "repair",
    "size",
    "verify",
]

LOCK_SCHEMA = "devman-lock/1"

_LEFTOVER_PREFIXES = (".staging-", ".trash-")


# ---------------------------------------------------------------------- #
# 磁盘占用
# ---------------------------------------------------------------------- #
def path_size(path: Path) -> tuple[int, int]:
    """递归统计 ``(字节数, 文件数)``；不跟随符号链接 / junction，避免重复计数。"""
    total = 0
    files = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_symlink() or entry.is_junction():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
                    continue
                total += entry.stat(follow_symlinks=False).st_size
                files += 1
            except OSError:
                continue
    return total, files


def size(layout: Layout, *, tool: str | None = None) -> dict[str, Any]:
    """各版本目录 / 下载缓存 / shim 的占用；``tool`` 为空时统计全部工具。"""
    tools = (tool,) if tool else TOOLS
    result: dict[str, Any] = {"schema": "devman/size/1", "root": str(layout.root), "tools": {}}
    total = 0
    for name in tools:
        versions: list[dict[str, Any]] = []
        tool_bytes = 0
        for row in _toolchain.list_installed(layout, name):
            used, count = path_size(row.path)
            tool_bytes += used
            versions.append(
                {
                    "version": row.version,
                    "name": row.name,
                    "bytes": used,
                    "files": count,
                    "status": "ok" if row.complete else "incomplete",
                }
            )
        result["tools"][name] = {"versions": versions, "bytes": tool_bytes}
        total += tool_bytes
    cache_bytes, cache_files = path_size(layout.cache_dir)
    shim_bytes, shim_files = path_size(layout.shim_dir)
    result["cache"] = {"dir": str(layout.cache_dir), "bytes": cache_bytes, "files": cache_files}
    result["shims"] = {"dir": str(layout.shim_dir), "bytes": shim_bytes, "files": shim_files}
    result["total_bytes"] = total + cache_bytes + shim_bytes
    return result


# ---------------------------------------------------------------------- #
# 完整性校验
# ---------------------------------------------------------------------- #
def leftovers(layout: Layout, *, tool: str | None = None) -> list[Path]:
    """安装残留：``.staging-*`` / ``.trash-*`` 目录与缓存 ``*.part`` 文件。"""
    items: list[Path] = []
    for name in (tool,) if tool else TOOLS:
        root = layout.tool_root(name)
        try:
            entries = list(root.iterdir())
        except OSError:
            continue
        items.extend(item for item in entries if item.is_dir() and item.name.startswith(_LEFTOVER_PREFIXES))
    with contextlib.suppress(OSError):
        items.extend(sorted(layout.cache_dir.glob("*.part")))
    return items


def _issue(tool: str | None, name: str | None, kind: str, detail: str, hint: str | None = None) -> dict[str, Any]:
    return {"tool": tool, "name": name, "kind": kind, "detail": detail, "hint": hint}


def _version_issues(layout: Layout, row: _toolchain.Installed) -> list[dict[str, Any]]:
    """一个版本目录的问题列表；完整版本返回空列表。"""
    marker = row.marker
    if marker is None:
        return [
            _issue(
                row.tool,
                row.name,
                "marker-missing",
                "缺少 .devman.json 安装标记",
                f"运行 fcmd devman install {row.tool}@{row.version} --force 重新安装",
            )
        ]
    if marker.get("schema") != _state.MARKER_SCHEMA or marker.get("tool") != row.tool:
        return [
            _issue(
                row.tool,
                row.name,
                "marker-invalid",
                "安装标记的 schema / tool 字段不匹配",
                f"运行 fcmd devman install {row.tool}@{row.version} --force 重新安装",
            )
        ]
    if marker.get("version") != row.version:
        return [
            _issue(
                row.tool,
                row.name,
                "marker-invalid",
                f"安装标记版本 {marker.get('version')!r} 与目录名不一致",
                f"运行 fcmd devman install {row.tool}@{row.version} --force 重新安装",
            )
        ]
    if not _toolchain.executable_path(layout, row.tool, row.name).is_file():
        return [
            _issue(
                row.tool,
                row.name,
                "exe-missing",
                "主可执行文件缺失",
                f"运行 fcmd devman install {row.tool}@{row.version} --force 重新安装",
            )
        ]
    return []


def _shim_issues(layout: Layout) -> list[dict[str, Any]]:
    """shim 只在 bin 目录已有文件时检查，避免 ``--no-path`` / 未 init 时误报。"""
    if not layout.shim_dir.is_dir():
        return []
    try:
        if not any(layout.shim_dir.iterdir()):
            return []
    except OSError:
        return []
    issues: list[dict[str, Any]] = []
    for name, text in _env.shim_scripts().items():
        path = layout.shim_dir / name
        try:
            current = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            current = None
        if current is None:
            issues.append(_issue(None, name, "shim-missing", f"缺少 shim {name}", "运行 fcmd devman repair --yes"))
        elif current != text:
            issues.append(
                _issue(None, name, "shim-stale", f"shim {name} 内容与当前版本不一致", "运行 fcmd devman repair --yes")
            )
    return issues


def verify(layout: Layout, *, tool: str | None = None) -> dict[str, Any]:
    """检查安装标记、主可执行文件、活动版本、shim 与残留；只读。"""
    tools = (tool,) if tool else TOOLS
    issues: list[dict[str, Any]] = []
    checked: dict[str, int] = {}
    for name in tools:
        rows = _toolchain.list_installed(layout, name)
        checked[name] = len(rows)
        for row in rows:
            issues.extend(_version_issues(layout, row))
        active = _state.active_version(layout, name)
        if active and not any(row.name == active and row.complete for row in rows):
            issues.append(
                _issue(
                    name,
                    active,
                    "active-broken",
                    f"活动版本 {active} 不存在或不完整",
                    f"运行 fcmd devman use {name}@<version> 重新选择，或 repair --yes 清空",
                )
            )
    issues.extend(_shim_issues(layout))
    for item in leftovers(layout, tool=tool):
        issue = _issue(None, item.name, "leftover", f"安装残留：{item}", "运行 fcmd devman repair --yes")
        issue["path"] = str(item)
        issues.append(issue)
    return {"schema": "devman/verify/1", "ok": not issues, "checked": checked, "issues": issues}


# ---------------------------------------------------------------------- #
# 修复
# ---------------------------------------------------------------------- #
def repair(  # noqa: PLR0912 - 按问题类型逐类收敛，线性展开更易核对
    layout: Layout,
    *,
    tool: str | None = None,
    dry_run: bool = False,
    purge_incomplete: bool = False,
) -> dict[str, Any]:
    """修复可自动处理的问题：重建 shim、清残留、清空失效的活动版本。

    半完成版本目录默认只报告（可能含用户数据），需要 ``purge_incomplete`` 才删除。
    损坏的完整版本（标记 / 可执行文件缺失）提示用 ``install --force`` 重装，不在此处删除。
    """
    report = verify(layout, tool=tool)
    actions: list[dict[str, Any]] = []
    for issue in report["issues"]:
        if issue["kind"] in {"shim-missing", "shim-stale"}:
            actions.append({"kind": "shim", "target": str(layout.shim_dir), "detail": "重建 shim"})
        elif issue["kind"] == "leftover":
            actions.append({"kind": "leftover", "target": issue.get("path") or "", "detail": "删除安装残留"})
        elif issue["kind"] == "active-broken":
            actions.append(
                {
                    "kind": "active",
                    "target": f"{issue['tool']}@{issue['name']}",
                    "detail": "清空失效的活动版本",
                }
            )
        elif purge_incomplete and issue["kind"] in {"marker-missing", "marker-invalid", "exe-missing"}:
            actions.append(
                {
                    "kind": "incomplete",
                    "target": f"{issue['tool']}\\{issue['name']}",
                    "detail": "删除半完成版本目录",
                }
            )
    # 同一类动作去重（shim 目录会被多个缺失文件重复登记）
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for action in actions:
        key = (action["kind"], action["target"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(action)
    actions = unique

    done: list[dict[str, Any]] = []
    if not dry_run and actions:
        kinds = {action["kind"] for action in actions}
        if "shim" in kinds:
            written, _unchanged = _env.write_shims(layout)
            done.append({"kind": "shim", "written": written})
        if "leftover" in kinds or "incomplete" in kinds:
            for action in actions:
                if action["kind"] == "leftover":
                    path = Path(action["target"])
                    _remove(path)
                elif action["kind"] == "incomplete":
                    tool_name, _, version_name = action["target"].partition("\\")
                    _remove(layout.version_dir(tool_name, version_name))
        if "active" in kinds:
            for name in (tool,) if tool else TOOLS:
                active = _state.active_version(layout, name)
                if active and not any(
                    row.name == active and row.complete for row in _toolchain.list_installed(layout, name)
                ):
                    _state.remove_active(layout, name)

    remaining = verify(layout, tool=tool) if not dry_run else report
    return {
        "schema": "devman/repair/1",
        "dry_run": dry_run,
        "purge_incomplete": purge_incomplete,
        "actions": actions,
        "done": done,
        "ok": (not remaining["issues"]) if not dry_run else not actions,
        "remaining": remaining["issues"],
    }


def _remove(path: Path) -> None:
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    except OSError:
        return


# ---------------------------------------------------------------------- #
# 锁文件
# ---------------------------------------------------------------------- #
def export_lock(layout: Layout) -> dict[str, Any]:
    """导出可复现清单：每个工具的已安装版本、当前活动版本与校验信息。"""
    tools: dict[str, Any] = {}
    for tool in TOOLS:
        versions = []
        for row in _toolchain.list_installed(layout, tool):
            marker = row.marker or {}
            versions.append(
                {
                    "version": row.version,
                    "name": row.name,
                    "status": "ok" if row.complete else "incomplete",
                    "source": marker.get("source"),
                    "sha256": marker.get("sha256"),
                    "verify": marker.get("verify"),
                }
            )
        tools[tool] = {"active": _state.active_version(layout, tool), "versions": versions}
    return {"schema": LOCK_SCHEMA, "root": str(layout.root), "tools": tools}


def load_lock(path: Path) -> dict[str, Any]:
    """读取并校验锁文件；结构不合法抛 ``INVALID_ARGUMENT``。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise invalid_argument(f"锁文件无法读取：{path}", hint=f"确认路径；{exc}", field="lock") from exc
    except json.JSONDecodeError as exc:
        raise invalid_argument(f"锁文件不是合法 JSON：{path}", hint=str(exc), field="lock") from exc
    if not isinstance(data, dict):
        raise invalid_argument("锁文件顶层必须是对象", field="lock")
    if data.get("schema") != LOCK_SCHEMA:
        raise invalid_argument(
            f"锁文件 schema 必须是 {LOCK_SCHEMA}",
            hint="用 fcmd devman export 生成锁文件",
            field="lock",
        )
    tools = data.get("tools")
    if not isinstance(tools, dict):
        raise invalid_argument("锁文件缺少 tools 对象", field="lock")
    for tool in tools:
        if tool not in TOOLS:
            raise invalid_argument(f"锁文件包含未知工具 {tool!r}", hint="支持 node / python", field="lock")
        entry = tools[tool]
        if not isinstance(entry, dict) or not isinstance(entry.get("versions") or [], list):
            raise invalid_argument(f"锁文件的 tools.{tool} 结构不合法", field="lock")
        for item in entry.get("versions") or []:
            if not isinstance(item, dict) or not isinstance(item.get("version"), str):
                raise invalid_argument(f"锁文件的 tools.{tool}.versions 条目缺少 version", field="lock")
    return data


def lock_plan(layout: Layout, lock: dict[str, Any]) -> dict[str, Any]:
    """对比锁文件与本机：要安装的版本、要切换的活动版本、可跳过的条目。"""
    installs: list[dict[str, Any]] = []
    use: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for tool in TOOLS:
        entry = (lock.get("tools") or {}).get(tool) or {}
        rows = {row.name: row for row in _toolchain.list_installed(layout, tool)}
        for item in entry.get("versions") or []:
            version = item["version"]
            row = rows.get(version) or next(
                (candidate for candidate in rows.values() if candidate.version == version), None
            )
            if row is not None and row.complete:
                skipped.append({"tool": tool, "version": version, "reason": "已安装"})
                continue
            installs.append(
                {
                    "tool": tool,
                    "version": version,
                    "spec": f"{tool}@{version}",
                    "force": row is not None,
                }
            )
        active = entry.get("active")
        if isinstance(active, str) and active and _state.active_version(layout, tool) != active:
            use.append({"tool": tool, "active": active, "spec": f"{tool}@{active}"})
    return {"installs": installs, "use": use, "skipped": skipped}
