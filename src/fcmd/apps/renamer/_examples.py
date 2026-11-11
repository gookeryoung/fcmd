"""renamerw 使用帮助与典型规则示例（纯数据，供窗口与测试共用）。

示例规则同时以 JSON 文件放在本包的 ``help/examples/`` 下，可直接通过窗口的
「帮助 → 载入规则…」导入；``tests/apps/renamer/test_renamerw.py`` 会校验两份
数据一致，避免文档与内置示例漂移。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from fcmd.apps.renamer._core import (
    ANCHOR_END,
    FIELD_EXT,
    RenameRule,
)

__all__ = ["EXAMPLES", "HELP_TEXT", "Example", "example_to_mapping"]

HELP_TEXT = """\
【使用步骤】
1. 在「目标」区添加文件或目录；目录可勾选「递归子目录」「包含目录」。
2. 在「规则」区填写表单，点「添加规则」；规则按列表顺序依次执行。
3. 点「刷新预览」，确认「旧名 → 新名」与状态；可执行的行才会真正改名。
4. 点「执行改名」并确认；本次运行可用「撤销上次」回退。
5. 规则可用「帮助 → 保存规则…」导出为 JSON，下次「载入规则…」复用。

【规则类型】
replace   正则替换，替换模板用 \\1 或 \\g<name> 引用捕获组。
reorder   按捕获组重排，模板用 {0}/{1}/{name}；用于调整片段出现的位置。
move      把匹配文本整体搬到锚点（开头/结尾/锚点前/锚点后/指定位置）。
insert    在锚点插入字面文本。
case      大小写转换：保持 / 小写 / 大写 / 每词首字母 / 仅首字母。
sequence  按批内排序插入计数器，text 为计数器前的分隔符。

【常用字段】
作用域    名称（默认）或扩展名；目录始终按名称处理。
occurrence 0 表示全部匹配；N 表示只处理第 N 个匹配（越界不报错）。
锚点      开头 / 结尾 / 锚点前 / 锚点后 / 指定位置；锚点前后需填写锚点文本。
计数器    起始、步长、位数共同决定编号；位数不足时左侧补零。
排序      「计数器排序」可选名称 / 修改时间 / 创建时间；按名称是字符串排序。

【预览状态】
可执行    名称会变化，且目标未被占用、批内不重名。
未变化    规则执行后名称不变。
非法      生成的名称为空、含路径分隔符或 < > : " | ? * 等字符。
冲突      批内重名或目标文件已存在。

【提示】
- 左侧示例列表双击或点「载入选中示例」，可把典型规则写入规则表。
- 执行前务必先刷新预览；撤销只覆盖本次运行，不会持久化历史。
- reorder 不做数值补零；季号等需要固定位数时，请先把源名称统一为两位。
"""


@dataclass(frozen=True)
class Example:
    """一条典型规则示例：名称、说明、规则集与演示用「旧名 → 新名」。"""

    slug: str
    name: str
    summary: str
    rules: tuple[RenameRule, ...]
    demo: tuple[tuple[str, str], ...]


EXAMPLES: tuple[Example, ...] = (
    Example(
        slug="photo-sequence",
        name="照片 / 扫描件补零编号",
        summary="IMG_1.jpg → 照片_001.jpg：去掉原编号后按排序追加三位计数器",
        rules=(
            RenameRule(kind="replace", pattern="^IMG_", replacement="照片_"),
            RenameRule(kind="replace", pattern=r"_\d+$", replacement=""),
            RenameRule(kind="sequence", anchor=ANCHOR_END, text="_", start=1, step=1, digits=3),
        ),
        demo=(("IMG_1.jpg", "照片_001.jpg"), ("IMG_2.jpg", "照片_002.jpg")),
    ),
    Example(
        slug="tv-episode",
        name="剧集规范化（SxxExx）",
        summary="Show.01x02.1080p.mkv → Show - S01E02.mkv：重排捕获组并丢弃发布标记",
        rules=(
            RenameRule(
                kind="reorder",
                pattern=r"^(?P<show>.+?)\.(?P<season>\d{2})x(?P<episode>\d{2})\..*$",
                replacement="{show} - S{season}E{episode}",
            ),
        ),
        demo=(("Show.01x02.1080p.mkv", "Show - S01E02.mkv"),),
    ),
    Example(
        slug="date-prefix",
        name="日期片段搬到前面",
        summary="会议纪要-20240501.docx → 20240501_会议纪要.docx：交换标题与 8 位日期",
        rules=(
            RenameRule(
                kind="reorder",
                pattern=r"^(?P<title>.+?)[-_](?P<date>\d{8})$",
                replacement="{date}_{title}",
            ),
        ),
        demo=(("会议纪要-20240501.docx", "20240501_会议纪要.docx"),),
    ),
    Example(
        slug="cleanup-case",
        name="清理噪声 + 大小写统一",
        summary="my  file (1).TXT → My_File.txt：去括号内容、空格转下划线、名称标题式、扩展名小写",
        rules=(
            RenameRule(kind="replace", pattern=r"\s*[(\[][^)\]]*[)\]]", replacement=""),
            RenameRule(kind="replace", pattern=r"\s+", replacement="_"),
            RenameRule(kind="case", mode="title"),
            RenameRule(kind="case", field=FIELD_EXT, mode="lower"),
        ),
        demo=(("my  file (1).TXT", "My_File.txt"),),
    ),
)


def example_to_mapping(example: Example) -> list[dict[str, Any]]:
    """把示例规则转成 JSON 字典列表：始终保留 kind，其余字段省略默认值。"""
    defaults = RenameRule()
    rows: list[dict[str, Any]] = []
    for rule in example.rules:
        row: dict[str, Any] = {"kind": rule.kind}
        row.update(
            {key: value for key, value in asdict(rule).items() if key != "kind" and value != getattr(defaults, key)}
        )
        rows.append(row)
    return rows
