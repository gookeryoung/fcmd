"""devman 命令行入口：子命令注册、双读输出与错误映射。"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from typing import Any, Literal

import fcmd
from fcmd.apps.devman import _env, _ops
from fcmd.apps.devman._errors import DevmanError, invalid_argument, not_found, runtime_error
from fcmd.core.result import emit_result, record_error, report_error

__all__ = ["main"]

ShellName = Literal["pwsh", "cmd", "bash"]

_WARN_PREFIX = "warn:"


def _warn(payload: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        return
    for item in payload.get("warnings") or []:
        print(f"{_WARN_PREFIX} {item}", file=sys.stderr)


def _finish(
    subcommand: str,
    payload: dict[str, Any],
    *,
    json_output: bool,
    render: Any = None,
    ok: bool = True,
) -> None:
    """统一出口：机读写信封，人读写渲染；``ok=False`` 时登记退出码 5。"""
    _warn(payload, json_output=json_output)
    if json_output:
        emit_result("devman", subcommand, payload, ok=ok)
    elif render is not None:
        render(payload)
    if not ok:
        record_error("INCOMPLETE_INSTALL", kind="check")


def _fail(subcommand: str, exc: DevmanError, *, json_output: bool) -> None:
    report_error(
        "devman",
        subcommand,
        code=exc.code,
        message=exc.message,
        hint=exc.hint,
        field=exc.field,
        expected=exc.expected,
        kind=exc.kind,
        json_output=json_output,
    )


def _human_install(payload: dict[str, Any]) -> None:
    print(payload.get("message") or "完成")
    if payload.get("dry_run") and payload.get("planned"):
        planned = payload["planned"]
        if planned.get("local"):
            print(f"  构件: {planned['artifact']}")
            print(f"  本地来源: {planned['local']}")
            print(f"  校验: {planned['sha256_url'] or '--sha256 或同目录校验文件'}")
            print(f"  目标: {planned['dest']}")
        elif "urls" in planned:
            print(f"  构件: {planned['artifact']}")
            for url in planned["urls"]:
                print(f"  来源: {url}")
            print(f"  校验清单: {planned['sha256_url']}")
            print(f"  目标: {planned['dest']}")
        else:
            print(f"  解释器: {planned['key']}")
            print(f"  安装目录: {planned['install_dir']}")
    elif payload.get("path"):
        print(f"  路径: {payload['path']}")
    _print_user_path(payload.get("user_path") or {}, dry_run=bool(payload.get("dry_run")))


def _print_user_path(data: dict[str, Any], *, dry_run: bool) -> None:
    if not data.get("enabled"):
        return
    position = "追加" if data.get("position") == "append" else "前置"
    if dry_run:
        if data.get("already_present"):
            print("  用户 PATH: 已包含 devman（无需修改）")
        else:
            print(f"  用户 PATH: 将{position} {data.get('entry')}")
        return
    if data.get("changed"):
        print(f"  用户 PATH: 已{position} {data.get('entry')}")
    else:
        print("  用户 PATH: 无需修改（已包含 devman）")


def _human_uninstall(payload: dict[str, Any]) -> None:
    user_path = payload.get("user_path") or {}
    if payload.get("dry_run"):
        print("计划卸载：")
        for item in (payload.get("planned") or {}).get("targets", []):
            print(f"  {item['version']}  {item['path']}")
        if (payload.get("planned") or {}).get("clear_active"):
            print("  该版本是活动版本，将清空活动标记")
        if (payload.get("planned") or {}).get("purge_cache"):
            print("  同时清空下载缓存")
        for entry in (payload.get("planned") or {}).get("remove_path_entries") or []:
            print(f"  卸载最后一个版本，将移除用户 PATH 条目: {entry}")
        return
    removed = payload.get("removed") or []
    if not removed:
        print("没有可卸载的版本")
        return
    for item in removed:
        print(f"已卸载 {payload['tool']} {item['version']}")
    if payload.get("cleared_active"):
        print("  活动版本已清空")
    if payload.get("purged_cache"):
        print("  下载缓存已清空")
    for leftover in payload.get("leftovers") or []:
        print(f"  残留（可手工删除）：{leftover}")
    if user_path.get("changed"):
        print(f"  用户 PATH: 已精确移除 {user_path.get('removed')} 个 devman 条目")
    if payload.get("note"):
        print(f"  说明: {payload['note']}")


def _human_use(payload: dict[str, Any]) -> None:
    project = payload.get("project")
    if project:
        raw = project.get("raw")
        if raw is None:
            state = "已删除" if project.get("changed") else "不存在"
            print(f"{payload['tool']} 项目绑定{state}: {project['file']}")
        else:
            state = "已写入" if project.get("changed") else "未变化"
            print(f"{payload['tool']}@{raw} 项目绑定{state}: {project['file']}")
            print(f"  解析为 {project.get('resolved')}；全局活动版本未改（{payload.get('active') or '未设置'}）")
        return
    if payload.get("active") is None:
        print(f"{payload['tool']} 活动版本已清空")
        return
    changed = "已切换" if payload.get("changed") else "已是活动版本"
    print(f"{changed}: {payload['tool']} {payload['active']}")
    if payload.get("hint"):
        print(f"  {payload['hint']}")


def _human_upgrade(payload: dict[str, Any]) -> None:
    if payload.get("dry_run"):
        planned = payload.get("planned") or {}
        print(f"计划升级 {planned.get('current')} → {planned.get('target')}")
        if planned.get("purge"):
            print(f"  升级后卸载 {planned['purge']}")
        return
    if not payload.get("changed"):
        print(payload.get("message") or "已是最新")
        return
    print(f"已升级 {payload['tool']} {payload.get('current')} → {payload.get('target')}")
    if payload.get("purged"):
        print("  旧版本已卸载")


def _human_list(payload: dict[str, Any]) -> None:
    print(f"root: {payload['root']}")
    tools = payload.get("tools") or {}
    for tool in ("node", "python"):
        rows = tools.get(tool, {}).get("installed") or []
        active = tools.get(tool, {}).get("active")
        print(f"{tool}:")
        if not rows:
            print("  (未安装)")
            continue
        for row in rows:
            mark = "*" if row["name"] == active else " "
            status = "" if row["status"] == "ok" else "  [半完成]"
            print(f" {mark} {row['version']:<12} {row['path']}{status}")
    external = payload.get("external") or {}
    for tool, paths in external.items():
        for path in paths:
            print(f"外部 {tool}: {path}")
    shims = payload.get("shims") or {}
    where = f"顺位 {shims['index']}" if shims.get("on_path") else "不在 PATH"
    print(f"shim: {shims.get('dir')}（{where}）")
    pins = (payload.get("project") or {}).get("pins") or {}
    for tool, item in pins.items():
        state = f"→ {item['resolved']}" if item.get("installed") else "（未安装）"
        print(f"项目 {tool}: {item['raw']} {state}  {item['source']}")
    markers = payload.get("markers") or {}
    for key, value in markers.items():
        print(f"{key}={value}")


def _human_versions(payload: dict[str, Any]) -> None:
    rows = payload.get("versions") or []
    if not rows:
        print("没有可显示的版本")
        return
    for row in rows:
        flags: list[str] = []
        if row.get("installed"):
            flags.append("已安装")
        if row.get("active"):
            flags.append("活动")
        if row.get("lts"):
            flags.append(f"LTS {row['lts']}")
        suffix = f"  ({', '.join(flags)})" if flags else ""
        print(f"{row['version']}{suffix}")
    if payload.get("source"):
        print(f"来源: {payload['source']}")


def _human_doctor(payload: dict[str, Any]) -> None:
    for check in payload.get("checks") or []:
        print(f"[{check['status']}] {check['name']}: {check['detail']}")
        if check.get("hint") and check["status"] != "ok":
            print(f"       {check['hint']}")
    print("结论: " + ("通过" if payload.get("ok") else "存在失败项"))


# ---------------------------------------------------------------------- #
# 子命令
# ---------------------------------------------------------------------- #


@fcmd.tool(
    "devman",
    subcommand="list",
    help="列出已安装版本、活动版本与外部 node/python 探测结果",
    param_help={"root": "devman 根目录（默认 %LOCALAPPDATA%\\devman）"},
)
def list_versions(root: str = "", *, json_output: bool = False) -> None:
    """只读：已安装版本 + 活动版本 + external 探测。"""
    try:
        ctx = _ops.new_context(root)
        payload = _ops.list_payload(ctx)
    except DevmanError as exc:
        _fail("list", exc, json_output=json_output)
        return
    _finish("list", payload, json_output=json_output, render=_human_list)


@fcmd.tool(
    "devman",
    subcommand="versions",
    help="列出某个工具的版本（--remote 读远端索引）",
    param_help={
        "tool": "node / python",
        "remote": "读取远端索引（不下载构件）",
        "prerelease": "包含预发布版本（仅 python 远端列表）",
        "root": "devman 根目录",
    },
)
def versions(
    tool: str,
    remote: bool = False,
    prerelease: bool = False,
    root: str = "",
    *,
    json_output: bool = False,
) -> None:
    """已安装版本或远端可用版本。"""
    subcommand = "versions"
    try:
        ctx = _ops.new_context(root)
        payload = _ops.versions_payload(ctx, tool, remote=remote, prerelease=prerelease)
    except DevmanError as exc:
        _fail(subcommand, exc, json_output=json_output)
        return
    _finish(subcommand, payload, json_output=json_output, render=_human_versions)


@fcmd.tool(
    "devman",
    subcommand="install",
    help="安装指定版本的 node / python（默认把 shim 目录写入用户 PATH）",
    param_help={
        "spec": "版本规格，如 node@20.11.1、python@3.12、node@lts",
        "dry_run": "只输出计划，不建目录、不下载、不改状态、不写 PATH",
        "force": "已安装时强制重装",
        "sha256": "显式指定构件 sha256（官方清单不可达时用）",
        "from_path": "离线导入本地 node zip（需精确版本号；校验值取 <zip>.sha256 / SHASUMS256.txt）",
        "no_path": "不把 shim 目录写入用户 PATH",
        "allow_shadow": "允许 devman 的 shim 遮蔽已有 node/python",
        "root": "devman 根目录",
    },
)
def install(  # noqa: PLR0913, PLR0917 - CLI 参数与 ADR D10 的命令面一一对应
    spec: str,
    dry_run: bool = False,
    force: bool = False,
    sha256: str = "",
    from_path: str = "",
    no_path: bool = False,
    allow_shadow: bool = False,
    root: str = "",
    *,
    json_output: bool = False,
) -> None:
    """安装一个版本，默认完成用户 PATH 集成。"""
    subcommand = "install"
    try:
        ctx = _ops.new_context(root)
        payload = _ops.install(
            ctx,
            spec,
            dry_run=dry_run,
            force=force,
            sha256=sha256 or None,
            source=from_path or None,
            write_path=not no_path,
            allow_shadow=allow_shadow,
        )
    except DevmanError as exc:
        _fail(subcommand, exc, json_output=json_output)
        return
    _finish(subcommand, payload, json_output=json_output, render=_human_install)


@fcmd.tool(
    "devman",
    subcommand="uninstall",
    help="卸载版本（破坏性操作需要 --yes）",
    param_help={
        "spec": "版本规格，如 node@20.11.1；省略版本表示最高匹配",
        "yes": "确认执行破坏性操作",
        "dry_run": "只输出计划",
        "if_present": "目标不存在时视为成功（幂等脚本用）",
        "force": "活动版本也强制卸载（先清空活动标记）",
        "purge_cache": "同时清空下载缓存",
        "all_versions": "卸载所有匹配版本",
        "root": "devman 根目录",
    },
)
def uninstall(  # noqa: PLR0913, PLR0917 - CLI 参数与 ADR D10 的命令面一一对应
    spec: str,
    yes: bool = False,
    dry_run: bool = False,
    if_present: bool = False,
    force: bool = False,
    purge_cache: bool = False,
    all_versions: bool = False,
    root: str = "",
    *,
    json_output: bool = False,
) -> None:
    """删除版本目录（保留其余版本与用户数据）。"""
    subcommand = "uninstall"
    try:
        ctx = _ops.new_context(root)
        payload = _ops.uninstall(
            ctx,
            spec,
            dry_run=dry_run,
            yes=yes,
            if_present=if_present,
            force=force,
            purge_cache=purge_cache,
            all_versions=all_versions,
        )
    except DevmanError as exc:
        _fail(subcommand, exc, json_output=json_output)
        return
    _finish(subcommand, payload, json_output=json_output, render=_human_uninstall)


@fcmd.tool(
    "devman",
    subcommand="use",
    help="切换活动版本（原子写 active/<tool>.txt）；--project 只写当前目录的版本文件",
    param_help={
        "spec": "版本规格；配合 --clear 时只需工具名",
        "clear": "清空活动版本（配合 --project 时删除当前目录的版本文件）",
        "project": "写当前目录的 .node-version / .python-version，不改全局活动版本",
        "root": "devman 根目录",
    },
)
def use(  # noqa: PLR0913 - CLI 参数与 ADR 命令面一一对应
    spec: str,
    clear: bool = False,
    dry_run: bool = False,
    project: bool = False,
    root: str = "",
    *,
    json_output: bool = False,
) -> None:
    """切换活动版本；``--project`` 时只写项目文件；都不修改 PATH。"""
    subcommand = "use"
    try:
        ctx = _ops.new_context(root)
        payload = _ops.use(ctx, spec, clear=clear, dry_run=dry_run, project=project)
    except DevmanError as exc:
        _fail(subcommand, exc, json_output=json_output)
        return
    _finish(subcommand, payload, json_output=json_output, render=_human_use)


@fcmd.tool(
    "devman",
    subcommand="upgrade",
    help="升级到当前版本线的最新版（或 --to 指定版本）",
    param_help={
        "tool": "node / python",
        "to": "目标版本或通道（默认当前主版本线最新）",
        "purge_old": "升级成功后卸载旧版本（需要 --yes）",
        "yes": "确认卸载旧版本",
        "dry_run": "只输出计划",
        "force": "强制重装目标版本",
        "no_path": "不把 shim 目录写入用户 PATH",
        "allow_shadow": "允许 devman 的 shim 遮蔽已有 node/python",
        "root": "devman 根目录",
    },
)
def upgrade(  # noqa: PLR0913, PLR0917 - CLI 参数与 ADR D10 的命令面一一对应
    tool: str,
    to: str = "",
    purge_old: bool = False,
    yes: bool = False,
    dry_run: bool = False,
    force: bool = False,
    no_path: bool = False,
    allow_shadow: bool = False,
    root: str = "",
    *,
    json_output: bool = False,
) -> None:
    """安装新版本并切换。"""
    subcommand = "upgrade"
    try:
        ctx = _ops.new_context(root)
        payload = _ops.upgrade(
            ctx,
            tool,
            to=to or None,
            purge_old=purge_old,
            yes=yes,
            dry_run=dry_run,
            force=force,
            write_path=not no_path,
            allow_shadow=allow_shadow,
        )
    except DevmanError as exc:
        _fail(subcommand, exc, json_output=json_output)
        return
    _finish(subcommand, payload, json_output=json_output, render=_human_upgrade)


@fcmd.tool(
    "devman",
    subcommand="init",
    help="生成 shim 并把 shim 目录写入用户 PATH（--no-path 只生成 shim）",
    param_help={
        "no_path": "只生成 shim，不改用户 PATH",
        "append": "追加到 PATH 末尾而不是最前",
        "allow_shadow": "允许遮蔽已有 node/python",
        "dry_run": "只输出计划",
        "root": "devman 根目录",
    },
)
def init(  # noqa: PLR0913 - CLI 参数与 ADR D10 的命令面一一对应
    no_path: bool = False,
    append: bool = False,
    allow_shadow: bool = False,
    dry_run: bool = False,
    root: str = "",
    *,
    json_output: bool = False,
) -> None:
    """生成 shim 并写入用户 PATH（默认）。"""
    subcommand = "init"
    try:
        ctx = _ops.new_context(root)
        payload = _ops.init(
            ctx,
            write_path=not no_path,
            append=append,
            allow_shadow=allow_shadow,
            dry_run=dry_run,
        )
    except DevmanError as exc:
        _fail(subcommand, exc, json_output=json_output)
        return

    def render(data: dict[str, Any]) -> None:
        if data.get("dry_run"):
            print(f"shim 目录: {data['planned']['shim_dir']}")
            print(f"将生成: {', '.join(data['planned']['shims'])}")
            _print_user_path(
                {
                    "enabled": data["planned"]["add_to_user_path"],
                    "position": data["planned"]["position"],
                    "entry": data["planned"]["shim_dir"],
                    "already_present": data["planned"]["already_present"],
                },
                dry_run=True,
            )
            for tool, items in (data["planned"].get("shadowed") or {}).items():
                print(f"  会遮蔽 {tool}: {', '.join(items)}")
            return
        if data.get("shims_written"):
            print(f"已生成 shim: {', '.join(data['shims_written'])}")
        else:
            print("shim 已是最新")
        _print_user_path(data.get("user_path") or {}, dry_run=False)

    _finish(subcommand, payload, json_output=json_output, render=render)


@fcmd.tool(
    "devman",
    subcommand="uninit",
    help="删除 shim 并精确移除 devman 写入的用户 PATH 条目（--no-path 保留 PATH）",
    param_help={
        "no_path": "保留用户 PATH 中的 devman 条目",
        "dry_run": "只输出计划",
        "root": "devman 根目录",
    },
)
def uninit(no_path: bool = False, dry_run: bool = False, root: str = "", *, json_output: bool = False) -> None:
    """撤销 shim / PATH 集成。"""
    subcommand = "uninit"
    try:
        ctx = _ops.new_context(root)
        payload = _ops.uninit(ctx, write_path=not no_path, dry_run=dry_run)
    except DevmanError as exc:
        _fail(subcommand, exc, json_output=json_output)
        return

    def render(data: dict[str, Any]) -> None:
        if data.get("dry_run"):
            print(f"将删除 shim: {data['planned']['remove_shims']}")
            for entry in data["planned"]["remove_path_entries"]:
                print(f"将移除用户 PATH 条目: {entry}")
            return
        print(f"已删除 shim: {', '.join(data['removed_shims']) or '（无）'}")
        user_path = data.get("user_path") or {}
        if user_path.get("enabled"):
            if user_path.get("changed"):
                print(f"用户 PATH: 已精确移除 {user_path.get('removed')} 个 devman 条目")
            else:
                print("用户 PATH: 无需修改")

    _finish(subcommand, payload, json_output=json_output, render=render)


@fcmd.tool(
    "devman",
    subcommand="env",
    help="打印把活动版本加入当前 shell PATH 的片段",
    param_help={"shell": "pwsh / cmd / bash", "root": "devman 根目录"},
)
def env(shell: ShellName = "pwsh", root: str = "", *, json_output: bool = False) -> None:
    """输出激活片段（不落盘）。"""
    subcommand = "env"
    try:
        ctx = _ops.new_context(root)
        payload = _ops.env_payload(ctx, shell=shell)
    except DevmanError as exc:
        _fail(subcommand, exc, json_output=json_output)
        return

    def render(data: dict[str, Any]) -> None:
        print(data["snippet"])

    _finish(subcommand, payload, json_output=json_output, render=render)


@fcmd.tool(
    "devman",
    subcommand="exec",
    help="在活动版本环境下执行命令（只在子进程内改 PATH）",
    param_help={"command": "要执行的命令，例：fcmd devman exec -- node -v", "root": "devman 根目录"},
)
def exec_command(command: list[str], root: str = "", *, json_output: bool = False) -> None:
    """子进程执行；不修改当前 shell 与注册表。"""
    subcommand = "exec"
    try:
        ctx = _ops.new_context(root)
        pins = _ops.project_versions(ctx)
        dirs = _env.path_dirs(ctx.layout, pins=pins)
        if not dirs:
            raise not_found(
                "没有活动版本，无法构造执行环境",
                hint="先运行 fcmd devman use node@<ver>（或 python@<ver>）",
            )
        if not command:
            raise invalid_argument("缺少要执行的命令", hint="例：fcmd devman exec -- node -v")
        child_env = _env.child_env(ctx.layout, pins=pins)
        # CreateProcess 用父进程 PATH 解析命令，必须显式按活动版本解析
        resolved = shutil.which(command[0], path=child_env.get("PATH"))
        if resolved is None:
            raise not_found(f"找不到命令：{command[0]}", hint="确认命令名，或使用绝对路径")
    except DevmanError as exc:
        _fail(subcommand, exc, json_output=json_output)
        return

    try:
        completed = subprocess.run([resolved, *command[1:]], env=child_env, check=False)
    except FileNotFoundError:
        _fail(
            subcommand,
            runtime_error(f"找不到命令：{command[0]}", hint="确认活动版本包含该可执行文件", code="EXEC_FAILED"),
            json_output=json_output,
        )
        return
    except OSError as exc:
        _fail(subcommand, runtime_error(f"执行失败：{exc}", code="EXEC_FAILED"), json_output=json_output)
        return

    payload = {
        "schema": "devman/exec/1",
        "command": command,
        "exit_code": completed.returncode,
        "dirs": [str(item) for item in dirs],
        "project": pins,
        "warnings": ctx.warnings,
    }
    if completed.returncode != 0:
        _fail(
            subcommand,
            runtime_error(f"命令以退出码 {completed.returncode} 结束", code="EXEC_FAILED"),
            json_output=json_output,
        )
        return
    _finish(subcommand, payload, json_output=json_output)


@fcmd.tool(
    "devman",
    subcommand="doctor",
    help="只读诊断：根目录、活动版本、shim、残留、外部环境",
    param_help={"root": "devman 根目录"},
)
def doctor(root: str = "", *, json_output: bool = False) -> None:
    """诊断；存在 ``fail`` 项时退出 5。"""
    subcommand = "doctor"
    try:
        ctx = _ops.new_context(root)
        payload = _ops.doctor_payload(ctx)
    except DevmanError as exc:
        _fail(subcommand, exc, json_output=json_output)
        return
    _finish(subcommand, payload, json_output=json_output, render=_human_doctor, ok=bool(payload.get("ok")))


def _format_bytes(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    size = float(value)
    index = 0
    while size >= 1024 and index < len(units) - 1:
        size /= 1024
        index += 1
    return f"{int(size)} B" if index == 0 else f"{size:.1f} {units[index]}"


def _human_size(payload: dict[str, Any]) -> None:
    for tool, data in (payload.get("tools") or {}).items():
        print(f"{tool}: {_format_bytes(data['bytes'])}（{len(data['versions'])} 个版本）")
        for item in data["versions"]:
            print(f"  {item['version']:<14} {_format_bytes(item['bytes']):>10}  {item['status']}")
    cache = payload.get("cache") or {}
    shims = payload.get("shims") or {}
    print(f"缓存: {_format_bytes(cache.get('bytes', 0))}  {cache.get('dir')}")
    print(f"shim: {_format_bytes(shims.get('bytes', 0))}  {shims.get('dir')}")
    print(f"合计: {_format_bytes(payload.get('total_bytes', 0))}")


def _human_verify(payload: dict[str, Any]) -> None:
    if payload.get("ok"):
        print("完整性检查通过")
        return
    issues = payload.get("issues") or []
    print(f"发现 {len(issues)} 个问题：")
    for issue in issues:
        where = f"{issue['tool']}\\{issue['name']}" if issue.get("tool") else (issue.get("name") or "-")
        print(f"  [{issue['kind']}] {where}: {issue['detail']}")
        if issue.get("hint"):
            print(f"    {issue['hint']}")


def _human_repair(payload: dict[str, Any]) -> None:
    actions = payload.get("actions") or []
    if not actions:
        print("没有需要修复的问题")
        return
    head = "将执行" if payload.get("dry_run") else "已执行"
    print(f"{head} {len(actions)} 项修复：")
    for action in actions:
        print(f"  {action['kind']}: {action['detail']}  {action['target']}")
    remaining = payload.get("remaining") or []
    if payload.get("dry_run"):
        return
    for issue in remaining[:5]:
        print(f"  待手工处理 [{issue['kind']}] {issue.get('tool') or ''} {issue.get('name') or ''}: {issue['detail']}")


def _human_export(payload: dict[str, Any]) -> None:
    if payload.get("written"):
        print(f"已写入锁文件：{payload['output']}")
        return
    if payload.get("output"):
        print(f"将写入锁文件：{payload['output']}")
        return
    print(json.dumps(payload.get("lock") or {}, ensure_ascii=False, indent=2))


def _human_restore(payload: dict[str, Any]) -> None:
    plan = payload.get("planned") or {}
    if payload.get("dry_run"):
        print("恢复计划：")
    elif not payload.get("changed"):
        print("已与锁文件一致，无需操作")
        return
    else:
        print("恢复完成")
    for item in plan.get("installs") or []:
        print(f"  安装 {item['spec']}")
    for item in plan.get("use") or []:
        print(f"  切换 {item['spec']}")
    for item in plan.get("skipped") or []:
        print(f"  跳过 {item['tool']}@{item['version']}（{item['reason']}）")


@fcmd.tool(
    "devman",
    subcommand="size",
    help="统计各版本目录、下载缓存与 shim 的磁盘占用",
    param_help={"tool": "只统计 node 或 python", "root": "devman 根目录"},
)
def size(tool: str = "", root: str = "", *, json_output: bool = False) -> None:
    """磁盘占用统计（只读）。"""
    subcommand = "size"
    try:
        ctx = _ops.new_context(root)
        payload = _ops.size_payload(ctx, tool=tool or None)
    except DevmanError as exc:
        _fail(subcommand, exc, json_output=json_output)
        return
    _finish(subcommand, payload, json_output=json_output, render=_human_size)


@fcmd.tool(
    "devman",
    subcommand="verify",
    help="校验安装完整性（标记 / 可执行文件 / 活动版本 / shim / 残留），有问题退出 5",
    param_help={"tool": "只检查 node 或 python", "root": "devman 根目录"},
)
def verify(tool: str = "", root: str = "", *, json_output: bool = False) -> None:
    """完整性检查（只读）；有问题时退出 5。"""
    subcommand = "verify"
    try:
        ctx = _ops.new_context(root)
        payload = _ops.verify_payload(ctx, tool=tool or None)
    except DevmanError as exc:
        _fail(subcommand, exc, json_output=json_output)
        return
    _finish(subcommand, payload, json_output=json_output, render=_human_verify, ok=bool(payload.get("ok")))


@fcmd.tool(
    "devman",
    subcommand="repair",
    help="修复可自动处理的问题：重建 shim、清安装残留、清空失效活动版本",
    param_help={
        "tool": "只修复 node 或 python",
        "yes": "确认执行修复",
        "purge_incomplete": "同时删除半完成版本目录（可能含用户数据）",
        "dry_run": "只输出计划，不改文件",
        "root": "devman 根目录",
    },
)
def repair(  # noqa: PLR0913 - CLI 参数与安全语义一一对应
    tool: str = "",
    yes: bool = False,
    purge_incomplete: bool = False,
    dry_run: bool = False,
    root: str = "",
    *,
    json_output: bool = False,
) -> None:
    """自动修复；需要 ``--yes``（``--dry-run`` 只出计划）。"""
    subcommand = "repair"
    try:
        ctx = _ops.new_context(root)
        payload = _ops.repair_payload(
            ctx,
            tool=tool or None,
            dry_run=dry_run,
            yes=yes,
            purge_incomplete=purge_incomplete,
        )
    except DevmanError as exc:
        _fail(subcommand, exc, json_output=json_output)
        return
    _finish(subcommand, payload, json_output=json_output, render=_human_repair, ok=bool(payload.get("ok")))


@fcmd.tool(
    "devman",
    subcommand="export",
    help="导出锁文件（已安装版本 + 活动版本 + 校验信息），便于新机复现",
    param_help={
        "output": "写入文件；省略时打印到标准输出",
        "force": "覆盖已存在的文件",
        "dry_run": "只检查不写文件",
        "root": "devman 根目录",
    },
)
def export_lock(output: str = "", force: bool = False, dry_run: bool = False, root: str = "", *, json_output: bool = False) -> None:
    """导出锁文件（只读；``--output`` 时才写文件）。"""
    subcommand = "export"
    try:
        ctx = _ops.new_context(root)
        payload = _ops.export_payload(ctx, output=output or None, force=force, dry_run=dry_run)
    except DevmanError as exc:
        _fail(subcommand, exc, json_output=json_output)
        return
    _finish(subcommand, payload, json_output=json_output, render=_human_export)


@fcmd.tool(
    "devman",
    subcommand="restore",
    help="按锁文件补齐缺失版本并切换活动版本（新机复现），需要 --yes",
    param_help={
        "lock": "锁文件路径（fcmd devman export --output 生成）",
        "yes": "确认安装 / 切换版本",
        "dry_run": "只输出计划",
        "no_path": "不写用户 PATH",
        "allow_shadow": "允许遮蔽已有的 node / python",
        "root": "devman 根目录",
    },
)
def restore(  # noqa: PLR0913, PLR0917 - CLI 参数与安装语义一一对应
    lock: str,
    yes: bool = False,
    dry_run: bool = False,
    no_path: bool = False,
    allow_shadow: bool = False,
    root: str = "",
    *,
    json_output: bool = False,
) -> None:
    """按锁文件恢复环境；``--dry-run`` 只出计划。"""
    subcommand = "restore"
    try:
        ctx = _ops.new_context(root)
        payload = _ops.restore_payload(
            ctx,
            lock,
            yes=yes,
            dry_run=dry_run,
            write_path=not no_path,
            allow_shadow=allow_shadow,
        )
    except DevmanError as exc:
        _fail(subcommand, exc, json_output=json_output)
        return
    _finish(subcommand, payload, json_output=json_output, render=_human_restore)


@fcmd.main("devman")
def main() -> None:
    """管理 Windows 上的 node / python 多版本环境（安装、切换、卸载、诊断）。"""
