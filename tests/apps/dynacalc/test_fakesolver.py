"""dynacalc 假求解器测试。

``_fakesolver`` 由 :mod:`fcmd.apps.dynacalc._solver` 以子进程拉起，因此正常
端到端测试不会计入本模块覆盖率。这里直接调用 ``main()`` 与内部辅助函数，
验证参数校验、环境变量注入（失败 / 延时 / 淬熄）、三件套输出与确定性。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from fcmd.apps.dynacalc import _fakesolver


def _deck(tmp_path: Path, *, code: str = "M001", fingerprint: str = "deadbeef00") -> Path:
    """构造最小工况目录（``case.json``）。"""
    deck = tmp_path / "deck"
    deck.mkdir(parents=True)
    (deck / "case.json").write_text(json.dumps({"code": code, "fingerprint": fingerprint}), encoding="utf-8")
    return deck


def _rows(deck: Path) -> list[list[float]]:
    """读取 ``history.csv`` 的数值行。"""
    lines = (deck / "history.csv").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "time,front_z,reaction_fraction,wall_velocity,wall_strain"
    return [[float(item) for item in line.split(",")] for line in lines[1:]]


class TestMain:
    """入口参数校验与环境变量注入。"""

    def test_requires_exactly_one_argument(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert _fakesolver.main([]) == 2
        assert "用法" in capsys.readouterr().err

    def test_missing_case_json(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        deck = tmp_path / "deck"
        deck.mkdir()
        assert _fakesolver.main([str(deck)]) == 2
        assert "无法读取" in capsys.readouterr().err

    def test_corrupt_case_json(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        deck = tmp_path / "deck"
        deck.mkdir()
        (deck / "case.json").write_text("{oops", encoding="utf-8")
        assert _fakesolver.main([str(deck)]) == 2
        assert "无法读取" in capsys.readouterr().err

    def test_success_writes_three_outputs(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        deck = _deck(tmp_path)
        assert _fakesolver.main([str(deck)]) == 0
        assert "假求解器完成: M001" in capsys.readouterr().out
        rows = _rows(deck)
        assert len(rows) == _fakesolver._SAMPLES
        assert rows[0][1] == 0.0
        assert rows[-1][0] > 0.0
        glstat = (deck / "glstat").read_text(encoding="utf-8")
        assert "kinetic energy" in glstat
        assert "total energy" in glstat
        matsum = (deck / "matsum").read_text(encoding="utf-8")
        assert "part id = 1" in matsum
        assert "part id = 2" in matsum

    def test_injected_failure_skips_outputs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("DYNACALC_FAKE_FAIL_CODES", " M999 , M001 ")
        deck = _deck(tmp_path)
        assert _fakesolver.main([str(deck)]) == 1
        assert "假求解器注入失败: M001" in capsys.readouterr().err
        assert not (deck / "history.csv").exists()

    def test_quench_caps_reaction_fraction(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DYNACALC_FAKE_QUENCH_CODES", "M001")
        deck = _deck(tmp_path)
        assert _fakesolver.main([str(deck)]) == 0
        assert max(row[2] for row in _rows(deck)) == pytest.approx(0.95)

    def test_without_quench_reaches_full_reaction(self, tmp_path: Path) -> None:
        deck = _deck(tmp_path)
        assert _fakesolver.main([str(deck)]) == 0
        assert max(row[2] for row in _rows(deck)) == pytest.approx(1.0)

    def test_delay_is_honoured(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DYNACALC_FAKE_DELAY_MS", "1")
        deck = _deck(tmp_path)
        assert _fakesolver.main([str(deck)]) == 0
        assert (deck / "history.csv").is_file()

    def test_invalid_delay_is_treated_as_zero(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DYNACALC_FAKE_DELAY_MS", "not-a-number")
        deck = _deck(tmp_path)
        assert _fakesolver.main([str(deck)]) == 0

    def test_outputs_are_deterministic(self, tmp_path: Path) -> None:
        first = _deck(tmp_path / "a")
        second = _deck(tmp_path / "b")
        assert _fakesolver.main([str(first)]) == 0
        assert _fakesolver.main([str(second)]) == 0
        for name in ("history.csv", "glstat", "matsum"):
            assert (first / name).read_bytes() == (second / name).read_bytes()


class TestHelpers:
    """环境变量解析与指纹种子。"""

    def test_codes_env_splits_and_strips(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FAKE_CODES", " A , ,B ")
        assert _fakesolver._codes_env("FAKE_CODES") == {"A", "B"}

    def test_codes_env_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FAKE_CODES", raising=False)
        assert _fakesolver._codes_env("FAKE_CODES") == set()

    @pytest.mark.parametrize(("raw", "expected"), [("2.5", 2.5), ("", 0.0), ("oops", 0.0)])
    def test_float_env(self, monkeypatch: pytest.MonkeyPatch, raw: str, expected: float) -> None:
        monkeypatch.setenv("FAKE_DELAY", raw)
        assert _fakesolver._float_env("FAKE_DELAY") == expected

    def test_float_env_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FAKE_DELAY", raising=False)
        assert _fakesolver._float_env("FAKE_DELAY") == 0.0

    def test_seed_uses_hex_prefix(self) -> None:
        assert _fakesolver._seed("deadbeef00") == 0xDEADBEEF

    def test_seed_falls_back_to_char_sum(self) -> None:
        assert _fakesolver._seed("zz") == sum(ord(char) for char in "zz")

    def test_seed_without_fingerprint(self) -> None:
        assert _fakesolver._seed("") == 1

    def test_write_outputs_with_non_hex_fingerprint(self, tmp_path: Path) -> None:
        """非十六进制指纹走字符和分支，仍写出合法文件。"""
        _fakesolver._write_outputs(tmp_path, "zz", quench=False)
        rows = _rows(tmp_path)
        assert len(rows) == _fakesolver._SAMPLES
        assert all(math.isfinite(value) for row in rows for value in row)

    def test_write_uses_lf_newlines(self, tmp_path: Path) -> None:
        target = tmp_path / "out.txt"
        _fakesolver._write(target, "a\nb\n")
        assert target.read_bytes() == b"a\nb\n"
