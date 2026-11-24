"""帮助文档通用数据层：分段、Markdown 解析与 CLI epilog 渲染。

本模块只依赖标准库（``shared`` 层约束），因此 CLI 冷启动路径不会因为帮助
功能导入 rich / PySide；GUI 帮助窗口与 ``--help`` / ``fcmd info`` 读同一份
结构，避免「窗口里一套说明、命令行里另一套」的漂移。

约定的分段顺序与标题见 :data:`SECTION_ORDER` / :data:`DEFAULT_TITLES`，
与 GNU man page 的常用小节（EXAMPLES / ENVIRONMENT / EXIT STATUS / FILES /
SEE ALSO）对齐；未知 ``kind`` 的分段排在末尾，保持调用方给定顺序。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_TITLES",
    "HELP_DIR",
    "SECTION_ORDER",
    "HelpDoc",
    "HelpSection",
    "help_headings",
    "help_outline",
    "load_help_doc",
    "parse_markdown",
]

_HEADING_RE = re.compile(r"^(#{2,3})\s+(.+?)\s*$")

#: 包内帮助文档目录（``src/fcmd/help/``）；随 wheel 一起安装。
HELP_DIR = Path(__file__).resolve().parents[1] / "help"

#: 已知分段顺序（人读顺序）；未列出的 kind 追加在末尾。
SECTION_ORDER: tuple[str, ...] = ("usage", "examples", "env", "exit_codes", "files", "see_also", "notes")

#: kind → 默认中文标题；调用方可用 ``HelpSection.title`` 覆盖。
DEFAULT_TITLES: dict[str, str] = {
    "usage": "用法",
    "examples": "示例",
    "env": "环境变量",
    "exit_codes": "退出码",
    "files": "文件",
    "see_also": "相关",
    "notes": "说明",
}

#: 从 Markdown 二级标题推断 kind 的关键字（中文 / 英文都覆盖）。
_KIND_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("usage", ("用法", "快速开始", "使用步骤", "usage", "quickstart", "getting started")),
    ("examples", ("示例", "例子", "example")),
    ("env", ("环境变量", "environment", "env var")),
    ("exit_codes", ("退出码", "exit code", "exit status")),
    ("files", ("文件", "files", "paths")),
    ("see_also", ("相关", "另见", "see also", "links", "参考")),
    ("notes", ("说明", "注意", "提示", "notes", "faq", "常见问题")),
)


@dataclass(frozen=True)
class HelpSection:
    """帮助文档的一段：``kind`` 决定排序，``title`` 是展示标题。

    参数
    ----
    kind:
        分段类型（``examples`` / ``env`` / ``exit_codes`` / ...）；未知值按原顺序
        排在已知分段之后。
    title:
        展示标题；为空时按 :data:`DEFAULT_TITLES` 回退，未知 kind 回退到 kind 本身。
    lines:
        段内文本行；原样输出，不加列表符号（示例命令可直接复制）。
    """

    kind: str
    title: str = ""
    lines: Sequence[str] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        kind = self.kind.strip()
        if not kind:
            raise ValueError("HelpSection.kind 不能为空")
        title = self.title.strip() or DEFAULT_TITLES.get(kind, kind)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "lines", tuple(str(line) for line in self.lines))

    def to_mapping(self) -> dict[str, Any]:
        """转为 JSON 可序列化结构（``fcmd info --json`` 使用）。"""
        return {"kind": self.kind, "title": self.title, "lines": list(self.lines)}


@dataclass(frozen=True)
class HelpDoc:
    """一份工具帮助：摘要 + 结构化分段 + 可选 Markdown 文档路径。

    ``--help`` 只用 :meth:`epilog`；``fcmd info --json`` 用 :meth:`to_mapping`；
    GUI 帮助窗口优先渲染 ``doc_path`` 指向的 Markdown（同一份内容也随包发布）。
    """

    tool: str
    title: str = ""
    summary: str = ""
    sections: tuple[HelpSection, ...] = field(default_factory=tuple)
    doc_path: Path | None = None

    def __post_init__(self) -> None:
        tool = self.tool.strip()
        if not tool:
            raise ValueError("HelpDoc.tool 不能为空")
        object.__setattr__(self, "tool", tool)
        object.__setattr__(self, "sections", tuple(self.sections))
        if self.doc_path is not None:
            object.__setattr__(self, "doc_path", Path(self.doc_path))

    @property
    def display_title(self) -> str:
        """展示标题：未显式给出时用工具名。"""
        return self.title.strip() or self.tool

    def ordered_sections(self) -> tuple[HelpSection, ...]:
        """按 :data:`SECTION_ORDER` 稳定排序；未知 kind 保持原相对顺序排在末尾。"""

        def rank(section: HelpSection) -> int:
            return SECTION_ORDER.index(section.kind) if section.kind in SECTION_ORDER else len(SECTION_ORDER)

        return tuple(sorted(self.sections, key=rank))

    def with_examples(self, examples: Sequence[str]) -> HelpDoc:
        """合并 ``ToolSpec.examples``；已有 ``examples`` 分段时追加未出现过的行。"""
        if not examples:
            return self
        extra = tuple(dict.fromkeys(str(example) for example in examples))
        for index, section in enumerate(self.sections):
            if section.kind == "examples":
                merged = tuple(section.lines) + tuple(line for line in extra if line not in section.lines)
                updated = (*self.sections[:index], replace(section, lines=merged), *self.sections[index + 1 :])
                return replace(self, sections=updated)
        return replace(self, sections=(*self.sections, HelpSection("examples", lines=extra)))

    def epilog(self) -> str:
        """渲染 argparse 的 ``epilog``（与旧版仅示例时的输出逐字兼容）。"""
        blocks: list[str] = []
        for section in self.ordered_sections():
            lines = [line for line in section.lines if line.strip()]
            if not lines:
                continue
            body = "\n".join(f"  {line}" for line in lines)
            blocks.append(f"{section.title}:\n{body}")
        return "\n\n".join(blocks)

    def to_mapping(self) -> dict[str, Any]:
        """转为 JSON 可序列化结构（供 ``fcmd info --json`` 使用）。"""
        return {
            "tool": self.tool,
            "title": self.display_title,
            "summary": self.summary,
            "sections": [section.to_mapping() for section in self.ordered_sections()],
            "doc": str(self.doc_path) if self.doc_path is not None else None,
        }

    def to_markdown(self) -> str:
        """按分段生成 Markdown（没有包内文档时给 GUI 的兜底正文）。"""
        blocks: list[str] = [f"# {self.display_title}"]
        if self.summary.strip():
            blocks.append(self.summary.strip())
        for section in self.ordered_sections():
            lines = [line for line in section.lines if line.strip()]
            if not lines:
                continue
            blocks.append(f"## {section.title}\n\n" + "\n\n".join(lines))
        return "\n\n".join(blocks) + "\n"

    def read_markdown(self) -> str:
        """读取包内 Markdown；文件缺失 / 不可读时回退到 :meth:`to_markdown`。"""
        if self.doc_path is None:
            return self.to_markdown()
        try:
            return self.doc_path.read_text(encoding="utf-8")
        except OSError:
            return self.to_markdown()

    @classmethod
    def from_markdown(cls, path: str | Path, *, tool: str = "", title: str = "") -> HelpDoc:
        """从 Markdown 文件构造帮助；``tool`` 缺省取文件名（去扩展名）。"""
        resolved = Path(path)
        text = resolved.read_text(encoding="utf-8")
        doc = parse_markdown(text, tool=tool or resolved.stem, title=title)
        return replace(doc, doc_path=resolved)


def help_outline(markdown: str) -> tuple[tuple[int, str], ...]:
    """提取二级 / 三级标题（跳过代码块），返回 ``(level, title)``。

    纯标准库实现，CLI 与 GUI 共用；目录树、正文定位都不必再各自解析一遍。
    """
    headings: list[tuple[int, str]] = []
    in_fence = False
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _HEADING_RE.match(line)
        if match is not None:
            headings.append((len(match.group(1)), match.group(2).strip()))
    return tuple(headings)


def help_headings(markdown: str) -> tuple[str, ...]:
    """提取二级 / 三级标题文本，用于正文定位。"""
    return tuple(title for _level, title in help_outline(markdown))


def _kind_for(title: str) -> str:
    """按标题推断分段 kind；未命中时归入 ``notes``。"""
    lowered = title.strip().lower()
    for kind, keywords in _KIND_KEYWORDS:
        if any(keyword.lower() in lowered for keyword in keywords):
            return kind
    return "notes"


def _trim(lines: Iterable[str]) -> tuple[str, ...]:
    """去掉段首 / 段尾空行，保留段内空行。"""
    materialized = list(lines)
    while materialized and not materialized[0].strip():
        materialized.pop(0)
    while materialized and not materialized[-1].strip():
        materialized.pop()
    return tuple(materialized)


def parse_markdown(text: str, *, tool: str, title: str = "") -> HelpDoc:
    """把 Markdown 解析成 :class:`HelpDoc`。

    只把一级标题当文档标题、二级标题当分段；三级及以下标题留在段正文里，
    由 GUI 的正文渲染负责。代码块里的 ``#`` 不参与切分。
    """
    lines = text.splitlines()
    doc_title = title.strip()
    summary: list[str] = []
    sections: list[HelpSection] = []
    current_title: str | None = None
    current_kind = "notes"
    buffer: list[str] = []
    in_fence = False

    def flush() -> None:
        if current_title is not None:
            sections.append(HelpSection(current_kind, current_title, _trim(buffer)))

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
        elif not in_fence:
            heading = re.match(r"^(#{1,2})\s+(.+?)\s*$", line)
            if heading is not None:
                level = len(heading.group(1))
                heading_text = heading.group(2).strip()
                if level == 1:
                    if not doc_title:
                        doc_title = heading_text
                    continue
                flush()
                current_title = heading_text
                current_kind = _kind_for(heading_text)
                buffer = []
                continue
        if current_title is None:
            summary.append(line)
        else:
            buffer.append(line)
    flush()
    return HelpDoc(
        tool=tool,
        title=doc_title,
        summary="\n".join(_trim(summary)),
        sections=tuple(sections),
    )


def load_help_doc(tool: str, *, title: str = "") -> HelpDoc | None:
    """读取 ``src/fcmd/help/<tool>.md``；不存在时返回 ``None``。

    用文件名而不是工具注册表定位，CLI / GUI 都不需要先触发工具发现。
    """
    path = HELP_DIR / f"{tool}.md"
    if not path.is_file():
        return None
    return HelpDoc.from_markdown(path, tool=tool, title=title)
