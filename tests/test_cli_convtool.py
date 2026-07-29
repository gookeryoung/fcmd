"""convtool 工具测试。

验证 ``fcmd.cli.convtool`` 模块：
- 工具注册与四子命令结构（length/weight/temp/datasize）
- ``convert_length``/``convert_weight``/``convert_temperature``/``convert_datasize``
- 单位列举与错误分支
- CLI 子命令端到端
"""

from __future__ import annotations

import pytest

from fcmd.apis.toolkit import list_subcommands, run_tool
from fcmd.cli.convtool import (
    convert_datasize,
    convert_length,
    convert_temperature,
    convert_weight,
    list_datasize_units,
    list_length_units,
    list_temperature_units,
    list_weight_units,
)


# ============================================================================ #
# 工具注册
# ============================================================================ #
class TestRegistration:
    """工具注册与子命令结构测试。"""

    def test_registered(self) -> None:
        """convtool 已注册到工具表。"""
        from fcmd.apis.toolkit import list_tools

        assert "convtool" in list_tools()

    def test_subcommands(self) -> None:
        """convtool 有 length/weight/temp/datasize 四个子命令。"""
        subs = list_subcommands("convtool")
        assert set(subs) == {"length", "weight", "temp", "datasize"}


# ============================================================================ #
# convert_length
# ============================================================================ #
class TestConvertLength:
    """convert_length 长度换算测试。"""

    def test_meter_to_feet(self) -> None:
        """1 m = 3.28084 ft。"""
        result = convert_length(1, "m", "ft")
        assert abs(result - 3.280839895013123) < 1e-10

    def test_km_to_mile(self) -> None:
        """1 km = 0.621371 mile。"""
        result = convert_length(1, "km", "mile")
        assert abs(result - 0.6213711922373339) < 1e-10

    def test_same_unit(self) -> None:
        """同单位换算返回原值。"""
        assert convert_length(5, "m", "m") == 5

    def test_cm_to_mm(self) -> None:
        """1 cm = 10 mm。"""
        assert convert_length(1, "cm", "mm") == 10

    def test_inch_to_cm(self) -> None:
        """1 in = 2.54 cm。"""
        result = convert_length(1, "in", "cm")
        assert abs(result - 2.54) < 1e-10

    def test_yd_to_m(self) -> None:
        """1 yd = 0.9144 m。"""
        result = convert_length(1, "yd", "m")
        assert abs(result - 0.9144) < 1e-10

    def test_invalid_from_unit(self) -> None:
        """无效源单位抛 ValueError。"""
        with pytest.raises(ValueError, match="不支持的长度单位"):
            convert_length(1, "invalid", "m")

    def test_invalid_to_unit(self) -> None:
        """无效目标单位抛 ValueError。"""
        with pytest.raises(ValueError, match="不支持的长度单位"):
            convert_length(1, "m", "invalid")

    def test_list_units(self) -> None:
        """list_length_units 返回支持单位列表。"""
        units = list_length_units()
        assert "m" in units
        assert "km" in units
        assert "mile" in units


# ============================================================================ #
# convert_weight
# ============================================================================ #
class TestConvertWeight:
    """convert_weight 重量换算测试。"""

    def test_kg_to_lb(self) -> None:
        """1 kg = 2.20462 lb。"""
        result = convert_weight(1, "kg", "lb")
        assert abs(result - 2.2046226218487757) < 1e-10

    def test_same_unit(self) -> None:
        """同单位换算返回原值。"""
        assert convert_weight(5, "g", "g") == 5

    def test_kg_to_g(self) -> None:
        """1 kg = 1000 g。"""
        assert convert_weight(1, "kg", "g") == 1000

    def test_oz_to_g(self) -> None:
        """1 oz = 28.3495 g。"""
        result = convert_weight(1, "oz", "g")
        assert abs(result - 28.349523125) < 1e-10

    def test_t_to_kg(self) -> None:
        """1 t = 1000 kg。"""
        assert convert_weight(1, "t", "kg") == 1000

    def test_lb_to_oz(self) -> None:
        """1 lb = 16 oz。"""
        result = convert_weight(1, "lb", "oz")
        assert abs(result - 16.0) < 1e-6

    def test_invalid_unit(self) -> None:
        """无效单位抛 ValueError。"""
        with pytest.raises(ValueError, match="不支持的重量单位"):
            convert_weight(1, "invalid", "g")

    def test_list_units(self) -> None:
        """list_weight_units 返回支持单位列表。"""
        units = list_weight_units()
        assert "g" in units
        assert "kg" in units
        assert "lb" in units


# ============================================================================ #
# convert_temperature
# ============================================================================ #
class TestConvertTemperature:
    """convert_temperature 温度换算测试。"""

    def test_c_to_f(self) -> None:
        """100°C = 212°F。"""
        assert convert_temperature(100, "C", "F") == 212

    def test_f_to_c(self) -> None:
        """32°F = 0°C。"""
        assert convert_temperature(32, "F", "C") == 0

    def test_c_to_k(self) -> None:
        """0°C = 273.15 K。"""
        result = convert_temperature(0, "C", "K")
        assert abs(result - 273.15) < 1e-10

    def test_k_to_c(self) -> None:
        """273.15 K = 0°C。"""
        result = convert_temperature(273.15, "K", "C")
        assert abs(result - 0) < 1e-10

    def test_f_to_k(self) -> None:
        """32°F = 273.15 K。"""
        result = convert_temperature(32, "F", "K")
        assert abs(result - 273.15) < 1e-10

    def test_k_to_f(self) -> None:
        """273.15 K = 32°F。"""
        result = convert_temperature(273.15, "K", "F")
        assert abs(result - 32) < 1e-10

    def test_same_unit(self) -> None:
        """同单位换算返回原值。"""
        assert convert_temperature(25, "C", "C") == 25

    def test_case_insensitive(self) -> None:
        """单位名大小写不敏感。"""
        assert convert_temperature(100, "c", "f") == 212
        assert convert_temperature(100, "C", "f") == 212

    def test_below_absolute_zero_celsius(self) -> None:
        """低于绝对零度（C）抛 ValueError。"""
        with pytest.raises(ValueError, match="绝对零度"):
            convert_temperature(-300, "C", "F")

    def test_below_absolute_zero_fahrenheit(self) -> None:
        """低于绝对零度（F）抛 ValueError。"""
        with pytest.raises(ValueError, match="绝对零度"):
            convert_temperature(-500, "F", "C")

    def test_below_absolute_zero_kelvin(self) -> None:
        """负开尔文抛 ValueError。"""
        with pytest.raises(ValueError, match="绝对零度"):
            convert_temperature(-1, "K", "C")

    def test_invalid_unit(self) -> None:
        """无效单位抛 ValueError。"""
        with pytest.raises(ValueError, match="不支持的温度单位"):
            convert_temperature(0, "X", "C")

    def test_list_units(self) -> None:
        """list_temperature_units 返回支持单位列表。"""
        assert set(list_temperature_units()) == {"C", "F", "K"}


# ============================================================================ #
# convert_datasize
# ============================================================================ #
class TestConvertDatasize:
    """convert_datasize 数据大小换算测试。"""

    def test_gb_to_mb_binary(self) -> None:
        """1 GB = 1024 MB（默认二进制）。"""
        assert convert_datasize(1, "GB", "MB") == 1024

    def test_gb_to_mb_decimal(self) -> None:
        """1 GB = 1000 MB（十进制）。"""
        assert convert_datasize(1, "GB", "MB", base="decimal") == 1000

    def test_tb_to_gb_binary(self) -> None:
        """1 TB = 1024 GB。"""
        assert convert_datasize(1, "TB", "GB") == 1024

    def test_pb_to_tb(self) -> None:
        """1 PB = 1024 TB。"""
        assert convert_datasize(1, "PB", "TB") == 1024

    def test_b_to_kb_binary(self) -> None:
        """1024 B = 1 KB（二进制）。"""
        assert convert_datasize(1024, "B", "KB") == 1

    def test_b_to_kb_decimal(self) -> None:
        """1000 B = 1 KB（十进制）。"""
        assert convert_datasize(1000, "B", "KB", base="decimal") == 1

    def test_same_unit(self) -> None:
        """同单位换算返回原值。"""
        assert convert_datasize(5, "MB", "MB") == 5

    def test_kb_to_gb_binary(self) -> None:
        """1 KB = 1/1048576 GB。"""
        result = convert_datasize(1, "KB", "GB")
        assert abs(result - 1 / (1024**2)) < 1e-15

    def test_invalid_unit(self) -> None:
        """无效单位抛 ValueError。"""
        with pytest.raises(ValueError, match="不支持的数据大小单位"):
            convert_datasize(1, "XB", "MB")

    def test_invalid_base(self) -> None:
        """无效进制抛 ValueError。"""
        with pytest.raises(ValueError, match="不支持的进制基础"):
            convert_datasize(1, "KB", "MB", base="hex")

    def test_list_units(self) -> None:
        """list_datasize_units 返回支持单位列表。"""
        units = list_datasize_units()
        assert "B" in units
        assert "KB" in units
        assert "GB" in units
        assert "PB" in units


# ============================================================================ #
# CLI 子命令测试
# ============================================================================ #
class TestConvtoolCLI:
    """``convtool`` 通过 ``run_tool`` 调用测试。"""

    def test_length(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd convtool length 1 m ft。"""
        code = run_tool("convtool", ["length", "1", "m", "ft"])
        assert code == 0
        out = capsys.readouterr().out
        assert "3.28" in out

    def test_length_invalid_unit(self, capsys: pytest.CaptureFixture[str]) -> None:
        """length 无效单位提示。"""
        code = run_tool("convtool", ["length", "1", "invalid", "m"])
        assert code == 0
        out = capsys.readouterr().out
        assert "不支持的长度单位" in out

    def test_weight(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd convtool weight 1 kg lb。"""
        code = run_tool("convtool", ["weight", "1", "kg", "lb"])
        assert code == 0
        out = capsys.readouterr().out
        assert "2.20" in out

    def test_weight_invalid_unit(self, capsys: pytest.CaptureFixture[str]) -> None:
        """weight 无效单位提示。"""
        code = run_tool("convtool", ["weight", "1", "invalid", "g"])
        assert code == 0
        out = capsys.readouterr().out
        assert "不支持的重量单位" in out

    def test_temp_c_to_f(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd convtool temp 100 C F。"""
        code = run_tool("convtool", ["temp", "100", "C", "F"])
        assert code == 0
        out = capsys.readouterr().out
        # 100C = 212F
        assert "212" in out

    def test_temp_below_absolute_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        """temp 低于绝对零度提示。"""
        code = run_tool("convtool", ["temp", "-300", "C", "F"])
        assert code == 0
        out = capsys.readouterr().out
        assert "绝对零度" in out

    def test_datasize_binary(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd convtool datasize 1 GB MB（默认二进制）。"""
        code = run_tool("convtool", ["datasize", "1", "GB", "MB"])
        assert code == 0
        out = capsys.readouterr().out
        assert "1024" in out

    def test_datasize_decimal(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--base decimal。"""
        code = run_tool("convtool", ["datasize", "1", "GB", "MB", "--base", "decimal"])
        assert code == 0
        out = capsys.readouterr().out
        assert "1000" in out

    def test_datasize_invalid_base(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--base hex 提示。"""
        code = run_tool("convtool", ["datasize", "1", "GB", "MB", "--base", "hex"])
        assert code == 0
        out = capsys.readouterr().out
        assert "不支持的进制基础" in out

    def test_datasize_invalid_unit(self, capsys: pytest.CaptureFixture[str]) -> None:
        """datasize 无效单位提示。"""
        code = run_tool("convtool", ["datasize", "1", "XB", "MB"])
        assert code == 0
        out = capsys.readouterr().out
        assert "不支持的数据大小单位" in out
