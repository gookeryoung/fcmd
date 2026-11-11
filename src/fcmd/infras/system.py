"""系统基础操作：进程终止、命令查找、系统信息、文件夹备份、环境变量持久化与文件副作用。

本模块只做操作与数据收集，**不打印**任何内容；需要回显的过程反馈以字符串
列表返回，由 :mod:`fcmd.tools.system` 下的命令行外壳统一输出。
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

__all__ = [
    "backup_folder",
    "collect_system_info",
    "format_bytes",
    "kill_processes",
    "persist_env",
    "set_environment_variable",
    "shell_profile_path",
    "which_commands",
    "write_text_file",
]


def kill_processes(names: list[str]) -> list[str]:
    """按名称前缀终止进程（跨平台，基于 psutil），返回逐条回显消息。

    逐个名称用 ``psutil.process_iter`` 匹配名称前缀，命中后先 ``terminate()``
    给进程一个优雅退出的机会，等待 3 秒仍未退出的 ``kill()``。
    未匹配时返回提示后继续，不视为失败。
    """
    import psutil  # 延迟导入：仅终止进程场景需要（冷启动导入税）

    messages: list[str] = []
    for name in names:
        messages.append(f"终止进程: {name}")
        targets = [
            proc for proc in psutil.process_iter(["pid", "name"]) if (proc.info.get("name") or "").startswith(name)
        ]
        if not targets:
            messages.append(f"  未找到匹配进程: {name}")
            continue
        for proc in targets:
            messages.append(f"  PID={proc.pid} 名称={proc.info.get('name')} -> terminate")
            try:
                proc.terminate()
            except psutil.NoSuchProcess:
                messages.append(f"  PID={proc.pid} 已退出")
            except psutil.AccessDenied:
                messages.append(f"  PID={proc.pid} 权限不足，跳过")
        _gone, alive = psutil.wait_procs(targets, timeout=3)
        for proc in alive:
            try:
                proc.kill()
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied:
                messages.append(f"  PID={proc.pid} 权限不足，无法强制终止")
            else:
                messages.append(f"  PID={proc.pid} 已强制终止")
    return messages


def which_commands(commands: list[str]) -> list[str]:
    """逐条查找可执行命令路径（跨平台 shutil.which），返回回显消息。"""
    import shutil  # 延迟导入：shutil 仅命令查找场景需要（冷启动导入税）

    messages: list[str] = []
    for command in commands:
        path = shutil.which(command)
        if path is None:
            messages.append(f"{command} -> 未找到")
        else:
            messages.append(f"{command} -> {path}")
    return messages


def format_bytes(size: int) -> str:
    """将字节数格式化为人类可读字符串（形如 ``1.5 GB``）。"""
    value: float = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PB"


def collect_system_info() -> dict[str, str]:
    """收集当前环境的系统信息（Python/平台/内存/磁盘/CPU）。"""
    import platform  # 延迟导入：platform 仅系统诊断场景需要（冷启动导入税）
    import time

    import psutil

    info: dict[str, str] = {
        "Python 版本": sys.version.split()[0],
        "Python 路径": sys.executable,
        "平台": platform.platform(),
        "架构": platform.machine(),
        "处理器": platform.processor() or "未知",
        "操作系统": f"{platform.system()} {platform.release()}",
    }

    mem = psutil.virtual_memory()
    info["内存总量"] = format_bytes(mem.total)
    info["内存已用"] = f"{format_bytes(mem.used)} ({mem.percent:.1f}%)"
    info["内存可用"] = format_bytes(mem.available)

    swap = psutil.swap_memory()
    info["交换区总量"] = format_bytes(swap.total)
    info["交换区已用"] = format_bytes(swap.used)

    # 磁盘信息（当前目录所在分区）
    try:
        usage = psutil.disk_usage(str(Path.cwd()))
    except OSError:  # pragma: no cover - 罕见平台
        pass
    else:
        info["磁盘总量"] = format_bytes(usage.total)
        info["磁盘已用"] = format_bytes(usage.used)
        info["磁盘可用"] = format_bytes(usage.free)

    info["CPU 核心数"] = str(psutil.cpu_count() or "未知")
    try:
        boot_time = psutil.boot_time()
    except OSError:  # pragma: no cover - 罕见平台
        pass
    else:
        info["开机时间"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(boot_time))
    info["工作目录"] = str(Path.cwd())
    return info


def backup_folder(src: str = ".", dst: str = "./backup", max_zip: int = 5) -> list[str]:
    """备份文件夹到指定目录（zip 压缩），自动清理旧备份，返回提示消息。"""
    import shutil  # 延迟导入：备份场景才需要（冷启动导入税）
    import time

    messages: list[str] = []
    src_path = Path(src)
    dst_path = Path(dst)
    if not src_path.exists():
        raise FileNotFoundError(f"源文件夹不存在: {src_path}")
    if not dst_path.exists():
        dst_path.mkdir(parents=True, exist_ok=True)
        messages.append(f"创建目标文件夹: {dst_path}")

    timestamp = time.strftime("_%Y%m%d_%H%M%S")
    target_path = dst_path / (src_path.stem + timestamp + ".zip")
    # 以源目录的父目录为 root、源目录名为 base_dir，压缩包内保留顶层目录名
    shutil.make_archive(str(target_path.with_suffix("")), "zip", root_dir=src_path.parent, base_dir=src_path.name)

    # 清理旧备份：递归删除匹配源文件夹名的旧 zip，保留最新 max_zip 个
    while True:
        zip_paths = [fp for fp in dst_path.rglob("*.zip") if src_path.stem in str(fp)]
        zip_files = sorted(zip_paths, key=lambda fn: str(fn)[-19:-4])
        if len(zip_files) <= max_zip:
            break
        zip_files[0].unlink()
    messages.append(f"备份完成: {target_path}")
    return messages


def set_environment_variable(name: str, value: str, default: bool = False) -> None:
    """设置当前进程环境变量（``default`` 为 True 时不覆盖已有值）。"""
    if default:
        os.environ.setdefault(name, value)
    else:
        os.environ[name] = value


def write_text_file(path: str, content: str, encoding: str = "utf-8") -> None:
    """将文本内容写入指定路径的文件。"""
    Path(path).write_text(content, encoding=encoding)


# ============================================================================
# 环境变量持久化
# ============================================================================

# Windows 注册表用户环境变量键路径（HKEY_CURRENT_USER 下）。
_WIN_ENV_KEY: str = "Environment"


def shell_profile_path() -> Path:
    """返回 Linux/macOS 上用于持久化环境变量的 shell 启动文件路径。

    优先使用 ``~/.profile``（登录 shell 通用，bash/sh/zsh 均会读取）。
    """
    return Path.home() / ".profile"


def _persist_env_windows(name: str, value: str) -> None:  # pragma: no cover - 仅 Windows 运行
    """在 Windows 上将环境变量写入注册表 ``HKCU\\Environment`` 并广播刷新通知。"""
    import ctypes
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_ENV_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_EXPAND_SZ, value)

    # 广播 WM_SETTINGCHANGE，通知已运行进程刷新环境块（否则新终端才生效）。
    # getattr 取 windll 避免 Linux 下 pyrefly 报 missing-attribute（windll 仅 Windows 存在）
    hwnd_broadcast = 0xFFFF  # HWND_BROADCAST
    wm_settingchange = 0x001A
    smto_abortifhung = 0x0002
    windll = getattr(ctypes, "windll")  # noqa: B009 - 跨平台动态访问，非安全访问用途
    windll.user32.SendMessageTimeoutW(
        hwnd_broadcast,
        wm_settingchange,
        0,
        "Environment",
        smto_abortifhung,
        5000,
        None,
    )


def _persist_env_unix(name: str, value: str) -> None:
    """在 Linux/macOS 上将 ``export NAME="value"`` 追加或更新到 shell 启动文件。"""
    profile = shell_profile_path()
    profile.parent.mkdir(parents=True, exist_ok=True)

    export_line = f'export {name}="{value}"'
    try:
        existing = profile.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        existing = ""

    # 匹配同名变量的已有 export 行（行首可有空白），存在则替换，否则追加。
    pattern = re.compile(rf"^\s*export\s+{re.escape(name)}=.*$", re.MULTILINE)
    if pattern.search(existing):
        updated = pattern.sub(export_line, existing)
    else:
        prefix = existing if existing.endswith("\n") or not existing else existing + "\n"
        updated = f"{prefix}{export_line}\n"

    profile.write_text(updated, encoding="utf-8")


def persist_env(name: str, value: str) -> None:
    """持久化设置环境变量（跨平台），并同步更新当前进程。

    ``set_environment_variable`` 只改当前进程的 ``os.environ``；本函数在
    Windows 额外写入注册表用户环境变量并广播刷新，Linux/macOS 写入
    ``~/.profile``，使新终端自动生效。

    Parameters
    ----------
    name:
        环境变量名。
    value:
        环境变量值。

    Notes
    -----
    持久化写入通常需重开终端或重新登录后对新进程生效；当前进程立即生效。
    """
    os.environ[name] = value
    if sys.platform == "win32":
        _persist_env_windows(name, value)
    else:
        _persist_env_unix(name, value)
