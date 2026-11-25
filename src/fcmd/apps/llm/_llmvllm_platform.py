"""_llmvllm_platform - vLLM 平台探测：WSL 实例枚举、Python 探针与可用性快照。

``wsl -d <名称>`` 会把已停止的发行版拉起来，所以这里的默认口径是**只探
运行中的实例**：``allow_start`` 为假时把其余实例名放进 ``VllmPlatform.stopped``
交给界面提示，只有用户显式确认（安装 vLLM）时才连已停止实例一起探。打开
llmw（``RunPage`` 在首帧后于后台线程调用 :func:`detect_platform`）或跑测试
都不会隐式启动 Ubuntu；单个发行版的信息（``$HOME`` / Python / 版本 / GPU）
合并成一次 ``sh -lc`` 往返，避免四次子进程开销。
"""

from __future__ import annotations

import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from fcmd.apps.llm.llmbase import Runner, run_command

__all__ = [
    "PlatformKind",
    "VllmInterpreter",
    "VllmPlatform",
    "detect_platform",
    "parse_probe",
    "parse_wsl_distros",
    "parse_wsl_rows",
    "python_supported",
    "reset_platform_cache",
]

PlatformKind = Literal["linux", "wsl", "none"]

MIN_PYTHON: Final[tuple[int, int]] = (3, 10)
MAX_PYTHON: Final[tuple[int, int]] = (3, 13)
"""vLLM 轮子覆盖的 Python 版本区间（含下界，不含上界）。"""

_WSL_EXE: Final[str] = "wsl.exe"
_DISTRO_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_VERSION_RE: Final[re.Pattern[str]] = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:\.post\d+)?$")
_PLATFORM_TTL: Final[float] = 60.0
_PLATFORM_CACHE: dict[str, tuple[float, VllmPlatform]] = {}


@dataclass(frozen=True, slots=True)
class VllmInterpreter:
    """一个可用于安装 vLLM 的 Python 解释器。

    ``distro`` 为空表示本机 Linux；否则是 WSL 发行版名。``home`` 是安装
    venv 的目标根（WSL 内为 ``$HOME``，Linux 为本机 home），只用于拼目录。
    """

    python: str
    version: str
    distro: str = ""
    home: str = ""
    gpu: str = ""

    @property
    def label(self) -> str:
        """展示名：``WSL: Ubuntu-24.04 · Python 3.12`` / ``本机 · Python 3.12``。"""
        where = f"WSL: {self.distro}" if self.distro else "本机"
        return f"{where} · Python {self.version}"


@dataclass(frozen=True, slots=True)
class VllmPlatform:
    """vLLM 在当前机器上的可用性快照。"""

    kind: PlatformKind
    supported: bool
    reason: str
    interpreters: tuple[VllmInterpreter, ...] = ()
    stopped: tuple[str, ...] = ()
    """未参与探测的已停止 WSL 实例：探测等于启动它们，所以默认不碰。"""

    @property
    def ready(self) -> bool:
        """是否有可用解释器（决定「安装」按钮是否可用）。"""
        return bool(self.interpreters)

    @property
    def primary(self) -> VllmInterpreter | None:
        """默认解释器（探测顺序的第一个）。"""
        return self.interpreters[0] if self.interpreters else None


_RUNNING_MARKS = ("running", "运行")
"""判断 ``wsl -l -v`` 状态列是否为运行中的子串（不区分大小写）。"""


def parse_wsl_rows(text: str) -> list[tuple[str, bool]]:
    """解析 ``wsl.exe --list --verbose``：``(名称, 是否运行中)``，默认发行版排在前。

    状态列随系统语言变化（``Running`` / ``运行中``…），这里只认「running」与
    「运行」两个子串；认不出的一律按「未运行」处理——**宁可少探测一个实例，
    也不隐式启动它**：``wsl -d <名称>`` 会把已停止的发行版拉起来。
    """
    cleaned = text.replace("\x00", "")
    default = ""
    others: list[tuple[str, bool]] = []
    for line in cleaned.splitlines():
        stripped = line.strip()
        running = any(mark in stripped.casefold() for mark in _RUNNING_MARKS)
        parts = stripped.split()
        if not parts or parts[0] in {"NAME", "名称", "*"}:
            if parts[:1] == ["*"] and len(parts) >= 2:
                default = parts[1]
            continue
        name = parts[0]
        if _DISTRO_RE.match(name) is None:
            continue
        others.append((name, running))
    ordered = ([(default, True)] if default else []) + others
    merged: dict[str, bool] = {}
    for name, running in ordered:
        if name:
            merged[name] = merged.get(name, False) or running
    return list(merged.items())


def parse_wsl_distros(text: str) -> list[str]:
    """解析 ``wsl.exe --list --verbose`` 输出，默认发行版排在最前。

    WSL 在 Windows 控制台返回 UTF-16LE，经按本地编码解码后会夹杂 ``\\x00``；
    这里先去掉空字符再按行解析，避免发行版名被拆成单字符。错误信息
    （``wsl: ...``）与表头在名称正则处被过滤掉。
    """
    return [name for name, _running in parse_wsl_rows(text)]


def python_supported(version: str) -> bool:
    """Python 版本是否落在 vLLM 轮子支持的区间内。"""
    parts = _version_tuple(version)
    return MIN_PYTHON <= parts[:2] < MAX_PYTHON


def _version_tuple(text: str) -> tuple[int, int, int]:
    match = _VERSION_RE.match(text.strip().split("+", 1)[0])
    if match is None:
        return (0, 0, 0)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _clean(text: str) -> str:
    return text.replace("\x00", "").strip()


# WSL 内一次性取 ``$HOME`` / Python 路径 / 版本 / GPU 的脚本。四个值以前分四次
# ``wsl.exe ... sh -lc``（每次都有 WSL 会话建立开销），合并成一次往返后用
# ``HOME:`` / ``PY:`` / ``VER:`` / ``GPU:`` 前缀标记，由 :func:`parse_probe`
# 还原；GPU 缺失时该行输出空值而不是省略，解析端不必猜。
_PROBE_SCRIPT: Final[str] = r"""printf 'HOME:%s\n' "$HOME"
py=$(command -v python3.12 || command -v python3.11 || command -v python3.10 || command -v python3)
printf 'PY:%s\n' "$py"
ver=""
if [ -n "$py" ]; then ver=$("$py" -c 'import sys;print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null); fi
printf 'VER:%s\n' "$ver"
printf 'GPU:%s\n' "$(/usr/lib/wsl/lib/nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)"
"""

_PROBE_KEYS: Final[tuple[str, ...]] = ("HOME", "PY", "VER", "GPU")


def parse_probe(text: str) -> tuple[str, str, str, str]:
    """解析 :data:`_PROBE_SCRIPT` 的输出：``(home, python, version, gpu)``。

    只按首个冒号切分（值里可能带冒号），缺项返回空串；同一前缀重复出现时
    取第一次的值，避免发行版的 shell rc 往 stdout 里写东西时覆盖真实结果。
    """
    found: dict[str, str] = dict.fromkeys(_PROBE_KEYS, "")
    for line in _clean(text).splitlines():
        key, sep, value = line.partition(":")
        if sep and key in found and not found[key]:
            found[key] = value.strip()
    return found["HOME"], found["PY"], found["VER"], found["GPU"]


def _probe_wsl(runner: Runner, distro: str) -> VllmInterpreter | None:
    """探测单个 WSL 发行版：一次 ``sh -lc`` 取 ``$HOME`` / Python / 版本 / GPU。"""
    text = runner([_WSL_EXE, "-d", distro, "--", "sh", "-lc", _PROBE_SCRIPT], 20.0).stdout
    home, python, version, gpu = parse_probe(text)
    if not home or not python or not python_supported(version):
        return None
    return VllmInterpreter(python=python, version=version, distro=distro, home=home, gpu=gpu)


def _probe_linux(runner: Runner) -> VllmInterpreter | None:
    """探测本机 Linux 的 python3。"""
    python = shutil.which("python3") or ""
    if not python:
        return None
    result = runner([python, "-c", "import sys;print('%d.%d.%d' % sys.version_info[:3])"], 15.0)
    version = _clean(result.stdout or result.stderr)
    if not python_supported(version):
        return None
    gpu = _clean(runner(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], 10.0).stdout).splitlines()
    return VllmInterpreter(python=python, version=version, home=str(Path.home()), gpu=gpu[0] if gpu else "")


_PLATFORM_CACHE: dict[str, tuple[float, VllmPlatform]] = {}


def detect_platform(
    runner: Runner = run_command,
    *,
    use_cache: bool = True,
    home: Path | None = None,
    allow_start: bool = False,
) -> VllmPlatform:
    """探测 vLLM 平台可用性（Windows 依次尝试各 WSL 发行版）。

    ``supported`` 表示操作系统层面能跑 vLLM（Linux / WSL2）；``ready`` 表示
    还找到了可安装的 Python 解释器。Windows 没有 WSL 或非 Linux 平台返回
    ``supported=False``，UI 据此折叠并禁用配置。

    ``allow_start`` 控制要不要探测**已停止**的 WSL 实例：``wsl -d <名称>`` 会把
    发行版拉起来，探测就等于启动它，所以默认只探运行中的实例，其余实例名放进
    ``stopped`` 交给界面提示；只有用户显式确认（安装 vLLM）时才传 ``True``。
    这样打开 llmw 或跑测试都不会顺手把 Ubuntu 启动起来。
    """
    cache_key = f"{'nt' if os.name == 'nt' else os.name}:{'start' if allow_start else 'norun'}"
    if use_cache:
        cached = _PLATFORM_CACHE.get(cache_key)
        if cached is not None and time.monotonic() - cached[0] < _PLATFORM_TTL:
            return cached[1]
    platform = _detect_platform_uncached(runner, home, allow_start=allow_start)
    _PLATFORM_CACHE[cache_key] = (time.monotonic(), platform)
    return platform


def reset_platform_cache() -> None:
    """清空平台探测缓存（测试与设置页「重新检测」使用）。"""
    _PLATFORM_CACHE.clear()


def _detect_platform_uncached(  # noqa: PLR0911 - 按平台 / 有无解释器分支返回不同说明
    runner: Runner,
    home: Path | None,
    *,
    allow_start: bool = False,
) -> VllmPlatform:
    """未缓存的平台探测；``allow_start`` 决定是否连已停止的 WSL 实例一起探。

    ``wsl -d <名称> -- sh -lc ...`` 会把发行版拉起来，所以默认只探运行中的实例：
    打开 llmw（``RunPage`` 后台线程探测）或跑测试都不该顺手启动 Ubuntu。
    """
    del home
    if os.name == "nt":
        listing = runner([_WSL_EXE, "--list", "--verbose"], 15.0)
        rows = parse_wsl_rows(f"{listing.stdout}\n{listing.stderr}")
        if not rows:
            return VllmPlatform(
                "none",
                False,
                "Windows 需要 WSL2：未检测到已安装的 WSL 发行版（wsl --install）",
            )
        stopped = tuple(name for name, running in rows if not running)
        if allow_start:
            targets = [name for name, _running in rows]
        else:
            targets = [name for name, running in rows if running]
        interpreters: list[VllmInterpreter] = []
        for distro in targets:
            found = _probe_wsl(runner, distro)
            if found is not None:
                interpreters.append(found)
        skipped = tuple(name for name in stopped if name not in targets)
        if not interpreters:
            if skipped:
                return VllmPlatform(
                    "wsl",
                    True,
                    "WSL2 可用，但以下实例未运行、未做探测："
                    + "、".join(skipped)
                    + "；检测会启动它们（安装 vLLM 时会先确认再启动）",
                    (),
                    skipped,
                )
            return VllmPlatform(
                "wsl",
                True,
                "WSL2 可用，但发行版中没有 Python 3.10–3.12（vLLM 轮子要求）；可在 WSL 内安装 python3.12 后重新检测",
            )
        primary = interpreters[0]
        gpu_note = f" · GPU: {primary.gpu}" if primary.gpu else " · 未检测到 GPU（CPU 推理会非常慢）"
        skipped_note = f" · 未探测已停止实例 {'、'.join(skipped)}" if skipped else ""
        return VllmPlatform(
            "wsl",
            True,
            f"WSL2 · {primary.distro} · Python {primary.version}{gpu_note}{skipped_note}",
            tuple(interpreters),
            skipped,
        )
    if os.name == "posix":
        found = _probe_linux(runner)
        if found is None:
            return VllmPlatform(
                "linux",
                True,
                "Linux 可用，但未找到 Python 3.10–3.12；安装对应版本后重新检测",
            )
        gpu_note = f" · GPU: {found.gpu}" if found.gpu else " · 未检测到 GPU"
        return VllmPlatform("linux", True, f"Linux · Python {found.version}{gpu_note}", (found,))
    return VllmPlatform("none", False, f"当前系统（{os.name}）不支持 vLLM：需要 Linux 或 Windows + WSL2")
