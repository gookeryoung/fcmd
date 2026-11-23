"""wslmanw「迁移配置文件」的探测、写入与还原脚本。

从 :mod:`fcmd.apps.wslman._configfiles` 拆出：只负责生成实例内只读探测脚本、
解析探测结果、生成单文件写入脚本、解析写入结果，以及生成「还原上次迁移」脚本。
拆开是为了让两个模块都留在仓库的单文件行数预算内，同时保持「界面 / 纯逻辑」边界不变。
"""

from __future__ import annotations

import base64
import binascii
import posixpath
import shlex

from fcmd.apps.wslman._configfiles import (
    _DEFAULT_DIR_MODE,
    _DIR_PATHS,
    _MANAGED_PATHS,
    _PROBE_PREFIX,
    WRITE_PREFIX,
    ConfigTarget,
    RemoteFile,
    RestoreRecord,
    group_for_path,
)
from fcmd.apps.wslman._errors import runtime_error

__all__ = [
    "BACKUP_PREFIX",
    "parse_backup_path",
    "parse_probe",
    "parse_write_meta",
    "parse_write_result",
    "probe_script",
    "remote_read_argv",
    "restore_script",
    "write_script",
]

BACKUP_PREFIX = "WSLCFG_BACKUP="
"""写入脚本覆盖前备份时输出的一行：备份路径（相对 ``$HOME``，base64）。"""


def remote_read_argv(path: str) -> tuple[str, str, str]:
    """读取来源实例文件的 argv：``cat`` 直接输出文件流，不依赖实例里有 rsync。"""
    return ("cat", "--", path)


def probe_script() -> str:
    """只读探测实例 ``$HOME`` 下受管配置文件（路径 base64，权限 / 大小原样）。"""
    file_loop = "\n".join(
        (
            '    for file in "$dir"/*; do',
            '        [ -f "$file" ] || continue',
            '        name="${file##*/}"',
            '        mode="$(stat -c \'%a\' "$file" 2>/dev/null)"',
            '        size="$(stat -c \'%s\' "$file" 2>/dev/null)"',
            '        printf "WSLCFG_FILE=%s|%s|%s\\n" "$(printf \'%s\' "$rel/$name" | base64 | tr -d \'\\n\')" "$mode" "$size"',
            "    done",
        )
    )
    return "\n".join(
        (
            "set +e",
            'printf "WSLCFG_USER=%s\\n" "$(id -un 2>/dev/null)"',
            'printf "WSLCFG_HOME=%s\\n" "$HOME"',
            *(
                line
                for rel in (".ssh",)
                for line in (
                    f"rel={rel}",
                    'dir="$HOME/$rel"',
                    'if [ -d "$dir" ]; then',
                    '    printf "WSLCFG_DIR=%s|%s\\n" "$(printf \'%s\' "$rel" | base64 | tr -d \'\\n\')" "$(stat -c \'%a\' "$dir" 2>/dev/null)"',
                    file_loop,
                    "fi",
                )
            ),
            "emit_file() {",
            '    file="$HOME/$1"',
            '    [ -f "$file" ] || return 0',
            '    mode="$(stat -c \'%a\' "$file" 2>/dev/null)"',
            '    size="$(stat -c \'%s\' "$file" 2>/dev/null)"',
            '    printf "WSLCFG_FILE=%s|%s|%s\\n" "$(printf \'%s\' "$1" | base64 | tr -d \'\\n\')" "$mode" "$size"',
            "}",
            *(f"emit_file {shlex.quote(rel)}" for rel in _MANAGED_PATHS if rel not in _DIR_PATHS),
        )
    )


def _decode_text(raw: str) -> str:
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


def parse_probe(text: str) -> ConfigTarget:
    """解析探测输出；非 ``WSLCFG_`` 行（profile 横幅）忽略。"""
    values: dict[str, str] = {}
    files: list[RemoteFile] = []
    dirs: dict[str, str] = {}
    for line in text.splitlines():
        if not line.startswith(_PROBE_PREFIX) or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key[len(_PROBE_PREFIX) :]
        if key == "FILE":
            raw_path, _, rest = value.partition("|")
            mode, _, size = rest.partition("|")
            files.append(RemoteFile(_decode_text(raw_path), mode.strip(), _decode_size(size)))
            continue
        if key == "DIR":
            raw_path, _, mode = value.partition("|")
            dirs[_decode_text(raw_path)] = mode.strip()
            continue
        values[key] = value.strip()
    return ConfigTarget(
        user=values.get("USER", ""),
        home=values.get("HOME", ""),
        files=tuple(files),
        dirs=dirs,
    )


def _safe_rel(path: str) -> str:
    """只接受受管路径：拒绝绝对路径与 ``..``，避免把文件写到 ``$HOME`` 之外。"""
    normalized = posixpath.normpath(path)
    if path.startswith("/") or normalized.startswith("..") or group_for_path(normalized) is None:
        raise runtime_error(f"不受支持的配置路径：{path}", hint="重新扫描后再迁移")
    return normalized


def write_script(
    path: str,
    mode: int,
    *,
    overwrite: bool,
    backup: str,
    dir_mode: int = _DEFAULT_DIR_MODE,
) -> str:
    """生成写单个配置文件的脚本；内容从 stdin 读入，不出现在命令行里。

    ``overwrite=False`` 时目标已存在会以退出码 3 失败；``overwrite=True`` 时先把
    原文件移到 ``$HOME/<backup>/<path>``，再写入新文件。中间目录按 ``dir_mode``
    建好（SSH 是 700，其余 755）。覆盖时额外输出备份路径，供「还原上次迁移」使用。
    """
    rel = _safe_rel(path)
    quoted_rel = shlex.quote(rel)
    # SSH 的备份目录本身就在 ~/.ssh 下，备份文件名不再重复 .ssh/ 前缀。
    backup_rel = rel[len(".ssh/") :] if backup.startswith(".ssh/") and rel.startswith(".ssh/") else rel
    quoted_backup = shlex.quote(backup)
    quoted_backup_rel = shlex.quote(backup_rel)
    overwrite_flag = 1 if overwrite else 0
    return "\n".join(
        (
            "set -euo pipefail",
            'home="$HOME"',
            "umask 022",
            f'target="$home"/{quoted_rel}',
            'dir="$(dirname "$target")"',
            'mkdir -p "$dir"',
            f'chmod {dir_mode:03o} "$dir"',
            'if [ -e "$target" ] || [ -L "$target" ]; then',
            f'    if [ "{overwrite_flag}" -ne 1 ]; then',
            '        echo "目标已存在且未确认覆盖：$target" >&2',
            "        exit 3",
            "    fi",
            f'    backup="$home"/{quoted_backup}/{quoted_backup_rel}',
            "    n=1",
            '    while [ -e "$backup" ]; do',
            f'        backup="$home"/{quoted_backup}/{quoted_backup_rel}-"$n"',
            "        n=$((n + 1))",
            "    done",
            '    mkdir -p "$(dirname "$backup")"',
            '    chmod 700 "$(dirname "$backup")"',
            '    mv "$target" "$backup"',
            '    echo "已备份原有文件：$backup"',
            '    backup_rel="${backup#"$home"/}"',
            '    printf "WSLCFG_BACKUP=%s\\n" "$(printf \'%s\' "$backup_rel" | base64 | tr -d \'\\n\')"',
            "fi",
            'tmp="$dir/.fcmd-config.$$"',
            "trap 'rm -f \"$tmp\"' EXIT",
            "umask 077",
            'cat > "$tmp"',
            f'chmod {mode:03o} "$tmp"',
            'mv -f "$tmp" "$target"',
            f'chmod {mode:03o} "$target"',
            f'echo "已写入 $target（权限 {mode:03o}）"',
            f'echo "{WRITE_PREFIX}$(stat -c \'%a\' "$target" 2>/dev/null)|$(id -un 2>/dev/null)|$(stat -c \'%s\' "$target" 2>/dev/null)"',
        )
    )


def _decode(raw: str) -> str:
    text = raw.strip()
    if not text:
        return ""
    try:
        return base64.b64decode(text, validate=True).decode("utf-8", errors="replace").strip()
    except (binascii.Error, ValueError):
        return text


def parse_write_meta(text: str) -> tuple[str, str, int]:
    """从写入脚本输出里取 ``(权限, 属主, 字节数)``；没有标记时返回空值。"""
    for line in reversed(text.splitlines()):
        if line.startswith(WRITE_PREFIX):
            fields = line[len(WRITE_PREFIX) :].split("|")
            mode = fields[0].strip() if fields else ""
            owner = fields[1].strip() if len(fields) > 1 else ""
            try:
                size = int(fields[2]) if len(fields) > 2 and fields[2].strip() else 0
            except ValueError:
                size = 0
            return mode, owner, max(size, 0)
    return "", "", 0


def parse_write_result(text: str) -> tuple[str, str]:
    """兼容旧调用：只取 ``(权限, 属主)``。"""
    mode, owner, _size = parse_write_meta(text)
    return mode, owner


def parse_backup_path(text: str) -> str:
    """取覆盖前备份路径（相对 ``$HOME``）；没有备份时返回空串。"""
    for line in reversed(text.splitlines()):
        if line.startswith(BACKUP_PREFIX):
            return _decode(line[len(BACKUP_PREFIX) :])
    return ""


def restore_script(record: RestoreRecord) -> str:
    """生成「还原上次迁移」脚本：覆盖过的从备份恢复，新增的按大小确认后删除。

    脚本可重复执行：备份已经不在但目标存在时视为已还原；只有「备份和目标都缺失」
    或「新增文件在迁移后被修改」才计入失败，最后以失败数作为退出码。
    """
    lines = ["set +e", 'home="$HOME"', "errors=0"]
    for entry in record.entries:
        rel = _safe_rel(entry.path)
        lines.append(f'target="$home"/{shlex.quote(rel)}')
        if entry.existed:
            lines.append(f'backup="$home"/{shlex.quote(entry.backup)}')
            lines += [
                'if [ -e "$backup" ]; then',
                '    mkdir -p "$(dirname "$target")"',
                '    mv -f "$backup" "$target"',
                f'    echo "已还原 {rel}"',
                'elif [ -e "$target" ]; then',
                f'    echo "备份已不在（视为已还原）：{rel}"',
                "else",
                f'    echo "备份不存在且目标缺失：{rel}" >&2',
                "    errors=$((errors + 1))",
                "fi",
            ]
        else:
            lines += [
                'if [ -e "$target" ] || [ -L "$target" ]; then',
                '    size="$(stat -c \'%s\' "$target" 2>/dev/null)"',
                f'    if [ "$size" != "{entry.size}" ]; then',
                f'        echo "目标在迁移后被修改，跳过：{rel}" >&2',
                "        errors=$((errors + 1))",
                "    else",
                '        rm -f "$target"',
                f'        echo "已删除迁移新增的 {rel}"',
                "    fi",
                "fi",
            ]
    lines.append('exit "$errors"')
    return "\n".join(lines)
