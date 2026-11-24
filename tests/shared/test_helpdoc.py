"""帮助文档数据层：分段排序、Markdown 解析、包内文档索引与链接。"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from fcmd.shared.helpdoc import (
    HELP_DIR,
    HelpDoc,
    HelpSection,
    load_help_doc,
    parse_markdown,
)

_ROOT = Path(__file__).resolve().parents[2]
_DOCS_INDEX = _ROOT / "docs" / "help" / "README.md"


def test_examples_epilog_matches_legacy_output() -> None:
    """只声明 examples 时，epilog 与旧版「示例:」逐字一致。"""
    doc = HelpDoc(tool="demo").with_examples(["fcmd demo .", "fcmd demo . --json"])
    assert doc.epilog() == "示例:\n  fcmd demo .\n  fcmd demo . --json"


def test_sections_are_ordered_by_kind_not_declaration_order() -> None:
    """分段按 SECTION_ORDER 排序，未知 kind 排在末尾并保持原相对顺序。"""
    doc = HelpDoc(
        tool="demo",
        sections=(
            HelpSection("notes", lines=["注意"]),
            HelpSection("env", lines=["FCMD_HOME"]),
            HelpSection("custom", "自定义", lines=["扩展"]),
        ),
    )
    assert [section.kind for section in doc.ordered_sections()] == ["env", "notes", "custom"]


def test_section_default_title_and_empty_kind() -> None:
    """标题缺省按 kind 回退；kind 为空时报错。"""
    assert HelpSection("exit_codes").title == "退出码"
    with pytest.raises(ValueError):
        HelpSection("  ")


def test_examples_merge_into_existing_section() -> None:
    """已声明 examples 分段时追加未出现过的行，不重复。"""
    doc = HelpDoc(tool="demo", sections=(HelpSection("examples", lines=["fcmd demo a"]),))
    merged = doc.with_examples(["fcmd demo a", "fcmd demo b"])
    assert merged.ordered_sections()[0].lines == ("fcmd demo a", "fcmd demo b")


def test_markdown_parse_sections_and_code_fence() -> None:
    """二级标题切分、代码块内的 # 不参与切分、三级标题留在正文。"""
    text = """# 演示工具

一句话说明。

## 用法

前置条件。

```text
## 不是标题
```

### 子步骤

1. 第一步

## 环境变量

FCMD_HOME  配置目录
"""
    doc = parse_markdown(text, tool="demo")
    assert doc.title == "演示工具"
    assert doc.summary == "一句话说明。"
    assert [(section.kind, section.title) for section in doc.sections] == [
        ("usage", "用法"),
        ("env", "环境变量"),
    ]
    assert "## 不是标题" in doc.sections[0].lines
    assert "### 子步骤" in doc.sections[0].lines


def test_to_mapping_is_json_serializable(tmp_path: Path) -> None:
    """schema 结构可被 json.dumps，doc 路径为字符串。"""
    path = tmp_path / "demo.md"
    path.write_text("# 演示\n\n## 示例\n\nfcmd demo .\n", encoding="utf-8")
    doc = HelpDoc.from_markdown(path, tool="demo")
    payload = json.loads(json.dumps(doc.to_mapping(), ensure_ascii=False))
    assert payload["tool"] == "demo"
    assert payload["doc"] == str(path)
    assert payload["sections"][0]["kind"] == "examples"


def test_packaged_help_docs_parse() -> None:
    """包内每个帮助文档都能解析出标题与至少一个分段。"""
    docs = sorted(HELP_DIR.glob("*.md"))
    assert docs, "src/fcmd/help 下没有帮助文档"
    for path in docs:
        doc = load_help_doc(path.stem)
        assert doc is not None
        assert doc.doc_path == path
        assert doc.display_title
        assert doc.sections, f"{path.name} 没有可渲染的分段"


def test_packaged_help_docs_are_indexed() -> None:
    """每个包内帮助文档都在 docs/help/README.md 有索引行。"""
    index = _DOCS_INDEX.read_text(encoding="utf-8")
    missing = [path.name for path in HELP_DIR.glob("*.md") if path.name not in index]
    assert not missing, f"docs/help/README.md 缺少索引: {missing}"


def _markdown_links(text: str) -> list[str]:
    """提取 Markdown 内联链接目标（跳过锚点、外链与行内代码里的括号）。"""
    without_code = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    without_code = re.sub(r"`[^`]*`", "", without_code)
    targets = re.findall(r"\]\(([^)\s]+)\)", without_code)
    return [target for target in targets if not target.startswith(("#", "http://", "https://", "mailto:"))]


def test_help_markdown_relative_links_resolve() -> None:
    """包内文档与 docs 索引的相对链接都能在仓库中找到。"""
    files = [*HELP_DIR.glob("*.md"), _DOCS_INDEX]
    broken: list[str] = []
    for path in files:
        for target in _markdown_links(path.read_text(encoding="utf-8")):
            resolved = (path.parent / target).resolve()
            if not resolved.exists():
                broken.append(f"{path.relative_to(_ROOT)} -> {target}")
    assert not broken, f"帮助文档相对链接失效: {broken}"
