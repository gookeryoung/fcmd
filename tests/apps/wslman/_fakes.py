"""wslman 测试夹具：假执行器、假注册表数据与结果构造。

放在 ``_fakes.py`` 而不是 ``conftest.py``，便于测试直接 import 工厂函数。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from fcmd.apps.wslman._registry import RegistryDistro, RegistrySnapshot
from fcmd.apps.wslman._runner import Capabilities, CommandResult

WSL = r"C:\Windows\System32\wsl.exe"


def utf16(text: str) -> bytes:
    """模拟 ``wsl.exe`` 被重定向时的 UTF-16LE（带 BOM）输出。"""
    return text.encode("utf-16")


def make_result(
    *,
    returncode: int = 0,
    stdout: bytes = b"",
    stderr: bytes = b"",
) -> CommandResult:
    return CommandResult(argv=("wsl.exe",), returncode=returncode, stdout=stdout, stderr=stderr, duration_ms=1)


class FakeRunner:
    """按 ``argv[1:]`` 返回预置结果；未预置的命令直接失败，防止测试静默跑真命令。

    值为 ``list`` 时按调用顺序依次弹出，可模拟「执行前后各读一次列表」这类序列；
    列表耗尽后继续使用最后一个结果。值为可调用对象时以完整 argv 调用它，
    便于模拟 ``wsl --export`` 写入目标文件；``fallback`` 用于路径含临时目录、
    无法预先写死的命令。
    """

    def __init__(
        self,
        responses: Mapping[tuple[str, ...], CommandResult | BaseException | list[CommandResult | BaseException]]
        | None = None,
        *,
        fallback: Callable[[tuple[str, ...]], CommandResult] | None = None,
    ) -> None:
        self.responses: dict[tuple[str, ...], CommandResult | BaseException | list[CommandResult | BaseException]] = (
            dict(responses or {})
        )
        self.fallback = fallback
        self.calls: list[tuple[str, ...]] = []

    def run(
        self,
        argv: Any,
        *,
        timeout: float | None = None,
        env: Any = None,
    ) -> CommandResult:
        command = tuple(argv)
        key = command[1:]
        self.calls.append(command)
        if key not in self.responses:
            if self.fallback is not None:
                return self.fallback(command)
            raise AssertionError(f"未预置的命令：{key}")
        value = self.responses[key]
        if isinstance(value, list):
            if not value:
                raise AssertionError(f"预置结果已耗尽：{key}")
            value = value.pop(0) if len(value) > 1 else value[0]
        if isinstance(value, BaseException):
            raise value
        return value


def capabilities(**overrides: Any) -> Capabilities:
    values: dict[str, Any] = {
        "wsl_path": WSL,
        "version": "2.3.26.0",
        "help_text": "--install --no-launch --import --version",
        "has_install": True,
        "has_install_no_launch": True,
        "has_import_version": True,
        "errors": (),
    }
    values.update(overrides)
    return Capabilities(**values)


def registry_distro(
    name: str,
    *,
    version: int | None = 2,
    base_path: str | None = None,
    key: str | None = None,
) -> RegistryDistro:
    return RegistryDistro(
        name=name,
        key=key or f"{{{name}}}",
        base_path=base_path,
        version=version,
        default_uid=1000,
        package_family=None,
        flags=15,
        state=1,
    )


def snapshot(
    *,
    distros: tuple[RegistryDistro, ...] = (),
    default_distribution: str | None = None,
    default_version: int | None = 2,
    available: bool = True,
    error: str | None = None,
) -> RegistrySnapshot:
    return RegistrySnapshot(
        available=available,
        default_distribution=default_distribution,
        default_version=default_version,
        distros=distros,
        error=error,
    )
