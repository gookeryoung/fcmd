"""开发域基础设施：pip 包名展开/受保护包过滤与 Windows SSH 客户端探测。

工具层（``fcmd.tools.dev.*``）负责打印与 CLI 参数适配；本模块只做纯逻辑，
不打印、不抛用户文案（命令执行失败由工具层处理）。
"""

from __future__ import annotations

import fnmatch
import os
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

__all__ = [
    "PROTECTED_PACKAGES",
    "expand_packages",
    "filter_protected",
    "pip_installed_packages",
    "resolve_windows_openssh",
    "resolve_windows_plink",
    "resolve_windows_ssh_client",
    "rewrite_ssh_cmd",
]

# 受保护包名（pip 卸载/重装时跳过，避免破坏运行环境）
PROTECTED_PACKAGES: frozenset[str] = frozenset({"fcmd"})


def pip_installed_packages() -> list[str]:
    """获取当前环境已安装包名列表（``pip list --format=freeze`` 输出解析）。"""
    result = subprocess.run(["pip", "list", "--format=freeze"], check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"pip list 失败（退出码 {result.returncode}）: {result.stderr.strip()}")
    return [line.split("==")[0].strip() for line in result.stdout.splitlines() if "==" in line]


def filter_protected(packages: list[str]) -> tuple[list[str], list[str]]:
    """过滤受保护包，返回 ``(保留的包, 被跳过的包)``。"""
    protected_lower = {p.lower() for p in PROTECTED_PACKAGES}
    kept: list[str] = []
    skipped: list[str] = []
    for package in packages:
        if package.lower() in protected_lower:
            skipped.append(package)
        else:
            kept.append(package)
    return kept, skipped


def expand_packages(packages: list[str]) -> list[str]:
    """展开包名通配符（不做受保护包过滤）。

    无通配符字符（``* ? [ ]``）的模式原样保留（不触发 ``pip list`` 采集）；
    有通配符的模式按已安装包名大小写不敏感匹配展开（全模式共享一次采集）。
    """
    expanded: list[str] = []
    installed: list[str] | None = None
    for pattern in packages:
        if not any(ch in pattern for ch in "*?[]"):
            expanded.append(pattern)
            continue
        if installed is None:
            installed = pip_installed_packages()
        expanded.extend(pkg for pkg in installed if fnmatch.fnmatchcase(pkg.lower(), pattern.lower()))
    return expanded


@lru_cache(maxsize=1)
def resolve_windows_openssh() -> str | None:
    """定位 Windows 上 OpenSSH ssh.exe 的完整路径（非 win32 返回 ``None``）。

    优先检查 Windows 10/11 自带 OpenSSH 的标准安装路径，再用 ``where ssh``
    枚举 PATH 中的 ``ssh.exe``，逐个执行 ``ssh -V`` 从 stderr 版本串判断
    是否含 "OpenSSH"，取首个 OpenSSH 路径。
    """
    if sys.platform != "win32":
        return None

    candidates: list[Path] = []
    system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
    for sub in ("System32", "SysWOW64"):
        path = Path(system_root) / sub / "OpenSSH" / "ssh.exe"
        if path.is_file():
            candidates.append(path)

    try:
        proc = subprocess.run(
            ["where", "ssh"],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode == 0:
            for raw_line in proc.stdout.strip().splitlines():
                candidate_line = raw_line.strip()
                if candidate_line and candidate_line.lower().endswith(".exe"):
                    candidates.append(Path(candidate_line))
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    for ssh_path in candidates:
        if not ssh_path.is_file():
            continue
        try:
            result = subprocess.run(
                [str(ssh_path), "-V"],
                capture_output=True,
                check=False,
                text=True,
                timeout=3,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            version = (result.stderr or result.stdout or "").lower()
            if "openssh" in version and "putty" not in version:
                return str(ssh_path)
        except (subprocess.TimeoutExpired, OSError):
            continue

    return None


@lru_cache(maxsize=1)
def resolve_windows_plink() -> str | None:
    """定位 Windows 上 plink.exe（PuTTY 命令行工具）的完整路径。

    plink 支持 ``-pw`` 从命令行自动传递密码（OpenSSH for Windows 不支持），
    因此优先使用 plink 让密码参数生效。非 win32 返回 ``None``。
    """
    if sys.platform != "win32":
        return None

    candidates: list[Path] = []

    try:
        proc = subprocess.run(
            ["where", "plink"],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode == 0:
            for raw_line in proc.stdout.strip().splitlines():
                candidate_line = raw_line.strip()
                if candidate_line and candidate_line.lower().endswith(".exe"):
                    candidates.append(Path(candidate_line))
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    for plink_path in candidates:
        if not plink_path.is_file():
            continue
        try:
            result = subprocess.run(
                [str(plink_path), "-V"],
                capture_output=True,
                check=False,
                text=True,
                timeout=3,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            version = (result.stderr or result.stdout or "").lower()
            if "putty" in version:
                return str(plink_path)
        except (subprocess.TimeoutExpired, OSError):
            continue

    return None


@lru_cache(maxsize=1)
def resolve_windows_ssh_client() -> tuple[str, str] | None:
    """返回 Windows 上最优 SSH 客户端：``("plink", path)`` / ``("openssh", path)`` / ``None``。"""
    plink = resolve_windows_plink()
    if plink is not None:
        return ("plink", plink)
    openssh = resolve_windows_openssh()
    if openssh is not None:
        return ("openssh", openssh)
    return None


def _rewrite_openssh_cmd_as_plink(openssh_cmd: str, plink_path: str) -> str:
    """把 OpenSSH 命令模板字符串改写成 plink 格式（保留 ``{param}`` 占位符）。"""
    import re

    if not openssh_cmd.startswith("ssh "):
        return openssh_cmd
    cmd = plink_path + " -ssh -pw {password} -batch" + openssh_cmd[3:]
    cmd = re.sub(r"\s+-o\s+\S+", "", cmd)
    return cmd.replace(" -p {port}", " -P {port}")


def rewrite_ssh_cmd(
    cmd: str | tuple[str, ...] | None,
    platform: str | None = None,
) -> str | tuple[str, ...] | None:
    """在 win32 平台上按检测到的 SSH 客户端重写命令模板。

    plink 优先（支持 ``-pw`` 自动传密码），其次 OpenSSH（只替换为完整路径），
    都没找到时原样返回。仅处理以裸 ``ssh `` 起头的 shell 字符串。
    """
    platform = sys.platform if platform is None else platform
    if platform != "win32" or not isinstance(cmd, str) or not cmd.startswith("ssh "):
        return cmd

    client = resolve_windows_ssh_client()
    if client is None:
        return cmd

    client_type, client_path = client
    if client_type == "plink":
        return _rewrite_openssh_cmd_as_plink(cmd, client_path)
    return client_path + " " + cmd[4:]
