"""img2ico 工具测试。

验证 ``fcmd.dsl.actions.media`` 模块：
- 工具注册与常量
- icon_build 核心转换函数
- CLI 子命令通过 run_tool 调用
- 边界分支与错误处理
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool

try:
    from fcmd.dsl.actions.media import (
        SVG_TO_ICO_MAC_SIZES,
        SVG_TO_ICO_WIN_SIZES,
        icon_build,
    )
except OSError as _exc:
    # 本机缺 libcairo 原生动态库时 img2ico 无法导入，
    # 整模块跳过（allow_module_level），避免集合错误中断整个测试套件。
    pytest.skip(f"需要 libcairo 原生动态库: {_exc}", allow_module_level=True)


# ---------------------------------------------------------------------- #
# fixtures: 生成测试 SVG
# ---------------------------------------------------------------------- #
@pytest.fixture
def sample_svg(tmp_path: Path) -> Path:
    """生成简单 SVG 文件。"""
    content = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        '<circle cx="50" cy="50" r="40" fill="red"/>'
        "</svg>"
    )
    p = tmp_path / "icon.svg"
    p.write_text(content)
    return p


# ---------------------------------------------------------------------- #
# 常量与注册验证
# ---------------------------------------------------------------------- #
class TestRegistration:
    """常量与注册验证。"""

    def test_tool_registered(self) -> None:
        """img2ico 应在 _TOOL_REGISTRY 中注册。"""
        assert "img2ico" in _TOOL_REGISTRY

    def test_gen_subcommand_exists(self) -> None:
        """img2ico 应注册 gen 子命令。"""
        subs = _TOOL_REGISTRY["img2ico"]
        assert "gen" in subs

    def test_win_sizes_count(self) -> None:
        """Windows ICO 默认尺寸应为 10 种。"""
        assert len(SVG_TO_ICO_WIN_SIZES) == 10
        # GreenFish Icon Editor 标准
        expected = {16, 24, 32, 40, 48, 64, 72, 96, 128, 256}
        actual = {w for w, _ in SVG_TO_ICO_WIN_SIZES}
        assert actual == expected

    def test_mac_sizes_count(self) -> None:
        """macOS ICNS 默认尺寸应为 6 种。"""
        assert len(SVG_TO_ICO_MAC_SIZES) == 6
        expected = {16, 32, 128, 256, 512, 1024}
        actual = {w for w, _ in SVG_TO_ICO_MAC_SIZES}
        assert actual == expected


# ---------------------------------------------------------------------- #
# icon_build 核心函数
# ---------------------------------------------------------------------- #
class TestIconBuild:
    """icon_build 核心转换函数测试。"""

    def test_build_windows_ico_default(self, sample_svg: Path, tmp_path: Path) -> None:
        """默认格式生成 Windows ICO，应包含全部 10 种尺寸。"""
        out = tmp_path / "out.ico"
        fmt, sizes = icon_build(sample_svg, out)
        assert fmt == "windows"
        assert len(sizes) == 10
        assert out.exists()
        assert out.stat().st_size > 0

    def test_build_mac_icns(self, sample_svg: Path, tmp_path: Path) -> None:
        """显式 --format mac 生成 ICNS。"""
        out = tmp_path / "out.icns"
        fmt, sizes = icon_build(sample_svg, out, fmt="mac")
        assert fmt == "mac"
        assert len(sizes) == 6
        assert out.exists()
        assert out.stat().st_size > 0

    def test_format_alias_win(self, sample_svg: Path, tmp_path: Path) -> None:
        """fmt=win 应等价于 fmt=windows。"""
        out = tmp_path / "out.ico"
        fmt, sizes = icon_build(sample_svg, out, fmt="win")
        assert fmt == "windows"
        assert len(sizes) == 10

    def test_format_alias_ico(self, sample_svg: Path, tmp_path: Path) -> None:
        """fmt=ico 应等价于 fmt=windows。"""
        out = tmp_path / "out.ico"
        fmt, _sizes = icon_build(sample_svg, out, fmt="ico")
        assert fmt == "windows"

    def test_format_alias_darwin(self, sample_svg: Path, tmp_path: Path) -> None:
        """fmt=darwin 应等价于 fmt=mac。"""
        out = tmp_path / "out.icns"
        fmt, _sizes = icon_build(sample_svg, out, fmt="darwin")
        assert fmt == "mac"

    def test_format_infer_by_suffix_ico(self, sample_svg: Path, tmp_path: Path) -> None:
        """输出后缀 .ico 应自动推断为 Windows 格式。"""
        out = tmp_path / "auto.ico"
        fmt, _ = icon_build(sample_svg, out)
        assert fmt == "windows"

    def test_format_infer_by_suffix_icns(self, sample_svg: Path, tmp_path: Path) -> None:
        """输出后缀 .icns 应自动推断为 Mac 格式。"""
        out = tmp_path / "auto.icns"
        fmt, _ = icon_build(sample_svg, out)
        assert fmt == "mac"

    def test_invalid_format_raises(self, sample_svg: Path, tmp_path: Path) -> None:
        """非法格式应抛 ValueError。"""
        out = tmp_path / "out.ico"
        with pytest.raises(ValueError, match="不支持的格式"):
            icon_build(sample_svg, out, fmt="linux")

    def test_custom_sizes(self, sample_svg: Path, tmp_path: Path) -> None:
        """自定义尺寸列表应覆盖默认值。"""
        out = tmp_path / "custom.ico"
        fmt, sizes = icon_build(sample_svg, out, sizes=[16, 32, 64])
        assert fmt == "windows"
        assert sizes == [(16, 16), (32, 32), (64, 64)]
        assert out.exists()

    def test_missing_input_raises(self, tmp_path: Path) -> None:
        """不存在的 SVG 文件应抛 FileNotFoundError。"""
        missing = tmp_path / "nonexistent.svg"
        out = tmp_path / "out.ico"
        with pytest.raises(FileNotFoundError):
            icon_build(missing, out)

    def test_empty_sizes_raises(self, sample_svg: Path, tmp_path: Path) -> None:
        """空尺寸列表应抛 ValueError。"""
        out = tmp_path / "out.ico"
        with pytest.raises(ValueError, match="尺寸列表为空"):
            icon_build(sample_svg, out, sizes=[])

    def test_output_parent_auto_create(self, sample_svg: Path, tmp_path: Path) -> None:
        """输出路径的父目录应自动创建。"""
        out = tmp_path / "subdir" / "nested" / "out.ico"
        icon_build(sample_svg, out)
        assert out.exists()

    def test_no_suffix_no_format_raises(self, sample_svg: Path, tmp_path: Path) -> None:
        """无后缀也未显式指定格式应抛 ValueError。"""
        out = tmp_path / "icon"
        with pytest.raises(ValueError, match="无法推断目标格式"):
            icon_build(sample_svg, out)


# ---------------------------------------------------------------------- #
# CLI 子命令（run_tool）
# ---------------------------------------------------------------------- #
class TestGenSubcommand:
    """gen 子命令通过 run_tool 调用。"""

    def test_gen_windows_default(self, sample_svg: Path, tmp_path: Path) -> None:
        """无 --format 时默认生成 Windows ICO。"""
        out = tmp_path / "gen.ico"
        code = run_tool("img2ico", ["gen", str(sample_svg), str(out)])
        assert code == 0
        assert out.exists()
        assert out.stat().st_size > 0

    def test_gen_mac_format(self, sample_svg: Path, tmp_path: Path) -> None:
        """--format mac 生成 ICNS。"""
        out = tmp_path / "gen.icns"
        code = run_tool("img2ico", ["gen", str(sample_svg), str(out), "--format", "mac"])
        assert code == 0
        assert out.exists()

    def test_gen_custom_sizes(self, sample_svg: Path, tmp_path: Path) -> None:
        """--sizes 可自定义尺寸。"""
        out = tmp_path / "gen_small.ico"
        code = run_tool(
            "img2ico",
            ["gen", str(sample_svg), str(out), "--sizes", "16", "32", "256"],
        )
        assert code == 0
        assert out.exists()

    def test_gen_missing_input_error(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """不存在的输入文件应打印错误提示（DSL action 不 raise，退出码保持 0）。"""
        missing = tmp_path / "nope.svg"
        out = tmp_path / "out.ico"
        code = run_tool("img2ico", ["gen", str(missing), str(out)])
        assert code == 0
        assert "错误" in capsys.readouterr().out


# ---------------------------------------------------------------------- #
# 输出内容验证（Pillow 解析）
# ---------------------------------------------------------------------- #
class TestOutputValidation:
    """用 Pillow 解析输出文件验证格式正确性。"""

    def test_ico_contains_expected_subimages(self, sample_svg: Path, tmp_path: Path) -> None:
        """输出 ICO 应包含全部默认尺寸的子图像。"""
        from PIL import Image

        out = tmp_path / "val.ico"
        icon_build(sample_svg, out)
        img = Image.open(out)
        subimages = img.ico.sizes()  # type: ignore[union-attr]
        expected = {(w, h) for w, h in SVG_TO_ICO_WIN_SIZES}
        assert subimages == expected

    def test_icns_is_valid_container(self, sample_svg: Path, tmp_path: Path) -> None:
        """输出 ICNS 应可被 Pillow 打开。"""
        from PIL import Image

        out = tmp_path / "val.icns"
        icon_build(sample_svg, out, fmt="mac")
        img = Image.open(out)
        assert img.format == "ICNS"
