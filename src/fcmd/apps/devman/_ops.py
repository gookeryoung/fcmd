"""命令编排：解析目标版本、加锁、执行、回滚与 dry-run 计划。

CLI / 后续 GUI 共用本模块，业务判定不重复实现。
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import sys
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from fcmd.apps.devman import _env, _maintenance, _node, _project, _python
from fcmd.apps.devman._errors import (
    BUSY,
    NOT_INSTALLED,
    DevmanError,
    confirm_required,
    invalid_argument,
    not_found,
    runtime_error,
)
from fcmd.apps.devman._net import Fetcher, ProgressCallback, UrllibFetcher, normalize_proxy
from fcmd.apps.devman._paths import Layout, normalize_tool
from fcmd.apps.devman._runner import Runner, SubprocessRunner
from fcmd.apps.devman._state import (
    MARKER_NAME,
    Config,
    State,
    active_version,
    load_config,
    load_state,
    now_iso,
    save_state,
    set_active,
    write_atomic,
)
from fcmd.apps.devman._toolchain import (
    Spec,
    executable_path,
    list_installed,
    matches,
    parse_spec,
    resolve_installed,
    sort_key,
    version_of,
)

__all__ = [
    "JobContext",
    "doctor_payload",
    "env_payload",
    "export_payload",
    "init",
    "install",
    "list_payload",
    "new_context",
    "project_pins",
    "project_snapshot",
    "project_versions",
    "repair_payload",
    "restore_payload",
    "size_payload",
    "uninit",
    "uninstall",
    "upgrade",
    "use",
    "verify_payload",
    "versions_payload",
]

LOCK_STALE_SECONDS = 30 * 60
_EXACT_RE = re.compile(r"^\d+\.\d+\.\d+$")


@dataclass(slots=True)
class JobContext:
    """一次命令的根、配置、状态与已收集的告警。"""

    layout: Layout
    config: Config
    state: State
    warnings: list[str] = field(default_factory=list)
    fetcher: Fetcher | None = None
    runner: Runner | None = None
    env_store: _env.EnvStore | None = None
    env: Mapping[str, str] | None = None

    def fetch(self) -> Fetcher:
        if self.fetcher is None:
            self.fetcher = UrllibFetcher(timeout_s=self.config.timeout_s, proxy=self.config.proxy)
        return self.fetcher

    def execute(self) -> Runner:
        if self.runner is None:
            self.runner = SubprocessRunner()
        return self.runner


def new_context(root: str | Path | None = None) -> JobContext:
    """读取配置与状态，组装命令上下文。"""
    layout = Layout.create(root)
    config, config_warnings = load_config(layout)
    state, state_warnings = load_state(layout)
    return JobContext(layout=layout, config=config, state=state, warnings=[*config_warnings, *state_warnings])


# ---- 项目级绑定 ----
def project_pins(
    *,
    start: Path | str | None = None,
) -> tuple[dict[str, _project.Pin], list[str]]:
    """从当前目录向上查找项目版本文件（``devman.toml`` 优先）。"""
    return _project.discover(start=start)


def project_snapshot(
    ctx: JobContext,
    *,
    start: Path | str | None = None,
) -> dict[str, Any]:
    """只读快照：项目文件、绑定值、是否已安装；不抛错（供 ``list`` / ``doctor``）。"""
    pins, warnings = project_pins(start=start)
    entries: dict[str, Any] = {}
    for tool, pin in pins.items():
        entry: dict[str, Any] = {
            "raw": pin.raw,
            "source": pin.source,
            "kind": pin.kind,
            "installed": False,
            "resolved": None,
            "path": None,
        }
        try:
            row = resolve_installed(ctx.layout, Spec(tool, pin.raw))
        except DevmanError:
            pass
        else:
            entry.update(installed=True, resolved=row.name, version=row.version, path=str(row.path))
        entries[tool] = entry
    return {
        "schema": _project.SCHEMA,
        "files": sorted({pin.source for pin in pins.values()}),
        "pins": entries,
        "warnings": warnings,
    }


def project_versions(
    ctx: JobContext,
    *,
    start: Path | str | None = None,
) -> dict[str, str]:
    """项目绑定的安装名（``tool → 版本目录名``）；绑定版本未安装时抛 ``NOT_INSTALLED``。

    只影响本次进程的环境构造，不改全局 active、不写 PATH、不自动下载。
    """
    pins, _warnings = project_pins(start=start)
    versions: dict[str, str] = {}
    for tool, pin in pins.items():
        try:
            row = resolve_installed(ctx.layout, Spec(tool, pin.raw))
        except DevmanError as exc:
            raise not_found(
                f"项目要求 {tool}@{pin.raw}，但本机没有可用的已安装版本（{pin.source}）",
                hint=f"运行 fcmd devman install {tool}@{pin.raw}，或修改 {pin.source}",
                code=NOT_INSTALLED,
            ) from exc
        versions[tool] = row.name
    return versions


# ---- PATH 集成 ----
def _store_of(ctx: JobContext, store: _env.EnvStore | None = None) -> _env.EnvStore:
    return store or ctx.env_store or _env.default_env_store()


def _path_plan(  # noqa: PLR0913 - 计划需要写入开关、遮蔽放行与注入点
    ctx: JobContext,
    *,
    write_path: bool,
    allow_shadow: bool,
    append: bool = False,
    store: _env.EnvStore | None = None,
    env: Mapping[str, str] | None = None,
    strict: bool = True,
) -> dict[str, Any]:
    """计算 PATH 集成计划；新增条目会遮蔽现有 node/python 时按需拒绝。"""
    if not write_path:
        return {"enabled": False, "changed": False, "shadowed": {}}
    layout = ctx.layout
    entry = str(layout.shim_dir)
    present = _env.path_entry_present(_store_of(ctx, store).get("Path"), entry)
    shadowed: dict[str, list[str]] = (
        {} if present else _env.shadowed_commands(layout, env=env if env is not None else ctx.env)
    )
    if shadowed and not allow_shadow and strict:
        details = "；".join(f"{tool}: {', '.join(items)}" for tool, items in shadowed.items())
        raise invalid_argument(
            f"把 devman 加入用户 PATH 会遮蔽现有可执行文件：{details}",
            hint="确认要接管时加 --allow-shadow；或用 --no-path 跳过 PATH 集成",
            field="path",
        )
    return {
        "enabled": True,
        "changed": False,
        "entry": entry,
        "position": "append" if append else "prepend",
        "already_present": present,
        "shadowed": shadowed,
    }


def _apply_path(ctx: JobContext, *, append: bool = False, store: _env.EnvStore | None = None) -> dict[str, Any]:
    """生成 shim 并把 shim 目录写入用户 PATH；只记录 devman 实际新增的条目。"""
    layout = ctx.layout
    backend = _store_of(ctx, store)
    written, unchanged = _env.write_shims(layout)
    result = _env.install_path(layout, store=backend, append=append)
    entry = str(result["entry"])
    if result["changed"]:
        if entry not in ctx.state.path_added:
            ctx.state.path_added.append(entry)
        if ctx.state.path_previous_user_path is None and result["previous"] is not None:
            ctx.state.path_previous_user_path = str(result["previous"])
        save_state(layout, ctx.state)
    return {
        "enabled": True,
        "changed": bool(result["changed"]),
        "entry": entry,
        "position": result["position"],
        "shims_written": written,
        "shims_unchanged": unchanged,
    }


def _drop_recorded_paths(ctx: JobContext, *, store: _env.EnvStore | None = None) -> dict[str, Any]:
    """精确移除 state 里记录的 PATH 条目（不整份恢复旧快照）。"""
    entries = list(ctx.state.path_added)
    if not entries:
        return {"enabled": True, "changed": False, "removed": 0, "entries": []}
    backend = _store_of(ctx, store)
    current = backend.get("Path")
    removed = 0
    for entry in entries:
        current, count = _env.remove_path_entry(current, entry)
        removed += count
    if removed:
        backend.set("Path", current or "")
    ctx.state.path_added = []
    save_state(ctx.layout, ctx.state)
    return {"enabled": True, "changed": bool(removed), "removed": removed, "entries": entries}


def _remove_shims(layout: Layout) -> list[str]:
    """删除已知 shim 文件；返回被删除的文件名。"""
    removed: list[str] = []
    if not layout.shim_dir.is_dir():
        return removed
    for item in layout.shim_dir.iterdir():
        if item.is_file():
            item.unlink(missing_ok=True)
            removed.append(item.name)
    with contextlib.suppress(OSError):
        layout.shim_dir.rmdir()
    return removed


@contextmanager
def install_lock(layout: Layout) -> Iterator[None]:
    """安装互斥锁：``O_EXCL`` 创建；超过 30 分钟视为陈旧锁。"""
    layout.root.mkdir(parents=True, exist_ok=True)
    path = layout.lock_file
    deadline = time.time()
    for attempt in range(2):
        try:
            handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            stale = False
            try:
                stale = (deadline - path.stat().st_mtime) > LOCK_STALE_SECONDS
            except OSError:
                stale = True
            if stale and attempt == 0:
                path.unlink(missing_ok=True)
                continue
            raise runtime_error(
                f"另一个 devman 正在安装（锁文件 {path}）",
                hint="等它结束后重试；确认无进程占用可删除该锁文件",
                code=BUSY,
            ) from exc
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(f"pid={os.getpid()} at={now_iso()}\n")
        break
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


# ---- 目标版本解析 ----
def _resolve_node_version(ctx: JobContext, spec: Spec, *, remote_only: bool = False) -> str:
    layout = ctx.layout
    if not remote_only and not spec.channel:
        if _EXACT_RE.fullmatch(spec.raw) and spec.raw:
            return spec.raw
        rows = [row for row in list_installed(layout, "node") if matches(spec.raw, row.version, name=row.name)]
        if rows:
            return max(rows, key=lambda row: sort_key(row.version)).version
    releases = _node.parse_release_index(ctx.fetch().get_text(_node.RELEASE_INDEX))
    return _node.latest_matching(releases, spec.raw)


def _resolve_python_key(ctx: JobContext, spec: Spec, *, remote_only: bool = False) -> str:
    layout = ctx.layout
    if not remote_only and not spec.channel:
        rows = [row for row in list_installed(layout, "python") if matches(spec.raw, row.version, name=row.name)]
        if rows:
            return max(rows, key=lambda row: sort_key(row.version)).name
    uv = _python.find_uv(ctx.config.python_uv)
    return _python.resolve_key(ctx.execute(), uv, spec.raw)


def resolve_target(ctx: JobContext, spec: Spec, *, remote_only: bool = False) -> tuple[str, str]:
    """返回 ``(规范名, 目标版本/安装键)``。"""
    if not spec.raw:
        raise invalid_argument(
            f"{spec.tool} 需要指定版本",
            hint=f"例如 fcmd devman install {spec.tool}@latest",
            field="spec",
        )
    if spec.tool == "node":
        return "node", _resolve_node_version(ctx, spec, remote_only=remote_only)
    return "python", _resolve_python_key(ctx, spec, remote_only=remote_only)


# ---- list ----
def list_payload(
    ctx: JobContext,
    *,
    env: Mapping[str, str] | None = None,
    cwd: Path | str | None = None,
) -> dict[str, Any]:
    """已安装版本 + 活动版本 + 外部探测 + 项目绑定（只读）。"""
    layout = ctx.layout
    tools: dict[str, Any] = {}
    for tool in ("node", "python"):
        installed = list_installed(layout, tool)
        active = active_version(layout, tool)
        tools[tool] = {
            "installed": [row.to_data() for row in installed],
            "active": active,
            "incomplete": [row.name for row in installed if not row.complete],
        }
    detected = _env.detect_environment(layout, env=env)
    project = project_snapshot(ctx, start=cwd)
    warnings = [*ctx.warnings, *[str(item) for item in detected["warnings"]], *project["warnings"]]
    return {
        "schema": "devman/list/1",
        "root": str(layout.root),
        "tools": tools,
        "shims": {
            "dir": str(layout.shim_dir),
            "on_path": detected["shim_on_path"],
            "index": detected["shim_index"],
        },
        "external": detected["path_resolution"],
        "markers": detected["markers"],
        "project": project,
        "warnings": warnings,
    }


# ---- versions ----
def versions_payload(
    ctx: JobContext,
    tool_text: str,
    *,
    remote: bool = False,
    prerelease: bool = False,
) -> dict[str, Any]:
    """已安装版本或远端可用版本（``versions`` 子命令与 GUI 共用同一份数据）。"""
    name = normalize_tool(tool_text)
    installed = list_installed(ctx.layout, name)
    active = ctx.state.active.get(name)
    rows: list[dict[str, Any]] = []
    source: str | None = None
    if remote:
        if name == "node":
            releases = _node.parse_release_index(ctx.fetch().get_text(_node.RELEASE_INDEX))
            seen = {row.version for row in installed}
            for release in sorted(releases, key=lambda item: sort_key(item.version)):
                rows.append(
                    {
                        "version": release.version,
                        "installed": release.version in seen,
                        "active": release.version == active,
                        "lts": release.lts or False,
                    }
                )
            source = _node.RELEASE_INDEX
        else:
            uv = _python.find_uv(ctx.config.python_uv)
            entries = _python.list_remote(ctx.execute(), uv, include_prerelease=prerelease)
            installed_versions = {row.version for row in installed}
            active_version_text = version_of("python", active or "")
            seen_keys: set[str] = set()
            for entry in sorted(entries, key=lambda item: sort_key(item.version)):
                if entry.version in seen_keys:
                    continue
                seen_keys.add(entry.version)
                rows.append(
                    {
                        "version": entry.version,
                        "installed": entry.version in installed_versions,
                        "active": entry.version == active_version_text,
                        "key": entry.key,
                    }
                )
            source = "uv python list --all-versions"
    else:
        for row in installed:
            rows.append(
                {
                    "version": row.version,
                    "installed": row.complete,
                    "active": row.name == active,
                    "name": row.name,
                    "path": str(row.path),
                }
            )
    return {
        "schema": "devman/versions/1",
        "tool": name,
        "remote": remote,
        "source": source,
        "versions": rows,
        "warnings": ctx.warnings,
    }


# ---- install ----
def install(  # noqa: PLR0913 - 安装选项与安全语义一一对应
    ctx: JobContext,
    spec_text: str,
    *,
    dry_run: bool = False,
    force: bool = False,
    sha256: str | None = None,
    source: str | None = None,
    write_path: bool = True,
    allow_shadow: bool = False,
    store: _env.EnvStore | None = None,
    env: Mapping[str, str] | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """安装一个具体版本；``source`` 指定本地 zip 时走离线导入（仅 node）。

    默认把 shim 目录写入用户 PATH，``--dry-run`` 只输出计划。
    """
    spec = parse_spec(spec_text)
    local_artifact: Path | None = None
    if source:
        if spec.tool != "node":
            raise invalid_argument("--from 仅支持 node 构件", hint="python 版本请用 uv 安装", field="from")
        if not _EXACT_RE.fullmatch(spec.raw):
            raise invalid_argument(
                f"--from 需要精确版本号（当前为 {spec.raw or '空'}）",
                hint="例如 fcmd devman install node@22.23.5 --from D:\\node-v22.23.5-win-x64.zip",
                field="spec",
            )
        local_artifact = Path(source)
    tool, target = resolve_target(ctx, spec)
    layout = ctx.layout
    path_plan = _path_plan(
        ctx,
        write_path=write_path,
        allow_shadow=allow_shadow,
        store=store,
        env=env,
        strict=not dry_run,
    )

    if tool == "node":
        plan = (
            _node.plan_local(layout, target, local_artifact, sha256=sha256)
            if local_artifact is not None
            else _node.plan_install(layout, target, mirror=ctx.config.node_mirror, sha256=sha256)
        )
        if dry_run:
            return {
                "schema": "devman/install/1",
                "tool": tool,
                "version": target,
                "changed": False,
                "dry_run": True,
                "planned": plan.to_data(),
                "actions": [],
                "user_path": path_plan,
                "warnings": ctx.warnings,
            }
        with install_lock(layout):
            layout.ensure_bootstrap()
            result = _node.install(
                plan, layout, fetcher=ctx.fetch(), runner=ctx.execute(), force=force, progress=progress
            )
            user_path = _apply_path(ctx, store=store) if write_path else path_plan
    else:
        uv = _python.find_uv(ctx.config.python_uv)
        if dry_run:
            return {
                "schema": "devman/install/1",
                "tool": tool,
                "version": version_of("python", target),
                "key": target,
                "changed": False,
                "dry_run": True,
                "planned": {
                    "tool": "python",
                    "key": target,
                    "install_dir": str(layout.python_root),
                    "uv": uv,
                    "mirror": ctx.config.python_mirror,
                },
                "actions": [],
                "user_path": path_plan,
                "warnings": ctx.warnings,
            }
        with install_lock(layout):
            layout.ensure_bootstrap()
            result = _python.install(
                layout,
                target,
                runner=ctx.execute(),
                uv=uv,
                mirror=ctx.config.python_mirror,
                force=force,
            )
            user_path = _apply_path(ctx, store=store) if write_path else path_plan

    payload = {
        "schema": "devman/install/1",
        "tool": tool,
        "version": result["version"],
        "changed": result["changed"],
        "path": result["path"],
        "message": result["message"],
        "actions": result["actions"],
        "user_path": user_path,
        "warnings": ctx.warnings,
    }
    user_path["shadowed"] = path_plan.get("shadowed", {})
    if user_path.get("enabled"):
        user_path["already_present"] = path_plan.get("already_present", False)
    if tool == "python":
        payload["key"] = target
    return payload


# ---- uninstall ----
def _uv_uninstall(ctx: JobContext, name: str) -> bool:
    """让 uv 自己清理托管解释器（含 junction 别名与元数据）；失败不致命。"""
    try:
        uv = _python.find_uv(ctx.config.python_uv)
        result = ctx.execute().run(
            [uv, "python", "uninstall", name, "--install-dir", str(ctx.layout.python_root), "--no-config"]
        )
    except DevmanError:
        return False
    return result.returncode == 0


def _remove_version(ctx: JobContext, tool: str, name: str) -> dict[str, Any]:
    """先交给 uv（python）再兜底删除；返回残留信息。"""
    layout = ctx.layout
    source = layout.version_dir(tool, name)
    if tool == "python":
        _uv_uninstall(ctx, name)
        if not source.exists():
            return {"leftovers": []}
    trash = layout.tool_root(tool) / f".trash-{name}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    leftovers: list[str] = []
    try:
        source.replace(trash)
    except OSError as exc:
        raise runtime_error(f"无法移走 {source}：{exc}", hint="确认没有进程正在使用该版本") from exc
    shutil.rmtree(trash, ignore_errors=True)
    if trash.exists():
        leftovers.append(str(trash))
    return {"leftovers": leftovers}


def uninstall(  # noqa: PLR0913 - 卸载选项与安全语义一一对应
    ctx: JobContext,
    spec_text: str,
    *,
    dry_run: bool = False,
    yes: bool = False,
    if_present: bool = False,
    force: bool = False,
    purge_cache: bool = False,
    all_versions: bool = False,
    store: _env.EnvStore | None = None,
) -> dict[str, Any]:
    """卸载版本；破坏性操作必须 ``--yes``。"""
    layout = ctx.layout
    spec = parse_spec(spec_text)
    rows = [row for row in list_installed(layout, spec.tool) if matches(spec.raw, row.version, name=row.name)]
    if not rows:
        if if_present:
            return {
                "schema": "devman/uninstall/1",
                "tool": spec.tool,
                "removed": [],
                "changed": False,
                "user_path": {"enabled": False, "changed": False, "removed": 0},
                "warnings": ctx.warnings,
            }
        raise not_found(
            f"未安装 {spec.text}",
            hint="用 fcmd devman list 查看已安装版本",
            code=NOT_INSTALLED,
        )
    rows = sorted(rows, key=lambda row: sort_key(row.version))
    targets = rows if all_versions else [rows[-1]]
    if not yes and not dry_run:
        names = ", ".join(row.version for row in targets)
        raise _confirm_error(names, spec.tool)

    active = active_version(layout, spec.tool)
    clearing = [row for row in targets if row.name == active]
    if clearing and not force and not dry_run:
        raise invalid_argument(
            f"{spec.tool} 的 {active} 是当前活动版本",
            hint="先 fcmd devman use 切到别的版本，或加 --force 清空活动版本后卸载",
            field="spec",
        )

    target_names = {row.name for row in targets}
    remaining = [
        row
        for row in [*list_installed(layout, "node"), *list_installed(layout, "python")]
        if not (row.tool == spec.tool and row.name in target_names)
    ]
    plan = {
        "targets": [{"name": row.name, "version": row.version, "path": str(row.path)} for row in targets],
        "clear_active": bool(clearing),
        "purge_cache": purge_cache,
        "remove_path_entries": [] if remaining else list(ctx.state.path_added),
    }
    if dry_run:
        return {
            "schema": "devman/uninstall/1",
            "tool": spec.tool,
            "changed": False,
            "dry_run": True,
            "planned": plan,
            "removed": [],
            "user_path": {"enabled": True, "changed": False, "removed": 0, "entries": plan["remove_path_entries"]},
            "warnings": ctx.warnings,
        }

    user_path: dict[str, Any] = {"enabled": bool(ctx.state.path_added), "changed": False, "removed": 0, "entries": []}
    with install_lock(layout):
        removed: list[dict[str, str]] = []
        leftovers: list[str] = []
        for row in targets:
            outcome = _remove_version(ctx, spec.tool, row.name)
            leftovers.extend(outcome["leftovers"])
            removed.append({"name": row.name, "version": row.version})
        if clearing:
            set_active(layout, spec.tool, None)
            ctx.state.active.pop(spec.tool, None)
        if purge_cache:
            shutil.rmtree(layout.cache_dir, ignore_errors=True)
        save_state(layout, ctx.state)
        if not [*list_installed(layout, "node"), *list_installed(layout, "python")] and ctx.state.path_added:
            user_path = _drop_recorded_paths(ctx, store=store)

    return {
        "schema": "devman/uninstall/1",
        "tool": spec.tool,
        "changed": bool(removed),
        "removed": removed,
        "cleared_active": bool(clearing),
        "purged_cache": purge_cache,
        "leftovers": leftovers,
        "user_path": user_path,
        "note": "npm 全局包与 uv 托管解释器不在 devman 管理范围；卸载最后版本会移除 devman 写入的用户 PATH 条目",
        "warnings": ctx.warnings,
    }


def _confirm_error(names: str, tool: str):
    from fcmd.apps.devman._errors import confirm_required

    return confirm_required(
        f"卸载 {tool} {names} 会删除该版本目录",
        hint="确认后加 --yes；先用 --dry-run 预览",
    )


# ---- use ----
def use(  # noqa: PLR0913 - 切换选项与 CLI 参数一一对应
    ctx: JobContext,
    spec_text: str,
    *,
    clear: bool = False,
    dry_run: bool = False,
    project: bool = False,
    cwd: Path | str | None = None,
) -> dict[str, Any]:
    """切换活动版本：原子写 ``active/<tool>.txt``；``project`` 只写项目版本文件。"""
    layout = ctx.layout
    spec = parse_spec(spec_text)
    if project:
        return _use_project(ctx, spec, clear=clear, dry_run=dry_run, cwd=cwd)
    if clear:
        if dry_run:
            return {
                "schema": "devman/use/1",
                "tool": spec.tool,
                "changed": False,
                "dry_run": True,
                "active": None,
                "warnings": ctx.warnings,
            }
        set_active(layout, spec.tool, None)
        ctx.state.active.pop(spec.tool, None)
        save_state(layout, ctx.state)
        return {
            "schema": "devman/use/1",
            "tool": spec.tool,
            "changed": True,
            "active": None,
            "warnings": ctx.warnings,
        }

    row = resolve_installed(layout, spec)
    previous = active_version(layout, spec.tool)
    if dry_run:
        return {
            "schema": "devman/use/1",
            "tool": spec.tool,
            "changed": previous != row.name,
            "dry_run": True,
            "active": row.name,
            "previous": previous,
            "warnings": ctx.warnings,
        }
    if previous != row.name:
        set_active(layout, spec.tool, row.name)
        ctx.state.active[spec.tool] = row.name
        save_state(layout, ctx.state)
    payload: dict[str, Any] = {
        "schema": "devman/use/1",
        "tool": spec.tool,
        "changed": previous != row.name,
        "active": row.name,
        "previous": previous,
        "path": str(executable_path(layout, spec.tool, row.name)),
        "warnings": ctx.warnings,
    }
    if spec.tool == "python":
        payload["hint"] = _env.python_hint(layout, row.name)
    return payload


def _use_project(
    ctx: JobContext,
    spec: Spec,
    *,
    clear: bool,
    dry_run: bool,
    cwd: Path | str | None,
) -> dict[str, Any]:
    """把版本绑定写进当前目录的 ``.node-version`` / ``.python-version``。"""
    directory = Path(cwd or Path.cwd()).resolve()
    path_file = directory / _project.VERSION_FILES[spec.tool]
    warnings = list(ctx.warnings)
    shadow_pin = _project.read_directory(directory)[0].get(spec.tool)
    if shadow_pin is not None and shadow_pin.kind == _project.PROJECT_FILE:
        warnings.append(f"同目录 {shadow_pin.source} 已绑定 {spec.tool}，单行版本文件不会生效")

    if clear:
        changed = False if dry_run else _project.remove_pin(directory, spec.tool)
        return {
            "schema": "devman/use/1",
            "tool": spec.tool,
            "changed": changed,
            "dry_run": dry_run,
            "active": active_version(ctx.layout, spec.tool),
            "project": {"file": str(path_file), "raw": None, "changed": changed},
            "warnings": warnings,
        }

    row = resolve_installed(ctx.layout, spec)
    raw = spec.raw or row.version
    try:
        current = path_file.read_text(encoding="utf-8").strip()
    except OSError:
        current = None
    if not dry_run and current != raw:
        _project.write_pin(directory, spec.tool, raw)
    return {
        "schema": "devman/use/1",
        "tool": spec.tool,
        "changed": current != raw,
        "dry_run": dry_run,
        "active": active_version(ctx.layout, spec.tool),
        "project": {
            "file": str(path_file),
            "raw": raw,
            "resolved": row.name,
            "path": str(row.path),
            "changed": current != raw,
        },
        "warnings": warnings,
    }


# ---- upgrade ----
def upgrade(  # noqa: PLR0913 - 升级选项与安全语义一一对应
    ctx: JobContext,
    tool_text: str,
    *,
    to: str | None = None,
    purge_old: bool = False,
    yes: bool = False,
    dry_run: bool = False,
    force: bool = False,
    write_path: bool = True,
    allow_shadow: bool = False,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """升级到同一条版本线的最新版（或 ``--to`` 指定版本）。"""
    layout = ctx.layout
    spec = parse_spec(tool_text)
    tool = spec.tool
    rows = [row for row in list_installed(layout, tool) if row.complete]
    if not rows:
        raise not_found(f"未安装任何 {tool} 版本", hint=f"先运行 fcmd devman install {tool}@latest", code=NOT_INSTALLED)

    active = active_version(layout, tool)
    current = next((row for row in rows if row.name == active), max(rows, key=lambda row: sort_key(row.version)))
    current_parts = sort_key(current.version)
    raw = (to or "").strip()
    if not raw:
        raw = str(current_parts[0]) if tool == "node" else f"{current_parts[0]}.{current_parts[1]}"

    target_spec = Spec(tool, raw)
    _, target = resolve_target(ctx, target_spec, remote_only=True)
    if tool == "python":
        target_version = version_of("python", target)
    else:
        target_version = target
    if sort_key(target_version) <= sort_key(current.version):
        return {
            "schema": "devman/upgrade/1",
            "tool": tool,
            "current": current.version,
            "target": target_version,
            "changed": False,
            "message": "已是最新（按当前版本线）",
            "warnings": ctx.warnings,
        }

    if purge_old and not yes and not dry_run:
        raise _confirm_error(current.version, tool)

    plan = {
        "tool": tool,
        "current": current.name,
        "target": target,
        "install": target,
        "activate": target,
        "purge": current.name if purge_old else None,
        "add_to_user_path": write_path,
    }
    if dry_run:
        return {
            "schema": "devman/upgrade/1",
            "tool": tool,
            "changed": False,
            "dry_run": True,
            "planned": plan,
            "warnings": ctx.warnings,
        }

    installed = install(
        ctx,
        f"{tool}@{target_version}",
        force=force,
        write_path=write_path,
        allow_shadow=allow_shadow,
        progress=progress,
    )
    switched = use(ctx, f"{tool}@{target_version}")
    purged: dict[str, Any] | None = None
    if purge_old:
        purged = uninstall(ctx, f"{tool}@{current.name}", yes=True)
    return {
        "schema": "devman/upgrade/1",
        "tool": tool,
        "changed": bool(installed.get("changed") or switched.get("changed")),
        "current": current.version,
        "target": target_version,
        "installed": installed,
        "use": switched,
        "purged": purged,
        "warnings": ctx.warnings,
    }


# ---- init / uninit ----
def init(  # noqa: PLR0913 - 可选注入项用于测试与 GUI 复用
    ctx: JobContext,
    *,
    write_path: bool = True,
    append: bool = False,
    allow_shadow: bool = False,
    dry_run: bool = False,
    store: _env.EnvStore | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """生成 shim，并默认把 shim 目录写入用户 PATH（``--no-path`` 关闭）。"""
    layout = ctx.layout
    path_plan = _path_plan(
        ctx,
        write_path=write_path,
        allow_shadow=allow_shadow,
        append=append,
        store=store,
        env=env,
        strict=not dry_run,
    )
    if dry_run:
        return {
            "schema": "devman/init/1",
            "changed": False,
            "dry_run": True,
            "planned": {
                "shim_dir": str(layout.shim_dir),
                "shims": list(_env.shim_scripts()),
                "add_to_user_path": write_path,
                "position": path_plan.get("position", "prepend"),
                "already_present": path_plan.get("already_present", False),
                "shadowed": path_plan.get("shadowed", {}),
            },
            "warnings": ctx.warnings,
        }
    if write_path:
        applied = _apply_path(ctx, append=append, store=store)
        written = list(applied.pop("shims_written"))
        unchanged = list(applied.pop("shims_unchanged"))
        applied["shadowed"] = path_plan.get("shadowed", {})
        applied["already_present"] = path_plan.get("already_present", False)
        user_path = applied
    else:
        written, unchanged = _env.write_shims(layout)
        user_path = {"enabled": False, "changed": False}
    return {
        "schema": "devman/init/1",
        "changed": bool(written or user_path["changed"]),
        "shims_written": written,
        "shims_unchanged": unchanged,
        "user_path": user_path,
        "warnings": ctx.warnings,
    }


def uninit(
    ctx: JobContext,
    *,
    write_path: bool = True,
    dry_run: bool = False,
    store: _env.EnvStore | None = None,
) -> dict[str, Any]:
    """删除 shim，并精确移除 devman 写入的用户 PATH 条目（``--no-path`` 保留）。"""
    layout = ctx.layout
    recorded = list(ctx.state.path_added)
    if dry_run:
        return {
            "schema": "devman/uninit/1",
            "changed": False,
            "dry_run": True,
            "planned": {
                "remove_shims": str(layout.shim_dir),
                "remove_path_entries": recorded if write_path else [],
            },
            "warnings": ctx.warnings,
        }
    removed = _remove_shims(layout)
    user_path: dict[str, Any] = {"enabled": write_path, "changed": False, "removed": 0, "entries": []}
    if write_path:
        user_path = _drop_recorded_paths(ctx, store=store)
    return {
        "schema": "devman/uninit/1",
        "changed": bool(removed or user_path["changed"]),
        "removed_shims": removed,
        "user_path": user_path,
        "warnings": ctx.warnings,
    }


# ---- env / exec ----
def env_payload(
    ctx: JobContext,
    *,
    shell: str = "pwsh",
    cwd: Path | str | None = None,
) -> dict[str, Any]:
    """打印当前活动版本的激活片段（不落盘）；项目绑定时改用项目版本。"""
    layout = ctx.layout
    pins = project_versions(ctx, start=cwd)
    project = project_snapshot(ctx, start=cwd)
    return {
        "schema": "devman/env/1",
        "shell": shell,
        "snippet": _env.shell_snippet(layout, shell, pins=pins),
        "dirs": [str(item) for item in _env.path_dirs(layout, pins=pins)],
        "active": {
            tool: pins.get(tool) or active_version(layout, tool) for tool in ("node", "python")
        },
        "project": project,
        "warnings": [*ctx.warnings, *project["warnings"]],
    }


# ---- doctor ----
def _check(name: str, status: str, detail: str, hint: str | None = None) -> dict[str, Any]:
    return {"name": name, "status": status, "detail": detail, "hint": hint}


def doctor_payload(  # noqa: PLR0912 - 诊断项按类别线性展开，便于逐条阅读
    ctx: JobContext,
    *,
    env: Mapping[str, str] | None = None,
    cwd: Path | str | None = None,
) -> dict[str, Any]:
    """只读诊断；任一 ``fail`` 时整体退出 5（由命令层判定）。"""
    layout = ctx.layout
    checks: list[dict[str, Any]] = []

    # 1. 根目录可写
    try:
        if layout.root.exists():
            writable = os.access(layout.root, os.W_OK)
            detail = str(layout.root)
        else:
            parent = layout.root.parent
            writable = parent.exists() and os.access(parent, os.W_OK)
            detail = f"{layout.root}（尚不存在，检查父目录 {parent}）"
        checks.append(
            _check(
                "root_writable",
                "ok" if writable else "fail",
                detail,
                None if writable else "换一个可写目录：--root <dir> 或设置 DEVMAN_ROOT",
            )
        )
    except OSError as exc:
        checks.append(_check("root_writable", "fail", f"{layout.root}: {exc}", "检查路径权限"))

    # 2. 活动版本一致性
    for tool in ("node", "python"):
        name = active_version(layout, tool)
        if name is None:
            checks.append(_check(f"active_{tool}", "ok", "未设置活动版本"))
            continue
        exe = executable_path(layout, tool, name)
        complete = exe.is_file()
        status = "ok" if complete else "fail"
        checks.append(
            _check(
                f"active_{tool}",
                status,
                f"{name} → {exe}",
                None if complete else "运行 fcmd devman use 切换，或重新安装该版本",
            )
        )

    # 3. shim 状态
    scripts = _env.shim_scripts()
    missing = [name for name in scripts if not (layout.shim_dir / name).is_file()]
    mismatched = [
        name
        for name in scripts
        if (layout.shim_dir / name).is_file()
        and (layout.shim_dir / name).read_bytes().decode("utf-8", errors="replace") != scripts[name]
    ]
    if missing:
        checks.append(_check("shims", "warn", f"缺少 shim：{', '.join(missing)}", "运行 fcmd devman init 重新生成"))
    elif mismatched:
        checks.append(_check("shims", "warn", f"shim 内容已过期：{', '.join(mismatched)}", "运行 fcmd devman init"))
    else:
        checks.append(_check("shims", "ok", f"{layout.shim_dir}（{len(scripts)} 个）"))

    # 4. 残留临时目录 / 半完成安装
    stray: list[str] = []
    for tool in ("node", "python"):
        root = layout.tool_root(tool)
        if not root.is_dir():
            continue
        for entry in root.iterdir():
            alias = entry.is_symlink() or os.path.isjunction(entry)
            if entry.name.startswith((".staging-", ".trash-")) or (
                entry.is_dir() and not entry.name.startswith(".") and not alias and not (entry / MARKER_NAME).is_file()
            ):
                stray.append(str(entry))
    partials = [str(item) for item in layout.cache_dir.glob("*.part")] if layout.cache_dir.is_dir() else []
    if stray or partials:
        checks.append(
            _check(
                "leftovers",
                "warn",
                "；".join([*stray, *partials])[:500],
                "重新 install 对应版本可自动清理；缓存 .part 可安全删除",
            )
        )
    else:
        checks.append(_check("leftovers", "ok", "无残留"))

    # 5. 环境探测
    detected = _env.detect_environment(layout, env=env)
    checks.append(
        _check(
            "shim_on_path",
            "ok" if detected["shim_on_path"] else "warn",
            f"{layout.shim_dir} 在 PATH 顺位 {detected['shim_index']}"
            if detected["shim_on_path"]
            else "shim 目录不在用户 PATH",
            None if detected["shim_on_path"] else "运行 fcmd devman init（默认写入用户 PATH，可用 --no-path 关闭）",
        )
    )
    for marker_name, value in dict(detected["markers"]).items():
        checks.append(_check(f"env_{marker_name.lower()}", "warn", f"{marker_name}={value}", "devman 不会接管该环境"))

    # 6. 项目版本绑定
    project = project_snapshot(ctx, start=cwd)
    if not project["pins"]:
        checks.append(_check("project", "ok", "未检测到项目版本文件"))
    else:
        missing_pins = [
            f"{tool}@{item['raw']}（{item['source']}）"
            for tool, item in project["pins"].items()
            if not item["installed"]
        ]
        if missing_pins:
            checks.append(
                _check(
                    "project",
                    "warn",
                    ("项目要求的版本未安装：" + "、".join(missing_pins))[:400],
                    "运行 fcmd devman install <tool>@<version>，或 use --project --clear <tool> 取消绑定",
                )
            )
        else:
            detail = "；".join(
                f"{tool}@{item['resolved']}（{item['source']}）" for tool, item in project["pins"].items()
            )
            checks.append(_check("project", "ok", detail[:400]))

    # 7. uv（仅 python 相关时提示缺失）
    try:
        uv = _python.find_uv(ctx.config.python_uv)
        probe = ctx.execute().run([uv, "--version"])
        version_text = (probe.stdout or probe.stderr).strip() or "未知版本"
        checks.append(_check("uv", "ok" if probe.returncode == 0 else "warn", f"{uv}（{version_text}）"))
    except Exception as exc:  # 诊断命令不应因单点探测失败整体崩掉
        checks.append(_check("uv", "warn", str(exc), "安装 uv 后才能获取新的 Python 版本"))

    # 8. 网络配置（只读，不联网）
    proxy = normalize_proxy(ctx.config.proxy)
    if proxy is None:
        proxy_text, proxy_ok = "代理跟随系统", True
    elif proxy == "":
        proxy_text, proxy_ok = "显式禁用代理", True
    else:
        scheme = proxy.split("://", 1)[0].lower()
        proxy_ok = scheme in {"http", "https"} and " " not in proxy
        proxy_text = f"显式代理 {proxy}"
    checks.append(
        _check(
            "network",
            "ok" if proxy_ok else "warn",
            f"node.mirror={ctx.config.node_mirror}；python.mirror={ctx.config.python_mirror or '默认'}；"
            f"{proxy_text}；超时 {ctx.config.timeout_s}s",
            None if proxy_ok else "urllib 只支持 http/https 代理；SOCKS 请改用系统代理或本地转发",
        )
    )

    # 9. 平台能力
    if sys.platform == "win32":
        checks.append(_check("platform", "ok", "Windows：支持安装 / 卸载"))
    else:
        checks.append(_check("platform", "warn", "非 Windows：仅支持查看与诊断", "安装能力仅在 Windows 提供"))

    failed = [item for item in checks if item["status"] == "fail"]
    return {
        "schema": "devman/doctor/1",
        "ok": not failed,
        "root": str(layout.root),
        "checks": checks,
        "project": project,
        "warnings": ctx.warnings,
    }


# ---- 可运维性 ----
def size_payload(ctx: JobContext, *, tool: str | None = None) -> dict[str, Any]:
    """磁盘占用：各版本目录、下载缓存与 shim（只读）。"""
    payload = _maintenance.size(ctx.layout, tool=normalize_tool(tool) if tool else None)
    payload["warnings"] = ctx.warnings
    return payload


def verify_payload(ctx: JobContext, *, tool: str | None = None) -> dict[str, Any]:
    """完整性检查：标记、可执行文件、活动版本、shim 与安装残留（只读）。"""
    payload = _maintenance.verify(ctx.layout, tool=normalize_tool(tool) if tool else None)
    payload["warnings"] = ctx.warnings
    return payload


def repair_payload(
    ctx: JobContext,
    *,
    tool: str | None = None,
    dry_run: bool = False,
    yes: bool = False,
    purge_incomplete: bool = False,
) -> dict[str, Any]:
    """自动修复可处理的问题；需要 ``--yes``（``--dry-run`` 只出计划）。"""
    if not yes and not dry_run:
        raise confirm_required(
            "repair 会修改 shim / 删除残留 / 清空失效活动版本",
            hint="先运行 fcmd devman repair --dry-run 查看计划",
        )
    payload = _maintenance.repair(
        ctx.layout,
        tool=normalize_tool(tool) if tool else None,
        dry_run=dry_run,
        purge_incomplete=purge_incomplete,
    )
    payload["warnings"] = ctx.warnings
    return payload


def export_payload(
    ctx: JobContext,
    *,
    output: str | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """导出锁文件：已安装版本 + 活动版本 + 校验信息；``output`` 为空时只返回内容。"""
    lock = _maintenance.export_lock(ctx.layout)
    written = False
    target: Path | None = None
    if output:
        target = Path(output).expanduser().resolve()
        if target.exists() and not force:
            raise invalid_argument(f"目标已存在：{target}", hint="加 --force 覆盖", field="output")
        if not target.parent.is_dir():
            raise invalid_argument(f"输出目录不存在：{target.parent}", field="output")
        if not dry_run:
            write_atomic(target, json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            written = True
    return {
        "schema": "devman/export/1",
        "lock": lock,
        "output": str(target) if target else None,
        "written": written,
        "dry_run": dry_run,
        "warnings": ctx.warnings,
    }


def restore_payload(  # noqa: PLR0913 - 恢复选项与 CLI 参数一一对应
    ctx: JobContext,
    lock_path: str,
    *,
    yes: bool = False,
    dry_run: bool = False,
    write_path: bool = True,
    allow_shadow: bool = False,
) -> dict[str, Any]:
    """按锁文件补齐缺失版本并切换活动版本；安装复用 :func:`install`。"""
    lock = _maintenance.load_lock(Path(lock_path).expanduser().resolve())
    plan = _maintenance.lock_plan(ctx.layout, lock)
    changed = bool(plan["installs"] or plan["use"])
    if changed and not yes and not dry_run:
        raise confirm_required(
            "restore 会安装 / 切换版本",
            hint="先运行 fcmd devman restore <lock> --dry-run 查看计划",
        )
    installed: list[str] = []
    used: list[str] = []
    if not dry_run:
        for item in plan["installs"]:
            install(
                ctx,
                item["spec"],
                force=item["force"],
                write_path=write_path,
                allow_shadow=allow_shadow,
            )
            installed.append(item["spec"])
        for item in plan["use"]:
            use(ctx, item["spec"])
            used.append(item["spec"])
    return {
        "schema": "devman/restore/1",
        "lock": str(lock_path),
        "changed": changed,
        "dry_run": dry_run,
        "planned": plan,
        "installed": installed,
        "used": used,
        "warnings": ctx.warnings,
    }
