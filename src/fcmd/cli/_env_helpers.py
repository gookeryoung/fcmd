"""``fcmd env`` / ``fcmd doctor`` 共享的纯函数辅助。

供 :mod:`fcmd.cli._builtins.env_cmd` 与 :mod:`fcmd.cli._doctor_helpers`
复用。本模块以下划线开头，``ensure_tools_discovered`` 会跳过它（非工具模块）。
"""

from __future__ import annotations

import importlib.util
import platform
import re
import sys
from pathlib import Path
from typing import Any, cast

__all__ = ["collect_optional_deps_status", "platform_string"]

# 可选依赖清单：(extra 分组, import 包名, 分发包名)
# extra 对应 pyproject.toml 的 [project.optional-dependencies]
# import 包名用于 find_spec 探测；分发包名用于 dist-info 扫描读取版本
#
# 只列「需要额外安装」的依赖：pillow / psutil / pyyaml / rich / Qt 都是主依赖，
# 缺了说明安装不完整（重装 fcmd），不作为可选分组出现在 env / doctor 报告里。
_OPTIONAL_DEPS: tuple[tuple[str, str, str], ...] = (
    ("pdf", "fitz", "pymupdf"),
    ("pdf", "pypdf", "pypdf"),
    ("ocr", "pytesseract", "pytesseract"),
    ("svg", "resvg_py", "resvg-py"),
    ("crypto", "cryptography", "cryptography"),
    ("archive", "pyzipper", "pyzipper"),
    ("filecheck", "puremagic", "puremagic"),
    ("color", "coloraide", "coloraide"),
    ("pe", "pefile", "pefile"),
    ("ppt", "pptx", "python-pptx"),
)


def platform_string() -> str:
    """返回与 ``platform.platform()`` 逐字等价的平台描述（Win 上走轻量探测）。

    Windows 上 ``platform.platform()`` 经 ``uname()`` → ``win32_ver()``：
    先试 WMI，失败后执行 ``ver`` 命令并读注册表，单次 40–120ms。
    ``sys.getwindowsversion()`` 直接提供 major/minor/build、product type 与
    service pack，配合 CPython 自己的版本映射表可拼出同一字符串（约 1ms）。

    映射表不可用（未来 Python 改名）时回退 ``platform.platform()``，宁可慢
    也不改输出语义；非 Windows 平台差异很小，同样直接用标准库实现。
    """
    if sys.platform != "win32":
        return platform.platform()
    # Python 3.10 的 ``_WIN32_*_RELEASES`` 是 ``(major, minor) -> name`` 字典，
    # 3.11+ 才改为 ``((major, minor, build), name)`` 列表；旧解释器直接走标准库。
    if sys.version_info < (3, 11):
        return platform.platform()
    # ``sys.getwindowsversion`` 是 Windows-only API：pyrefly 在 Linux 上按非 Windows
    # typeshed 解析会报 missing-attribute，而在 Windows 上又会把 pyrefly: ignore
    # 判成 unused-ignore（同一行两个平台结论相反）。这里在平台边界把它当 Any 取，
    # 两种平台都能通过静态检查，运行期行为不变（函数开头已排除非 Windows）。
    winver = cast("Any", sys).getwindowsversion()
    table = "_WIN32_CLIENT_RELEASES" if winver.product_type == 1 else "_WIN32_SERVER_RELEASES"
    # 表名是运行期拼接的字符串，getattr 无法静态求值（pyrefly 只会取到默认值 None），
    # 这里按 CPython 的实际结构收窄；取不到时仍走下面的回退分支。
    releases = cast("list[tuple[tuple[int, int, int], str]]", getattr(platform, table, None) or [])
    if not releases:
        return platform.platform()
    current = (winver.major, winver.minor, winver.build)
    release = next((name for threshold, name in releases if current >= threshold), "")
    version = "{}.{}.{}".format(*current)
    service_pack = getattr(winver, "service_pack_major", None)
    csd = "" if service_pack is None else f"SP{service_pack}"
    return "-".join(part for part in ("Windows", release, version, csd) if part)


def collect_optional_deps_status() -> list[dict[str, Any]]:
    """收集可选依赖的安装状态与版本。

    返回列表，每项包含 extra / package / installed / version（已安装时）。

    仅用 ``find_spec`` + ``*.dist-info`` 扫描探测，**不真正导入模块**：
    ``fcmd env`` / ``fcmd doctor`` 属只读诊断，导入 PyMuPDF 等重型依赖会
    破坏三层懒加载（且部分原生库在导入期即崩溃）。

    版本不用 ``importlib.metadata``：其冷导入约 100ms，``distributions()``
    单次扫描约 86ms，都远高于「扫一遍 dist-info 目录 + 读 4 个 METADATA」。
    """
    installed: dict[str, bool] = {}
    for _extra, package, distribution in _OPTIONAL_DEPS:
        try:
            installed[distribution] = importlib.util.find_spec(package) is not None
        except (ImportError, ValueError):
            installed[distribution] = False

    versions = _distribution_versions({name for name, present in installed.items() if present})
    return [
        {
            "extra": extra,
            "package": package,
            "installed": installed[distribution],
            "version": versions.get(distribution, ""),
        }
        for extra, package, distribution in _OPTIONAL_DEPS
    ]


def _normalize_distribution(name: str) -> str:
    """按 PEP 503 规范化分发包名（与 importlib.metadata 查找口径一致）。"""
    return re.sub(r"[-_.]+", "-", name).lower()


def _distribution_versions(names: set[str]) -> dict[str, str]:
    """一次扫描 ``sys.path`` 上的 ``*.dist-info``，读取指定分发包的版本。

    只读取命中的 4 个左右 ``METADATA`` 文件；同名分发包以 ``sys.path`` 中
    先出现者为准（与 ``importlib.metadata`` 的查找顺序一致）。
    """
    if not names:
        return {}
    wanted = {_normalize_distribution(name): name for name in names}
    versions: dict[str, str] = {}
    for entry in sys.path:
        if not wanted:
            break
        try:
            dist_infos = list(Path(entry or ".").glob("*.dist-info"))
        except OSError:
            continue
        for dist_info in dist_infos:
            stem = dist_info.name.removesuffix(".dist-info")
            dist_name, sep, _version = stem.rpartition("-")
            if not sep:
                continue
            original = wanted.get(_normalize_distribution(dist_name))
            if original is None:
                continue
            version = _read_metadata_version(dist_info / "METADATA")
            if version:
                versions[original] = version
                del wanted[_normalize_distribution(dist_name)]
    return versions


def _read_metadata_version(path: Path) -> str:
    """读取 ``METADATA`` 的 ``Version`` 头（只读首个头部块，不解析整个文件）。"""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.strip():
                    break
                if line.lower().startswith("version:"):
                    return line.partition(":")[2].strip()
    except OSError:
        return ""
    return ""
