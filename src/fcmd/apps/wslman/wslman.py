"""wslman - Windows 上的 WSL 发行版管理（CLI）。

只读子命令：``list`` / ``status`` / ``info`` / ``config`` / ``doctor``。
所有外部命令经可注入执行器执行；单元测试不跑真实 ``wsl.exe``。

示例
----
    fcmd wslman list
    fcmd wslman list --json --disk
    fcmd wslman status
    fcmd wslman info Ubuntu
    fcmd wslman config
    fcmd wslman doctor
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from fcmd.apps.wslman._source import DownloadResult

import fcmd
from fcmd.apps.wslman import _images
from fcmd.apps.wslman._config import WslmanConfig, load_config
from fcmd.apps.wslman._decode import (
    DistroRow,
    OnlineRow,
    decode_console,
    is_no_distribution_message,
    is_not_enabled_message,
    parse_distro_list,
    parse_online_list,
    parse_status,
)
from fcmd.apps.wslman._disk import distro_disk_bytes, format_bytes, recommended_backup_dir, volume_info
from fcmd.apps.wslman._distro import (
    Distro,
    DistroView,
    distro_data,
    format_info,
    format_list,
    merge,
    state_label,
    to_data,
    with_disk,
)
from fcmd.apps.wslman._errors import (
    WslmanError,
    interrupted,
    invalid_argument,
    not_found,
    runtime_error,
    usage_error,
)
from fcmd.apps.wslman._registry import RegistrySnapshot, WindowsRegistryReader, unavailable_snapshot
from fcmd.apps.wslman._runner import Capabilities, CommandResult, Runner, SubprocessRunner, is_windows, probe
from fcmd.apps.wslman._terminal import (
    Launcher,
    LaunchError,
    SubprocessLauncher,
    TerminalCommand,
    build_command,
    uses_windows_path,
)
from fcmd.apps.wslman._transfer import (
    ProgressReporter,
    SpacePreflight,
    TransferLock,
    archive_info,
    atomic_replace,
    backup_files,
    gzip_to,
    part_path,
    preflight_space,
    sha256_file,
    volume_problem,
)
from fcmd.apps.wslman._wslconfig import default_path as wslconfig_default_path
from fcmd.apps.wslman._wslconfig import load_wslconfig, summary_lines
from fcmd.core.result import emit_result, record_error, report_error

__all__ = [
    "available",
    "backup_remove",
    "backup_verify",
    "backups",
    "compress_file",
    "config",
    "copy_distro",
    "doctor",
    "download",
    "export_distro",
    "import_distro",
    "info",
    "list_distros",
    "main",
    "open_distro",
    "set_default",
    "shutdown",
    "status",
    "terminate",
    "unregister",
]

__version__ = "0.1.0"

_DESCRIPTION = (
    "Windows 上的 WSL 发行版管理：读取 Lxss 注册表与 .wslconfig，列出/查看发行版并诊断环境；"
    "只读命令不会启动发行版，也不写注册表与配置文件"
)

_NOT_ENABLED_HINT = (
    "以管理员身份运行 wsl --install，或启用「适用于 Linux 的 Windows 子系统」与「虚拟机平台」后重启；"
    "随后运行 fcmd wslman doctor 复核"
)


@dataclass(frozen=True, slots=True)
class Environment:
    """一次命令运行内的环境快照；执行器与终端启动器可注入。"""

    runner: Runner
    capabilities: Capabilities
    registry: RegistrySnapshot
    config: WslmanConfig
    platform_ok: bool
    launcher: Launcher = field(default_factory=SubprocessLauncher)
    lock_dir: Path | None = None


@dataclass(frozen=True, slots=True)
class _Check:
    name: str
    status: str
    detail: str
    hint: str | None = None

    def to_data(self) -> dict[str, object]:
        return {"name": self.name, "status": self.status, "detail": self.detail, "hint": self.hint}


def _build_environment(config_path: Path | None = None, *, runner: Runner | None = None) -> Environment:
    active = runner or SubprocessRunner()
    capabilities = probe(active)
    registry = WindowsRegistryReader().snapshot() if is_windows() else unavailable_snapshot("非 Windows 平台")
    return Environment(
        runner=active,
        capabilities=capabilities,
        registry=registry,
        config=load_config(config_path),
        platform_ok=is_windows(),
    )


def _require_wsl(env: Environment) -> str:
    if not env.platform_ok:
        raise not_found("wslman 仅支持 Windows", hint="请在 Windows 上运行；其他平台请使用 WSL 内部工具")
    if env.capabilities.wsl_path is None:
        raise not_found(
            "未找到 wsl.exe",
            hint="以管理员 PowerShell 执行 wsl --install，或确认 C:\\Windows\\System32 在 PATH 中",
        )
    return env.capabilities.wsl_path


def _run_wsl(env: Environment, args: list[str], *, timeout: float = 30.0) -> CommandResult:
    path = _require_wsl(env)
    argv = [path, *args]
    try:
        return env.runner.run(argv, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise runtime_error(
            f"执行 wsl {' '.join(args)} 失败：{exc}",
            hint="运行 fcmd wslman doctor 检查环境；若命令长时间无响应，可在任务管理器中结束 wsl.exe",
        ) from exc


def _first_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return "无输出"


def _list_rows(env: Environment) -> tuple[tuple[DistroRow, ...], tuple[str, ...]]:
    result = _run_wsl(env, ["--list", "--verbose"])
    text = decode_console(result.stdout or result.stderr)
    if result.returncode != 0:
        if is_no_distribution_message(text):
            return (), ("WSL 已启用，但尚未安装任何发行版",)
        if is_not_enabled_message(text):
            raise runtime_error("WSL 未启用或系统组件缺失", hint=_NOT_ENABLED_HINT)
        raise runtime_error(
            f"wsl --list --verbose 失败（退出码 {result.returncode}）：{_first_line(text)}",
            hint="运行 fcmd wslman doctor 获取诊断",
        )
    return parse_distro_list(text), ()


def _view(env: Environment) -> DistroView:
    rows, notes = _list_rows(env)
    view = merge(rows, env.registry)
    if notes:
        view = replace(view, warnings=(*notes, *view.warnings))
    return view


def _with_target_disk(view: DistroView, target: Distro) -> DistroView:
    size, estimated = distro_disk_bytes(target.base_path, target.version)
    return with_disk(view, {target.name: (size, estimated)})


def _fail(exc: WslmanError, subcommand: str, json_output: bool) -> None:
    report_error(
        "wslman",
        subcommand,
        code=exc.code,
        message=str(exc),
        json_output=json_output,
        hint=exc.hint,
        field=exc.field,
    )


def _registry_data(snapshot: RegistrySnapshot) -> dict[str, object]:
    return {
        "available": snapshot.available,
        "error": snapshot.error,
        "default_distribution": snapshot.default_distribution,
        "default_version": snapshot.default_version,
        "count": len(snapshot.distros),
    }


@fcmd.tool(
    "wslman",
    subcommand="list",
    help="列出已安装的 WSL 发行版（只读，不启动发行版）",
    param_help={
        "json_output": "输出机读结果信封",
        "disk": "一并估算磁盘占用（WSL1 需要遍历目录，可能较慢）",
    },
)
def list_distros(json_output: bool = False, disk: bool = False) -> None:
    """合并 ``wsl --list --verbose`` 与 Lxss 注册表，列出全部发行版。

    Parameters
    ----------
    json_output:
        输出 ``fcmd-result/1`` 机读信封
    disk:
        估算每个发行版的磁盘占用；WSL2 读取 ``ext4.vhdx`` 大小，WSL1 遍历 ``rootfs``
    """
    try:
        env = _build_environment()
        view = _view(env)
        if disk:
            sizes = {distro.name: distro_disk_bytes(distro.base_path, distro.version) for distro in view.distros}
            view = with_disk(view, sizes)
    except WslmanError as exc:
        _fail(exc, "list", json_output)
        return

    if json_output:
        payload = to_data(view)
        payload["config_warnings"] = list(env.config.warnings)
        emit_result("wslman", "list", payload)
        return

    print(format_list(view, disk=disk))
    for warning in env.config.warnings:
        print(f"[配置警告] {warning}")


@fcmd.tool(
    "wslman",
    subcommand="status",
    help="显示 WSL 状态、默认发行版与 .wslconfig 摘要（只读）",
    param_help={"json_output": "输出机读结果信封"},
)
def status(json_output: bool = False) -> None:
    """展示 ``wsl --status`` 原文、注册表默认值与 ``.wslconfig`` 摘要。

    本地化的 ``--status`` 文本只原样展示，默认发行版与版本以注册表和 ``-l -v`` 为准。
    """
    try:
        env = _build_environment()
        path = _require_wsl(env)
        result = _run_wsl(env, ["--status"])
        raw = decode_console(result.stdout or result.stderr)
        if result.returncode != 0:
            if is_not_enabled_message(raw):
                raise runtime_error("WSL 未启用或系统组件缺失", hint=_NOT_ENABLED_HINT)
            if not raw.strip():
                raise runtime_error(
                    f"wsl --status 失败（退出码 {result.returncode}）：无输出", hint="运行 fcmd wslman doctor"
                )
        pairs = parse_status(raw)
        view = _view(env)
    except WslmanError as exc:
        _fail(exc, "status", json_output)
        return

    wslconfig = load_wslconfig(wslconfig_default_path())
    running = [distro.name for distro in view.distros if distro.state == "running"]
    data: dict[str, object] = {
        "schema": "wslman/status/1",
        "wsl": {
            "path": path,
            "version": env.capabilities.version,
            "status_returncode": result.returncode,
            "status_pairs": [{"label": label, "value": value} for label, value in pairs],
            "status_raw": raw,
        },
        "registry": _registry_data(env.registry),
        "distros": {"count": len(view.distros), "default": view.default, "running": running},
        "wslconfig": {
            "path": str(wslconfig.path),
            "exists": wslconfig.exists,
            "error": wslconfig.error,
            "sections": {name: dict(values) for name, values in wslconfig.sections.items()},
            "warnings": list(wslconfig.warnings),
        },
        "warnings": [*view.warnings, *env.config.warnings],
    }
    if json_output:
        emit_result("wslman", "status", data)
        return

    print(f"WSL 路径: {path}")
    print(f"WSL 版本: {env.capabilities.version or '未知'}")
    print(f"默认发行版: {view.default or '未设置'}")
    print(f"发行版数量: {len(view.distros)}（运行中 {len(running)}：{', '.join(running) if running else '无'}）")
    if env.registry.available:
        print(
            f"注册表: 可读（登记 {len(env.registry.distros)} 个，DefaultVersion={env.registry.default_version or '未知'}）"
        )
    else:
        print(f"注册表: 不可读（{env.registry.error or '未知原因'}）")
    print(f".wslconfig: {wslconfig.path}")
    for line in summary_lines(wslconfig):
        print(f"  {line}")
    if result.returncode != 0:
        print(f"[警告] wsl --status 退出码 {result.returncode}")
    print("wsl --status 原始输出:")
    for line in raw.splitlines() or ["(无输出)"]:
        print(f"  {line}")
    for warning in (*view.warnings, *env.config.warnings):
        print(f"[警告] {warning}")


@fcmd.tool(
    "wslman",
    subcommand="info",
    help="查看单个发行版的注册表信息与占用（默认不启动发行版）",
    param_help={
        "distro": "发行版名称，大小写不敏感",
        "json_output": "输出机读结果信封",
        "include_config": "读取发行版内 /etc/wsl.conf；会启动该发行版",
    },
)
def info(distro: str, json_output: bool = False, include_config: bool = False) -> None:
    """展示发行版的 BasePath、版本、默认用户与磁盘占用。

    Parameters
    ----------
    distro:
        发行版名称；按不区分大小写匹配
    json_output:
        输出 ``fcmd-result/1`` 机读信封
    include_config:
        额外读取 ``/etc/wsl.conf``；该操作会启动发行版，默认关闭
    """
    try:
        env = _build_environment()
        view = _view(env)
        target = view.find(distro)
        if target is None:
            raise not_found(f"未找到发行版 {distro}", hint="运行 fcmd wslman list 查看已安装发行版")
        view = _with_target_disk(view, target)
        updated = view.find(distro)
        if updated is None:
            raise not_found(f"未找到发行版 {distro}", hint="运行 fcmd wslman list 查看已安装发行版")
        target = updated
        wslconf_text: str | None = None
        wslconf_error: str | None = None
        if include_config:
            wslconf_text, wslconf_error = _read_wsl_conf(env, target)
    except WslmanError as exc:
        _fail(exc, "info", json_output)
        return

    data = {
        "schema": "wslman/info/1",
        "distro": distro_data(target),
        "wslconf": {
            "requested": include_config,
            "text": wslconf_text,
            "error": wslconf_error,
        },
        "warnings": list(view.warnings),
    }
    if json_output:
        emit_result("wslman", "info", data)
        return

    lines: list[str] | None = None
    if include_config:
        if wslconf_error:
            lines = [f"[错误] {wslconf_error}"]
        else:
            lines = (wslconf_text or "").splitlines() or ["(空文件)"]
    print(format_info(target, wslconfig_lines=lines))
    for warning in view.warnings:
        print(f"[警告] {warning}")


def _read_wsl_conf(env: Environment, distro: Distro) -> tuple[str | None, str | None]:
    result = _run_wsl(env, ["-d", distro.name, "-u", "root", "--", "cat", "/etc/wsl.conf"], timeout=60.0)
    if result.returncode != 0:
        detail = _first_line(decode_console(result.stderr or result.stdout))
        return None, f"读取 /etc/wsl.conf 失败（退出码 {result.returncode}）：{detail}"
    return decode_console(result.stdout), None


# ---------------------------------------------------------------------- #
# 变更档：open / terminate / set-default
# ---------------------------------------------------------------------- #

_TRANSITIONAL_STATES = frozenset({"installing", "uninstalling", "converting"})


def _validated_name(distro: str) -> str:
    name = distro.strip()
    if not name:
        raise invalid_argument(
            "发行版名称不能为空",
            hint="运行 fcmd wslman list 查看已安装发行版",
            field="distro",
        )
    return name


def _try_view(env: Environment) -> DistroView | None:
    """只读列表失败时返回 ``None``，让变更命令继续执行而不是被诊断信息挡住。"""
    try:
        return _view(env)
    except WslmanError:
        return None


def _display_argv(argv: Sequence[str]) -> str:
    return subprocess.list2cmdline(list(argv))


def _terminal_label(terminal: str) -> str:
    return "Windows Terminal (wt)" if terminal == "wt" else "新控制台 (conhost)"


def _emit_action(
    subcommand: str,
    data: Mapping[str, object],
    *,
    json_output: bool,
    lines: Sequence[str],
    warnings: Sequence[str] = (),
) -> None:
    if json_output:
        emit_result("wslman", subcommand, data)
        return
    for line in lines:
        print(line)
    for warning in warnings:
        print(f"[警告] {warning}")


@fcmd.tool(
    "wslman",
    subcommand="open",
    help="在新终端窗口中打开发行版（会启动该发行版；--preview 只打印命令）",
    param_help={
        "distro": "发行版名称，大小写不敏感",
        "json_output": "输出机读结果信封",
        "preview": "只打印将执行的命令，不启动任何进程",
        "workdir": "新窗口的工作目录，必须是带盘符的 Windows 路径（如 C:\\Users\\me\\proj）",
        "user": "以指定 Linux 用户登录（等价 wsl -u <用户>）",
    },
)
def open_distro(
    distro: str,
    json_output: bool = False,
    preview: bool = False,
    workdir: str | None = None,
    user: str | None = None,
) -> None:
    """优先用 Windows Terminal 打开，找不到或启动失败时回退新控制台。

    Parameters
    ----------
    distro:
        发行版名称；按不区分大小写匹配
    json_output:
        输出 ``fcmd-result/1`` 机读信封
    preview:
        只打印完整 argv 与预检结论，不启动进程
    workdir:
        新窗口的工作目录；必须是带盘符的 Windows 路径（对应 wsl 的 ``--cd``）
    user:
        以指定 Linux 用户登录；不存在时报错由 ``wsl.exe`` 给出
    """
    warnings: list[str] = []
    try:
        env = _build_environment()
        wsl_path = _require_wsl(env)
        name = _validated_name(distro)
        if workdir is not None and not uses_windows_path(workdir):
            raise invalid_argument(
                f"--workdir 必须是带盘符的 Windows 路径：{workdir}",
                hint=r"例如 --workdir C:\Users\me\proj；WSL 内部路径请在窗口里的 shell 中切换",
                field="workdir",
            )

        def make(program: str, target: str) -> TerminalCommand:
            return build_command(
                wsl_path=wsl_path,
                distro=target,
                cwd=workdir,
                user=user,
                program=program,
                extra_args=env.config.terminal_extra_args,
            )

        command = make(env.config.terminal_program, name)
        if preview:
            _emit_action(
                "open",
                {
                    "schema": "wslman/open/1",
                    "distro": name,
                    "terminal": command.terminal,
                    "commands": [list(command.argv)],
                    "preview": True,
                    "pid": None,
                    "warnings": [*warnings, *env.config.warnings],
                },
                json_output=json_output,
                lines=[
                    f"发行版: {name}",
                    f"终端: {_terminal_label(command.terminal)}",
                    f"命令: {_display_argv(command.argv)}",
                    "[预览] 未启动任何进程；去掉 --preview 才会真正打开",
                ],
                warnings=env.config.warnings,
            )
            return

        view = _try_view(env)
        if view is not None:
            target = view.find(name)
            if target is None:
                raise not_found(f"未找到发行版 {name}", hint="运行 fcmd wslman list 查看已安装发行版")
            if target.state in _TRANSITIONAL_STATES:
                raise invalid_argument(
                    f"{target.name} 正在{state_label(target)}，暂不能打开",
                    hint="稍后重试；运行 fcmd wslman list 查看当前状态",
                )
            if target.name != name:
                name = target.name
                command = make(env.config.terminal_program, name)

        try:
            pid = env.launcher.launch(command)
        except LaunchError as exc:
            if command.terminal != "wt":
                raise
            warnings.append(f"Windows Terminal 启动失败（{exc}），已回退新控制台")
            command = make("conhost", name)
            pid = env.launcher.launch(command)
    except WslmanError as exc:
        _fail(exc, "open", json_output)
        return
    except LaunchError as exc:
        _fail(runtime_error(f"启动终端失败：{exc}", hint=exc.hint), "open", json_output)
        return

    _emit_action(
        "open",
        {
            "schema": "wslman/open/1",
            "distro": name,
            "terminal": command.terminal,
            "commands": [list(command.argv)],
            "preview": False,
            "pid": pid,
            "warnings": [*warnings, *env.config.warnings],
        },
        json_output=json_output,
        lines=[
            f"发行版: {name}",
            f"终端: {_terminal_label(command.terminal)}",
            f"命令: {_display_argv(command.argv)}",
            f"已启动（PID {pid}）",
        ],
        warnings=[*warnings, *env.config.warnings],
    )


@fcmd.tool(
    "wslman",
    subcommand="terminate",
    help="终止指定发行版（会杀掉其内部进程；不需要 --yes，GUI 侧保留确认）",
    param_help={
        "distro": "发行版名称，大小写不敏感",
        "json_output": "输出机读结果信封",
        "preview": "只打印将执行的命令，不终止任何发行版",
    },
)
def terminate(distro: str, json_output: bool = False, preview: bool = False) -> None:
    """执行 ``wsl --terminate``；未运行的发行版按无操作处理。

    Parameters
    ----------
    distro:
        发行版名称；按不区分大小写匹配
    json_output:
        输出 ``fcmd-result/1`` 机读信封
    preview:
        只打印完整 argv，不读取运行状态、不终止进程
    """
    warnings: list[str] = []
    try:
        env = _build_environment()
        wsl_path = _require_wsl(env)
        name = _validated_name(distro)
        if preview:
            argv = [wsl_path, "--terminate", name]
            _emit_action(
                "terminate",
                {
                    "schema": "wslman/terminate/1",
                    "distro": name,
                    "commands": [argv],
                    "preview": True,
                    "executed": False,
                    "state_before": None,
                    "state_after": None,
                    "warnings": [*warnings, *env.config.warnings],
                },
                json_output=json_output,
                lines=[
                    f"发行版: {name}",
                    f"命令: {_display_argv(argv)}",
                    "[预览] 未终止任何进程，也未读取运行状态；去掉 --preview 才会真正执行",
                ],
                warnings=env.config.warnings,
            )
            return

        view = _view(env)
        target = view.find(name)
        if target is None:
            raise not_found(f"未找到发行版 {name}", hint="运行 fcmd wslman list 查看已安装发行版")
        name = target.name
        state_before = target.state
        if state_before != "running":
            _emit_action(
                "terminate",
                {
                    "schema": "wslman/terminate/1",
                    "distro": name,
                    "commands": [],
                    "preview": False,
                    "executed": False,
                    "state_before": state_before,
                    "state_after": state_before,
                    "warnings": [*warnings, *env.config.warnings],
                },
                json_output=json_output,
                lines=[f"{name} 当前状态为{state_label(target)}，无需终止"],
                warnings=env.config.warnings,
            )
            return

        argv = [wsl_path, "--terminate", name]
        result = _run_wsl(env, ["--terminate", name], timeout=120.0)
        if result.returncode != 0:
            detail = _first_line(decode_console(result.stderr or result.stdout))
            raise runtime_error(
                f"wsl --terminate {name} 失败（退出码 {result.returncode}）：{detail}",
                hint="运行 fcmd wslman list 复核状态；必要时以管理员身份重试",
            )

        state_after: str | None = None
        after = _try_view(env)
        if after is not None:
            updated = after.find(name)
            state_after = updated.state if updated is not None else None
            if state_after == "running":
                warnings.append(f"{name} 仍显示为运行中，可能还有进程正在退出")
    except WslmanError as exc:
        _fail(exc, "terminate", json_output)
        return

    state_text = state_after or "未知"
    _emit_action(
        "terminate",
        {
            "schema": "wslman/terminate/1",
            "distro": name,
            "commands": [argv],
            "preview": False,
            "executed": True,
            "state_before": state_before,
            "state_after": state_after,
            "warnings": [*warnings, *env.config.warnings],
        },
        json_output=json_output,
        lines=[f"已终止 {name}（当前状态：{state_text}）"],
        warnings=[*warnings, *env.config.warnings],
    )


@fcmd.tool(
    "wslman",
    subcommand="set-default",
    help="设置默认发行版（执行后用 wsl -l -v 复核 * 标记）",
    param_help={
        "distro": "发行版名称，大小写不敏感",
        "json_output": "输出机读结果信封",
        "preview": "只打印将执行的命令，不修改默认发行版",
    },
)
def set_default(distro: str, json_output: bool = False, preview: bool = False) -> None:
    """执行 ``wsl --set-default`` 并复核默认 ``*`` 标记。

    Parameters
    ----------
    distro:
        发行版名称；按不区分大小写匹配
    json_output:
        输出 ``fcmd-result/1`` 机读信封
    preview:
        只打印完整 argv，不读取当前默认值、不修改任何设置
    """
    warnings: list[str] = []
    try:
        env = _build_environment()
        wsl_path = _require_wsl(env)
        name = _validated_name(distro)
        if preview:
            argv = [wsl_path, "--set-default", name]
            _emit_action(
                "set-default",
                {
                    "schema": "wslman/set-default/1",
                    "distro": name,
                    "commands": [argv],
                    "preview": True,
                    "executed": False,
                    "previous": None,
                    "default": None,
                    "verified": False,
                    "warnings": [*warnings, *env.config.warnings],
                },
                json_output=json_output,
                lines=[
                    f"发行版: {name}",
                    f"命令: {_display_argv(argv)}",
                    "[预览] 未修改默认发行版，也未读取当前默认值；去掉 --preview 才会真正执行",
                ],
                warnings=env.config.warnings,
            )
            return

        view = _view(env)
        target = view.find(name)
        if target is None:
            raise not_found(f"未找到发行版 {name}", hint="运行 fcmd wslman list 查看已安装发行版")
        name = target.name
        previous = view.default
        if previous is not None and previous.casefold() == name.casefold():
            _emit_action(
                "set-default",
                {
                    "schema": "wslman/set-default/1",
                    "distro": name,
                    "commands": [],
                    "preview": False,
                    "executed": False,
                    "previous": previous,
                    "default": previous,
                    "verified": True,
                    "warnings": [*warnings, *env.config.warnings],
                },
                json_output=json_output,
                lines=[f"{name} 已是默认发行版，无需修改"],
                warnings=env.config.warnings,
            )
            return

        argv = [wsl_path, "--set-default", name]
        result = _run_wsl(env, ["--set-default", name], timeout=120.0)
        if result.returncode != 0:
            detail = _first_line(decode_console(result.stderr or result.stdout))
            raise runtime_error(
                f"wsl --set-default {name} 失败（退出码 {result.returncode}）：{detail}",
                hint="运行 fcmd wslman list 复核；必要时以管理员身份重试",
            )

        after = _try_view(env)
        new_default = after.default if after is not None else None
        if new_default is None or new_default.casefold() != name.casefold():
            raise runtime_error(
                f"已执行 wsl --set-default {name}，但复核未在 wsl -l -v 中看到默认标记",
                hint="运行 fcmd wslman list 查看当前默认发行版；若仍不正确，重试或检查 WSL 服务",
            )
    except WslmanError as exc:
        _fail(exc, "set-default", json_output)
        return

    _emit_action(
        "set-default",
        {
            "schema": "wslman/set-default/1",
            "distro": name,
            "commands": [argv],
            "preview": False,
            "executed": True,
            "previous": previous,
            "default": new_default,
            "verified": True,
            "warnings": [*warnings, *env.config.warnings],
        },
        json_output=json_output,
        lines=[f"已将默认发行版从 {previous or '未设置'} 改为 {name}（复核通过）"],
        warnings=[*warnings, *env.config.warnings],
    )


# ---------------------------------------------------------------------- #
# 传输档：export / import / copy / compress / backups
# ---------------------------------------------------------------------- #

_TRANSFER_TIMEOUT_S = 24 * 60 * 60.0
_RESERVE_HINT = "更换目标路径或清理空间后重试"


def _require_target(env: Environment, name: str) -> Distro:
    view = _view(env)
    target = view.find(name)
    if target is None:
        raise not_found(f"未找到发行版 {name}", hint="运行 fcmd wslman list 查看已安装发行版")
    return target


def _require_dir(directory: Path | None, *, example: str) -> Path:
    if directory is None:
        raise invalid_argument("必须用 --dir 指定目标目录", hint=f"例如：{example}", field="dir")
    return directory


def _dir_problem(path: Path) -> str | None:
    if path.exists():
        if not path.is_dir():
            return f"目标路径不是目录：{path}"
        try:
            if any(path.iterdir()):
                return f"目标目录必须为空：{path}"
        except OSError as exc:
            return f"无法读取目标目录 {path}：{exc}"
    return volume_problem(path)


def _guard_running_export(
    target: Distro,
    *,
    terminate_first: bool,
    allow_running: bool,
    command: str,
) -> None:
    if terminate_first and allow_running:
        raise invalid_argument(
            "--terminate-first 与 --allow-running 只能二选一",
            hint=f"二选一后重试：{command}",
        )
    if target.state != "running" or terminate_first or allow_running:
        return
    raise invalid_argument(
        f"{target.name} 正在运行，导出不会自动停机，必须显式选择",
        hint=(f"先停机再导出：{command} --terminate-first\n接受运行中导出的一致性风险：{command} --allow-running"),
    )


def _guard_running_strict(target: Distro, *, terminate_first: bool, command: str) -> None:
    if target.state == "running" and not terminate_first:
        raise invalid_argument(
            f"{target.name} 正在运行，必须先停机",
            hint=f"执行：{command} --terminate-first",
        )


def _guard_replace(  # noqa: PLR0913
    env: Environment,
    *,
    existing: Distro,
    yes: bool,
    backup: Path | None,
    terminate_first: bool,
    command: str,
) -> None:
    if not yes:
        suffix = " --backup <备份文件>" if env.config.require_backup_on_replace else ""
        raise usage_error(
            f"替换 {existing.name} 需要 --replace --yes",
            hint=f"确认后执行：{command} --replace --yes{suffix}",
        )
    if env.config.require_backup_on_replace and backup is None:
        raise usage_error(
            f"替换 {existing.name} 前必须先备份",
            hint=(
                f"执行：{command} --replace --yes --backup <备份文件>；"
                "或在 ~/.fcmd/wslman.json 把 safety.require_backup_on_replace 设为 false（不推荐）"
            ),
        )
    if backup is not None:
        if backup.exists():
            raise invalid_argument(
                f"备份文件已存在：{backup}",
                hint="换一个备份路径，避免覆盖既有备份",
                field="backup",
            )
        problem = volume_problem(backup)
        if problem:
            raise invalid_argument(problem, hint="选择本地 NTFS 卷上的备份路径", field="backup")
    if existing.state == "running" and not terminate_first:
        raise invalid_argument(
            f"{existing.name} 正在运行，替换前必须先停机",
            hint=f"执行：{command} --terminate-first",
        )


def _run_step(
    env: Environment,
    args: Sequence[str],
    *,
    action: str,
    hint: str | None = None,
    timeout: float = 120.0,
) -> CommandResult:
    result = _run_wsl(env, list(args), timeout=timeout)
    if result.returncode != 0:
        detail = _first_line(decode_console(result.stderr or result.stdout))
        raise runtime_error(f"{action}失败（退出码 {result.returncode}）：{detail}", hint=hint)
    return result


def _abort_parts(parts: Sequence[Path], *, keep: bool) -> str:
    existing = [path for path in parts if path.exists()]
    if not existing:
        return "没有残留文件"
    if keep:
        return "已保留未完成文件：" + "、".join(str(path) for path in existing)
    removed: list[str] = []
    for path in existing:
        try:
            path.unlink()
            removed.append(str(path))
        except OSError:
            continue
    return "已清理未完成文件：" + "、".join(removed) if removed else "未能清理未完成文件"


def _export_target(file: Path, compress: str) -> Path:
    if compress == "gzip" and not file.name.casefold().endswith(".gz"):
        return file.with_name(file.name + ".gz")
    return file


def _export_parts(target_path: Path, compress: str) -> tuple[Path, Path | None]:
    """返回 ``(原始 tar 的 .part, gzip 的 .part 或 None)``，供 CLI 与 GUI 共用。"""
    if compress == "gzip":
        return part_path(target_path.with_name(target_path.name[:-3])), part_path(target_path)
    return part_path(target_path), None


def _export_stream(  # noqa: PLR0913 - 导出核心按需显式传参，CLI 与 GUI 共用
    env: Environment,
    name: str,
    target_path: Path,
    parts: tuple[Path, Path | None],
    *,
    level: int,
    terminate_first: bool,
    estimate: int | None,
) -> tuple[int, str]:
    """执行 ``wsl --export`` 到 ``.part``，按需 gzip 后原子改名；不负责清理。"""
    raw_part, gz_part = parts
    with TransferLock(name, directory=env.lock_dir):
        if terminate_first:
            _run_step(
                env,
                ["--terminate", name],
                action=f"导出前终止 {name}",
                hint="改用 --allow-running 接受运行中导出，或手动终止后重试",
            )
        for stale in (path for path in parts if path is not None):
            with contextlib.suppress(OSError):
                stale.unlink()
        raw_part.parent.mkdir(parents=True, exist_ok=True)
        reporter = ProgressReporter(raw_part, estimate, label="导出")
        reporter.start()
        try:
            result = _run_wsl(env, ["--export", name, str(raw_part)], timeout=_TRANSFER_TIMEOUT_S)
        finally:
            reporter.stop()
        if result.returncode != 0:
            detail = _first_line(decode_console(result.stderr or result.stdout))
            raise runtime_error(
                f"wsl --export {name} 失败（退出码 {result.returncode}）：{detail}",
                hint=f"{_RESERVE_HINT}；残留文件：{raw_part}",
            )
        if not raw_part.is_file():
            raise runtime_error(
                "wsl --export 返回成功但未生成文件",
                hint=f"检查 {raw_part} 所在目录的权限与剩余空间",
            )
        if gz_part is not None:
            gzip_to(raw_part, gz_part, level=level)
            with contextlib.suppress(OSError):
                raw_part.unlink()
            atomic_replace(gz_part, target_path)
        else:
            atomic_replace(raw_part, target_path)
    return target_path.stat().st_size, sha256_file(target_path)


def _slug(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-._" else "_" for ch in name)
    return cleaned or "distro"


def _temp_tar(explicit: Path | None, env: Environment, directory: Path, name: str) -> Path:
    if explicit is not None:
        base = explicit
    elif env.config.temp_dir.strip():
        base = Path(env.config.temp_dir)
    else:
        base = directory.parent
    if base.suffix.casefold() == ".tar":
        return base
    return base / f"wslman-copy-{_slug(name)}-{time.monotonic_ns()}.tar"


def _preflight_for(
    env: Environment,
    path: Path,
    needed: int | None,
    *,
    estimated: bool,
) -> SpacePreflight:
    return preflight_space(
        path,
        needed,
        estimated=estimated,
        margin=env.config.safety_margin,
        min_free_after_mb=env.config.min_free_after_mb,
    )


@fcmd.tool(
    "wslman",
    subcommand="export",
    help="导出发行版为 tar（可选 gzip；运行中必须显式选择停机或接受一致性风险）",
    param_help={
        "distro": "发行版名称，大小写不敏感",
        "file": "导出文件路径（.tar；--compress gzip 时自动补 .gz）",
        "json_output": "输出机读结果信封",
        "preview": "打印完整命令、预检结论与目标路径，不写文件",
        "overwrite": "覆盖已存在的目标文件",
        "terminate_first": "导出前先终止发行版",
        "allow_running": "允许在发行版运行时导出（可能不一致）",
        "compress": "压缩格式：none（默认）或 gzip",
        "level": "gzip 压缩级别 1-9，默认 6",
        "keep_partial": "中断或失败后保留 .part 文件",
    },
)
def export_distro(  # noqa: PLR0913, PLR0917
    distro: str,
    file: Path,
    json_output: bool = False,
    preview: bool = False,
    overwrite: bool = False,
    terminate_first: bool = False,
    allow_running: bool = False,
    compress: Literal["none", "gzip"] = "none",
    level: int = 6,
    keep_partial: bool = False,
) -> None:
    """导出前做磁盘预检，写 ``.part`` 后原子改名；失败默认清理残留。"""
    warnings: list[str] = []
    parts: list[Path] = []
    keep_on_failure = keep_partial
    try:
        env = _build_environment()
        wsl_path = _require_wsl(env)
        name = _validated_name(distro)
        if not 1 <= level <= 9:
            raise invalid_argument(f"--level 必须在 1-9 之间：{level}", field="level")

        target_path = _export_target(file, compress)
        if target_path != file:
            warnings.append(f"已按 gzip 约定改用 .gz 后缀：{target_path}")
        problem = volume_problem(target_path)
        if problem:
            raise invalid_argument(problem, hint="选择本地 NTFS 卷上的路径", field="file")
        if target_path.exists() and target_path.is_dir():
            raise invalid_argument(f"目标路径是目录：{target_path}", field="file")
        if target_path.exists() and not overwrite:
            raise invalid_argument(
                f"目标文件已存在：{target_path}",
                hint="换一个路径，或加 --overwrite 覆盖",
                field="file",
            )

        target = _require_target(env, name)
        name = target.name
        _guard_running_export(
            target,
            terminate_first=terminate_first,
            allow_running=allow_running,
            command=f"fcmd wslman export {name} {target_path}",
        )
        estimate, estimated = distro_disk_bytes(target.base_path, target.version)
        check = _preflight_for(env, target_path, estimate, estimated=estimated)

        parts_pair = _export_parts(target_path, compress)
        raw_part = parts_pair[0]
        parts = [path for path in parts_pair if path is not None]
        keep_on_failure = keep_partial or env.config.keep_partial

        export_args = ["--export", name, str(raw_part)]
        commands: list[list[str]] = []
        if terminate_first:
            commands.append([wsl_path, "--terminate", name])
        commands.append([wsl_path, *export_args])
        base_data: dict[str, object] = {
            "schema": "wslman/export/1",
            "distro": name,
            "file": str(target_path),
            "part": str(raw_part),
            "format": "tar.gz" if compress == "gzip" else "tar",
            "compression_level": level if compress == "gzip" else None,
            "commands": commands,
            "preflight": check.to_data(),
            "estimated_bytes": estimate,
            "estimated": estimated,
            "terminate_first": terminate_first,
            "allow_running": allow_running,
            "warnings": [*warnings, *env.config.warnings],
        }

        if preview:
            _emit_action(
                "export",
                {**base_data, "preview": True, "size_bytes": None, "sha256": None, "artifacts": []},
                json_output=json_output,
                lines=[
                    f"发行版: {name}（{state_label(target)}）",
                    f"目标: {target_path}",
                    f"临时文件: {raw_part}",
                    f"格式: {'tar.gz' if compress == 'gzip' else 'tar'}",
                    f"预检: {check.detail}",
                    "命令:",
                    *[f"  {_display_argv(argv)}" for argv in commands],
                    "[预览] 未写入任何文件，也未获取传输锁",
                ],
                warnings=[*warnings, *env.config.warnings],
            )
            return

        if not check.ok:
            raise runtime_error(f"目标卷空间不足：{check.detail}", hint=_RESERVE_HINT)

        started = time.monotonic()
        size, digest = _export_stream(
            env,
            name,
            target_path,
            parts_pair,
            level=level,
            terminate_first=terminate_first,
            estimate=estimate,
        )
    except KeyboardInterrupt:
        detail = _abort_parts(parts, keep=keep_on_failure)
        _fail(
            interrupted(f"导出被中断；{detail}", hint=f"重新执行会从 .part 重新开始；目标文件 {file} 未生成"),
            "export",
            json_output,
        )
        return
    except WslmanError as exc:
        _abort_parts(parts, keep=keep_on_failure)
        _fail(exc, "export", json_output)
        return
    except OSError as exc:
        detail = _abort_parts(parts, keep=keep_on_failure)
        _fail(runtime_error(f"导出文件操作失败：{exc}", hint=detail), "export", json_output)
        return

    artifacts = [{"kind": "backup", "path": str(target_path), "bytes": size, "sha256": digest}]
    _emit_action(
        "export",
        {
            **base_data,
            "preview": False,
            "size_bytes": size,
            "sha256": digest,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "artifacts": artifacts,
        },
        json_output=json_output,
        lines=[
            f"导出完成: {target_path}",
            f"大小: {format_bytes(size)}",
            f"sha256: {digest}",
        ],
        warnings=[*warnings, *env.config.warnings],
    )


@fcmd.tool(
    "wslman",
    subcommand="import",
    help="从 tar 导入为新发行版（覆盖已有发行版需 --replace --yes，且默认强制先备份）",
    param_help={
        "name": "新发行版名称",
        "file": "要导入的 tar 文件",
        "json_output": "输出机读结果信封",
        "preview": "打印命令与预检结论，不创建目录、不导入",
        "dir": "目标安装目录（必须为空；不存在会在执行时创建）",
        "version": "WSL 版本：1 或 2；默认交给 wsl.exe 决定",
        "replace": "覆盖同名发行版（会先注销旧发行版）",
        "yes": "确认破坏性替换",
        "backup": "--replace 时旧发行版的备份文件路径",
        "terminate_first": "替换前先终止正在运行的旧发行版",
    },
)
def import_distro(  # noqa: PLR0912, PLR0913, PLR0917
    name: str,
    file: Path,
    json_output: bool = False,
    preview: bool = False,
    dir: Path | None = None,
    version: Literal["1", "2"] | None = None,
    replace: bool = False,
    yes: bool = False,
    backup: Path | None = None,
    terminate_first: bool = False,
) -> None:
    """校验目标目录与名称冲突，必要时先备份并注销旧发行版，再执行 ``wsl --import``。"""
    warnings: list[str] = []
    try:
        env = _build_environment()
        wsl_path = _require_wsl(env)
        name = _validated_name(name)
        target_dir = _require_dir(dir, example=r"fcmd wslman import Ubuntu2 D:\b\Ubuntu.tar --dir D:\WSL\Ubuntu2")
        problem = _dir_problem(target_dir)
        if problem:
            raise invalid_argument(problem, hint="目标目录必须为空且位于本地磁盘", field="dir")
        if not file.is_file():
            raise not_found(
                f"导入文件不存在：{file}",
                hint="确认路径；备份文件可先用 fcmd wslman backup-verify 校验",
                field="file",
            )
        if backup is not None and not replace:
            raise invalid_argument("--backup 只用于 --replace 替换前备份", field="backup")

        view = _view(env)
        existing = view.find(name)
        if existing is not None and not replace:
            raise invalid_argument(
                f"发行版 {existing.name} 已存在",
                hint="换一个名称，或加 --replace --yes（配置要求时再加 --backup <文件>）",
                field="name",
            )

        commands: list[list[str]] = []
        backup_check = None
        if existing is not None:
            name = existing.name
            _guard_replace(
                env,
                existing=existing,
                yes=yes,
                backup=backup,
                terminate_first=terminate_first,
                command=f"fcmd wslman import {name} {file} --dir {target_dir}",
            )
            if terminate_first:
                commands.append([wsl_path, "--terminate", existing.name])
            if backup is not None:
                commands.append([wsl_path, "--export", existing.name, str(backup)])
                estimate, estimated = distro_disk_bytes(existing.base_path, existing.version)
                backup_check = _preflight_for(env, backup, estimate, estimated=estimated)
                if not backup_check.ok and not preview:
                    raise runtime_error(f"备份路径空间不足：{backup_check.detail}", hint=_RESERVE_HINT)
            commands.append([wsl_path, "--unregister", existing.name])

        import_args = ["--import", name, str(target_dir), str(file)]
        if version is not None:
            import_args.extend(["--version", version])
        commands.append([wsl_path, *import_args])

        check = _preflight_for(env, target_dir, file.stat().st_size, estimated=False)
        base_data: dict[str, object] = {
            "schema": "wslman/import/1",
            "distro": name,
            "file": str(file),
            "dir": str(target_dir),
            "version": version,
            "replace": existing is not None,
            "backup": str(backup) if backup is not None else None,
            "commands": commands,
            "preflight": check.to_data(),
            "backup_preflight": backup_check.to_data() if backup_check is not None else None,
            "warnings": [*warnings, *env.config.warnings],
        }
        if preview:
            _emit_action(
                "import",
                {**base_data, "preview": True, "executed": False, "verified": None},
                json_output=json_output,
                lines=[
                    f"名称: {name}",
                    f"来源: {file}",
                    f"安装目录: {target_dir}",
                    f"预检: {check.detail}",
                    "命令:",
                    *[f"  {_display_argv(argv)}" for argv in commands],
                    "[预览] 未创建目录、未导入、未注销任何发行版",
                ],
                warnings=[*warnings, *env.config.warnings],
            )
            return
        if not check.ok:
            raise runtime_error(f"目标卷空间不足：{check.detail}", hint=_RESERVE_HINT)

        started = time.monotonic()
        with TransferLock(name, directory=env.lock_dir):
            if existing is not None:
                if terminate_first:
                    _run_step(env, ["--terminate", existing.name], action=f"终止 {existing.name}")
                if backup is not None:
                    _run_step(
                        env,
                        ["--export", existing.name, str(backup)],
                        action=f"备份 {existing.name}",
                        hint="备份失败时不会注销旧发行版；检查备份路径后重试",
                        timeout=_TRANSFER_TIMEOUT_S,
                    )
                _run_step(
                    env,
                    ["--unregister", existing.name],
                    action=f"注销 {existing.name}",
                    hint=f"旧发行版可能已被部分移除；运行 fcmd wslman list 复核，必要时 wsl --unregister {name}",
                )
            target_dir.mkdir(parents=True, exist_ok=True)
            _run_step(
                env,
                import_args,
                action=f"导入 {name}",
                hint=f"可能留下半成品或空目录；运行 fcmd wslman list 复核，必要时 wsl --unregister {name}",
                timeout=_TRANSFER_TIMEOUT_S,
            )
        after = _try_view(env)
        verified = after is not None and after.find(name) is not None
        if not verified:
            warnings.append("导入命令已成功，但复核时尚未在 wsl -l -v 中看到该发行版；稍后用 fcmd wslman list 确认")
    except KeyboardInterrupt:
        _fail(
            interrupted("导入被中断", hint="运行 fcmd wslman list 复核；必要时 wsl --unregister 清理半成品"),
            "import",
            json_output,
        )
        return
    except WslmanError as exc:
        _fail(exc, "import", json_output)
        return
    except OSError as exc:
        _fail(runtime_error(f"导入文件操作失败：{exc}", hint="检查目标目录权限与剩余空间"), "import", json_output)
        return

    _emit_action(
        "import",
        {
            **base_data,
            "preview": False,
            "executed": True,
            "verified": verified,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "warnings": [*warnings, *env.config.warnings],
        },
        json_output=json_output,
        lines=[f"导入完成: {name} → {target_dir}"],
        warnings=[*warnings, *env.config.warnings],
    )


@fcmd.tool(
    "wslman",
    subcommand="copy",
    help="复制发行版为新名称（导出临时 tar 再导入，默认清理临时文件）",
    param_help={
        "source": "源发行版名称",
        "name": "新发行版名称",
        "json_output": "输出机读结果信封",
        "preview": "打印命令与预检结论，不导出、不导入、不写临时文件",
        "dir": "新发行版的安装目录（必须为空）",
        "temp": "临时 tar 路径或所在目录；默认放在安装目录的上一级",
        "version": "新发行版的 WSL 版本；默认沿用源发行版",
        "terminate_first": "导出前先终止源发行版（替换目标时也会先终止目标）",
        "replace": "覆盖同名目标发行版",
        "yes": "确认破坏性替换",
        "backup": "--replace 时目标发行版的备份文件路径",
        "keep_temp": "保留临时 tar 便于排查",
    },
)
def copy_distro(  # noqa: PLR0912, PLR0913, PLR0917
    source: str,
    name: str,
    json_output: bool = False,
    preview: bool = False,
    dir: Path | None = None,
    temp: Path | None = None,
    version: Literal["1", "2"] | None = None,
    terminate_first: bool = False,
    replace: bool = False,
    yes: bool = False,
    backup: Path | None = None,
    keep_temp: bool = False,
) -> None:
    """导出源发行版到临时 tar，再以新名称导入；失败时保留可诊断信息。"""
    warnings: list[str] = []
    temp_path: Path | None = None
    temp_part: Path | None = None
    try:
        env = _build_environment()
        wsl_path = _require_wsl(env)
        source_name = _validated_name(source)
        target_name = _validated_name(name)
        if source_name.casefold() == target_name.casefold():
            raise invalid_argument("源与目标名称不能相同", hint="为副本指定一个新名称", field="name")
        target_dir = _require_dir(dir, example=r"fcmd wslman copy Ubuntu Ubuntu2 --dir D:\WSL\Ubuntu2")
        problem = _dir_problem(target_dir)
        if problem:
            raise invalid_argument(problem, hint="目标安装目录必须为空且位于本地磁盘", field="dir")
        if backup is not None and not replace:
            raise invalid_argument("--backup 只用于 --replace 替换前备份", field="backup")

        view = _view(env)
        origin = view.find(source_name)
        if origin is None:
            raise not_found(f"未找到发行版 {source_name}", hint="运行 fcmd wslman list 查看已安装发行版")
        source_name = origin.name
        _guard_running_strict(
            origin,
            terminate_first=terminate_first,
            command=f"fcmd wslman copy {source_name} {target_name} --dir {target_dir}",
        )

        existing = view.find(target_name)
        if existing is not None and not replace:
            raise invalid_argument(
                f"发行版 {existing.name} 已存在",
                hint="换一个名称，或加 --replace --yes（配置要求时再加 --backup <文件>）",
                field="name",
            )
        commands: list[list[str]] = []
        backup_check = None
        if existing is not None:
            target_name = existing.name
            _guard_replace(
                env,
                existing=existing,
                yes=yes,
                backup=backup,
                terminate_first=terminate_first,
                command=f"fcmd wslman copy {source_name} {target_name} --dir {target_dir}",
            )
            if terminate_first:
                commands.append([wsl_path, "--terminate", existing.name])
            if backup is not None:
                commands.append([wsl_path, "--export", existing.name, str(backup)])
                estimate, estimated = distro_disk_bytes(existing.base_path, existing.version)
                backup_check = _preflight_for(env, backup, estimate, estimated=estimated)
                if not backup_check.ok and not preview:
                    raise runtime_error(f"备份路径空间不足：{backup_check.detail}", hint=_RESERVE_HINT)
            commands.append([wsl_path, "--unregister", existing.name])

        temp_path = _temp_tar(temp, env, target_dir, target_name)
        temp_part = part_path(temp_path)
        estimate, estimated = distro_disk_bytes(origin.base_path, origin.version)
        check = _preflight_for(env, temp_path, estimate, estimated=estimated)
        effective_version = version or (str(origin.version) if origin.version else None)

        export_args = ["--export", source_name, str(temp_part)]
        import_args = ["--import", target_name, str(target_dir), str(temp_path)]
        if effective_version is not None:
            import_args.extend(["--version", effective_version])
        planned = [[wsl_path, *export_args], *commands, [wsl_path, *import_args]]
        if terminate_first:
            planned.insert(0, [wsl_path, "--terminate", source_name])

        base_data: dict[str, object] = {
            "schema": "wslman/copy/1",
            "source": source_name,
            "distro": target_name,
            "dir": str(target_dir),
            "temp": str(temp_path),
            "version": effective_version,
            "replace": existing is not None,
            "backup": str(backup) if backup is not None else None,
            "keep_temp": keep_temp,
            "commands": planned,
            "preflight": check.to_data(),
            "backup_preflight": backup_check.to_data() if backup_check is not None else None,
            "warnings": [*warnings, *env.config.warnings],
        }
        if preview:
            _emit_action(
                "copy",
                {**base_data, "preview": True, "executed": False, "verified": None, "artifacts": []},
                json_output=json_output,
                lines=[
                    f"源: {source_name}（{state_label(origin)}）",
                    f"目标: {target_name} → {target_dir}",
                    f"临时包: {temp_path}",
                    f"预检: {check.detail}",
                    "命令:",
                    *[f"  {_display_argv(argv)}" for argv in planned],
                    "[预览] 未导出、未导入、未写临时文件",
                ],
                warnings=[*warnings, *env.config.warnings],
            )
            return
        if not check.ok:
            raise runtime_error(f"临时目录所在卷空间不足：{check.detail}", hint=_RESERVE_HINT)

        started = time.monotonic()
        with ExitStack() as stack:
            stack.enter_context(TransferLock(source_name, directory=env.lock_dir))
            stack.enter_context(TransferLock(target_name, directory=env.lock_dir))
            if terminate_first:
                _run_step(env, ["--terminate", source_name], action=f"终止 {source_name}")
            with contextlib.suppress(OSError):
                temp_part.unlink()
            temp_part.parent.mkdir(parents=True, exist_ok=True)
            reporter = ProgressReporter(temp_part, estimate, label="复制")
            reporter.start()
            try:
                result = _run_wsl(env, export_args, timeout=_TRANSFER_TIMEOUT_S)
            finally:
                reporter.stop()
            if result.returncode != 0:
                detail = _first_line(decode_console(result.stderr or result.stdout))
                raise runtime_error(
                    f"导出 {source_name} 失败（退出码 {result.returncode}）：{detail}",
                    hint=f"检查临时目录空间与权限；残留文件：{temp_part}",
                )
            atomic_replace(temp_part, temp_path)
            if existing is not None:
                if terminate_first:
                    _run_step(env, ["--terminate", existing.name], action=f"终止 {existing.name}")
                if backup is not None:
                    _run_step(
                        env,
                        ["--export", existing.name, str(backup)],
                        action=f"备份 {existing.name}",
                        hint="备份失败时不会注销目标发行版；检查备份路径后重试",
                        timeout=_TRANSFER_TIMEOUT_S,
                    )
                _run_step(env, ["--unregister", existing.name], action=f"注销 {existing.name}")
            target_dir.mkdir(parents=True, exist_ok=True)
            _run_step(
                env,
                import_args,
                action=f"导入 {target_name}",
                hint=f"可能留下半成品；运行 fcmd wslman list 复核，必要时 wsl --unregister {target_name}",
                timeout=_TRANSFER_TIMEOUT_S,
            )
        size = temp_path.stat().st_size
        digest = sha256_file(temp_path)
        if not keep_temp:
            with contextlib.suppress(OSError):
                temp_path.unlink()
        after = _try_view(env)
        verified = after is not None and after.find(target_name) is not None
        if not verified:
            warnings.append("复制命令已成功，但复核时尚未在 wsl -l -v 中看到新发行版；稍后用 fcmd wslman list 确认")
    except KeyboardInterrupt:
        detail = _abort_parts([temp_part] if temp_part is not None else [], keep=keep_temp)
        _fail(interrupted(f"复制被中断；{detail}"), "copy", json_output)
        return
    except WslmanError as exc:
        _abort_parts([temp_part] if temp_part is not None else [], keep=keep_temp)
        _fail(exc, "copy", json_output)
        return
    except OSError as exc:
        detail = _abort_parts([temp_part] if temp_part is not None else [], keep=keep_temp)
        _fail(runtime_error(f"复制文件操作失败：{exc}", hint=detail), "copy", json_output)
        return

    artifacts = [
        {
            "kind": "backup",
            "path": str(temp_path),
            "bytes": size,
            "sha256": digest,
            "kept": keep_temp,
        }
    ]
    _emit_action(
        "copy",
        {
            **base_data,
            "preview": False,
            "executed": True,
            "verified": verified,
            "size_bytes": size,
            "sha256": digest,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "artifacts": artifacts,
            "warnings": [*warnings, *env.config.warnings],
        },
        json_output=json_output,
        lines=[
            f"复制完成: {source_name} → {target_name}（{target_dir}）",
            f"临时包: {temp_path}" + ("（已保留）" if keep_temp else "（已清理）"),
        ],
        warnings=[*warnings, *env.config.warnings],
    )


@fcmd.tool(
    "wslman",
    subcommand="compress",
    help="把导出的 tar 压缩为 gzip（--remove-source 需同时 --yes）",
    param_help={
        "file": "要压缩的 tar 文件",
        "json_output": "输出机读结果信封",
        "preview": "打印目标路径、预检与压缩参数，不写文件",
        "output": "输出路径；默认在源文件后追加 .gz",
        "level": "gzip 压缩级别 1-9，默认 6",
        "overwrite": "覆盖已存在的输出文件",
        "remove_source": "压缩成功后删除源文件（需 --yes）",
        "yes": "确认删除源文件",
    },
)
def compress_file(  # noqa: PLR0912, PLR0913, PLR0917
    file: Path,
    json_output: bool = False,
    preview: bool = False,
    output: Path | None = None,
    level: int = 6,
    overwrite: bool = False,
    remove_source: bool = False,
    yes: bool = False,
) -> None:
    """纯 Python gzip 流式压缩，先写 ``.part`` 再原子改名。"""
    warnings: list[str] = []
    part: Path | None = None
    try:
        env = _build_environment()
        if not file.is_file():
            raise not_found(f"源文件不存在：{file}", hint="确认导出的 tar 路径后重试", field="file")
        if file.name.casefold().endswith((".gz", ".tgz")):
            raise invalid_argument(f"源文件已是 gzip：{file}", hint="compress 只处理未压缩的 tar", field="file")
        if not 1 <= level <= 9:
            raise invalid_argument(f"--level 必须在 1-9 之间：{level}", field="level")
        target_path = output if output is not None else file.with_name(file.name + ".gz")
        if target_path == file:
            raise invalid_argument("输出路径不能与源文件相同", field="output")
        problem = volume_problem(target_path)
        if problem:
            raise invalid_argument(problem, hint="选择本地 NTFS 卷上的路径", field="output")
        if target_path.exists() and target_path.is_dir():
            raise invalid_argument(f"输出路径是目录：{target_path}", field="output")
        if target_path.exists() and not overwrite:
            raise invalid_argument(
                f"输出文件已存在：{target_path}",
                hint="换一个路径，或加 --overwrite 覆盖",
                field="output",
            )
        if remove_source and not yes:
            raise usage_error(
                "删除源文件需要 --yes",
                hint=f"确认后执行：fcmd wslman compress {file} --remove-source --yes",
            )

        source_size = file.stat().st_size
        check = _preflight_for(env, target_path, source_size, estimated=False)
        part = part_path(target_path)
        data: dict[str, object] = {
            "schema": "wslman/compress/1",
            "source": str(file),
            "output": str(target_path),
            "source_bytes": source_size,
            "level": level,
            "remove_source": remove_source,
            "preflight": check.to_data(),
            "warnings": [*warnings, *env.config.warnings],
        }
        if preview:
            _emit_action(
                "compress",
                {**data, "preview": True, "output_bytes": None, "sha256": None, "removed_source": False},
                json_output=json_output,
                lines=[
                    f"源: {file}（{format_bytes(source_size)}）",
                    f"输出: {target_path}",
                    f"级别: {level}",
                    f"预检: {check.detail}",
                    "[预览] 未写文件" + ("；执行时会删除源文件" if remove_source else ""),
                ],
                warnings=[*warnings, *env.config.warnings],
            )
            return
        if not check.ok:
            raise runtime_error(f"目标卷空间不足：{check.detail}", hint=_RESERVE_HINT)

        started = time.monotonic()
        with contextlib.suppress(OSError):
            part.unlink()
        size = gzip_to(file, part, level=level)
        atomic_replace(part, target_path)
        digest = sha256_file(target_path)
        removed = False
        if remove_source:
            file.unlink()
            removed = True
    except KeyboardInterrupt:
        detail = _abort_parts([part] if part is not None else [], keep=False)
        _fail(interrupted(f"压缩被中断；{detail}"), "compress", json_output)
        return
    except WslmanError as exc:
        _abort_parts([part] if part is not None else [], keep=False)
        _fail(exc, "compress", json_output)
        return
    except OSError as exc:
        detail = _abort_parts([part] if part is not None else [], keep=False)
        _fail(runtime_error(f"压缩文件操作失败：{exc}", hint=detail), "compress", json_output)
        return

    ratio = size / source_size * 100 if source_size else 0.0
    _emit_action(
        "compress",
        {
            **data,
            "preview": False,
            "output_bytes": size,
            "ratio_percent": round(ratio, 1),
            "sha256": digest,
            "removed_source": removed,
            "duration_ms": int((time.monotonic() - started) * 1000),
        },
        json_output=json_output,
        lines=[
            f"压缩完成: {file} → {target_path}",
            f"大小: {format_bytes(source_size)} → {format_bytes(size)}（{ratio:.1f}%）",
            f"sha256: {digest}",
            *(["已删除源文件"] if removed else []),
        ],
        warnings=[*warnings, *env.config.warnings],
    )


@fcmd.tool(
    "wslman",
    subcommand="backups",
    help="列出备份目录内的 tar / tar.gz（只读）",
    param_help={
        "dir": "备份目录；默认取配置 backups.dir（默认空，必须显式指定）",
        "json_output": "输出机读结果信封",
    },
)
def backups(dir: Path | None = None, json_output: bool = False) -> None:
    """``--dir`` 优先，其次配置 ``backups.dir``；目录不存在不隐式创建。"""
    try:
        env = _build_environment()
        configured = Path(env.config.backups_dir) if env.config.backups_dir.strip() else None
        target = dir if dir is not None else configured
        if target is None:
            raise invalid_argument(
                "未指定备份目录",
                hint=(
                    "用 --dir 指定，或在 ~/.fcmd/wslman.json 设置 backups.dir；"
                    f"推荐目录（含剩余空间）见 fcmd wslman doctor，例如 {recommended_backup_dir()}"
                ),
                field="dir",
            )
        files = backup_files(target)
    except WslmanError as exc:
        _fail(exc, "backups", json_output)
        return

    total = sum(item.size_bytes for item in files)
    data = {
        "schema": "wslman/backups/1",
        "dir": str(target),
        "count": len(files),
        "total_bytes": total,
        "files": [item.to_data() for item in files],
        "warnings": list(env.config.warnings),
    }
    if json_output:
        emit_result("wslman", "backups", data)
        return
    if not files:
        print(f"{target} 下没有 tar / tar.gz 备份。")
        return
    print(f"{target}（{len(files)} 个，共 {format_bytes(total)}）")
    for item in files:
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(item.modified))
        kind = "tar.gz" if item.compressed else "tar"
        print(f"  {item.path.name}  {kind}  {format_bytes(item.size_bytes)}  {stamp}")


@fcmd.tool(
    "wslman",
    subcommand="backup-verify",
    help="校验备份 tar / tar.gz 的完整性与 sha256（只读）",
    param_help={
        "file": "备份文件路径",
        "json_output": "输出机读结果信封",
    },
)
def backup_verify(file: Path, json_output: bool = False) -> None:
    """流式读取归档；损坏时以 CHECK_FAILED 退出。"""
    try:
        info = archive_info(file)
    except WslmanError as exc:
        _fail(exc, "backup-verify", json_output)
        return

    data = {"schema": "wslman/backup-verify/1", **info.to_data(), "ok": True}
    if json_output:
        emit_result("wslman", "backup-verify", data)
        return
    kind = "tar.gz" if info.compressed else "tar"
    print(f"备份完整: {info.path}（{kind}）")
    print(f"大小: {format_bytes(info.size_bytes)}  条目: {info.members}  解压后: {format_bytes(info.total_bytes)}")
    print(f"sha256: {info.sha256}")


@fcmd.tool(
    "wslman",
    subcommand="backup-remove",
    help="删除指定备份文件（破坏性，执行需要 --yes）",
    param_help={
        "file": "要删除的备份文件",
        "json_output": "输出机读结果信封",
        "preview": "只显示将删除的文件与大小，不删除",
        "yes": "确认删除",
    },
)
def backup_remove(file: Path, json_output: bool = False, preview: bool = False, yes: bool = False) -> None:
    """只删除单个文件；``--preview`` 不读执行器、不碰文件系统写入。"""
    warnings: list[str] = []
    try:
        if not file.is_file():
            raise not_found(
                f"备份文件不存在：{file}", hint="运行 fcmd wslman backups --dir <目录> 查看现有备份", field="file"
            )
        size = file.stat().st_size
        if preview:
            _emit_action(
                "backup-remove",
                {
                    "schema": "wslman/backup-remove/1",
                    "file": str(file),
                    "size_bytes": size,
                    "preview": True,
                    "removed": False,
                    "warnings": warnings,
                },
                json_output=json_output,
                lines=[f"将删除: {file}（{format_bytes(size)}）", "[预览] 未删除任何文件"],
            )
            return
        if not yes:
            raise usage_error(
                "删除备份需要 --yes",
                hint=f"确认后执行：fcmd wslman backup-remove {file} --yes",
            )
        file.unlink()
    except WslmanError as exc:
        _fail(exc, "backup-remove", json_output)
        return
    except OSError as exc:
        _fail(runtime_error(f"删除备份失败：{exc}", hint=f"检查文件是否被占用：{file}"), "backup-remove", json_output)
        return

    _emit_action(
        "backup-remove",
        {
            "schema": "wslman/backup-remove/1",
            "file": str(file),
            "size_bytes": size,
            "preview": False,
            "removed": True,
            "warnings": warnings,
        },
        json_output=json_output,
        lines=[f"已删除: {file}（{format_bytes(size)}）"],
    )


@fcmd.tool(
    "wslman",
    subcommand="available",
    help="列出可在线安装的发行版（只读，需联网；失败时给出离线降级提示）",
    param_help={"json_output": "输出机读结果信封"},
)
def available(json_output: bool = False) -> None:
    """合并 ``wsl --list --online`` 与微软官方清单，列出全部可用镜像。

    两个来源互补：CLI 反映本机 WSL 能直接 ``--install`` 的名称，官方清单补 amd64 /
    arm64 下载地址与 sha256。任一来源失败时用本地缓存补齐并标记 ``stale``；
    只有「两个来源都失败且无缓存」才报错，退出码语义不变。
    """
    warnings: list[str] = []
    try:
        env = _build_environment()
        _require_wsl(env)
        catalog = _images.collect_images(
            fetch_cli=lambda: _online_rows(env),
            fetch_manifest=_manifest_images,
        )
    except WslmanError as exc:
        _fail(exc, "available", json_output)
        return

    entries = _images.mark_local_state(
        catalog.entries,
        installed=_installed_names(env),
        backups=_backup_names(env),
    )
    warnings.extend(catalog.warnings)
    if not entries:
        warnings.append("在线清单为空；可能是网络不可达或 WSL 版本过旧")
    age = max(0, int(time.time() - catalog.fetched_at))
    fetched_text = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(catalog.fetched_at))
    warnings.extend(env.config.warnings)

    data = {
        "schema": "wslman/available/1",
        "count": len(entries),
        "distros": [entry.to_data() for entry in entries],
        "source": " + ".join(catalog.sources) or "cache",
        "sources": list(catalog.sources),
        "fetched_at": fetched_text,
        "from_cache": catalog.from_cache,
        "stale": catalog.stale,
        "cache_age_seconds": age,
        "ttl_seconds": _images.CACHE_TTL_SECONDS,
        "warnings": warnings,
    }
    if json_output:
        emit_result("wslman", "available", data)
        return

    print(f"可安装的镜像（{len(entries)}）：")
    name_width = max((len(entry.name) for entry in entries), default=8)
    friendly_width = max((len(entry.friendly_name) for entry in entries), default=8)
    for entry in entries:
        arches = "/".join(entry.arches) or "任意架构"
        marks = [
            friendly
            for friendly, flag in (
                ("sha256", entry.has_sha256),
                ("已安装", entry.installed),
                ("已备份", entry.backed_up),
            )
            if flag
        ]
        print(
            f"  {entry.name:<{name_width}}  {entry.friendly_name or '-':<{friendly_width}}  {arches:<16} "
            f"{'/'.join(entry.sources):<16} {' '.join(marks)}".rstrip()
        )
    print(f"数据来源：{data['source']}；获取于 {fetched_text}" + ("（缓存）" if catalog.from_cache else ""))
    for warning in warnings:
        print(f"[警告] {warning}")


def _online_rows(env: Environment) -> tuple[OnlineRow, ...]:
    """运行 ``wsl --list --online`` 并解析两列表格。"""
    result = _run_wsl(env, ["--list", "--online"], timeout=120.0)
    text = decode_console(result.stdout or result.stderr)
    if result.returncode != 0:
        if is_not_enabled_message(text):
            raise runtime_error("WSL 未启用或系统组件缺失", hint=_NOT_ENABLED_HINT)
        raise runtime_error(
            f"wsl --list --online 失败（退出码 {result.returncode}）：{_first_line(text)}",
            hint="检查网络或代理；离线时可用 fcmd wslman download <名称> --source file --file <本地 tar>",
        )
    return parse_online_list(text)


def _manifest_images() -> tuple[_images.ImageEntry, ...]:
    """拉取并解析微软官方 DistributionInfo.json。"""
    from fcmd.apps.wslman._source import fetch_manifest

    return _images.parse_distribution_info(fetch_manifest(_images.MANIFEST_URL))


def _installed_names(env: Environment) -> tuple[str, ...]:
    """已安装名称；离线或 WSL 不可用时返回空（列表仍可展示在线镜像）。"""
    try:
        return tuple(distro.name for distro in _view(env).distros)
    except Exception:
        return ()


def _backup_names(env: Environment) -> tuple[str, ...]:
    """备份目录里 ``<名称>.tar`` / ``<名称>.tar.gz`` 对应的发行版名。"""
    directory = Path(env.config.backups_dir) if env.config.backups_dir else recommended_backup_dir()
    names: list[str] = []
    try:
        for entry in directory.iterdir():
            if not entry.is_file():
                continue
            for suffix in (".tar.gz", ".tar"):
                if entry.name.casefold().endswith(suffix):
                    names.append(entry.name[: -len(suffix)])
                    break
    except OSError:
        return ()
    return tuple(names)


# ---------------------------------------------------------------------- #
# 下载档：download / unregister / shutdown
# ---------------------------------------------------------------------- #

_DOWNLOAD_HINT = (
    "wsl --install 会触发系统级安装、可能弹出 UAC，且不能指定自定义镜像；"
    "需要特定镜像或版本时改用 --source manifest / --source file 下载后执行 wslman import"
)
_DOWNLOAD_SOURCES = frozenset({"auto", "cli", "manifest", "file"})


@fcmd.tool(
    "wslman",
    subcommand="download",
    help=(
        "下载发行版归档或调用 wsl --install（会触发系统级安装、可能弹 UAC，且不能指定自定义镜像；"
        "需要特定镜像请用 --source manifest/file）"
    ),
    param_help={
        "name": "发行版名称，大小写不敏感",
        "dir": "归档下载目录（默认取配置 download.dir；manifest 源必填）",
        "file": "本地归档路径（--source file 时必填）",
        "source": "auto（默认，优先 wsl --install）/ cli / manifest / file",
        "manifest_url": "临时的清单地址，覆盖配置 download.manifest_url",
        "sha256": "期望的 sha256；缺省时用配置 download.sha256.<名称>",
        "overwrite": "覆盖已存在的目标文件",
        "preview": "只打印计划、来源与目标路径，不联网、不写文件",
        "json_output": "输出机读结果信封",
    },
)
def download(  # noqa: PLR0912, PLR0913, PLR0917
    name: str,
    dir: Path | None = None,
    file: Path | None = None,
    source: Literal["auto", "cli", "manifest", "file"] = "auto",
    manifest_url: str | None = None,
    sha256: str | None = None,
    overwrite: bool = False,
    preview: bool = False,
    json_output: bool = False,
) -> None:
    """按来源分层获取发行版：CLI 安装、清单下载或本地文件校验。

    ``auto`` 只在能力探测到 ``--no-launch`` 时走 ``wsl --install``，失败自动落到清单；
    下载始终计算并输出 sha256，只有给了期望值且匹配才置 ``verified``。
    """
    from fcmd.apps.wslman._source import download as download_archive
    from fcmd.apps.wslman._source import download_name, fetch_manifest, file_source, find_entry, parse_manifest

    warnings: list[str] = []
    try:
        env = _build_environment()
        wsl_path = _require_wsl(env)
        name = _validated_name(name)
        if source not in _DOWNLOAD_SOURCES:
            raise invalid_argument(f"不支持的 --source：{source}", field="source")
        expected = (sha256 or env.config.sha256.get(name) or "").strip().casefold() or None
        configured_dir = env.config.download_dir.strip()
        target_dir = dir or (Path(configured_dir) if configured_dir else None)

        use_cli = source == "cli" or (source == "auto" and env.capabilities.has_install_no_launch)
        if source == "cli" and not env.capabilities.has_install:
            raise runtime_error("当前 wsl.exe 不支持 --install", hint="升级 WSL 或改用 --source manifest/file")

        if preview:
            _download_preview(
                env,
                name,
                file=file,
                source=source,
                target_dir=target_dir,
                manifest_url=manifest_url,
                expected=expected,
                use_cli=use_cli,
                json_output=json_output,
            )
            return

        if use_cli:
            view = _try_view(env)
            existing = view.find(name) if view is not None else None
            if existing is not None:
                raise invalid_argument(
                    f"{existing.name} 已安装，无需下载",
                    hint=f"运行 fcmd wslman open {existing.name} 打开，或 fcmd wslman list 查看",
                )
            argv = [wsl_path, "--install", "-d", name]
            if env.capabilities.has_install_no_launch:
                argv.append("--no-launch")
            result = _run_wsl(env, argv[1:], timeout=3600.0)
            if result.returncode == 0:
                _emit_action(
                    "download",
                    {
                        "schema": "wslman/download/1",
                        "distro": name,
                        "method": "cli",
                        "system_install": True,
                        "commands": [argv],
                        "artifacts": [],
                        "hint": _DOWNLOAD_HINT,
                        "warnings": [*warnings, *env.config.warnings],
                    },
                    json_output=json_output,
                    lines=[
                        f"已通过 wsl --install 安装 {name}（--no-launch，未自动启动）",
                        f"下一步: fcmd wslman open {name}",
                        "[注意] 系统级安装、可能弹出 UAC；不适用于自定义镜像",
                    ],
                    warnings=env.config.warnings,
                )
                return
            detail = _first_line(decode_console(result.stderr or result.stdout))
            if source == "cli":
                raise runtime_error(
                    f"wsl --install -d {name} 失败（退出码 {result.returncode}）：{detail}",
                    hint=_DOWNLOAD_HINT,
                )
            warnings.append(f"wsl --install 失败，改用清单：{detail}")

        manifest_url = manifest_url or env.config.manifest_url
        if source == "file":
            if file is None:
                raise invalid_argument("--source file 需要 --file 指定本地归档", field="file")
            _emit_download_file(
                file_source(file, expected_sha256=expected),
                name,
                method="file",
                warnings=[*warnings, *env.config.warnings],
                json_output=json_output,
            )
            return
        if not manifest_url:
            raise invalid_argument(
                "未配置清单地址，无法使用 manifest 来源",
                hint="在 wslman.json 写入 download.manifest_url，或临时传 --manifest-url；也可用 --source file",
                field="manifest_url",
            )
        target_dir = _require_dir(
            target_dir,
            example=f"fcmd wslman download {name} --dir D:\\wsl-images",
        )
        payload = fetch_manifest(manifest_url)
        manifest = parse_manifest(payload)
        entry = find_entry(manifest, name)
        target_path = target_dir / download_name(name, entry.url, manifest.arch)
        if target_path.exists() and not overwrite:
            raise invalid_argument(
                f"目标文件已存在：{target_path}",
                hint="换一个 --dir，或加 --overwrite 覆盖",
                field="dir",
            )
        expected = expected or entry.sha256
        check = _preflight_for(env, target_path, entry.size, estimated=entry.size is None)
        if not check.ok:
            raise runtime_error(f"目标卷空间不足：{check.detail}", hint=_RESERVE_HINT)
        try:
            downloaded = download_archive(entry.url, target_path, expected_sha256=expected)
        except KeyboardInterrupt:
            raise interrupted(
                f"下载被中断；临时文件 {part_path(target_path)} 已保留",
                hint="重新执行会尝试续传；也可用 --source file 手工下载",
            ) from None
    except WslmanError as exc:
        _fail(exc, "download", json_output)
        return
    except OSError as exc:
        _fail(runtime_error(f"下载过程文件操作失败：{exc}", hint=_RESERVE_HINT), "download", json_output)
        return

    _emit_download_file(
        downloaded,
        name,
        method="manifest",
        warnings=[*warnings, *env.config.warnings],
        json_output=json_output,
        extra={"url": entry.url, "arch": manifest.arch, "preflight": check.to_data()},
    )


def _emit_download_file(  # noqa: PLR0913
    result_data: DownloadResult,
    name: str,
    *,
    method: str,
    warnings: Sequence[str],
    json_output: bool,
    extra: Mapping[str, object] | None = None,
) -> None:
    data = result_data.to_data()
    next_command = f"fcmd wslman import {name} {data['file']} --dir <安装目录>"
    payload: dict[str, object] = {
        "schema": "wslman/download/1",
        "distro": name,
        "method": method,
        "system_install": False,
        **data,
        "next_command": next_command,
        "hint": "归档已落盘；导入前请确认目标目录为空，并保留 sha256 以便复核",
        "warnings": warnings,
    }
    if extra:
        payload.update(extra)
    _emit_action(
        "download",
        payload,
        json_output=json_output,
        lines=[
            f"文件: {result_data.path}",
            f"大小: {format_bytes(result_data.size)}",
            f"sha256: {result_data.sha256}",
            f"校验: {'已校验' if result_data.verified else '未提供期望值，未校验'}",
            f"下一步: {next_command}",
        ],
        warnings=warnings,
    )


def _download_preview(  # noqa: PLR0913
    env: Environment,
    name: str,
    *,
    file: Path | None,
    source: str,
    target_dir: Path | None,
    manifest_url: str | None,
    expected: str | None,
    use_cli: bool,
    json_output: bool,
) -> None:
    warnings = list(env.config.warnings)
    if use_cli:
        argv = [env.capabilities.wsl_path or "wsl.exe", "--install", "-d", name]
        if env.capabilities.has_install_no_launch:
            argv.append("--no-launch")
        method = "cli"
        commands: list[list[str]] = [argv]
        lines = [
            f"发行版: {name}",
            "方式: wsl --install（系统级安装，可能弹出 UAC；不能指定自定义镜像）",
            f"命令: {_display_argv(argv)}",
            "[预览] 未执行安装、未联网、未写文件",
        ]
    elif source == "file":
        method = "file"
        commands = []
        lines = [
            f"发行版: {name}",
            f"本地文件: {file}",
            f"期望 sha256: {expected or '未提供（将只计算不校验）'}",
            "[预览] 未读取文件、未写文件",
        ]
    else:
        method = "manifest"
        commands = []
        from fcmd.apps.wslman._source import download_name

        url = manifest_url or env.config.manifest_url
        if not url:
            raise invalid_argument(
                "未配置清单地址，无法预览 manifest 来源",
                hint="在 wslman.json 写入 download.manifest_url，或临时传 --manifest-url；也可用 --source file",
                field="manifest_url",
            )
        target = (target_dir / download_name(name, url)) if target_dir else None
        lines = [
            f"发行版: {name}",
            f"清单: {url}",
            f"目标: {target or '<未指定 --dir>'}",
            f"期望 sha256: {expected or '清单未提供时只计算不校验'}",
            "[预览] 未联网、未写文件",
        ]
    _emit_action(
        "download",
        {
            "schema": "wslman/download/1",
            "distro": name,
            "method": method,
            "system_install": method == "cli",
            "preview": True,
            "file": str(file) if file else None,
            "dir": str(target_dir) if target_dir else None,
            "manifest_url": manifest_url or env.config.manifest_url,
            "expected_sha256": expected,
            "commands": commands,
            "artifacts": [],
            "warnings": warnings,
        },
        json_output=json_output,
        lines=lines,
        warnings=warnings,
    )


@fcmd.tool(
    "wslman",
    subcommand="unregister",
    help="注销发行版并删除其虚拟磁盘（破坏性，需 --yes；可先 --backup 导出）",
    param_help={
        "distro": "发行版名称，大小写不敏感",
        "yes": "确认注销；缺失时退出码 2 并给出完整命令",
        "terminate_first": "运行中时先终止再注销",
        "backup": "注销前导出到该文件；导出失败则绝不注销",
        "preview": "展示 BasePath、占用与将删除的虚拟磁盘，不注销",
        "json_output": "输出机读结果信封",
    },
)
def unregister(  # noqa: PLR0913, PLR0917
    distro: str,
    yes: bool = False,
    terminate_first: bool = False,
    backup: Path | None = None,
    preview: bool = False,
    json_output: bool = False,
) -> None:
    """注销前可先导出备份；``--preview`` 只读列表与元数据，不删任何东西。"""
    warnings: list[str] = []
    started = time.monotonic()
    name = distro.strip()
    try:
        env = _build_environment()
        wsl_path = _require_wsl(env)
        name = _validated_name(distro)
        target = _require_target(env, name)
        name = target.name
        vhdx = Path(target.base_path) / "ext4.vhdx" if target.base_path else None
        estimate, estimated = distro_disk_bytes(target.base_path, target.version)
        scope = (
            f"发行版: {name}\nBasePath: {target.base_path or '未知'}\n"
            f"占用: {format_bytes(estimate, estimated=estimated)}\n"
            f"虚拟磁盘: {vhdx or '未知'}"
        )
        commands: list[list[str]] = []
        if terminate_first:
            commands.append([wsl_path, "--terminate", name])
        if backup is not None:
            commands.append([wsl_path, "--export", name, str(backup)])
        commands.append([wsl_path, "--unregister", name])
        command_text = f"fcmd wslman unregister {name} --yes"

        if preview:
            _emit_action(
                "unregister",
                {
                    "schema": "wslman/unregister/1",
                    "distro": name,
                    "base_path": target.base_path,
                    "vhdx": str(vhdx) if vhdx else None,
                    "disk_bytes": estimate,
                    "estimated": estimated,
                    "backup": str(backup) if backup else None,
                    "commands": commands,
                    "destructive": True,
                    "confirmed": yes,
                    "preview": True,
                    "removed": False,
                    "warnings": [*warnings, *env.config.warnings],
                },
                json_output=json_output,
                lines=[
                    scope,
                    "命令:",
                    *[f"  {_display_argv(argv)}" for argv in commands],
                    "[预览] 未删除任何内容；虚拟磁盘无回收站，确认后加 --yes 执行",
                ],
                warnings=[*warnings, *env.config.warnings],
            )
            return

        if target.state == "running" and not terminate_first:
            raise invalid_argument(
                f"{name} 正在运行，注销前必须先停机",
                hint=f"执行：{command_text} --terminate-first",
            )
        if not yes:
            suffix = f" --backup {backup}" if backup else " --backup <备份文件>"
            raise usage_error(
                f"注销 {name} 需要 --yes",
                hint=f"确认后执行：{command_text}{suffix}",
            )

        backup_info: dict[str, object] | None = None
        with TransferLock(name, directory=env.lock_dir):
            if backup is not None:
                backup_info = _export_for_backup(env, name, backup, estimate, estimated=estimated)
            if terminate_first:
                _run_step(
                    env,
                    ["--terminate", name],
                    action=f"注销前终止 {name}",
                    hint=f"手动终止后重试：{command_text} --terminate-first",
                )
            _run_step(
                env,
                ["--unregister", name],
                action=f"注销 {name}",
                hint="注销后虚拟磁盘不可恢复；可先用 --backup 导出",
            )
        remaining = bool(target.base_path and Path(target.base_path).exists())
    except KeyboardInterrupt:
        _fail(interrupted(f"注销 {name} 被中断", hint="运行 fcmd wslman list 复核当前状态"), "unregister", json_output)
        return
    except WslmanError as exc:
        _fail(exc, "unregister", json_output)
        return
    except OSError as exc:
        _fail(
            runtime_error(f"注销过程文件操作失败：{exc}", hint="运行 fcmd wslman list 复核"), "unregister", json_output
        )
        return

    _emit_action(
        "unregister",
        {
            "schema": "wslman/unregister/1",
            "distro": name,
            "base_path": target.base_path,
            "vhdx": str(vhdx) if vhdx else None,
            "disk_bytes": estimate,
            "estimated": estimated,
            "backup": str(backup) if backup else None,
            "backup_info": backup_info,
            "commands": commands,
            "destructive": True,
            "confirmed": True,
            "preview": False,
            "removed": True,
            "remaining_base_path": remaining,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "warnings": [*warnings, *env.config.warnings],
        },
        json_output=json_output,
        lines=[
            f"已注销 {name}",
            *(
                [f"已备份: {backup}（sha256 {backup_info['sha256']}）"]
                if backup_info is not None
                else ["[提醒] 未提供 --backup，虚拟磁盘已删除且无回收站"]
            ),
            f"BasePath {'仍有残留目录（WSL 版本差异，未自动清理）：' + str(target.base_path) if remaining else '已不存在'}",
        ],
        warnings=[*warnings, *env.config.warnings],
    )


def _export_for_backup(
    env: Environment,
    name: str,
    backup: Path,
    estimate: int | None,
    *,
    estimated: bool,
) -> dict[str, object]:
    """注销前的备份导出：失败即抛错，调用方因此不会继续注销。"""
    if backup.exists():
        raise invalid_argument(
            f"备份文件已存在：{backup}",
            hint="换一个 --backup 路径；不会覆盖已有文件",
            field="backup",
        )
    problem = volume_problem(backup)
    if problem:
        raise invalid_argument(problem, hint="选择本地 NTFS 卷上的路径", field="backup")
    check = _preflight_for(env, backup, estimate, estimated=estimated)
    if not check.ok:
        raise runtime_error(f"备份目标空间不足，已取消注销：{check.detail}", hint=_RESERVE_HINT)
    part = part_path(backup)
    part.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        part.unlink()
    reporter = ProgressReporter(part, estimate, label="备份")
    reporter.start()
    try:
        result = _run_wsl(env, ["--export", name, str(part)], timeout=_TRANSFER_TIMEOUT_S)
    finally:
        reporter.stop()
    if result.returncode != 0:
        with contextlib.suppress(OSError):
            part.unlink()
        detail = _first_line(decode_console(result.stderr or result.stdout))
        raise runtime_error(
            f"备份 {name} 失败（退出码 {result.returncode}）：{detail}",
            hint="已取消注销，发行版保持原样；检查空间后重试",
        )
    if not part.is_file():
        raise runtime_error("wsl --export 返回成功但未生成备份文件", hint=f"检查 {part} 所在目录的权限")
    atomic_replace(part, backup)
    return {
        "file": str(backup),
        "size_bytes": backup.stat().st_size,
        "sha256": sha256_file(backup),
        "preflight": check.to_data(),
    }


@fcmd.tool(
    "wslman",
    subcommand="shutdown",
    help="停止全部 WSL 发行版与虚拟机（破坏性，需 --yes；会连带停掉 Docker Desktop 等）",
    param_help={
        "yes": "确认关闭全部发行版",
        "preview": "只打印命令与影响范围，不停止任何进程",
        "json_output": "输出机读结果信封",
    },
)
def shutdown(yes: bool = False, preview: bool = False, json_output: bool = False) -> None:
    """``wsl --shutdown``；预览不读取执行器，执行前必须显式 ``--yes``。"""
    impacts = "会停止全部发行版与 WSL 虚拟机，并连带停掉 Docker Desktop、IDE Remote 等后端"
    try:
        env = _build_environment()
        wsl_path = _require_wsl(env)
        argv = [wsl_path, "--shutdown"]
        if preview:
            _emit_action(
                "shutdown",
                {
                    "schema": "wslman/shutdown/1",
                    "commands": [argv],
                    "destructive": True,
                    "confirmed": yes,
                    "preview": True,
                    "shut_down": False,
                    "warnings": [*env.config.warnings],
                },
                json_output=json_output,
                lines=[f"命令: {_display_argv(argv)}", f"影响: {impacts}", "[预览] 未停止任何进程"],
                warnings=env.config.warnings,
            )
            return
        if not yes:
            raise usage_error("shutdown 需要 --yes", hint="确认后执行：fcmd wslman shutdown --yes")
        _run_step(env, ["--shutdown"], action="wsl --shutdown", hint=impacts)
        running = _try_view(env)
        remaining = [distro.name for distro in running.distros if distro.state == "running"] if running else []
    except WslmanError as exc:
        _fail(exc, "shutdown", json_output)
        return

    _emit_action(
        "shutdown",
        {
            "schema": "wslman/shutdown/1",
            "commands": [argv],
            "destructive": True,
            "confirmed": True,
            "preview": False,
            "shut_down": True,
            "running_after": remaining,
            "warnings": [*env.config.warnings],
        },
        json_output=json_output,
        lines=[f"已执行 {_display_argv(argv)}", f"复核运行中发行版: {', '.join(remaining) if remaining else '无'}"],
        warnings=env.config.warnings,
    )


@fcmd.tool(
    "wslman",
    subcommand="config",
    help="查看并校验 .wslconfig（只读，不修改文件）",
    param_help={
        "json_output": "输出机读结果信封",
        "path": "指定配置文件路径，默认 %UserProfile%\\.wslconfig",
    },
)
def config(json_output: bool = False, path: Path | None = None) -> None:
    """解析 ``.wslconfig``，对未知小节 / 未知键 / 重复键给出提示。

    Parameters
    ----------
    json_output:
        输出 ``fcmd-result/1`` 机读信封
    path:
        自定义配置文件路径；文件不存在时报错而不是静默回落默认文件
    """
    target = path or wslconfig_default_path()
    if path is not None and not path.exists():
        _fail(not_found(f"配置文件不存在：{path}", hint="默认位置为 %UserProfile%\\.wslconfig"), "config", json_output)
        return
    wslconfig = load_wslconfig(target)
    if wslconfig.error:
        _fail(
            runtime_error(wslconfig.error, hint="检查文件是否为合法 INI；wslman 只读，不会修改该文件"),
            "config",
            json_output,
        )
        return

    data = {
        "schema": "wslman/config/1",
        "path": str(wslconfig.path),
        "exists": wslconfig.exists,
        "sections": {name: dict(values) for name, values in wslconfig.sections.items()},
        "warnings": list(wslconfig.warnings),
    }
    if json_output:
        emit_result("wslman", "config", data)
        return

    print(f"路径: {wslconfig.path}")
    print(f"状态: {'存在' if wslconfig.exists else '不存在'}")
    for line in summary_lines(wslconfig):
        print(line)


@fcmd.tool(
    "wslman",
    subcommand="doctor",
    help="检查 WSL 环境、注册表与 .wslconfig（有失败项时退出码 5）",
    param_help={"json_output": "输出机读结果信封"},
)
def doctor(json_output: bool = False) -> None:
    """逐项诊断：平台、wsl.exe、版本、发行版列表、注册表、默认值、配置与备份目录。"""
    env = _build_environment()
    checks = _doctor_checks(env)
    failed = sum(1 for check in checks if check.status == "fail")
    warned = sum(1 for check in checks if check.status == "warn")

    data = {
        "schema": "wslman/doctor/1",
        "checks": [check.to_data() for check in checks],
        "failed": failed,
        "warned": warned,
        "backup_dir": {
            "recommended": str(recommended_backup_dir()),
            "configured": env.config.backups_dir,
        },
    }
    if json_output:
        if failed:
            record_error("CHECK_FAILED", kind="check")
        emit_result("wslman", "doctor", data, ok=failed == 0)
        return

    labels = {"ok": "通过", "warn": "警告", "fail": "失败"}
    for check in checks:
        print(f"[{labels.get(check.status, check.status)}] {check.name}: {check.detail}")
        if check.hint and check.status != "ok":
            print(f"    提示：{check.hint}")
    for warning in env.config.warnings:
        print(f"[配置警告] {warning}")
    if failed:
        print(f"\n检查未通过：{failed} 项失败，{warned} 项警告")
        record_error("CHECK_FAILED", kind="check")
    else:
        print(f"\n检查通过（{warned} 项警告）")


def _doctor_checks(env: Environment) -> tuple[_Check, ...]:
    rows, notes, list_error = _probe_list(env)
    view = merge(rows, env.registry) if list_error is None else None
    checks = [
        _platform_check(env),
        _wsl_check(env),
        _version_check(env),
        _list_check(rows, notes, list_error, view),
        _registry_check(env),
    ]
    if view is not None:
        checks.extend(_default_checks(env, view))
    checks.append(_default_version_check(env))
    checks.append(_wslconfig_check())
    checks.append(_backup_check(env))
    return tuple(checks)


def _probe_list(env: Environment) -> tuple[tuple[DistroRow, ...], tuple[str, ...], WslmanError | None]:
    try:
        rows, notes = _list_rows(env)
    except WslmanError as exc:
        return (), (), exc
    return rows, notes, None


def _platform_check(env: Environment) -> _Check:
    if env.platform_ok:
        return _Check("平台", "ok", f"{sys.platform}（Windows）")
    return _Check("平台", "fail", f"当前平台 {sys.platform} 不受支持", "请在 Windows 上运行 wslman")


def _wsl_check(env: Environment) -> _Check:
    path = env.capabilities.wsl_path
    if path:
        return _Check("wsl.exe", "ok", path)
    return _Check("wsl.exe", "fail", "未在 PATH 中找到 wsl.exe", "以管理员 PowerShell 执行 wsl --install")


def _version_check(env: Environment) -> _Check:
    version = env.capabilities.version
    if version:
        return _Check("WSL 版本", "ok", version)
    if env.capabilities.wsl_path is None:
        return _Check("WSL 版本", "warn", "wsl.exe 缺失，无法读取版本", "先安装 WSL")
    return _Check("WSL 版本", "warn", "无法从 wsl --version 解析版本", "手动运行 wsl --version 确认")


def _list_check(
    rows: tuple[DistroRow, ...],
    notes: tuple[str, ...],
    error: WslmanError | None,
    view: DistroView | None,
) -> _Check:
    if error is not None:
        return _Check("发行版列表", "fail", str(error), error.hint)
    if not rows:
        detail = notes[0] if notes else "没有已安装的发行版"
        return _Check("发行版列表", "warn", detail, "运行 fcmd wslman available 查看可安装列表")
    detail = f"共 {len(rows)} 个发行版"
    if view is not None and view.warnings:
        detail += f"，{len(view.warnings)} 条提示"
    return _Check("发行版列表", "ok", detail)


def _registry_check(env: Environment) -> _Check:
    if env.registry.available:
        return _Check("Lxss 注册表", "ok", f"可读，登记 {len(env.registry.distros)} 个发行版")
    return _Check(
        "Lxss 注册表",
        "warn",
        env.registry.error or "不可读",
        "BasePath 与默认用户将不可用；不影响 list 的运行状态",
    )


def _default_checks(env: Environment, view: DistroView) -> list[_Check]:
    marked = next((distro.name for distro in view.distros if distro.default), None)
    registry_default = env.registry.default_name
    checks: list[_Check] = []
    if marked and registry_default and marked.casefold() != registry_default.casefold():
        checks.append(
            _Check(
                "默认发行版",
                "warn",
                f"wsl 标记为 {marked}，注册表为 {registry_default}",
                "运行 wsl --set-default <名称> 统一两边",
            )
        )
    elif view.default:
        checks.append(_Check("默认发行版", "ok", view.default))
    else:
        checks.append(_Check("默认发行版", "warn", "未设置默认发行版", "运行 wsl --set-default <名称>"))
    if not view.distros:
        checks.append(_Check("已安装发行版", "warn", "没有已安装的发行版", "运行 fcmd wslman available 查看可安装列表"))
    return checks


def _default_version_check(env: Environment) -> _Check:
    version = env.registry.default_version
    if version in (1, 2):
        return _Check("默认 WSL 版本", "ok", f"DefaultVersion={version}")
    return _Check("默认 WSL 版本", "warn", "未从注册表读取到", "运行 wsl --set-default-version 2")


def _wslconfig_check() -> _Check:
    wslconfig = load_wslconfig(wslconfig_default_path())
    path = str(wslconfig.path)
    if wslconfig.error:
        return _Check(".wslconfig", "fail", wslconfig.error, "修正 INI 语法；wslman 只读，不会修改该文件")
    if not wslconfig.exists:
        return _Check(".wslconfig", "warn", f"未找到 {path}", "可选文件；需要限制内存/CPU 时再创建")
    if wslconfig.warnings:
        return _Check(
            ".wslconfig", "warn", f"{path} 可解析，{len(wslconfig.warnings)} 条提示", "运行 fcmd wslman config 查看明细"
        )
    return _Check(".wslconfig", "ok", path)


def _backup_check(env: Environment) -> _Check:
    configured = env.config.backups_dir
    target = Path(configured) if configured else recommended_backup_dir()
    info = volume_info(target)
    if info.free_bytes is None:
        return _Check("备份目录", "warn", info.error or f"{target} 不可用", "在配置 backups.dir 指定一个本地目录")
    free_text = format_bytes(info.free_bytes)
    if configured:
        detail = f"{target}（已配置），所在卷剩余 {free_text}"
    else:
        detail = f"推荐 {target}（未配置，必须显式指定），所在卷剩余 {free_text}"
    if info.free_bytes < 2 * 1024**3:
        return _Check("备份目录", "warn", detail, "剩余空间不足 2 GiB，导出大发行版前先清理")
    return _Check("备份目录", "ok", detail)


@fcmd.main("wslman")
def main() -> None:
    """``wslman`` CLI 入口。"""


if __name__ == "__main__":  # pragma: no cover
    main()
