"""packtool - Python 打包工具。

提供源码打包/依赖打包/wheel 构建/嵌入式 Python 安装/zip 包创建/清理子命令。

简化说明：不解析 ``pyproject.toml`` 获取项目名，直接用项目目录名作为包名，
避免引入 ``tomllib``/``tomli`` 依赖（rule-11 优先标准库 + 谨慎新增依赖）。

示例
----
    fcmd packtool src                          # 打包当前项目源码到 .pypack/
    fcmd packtool deps requests flask          # 打包依赖到 libs/
    fcmd packtool wheel                        # 构建 wheel 到 dist/
    fcmd packtool embed --version 3.11         # 安装嵌入式 Python 3.11
    fcmd packtool zip                          # 创建 package.zip
    fcmd packtool clean                        # 清理 .pypack 目录
"""

from __future__ import annotations

import platform
import shutil
import urllib.request
import zipfile
from pathlib import Path

import fcmd
from fcmd.models import IgnoreSpec, run_command, should_ignore, to_shutil_ignore

__all__ = [
    "clean_build_dir",
    "create_zip_package",
    "install_embed_python",
    "pack_dependencies",
    "pack_source",
    "pack_wheel",
]

# ============================================================================
# 配置
# ============================================================================

# 打包源码时忽略的规则（目录名 + glob 模式）
IGNORE_SPEC: IgnoreSpec = IgnoreSpec.from_iterable(
    [
        "__pycache__",
        "*.pyc",
        "*.pyo",
        ".git",
        ".venv",
        ".idea",
        ".vscode",
        "*.egg-info",
        "dist",
        "build",
        ".pytest_cache",
        ".tox",
        ".mypy_cache",
        ".ruff_cache",
        ".pyrefly_cache",
    ]
)

# 嵌入式 Python 版本映射（短版本 -> 完整版本）
_VERSION_MAP: dict[str, str] = {
    "3.8": "3.8.10",
    "3.9": "3.9.13",
    "3.10": "3.10.11",
    "3.11": "3.11.9",
    "3.12": "3.12.4",
}


# ============================================================================
# 私有辅助函数
# ============================================================================


def _normalize_arch() -> str:
    """获取当前平台架构标识（用于嵌入式 Python 下载 URL）。

    Returns
    -------
    str
        ``amd64`` 或 ``arm64``
    """
    arch = platform.machine().lower()
    if arch in ("x86_64", "amd64"):
        return "amd64"
    if arch in ("arm64", "aarch64"):
        return "arm64"
    return arch


# ============================================================================
# CLI 子命令
# ============================================================================


@fcmd.tool("packtool", subcommand="src", help="打包源码")
def pack_source(project_dir: Path = Path(), output_dir: Path = Path(".pypack")) -> None:
    """打包项目源码到指定目录。

    自动跳过 ``__pycache__``/``.git``/``.venv`` 等缓存与构建目录。
    项目名取自 ``project_dir`` 的目录名（简化版不解析 pyproject.toml）。

    Parameters
    ----------
    project_dir:
        项目目录（默认：当前目录）
    output_dir:
        输出目录（默认：``.pypack``）
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    project_name = project_dir.name

    source_dir = output_dir / "src" / project_name
    source_dir.mkdir(parents=True, exist_ok=True)

    src_subdir = project_dir / "src"
    if src_subdir.exists():
        shutil.copytree(
            src_subdir,
            source_dir / "src",
            ignore=to_shutil_ignore(IGNORE_SPEC),
            dirs_exist_ok=True,
        )
    else:
        for item in project_dir.iterdir():
            if should_ignore(Path(item.name), IGNORE_SPEC) or item.name.startswith("."):
                continue
            dst_item = source_dir / item.name
            if item.is_dir():
                shutil.copytree(
                    item,
                    dst_item,
                    ignore=to_shutil_ignore(IGNORE_SPEC),
                    dirs_exist_ok=True,
                )
            else:
                shutil.copy2(item, dst_item)

    print(f"源码打包完成: {source_dir}")


@fcmd.tool("packtool", subcommand="deps", help="打包依赖")
def pack_dependencies(packages: list[str], lib_dir: Path = Path("libs")) -> None:
    """打包项目依赖到指定目录（使用 ``pip install --target``）。

    Parameters
    ----------
    packages:
        依赖包名列表（至少一个）
    lib_dir:
        依赖库目录（默认：``libs``）
    """
    lib_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "pip",
        "install",
        "--target",
        str(lib_dir),
        "--no-compile",
        "--no-warn-script-location",
        *packages,
    ]

    run_command(cmd)
    print(f"依赖打包完成: {lib_dir}")


@fcmd.tool("packtool", subcommand="wheel", help="构建 wheel")
def pack_wheel(project_dir: Path = Path(), output_dir: Path = Path("dist")) -> None:
    """打包项目为 wheel 文件（使用 ``pip wheel``）。

    Parameters
    ----------
    project_dir:
        项目目录（默认：当前目录）
    output_dir:
        输出目录（默认：``dist``）
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "pip",
        "wheel",
        "--no-deps",
        "--wheel-dir",
        str(output_dir),
        str(project_dir),
    ]

    run_command(cmd)
    print(f"Wheel 打包完成: {output_dir}")


@fcmd.tool("packtool", subcommand="embed", help="安装嵌入式 Python")
def install_embed_python(version: str = "3.10", output_dir: Path = Path("python")) -> None:
    """安装嵌入式 Python 到指定目录。

    从 python.org 下载嵌入式 Python zip 包并解压。

    Parameters
    ----------
    version:
        Python 短版本（如 ``3.10``/``3.11``，默认 ``3.10``）
    output_dir:
        输出目录（默认：``python``）
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    arch = _normalize_arch()
    full_version = _VERSION_MAP.get(version, f"{version}.0")

    url = f"https://www.python.org/ftp/python/{full_version}/python-{full_version}-embed-{arch}.zip"
    cache_file = Path(".cache/pypack") / f"python-{full_version}-embed-{arch}.zip"
    cache_file.parent.mkdir(parents=True, exist_ok=True)

    if not cache_file.exists():
        print(f"正在下载嵌入式 Python {full_version}...")
        urllib.request.urlretrieve(url, cache_file)
        print(f"下载完成: {cache_file}")

    with zipfile.ZipFile(cache_file, "r") as zf:
        zf.extractall(output_dir)

    print(f"嵌入式 Python 安装完成: {output_dir}")


@fcmd.tool("packtool", subcommand="zip", help="创建 zip 包")
def create_zip_package(source_dir: Path = Path(), output_file: Path = Path("package.zip")) -> None:
    """创建 ZIP 打包文件。

    Parameters
    ----------
    source_dir:
        源目录（默认：当前目录）
    output_file:
        输出文件（默认：``package.zip``）
    """
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(output_file, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in source_dir.rglob("*"):
            if file.is_file():
                arcname = file.relative_to(source_dir)
                zf.write(file, arcname)

    print(f"ZIP 打包完成: {output_file}")


@fcmd.tool("packtool", subcommand="clean", help="清理构建目录")
def clean_build_dir(build_dir: Path = Path(".pypack")) -> None:
    """清理构建目录。

    Parameters
    ----------
    build_dir:
        构建目录（默认：``.pypack``）
    """
    if build_dir.exists():
        shutil.rmtree(build_dir)
        print(f"清理完成: {build_dir}")
    else:
        print(f"目录不存在: {build_dir}")
