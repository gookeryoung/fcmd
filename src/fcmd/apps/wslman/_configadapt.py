"""wslmanw「迁移配置文件」的 WSL 适配。

Windows 侧的 SSH / Git 配置里经常写着 Windows 专用值：`core.sshCommand` 指向
`C:/Program Files/OpenSSH/ssh.exe`、`IdentityFile C:\\Users\\...`、`credential.helper
= manager`、CRLF 换行等。原样搬进 WSL 会让 `git pull` 直接报
`C:/Program Files/OpenSSH/ssh.exe: not found`，或者私钥路径永远找不到。

这里在**写入前**做保守适配，并把每一处改动写进迁移日志：

- 统一把 CRLF 转成 LF；
- Git：`core.sshCommand` 里的 Windows ssh 可执行文件换成 `ssh`；Windows 专用
  `credential.helper` / `editor` / `pager` / `include.path` 等整行注释掉；
  `.gitignore_global` / `.gitattributes` 这类 Windows 绝对路径改成 `~/<文件名>`；
- SSH：`IdentityFile` / `CertificateFile` 的 Windows 绝对路径改成 `~/.ssh/<文件名>`；
  `IdentityAgent`（Windows named pipe）、`ProxyCommand`、`Include`、`ControlPath`
  等 Windows 专用值注释掉。

只做「把明显不可用的 Windows 值换成 WSL 等价物 / 注释掉」，不猜测用户的其它
自定义配置；每处改动都有日志，用户可以在实例内继续手工调整。
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass

__all__ = ["Adaptation", "adapt", "adapt_notes", "has_windows_path", "normalize_newlines"]

_WIN_PATH = re.compile(r"(?i)(?:[a-z]:[\\/]|\\\\[^\\\s]+\\|\bcmd(?:\.exe)?\b|\bpowershell(?:\.exe)?\b|\.exe\b)")
"""匹配 Windows 绝对路径 / 反斜杠 UNC / 常见 Windows 可执行文件。"""

_GIT_KNOWN_FILES = {".gitconfig", ".gitignore_global", ".gitattributes"}
_WINDOWS_HELPERS = {"manager", "manager-core", "wincred", "winstore", "git-credential-manager"}
_SSH_PATH_KEYS = {"identityfile", "certificatefile", "userknownhostsfile", "globalknownhostsfile"}
_SSH_DROP_KEYS = {
    "identityagent",
    "proxycommand",
    "localcommand",
    "remotecommand",
    "knownhostscommand",
    "include",
    "controlpath",
    "pkcs11provider",
}
_GIT_DROP_KEYS = {
    "editor",
    "pager",
    "askpass",
    "hookspath",
    "worktree",
    "sshcommand",
    "excludesfile",
    "attributesfile",
}


@dataclass(frozen=True, slots=True)
class Adaptation:
    """一次适配结果：新的字节内容 + 用户可读的改动说明。"""

    data: bytes
    notes: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.notes)


def has_windows_path(value: str) -> bool:
    return bool(_WIN_PATH.search(value))


def normalize_newlines(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _note_line(prefix: str, text: str) -> str:
    """把一行配置注释掉，同时留下可追溯的原值。"""
    return f"{prefix} fcmd: 已注释 Windows 专用配置：{text.strip()}"


def _split_value(line: str) -> tuple[str, str]:
    key, sep, value = line.partition("=")
    if not sep:
        return line.strip(), ""
    return key.strip(), value.strip()


def _git_key(section: str, key: str) -> str:
    return f"{section}.{key}".lower() if section else key.lower()


def _git_section(line: str) -> str:
    text = line.strip()
    if not text.startswith("[") or not text.endswith("]"):
        return ""
    return text[1:-1].strip().split('"', 1)[0].strip().lower()


def _git_adapt(data: bytes, notes: list[str]) -> bytes:
    section = ""
    out: list[str] = []
    for line in normalize_newlines(data).decode("utf-8", "surrogateescape").split("\n"):
        stripped = line.strip()
        if stripped.startswith("["):
            section = _git_section(line)
            out.append(line)
            continue
        if not stripped or stripped.startswith(("#", ";")):
            out.append(line)
            continue
        key, value = _split_value(line)
        full = _git_key(section, key)
        if not value:
            out.append(line)
            continue
        if (full == "credential.helper" or full.endswith(".helper")) and value.casefold() in _WINDOWS_HELPERS:
            out.append(_note_line("#", line))
            notes.append(f"注释 Windows 专用 credential.helper：{value}")
            continue
        if not has_windows_path(value):
            out.append(line)
            continue
        indent = line[: len(line) - len(line.lstrip())]
        basename = posixpath.basename(value.replace("\\", "/").rstrip("/"))
        if full == "credential.helper" or full.endswith(".helper"):
            out.append(_note_line("#", line))
            notes.append(f"注释 Windows 专用 credential.helper：{value}")
            continue
        if full in {"core.sshcommand"}:
            # 保留 section / 缩进，值换成 WSL 自带 ssh；命令行里常带 Windows 引号，
            # 原样保留反而更危险，所以只保留可执行的 ssh。
            out.append(f"{indent}sshCommand = ssh")
            notes.append(f"把 core.sshCommand 的 Windows ssh 换成 ssh：{value}")
            continue
        if full in {"core.excludesfile", "core.attributesfile"} and basename.startswith("."):
            out.append(f"{indent}{key} = ~/{basename}")
            notes.append(f"把 {key} 的 Windows 路径改成 ~/{basename}")
            continue
        if full == "include.path" or full.startswith("includeif.") or full in _GIT_DROP_KEYS:
            out.append(_note_line("#", line))
            notes.append(f"注释 Windows 专用配置 {key}：{value}")
            continue
        # 别名 / diff / merge / filter 命令里带 Windows 可执行文件时同样不可用。
        out.append(_note_line("#", line))
        notes.append(f"注释含 Windows 路径的配置 {key}：{value}")
    return "\n".join(out).encode("utf-8", "surrogateescape")


def _ssh_adapt(data: bytes, notes: list[str]) -> bytes:
    out: list[str] = []
    for line in normalize_newlines(data).decode("utf-8", "surrogateescape").split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            out.append(line)
            continue
        parts = stripped.split(None, 1)
        key = parts[0].lower()
        value = parts[1].strip() if len(parts) > 1 else ""
        if not value or not has_windows_path(value):
            out.append(line)
            continue
        indent = line[: len(line) - len(line.lstrip())]
        basename = posixpath.basename(value.strip('"').replace("\\", "/").rstrip("/"))
        if key in _SSH_PATH_KEYS and basename:
            if key == "identityfile":
                out.append(f"{indent}IdentityFile ~/.ssh/{basename}")
                notes.append(f"把 IdentityFile 的 Windows 路径改成 ~/.ssh/{basename}")
            elif key == "certificatefile":
                out.append(f"{indent}CertificateFile ~/.ssh/{basename}")
                notes.append(f"把 CertificateFile 的 Windows 路径改成 ~/.ssh/{basename}")
            else:
                out.append(f"{indent}{parts[0]} ~/.ssh/known_hosts")
                notes.append(f"把 {parts[0]} 的 Windows 路径改成 ~/.ssh/known_hosts")
            continue
        if key in _SSH_DROP_KEYS:
            out.append(_note_line("#", line))
            notes.append(f"注释 Windows 专用 SSH 配置 {parts[0]}：{value}")
            continue
        out.append(_note_line("#", line))
        notes.append(f"注释含 Windows 路径的 SSH 配置 {parts[0]}：{value}")
    return "\n".join(out).encode("utf-8", "surrogateescape")


def adapt(group: str, path: str, data: bytes) -> Adaptation:
    """按项目适配一个待写入文件；返回新内容与实际改动说明。

    只处理文本配置：SSH 私钥 / 公钥原样写入，绝不做换行转换（二进制密钥不能被改）。
    """
    name = posixpath.basename(path).casefold()
    git_config = group == "git" and (name in _GIT_KNOWN_FILES or path.startswith(".config/git/"))
    ssh_config = group == "ssh" and name == "config"
    shell_config = group == "shell"
    if not (git_config or ssh_config or shell_config):
        return Adaptation(data)
    normalized = normalize_newlines(data)
    notes: list[str] = []
    if normalized != data:
        notes.append("把 Windows 换行（CRLF）转成 LF")
    if git_config:
        result = _git_adapt(normalized, notes)
    elif ssh_config:
        result = _ssh_adapt(normalized, notes)
    else:
        result = normalized
    return Adaptation(result, tuple(dict.fromkeys(notes)))


def adapt_notes(group: str, path: str, data: bytes) -> tuple[str, ...]:
    """只取改动说明（扫描时给提示用），不保留适配后的内容。"""
    return adapt(group, path, data).notes
