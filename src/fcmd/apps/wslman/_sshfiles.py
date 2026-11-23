"""wslmanw「迁移 SSH 文件」的纯逻辑。

从 Windows 侧（默认 ``%USERPROFILE%\\.ssh``）读取私钥 / 公钥 / ``known_hosts`` /
``config``，写入实例的 ``~/.ssh``。这里只负责扫描、分类、生成探测与写入脚本、
解析结果；文件内容由界面线程读入后通过 stdin 送进实例，避免私钥出现在命令行
（进程列表、日志都可能被看到）。

权限口径与 OpenSSH 一致：``~/.ssh`` 目录 700，私钥 600，其余文件 644。覆盖
已有文件时先备份到 ``~/.ssh/fcmd-ssh-backup-<时间>/``，脚本在目标文件已存在
但未确认覆盖时直接失败，不做隐式覆盖。
"""

from __future__ import annotations

import base64
import binascii
import re
import shlex
import time
from dataclasses import dataclass
from pathlib import Path

from fcmd.apps.wslman._errors import not_found, runtime_error

__all__ = [
    "BACKUP_PREFIX",
    "SSH_DIR",
    "WRITE_PREFIX",
    "RemoteFile",
    "SshFile",
    "SshTarget",
    "backup_name",
    "classify",
    "conflicts",
    "default_source_dir",
    "kind_label",
    "looks_private",
    "loose_modes",
    "parse_probe",
    "parse_write_result",
    "probe_script",
    "read_source",
    "scan_source",
    "target_summary",
    "write_script",
]

SSH_DIR = "~/.ssh"
"""实例内的目标目录（展示用；脚本里用 ``$HOME/.ssh``）。"""

BACKUP_PREFIX = "fcmd-ssh-backup-"
"""覆盖前备份目录名前缀。"""

WRITE_PREFIX = "WSLSSH_WRITTEN="
"""写入脚本最后一行：``<权限>|<属主>``，供界面校验落盘结果。"""

_PROBE_PREFIX = "WSLSSH_"
_HEAD_BYTES = 4096
"""扫描时只读文件头用于分类，不把整个文件读进内存。"""

_KIND_ORDER = {"private": 0, "public": 1, "known_hosts": 2, "config": 3, "other": 4}
_KIND_LABELS = {
    "private": "私钥",
    "public": "公钥",
    "known_hosts": "known_hosts",
    "config": "配置",
    "other": "其它",
}
_KIND_MODES = {"private": 0o600}
_KIND_DEFAULT_MODE = 0o644
_DRIVE_PATH = re.compile(r"[A-Za-z]:[\\/]")


@dataclass(frozen=True, slots=True)
class SshFile:
    """Windows 侧可迁移的一个文件。"""

    name: str
    size: int
    kind: str
    warning: str = ""

    @property
    def mode(self) -> int:
        """写入实例后的权限：私钥 600，其余 644（八进制）。"""
        return _KIND_MODES.get(self.kind, _KIND_DEFAULT_MODE)

    @property
    def mode_text(self) -> str:
        return f"{self.mode:03o}"

    @property
    def kind_label(self) -> str:
        return kind_label(self.kind)

    @property
    def size_text(self) -> str:
        if self.size < 1024:
            return f"{self.size} B"
        if self.size < 1024 * 1024:
            return f"{self.size / 1024:.1f} KB"
        return f"{self.size / (1024 * 1024):.1f} MB"

    @property
    def checked_by_default(self) -> bool:
        """未知类型默认不勾选：仍然可见、可手动勾选，但不顺手带走无关文件。"""
        return self.kind != "other"


@dataclass(frozen=True, slots=True)
class RemoteFile:
    """实例内 ``~/.ssh`` 下已有的文件。"""

    name: str
    mode: str
    size: int = 0


@dataclass(frozen=True, slots=True)
class SshTarget:
    """一次探测得到的实例 SSH 目录快照。"""

    user: str = ""
    home: str = ""
    dir: str = ""
    dir_mode: str = ""
    files: tuple[RemoteFile, ...] = ()

    @property
    def exists(self) -> bool:
        return bool(self.dir)

    def names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.files)

    def find(self, name: str) -> RemoteFile | None:
        return next((item for item in self.files if item.name == name), None)


def default_source_dir() -> Path:
    """默认来源：当前 Windows 用户的家目录下的 ``.ssh``。"""
    return Path.home() / ".ssh"


def kind_label(kind: str) -> str:
    return _KIND_LABELS.get(kind, kind)


def looks_private(name: str) -> bool:
    """按文件名判断是否像私钥（``id_*`` 且不是 ``.pub``）。"""
    return name.startswith("id_") and not name.endswith(".pub")


def classify(name: str, content: bytes) -> str:
    """按文件名与文件头判断类型；识别不了的归入 ``other``。"""
    if name.startswith("known_hosts"):
        return "known_hosts"
    if name == "config":
        return "config"
    if name.endswith(".pub"):
        return "public"
    if b"PRIVATE KEY" in content:
        return "private"
    if looks_private(name):
        return "private"
    return "other"


def _warning(name: str, kind: str, head: bytes) -> str:
    if name.casefold().endswith(".ppk"):
        return "PuTTY 私钥格式，OpenSSH 默认不能直接使用"
    if kind == "private" and not head:
        return "文件为空"
    if kind == "config":
        text = head.decode("utf-8", errors="replace")
        if "\\" in text or _DRIVE_PATH.search(text):
            return "配置里可能有 Windows 路径，迁移后请检查 IdentityFile"
    if kind == "other":
        return "未知类型，确认后再迁移"
    return ""


def scan_source(directory: Path) -> tuple[SshFile, ...]:
    """列出来源目录里的普通文件；目录不存在时给出可操作提示。"""
    path = Path(directory)
    try:
        if not path.is_dir():
            raise not_found(
                f"未找到 SSH 目录：{path}",
                hint="在 Windows 上先执行一次 ssh 连接，或点「浏览…」选择其它目录",
            )
        children = list(path.iterdir())
    except OSError as exc:
        raise runtime_error(f"无法读取 SSH 目录：{path}", hint=str(exc)) from exc

    files: list[SshFile] = []
    for child in children:
        try:
            if not child.is_file():
                continue
            size = child.stat().st_size
            with child.open("rb") as handle:
                head = handle.read(_HEAD_BYTES)
        except OSError:
            # 单个文件读不到不影响其它文件；跳过即可，避免整次扫描失败。
            continue
        kind = classify(child.name, head)
        files.append(SshFile(child.name, size, kind, _warning(child.name, kind, head)))
    files.sort(key=lambda item: (_KIND_ORDER.get(item.kind, len(_KIND_ORDER)), item.name.casefold()))
    return tuple(files)


def read_source(path: Path) -> bytes:
    """读取一个待迁移文件的内容；失败时转成可操作错误。"""
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise runtime_error(f"无法读取源文件：{path}", hint=str(exc)) from exc


def probe_script() -> str:
    """只读探测实例 ``~/.ssh``：目录、权限与文件清单（文件名 base64）。"""
    return "\n".join(
        (
            "set +e",
            'printf "WSLSSH_USER=%s\\n" "$(id -un 2>/dev/null)"',
            'printf "WSLSSH_HOME=%s\\n" "$HOME"',
            'dir="$HOME/.ssh"',
            'if [ -d "$dir" ]; then',
            '    printf "WSLSSH_DIR=%s\\n" "$dir"',
            '    printf "WSLSSH_DIRMODE=%s\\n" "$(stat -c \'%a\' "$dir" 2>/dev/null)"',
            '    for file in "$dir"/*; do',
            '        [ -f "$file" ] || continue',
            '        name="${file##*/}"',
            '        mode="$(stat -c \'%a\' "$file" 2>/dev/null)"',
            '        size="$(stat -c \'%s\' "$file" 2>/dev/null)"',
            '        printf "WSLSSH_FILE=%s|%s|%s\\n" "$(printf \'%s\' "$name" | base64 | tr -d \'\\n\')" "$mode" "$size"',
            "    done",
            "fi",
        )
    )


def _decode_name(raw: str) -> str:
    text = raw.strip()
    if not text:
        return ""
    try:
        return base64.b64decode(text, validate=True).decode("utf-8", errors="replace")
    except (binascii.Error, ValueError):
        return text


def _decode_size(raw: str) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def parse_probe(text: str) -> SshTarget:
    """解析探测输出；非 ``WSLSSH_`` 行（profile 横幅）忽略。"""
    values: dict[str, str] = {}
    files: list[RemoteFile] = []
    for line in text.splitlines():
        if not line.startswith(_PROBE_PREFIX) or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key[len(_PROBE_PREFIX) :]
        if key == "FILE":
            raw_name, _, rest = value.partition("|")
            mode, _, size = rest.partition("|")
            files.append(RemoteFile(_decode_name(raw_name), mode.strip(), _decode_size(size)))
            continue
        values[key] = value.strip()
    return SshTarget(
        user=values.get("USER", ""),
        home=values.get("HOME", ""),
        dir=values.get("DIR", ""),
        dir_mode=values.get("DIRMODE", ""),
        files=tuple(files),
    )


def conflicts(names: tuple[str, ...] | list[str], target: SshTarget) -> tuple[str, ...]:
    """返回会覆盖实例内已有文件的名称（保持传入顺序）。"""
    present = set(target.names())
    return tuple(name for name in names if name in present)


def loose_modes(target: SshTarget) -> tuple[RemoteFile, ...]:
    """实例内权限过宽（组 / 其他人可读写）的私钥；迁移同名文件后会被修正为 600。"""
    found: list[RemoteFile] = []
    for item in target.files:
        if not looks_private(item.name):
            continue
        try:
            mode = int(item.mode, 8)
        except ValueError:
            continue
        if mode & 0o077:
            found.append(item)
    return tuple(found)


def target_summary(target: SshTarget) -> str:
    """实例侧现状的一行说明：目录 / 文件 / 覆盖与权限提示。"""
    if not target.exists:
        return "实例内尚未创建 ~/.ssh（迁移时自动创建，权限 700）"
    mode = f"，目录权限 {target.dir_mode}" if target.dir_mode else ""
    if not target.files:
        return f"实例内 ~/.ssh 为空目录{mode}，迁移不会覆盖已有文件"
    names = "、".join(target.names())
    text = f"实例内 ~/.ssh 已有 {len(target.files)} 个文件{mode}：{names}；同名文件会先备份再覆盖"
    loose = loose_modes(target)
    if loose:
        detail = "、".join(f"{item.name}（{item.mode}）" for item in loose)
        text += f"。注意：{detail}权限过宽，迁移同名文件后会改为 600"
    return text


def backup_name(stamp: float) -> str:
    """备份目录名：``fcmd-ssh-backup-YYYYmmdd-HHMMSS``。"""
    return BACKUP_PREFIX + time.strftime("%Y%m%d-%H%M%S", time.localtime(stamp))


def write_script(name: str, mode: int, *, overwrite: bool, backup: str) -> str:
    """生成写单个文件的脚本；内容从 stdin 读入，不出现在命令行里。

    ``overwrite=False`` 时目标已存在会以退出码 3 失败；``overwrite=True`` 时
    先把原文件移进 ``backup`` 目录（同名则追加序号），再写入新文件。
    """
    quoted_name = shlex.quote(name)
    quoted_backup = shlex.quote(backup)
    overwrite_flag = 1 if overwrite else 0
    return "\n".join(
        (
            "set -euo pipefail",
            'dir="$HOME/.ssh"',
            "umask 077",
            'mkdir -p "$dir"',
            'chmod 700 "$dir"',
            f'target="$dir"/{quoted_name}',
            'if [ -e "$target" ] || [ -L "$target" ]; then',
            f'    if [ "{overwrite_flag}" -ne 1 ]; then',
            '        echo "目标已存在且未确认覆盖：$target" >&2',
            "        exit 3",
            "    fi",
            f'    backup="$dir"/{quoted_backup}',
            "    n=1",
            '    while [ -e "$backup" ]; do',
            f'        backup="$dir"/{quoted_backup}-"$n"',
            "        n=$((n + 1))",
            "    done",
            '    mkdir -p "$backup"',
            '    chmod 700 "$backup"',
            '    mv "$target" "$backup"/',
            f'    echo "已备份原有文件：$backup/{name}"',
            "fi",
            'tmp="$dir/.fcmd-ssh.$$"',
            "trap 'rm -f \"$tmp\"' EXIT",
            'cat > "$tmp"',
            f'chmod {mode:03o} "$tmp"',
            'mv -f "$tmp" "$target"',
            f'chmod {mode:03o} "$target"',
            f'echo "已写入 $target（权限 {mode:03o}）"',
            f'echo "{WRITE_PREFIX}$(stat -c \'%a\' "$target" 2>/dev/null)|$(id -un 2>/dev/null)"',
        )
    )


def parse_write_result(text: str) -> tuple[str, str]:
    """从脚本输出里取 ``(权限, 属主)``；没有标记时返回两个空串。"""
    for line in reversed(text.splitlines()):
        if line.startswith(WRITE_PREFIX):
            mode, _, owner = line[len(WRITE_PREFIX) :].partition("|")
            return mode.strip(), owner.strip()
    return "", ""
