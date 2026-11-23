"""wslmanw「用 IDE / AI Agent 打开当前目录」的纯逻辑。

两种工具形态分开处理：

- ``windows``：在 Windows 宿主机执行 GUI 程序（VSCode、Trae、Qoder 等）。命令
  模板里的 ``{dir}`` 是实例内 POSIX 路径，``{unc}`` 是 ``\\\\wsl.localhost\\<实例>``
  形式的 UNC 路径；模板里的引号只用来保留空格，不做反斜杠转义。
- ``wsl``：在实例内执行终端类程序（OpenCode、PI 等）。实例名通过
  ``wsl.exe -d <实例>`` 传入，工作目录由 ``--cd`` 指定，模板里通常无需再写路径。

内置项只是默认模板：命令名 / 参数因本机安装方式而异，全部可以在设置里改用
自定义条目（见 ``_wslmanw_qt`` 的「打开方式」卡片）。
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fcmd.apps.wslman._errors import WslmanError, invalid_argument

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "BUILTINS",
    "DEFAULT_KEYS",
    "HOST_LABELS",
    "HOST_WINDOWS",
    "HOST_WSL",
    "KINDS",
    "KIND_AGENT",
    "KIND_IDE",
    "KIND_LABELS",
    "ORIGIN_DEFAULT",
    "ORIGIN_DETECTED",
    "ORIGIN_USER",
    "Detection",
    "Opener",
    "command_name",
    "default_openers",
    "detect_host",
    "detect_script",
    "detect_wsl",
    "new_key",
    "parse_detect",
    "parse_mappings",
    "probe_script",
    "render_command",
    "split_command",
    "to_mapping",
    "validate_opener",
    "windows_argv",
    "windows_candidates",
    "wrap_windows_argv",
    "wsl_command",
]

ORIGIN_DEFAULT = "default"
"""命令来自内置预设，尚未被用户或检测结果改动。"""

ORIGIN_DETECTED = "detected"
"""命令由「智能检测」补全；再次检测时可以继续更新。"""

ORIGIN_USER = "user"
"""用户手填 / 手改过：检测永远不覆盖。"""

KIND_IDE = "ide"
KIND_AGENT = "agent"
KINDS: tuple[str, ...] = (KIND_IDE, KIND_AGENT)
KIND_LABELS = {KIND_IDE: "IDE", KIND_AGENT: "AI Agent"}

HOST_WINDOWS = "windows"
HOST_WSL = "wsl"
HOST_LABELS = {HOST_WINDOWS: "Windows 宿主机", HOST_WSL: "WSL 实例内"}


@dataclass(frozen=True, slots=True)
class Opener:
    """一种打开方式：显示名 + 运行位置 + 命令模板。"""

    key: str
    name: str
    kind: str
    host: str
    command: str
    builtin: bool = False
    note: str = ""
    origin: str = ORIGIN_DEFAULT

    @property
    def user_edited(self) -> bool:
        """用户手填 / 手改过：智能检测不得覆盖。"""
        return self.origin == ORIGIN_USER


BUILTINS: dict[str, tuple[Opener, ...]] = {
    KIND_IDE: (
        Opener(
            "vscode",
            "VSCode",
            KIND_IDE,
            HOST_WINDOWS,
            'code --remote wsl+{distro} "{dir}"',
            True,
            "调用 code 命令的 WSL 远程参数；需把 VSCode 的 code 加入 PATH。",
        ),
        Opener(
            "trae",
            "Trae",
            KIND_IDE,
            HOST_WINDOWS,
            'trae --remote wsl+{distro} "{dir}"',
            True,
            "按 VS Code 系 remote 参数估计；若 Trae 的 CLI 参数不同，请在设置里改模板。",
        ),
        Opener(
            "trae-cn",
            "TRAE CN（国内版）",
            KIND_IDE,
            HOST_WINDOWS,
            'trae-cn --remote wsl+{distro} "{dir}"',
            True,
            "国内版与海外版分开配置，不共用同一条：命令名按 trae-cn 估计，安装目录按 "
            "Programs\\Trae CN 估计，均未逐一确认；若本机实际不同，请在设置里改模板。",
        ),
        Opener(
            "qoder",
            "Qoder",
            KIND_IDE,
            HOST_WINDOWS,
            'qoder --remote wsl+{distro} "{dir}"',
            True,
            "按 VS Code 系 remote 参数估计；若 Qoder 的 CLI 参数不同，请在设置里改模板。",
        ),
        Opener(
            "qoder-cn",
            "QODER CN（国内版）",
            KIND_IDE,
            HOST_WINDOWS,
            'qoder-cn --remote wsl+{distro} "{dir}"',
            True,
            "国内版与海外版分开配置，不共用同一条：命令名按 qoder-cn 估计，安装目录按 "
            "Programs\\Qoder CN 估计，均未逐一确认；若本机实际不同，请在设置里改模板。",
        ),
        Opener(
            "workbuddy",
            "WorkBuddy",
            KIND_IDE,
            HOST_WINDOWS,
            'workbuddy "{unc}"',
            True,
            "未确认官方 CLI：默认首版按可执行文件 workbuddy 传 UNC 路径，可在设置里改。",
        ),
    ),
    KIND_AGENT: (
        Opener(
            "pi",
            "PI",
            KIND_AGENT,
            HOST_WSL,
            "pi",
            True,
            "在实例内以当前目录启动 pi；未确认官方命令名，可在设置里改。",
        ),
        Opener(
            "opencode",
            "OpenCode",
            KIND_AGENT,
            HOST_WSL,
            "opencode",
            True,
            "在实例内以当前目录启动 OpenCode TUI。",
        ),
        Opener(
            "deepseek-harness",
            "DeepSeek Harness",
            KIND_AGENT,
            HOST_WSL,
            "dsh",
            True,
            "按官方 CLI dsh（npm 包 @deepseek-ai/dsh）处理；可执行名也可能是 "
            "deepseek-harness，检测两者都会覆盖。若本机命令不同，请在设置里改模板。",
        ),
        Opener(
            "opencode-windows",
            "OpenCode（Windows）",
            KIND_AGENT,
            HOST_WINDOWS,
            'opencode "{unc}"',
            True,
            "Windows 侧 OpenCode：优先 PATH 里的 opencode，其次 ~/.bun/bin、~/.opencode/bin "
            "与桌面版 resources\\opencode-cli.exe。桌面版若不接受目录参数，请在设置里改模板。",
        ),
        Opener(
            "deepseek-harness-windows",
            "DeepSeek Harness（Windows）",
            KIND_AGENT,
            HOST_WINDOWS,
            'dsh "{unc}"',
            True,
            "Windows 侧 DSH：优先 PATH 里的 dsh / deepseek-harness，其次 ~/.bun/bin、~/.dsh/bin "
            "与 DeepSeek Harness 桌面版。桌面版若不接受目录参数，请在设置里改模板。",
        ),
    ),
}
"""内置预设。``note`` 写清不确定项，界面在编辑对话框里原样展示。"""

DEFAULT_KEYS = {KIND_IDE: "vscode", KIND_AGENT: "opencode"}
"""没有保存过选择时的默认条目：VSCode 与 OpenCode（已确认可用的那一个）。"""


def default_openers(kind: str) -> tuple[Opener, ...]:
    return BUILTINS.get(kind, ())


def to_mapping(opener: Opener) -> dict[str, object]:
    """序列化；``builtin`` 不落盘，重新载入时按 key 识别；``origin`` 记住是否手改过。"""
    return {
        "key": opener.key,
        "name": opener.name,
        "host": opener.host,
        "command": opener.command,
        "origin": opener.origin,
    }


def _origin(raw: Any, builtin: bool) -> str:
    value = raw if isinstance(raw, str) else ""
    if value in (ORIGIN_DEFAULT, ORIGIN_DETECTED, ORIGIN_USER):
        return value
    # 旧设置没有 origin：内置项视为默认模板，自定义条目视为用户手填。
    return ORIGIN_DEFAULT if builtin else ORIGIN_USER


def _from_mapping(raw: Any, kind: str) -> Opener | None:
    if not isinstance(raw, Mapping):
        return None
    key = raw.get("key")
    name = raw.get("name")
    host = raw.get("host")
    command = raw.get("command")
    if not all(isinstance(item, str) and item.strip() for item in (key, name, host, command)):
        return None
    assert isinstance(key, str) and isinstance(name, str) and isinstance(host, str) and isinstance(command, str)
    if host not in (HOST_WINDOWS, HOST_WSL):
        return None
    try:
        validate_opener(name, host, command)
    except WslmanError:
        return None
    blueprint = next((item for item in default_openers(kind) if item.key == key.strip()), None)
    origin = _origin(raw.get("origin"), blueprint is not None)
    if blueprint is not None:
        return Opener(key.strip(), name.strip(), kind, host, command.strip(), True, blueprint.note, origin)
    return Opener(key.strip(), name.strip(), kind, host, command.strip(), False, "", origin)


def parse_mappings(raw: Any, kind: str) -> tuple[Opener, ...]:
    """解析设置里的条目列表；坏行跳过，key 重复时保留第一个。"""
    if not isinstance(raw, list):
        return ()
    seen: set[str] = set()
    openers: list[Opener] = []
    for item in raw:
        opener = _from_mapping(item, kind)
        if opener is None or opener.key in seen:
            continue
        seen.add(opener.key)
        openers.append(opener)
    return tuple(openers)


@dataclass(frozen=True, slots=True)
class Detection:
    """一个内置工具在某一侧的探测结果。

    ``path`` 为空表示没找到；``command`` 是把可执行文件绝对路径写回内置模板后的
    命令（找到时才有），界面据此补全条目而不用改变运行位置。
    """

    key: str
    host: str
    path: str = ""
    command: str = ""

    @property
    def found(self) -> bool:
        return bool(self.path)

    @property
    def host_label(self) -> str:
        return HOST_LABELS.get(self.host, self.host)


_EXE_NAMES: dict[str, tuple[str, ...]] = {
    "vscode": ("code",),
    "trae": ("trae",),
    "trae-cn": ("trae-cn",),
    "qoder": ("qoder",),
    "qoder-cn": ("qoder-cn",),
    "workbuddy": ("workbuddy",),
    "pi": ("pi",),
    "opencode": ("opencode",),
    "deepseek-harness": ("dsh", "deepseek-harness"),
    "opencode-windows": ("opencode", "opencode-cli"),
    "deepseek-harness-windows": ("dsh", "deepseek-harness"),
}
"""内置条目对应的可执行文件名（PATH / ``command -v`` 探测用）。

国内版与海外版各用各自的别名，不共享探测名：同名可执行文件（如都叫 ``trae``）
会分不清是哪一版，宁可让国内版落到安装目录探测。
"""

_WINDOWS_INSTALLS: dict[str, tuple[str, ...]] = {
    "vscode": (r"Programs\Microsoft VS Code\bin\code.cmd", r"Microsoft VS Code\bin\code.cmd"),
    "trae": (r"Programs\Trae\Trae.exe", r"Programs\Trae\bin\trae.cmd"),
    "trae-cn": (r"Programs\Trae CN\Trae CN.exe", r"Programs\Trae CN\bin\trae-cn.cmd"),
    "qoder": (r"Programs\Qoder\Qoder.exe", r"Programs\Qoder\bin\qoder.cmd"),
    "qoder-cn": (
        # 国内版实际安装目录有 ``Programs\Qoder CN`` 与 ``Program Files\Qoder CN IDE``
        # 两种；两种都列，检测到哪个用哪个。
        r"Programs\Qoder CN\Qoder CN.exe",
        r"Programs\Qoder CN\bin\qoder-cn.cmd",
        r"Programs\Qoder CN IDE\Qoder CN IDE.exe",
        r"Programs\Qoder CN IDE\bin\qoder-cn.cmd",
        r"Qoder CN IDE\Qoder CN IDE.exe",
        r"Qoder CN IDE\bin\qoder-cn.cmd",
    ),
    "workbuddy": (r"Programs\WorkBuddy\WorkBuddy.exe", r"WorkBuddy\WorkBuddy.exe"),
    "pi": (r"npm\pi.cmd", r".bun\bin\pi.exe"),
    "opencode": (
        r"npm\opencode.cmd",
        r".bun\bin\opencode.exe",
        r".opencode\bin\opencode.exe",
        r".local\bin\opencode.exe",
        r"Programs\ai.opencode.desktop\resources\opencode-cli.exe",
        r"Programs\ai.opencode.desktop\OpenCode.exe",
    ),
    "deepseek-harness": (
        r"npm\dsh.cmd",
        r"npm\deepseek-harness.cmd",
        r".bun\bin\dsh.exe",
        r".dsh\bin\dsh.exe",
        r".local\bin\dsh.exe",
    ),
    "opencode-windows": (
        r"npm\opencode.cmd",
        r".bun\bin\opencode.exe",
        r".opencode\bin\opencode.exe",
        r".local\bin\opencode.exe",
        r"Programs\ai.opencode.desktop\resources\opencode-cli.exe",
        r"Programs\ai.opencode.desktop\OpenCode.exe",
    ),
    "deepseek-harness-windows": (
        r"npm\dsh.cmd",
        r"npm\deepseek-harness.cmd",
        r".bun\bin\dsh.exe",
        r".dsh\bin\dsh.exe",
        r".local\bin\dsh.exe",
        r"Programs\DeepSeek Harness\DeepSeek Harness.exe",
        r"Programs\DeepSeek Harness\resources\app.asar.unpacked\dsh\bin\dsh.exe",
    ),
}
"""常见安装位置（相对常见安装根目录）；PATH 里没有时再按这些候选逐一确认。

国内版走 ``Trae CN`` / ``Qoder CN`` 独立目录，避免与海外版同名路径混成一条。
"""


def _template_of(key: str) -> str:
    for kind in KINDS:
        for opener in default_openers(kind):
            if opener.key == key:
                return opener.command
    return ""


def _with_exe(template: str, path: str) -> str:
    """把模板里的命令名换成检测到的绝对路径（含空格时加引号）。"""
    _, _, rest = template.partition(" ")
    quoted = f'"{path}"' if " " in path else path
    return f"{quoted} {rest}".rstrip()


def windows_candidates(key: str, env: Mapping[str, str] | None = None) -> tuple[Path, ...]:
    """Windows 上常见安装位置：LOCALAPPDATA / APPDATA / 用户目录 / Program Files（含 x86）。"""
    values = os.environ if env is None else env
    roots = [
        values.get("LOCALAPPDATA", ""),
        values.get("APPDATA", ""),
        values.get("USERPROFILE", ""),
        values.get("ProgramFiles", ""),
        values.get("ProgramFiles(x86)", ""),
    ]
    candidates: list[Path] = []
    for relative in _WINDOWS_INSTALLS.get(key, ()):
        # 按段拼接：Windows 上仍是反斜杠，POSIX（测试 / CI）下落成本地分隔符，
        # 避免把 ``npm\x.cmd`` 当成一个带反斜杠的文件名。
        parts = tuple(part for part in relative.split("\\") if part)
        for root in roots:
            if root:
                candidates.append(Path(root, *parts))
    return tuple(candidates)


def detect_host(
    key: str,
    *,
    env: Mapping[str, str] | None = None,
    which: Any = shutil.which,
) -> Detection:
    """宿主机探测：先 ``PATH``，再常见安装目录；都没找到时 ``found`` 为假。"""
    template = _template_of(key)
    for exe in _EXE_NAMES.get(key, (key,)):
        found = which(exe)
        if found:
            return Detection(key, HOST_WINDOWS, str(found), _with_exe(template, str(found)))
    for candidate in windows_candidates(key, env):
        if candidate.is_file():
            return Detection(key, HOST_WINDOWS, str(candidate), _with_exe(template, str(candidate)))
    return Detection(key, HOST_WINDOWS)


def detect_script() -> str:
    """实例内一次性探测所有内置可执行文件，输出 ``OPEN=<命令>|<路径>``。"""
    exes = sorted({exe for names in _EXE_NAMES.values() for exe in names})
    return "\n".join(
        (
            "set +e",
            # 工具安装脚本常把 PATH 写进 ~/.bashrc，而 ``bash -lc`` 只读登录配置；
            # 显式补上常见用户级 bin 目录，避免已经装好的 opencode / dsh 被漏掉。
            'PATH="$HOME/.opencode/bin:$HOME/.bun/bin:$HOME/.local/bin:$HOME/.npm-global/bin:$HOME/.cargo/bin:$PATH"',
            "export PATH",
            f"for cmd in {' '.join(exes)}; do",
            '  p=$(command -v -- "$cmd" 2>/dev/null)',
            '  [ -n "$p" ] && printf "OPEN=%s|%s\\n" "$cmd" "$p"',
            "done",
            # 最后一项没找到时循环退出码为 1；显式归零，避免“找到一部分”被当失败。
            "exit 0",
        )
    )


def parse_detect(text: str) -> dict[str, str]:
    """解析 ``command -v`` 批量探测输出；忽略 profile 横幅等其它行。"""
    found: dict[str, str] = {}
    for line in text.splitlines():
        if not line.startswith("OPEN="):
            continue
        exe, _, path = line[len("OPEN=") :].partition("|")
        exe, path = exe.strip(), path.strip()
        if exe and path:
            found[exe] = path
    return found


def detect_wsl(key: str, found: Mapping[str, str]) -> Detection:
    """用实例内 ``command -v`` 的结果组装 Detection（命令走实例内绝对路径）。"""
    template = _template_of(key)
    for exe in _EXE_NAMES.get(key, (key,)):
        path = found.get(exe, "")
        if path:
            return Detection(key, HOST_WSL, path, _with_exe(template, path))
    return Detection(key, HOST_WSL)


def new_key(name: str, existing: Sequence[str]) -> str:
    """按名称生成稳定 key；与已有 key 冲突时追加序号。"""
    base = re.sub(r"[^a-z0-9]+", "-", name.strip().casefold()).strip("-") or "custom"
    used = {item.casefold() for item in existing}
    key = base
    index = 2
    while key.casefold() in used:
        key = f"{base}-{index}"
        index += 1
    return key


def split_command(text: str) -> tuple[str, ...]:
    """按空白拆命令，支持成对引号，但**不**把反斜杠当转义（UNC 路径要保留）。"""
    tokens: list[str] = []
    current: list[str] = []
    quote = ""
    for char in text.strip():
        if quote:
            if char == quote:
                quote = ""
            else:
                current.append(char)
        elif char in {'"', "'"}:
            quote = char
        elif char.isspace():
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(char)
    if quote:
        raise invalid_argument(f"命令模板引号未闭合：{text}", hint="检查引号是否成对")
    if current:
        tokens.append("".join(current))
    if not tokens:
        raise invalid_argument("命令模板为空", hint='填写可执行命令，例如 code --remote wsl+{distro} "{dir}"')
    return tuple(tokens)


def validate_opener(name: str, host: str, command: str) -> tuple[str, str, str]:
    """校验并返回 ``(名称, 运行位置, 模板)``；Windows 模板必须带路径占位符。"""
    title = name.strip()
    template = command.strip()
    if not title:
        raise invalid_argument("名称不能为空", hint="给这个打开方式起个名字")
    if host not in (HOST_WINDOWS, HOST_WSL):
        raise invalid_argument(f"未知的运行位置：{host}", hint="选择 Windows 宿主机或 WSL 实例内")
    split_command(template)
    if host == HOST_WINDOWS and "{dir}" not in template and "{unc}" not in template:
        raise invalid_argument(
            "Windows 命令模板需要包含 {dir} 或 {unc}",
            hint="否则无法把当前目录传给工具；WSL 内运行的工具由 --cd 指定目录，可不写占位符",
        )
    return title, host, template


def render_command(opener: Opener, *, dir: str, unc: str, distro: str, home: str) -> str:
    """替换占位符；未登记的 ``{xxx}`` 原样保留，由用户自行确认。"""
    return (
        opener.command.replace("{dir}", dir).replace("{unc}", unc).replace("{distro}", distro).replace("{home}", home)
    )


def windows_argv(opener: Opener, *, dir: str, unc: str, distro: str, home: str) -> tuple[str, ...]:
    return split_command(render_command(opener, dir=dir, unc=unc, distro=distro, home=home))


def wsl_command(opener: Opener, *, dir: str, unc: str, distro: str, home: str) -> str:
    """实例内执行的 shell 命令文本（由 ``bash -lc`` 解释）。"""
    return render_command(opener, dir=dir, unc=unc, distro=distro, home=home)


def command_name(opener: Opener, *, dir: str, unc: str, distro: str, home: str) -> str:
    """模板里的可执行文件名，用于启动前确认工具是否装了。"""
    argv = windows_argv(opener, dir=dir, unc=unc, distro=distro, home=home)
    return argv[0]


def probe_script(opener: Opener, *, dir: str, unc: str, distro: str, home: str) -> str:
    """``bash -lc`` 用的存在性探测：找到时输出路径，找不到时非零退出。"""
    exe = command_name(opener, dir=dir, unc=unc, distro=distro, home=home)
    return f"command -v -- {shlex.quote(exe)}"


def wrap_windows_argv(argv: Sequence[str]) -> tuple[str, ...]:
    """``.cmd`` / ``.bat`` 需要经 ``cmd.exe /c`` 启动，Popen 才能执行。

    不要把整条命令用 ``subprocess.list2cmdline`` 拼成一个参数：Python 会把带空格
    路径两侧的引号转义成 ``\"``，而 ``cmd.exe`` 不认这种转义，会把整个路径当成
    一条不存在的命令（实测 ``trae-cn.CMD`` 直接退出 1，IDE 永远不弹窗）。这里保留
    参数列表形式，只把第一项换成 ``cmd.exe``，由 Popen 按 Windows 规则给单个参数
    加引号；不要加 ``/s``：它会把带空格路径的首个引号对拆掉，同样会解析失败。
    """
    if not argv:
        raise invalid_argument("命令模板为空", hint="填写可执行命令")
    if argv[0].casefold().endswith((".cmd", ".bat")):
        return ("cmd.exe", "/d", "/c", *argv)
    return tuple(argv)
