"""stattool 工具测试。

验证 ``fcmd.cli.stattool`` 模块：
- 工具注册与五子命令结构（mean/median/stddev/variance/summarize）
- ``load_numbers``/``stat_mean``/``stat_median``/``stat_stddev``/``stat_variance``/``stat_summarize``
- CLI 子命令端到端
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fcmd.apis.toolkit import list_subcommands, run_tool
from fcmd.cli.stattool import (
    load_numbers,
    stat_mean,
    stat_median,
    stat_stddev,
    stat_summarize,
    stat_variance,
)


def _write_data(path: Path, content: str) -> None:
    """写入数据文件。"""
    path.write_text(content, encoding="utf-8")


# ============================================================================ #
# 工具注册
# ============================================================================ #
class TestRegistration:
    """工具注册与子命令结构测试。"""

    def test_registered(self) -> None:
        """stattool 已注册到工具表。"""
        from fcmd.apis.toolkit import list_tools

        assert "stattool" in list_tools()

    def test_subcommands(self) -> None:
        """stattool 有 mean/median/stddev/variance/summarize 五个子命令。"""
        subs = list_subcommands("stattool")
        assert set(subs) == {"mean", "median", "stddev", "variance", "summarize"}


# ============================================================================ #
# load_numbers
# ============================================================================ #
class TestLoadNumbers:
    """load_numbers 加载测试。"""

    def test_basic(self, tmp_path: Path) -> None:
        """基本加载。"""
        path = tmp_path / "data.txt"
        _write_data(path, "1\n2\n3\n")
        assert load_numbers(path) == [1.0, 2.0, 3.0]

    def test_skip_empty_lines(self, tmp_path: Path) -> None:
        """跳过空行。"""
        path = tmp_path / "data.txt"
        _write_data(path, "1\n\n2\n\n3\n")
        assert load_numbers(path) == [1.0, 2.0, 3.0]

    def test_skip_comments(self, tmp_path: Path) -> None:
        """跳过 # 注释行。"""
        path = tmp_path / "data.txt"
        _write_data(path, "# header\n1\n# mid comment\n2\n")
        assert load_numbers(path) == [1.0, 2.0]

    def test_decimal_numbers(self, tmp_path: Path) -> None:
        """支持小数。"""
        path = tmp_path / "data.txt"
        _write_data(path, "1.5\n2.5\n3.0\n")
        assert load_numbers(path) == [1.5, 2.5, 3.0]

    def test_negative_numbers(self, tmp_path: Path) -> None:
        """支持负数。"""
        path = tmp_path / "data.txt"
        _write_data(path, "-1.5\n2.5\n-3.0\n")
        assert load_numbers(path) == [-1.5, 2.5, -3.0]

    def test_nonexistent(self, tmp_path: Path) -> None:
        """不存在抛 FileNotFoundError。"""
        with pytest.raises(FileNotFoundError, match="文件不存在"):
            load_numbers(tmp_path / "no.txt")

    def test_invalid_line_raises(self, tmp_path: Path) -> None:
        """非数字行抛 ValueError（含行号）。"""
        path = tmp_path / "data.txt"
        _write_data(path, "1\nabc\n3\n")
        with pytest.raises(ValueError, match="第 2 行无法解析为数字"):
            load_numbers(path)

    def test_empty_file(self, tmp_path: Path) -> None:
        """空文件返回空列表。"""
        path = tmp_path / "empty.txt"
        _write_data(path, "")
        assert load_numbers(path) == []

    def test_only_comments_and_blanks(self, tmp_path: Path) -> None:
        """只有注释和空行返回空列表。"""
        path = tmp_path / "comments.txt"
        _write_data(path, "# comment\n\n# another\n")
        assert load_numbers(path) == []


# ============================================================================ #
# stat_mean
# ============================================================================ #
class TestStatMean:
    """stat_mean 测试。"""

    def test_basic(self) -> None:
        """基本平均值。"""
        assert stat_mean([1.0, 2.0, 3.0, 4.0, 5.0]) == 3.0

    def test_single_value(self) -> None:
        """单值返回自身。"""
        assert stat_mean([42.0]) == 42.0

    def test_negative(self) -> None:
        """负数。"""
        assert stat_mean([-1.0, 1.0]) == 0.0

    def test_empty_raises(self) -> None:
        """空列表抛 ValueError。"""
        with pytest.raises(ValueError, match="数据列表为空"):
            stat_mean([])


# ============================================================================ #
# stat_median
# ============================================================================ #
class TestStatMedian:
    """stat_median 测试。"""

    def test_odd_count(self) -> None:
        """奇数个返回中间值。"""
        assert stat_median([1.0, 3.0, 2.0]) == 2.0

    def test_even_count(self) -> None:
        """偶数个返回中间两值平均。"""
        assert stat_median([1.0, 2.0, 3.0, 4.0]) == 2.5

    def test_single_value(self) -> None:
        """单值返回自身。"""
        assert stat_median([42.0]) == 42.0

    def test_empty_raises(self) -> None:
        """空列表抛 ValueError。"""
        with pytest.raises(ValueError, match="数据列表为空"):
            stat_median([])


# ============================================================================ #
# stat_stddev
# ============================================================================ #
class TestStatStddev:
    """stat_stddev 测试。"""

    def test_basic(self) -> None:
        """基本标准差。"""
        # 1,2,3,4,5 的样本标准差 = sqrt(2.5) ≈ 1.5811
        result = stat_stddev([1.0, 2.0, 3.0, 4.0, 5.0])
        assert abs(result - 1.5811388300841898) < 1e-10

    def test_same_values(self) -> None:
        """所有值相同标准差为 0。"""
        assert stat_stddev([5.0, 5.0, 5.0, 5.0]) == 0.0

    def test_single_value_raises(self) -> None:
        """单值抛 ValueError（需要至少 2 个数据点）。"""
        with pytest.raises(ValueError, match="至少 2 个数据点"):
            stat_stddev([42.0])

    def test_empty_raises(self) -> None:
        """空列表抛 ValueError。"""
        with pytest.raises(ValueError, match="至少 2 个数据点"):
            stat_stddev([])


# ============================================================================ #
# stat_variance
# ============================================================================ #
class TestStatVariance:
    """stat_variance 测试。"""

    def test_basic(self) -> None:
        """基本方差。"""
        # 1,2,3,4,5 的样本方差 = 2.5
        result = stat_variance([1.0, 2.0, 3.0, 4.0, 5.0])
        assert abs(result - 2.5) < 1e-10

    def test_same_values(self) -> None:
        """所有值相同方差为 0。"""
        assert stat_variance([5.0, 5.0, 5.0, 5.0]) == 0.0

    def test_single_value_raises(self) -> None:
        """单值抛 ValueError。"""
        with pytest.raises(ValueError, match="至少 2 个数据点"):
            stat_variance([42.0])


# ============================================================================ #
# stat_summarize
# ============================================================================ #
class TestStatSummarize:
    """stat_summarize 测试。"""

    def test_basic_keys(self) -> None:
        """包含所有必要键。"""
        summary = stat_summarize([1.0, 2.0, 3.0, 4.0, 5.0])
        assert set(summary.keys()) == {"count", "sum", "mean", "median", "min", "max", "stddev", "variance"}

    def test_basic_values(self) -> None:
        """基本数值正确。"""
        summary = stat_summarize([1.0, 2.0, 3.0, 4.0, 5.0])
        assert summary["count"] == 5
        assert summary["sum"] == 15.0
        assert summary["mean"] == 3.0
        assert summary["median"] == 3.0
        assert summary["min"] == 1.0
        assert summary["max"] == 5.0
        assert abs(summary["variance"] - 2.5) < 1e-10

    def test_single_value(self) -> None:
        """单值时 stddev/variance 为 0。"""
        summary = stat_summarize([42.0])
        assert summary["count"] == 1
        assert summary["mean"] == 42.0
        assert summary["stddev"] == 0.0
        assert summary["variance"] == 0.0

    def test_empty_raises(self) -> None:
        """空列表抛 ValueError。"""
        with pytest.raises(ValueError, match="数据列表为空"):
            stat_summarize([])


# ============================================================================ #
# CLI 子命令测试
# ============================================================================ #
class TestStattoolCLI:
    """``stattool`` 通过 ``run_tool`` 调用测试。"""

    def test_mean(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd stattool mean 计算平均值。"""
        path = tmp_path / "data.txt"
        _write_data(path, "1\n2\n3\n4\n5\n")
        code = run_tool("stattool", ["mean", str(path)])
        assert code == 0
        out = capsys.readouterr().out
        assert "3" in out

    def test_median(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd stattool median 计算中位数。"""
        path = tmp_path / "data.txt"
        _write_data(path, "1\n2\n3\n4\n5\n")
        code = run_tool("stattool", ["median", str(path)])
        assert code == 0
        out = capsys.readouterr().out
        assert "3" in out

    def test_stddev(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd stattool stddev 计算标准差。"""
        path = tmp_path / "data.txt"
        _write_data(path, "1\n2\n3\n4\n5\n")
        code = run_tool("stattool", ["stddev", str(path)])
        assert code == 0
        out = capsys.readouterr().out
        # sqrt(2.5) ≈ 1.5811
        assert "1.5811" in out or "1.5812" in out

    def test_variance(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd stattool variance 计算方差。"""
        path = tmp_path / "data.txt"
        _write_data(path, "1\n2\n3\n4\n5\n")
        code = run_tool("stattool", ["variance", str(path)])
        assert code == 0
        out = capsys.readouterr().out
        assert "2.5" in out

    def test_summarize(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd stattool summarize 输出完整摘要。"""
        path = tmp_path / "data.txt"
        _write_data(path, "1\n2\n3\n4\n5\n")
        code = run_tool("stattool", ["summarize", str(path)])
        assert code == 0
        out = capsys.readouterr().out
        # 应包含所有统计键
        for key in ("count", "sum", "mean", "median", "min", "max", "stddev", "variance"):
            assert key in out

    def test_mean_nonexistent(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """mean 文件不存在提示。"""
        code = run_tool("stattool", ["mean", str(tmp_path / "no.txt")])
        assert code == 0
        out = capsys.readouterr().out
        assert "文件不存在" in out

    def test_mean_invalid_data(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """mean 非数字数据提示。"""
        path = tmp_path / "data.txt"
        _write_data(path, "1\nabc\n3\n")
        code = run_tool("stattool", ["mean", str(path)])
        assert code == 0
        out = capsys.readouterr().out
        assert "数据解析失败" in out
        assert "第 2 行" in out

    def test_stddev_single_value(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """stddev 单值数据提示需要至少 2 个数据点。"""
        path = tmp_path / "data.txt"
        _write_data(path, "42\n")
        code = run_tool("stattool", ["stddev", str(path)])
        assert code == 0
        out = capsys.readouterr().out
        assert "至少 2 个数据点" in out

    def test_summarize_single_value(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """summarize 单值数据正常输出。"""
        path = tmp_path / "data.txt"
        _write_data(path, "42\n")
        code = run_tool("stattool", ["summarize", str(path)])
        assert code == 0
        out = capsys.readouterr().out
        assert "count: 1" in out
        assert "mean: 42" in out

    def test_with_comments(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """含注释行的数据文件。"""
        path = tmp_path / "data.txt"
        _write_data(path, "# header\n1\n# mid\n2\n3\n")
        code = run_tool("stattool", ["mean", str(path)])
        assert code == 0
        out = capsys.readouterr().out
        assert "2" in out  # mean([1,2,3]) = 2

    @pytest.mark.parametrize(
        "subcommand",
        ["median", "stddev", "variance", "summarize"],
    )
    def test_nonexistent_file(
        self,
        subcommand: str,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """子命令文件不存在时打印错误且不输出结果。"""
        code = run_tool("stattool", [subcommand, str(tmp_path / "no.txt")])
        assert code == 0
        out = capsys.readouterr().out
        assert "文件不存在" in out

    @pytest.mark.parametrize(
        ("subcommand", "data", "expected"),
        [
            ("mean", "", "平均值 计算失败"),
            ("median", "", "中位数 计算失败"),
            ("variance", "42\n", "方差 计算失败"),
            ("summarize", "", "统计摘要 计算失败"),
        ],
    )
    def test_stat_failure(
        self,
        subcommand: str,
        data: str,
        expected: str,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """数据导致统计函数失败时打印错误且不输出结果。"""
        path = tmp_path / "data.txt"
        _write_data(path, data)
        code = run_tool("stattool", [subcommand, str(path)])
        assert code == 0
        out = capsys.readouterr().out
        assert expected in out
