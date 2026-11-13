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

import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import fcmd
from fcmd.apps.wslman._config import WslmanConfig, load_config
from fcmd.apps.wslman._decode import (
    DistroRow,
    decode_console,
    is_no_distribution_message,
    is_not_enabled_message,
    parse_distro_list,
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
    to_data,
    with_disk,
)
from fcmd.apps.wslman._errors import WslmanError, not_found, runtime_error
from fcmd.apps.wslman._registry import RegistrySnapshot, WindowsRegistryReader, unavailable_snapshot
from fcmd.apps.wslman._runner import Capabilities, CommandResult, Runner, SubprocessRunner, is_windows, probe
from fcmd.apps.wslman._wslconfig import default_path as wslconfig_default_path
from fcmd.apps.wslman._wslconfig import load_wslconfig, summary_lines
from fcmd.core.result import emit_result, record_error, report_error

__all__ = ["config", "doctor", "info", "list_distros", "main", "status"]

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
    """一次命令运行内的环境快照；执行器可注入。"""

    runner: Runner
    capabilities: Capabilities
    registry: RegistrySnapshot
    config: WslmanConfig
    platform_ok: bool


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
