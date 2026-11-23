"""「用 IDE / AI Agent 打开」的纯逻辑测试：预设、模板校验、路径占位与设置映射。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from fcmd.apps.wslman import _open, wslmanw
from fcmd.apps.wslman._errors import WslmanError

UNC = r"\\wsl.localhost\Ubuntu\home\me\proj"


def test_builtin_presets_cover_expected_tools() -> None:
    """内置 IDE / Agent 的 key 与顺序固定；未知类型没有预设。"""
    ide = _open.default_openers(_open.KIND_IDE)
    assert [item.key for item in ide] == ["vscode", "trae", "qoder", "workbuddy"]
    agent = _open.default_openers(_open.KIND_AGENT)
    assert [item.key for item in agent] == ["pi", "opencode"]
    assert all(item.builtin for item in ide + agent)
    assert all(item.note for item in ide + agent)
    assert _open.default_openers("other") == ()


def test_mapping_roundtrip_keeps_editable_fields() -> None:
    opener = _open.Opener("mine", "我的 IDE", _open.KIND_IDE, _open.HOST_WINDOWS, 'x "{dir}"')
    mapping = _open.to_mapping(opener)
    assert mapping == {
        "key": "mine",
        "name": "我的 IDE",
        "host": "windows",
        "command": 'x "{dir}"',
        "origin": _open.ORIGIN_DEFAULT,
    }
    assert _open.parse_mappings([mapping], _open.KIND_IDE) == (opener,)


def test_parse_mappings_skips_bad_rows_and_duplicates() -> None:
    rows = [
        {"key": "bad-host", "name": "X", "host": "mac", "command": "x {dir}"},
        {"key": "no-path", "name": "Y", "host": "windows", "command": "x"},
        {"key": "dup", "name": "Z", "host": "windows", "command": "z {dir}"},
        {"key": "dup", "name": "Z2", "host": "windows", "command": "z2 {dir}"},
        "not-a-mapping",
        {"key": "", "name": "E", "host": "windows", "command": "e {dir}"},
    ]
    parsed = _open.parse_mappings(rows, _open.KIND_IDE)
    assert [item.key for item in parsed] == ["dup"]
    assert parsed[0].name == "Z"
    assert _open.parse_mappings("nope", _open.KIND_IDE) == ()


def test_new_key_is_stable_and_unique() -> None:
    assert _open.new_key("My Tool", []) == "my-tool"
    assert _open.new_key("my tool", ["my-tool"]) == "my-tool-2"
    assert _open.new_key("  ", []) == "custom"


def test_split_command_keeps_backslashes_inside_quotes() -> None:
    """UNC 路径不能把反斜杠当转义；成对引号只用来保留空格。"""
    tokens = _open.split_command(f'code --remote wsl+Ubuntu "{UNC}"')
    assert tokens == ("code", "--remote", "wsl+Ubuntu", UNC)
    assert _open.split_command("opencode") == ("opencode",)


def test_split_command_rejects_unclosed_quote_and_empty() -> None:
    with pytest.raises(WslmanError):
        _open.split_command('code "{dir}')
    with pytest.raises(WslmanError):
        _open.split_command("   ")


def test_validate_opener_requires_path_for_windows_only() -> None:
    assert _open.validate_opener(" Code ", _open.HOST_WINDOWS, ' code "{dir}" ') == (
        "Code",
        _open.HOST_WINDOWS,
        'code "{dir}"',
    )
    assert _open.validate_opener("TUI", _open.HOST_WSL, "opencode") == ("TUI", _open.HOST_WSL, "opencode")
    with pytest.raises(WslmanError):
        _open.validate_opener("", _open.HOST_WINDOWS, "code {dir}")
    with pytest.raises(WslmanError):
        _open.validate_opener("X", "mac", "code {dir}")
    with pytest.raises(WslmanError):
        _open.validate_opener("X", _open.HOST_WINDOWS, "code")


def test_render_and_argv_replace_placeholders() -> None:
    opener = _open.Opener("v", "V", _open.KIND_IDE, _open.HOST_WINDOWS, 'code --remote wsl+{distro} "{dir}"')
    kwargs = {"dir": "~/proj", "unc": UNC, "distro": "Ubuntu", "home": "/home/me"}
    assert _open.render_command(opener, **kwargs) == 'code --remote wsl+Ubuntu "~/proj"'
    assert _open.windows_argv(opener, **kwargs) == ("code", "--remote", "wsl+Ubuntu", "~/proj")
    assert _open.command_name(opener, **kwargs) == "code"
    assert _open.wsl_command(opener, **kwargs) == 'code --remote wsl+Ubuntu "~/proj"'


def test_unknown_placeholder_is_left_for_user_to_fix() -> None:
    opener = _open.Opener("x", "X", _open.KIND_IDE, _open.HOST_WINDOWS, 'x "{dir}" {other}')
    rendered = _open.render_command(opener, dir="/p", unc=UNC, distro="U", home="/home/u")
    assert rendered == 'x "/p" {other}'


def test_probe_script_quotes_executable() -> None:
    opener = _open.Opener("p", "P", _open.KIND_AGENT, _open.HOST_WSL, '"my tool"')
    script = _open.probe_script(opener, dir="/p", unc=UNC, distro="U", home="/home/u")
    assert script == "command -v -- 'my tool'"


def test_wrap_windows_argv_routes_cmd_through_cmd_exe() -> None:
    wrapped = _open.wrap_windows_argv(("code.cmd", "--remote", "wsl+U", UNC))
    assert wrapped[:4] == ("cmd.exe", "/d", "/s", "/c")
    assert wrapped[4] == subprocess.list2cmdline(["code.cmd", "--remote", "wsl+U", UNC])
    assert _open.wrap_windows_argv(("code.exe", "--x")) == ("code.exe", "--x")
    with pytest.raises(WslmanError):
        _open.wrap_windows_argv(())


def test_settings_default_openers_when_field_missing() -> None:
    settings = wslmanw.settings_from_mapping({})
    assert settings.ide_openers == _open.default_openers(_open.KIND_IDE)
    assert settings.agent_openers == _open.default_openers(_open.KIND_AGENT)
    assert settings.ide_opener == "vscode"
    assert settings.agent_opener == "opencode"
    selected = wslmanw.selected_opener(settings, _open.KIND_IDE)
    assert selected is not None
    assert selected.key == "vscode"


def test_settings_explicit_empty_list_means_user_deleted_everything() -> None:
    settings = wslmanw.settings_from_mapping(
        {"openers": {"ide": [], "agent": [], "selected": {"ide": "gone", "agent": 3}}}
    )
    assert settings.ide_openers == ()
    assert settings.agent_openers == ()
    assert settings.ide_opener == ""
    assert settings.agent_opener == ""
    assert wslmanw.selected_opener(settings, _open.KIND_IDE) is None


def test_settings_roundtrip_and_invalid_selection_falls_back() -> None:
    custom = _open.Opener("mine", "Mine", _open.KIND_IDE, _open.HOST_WINDOWS, 'x "{unc}"')
    settings = wslmanw.WslmanwSettings(ide_openers=(custom,), ide_opener="mine")
    restored = wslmanw.settings_from_mapping(wslmanw.settings_to_mapping(settings))
    assert restored.ide_openers == (custom,)
    assert restored.ide_opener == "mine"

    stale = wslmanw.settings_from_mapping(
        wslmanw.settings_to_mapping(wslmanw.WslmanwSettings(ide_openers=(custom,), ide_opener="gone"))
    )
    assert stale.ide_opener == "mine"


def test_origin_roundtrip_and_legacy_mappings() -> None:
    """``origin`` 决定检测能否覆盖；旧设置按内置 / 自定义推断。"""
    user = _open.Opener("mine", "Mine", _open.KIND_IDE, _open.HOST_WINDOWS, 'x "{dir}"', origin=_open.ORIGIN_USER)
    restored = _open.parse_mappings([_open.to_mapping(user)], _open.KIND_IDE)[0]
    assert restored.user_edited is True
    detected = _open.Opener(
        "vscode", "VSCode", _open.KIND_IDE, _open.HOST_WINDOWS, 'code "{dir}"', origin=_open.ORIGIN_DETECTED
    )
    assert _open.to_mapping(detected)["origin"] == _open.ORIGIN_DETECTED

    # 旧设置没有 origin：内置项可被检测继续补全，自定义条目视为用户手填。
    legacy = [
        {"key": "vscode", "name": "VSCode", "host": "windows", "command": 'code "{dir}"'},
        {"key": "mine", "name": "Mine", "host": "windows", "command": 'x "{dir}"'},
    ]
    parsed = {item.key: item for item in _open.parse_mappings(legacy, _open.KIND_IDE)}
    assert parsed["vscode"].origin == _open.ORIGIN_DEFAULT
    assert parsed["mine"].origin == _open.ORIGIN_USER


def test_detect_host_prefers_path_then_common_install_dirs(tmp_path: Path) -> None:
    """宿主机检测：PATH 优先，其次 LOCALAPPDATA / Program Files 下的常见位置。"""
    from_path = _open.detect_host("vscode", env={}, which=lambda _name: r"C:\tools\code.cmd")
    assert from_path.found is True
    assert from_path.path == r"C:\tools\code.cmd"
    assert from_path.command == r'C:\tools\code.cmd --remote wsl+{distro} "{dir}"'

    installed = tmp_path / "Programs" / "Microsoft VS Code" / "bin" / "code.cmd"
    installed.parent.mkdir(parents=True)
    installed.write_text("")
    detected = _open.detect_host(
        "vscode",
        env={"LOCALAPPDATA": str(tmp_path)},
        which=lambda _name: None,
    )
    assert detected.found is True
    assert detected.path == str(installed)
    assert detected.command.startswith(f'"{installed}"')
    assert "--remote wsl+{distro}" in detected.command

    missing = _open.detect_host("trae", env={"LOCALAPPDATA": str(tmp_path)}, which=lambda _name: None)
    assert missing.found is False
    assert missing.command == ""
    assert missing.host == _open.HOST_WINDOWS


def test_windows_candidates_cover_common_roots(tmp_path: Path) -> None:
    env = {
        "LOCALAPPDATA": str(tmp_path / "local"),
        "APPDATA": str(tmp_path / "roaming"),
        "ProgramFiles": str(tmp_path / "pf"),
    }
    candidates = [str(item) for item in _open.windows_candidates("opencode", env)]
    assert str(tmp_path / "roaming" / "npm" / "opencode.cmd") in candidates
    assert str(tmp_path / "local" / "npm" / "opencode.cmd") in candidates
    assert str(tmp_path / "pf" / "npm" / "opencode.cmd") in candidates


def test_parse_detect_and_detect_wsl() -> None:
    """实例内 ``command -v`` 输出解析：忽略横幅，路径含空格时命令加引号。"""
    text = "profile banner\nOPEN=code|/usr/bin/code\nOPEN=opencode|/opt/AI Tools/opencode\n"
    found = _open.parse_detect(text)
    assert found == {"code": "/usr/bin/code", "opencode": "/opt/AI Tools/opencode"}

    code = _open.detect_wsl("vscode", found)
    assert code.found is True
    assert code.host == _open.HOST_WSL
    assert code.command == '/usr/bin/code --remote wsl+{distro} "{dir}"'

    opencode = _open.detect_wsl("opencode", found)
    assert opencode.command == '"/opt/AI Tools/opencode"'

    assert _open.detect_wsl("trae", found).found is False
    script = _open.detect_script()
    assert "for cmd in" in script
    assert "opencode" in script
    assert "command -v" in script


def test_with_openers_updates_only_the_requested_kind() -> None:
    custom = _open.Opener("mine", "Mine", _open.KIND_IDE, _open.HOST_WINDOWS, 'x "{dir}"')
    settings = wslmanw.with_openers(wslmanw.WslmanwSettings(), _open.KIND_IDE, (custom,), "missing")
    assert settings.ide_openers == (custom,)
    assert settings.ide_opener == "mine"
    assert settings.agent_openers == _open.default_openers(_open.KIND_AGENT)

    cleared = wslmanw.with_openers(settings, _open.KIND_AGENT, (), "opencode")
    assert cleared.agent_openers == ()
    assert cleared.agent_opener == ""
    assert wslmanw.selected_opener(cleared, _open.KIND_AGENT) is None
