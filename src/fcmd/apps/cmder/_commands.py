"""cmderw 用户命令的纯逻辑：命令模型、占位符展开与持久化容错。

下划线前缀使其不参与 :mod:`fcmd` 的工具发现；窗口层 ``app.py`` 只做上下文
收集、入口重建与执行调度。口径见 ADR 0010。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, cast

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "COMMAND_LIMIT",
    "PLACEHOLDERS",
    "CommandContext",
    "PlaceholderError",
    "UserCommand",
    "expand_placeholders",
    "normalize_user_commands",
    "user_commands_to_json",
]

COMMAND_LIMIT: Final[int] = 50
"""用户命令条数上限，超出部分在读入与新增时都丢弃。"""

PLACEHOLDERS: Final[Mapping[str, str]] = {
    "P": "活动面板当前目录",
    "T": "对面面板当前目录",
    "N": "活动面板首个选中项名称",
    "S": "全部选中项名称（逐项加引号，空格分隔）",
    "L": "全部选中项完整路径（逐项加引号，空格分隔）",
    "%": "字面百分号",
}
"""占位符键（不带 ``%``）到说明的映射，供界面提示与文档复用。"""


class PlaceholderError(ValueError):
    """命令模板中存在非法或未闭合的占位符。"""

    def __init__(self, token: str, position: int) -> None:
        self.token = token
        self.position = position
        super().__init__(f"占位符 {token} 不合法（位置 {position}）；可用：%P %T %N %S %L %%")


@dataclass(frozen=True)
class CommandContext:
    """展开占位符所需的运行时上下文（全部为已解析的字符串）。"""

    active_dir: str = ""
    other_dir: str = ""
    selected: tuple[str, ...] = ()
    selected_paths: tuple[str, ...] = ()


def _quote(text: str) -> str:
    """把单个名称 / 路径包成双引号并转义内部双引号。"""
    return f'"{text.replace(chr(34), chr(92) + chr(34))}"'


def _join_quoted(items: tuple[str, ...]) -> str:
    return " ".join(_quote(item) for item in items)


def expand_placeholders(template: str, context: CommandContext) -> str:
    """展开命令模板中的占位符；未知占位符与孤立 ``%`` 抛 :class:`PlaceholderError`。

    参数:
        template: 命令或参数模板。
        context: 活动 / 对面目录与选中项。

    返回:
        展开后的命令文本；未命中占位符的部分原样保留。
    """
    out: list[str] = []
    index = 0
    size = len(template)
    while index < size:
        char = template[index]
        if char != "%":
            out.append(char)
            index += 1
            continue
        if index + 1 >= size:
            raise PlaceholderError("%", index)
        key = template[index + 1]
        if key == "%":
            out.append("%")
        elif key == "P":
            out.append(context.active_dir)
        elif key == "T":
            out.append(context.other_dir)
        elif key == "N":
            out.append(context.selected[0] if context.selected else "")
        elif key == "S":
            out.append(_join_quoted(context.selected))
        elif key == "L":
            out.append(_join_quoted(context.selected_paths))
        else:
            raise PlaceholderError(f"%{key}", index)
        index += 2
    return "".join(out)


@dataclass(frozen=True)
class UserCommand:
    """一条用户命令：命令模板、参数模板与三个入口开关。"""

    name: str
    command: str
    parameters: str = ""
    in_menu: bool = True
    in_toolbar: bool = True
    in_context: bool = True

    @property
    def full_command(self) -> str:
        """命令模板与参数模板拼接后的待展开文本（空参数不产生多余空格）。"""
        return " ".join(part for part in (self.command.strip(), self.parameters.strip()) if part)

    def to_dict(self) -> dict[str, str | bool]:
        """序列化为设置文件中的字典。"""
        return {
            "name": self.name,
            "command": self.command,
            "parameters": self.parameters,
            "in_menu": self.in_menu,
            "in_toolbar": self.in_toolbar,
            "in_context": self.in_context,
        }


def _flag(raw: dict[str, object], key: str) -> bool:
    value = raw.get(key)
    return value if isinstance(value, bool) else True


def normalize_user_commands(raw: object, *, limit: int = COMMAND_LIMIT) -> list[UserCommand]:
    """解析用户命令列表：丢弃非法条目与重名项，按原顺序保留至 ``limit`` 条。"""
    if not isinstance(raw, list):
        return []
    commands: list[UserCommand] = []
    seen: set[str] = set()
    for item in raw:
        if len(commands) >= limit:
            break
        if not isinstance(item, dict):
            continue
        data = cast(dict[str, object], item)
        name = data.get("name")
        command = data.get("command")
        if not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(command, str) or not command.strip():
            continue
        clean_name = name.strip()
        if clean_name in seen:
            continue
        parameters = data.get("parameters")
        commands.append(
            UserCommand(
                name=clean_name,
                command=command.strip(),
                parameters=parameters.strip() if isinstance(parameters, str) else "",
                in_menu=_flag(data, "in_menu"),
                in_toolbar=_flag(data, "in_toolbar"),
                in_context=_flag(data, "in_context"),
            )
        )
        seen.add(clean_name)
    return commands


def user_commands_to_json(commands: list[UserCommand]) -> list[dict[str, str | bool]]:
    """把用户命令列表序列化为设置 JSON 结构。"""
    return [command.to_dict() for command in commands]
