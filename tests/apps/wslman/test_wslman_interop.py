"""Windows 互操作路径过滤测试：``/mnt/c`` 下的可执行文件不算实例内已装。"""

from __future__ import annotations

from fcmd.apps.wslman import _interop


def test_is_windows_mount_covers_mnt_and_drive_paths() -> None:
    assert _interop.is_windows_mount("/mnt/c/Program Files/nodejs/node") is True
    assert _interop.is_windows_mount("/mnt") is True
    assert _interop.is_windows_mount("/mnt/") is True
    assert _interop.is_windows_mount("C:/Users/me/AppData/Roaming/npm/pi") is True
    assert _interop.is_windows_mount(r"C:\Users\me\AppData\Roaming\npm\pi") is True
    assert _interop.is_windows_mount("/home/me/.nvm/versions/node/v20/bin/node") is False
    assert _interop.is_windows_mount("/usr/local/bin/pi") is False
    assert _interop.is_windows_mount("") is False


def test_drop_windows_path_lines_keeps_linux_entries_and_saves_mnt() -> None:
    lines = _interop.drop_windows_path_lines()
    script = "\n".join(lines)
    assert lines[0] == "fcmd_drop_windows_path() {"
    assert lines[-1] == "fcmd_drop_windows_path"
    assert "/mnt/*" in script
    assert f'{_interop.WINDOWS_PATH_VAR}="$_win"' in script
    # PATH 被重写后必须导出，否则后面的 command -v 仍看到旧的 Windows 目录。
    assert 'PATH="$_keep"' in script
    assert "export PATH" in script
