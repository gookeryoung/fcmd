"""``wslman._decode``：控制台解码与 wsl.exe 文本解析。"""

from __future__ import annotations

import pytest

from fcmd.apps.wslman._decode import (
    decode_console,
    find_pair,
    is_no_distribution_message,
    is_not_enabled_message,
    parse_distro_list,
    parse_online_list,
    parse_status,
    parse_wsl_version,
)
from tests.apps.wslman._fakes import utf16

_TABLE = """\
  NAME            STATE           VERSION
* Ubuntu          Running         2
  Debian          Stopped         1
  Ubuntu Preview  Paused          2
"""


class TestDecodeConsole:
    def test_empty(self) -> None:
        assert decode_console(b"") == ""

    def test_utf16_with_bom(self) -> None:
        assert decode_console(utf16("NAME STATE")) == "NAME STATE"

    def test_utf16_without_bom(self) -> None:
        raw = "Ubuntu  Running  2".encode("utf-16-le")
        assert decode_console(raw) == "Ubuntu  Running  2"

    def test_utf8(self) -> None:
        assert decode_console("名称 状态".encode()) == "名称 状态"

    def test_fallback_for_invalid_utf8(self) -> None:
        text = decode_console(b"\x81\x40")
        assert isinstance(text, str)

    def test_strips_ansi_nul_and_bom(self) -> None:
        raw = b"\x1b[31mhello\x1b[0m\x00" + "\ufeff".encode()
        assert decode_console(raw) == "hello"


class TestParseDistroList:
    def test_rows_and_default_marker(self) -> None:
        rows = parse_distro_list(_TABLE)
        assert [row.name for row in rows] == ["Ubuntu", "Debian", "Ubuntu Preview"]
        assert rows[0].default is True
        assert rows[0].state == "running"
        assert rows[0].version == 2
        assert rows[1].version == 1

    def test_unknown_state_is_preserved(self) -> None:
        row = parse_distro_list("  Ubuntu Preview  Paused  2")[0]
        assert row.state == "unknown"
        assert row.state_raw == "Paused"

    def test_header_and_malformed_lines_are_skipped(self) -> None:
        assert parse_distro_list("NAME STATE VERSION\n名称 状态 版本\ntoo short\n") == ()

    def test_duplicates_are_skipped(self) -> None:
        rows = parse_distro_list("  Ubuntu  Running  2\n  ubuntu  Stopped  2\n")
        assert len(rows) == 1

    def test_empty(self) -> None:
        assert parse_distro_list("") == ()


class TestParseOnlineList:
    def test_multi_space_columns(self) -> None:
        rows = parse_online_list("NAME  FRIENDLY NAME\nUbuntu-22.04  Ubuntu 22.04 LTS\ndocker-desktop\n")
        assert [row.name for row in rows] == ["Ubuntu-22.04", "docker-desktop"]
        assert rows[0].friendly_name == "Ubuntu 22.04 LTS"

    def test_tab_columns_and_single_token(self) -> None:
        rows = parse_online_list("Ubuntu\tUbuntu\ndocker-desktop\n")
        assert rows[0].friendly_name == "Ubuntu"
        assert rows[1].friendly_name == ""

    def test_intro_sentence_is_skipped(self) -> None:
        text = "以下是一个可安装的有效发行版列表，可通过 wsl --install -d 安装\nNAME  FRIENDLY NAME\nUbuntu  Ubuntu\n"
        rows = parse_online_list(text)
        assert [row.name for row in rows] == ["Ubuntu"]

    def test_localized_header_is_skipped(self) -> None:
        assert parse_online_list("名称  友好名称\nUbuntu  Ubuntu\n")[0].name == "Ubuntu"

    def test_duplicates_are_skipped(self) -> None:
        assert len(parse_online_list("Ubuntu  Ubuntu\nubuntu  Ubuntu\n")) == 1


class TestParseMisc:
    def test_wsl_version(self) -> None:
        assert parse_wsl_version("WSL 版本： 2.3.26.0\n内核版本： 5.15.90.1") == "2.3.26.0"

    def test_wsl_version_missing(self) -> None:
        assert parse_wsl_version("没有版本号") is None

    def test_status_pairs_and_find(self) -> None:
        pairs = parse_status("默认版本: 2\n默认分发版: Ubuntu\n无冒号的行\n")
        assert ("默认版本", "2") in pairs
        assert find_pair(pairs, ["默认分发版", "Default Distribution"]) == "Ubuntu"
        assert find_pair(pairs, ["不存在"]) is None

    @pytest.mark.parametrize(
        "text",
        [
            "Windows Subsystem for Linux has no installed distributions.",
            "适用于 Linux 的 Windows 子系统没有已安装的分发版。",
        ],
    )
    def test_no_distribution_messages(self, text: str) -> None:
        assert is_no_distribution_message(text)

    @pytest.mark.parametrize(
        "text",
        [
            "Please enable the Virtual Machine Platform Windows feature and ensure virtualization is enabled.",
            "WSL 2 需要更新其内核组件。",
            "WSL is not installed",
        ],
    )
    def test_not_enabled_messages(self, text: str) -> None:
        assert is_not_enabled_message(text)

    def test_messages_do_not_cross_match(self) -> None:
        assert not is_no_distribution_message("Ubuntu Running 2")
        assert not is_not_enabled_message("Ubuntu Running 2")
