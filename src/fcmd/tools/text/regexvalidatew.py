"""regexvalidatew - 正则校验器（Qt 前端入口与设置）。

交互参考 ``bitool_gui.regexvalidate``：输入正则与测试文本后实时校验，展示
匹配数量、匹配位置、捕获组、全匹配与 findall 结果，并提供正则语法帮助
对话框。支持忽略大小写 / 多行 / DOTALL 标志与历史记录。

窗口实现在 :mod:`fcmd.tools.text._regexvalidatew_qt`，本模块保留示例、历史与
设置持久化；设置（当前输入、标志、历史记录与窗口位置）保存在
``${FCMD_HOME:-~/.fcmd}/ui/regexvalidate.json``，下次启动自动恢复。

示例
----
    fcmd regexvalidatew   # 打开正则校验器窗口
    regexvalidatew        # 全局命令等价入口
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import fcmd
from fcmd.gui import config as _guiconfig
from fcmd.gui import geometry as _guiwindow

__all__ = [
    "MAX_HISTORY_ITEMS",
    "REGEX_EXAMPLES",
    "RegexExample",
    "RegexHistoryEntry",
    "RegexValidateSettings",
    "config_path",
    "example_label",
    "history_label",
    "load_settings",
    "push_history",
    "resolve_geometry",
    "save_settings",
    "valid_geometry",
]

_DESCRIPTION = (
    "正则校验器窗口版（Qt）：实时校验正则与文本，展示匹配位置/捕获组/全匹配/findall，"
    "内置 20 个典型示例，支持 ignorecase/multiline/dotall 标志、历史记录与语法帮助；"
    "设置持久化到 ~/.fcmd/ui/regexvalidate.json"
)

DEFAULT_GEOMETRY = "960x680+240+140"
MIN_WIDTH = 800
MIN_HEIGHT = 540
MAX_HISTORY_ITEMS = 20
HISTORY_DELAY_MS = 1000


@dataclass(frozen=True)
class RegexExample:
    """典型示例：一键填入正则、测试文本与标志。"""

    title: str
    pattern: str
    text: str
    ignorecase: bool = False
    multiline: bool = False
    dotall: bool = False


#: 20 个典型示例，覆盖校验、提取、分组、反向引用与标志位
REGEX_EXAMPLES: tuple[RegexExample, ...] = (
    RegexExample("手机号", r"^1[3-9]\d{9}$", "13812345678"),
    RegexExample("邮箱", r"^[\w.+-]+@[\w-]+(?:\.[\w-]+)+$", "user.name+tag@example.com"),
    RegexExample("IPv4 地址", r"^(?:\d{1,3}\.){3}\d{1,3}$", "192.168.1.1"),
    RegexExample("URL", r"https?://[^\s]+", "访问 https://example.com/path?q=1 查看"),
    RegexExample(
        "日期（命名分组）",
        r"^(?P<year>\d{4})-(?P<month>0[1-9]|1[0-2])-(?P<day>0[1-9]|[12]\d|3[01])$",
        "2026-11-09",
    ),
    RegexExample("时间 HH:MM:SS", r"^(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d$", "23:59:59"),
    RegexExample("身份证号", r"^\d{17}[\dXx]$", "110101199003071234"),
    RegexExample("中文字符", r"[\u4e00-\u9fa5]+", "正则表达式校验"),
    RegexExample("十六进制颜色", r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$", "#4A90E2"),
    RegexExample("数字（整数/小数）", r"-?\d+(?:\.\d+)?", "价格 -12.5 元"),
    RegexExample("千分位数字", r"\d{1,3}(?:,\d{3})+", "1,234,567"),
    RegexExample("用户名", r"^[A-Za-z][A-Za-z0-9_]{3,15}$", "regex_fan"),
    RegexExample("强密码", r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d).{8,}$", "Abcd1234"),
    RegexExample(
        "域名",
        r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$",
        "www.example.com",
    ),
    RegexExample(
        "Windows 路径",
        r'^[A-Za-z]:\\(?:[^\\/:*?"<>|\r\n]+\\)*[^\\/:*?"<>|\r\n]+$',
        r"C:\Users\demo\file.txt",
    ),
    RegexExample("重复单词", r"\b(\w+)\s+\1\b", "this is is a test", ignorecase=True),
    RegexExample("HTML 标签", r"<(/?)(\w+)[^>]*>", '<div class="x">hello</div>'),
    RegexExample("Markdown 链接", r"\[([^\]]+)\]\(([^)]+)\)", "[Trae](https://trae.ai)"),
    RegexExample(
        "日志级别",
        r"\b(DEBUG|INFO|WARNING|ERROR|CRITICAL)\b",
        "2026-11-09 ERROR 请求失败",
        ignorecase=True,
    ),
    RegexExample("行尾空白", r"[ \t]+$", "line with spaces   \nsecond line", multiline=True),
)


def example_label(index: int, example: RegexExample) -> str:
    """生成示例下拉项文本：序号 + 标题。"""
    return f"{index + 1}. {example.title}"


REGEX_HELP_TEXT = """常用元字符
  .        任意字符（默认不含换行）
  \\d \\D    数字 / 非数字
  \\w \\W    单词字符 / 非单词字符
  \\s \\S    空白 / 非空白
  ^ $      行首 / 行尾
  [...]    字符集合，[^...] 取反
  |        或

量词
  * + ?        零次或多次 / 一次或多次 / 零次或一次
  {n} {n,m}    恰好 n 次 / n 到 m 次
  *? +? ??     非贪婪版本

分组
  (...)          捕获组
  (?:...)        非捕获组
  (?P<name>...)  命名组
  (?P=name)      按名称反向引用

标志
  ignorecase    忽略大小写（re.IGNORECASE）
  multiline     多行模式（re.MULTILINE，^ / $ 匹配每行）
  dotall        DOTALL 模式（re.DOTALL，. 匹配换行）

提示
  校验采用搜索语义（search）：文本中任一处匹配即视为匹配；
  「全匹配」要求整段文本完全匹配。
"""


# ---------------------------------------------------------------------- #
# 配置持久化
# ---------------------------------------------------------------------- #
def config_path() -> Path:
    """返回配置文件路径：``${FCMD_HOME:-~/.fcmd}/ui/regexvalidate.json``。"""
    return _guiconfig.config_path("regexvalidate")


@dataclass(frozen=True)
class RegexHistoryEntry:
    """一条历史记录：正则、测试文本与三个标志开关。"""

    pattern: str
    test_string: str = ""
    ignorecase: bool = False
    multiline: bool = False
    dotall: bool = False

    def to_dict(self) -> dict[str, str | bool]:
        """转为可 JSON 序列化的字典。"""
        return {
            "pattern": self.pattern,
            "test_string": self.test_string,
            "ignorecase": self.ignorecase,
            "multiline": self.multiline,
            "dotall": self.dotall,
        }

    @classmethod
    def from_dict(cls, raw: object) -> RegexHistoryEntry | None:
        """从 JSON 对象还原；缺少有效 ``pattern`` 时返回 ``None``。"""
        if not isinstance(raw, dict):
            return None
        pattern = raw.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            return None
        test_string = raw.get("test_string")
        return cls(
            pattern=pattern,
            test_string=test_string if isinstance(test_string, str) else "",
            ignorecase=raw.get("ignorecase") is True,
            multiline=raw.get("multiline") is True,
            dotall=raw.get("dotall") is True,
        )


@dataclass
class RegexValidateSettings:
    """正则校验器的持久化设置。"""

    pattern: str = ""
    test_string: str = ""
    ignorecase: bool = False
    multiline: bool = False
    dotall: bool = False
    history: list[RegexHistoryEntry] = field(default_factory=list)
    geometry: str | None = None
    splitter: tuple[int, int] | None = None


valid_geometry = _guiconfig.valid_geometry


def resolve_geometry(saved: str | None, screen_width: int, screen_height: int) -> str:
    """优先恢复记忆的位置，越界/异常时回落到默认 geometry（按屏幕夹紧）。"""
    return _guiwindow.resolve_geometry(
        saved,
        screen_width,
        screen_height,
        default=DEFAULT_GEOMETRY,
        min_width=MIN_WIDTH,
        min_height=MIN_HEIGHT,
    )


def history_label(index: int, entry: RegexHistoryEntry) -> str:
    """生成历史下拉项文本：序号 + 截断后的正则。"""
    pattern = entry.pattern
    if len(pattern) > 50:
        pattern = pattern[:50] + "…"
    return f"{index + 1}. {pattern}"


def push_history(
    history: list[RegexHistoryEntry],
    entry: RegexHistoryEntry,
    limit: int = MAX_HISTORY_ITEMS,
) -> list[RegexHistoryEntry]:
    """把条目放到历史最前：完全相同的条目去重，超过上限截断。

    正则为空的条目不入历史，原样返回副本。
    """
    if not entry.pattern:
        return list(history)
    return [entry, *(item for item in history if item != entry)][:limit]


def load_settings(path: Path | None = None) -> RegexValidateSettings:
    """读取设置文件；文件缺失、JSON 损坏或字段类型不符时逐项回退默认值。"""
    raw = _guiconfig.read_settings(path or config_path())
    if raw is None:
        return RegexValidateSettings()

    settings = RegexValidateSettings()
    pattern = raw.get("pattern")
    test_string = raw.get("test_string")
    geometry = raw.get("geometry")
    splitter = raw.get("splitter")
    if isinstance(pattern, str):
        settings.pattern = pattern
    if isinstance(test_string, str):
        settings.test_string = test_string
    if isinstance(geometry, str) and valid_geometry(geometry):
        settings.geometry = geometry
    if isinstance(splitter, list) and len(splitter) == 2 and all(isinstance(item, int) for item in splitter):
        settings.splitter = (splitter[0], splitter[1])
    settings.ignorecase = raw.get("ignorecase") is True
    settings.multiline = raw.get("multiline") is True
    settings.dotall = raw.get("dotall") is True

    raw_history = raw.get("history")
    if isinstance(raw_history, list):
        for item in raw_history:
            entry = RegexHistoryEntry.from_dict(item)
            if entry is not None:
                settings.history.append(entry)
        settings.history = settings.history[:MAX_HISTORY_ITEMS]
    return settings


def save_settings(settings: RegexValidateSettings, path: Path | None = None) -> None:
    """保存设置；父目录自动创建，写入失败时静默忽略（不影响窗口关闭）。"""
    payload = {
        "pattern": settings.pattern,
        "test_string": settings.test_string,
        "ignorecase": settings.ignorecase,
        "multiline": settings.multiline,
        "dotall": settings.dotall,
        "history": [entry.to_dict() for entry in settings.history],
        "geometry": settings.geometry,
        "splitter": list(settings.splitter) if settings.splitter is not None else None,
    }
    _guiconfig.write_settings(payload, path or config_path())


# ---------------------------------------------------------------------- #
# 入口
# ---------------------------------------------------------------------- #
def run_gui() -> None:
    """启动 Qt 窗口（高 DPI 与主题由 ``gui.app`` 统一处理）。"""
    try:
        from fcmd.tools.text._regexvalidatew_qt import run_qt
    except ImportError as exc:  # pragma: no cover - 安装完整时不会触发
        from fcmd.gui.compat import qt_import_failure

        raise SystemExit(qt_import_failure(exc)) from exc

    run_qt()


@fcmd.tool("regexvalidatew", help="打开正则校验器窗口（Qt）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开正则校验器窗口。"""
    run_gui()


@fcmd.main("regexvalidatew")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
