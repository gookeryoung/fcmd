"""实例内探测时排除 Windows 互操作路径（``/mnt/c``…）。

WSL 默认把 Windows 的 PATH 追加进实例（``appendWindowsPath=true``），于是没装
Linux 版 Node.js 的实例里 ``command -v node`` 会命中
``/mnt/c/Program Files/nodejs/node.exe``。把它当成「实例里已装」会误导用户：
用这个 npm 装的 ``pi`` 落在 Windows 侧，实例里启动时只报「找不到命令」。

这里提供两件东西：

- :func:`drop_windows_path_lines`：一段 POSIX 片段，把 ``/mnt/*`` 从 PATH 摘掉
  （摘掉的目录留存到 ``FCMD_WINDOWS_PATH``，需要时仍可查 Windows 侧版本）；
- :func:`is_windows_mount`：Python 侧兜底过滤（解析结果与历史缓存都要过一遍）。
"""

from __future__ import annotations

__all__ = ["WINDOWS_PATH_VAR", "drop_windows_path_lines", "is_windows_mount"]

WINDOWS_PATH_VAR = "FCMD_WINDOWS_PATH"
"""摘下来的 Windows 互操作目录集合（``:`` 分隔），供「Windows 侧有、实例内没有」提示用。"""


def is_windows_mount(path: str) -> bool:
    """``path`` 是否指向 Windows 互操作目录（``/mnt/...`` 或 ``C:\\...``）。"""
    text = path.strip().replace("\\", "/")
    if not text:
        return False
    folded = text.casefold()
    if folded == "/mnt" or folded.startswith("/mnt/"):
        return True
    # 兜底：万一探测输出（或手工填的命令模板）给的是盘符路径。
    return len(folded) > 2 and folded[1] == ":" and folded[0].isalpha() and folded[2] == "/"


def drop_windows_path_lines() -> tuple[str, ...]:
    """定义并调用 ``fcmd_drop_windows_path`` 的 POSIX 片段。

    不能用 ``command -v`` 之后再过路径：它只返回**第一个**命中，Windows 目录一旦
    排在前面就永远看不到实例内的同名可执行文件，必须先改 PATH 再探测。
    """
    return (
        "fcmd_drop_windows_path() {",
        '    _rest="$PATH"',
        '    _keep=""',
        '    _win=""',
        '    while [ -n "$_rest" ]; do',
        '        _dir="${_rest%%:*}"',
        '        if [ "$_dir" = "$_rest" ]; then _rest=""; else _rest="${_rest#*:}"; fi',
        '        case "$_dir" in',
        '            /mnt/*) _win="${_win:+$_win:}$_dir" ;;',
        '            "") ;;',
        '            *) _keep="${_keep:+$_keep:}$_dir" ;;',
        "        esac",
        "    done",
        f'    {WINDOWS_PATH_VAR}="$_win"',
        f"    export {WINDOWS_PATH_VAR}",
        '    PATH="$_keep"',
        "    export PATH",
        "    unset _rest _keep _win _dir",
        "}",
        "fcmd_drop_windows_path",
    )
