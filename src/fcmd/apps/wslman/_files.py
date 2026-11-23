"""wslmanw 文件管理的纯逻辑：家目录探测、UNC 映射与本地文件操作。

浏览与增删改都走 Windows 侧的 UNC 根（优先 ``\\\\wsl.localhost\\<实例>``，
旧系统回退 ``\\\\wsl$\\<实例>``），不逐条执行 ``wsl.exe``：列目录、复制、改名
由 Windows 文件 API 完成，路径口径更稳，也避免把 POSIX 路径塞进 shell 的引号
问题。唯一需要进入实例的命令是取当前用户与家目录（:func:`home_probe_script`），
用 ``wsl -d <实例> --exec bash -lc`` 执行。

这里只做路径换算、目录读取与文件操作，不依赖 Qt，也不读配置；界面负责把
:class:`WslmanError` 转成可操作提示。
"""

from __future__ import annotations

import posixpath
import shutil
import time
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING

from fcmd.apps.wslman._disk import format_bytes
from fcmd.apps.wslman._errors import invalid_argument, not_found, runtime_error

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

__all__ = [
    "HOME_ALIAS",
    "FileEntry",
    "check_name",
    "create_dir",
    "delete_entry",
    "display_path",
    "download_file",
    "format_mtime",
    "home_probe_script",
    "join_path",
    "list_entries",
    "normalize_path",
    "parent_path",
    "parse_home",
    "pick_unc_root",
    "rename_entry",
    "size_text",
    "to_local",
    "to_posix",
    "type_text",
    "unc_candidates",
    "upload_files",
]

HOME_ALIAS = "~"
"""路径栏里表示实例家目录的别名。"""

UNC_HOSTS: tuple[str, ...] = ("wsl.localhost", "wsl$")
"""WSL 文件系统在 Windows 侧的 UNC 主机名；``wsl$`` 是旧版回退。"""

_PROBE_PREFIX = "WSLFILES_"
_MAX_NAME_LENGTH = 255


@dataclass(frozen=True, slots=True)
class FileEntry:
    """目录里的一项；``size`` 对文件夹无意义，展示时统一写成 ``—``。"""

    name: str
    is_dir: bool
    size: int
    modified: float


def home_probe_script() -> str:
    """只读探测脚本：输出固定前缀的两行，供 :func:`parse_home` 解析。"""
    return "\n".join(
        (
            'printf "WSLFILES_USER=%s\\n" "$(id -un 2>/dev/null)"',
            'printf "WSLFILES_HOME=%s\\n" "$HOME"',
        )
    )


def parse_home(text: str) -> tuple[str, str]:
    """解析探测输出，返回 ``(用户名, 家目录)``；非前缀行（profile 横幅）忽略。"""
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line.startswith(_PROBE_PREFIX) or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key[len(_PROBE_PREFIX) :]] = value.strip()
    user = values.get("USER", "")
    home = values.get("HOME", "")
    if home and not home.startswith("/"):
        home = "/" + home
    return user, home


def unc_candidates(distro: str) -> tuple[str, ...]:
    """该实例的 UNC 根候选：先新版 ``wsl.localhost``，再旧版 ``wsl$``。"""
    return tuple(rf"\\{host}\{distro}" for host in UNC_HOSTS)


def _path_is_dir(path: str) -> bool:
    try:
        return Path(path).is_dir()
    except OSError:
        return False


def pick_unc_root(distro: str, *, probe: Callable[[str], bool] | None = None) -> str:
    """返回第一个可访问的 UNC 根。

    两个候选都不可访问时抛 :class:`WslmanError`：多半是实例没启动、发行版名
    对不上，或当前用户没有访问该实例文件系统的权限。
    """
    check = _path_is_dir if probe is None else probe
    candidates = unc_candidates(distro)
    for root in candidates:
        try:
            if check(root):
                return root
        except OSError:
            continue
    raise runtime_error(
        f"无法访问 {distro} 的文件系统（{candidates[0]}）",
        hint=f"确认实例已启动（wsl -d {distro} --exec true），并能在资源管理器中打开 {candidates[0]}",
    )


def normalize_path(path: str, home: str = "/") -> str:
    """把路径栏的输入规范成绝对 POSIX 路径。

    支持 ``~`` / ``~/project``、相对家目录的 ``project``、``..`` 与重复斜杠；
    反斜杠按 ``/`` 处理，方便从 Windows 复制路径。空输入回落到家目录。
    """
    base = _absolute(home or "/")
    text = (path or "").strip().replace("\\", "/")
    if "\0" in text:
        raise invalid_argument("路径包含非法字符 NUL", hint="检查路径后重试")
    if not text or text == HOME_ALIAS:
        return base
    if text.startswith(HOME_ALIAS + "/"):
        text = posixpath.join(base, text[len(HOME_ALIAS) + 1 :])
    elif not text.startswith("/"):
        text = posixpath.join(base, text)
    return _absolute(text)


def _absolute(path: str) -> str:
    """规范成单斜杠开头的绝对路径。"""
    normalized = posixpath.normpath(path.replace("\\", "/"))
    if normalized in {"", "."}:
        return "/"
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    # ``normpath`` 会保留 ``//server`` 形式的双斜杠；这里只接受单根。
    return "/" + normalized.lstrip("/") if normalized.startswith("//") else normalized


def parent_path(path: str, home: str = "/") -> str:
    """上一级目录；根目录返回 ``/``。"""
    current = normalize_path(path, home)
    if current == "/":
        return "/"
    return posixpath.dirname(current) or "/"


def join_path(base: str, name: str) -> str:
    """拼接目录与名称（名称由目录列表给出，不含分隔符）。"""
    return normalize_path(posixpath.join(normalize_path(base, "/"), name), "/")


def display_path(path: str, home: str) -> str:
    """展示用路径：家目录写成 ``~``，其下写成 ``~/x``，其余保持绝对路径。"""
    current = normalize_path(path, "/")
    base = normalize_path(home, "/") if home else "/"
    if home and current == base:
        return HOME_ALIAS
    if home and base != "/" and current.startswith(base + "/"):
        return HOME_ALIAS + current[len(base) :]
    return current


def check_name(name: str) -> str:
    """校验新建 / 重命名用的名称，返回去掉首尾空白后的名称。"""
    text = (name or "").strip()
    if not text or text in {".", ".."}:
        raise invalid_argument("名称不能为空或为 . / ..", hint="换一个名称")
    if any(ch in text for ch in ("/", "\\", "\0")):
        raise invalid_argument(f"名称不能包含路径分隔符：{text}", hint="名称只能是单个文件或文件夹名字")
    if len(text) > _MAX_NAME_LENGTH:
        raise invalid_argument(f"名称过长（{len(text)} 个字符）", hint=f"控制在 {_MAX_NAME_LENGTH} 个字符以内")
    return text


def to_local(root: str, posix_path: str) -> Path:
    """POSIX 路径 → Windows 本地路径（UNC 根下）。

    用 :class:`PureWindowsPath` 拼接，测试在非 Windows 平台也能验证映射结果；
    平台适配只发生在最后转成 :class:`Path`。
    """
    parts = [part for part in normalize_path(posix_path, "/").split("/") if part]
    return Path(str(PureWindowsPath(root, *parts)))


def to_posix(root: str, local: Path | str) -> str:
    """Windows 本地路径 → POSIX 路径；不在根下时回落到 ``/``。"""
    try:
        relative = PureWindowsPath(str(local)).relative_to(PureWindowsPath(root))
    except ValueError:
        return "/"
    parts = relative.parts
    return "/" + "/".join(parts) if parts else "/"


def _scan(path: Path) -> FileEntry:
    """读取一项的元数据；读不到时按文件、大小 / 时间未知处理，仍然列出。"""
    try:
        stat = path.stat()
    except OSError:
        return FileEntry(path.name, False, 0, 0.0)
    try:
        is_dir = path.is_dir()
    except OSError:
        is_dir = False
    return FileEntry(path.name, is_dir, 0 if is_dir else stat.st_size, stat.st_mtime)


def list_entries(directory: Path) -> tuple[FileEntry, ...]:
    """列出目录内容：文件夹在前，其余按名称（大小写不敏感）排序。"""
    try:
        children = list(directory.iterdir())
    except OSError as exc:
        raise runtime_error(
            f"无法读取目录：{directory}",
            hint=f"{exc}；确认实例已启动，且当前用户有权限访问该路径",
        ) from exc
    entries = [_scan(child) for child in children]
    return tuple(sorted(entries, key=lambda item: (not item.is_dir, item.name.casefold(), item.name)))


def type_text(entry: FileEntry) -> str:
    return "文件夹" if entry.is_dir else "文件"


def size_text(entry: FileEntry) -> str:
    return "—" if entry.is_dir else format_bytes(entry.size)


def format_mtime(entry: FileEntry) -> str:
    """修改时间：本地时区 ``YYYY-MM-DD HH:MM``；未知时 ``—``。"""
    if entry.modified <= 0:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(entry.modified))


def create_dir(parent: Path, name: str) -> Path:
    """在 ``parent`` 下新建文件夹；重名时报可操作错误。"""
    clean = check_name(name)
    target = parent / clean
    try:
        target.mkdir()
    except FileExistsError as exc:
        raise invalid_argument(f"已存在同名文件或文件夹：{clean}", hint="换一个名称后重试") from exc
    except OSError as exc:
        raise runtime_error(f"无法新建文件夹：{target}", hint=str(exc)) from exc
    return target


def rename_entry(path: Path, new_name: str) -> Path:
    """重命名文件 / 文件夹；目标已存在时不覆盖。"""
    clean = check_name(new_name)
    target = path.parent / clean
    if str(target) == str(path):
        return path
    if target.exists() or target.is_symlink():
        raise invalid_argument(f"已存在同名文件或文件夹：{clean}", hint="换一个名称后重试")
    try:
        path.rename(target)
    except OSError as exc:
        raise runtime_error(f"无法重命名 {path.name}", hint=str(exc)) from exc
    return target


def _same_path(left: Path, right: Path) -> bool:
    """UNC 路径比较：Windows 语义，大小写不敏感。"""
    return PureWindowsPath(str(left)) == PureWindowsPath(str(right))


def delete_entry(path: Path, root: Path) -> None:
    """删除文件或整个文件夹；拒绝删除实例根目录。"""
    if _same_path(path, root):
        raise invalid_argument("不能删除实例根目录", hint="请进入具体目录后再删除")
    try:
        # 符号链接本身要用 unlink 删除；``rmtree`` 会顺着链接报错或删到目标。
        if path.is_symlink() or not path.is_dir():
            path.unlink()
        else:
            shutil.rmtree(path)
    except OSError as exc:
        raise runtime_error(f"无法删除 {path.name}", hint=str(exc)) from exc


def upload_files(
    sources: Iterable[Path],
    target_dir: Path,
    *,
    overwrite: bool = False,
) -> tuple[Path, ...]:
    """把本地文件复制到实例目录；默认不覆盖同名文件。

    先整体检查源文件与目标冲突，再开始复制：避免复制到一半才因为重名失败，
    留下半套文件让用户猜哪些成功了。
    """
    pairs: list[tuple[Path, Path]] = []
    for source in sources:
        origin = Path(source)
        if not origin.is_file():
            raise not_found(f"源文件不存在：{origin}", hint="重新选择要上传的文件")
        target = target_dir / check_name(origin.name)
        if (target.exists() or target.is_symlink()) and not overwrite:
            raise invalid_argument(
                f"目标已存在同名文件：{origin.name}",
                hint="先在文件页删除 / 重命名目标，或勾选覆盖后重试",
            )
        pairs.append((origin, target))
    copied: list[Path] = []
    for origin, target in pairs:
        try:
            shutil.copy2(origin, target)
        except OSError as exc:
            raise runtime_error(f"上传失败：{origin.name}", hint=str(exc)) from exc
        copied.append(target)
    return tuple(copied)


def download_file(source: Path, target: Path, *, overwrite: bool = False) -> Path:
    """把实例里的文件复制到 Windows 磁盘；文件夹不走这里。"""
    if source.is_dir():
        raise invalid_argument(f"{source.name} 是文件夹", hint="暂不支持下载文件夹，请进入后选择具体文件")
    if not source.is_file():
        raise not_found(f"文件不存在：{source}", hint="点「刷新」重新列目录")
    if (target.exists() or target.is_symlink()) and not overwrite:
        raise invalid_argument(f"目标文件已存在：{target}", hint="换一个位置或确认覆盖后重试")
    try:
        shutil.copy2(source, target)
    except OSError as exc:
        raise runtime_error(f"下载失败：{source.name}", hint=str(exc)) from exc
    return target
