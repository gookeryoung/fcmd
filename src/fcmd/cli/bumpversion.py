"""bumpversion - 版本号自动管理工具。

扫描项目内所有 ``__init__.py`` 和 ``pyproject.toml`` 文件，按 ``patch``/``minor``/``major``
递增版本号并统一写入，最后执行 git add + commit + tag。

采用"先读取基准、再统一写入"的两阶段策略：先读取所有文件的当前版本号取最大值作为基准，
计算新版本号后统一写入所有文件，避免文件间版本号不同步导致的跳号问题。

示例
----
    fcmd bumpversion                        # patch 递增（默认）
    fcmd bumpversion --part minor           # minor 递增
    fcmd bumpversion --part major --no-tag  # major 递增，不创建 tag
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import fcmd
from fcmd.models import BumpPart, IgnoreSpec, Version, parse_version, run_command, should_ignore

__all__ = [
    "BumpVersionType",
    "bump_file_version",
    "bump_project_version",
]

# ============================================================================
# 配置
# ============================================================================

BumpVersionType = Literal["patch", "minor", "major"]

# pyproject.toml 中 version = "x.y.z" 的正则（PEP 440 兼容）
_PYPROJECT_VERSION_PATTERN = re.compile(
    r'(?:^|\n)\s*version\s*=\s*["\']'
    r"(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+(?P<buildmetadata>[0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?"
    r'["\']',
    re.MULTILINE,
)

# __init__.py 中 __version__ = "x.y.z" 的正则
_INIT_VERSION_PATTERN = re.compile(
    r'(?:^|\n)\s*__version__\s*=\s*["\']'
    r"(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+(?P<buildmetadata>[0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?"
    r'["\']',
    re.MULTILINE,
)

# 扫描时忽略的目录（用 IgnoreSpec 统一描述，供 should_ignore 使用）
_IGNORE_SPEC: IgnoreSpec = IgnoreSpec.from_iterable(
    [".venv", "venv", ".git", "__pycache__", ".tox", "node_modules", "build", "dist", ".eggs"]
)


# ============================================================================
# 私有辅助函数
# ============================================================================


def _get_pattern_for_file(file_name: str) -> re.Pattern[str] | None:
    """根据文件类型获取对应的版本号正则表达式。

    Parameters
    ----------
    file_name:
        文件名（``pyproject.toml`` 或 ``__init__.py``）

    Returns
    -------
    re.Pattern[str] | None
        正则表达式；不支持的文件类型返回 None
    """
    if file_name == "pyproject.toml":
        return _PYPROJECT_VERSION_PATTERN
    if file_name == "__init__.py":
        return _INIT_VERSION_PATTERN
    return None


def _build_replacement_string(original_match: str, new_version: str, file_name: str) -> str:
    """构建替换字符串，保留原始格式（缩进/引号/key 名）。

    Parameters
    ----------
    original_match:
        正则匹配到的原始字符串
    new_version:
        新版本号
    file_name:
        文件名（用于决定 key 是 ``version`` 还是 ``__version__``）

    Returns
    -------
    str
        替换字符串
    """
    quote_char = '"' if '"' in original_match else "'"
    key = "__version__" if file_name == "__init__.py" else "version"
    prefix_match = re.match(rf"(\s*{key}\s*=\s*)[\"']", original_match)
    prefix = prefix_match.group(1) if prefix_match else f"{key} = "
    return f"{prefix}{quote_char}{new_version}{quote_char}"


def _read_version(file_path: Path) -> Version | None:
    """从文件中读取版本号，返回 Version 对象；未找到返回 None。

    Parameters
    ----------
    file_path:
        文件路径

    Returns
    -------
    Version | None
        版本号对象；读取失败或未匹配返回 None
    """
    pattern = _get_pattern_for_file(file_path.name)
    if pattern is None:
        return None

    try:
        content = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    return parse_version(content)


def _write_version_to_file(file_path: Path, new_version: str) -> bool:
    """把新版本号写入指定文件。

    Parameters
    ----------
    file_path:
        文件路径
    new_version:
        新版本号

    Returns
    -------
    bool
        成功返回 True；未匹配到版本号或写入失败返回 False
    """
    pattern = _get_pattern_for_file(file_path.name)
    if pattern is None:  # pragma: no cover - 调用方已保证 pattern 不为 None
        return False

    try:
        content = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"读取文件 {file_path} 失败: {e}")
        return False

    match = pattern.search(content)
    if not match:  # pragma: no cover - 调用方已通过 _read_version 验证
        return False

    replacement = _build_replacement_string(match.group(0), new_version, file_path.name)
    content = content.replace(match.group(0), replacement)

    try:
        file_path.write_text(content, encoding="utf-8")
    except OSError as e:
        print(f"更新文件 {file_path} 版本号时出错: {e}")
        return False

    return True


def _collect_version_files(root: Path) -> set[Path]:
    """收集项目内所有包含版本号的文件。

    Parameters
    ----------
    root:
        根目录

    Returns
    -------
    set[Path]
        文件路径集合（排除虚拟环境与缓存目录）
    """
    all_files: set[Path] = set()
    for pattern in ("__init__.py", "pyproject.toml"):
        for file in root.rglob(pattern):
            if not should_ignore(file, _IGNORE_SPEC):
                all_files.add(file)
    return all_files


# ============================================================================
# 公共函数
# ============================================================================


def bump_file_version(file_path: Path, part: BumpVersionType = "patch") -> str | None:
    """更新单个文件中的版本号。

    读取文件当前版本号，按 ``part`` 指定的部分递增，写回文件。

    Parameters
    ----------
    file_path:
        要更新的文件路径（``pyproject.toml`` 或 ``__init__.py``）
    part:
        版本部分：``patch``/``minor``/``major``

    Returns
    -------
    str | None
        更新后的新版本号；文件中未找到版本号或读取失败时返回 None
    """
    version = _read_version(file_path)
    if version is None:
        print(f"文件 {file_path} 中未找到版本号模式")
        return None

    new_version = version.bump(BumpPart(part))
    if not _write_version_to_file(file_path, str(new_version)):  # pragma: no cover - _read_version 已验证
        return None

    return str(new_version)


@fcmd.tool("bumpversion", help="版本号自动管理")
def bump_project_version(part: BumpVersionType = "patch", no_tag: bool = False) -> str | None:
    """批量同步项目所有版本号文件并提交。

    扫描当前目录下所有 ``__init__.py`` 和 ``pyproject.toml`` 文件
    (排除虚拟环境和缓存目录)，先读取每个文件的当前版本号取最大值作为基准，
    计算新版本号后统一写入所有文件，最后执行 git add (按文件名) + commit + tag。

    采用 "先读取基准、再统一写入" 的两阶段策略，即使某些文件版本号不同步，
    也能在一次 bump 后重新对齐，避免跳号。

    Parameters
    ----------
    part:
        版本部分：``patch``/``minor``/``major``
    no_tag:
        提交后不创建 git tag

    Returns
    -------
    str | None
        更新后的新版本号；未找到版本号文件时返回 None
    """
    try:
        bump_part = BumpPart(part)
    except ValueError:
        valid_parts = [p.value for p in BumpPart]
        print(f"无效的版本部分 {part!r}，必须是 {valid_parts}")
        return None

    all_files = _collect_version_files(Path.cwd())
    if not all_files:
        print("未找到包含版本号的文件")
        return None

    print(f"找到 {len(all_files)} 个文件需要更新版本号")
    cwd = Path.cwd()
    for file in sorted(all_files):
        print(f"  - {file.relative_to(cwd)}")

    # 阶段 1：读取所有文件版本号，取最大值作为基准
    versions: list[Version] = []
    for file in sorted(all_files):
        v = _read_version(file)
        if v is not None:
            versions.append(v)

    if not versions:
        print("未能从任何文件读取版本号")
        return None

    base_version = max(versions, key=lambda v: (v.major, v.minor, v.patch))
    new_version = base_version.bump(bump_part)
    print(f"基准版本: {base_version.major}.{base_version.minor}.{base_version.patch} -> 新版本: {new_version}")

    # 阶段 2：统一写入新版本号到所有文件
    for file in sorted(all_files):
        _write_version_to_file(file, str(new_version))

    # 阶段 3：git add (按文件名) + commit + tag
    relative_files = [str(file.relative_to(cwd)) for file in sorted(all_files)]
    run_command(["git", "add", *relative_files])
    run_command(["git", "commit", "-m", f"bump version to {new_version}"])

    if not no_tag:
        tag_name = f"v{new_version}"
        run_command(["git", "tag", "-a", tag_name, "-m", f"Release {tag_name}"])
        print(f"已创建标签: {tag_name}")

    return str(new_version)
