"""colortool 工具测试（DSL 内建动作声明 commands/colortool.toml）。

验证 ``fcmd colortool`` 的 DSL action 迁移语义：
- 工具注册（多子命令 DSL 工具，内置声明）
- 声明契约：__dsl_action__ 标记、无 cmd（fn 任务形态）
- hex2rgb/rgb2hex/rgb2hsl/hsl2rgb 四子命令往返一致与错误分支
"""

from __future__ import annotations

import pytest

from fcmd.apis._tool_args import ToolSpec
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli._discovery import ensure_tools_discovered

ensure_tools_discovered()


def _run_and_capture(name: str, argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = run_tool(name, argv)
    raw = capsys.readouterr().out
    # 过滤掉控制台帧（以 > 或 OK/FAILED 开头的行），保留动作 print 输出
    lines = [
        ln
        for ln in raw.splitlines()
        if not ln.startswith("> ") and not ln.startswith("OK ") and not ln.startswith("FAILED ")
    ]
    return code, "\n".join(lines)


# ====================================================================== #
# 注册与声明契约
# ====================================================================== #
class TestColortoolRegistration:
    def test_subcommands(self) -> None:
        assert set(_TOOL_REGISTRY["colortool"]) == {"hex2rgb", "rgb2hex", "rgb2hsl", "hsl2rgb"}

    @pytest.mark.parametrize(
        "sub, action",
        [
            ("hex2rgb", "colortool_hex2rgb"),
            ("rgb2hex", "colortool_rgb2hex"),
            ("rgb2hsl", "colortool_rgb2hsl"),
            ("hsl2rgb", "colortool_hsl2rgb"),
        ],
    )
    def test_action_contract(self, sub: str, action: str) -> None:
        spec: ToolSpec = _TOOL_REGISTRY["colortool"][sub]
        assert spec.cmd is None
        assert getattr(spec.func, "__dsl_action__", None) == action


# ====================================================================== #
# hex2rgb
# ====================================================================== #
class TestHex2Rgb:
    def test_known(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("colortool", ["hex2rgb", "#ff5733"], capsys)
        assert code == 0
        assert out.strip() == "255 87 51"

    def test_no_hash_prefix(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("colortool", ["hex2rgb", "ff5733"], capsys)
        assert code == 0
        assert out.strip() == "255 87 51"

    def test_case_insensitive(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("colortool", ["hex2rgb", "#FF5733"], capsys)
        assert code == 0
        assert out.strip() == "255 87 51"

    def test_invalid_length_shows_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("colortool", ["hex2rgb", "#fff"], capsys)
        assert code == 0
        assert "HEX 颜色须为 6 位" in out

    def test_invalid_chars_shows_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("colortool", ["hex2rgb", "#gg5733"], capsys)
        assert code == 0
        assert "HEX 颜色含非十六进制字符" in out


# ====================================================================== #
# rgb2hex
# ====================================================================== #
class TestRgb2Hex:
    def test_known(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("colortool", ["rgb2hex", "255", "87", "51"], capsys)
        assert code == 0
        assert out.strip() == "#ff5733"

    def test_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("colortool", ["rgb2hex", "0", "0", "0"], capsys)
        assert code == 0
        assert out.strip() == "#000000"

    def test_out_of_range_shows_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("colortool", ["rgb2hex", "256", "0", "0"], capsys)
        assert code == 0
        assert "超出 0-255" in out


# ====================================================================== #
# rgb2hsl + hsl2rgb 往返
# ====================================================================== #
class TestColorRoundTrip:
    def test_black(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("colortool", ["rgb2hsl", "0", "0", "0"], capsys)
        assert code == 0
        _, _, lightness = out.strip().split()
        assert float(lightness) == 0.0

        code, out = _run_and_capture("colortool", ["hsl2rgb", "0", "0", "0"], capsys)
        assert code == 0
        assert out.strip() == "0 0 0"

    def test_white(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("colortool", ["rgb2hsl", "255", "255", "255"], capsys)
        assert code == 0
        _, _, lightness = out.strip().split()
        assert float(lightness) == 100.0

    def test_red(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("colortool", ["rgb2hsl", "255", "0", "0"], capsys)
        assert code == 0
        h, s, lightness = out.strip().split()
        assert float(h) == 0.0
        assert float(s) == 100.0
        assert float(lightness) == 50.0

    def test_hsl2rgb_out_of_range(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("colortool", ["hsl2rgb", "400", "50", "50"], capsys)
        assert code == 0
        assert "须在 0-360" in out
