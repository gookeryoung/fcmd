"""版本号模型。

提供 PEP 440 兼容的语义版本号解析与递增，替代 bumpversion 中分散的版本操作。

示例
----
    from fcmd.models import BumpPart, parse_version

    version = parse_version("1.2.3")
    new_version = version.bump(BumpPart.MINOR)
    print(new_version)  # 1.3.0
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

__all__ = ["BumpPart", "Version", "parse_version"]


class BumpPart(Enum):
    """版本号递增部分。"""

    PATCH = "patch"
    MINOR = "minor"
    MAJOR = "major"


# PEP 440 版本号正则（核心三段 + 预发布 + 构建元数据）
_VERSION_PATTERN = re.compile(
    r"(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+(?P<buildmetadata>[0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?"
)


@dataclass(frozen=True)
class Version:
    """语义版本号（不可变值对象，PEP 440 兼容）。

    Attributes
    ----------
    major:
        主版本号
    minor:
        次版本号
    patch:
        修订号
    prerelease:
        预发布标记（如 ``alpha.1``），无则为空字符串
    buildmetadata:
        构建元数据（如 ``build.1``），无则为空字符串
    """

    major: int
    minor: int
    patch: int
    prerelease: str = ""
    buildmetadata: str = ""

    def bump(self, part: BumpPart = BumpPart.PATCH) -> Version:
        """递增版本号，返回新 Version 实例。

        递增后预发布与构建元数据清零。

        Parameters
        ----------
        part:
            递增部分（默认 PATCH）

        Returns
        -------
        Version
            新版本号
        """
        if part is BumpPart.MAJOR:
            return Version(major=self.major + 1, minor=0, patch=0)
        if part is BumpPart.MINOR:
            return Version(major=self.major, minor=self.minor + 1, patch=0)
        return Version(major=self.major, minor=self.minor, patch=self.patch + 1)

    def __str__(self) -> str:
        """版本号字符串表示。"""
        version = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            version += f"-{self.prerelease}"
        if self.buildmetadata:
            version += f"+{self.buildmetadata}"
        return version


def parse_version(text: str) -> Version | None:
    """从字符串解析版本号。

    在文本中搜索首个符合 PEP 440 的版本号字符串。

    Parameters
    ----------
    text:
        包含版本号的文本（如 ``version = "1.2.3"``）

    Returns
    -------
    Version | None
        版本号对象；不匹配时返回 None
    """
    match = _VERSION_PATTERN.search(text)
    if not match:
        return None
    return Version(
        major=int(match.group("major")),
        minor=int(match.group("minor")),
        patch=int(match.group("patch")),
        prerelease=match.group("prerelease") or "",
        buildmetadata=match.group("buildmetadata") or "",
    )
