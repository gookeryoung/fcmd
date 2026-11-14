"""Python 来源：把取解释器的工作委托给 uv。

约定（见 ADR 0024 D7）：``--install-dir`` 指向 devman 根，``--no-bin`` 不留链接，
``--no-registry`` 不写 ``py`` 启动器注册表；uv 自己的默认目录与所有 venv 都不动。
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fcmd.apps.devman._errors import DOWNLOAD_FAILED, UV_UNAVAILABLE, not_found, runtime_error
from fcmd.apps.devman._paths import Layout
from fcmd.apps.devman._runner import Runner
from fcmd.apps.devman._state import MARKER_SCHEMA, now_iso, write_marker
from fcmd.apps.devman._toolchain import is_complete, version_of

__all__ = [
    "PythonEntry",
    "find_uv",
    "install",
    "latest_matching",
    "list_remote",
    "parse_list",
    "resolve_key",
]

_UNSTABLE_RE = re.compile(r"[ab]|rc|dev", re.IGNORECASE)
_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def find_uv(setting: str) -> str:
    """定位 uv 可执行文件；``auto`` 走 PATH，显式值必须是存在的文件。"""
    value = (setting or "auto").strip()
    if value == "auto":
        found = shutil.which("uv")
        if not found:
            raise runtime_error(
                "未找到 uv，无法获取 Python 解释器",
                hint="先安装 uv（见 fcmd envdev），或用 config.python.uv 指定路径",
                code=UV_UNAVAILABLE,
            )
        return found
    path = Path(value).expanduser()
    if not path.is_file():
        raise runtime_error(
            f"config.python.uv 指向的文件不存在：{path}",
            hint="改成 auto 或有效的 uv 路径",
            code=UV_UNAVAILABLE,
        )
    return str(path)


@dataclass(frozen=True, slots=True)
class PythonEntry:
    """uv 索引里的一条 Python 记录。"""

    key: str
    version: str
    implementation: str
    os: str
    arch: str
    variant: str | None
    path: str | None
    url: str | None

    @property
    def stable(self) -> bool:
        return bool(_VERSION_RE.fullmatch(self.version))

    def to_data(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "version": self.version,
            "implementation": self.implementation,
            "os": self.os,
            "arch": self.arch,
            "variant": self.variant,
            "installed": self.path is not None,
        }


def _arch_matches(arch: str) -> bool:
    machine = platform.machine().lower()
    wanted = "aarch64" if machine in {"arm64", "aarch64"} else "x86_64"
    return arch in {"", wanted}


def parse_list(text: str) -> list[PythonEntry]:
    """解析 ``uv python list --output-format json``。"""
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise runtime_error(
            f"uv python list 输出无法解析：{exc}",
            hint="uv 版本可能不兼容；可用 fcmd devman versions python 只看已安装",
            code=UV_UNAVAILABLE,
        ) from exc
    if not isinstance(raw, list):
        raise runtime_error("uv python list 输出不是数组", code=UV_UNAVAILABLE)
    entries: list[PythonEntry] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        version = item.get("version")
        if not isinstance(key, str) or not isinstance(version, str):
            continue
        entries.append(
            PythonEntry(
                key=key,
                version=version,
                implementation=str(item.get("implementation") or ""),
                os=str(item.get("os") or ""),
                arch=str(item.get("arch") or ""),
                variant=item.get("variant") if isinstance(item.get("variant"), str) else None,
                path=item.get("path") if isinstance(item.get("path"), str) else None,
                url=item.get("url") if isinstance(item.get("url"), str) else None,
            )
        )
    return entries


def _windows_entries(entries: list[PythonEntry], *, include_prerelease: bool) -> list[PythonEntry]:
    rows = [
        entry
        for entry in entries
        if entry.os == "windows"
        and entry.implementation == "cpython"
        and entry.variant in {None, "default"}
        and _arch_matches(entry.arch)
    ]
    if not include_prerelease:
        rows = [entry for entry in rows if entry.stable]
    return rows


def list_remote(runner: Runner, uv: str, *, include_prerelease: bool = False) -> list[PythonEntry]:
    """读取 uv 的全量索引（含已安装与可下载）。"""
    result = runner.run([uv, "python", "list", "--output-format", "json", "--all-versions"])
    if result.returncode != 0:
        raise runtime_error(
            f"uv python list 失败（退出 {result.returncode}）：{result.stderr.strip()}",
            hint="确认 uv 版本 >= 0.5 且可正常联网",
            code=UV_UNAVAILABLE,
        )
    return _windows_entries(parse_list(result.stdout), include_prerelease=include_prerelease)


def _key_sort(key: str) -> tuple[int, ...]:
    found = re.search(r"(\d+)\.(\d+)\.(\d+)", version_of("python", key))
    if not found:
        return (0, 0, 0)
    return (int(found.group(1)), int(found.group(2)), int(found.group(3)))


def latest_matching(entries: list[PythonEntry], raw: str) -> str:
    """在 uv 索引里解析版本前缀 / 安装键 / ``latest``，返回完整安装键。"""
    candidates = entries
    if raw and raw.lower() != "latest":
        if not raw[0].isdigit():
            # 完整安装键（cpython-3.12.15-windows-x86_64-none）或键前缀
            candidates = [entry for entry in entries if entry.key == raw or entry.key.startswith(raw)]
            if not candidates:
                raise not_found(
                    f"uv 索引里没有安装键 {raw}",
                    hint="用 fcmd devman versions python --remote 查看可用版本",
                )
            return max(candidates, key=lambda entry: _key_sort(entry.key)).key
        wanted = tuple(int(part) for part in re.findall(r"\d+", raw))
        if not wanted:
            raise not_found(f"无法解析 Python 版本前缀 {raw!r}", hint="例如 python@3.12")
        candidates = [
            entry for entry in entries if tuple(int(part) for part in entry.version.split("."))[: len(wanted)] == wanted
        ]
    if not candidates:
        raise not_found(
            f"uv 索引里没有匹配 {raw or 'latest'} 的 Python",
            hint="用 fcmd devman versions python --remote 查看可用版本",
        )
    return max(candidates, key=lambda entry: _key_sort(entry.key)).key


def resolve_key(runner: Runner, uv: str, raw: str, *, include_prerelease: bool = False) -> str:
    """把规格解析成 uv 安装键。"""
    entries = list_remote(runner, uv, include_prerelease=include_prerelease)
    return latest_matching(entries, raw)


def _install_env(base: Mapping[str, str], layout: Layout) -> dict[str, str]:
    env = dict(base)
    env["UV_PYTHON_INSTALL_DIR"] = str(layout.python_root)
    return env


def install(  # noqa: PLR0913 - 安装参数与 uv 调用语义一一对应
    layout: Layout,
    key: str,
    *,
    runner: Runner,
    uv: str,
    mirror: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """调用 uv 安装指定键；已装且完整时跳过。"""
    final = layout.version_dir("python", key)
    existed_complete = is_complete(layout, "python", key)
    if existed_complete and not force:
        return {
            "version": version_of("python", key),
            "key": key,
            "changed": False,
            "path": str(final),
            "message": "已安装，跳过（--force 可重装）",
            "actions": [],
        }

    layout.python_root.mkdir(parents=True, exist_ok=True)
    argv = [
        uv,
        "python",
        "install",
        key,
        "--install-dir",
        str(layout.python_root),
        "--no-bin",
        "--no-registry",
        "--managed-python",
    ]
    if mirror:
        argv.extend(["--mirror", mirror])
    if force:
        argv.append("--reinstall")
    try:
        result = runner.run(argv, env=_install_env(os.environ, layout))
        if result.returncode != 0:
            raise runtime_error(
                f"uv python install 失败（退出 {result.returncode}）：{result.stderr.strip() or result.stdout.strip()}",
                hint="检查网络 / 镜像；devman 不修改 uv 的全局配置",
                code=DOWNLOAD_FAILED,
            )
        exe = final / "python.exe"
        if not exe.is_file():
            raise runtime_error(
                f"uv 报告成功但未找到 {exe}",
                hint="检查 uv 的安装目录布局是否变化",
                code=UV_UNAVAILABLE,
            )
        probe = runner.run([str(exe), "-V"])
        reported = (probe.stdout or probe.stderr or "").strip()
        if probe.returncode != 0 or version_of("python", key) not in reported:
            raise runtime_error(
                f"新装的 Python 自检失败：{reported or probe.returncode}",
                hint="解释器与版本不匹配，已放弃",
            )
    except Exception:
        if not existed_complete:
            shutil.rmtree(final, ignore_errors=True)
        raise

    write_marker(
        final,
        {
            "schema": MARKER_SCHEMA,
            "tool": "python",
            "version": version_of("python", key),
            "source": f"uv python install {key}",
            "sha256": None,
            "verify": "uv-managed",
            "installed_at": now_iso(),
        },
    )
    return {
        "version": version_of("python", key),
        "key": key,
        "changed": True,
        "path": str(final),
        "message": f"已安装 python {version_of('python', key)}",
        "actions": [
            {"action": "uv-python-install", "argv": argv},
            {"action": "install", "path": str(final)},
        ],
    }
