"""colortool 工具测试（DSL 内建动作声明 commands/colortool.toml）。

验证 ``fcmd colortool`` 的 DSL action 迁移语义：
- 工具注册（多子命令 DSL 工具，内置声明）
- 声明契约：__dsl_action__ 标记、无 cmd（fn 任务形态）
- hex2rgb/rgb2hex/rgb2hsl/hsl2rgb 四子命令往返一致与错误分支
- coloraide 惰性导入与缺库降级
"""

from __future__ import annotations

import pytest

from fcmd.cli._discovery import ensure_tools_discovered
from fcmd.core.toolkit import _TOOL_REGISTRY
from fcmd.core.toolkit.args import ToolSpec
from fcmd.dsl.actions import conv
from fcmd.dsl.actions.conv import _require_coloraide
from tests.helpers import run_and_capture as _run_and_capture

ensure_tools_discovered()


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


# ====================================================================== #
# coloraide 换算与往返
# ====================================================================== #
class TestColoraideConversion:
    """coloraide 换算结果与输出格式（位数/取整）锁定。"""

    def test_require_coloraide_returns_class(self) -> None:
        """已安装时返回 coloraide.Color。"""
        from coloraide import Color

        assert _require_coloraide() is Color

    def test_known_hsl(self, capsys: pytest.CaptureFixture[str]) -> None:
        """#ff5733 的 HSL 为 10.59/100/60。"""
        code, out = _run_and_capture("colortool", ["rgb2hsl", "255", "87", "51"], capsys)
        assert code == 0
        assert out.strip() == "10.59 100.0 60.0"

    def test_gray_hue_normalized_to_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        """灰阶无定义色相被归零（不输出 nan）。"""
        code, out = _run_and_capture("colortool", ["rgb2hsl", "128", "128", "128"], capsys)
        assert code == 0
        hue, sat, light = (float(v) for v in out.strip().split())
        assert hue == 0.0
        assert sat == 0.0
        assert light == 50.2

    @pytest.mark.parametrize("hex_color", ["#ff5733", "#000000", "#ffffff", "#00ff00", "#123456"])
    def test_hex_rgb_round_trip(self, hex_color: str, capsys: pytest.CaptureFixture[str]) -> None:
        """HEX → RGB → HEX 完全一致。"""
        code, out = _run_and_capture("colortool", ["hex2rgb", hex_color], capsys)
        assert code == 0
        r, g, b = out.split()
        code, out = _run_and_capture("colortool", ["rgb2hex", r, g, b], capsys)
        assert code == 0
        assert out.strip() == hex_color

    @pytest.mark.parametrize(("r", "g", "b"), [("255", "87", "51"), ("0", "0", "0"), ("18", "52", "86")])
    def test_rgb_hsl_round_trip(self, r: str, g: str, b: str, capsys: pytest.CaptureFixture[str]) -> None:
        """RGB → HSL → RGB 误差不超过 1（HSL 保留两位小数）。"""
        code, out = _run_and_capture("colortool", ["rgb2hsl", r, g, b], capsys)
        assert code == 0
        h, s, light = out.split()
        code, out = _run_and_capture("colortool", ["hsl2rgb", h, s, light], capsys)
        assert code == 0
        back = [int(v) for v in out.split()]
        assert all(abs(a - int(e)) <= 1 for a, e in zip(back, (r, g, b), strict=True))

    def test_missing_coloraide_prints_hint(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """缺库时打印 extra 安装提示，退出码仍为 0。"""
        monkeypatch.setattr(conv, "_require_coloraide", lambda: None)
        code, out = _run_and_capture("colortool", ["hex2rgb", "#ff5733"], capsys)
        assert code == 0
        assert "pip install fcmd[color]" in out

    def test_missing_coloraide_keeps_other_actions(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """缺库不影响其他动作（casetool）注册与执行。"""
        monkeypatch.setattr(conv, "_require_coloraide", lambda: None)
        code, out = _run_and_capture("casetool", ["snake", "HelloWorld"], capsys)
        assert code == 0
        assert out.strip() == "hello_world"
