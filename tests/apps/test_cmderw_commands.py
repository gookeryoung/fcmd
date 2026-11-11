"""用户命令纯逻辑测试：占位符展开、命令模型与持久化容错。"""

from __future__ import annotations

from fcmd.apps.cmderw._cmderw_commands import (
    COMMAND_LIMIT,
    PLACEHOLDERS,
    CommandContext,
    PlaceholderError,
    UserCommand,
    expand_placeholders,
    normalize_user_commands,
    user_commands_to_json,
)

CONTEXT = CommandContext(
    active_dir="/data/work",
    other_dir="/data/backup",
    selected=("a.txt", "b c.txt"),
    selected_paths=("/data/work/a.txt", "/data/work/b c.txt"),
)


class TestPlaceholders:
    def test_expand_directories(self) -> None:
        assert expand_placeholders("cp %P/x %T/", CONTEXT) == "cp /data/work/x /data/backup/"

    def test_expand_first_selected_name(self) -> None:
        assert expand_placeholders("echo %N", CONTEXT) == "echo a.txt"

    def test_expand_selected_lists_are_quoted(self) -> None:
        assert expand_placeholders("tool %S", CONTEXT) == 'tool "a.txt" "b c.txt"'
        assert expand_placeholders("tool %L", CONTEXT) == 'tool "/data/work/a.txt" "/data/work/b c.txt"'

    def test_expand_empty_selection(self) -> None:
        empty = CommandContext(active_dir="/a", other_dir="/b")
        assert expand_placeholders("[%N][%S][%L]", empty) == "[][][]"

    def test_expand_escaped_percent(self) -> None:
        assert expand_placeholders("echo 100%%", CONTEXT) == "echo 100%"
        assert expand_placeholders("%%%P", CONTEXT) == "%/data/work"

    def test_quote_escapes_inner_double_quote(self) -> None:
        context = CommandContext(active_dir="/a", selected=('say "hi".txt',))
        assert expand_placeholders("%S", context) == '"say \\"hi\\".txt"'

    def test_plain_text_untouched(self) -> None:
        assert expand_placeholders("git status", CONTEXT) == "git status"
        assert expand_placeholders("", CONTEXT) == ""

    def test_unknown_placeholder_reports_token_and_position(self) -> None:
        try:
            expand_placeholders("ab%X", CONTEXT)
        except PlaceholderError as exc:
            assert exc.token == "%X"
            assert exc.position == 2
        else:  # pragma: no cover - 断言分支
            raise AssertionError("应当抛出 PlaceholderError")

    def test_trailing_percent_is_rejected(self) -> None:
        try:
            expand_placeholders("echo %", CONTEXT)
        except PlaceholderError as exc:
            assert exc.token == "%"
        else:  # pragma: no cover - 断言分支
            raise AssertionError("应当抛出 PlaceholderError")

    def test_placeholders_table_matches_supported_keys(self) -> None:
        assert set(PLACEHOLDERS) == {"P", "T", "N", "S", "L", "%"}


class TestUserCommandModel:
    def test_full_command_joins_parameters(self) -> None:
        assert UserCommand("v", "code", " %P").full_command == "code %P"
        assert UserCommand("v", "code").full_command == "code"

    def test_to_dict_roundtrip_fields(self) -> None:
        command = UserCommand("v", "code", "%P", in_menu=True, in_toolbar=False, in_context=False)
        data = command.to_dict()
        assert data == {
            "name": "v",
            "command": "code",
            "parameters": "%P",
            "in_menu": True,
            "in_toolbar": False,
            "in_context": False,
        }
        assert normalize_user_commands([data]) == [command]


class TestNormalization:
    def test_non_list_is_empty(self) -> None:
        assert normalize_user_commands("bad") == []
        assert normalize_user_commands(None) == []
        assert normalize_user_commands({}) == []

    def test_drops_invalid_entries(self) -> None:
        raw = [
            "bad",
            {"name": "", "command": "x"},
            {"name": "  ", "command": "x"},
            {"name": "a"},
            {"name": "b", "command": "   "},
            {"name": "ok", "command": "  code  ", "parameters": 3},
        ]
        assert normalize_user_commands(raw) == [UserCommand(name="ok", command="code")]

    def test_duplicate_names_keep_first(self) -> None:
        raw = [
            {"name": "dup", "command": "first"},
            {"name": "dup", "command": "second"},
        ]
        assert [command.command for command in normalize_user_commands(raw)] == ["first"]

    def test_flags_default_true_and_non_bool_falls_back(self) -> None:
        raw = [{"name": "a", "command": "x", "in_menu": "no", "in_toolbar": False}]
        command = normalize_user_commands(raw)[0]
        assert command.in_menu is True
        assert command.in_toolbar is False
        assert command.in_context is True

    def test_limit_is_applied(self) -> None:
        raw = [{"name": f"c{index}", "command": "x"} for index in range(COMMAND_LIMIT + 5)]
        assert len(normalize_user_commands(raw)) == COMMAND_LIMIT

    def test_serialization_of_empty_list(self) -> None:
        assert user_commands_to_json([]) == []
        assert normalize_user_commands(user_commands_to_json([])) == []
