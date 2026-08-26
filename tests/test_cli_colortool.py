"""colortool 工具测试。

验证 ``fcmd.cli.conv.colortool`` 模块：
- 工具注册与四子命令结构（hex2rgb/rgb2hex/rgb2hsl/hsl2rgb）
- ``hex_to_rgb``/``rgb_to_hex``/``rgb_to_hsl``/``hsl_to_rgb``
- 已知向量与往返一致性
- CLI 子命令端到端
"""

from __future__ import annotations

import pytest

from fcmd.apis.toolkit import list_subcommands, run_tool
from fcmd.cli.conv.colortool import (
    hex_to_rgb,
    hsl_to_rgb,
    rgb_to_hex,
    rgb_to_hsl,
)


# ============================================================================ #
# 工具注册
# ============================================================================ #
class TestRegistration:
    """工具注册与子命令结构测试。"""

    def test_registered(self) -> None:
        """colortool 已注册到工具表。"""
        from fcmd.apis.toolkit import list_tools

        assert "colortool" in list_tools()

    def test_subcommands(self) -> None:
        """colortool 有 hex2rgb/rgb2hex/rgb2hsl/hsl2rgb 四个子命令。"""
        subs = list_subcommands("colortool")
        assert set(subs) == {"hex2rgb", "rgb2hex", "rgb2hsl", "hsl2rgb"}


# ============================================================================ #
# hex_to_rgb
# ============================================================================ #
class TestHexToRgb:
    """hex_to_rgb 测试。"""

    def test_basic(self) -> None:
        """基本转换。"""
        assert hex_to_rgb("#ff5733") == (255, 87, 51)

    def test_without_hash(self) -> None:
        """不带 # 前缀。"""
        assert hex_to_rgb("ff5733") == (255, 87, 51)

    def test_uppercase(self) -> None:
        """大写十六进制。"""
        assert hex_to_rgb("#FF5733") == (255, 87, 51)

    def test_black(self) -> None:
        """黑色。"""
        assert hex_to_rgb("#000000") == (0, 0, 0)

    def test_white(self) -> None:
        """白色。"""
        assert hex_to_rgb("#ffffff") == (255, 255, 255)

    def test_with_whitespace(self) -> None:
        """含前后空白。"""
        assert hex_to_rgb("  #ff5733  ") == (255, 87, 51)

    def test_invalid_length(self) -> None:
        """长度错误抛 ValueError。"""
        with pytest.raises(ValueError, match="6 位十六进制"):
            hex_to_rgb("#ff")
        with pytest.raises(ValueError, match="6 位十六进制"):
            hex_to_rgb("#ff5733aa")

    def test_invalid_chars(self) -> None:
        """非十六进制字符抛 ValueError。"""
        with pytest.raises(ValueError, match="非十六进制字符"):
            hex_to_rgb("#xy5733")


# ============================================================================ #
# rgb_to_hex
# ============================================================================ #
class TestRgbToHex:
    """rgb_to_hex 测试。"""

    def test_basic(self) -> None:
        """基本转换。"""
        assert rgb_to_hex(255, 87, 51) == "#ff5733"

    def test_black(self) -> None:
        """黑色。"""
        assert rgb_to_hex(0, 0, 0) == "#000000"

    def test_white(self) -> None:
        """白色。"""
        assert rgb_to_hex(255, 255, 255) == "#ffffff"

    def test_lowercase_output(self) -> None:
        """输出为小写。"""
        result = rgb_to_hex(255, 255, 255)
        assert result == result.lower()

    def test_out_of_range(self) -> None:
        """超出范围抛 ValueError。"""
        with pytest.raises(ValueError, match="超出 0-255 范围"):
            rgb_to_hex(256, 0, 0)
        with pytest.raises(ValueError, match="超出 0-255 范围"):
            rgb_to_hex(-1, 0, 0)
        with pytest.raises(ValueError, match="超出 0-255 范围"):
            rgb_to_hex(0, 300, 0)

    def test_boundary_values(self) -> None:
        """边界值 0 和 255 都有效。"""
        assert rgb_to_hex(0, 0, 0) == "#000000"
        assert rgb_to_hex(255, 255, 255) == "#ffffff"


# ============================================================================ #
# rgb_to_hsl
# ============================================================================ #
class TestRgbToHsl:
    """rgb_to_hsl 测试。"""

    def test_red(self) -> None:
        """红色 (255,0,0) → H(0) S(100) L(50)。"""
        h, s, light = rgb_to_hsl(255, 0, 0)
        assert abs(h - 0) < 0.01
        assert abs(s - 100) < 0.01
        assert abs(light - 50) < 0.01

    def test_green(self) -> None:
        """绿色 (0,255,0) → H(120) S(100) L(50)。"""
        h, s, light = rgb_to_hsl(0, 255, 0)
        assert abs(h - 120) < 0.01
        assert abs(s - 100) < 0.01
        assert abs(light - 50) < 0.01

    def test_blue(self) -> None:
        """蓝色 (0,0,255) → H(240) S(100) L(50)。"""
        h, s, light = rgb_to_hsl(0, 0, 255)
        assert abs(h - 240) < 0.01
        assert abs(s - 100) < 0.01
        assert abs(light - 50) < 0.01

    def test_gray(self) -> None:
        """灰色 (128,128,128) → S(0)。"""
        _h, s, _light = rgb_to_hsl(128, 128, 128)
        assert s == 0  # 灰度饱和度为 0

    def test_black(self) -> None:
        """黑色 (0,0,0) → L(0)。"""
        _h, _s, light = rgb_to_hsl(0, 0, 0)
        assert light == 0

    def test_white(self) -> None:
        """白色 (255,255,255) → L(100)。"""
        _h, _s, light = rgb_to_hsl(255, 255, 255)
        assert light == 100

    def test_out_of_range(self) -> None:
        """超出范围抛 ValueError。"""
        with pytest.raises(ValueError, match="超出 0-255 范围"):
            rgb_to_hsl(256, 0, 0)


# ============================================================================ #
# hsl_to_rgb
# ============================================================================ #
class TestHslToRgb:
    """hsl_to_rgb 测试。"""

    def test_red(self) -> None:
        """红色 H(0,100,50) → (255,0,0)。"""
        r, g, b = hsl_to_rgb(0, 100, 50)
        assert (r, g, b) == (255, 0, 0)

    def test_green(self) -> None:
        """绿色 H(120,100,50) → (0,255,0)。"""
        r, g, b = hsl_to_rgb(120, 100, 50)
        assert (r, g, b) == (0, 255, 0)

    def test_blue(self) -> None:
        """蓝色 H(240,100,50) → (0,0,255)。"""
        r, g, b = hsl_to_rgb(240, 100, 50)
        assert (r, g, b) == (0, 0, 255)

    def test_gray(self) -> None:
        """灰色 H(0,0,50) → (128,128,128)。"""
        r, g, b = hsl_to_rgb(0, 0, 50)
        assert r == g == b
        assert abs(r - 128) <= 1  # 允许舍入误差

    def test_black(self) -> None:
        """黑色 H(0,0,0) → (0,0,0)。"""
        assert hsl_to_rgb(0, 0, 0) == (0, 0, 0)

    def test_white(self) -> None:
        """白色 H(0,0,100) → (255,255,255)。"""
        assert hsl_to_rgb(0, 0, 100) == (255, 255, 255)

    def test_h_out_of_range(self) -> None:
        """h 超出范围抛 ValueError。"""
        with pytest.raises(ValueError, match="h 须在 0-360"):
            hsl_to_rgb(361, 0, 0)
        with pytest.raises(ValueError, match="h 须在 0-360"):
            hsl_to_rgb(-1, 0, 0)

    def test_s_out_of_range(self) -> None:
        """s 超出范围抛 ValueError。"""
        with pytest.raises(ValueError, match="s 须在 0-100"):
            hsl_to_rgb(0, 101, 0)
        with pytest.raises(ValueError, match="s 须在 0-100"):
            hsl_to_rgb(0, -1, 0)

    def test_l_out_of_range(self) -> None:
        """l 超出范围抛 ValueError。"""
        with pytest.raises(ValueError, match="l 须在 0-100"):
            hsl_to_rgb(0, 0, 101)
        with pytest.raises(ValueError, match="l 须在 0-100"):
            hsl_to_rgb(0, 0, -1)


# ============================================================================ #
# 往返一致性
# ============================================================================ #
class TestRoundTrip:
    """往返一致性测试。"""

    def test_rgb_hex_round_trip(self) -> None:
        """RGB → HEX → RGB 往返一致。"""
        for rgb in [(0, 0, 0), (255, 255, 255), (255, 87, 51), (128, 64, 200)]:
            assert hex_to_rgb(rgb_to_hex(*rgb)) == rgb

    def test_rgb_hsl_round_trip(self) -> None:
        """RGB → HSL → RGB 往返一致（允许 1 的舍入误差）。"""
        for rgb in [(255, 0, 0), (0, 255, 0), (0, 0, 255), (128, 128, 128), (255, 87, 51)]:
            hsl = rgb_to_hsl(*rgb)
            back = hsl_to_rgb(*hsl)
            for a, b in zip(rgb, back):
                assert abs(a - b) <= 1


# ============================================================================ #
# CLI 子命令测试
# ============================================================================ #
class TestColortoolCLI:
    """``colortool`` 通过 ``run_tool`` 调用测试。"""

    def test_hex2rgb(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd colortool hex2rgb。"""
        code = run_tool("colortool", ["hex2rgb", "#ff5733"])
        assert code == 0
        out = capsys.readouterr().out
        assert "255 87 51" in out

    def test_hex2rgb_invalid(self, capsys: pytest.CaptureFixture[str]) -> None:
        """hex2rgb 无效输入提示。"""
        code = run_tool("colortool", ["hex2rgb", "#xyz"])
        assert code == 0
        out = capsys.readouterr().out
        assert "6 位十六进制" in out or "非十六进制字符" in out

    def test_rgb2hex(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd colortool rgb2hex。"""
        code = run_tool("colortool", ["rgb2hex", "255", "87", "51"])
        assert code == 0
        out = capsys.readouterr().out
        assert "#ff5733" in out

    def test_rgb2hex_out_of_range(self, capsys: pytest.CaptureFixture[str]) -> None:
        """rgb2hex 超出范围提示。"""
        code = run_tool("colortool", ["rgb2hex", "256", "0", "0"])
        assert code == 0
        out = capsys.readouterr().out
        assert "超出 0-255 范围" in out

    def test_rgb2hsl(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd colortool rgb2hsl。"""
        code = run_tool("colortool", ["rgb2hsl", "255", "0", "0"])
        assert code == 0
        out = capsys.readouterr().out
        # 红色 HSL = (0, 100, 50)
        assert "0" in out and "100" in out and "50" in out

    def test_hsl2rgb(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd colortool hsl2rgb。"""
        code = run_tool("colortool", ["hsl2rgb", "0", "100", "50"])
        assert code == 0
        out = capsys.readouterr().out
        # 红色 RGB = (255, 0, 0)
        assert "255 0 0" in out

    def test_hsl2rgb_out_of_range(self, capsys: pytest.CaptureFixture[str]) -> None:
        """hsl2rgb 超出范围提示。"""
        code = run_tool("colortool", ["hsl2rgb", "361", "0", "0"])
        assert code == 0
        out = capsys.readouterr().out
        assert "h 须在 0-360" in out
