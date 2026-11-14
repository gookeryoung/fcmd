"""devman 测试夹具：假网络、假执行器、假用户环境与安装目录构造。

测试不联网、不执行真实 node / uv、不读写真实注册表。
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from fcmd.apps.devman._net import FetchError
from fcmd.apps.devman._paths import Layout
from fcmd.apps.devman._runner import CommandResult
from fcmd.apps.devman._state import MARKER_SCHEMA, now_iso, write_marker


def result(*, returncode: int = 0, stdout: str = "", stderr: str = "") -> CommandResult:
    return CommandResult(returncode=returncode, stdout=stdout, stderr=stderr)


class FakeFetcher:
    """按 URL 返回预置文本 / 字节；未预置即失败，防止测试静默联网。"""

    def __init__(
        self,
        texts: Mapping[str, str] | None = None,
        payloads: Mapping[str, bytes] | None = None,
        errors: Mapping[str, BaseException] | None = None,
    ) -> None:
        self.texts = dict(texts or {})
        self.payloads = dict(payloads or {})
        self.errors = dict(errors or {})
        self.text_calls: list[str] = []
        self.download_calls: list[str] = []

    def get_text(self, url: str) -> str:
        self.text_calls.append(url)
        if url in self.errors:
            raise self.errors[url]
        if url not in self.texts:
            raise AssertionError(f"未预置文本：{url}")
        return self.texts[url]

    def download(self, url: str, dest: Path, progress: Callable[[int, int], None] | None = None) -> None:
        self.download_calls.append(url)
        if url in self.errors:
            raise self.errors[url]
        if url not in self.payloads:
            raise AssertionError(f"未预置构件：{url}")
        data = self.payloads[url]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        if progress is not None:
            progress(len(data), len(data))


class FakeRunner:
    """按 handler 返回预置结果，并记录调用（含子进程环境）。"""

    def __init__(self, handler: Callable[[list[str], Mapping[str, str] | None], CommandResult | BaseException] | None = None) -> None:
        self.handler = handler or (lambda argv, env: result())
        self.calls: list[list[str]] = []
        self.envs: list[dict[str, str]] = []

    def run(
        self,
        argv: Iterable[str],
        *,
        env: Mapping[str, str] | None = None,
        timeout_s: int = 120,
    ) -> CommandResult:
        command = [str(item) for item in argv]
        self.calls.append(command)
        self.envs.append(dict(env or {}))
        outcome = self.handler(command, env)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeEnvStore:
    """用户环境变量的内存实现。"""

    def __init__(self, values: Mapping[str, str] | None = None) -> None:
        self.values = dict(values or {})
        self.writes: list[tuple[str, str]] = []

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def set(self, name: str, value: str) -> None:
        self.values[name] = value
        self.writes.append((name, value))


def make_node_zip(version: str, *, arch: str = "x64", exe_content: bytes = b"MZ-fake-node") -> tuple[bytes, str]:
    """构造结构正确的 node zip；返回 ``(bytes, sha256)``。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr(f"node-v{version}-win-{arch}/node.exe", exe_content)
        bundle.writestr(f"node-v{version}-win-{arch}/npm.cmd", "@echo off\r\n")
    data = buffer.getvalue()
    return data, hashlib.sha256(data).hexdigest()


def node_plan_payload(
    version: str,
    *,
    arch: str = "x64",
    sha: str | None = None,
    base: str = "https://nodejs.org/dist",
) -> tuple[str, bytes, str, str]:
    """返回 ``(构件 URL, bytes, 清单 URL, sha256)``。"""
    data, digest = make_node_zip(version, arch=arch)
    filename = f"node-v{version}-win-{arch}.zip"
    return f"{base}/v{version}/{filename}", data, f"https://nodejs.org/dist/v{version}/SHASUMS256.txt", sha or digest


def shasums_text(*pairs: tuple[str, str]) -> str:
    return "".join(f"{digest}  {name}\n" for name, digest in pairs)


def install_fake_node(layout: Layout, version: str, *, complete: bool = True) -> Path:
    """在 devman 根下造一个 node 安装目录（默认含完整标记）。"""
    directory = layout.version_dir("node", version)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "node.exe").write_bytes(b"MZ-fake-node")
    if complete:
        write_marker(
            directory,
            {
                "schema": MARKER_SCHEMA,
                "tool": "node",
                "version": version,
                "source": "fake",
                "sha256": "0" * 64,
                "verify": "official-shasums",
                "installed_at": now_iso(),
            },
        )
    return directory


def install_fake_python(layout: Layout, key: str, version: str, *, complete: bool = True) -> Path:
    """在 devman 根下造一个 uv 布局的 python 安装目录。"""
    directory = layout.version_dir("python", key)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "python.exe").write_bytes(b"MZ-fake-python")
    if complete:
        write_marker(
            directory,
            {
                "schema": MARKER_SCHEMA,
                "tool": "python",
                "version": version,
                "source": f"uv python install {key}",
                "sha256": None,
                "verify": "uv-managed",
                "installed_at": now_iso(),
            },
        )
    return directory


PY_KEY = "cpython-3.12.15-windows-x86_64-none"


def python_list_json(*, include_downloads: bool = True) -> str:
    """uv python list --output-format json 的样例输出。"""
    rows: list[dict[str, Any]] = [
        {
            "key": PY_KEY,
            "version": "3.12.15",
            "implementation": "cpython",
            "os": "windows",
            "arch": "x86_64",
            "variant": "default",
            "path": None,
            "url": "https://example.invalid/cpython-3.12.15.zip",
        },
    ]
    if include_downloads:
        rows.extend(
            [
                {
                    "key": "cpython-3.13.14-windows-x86_64-none",
                    "version": "3.13.14",
                    "implementation": "cpython",
                    "os": "windows",
                    "arch": "x86_64",
                    "variant": "default",
                    "path": "C:/fake/3.13.14/python.exe",
                    "url": None,
                },
                {
                    "key": "cpython-3.12.15-windows-x86_64-freethreaded",
                    "version": "3.12.15",
                    "implementation": "cpython",
                    "os": "windows",
                    "arch": "x86_64",
                    "variant": "freethreaded",
                    "path": None,
                    "url": None,
                },
                {
                    "key": "cpython-3.12.15-linux-x86_64-gnu",
                    "version": "3.12.15",
                    "implementation": "cpython",
                    "os": "linux",
                    "arch": "x86_64",
                    "variant": "default",
                    "path": None,
                    "url": None,
                },
            ]
        )
    return json.dumps(rows)


def node_index_json(*versions: tuple[str, str | bool]) -> str:
    rows = [
        {"version": f"v{version}", "lts": lts, "date": "2026-01-01", "files": ["win-x64-zip"]}
        for version, lts in versions
    ]
    return json.dumps(rows)


def fetch_error(message: str = "boom") -> FetchError:
    return FetchError(message)
