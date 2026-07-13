"""CLI 主入口测试。"""

from __future__ import annotations

import subprocess
import sys

import pytest

from fcmd.cli.main import _build_parser, main


def test_cli_parser_version() -> None:
    """--version 打印版本号。"""
    parser = _build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--version"])
    assert exc_info.value.code == 0


def test_cli_no_args(monkeypatch: pytest.MonkeyPatch) -> None:
    """无参数调用不报错。"""
    monkeypatch.setattr(sys, "argv", ["fcmd"])
    main()


def test_cli_entry_point_version() -> None:
    """通过 console_script 入口调用 --version。"""
    result = subprocess.run(
        [sys.executable, "-m", "fcmd", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "fcmd" in result.stdout
