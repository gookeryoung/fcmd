"""Python 来源：uv 定位、索引解析与安装委托。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fcmd.apps.devman import _python
from fcmd.apps.devman._errors import DevmanError
from fcmd.apps.devman._paths import Layout
from tests.apps.devman import _fakes

PY_KEY = _fakes.PY_KEY


def _layout(tmp_path: Path) -> Layout:
    return Layout(tmp_path / "root")


def _list_runner(text: str | None = None, *, returncode: int = 0) -> _fakes.FakeRunner:
    payload = text if text is not None else _fakes.python_list_json()
    return _fakes.FakeRunner(lambda argv, env: _fakes.result(returncode=returncode, stdout=payload))


# ---------------------------------------------------------------------- #
# uv 定位
# ---------------------------------------------------------------------- #


def test_find_uv_auto_uses_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_python.shutil, "which", lambda name: r"C:\Tools\uv.exe")
    assert _python.find_uv("auto") == r"C:\Tools\uv.exe"
    assert _python.find_uv("") == r"C:\Tools\uv.exe"


def test_find_uv_auto_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_python.shutil, "which", lambda name: None)
    with pytest.raises(DevmanError) as info:
        _python.find_uv("auto")
    assert info.value.code == "UV_UNAVAILABLE"
    assert "uv" in (info.value.hint or "")


def test_find_uv_explicit_path(tmp_path: Path) -> None:
    exe = tmp_path / "uv.exe"
    exe.write_bytes(b"MZ")
    assert _python.find_uv(str(exe)) == str(exe)
    with pytest.raises(DevmanError) as info:
        _python.find_uv(str(tmp_path / "missing.exe"))
    assert info.value.code == "UV_UNAVAILABLE"


# ---------------------------------------------------------------------- #
# 索引
# ---------------------------------------------------------------------- #


def test_parse_list_skips_invalid_rows() -> None:
    rows = [
        {"key": PY_KEY, "version": "3.12.15"},
        {"key": "missing-version"},
        {"version": "3.12.0"},
        "not-a-dict",
    ]
    entries = _python.parse_list(json.dumps(rows))
    assert [entry.key for entry in entries] == [PY_KEY]
    assert entries[0].implementation == ""
    assert entries[0].variant is None
    assert entries[0].path is None
    assert entries[0].to_data()["installed"] is False


def test_parse_list_rejects_bad_output() -> None:
    with pytest.raises(DevmanError) as info:
        _python.parse_list("not json")
    assert info.value.code == "UV_UNAVAILABLE"
    with pytest.raises(DevmanError):
        _python.parse_list('{"key": "x"}')


def test_list_remote_filters_windows_stable_cpython() -> None:
    entries = _python.list_remote(_list_runner(), "uv")
    assert [entry.version for entry in entries] == ["3.12.15", "3.13.14"]


def test_list_remote_keeps_prerelease_when_asked() -> None:
    rows = json.loads(_fakes.python_list_json())
    rows.append(
        {
            "key": "cpython-3.15.0a1-windows-x86_64-none",
            "version": "3.15.0a1",
            "implementation": "cpython",
            "os": "windows",
            "arch": "x86_64",
            "variant": "default",
            "path": None,
            "url": None,
        }
    )
    runner = _list_runner(json.dumps(rows))
    assert [entry.version for entry in _python.list_remote(runner, "uv")] == ["3.12.15", "3.13.14"]
    assert "3.15.0a1" in [entry.version for entry in _python.list_remote(runner, "uv", include_prerelease=True)]


def test_list_remote_failure() -> None:
    with pytest.raises(DevmanError) as info:
        _python.list_remote(_list_runner("", returncode=2), "uv")
    assert info.value.code == "UV_UNAVAILABLE"
    assert "2" in str(info.value)


def test_latest_matching_prefixes() -> None:
    entries = _python.parse_list(_fakes.python_list_json())
    assert _python.latest_matching(entries, "3.12") == PY_KEY
    assert _python.latest_matching(entries, "latest").startswith("cpython-3.13.14")
    assert _python.latest_matching(entries, "").startswith("cpython-3.13.14")
    with pytest.raises(DevmanError) as info:
        _python.latest_matching(entries, "3.20")
    assert info.value.code == "VERSION_NOT_FOUND"
    with pytest.raises(DevmanError):
        _python.latest_matching(entries, "vNext")


def test_latest_matching_accepts_install_keys() -> None:
    entries = _python.parse_list(_fakes.python_list_json())
    assert _python.latest_matching(entries, PY_KEY) == PY_KEY
    assert _python.latest_matching(entries, "cpython-3.12") == PY_KEY
    with pytest.raises(DevmanError) as info:
        _python.latest_matching(entries, "pypy-3.12")
    assert info.value.code == "VERSION_NOT_FOUND"


def test_latest_matching_rejects_non_decimal_digit_prefix() -> None:
    entries = _python.parse_list(_fakes.python_list_json())
    with pytest.raises(DevmanError) as info:
        _python.latest_matching(entries, "\u00b2")
    assert info.value.code == "VERSION_NOT_FOUND"


def test_key_sort_ignores_unparseable_keys() -> None:
    assert _python._key_sort("weird-key") == (0, 0, 0)


def test_resolve_key_uses_index() -> None:
    assert _python.resolve_key(_list_runner(), "uv", "3.12") == PY_KEY


# ---------------------------------------------------------------------- #
# 安装
# ---------------------------------------------------------------------- #


def _install_runner(layout: Layout, *, probe: str = "Python 3.12.15", create: bool = True) -> _fakes.FakeRunner:
    final = layout.version_dir("python", PY_KEY)

    def handler(argv: list[str], env: Any) -> Any:
        if argv[1:3] == ["python", "install"]:
            if create:
                final.mkdir(parents=True, exist_ok=True)
                (final / "python.exe").write_bytes(b"MZ")
            return _fakes.result()
        return _fakes.result(stdout=f"{probe}\n")

    return _fakes.FakeRunner(handler)


def test_install_success_writes_marker(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    runner = _install_runner(layout)
    result = _python.install(layout, PY_KEY, runner=runner, uv="uv", mirror="https://mirror.invalid")
    assert result["changed"] is True
    assert result["version"] == "3.12.15"
    assert (layout.version_dir("python", PY_KEY) / ".devman.json").is_file()
    argv = result["actions"][0]["argv"]
    assert "--no-bin" in argv and "--no-registry" in argv and "--managed-python" in argv
    assert argv[-1] == "https://mirror.invalid"
    assert runner.envs[0]["UV_PYTHON_INSTALL_DIR"] == str(layout.python_root)


def test_install_skips_complete(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _fakes.install_fake_python(layout, PY_KEY, "3.12.15")
    runner = _fakes.FakeRunner()
    result = _python.install(layout, PY_KEY, runner=runner, uv="uv")
    assert result["changed"] is False
    assert runner.calls == []


def test_install_uv_failure_cleans_new_directory(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    runner = _fakes.FakeRunner(lambda argv, env: _fakes.result(returncode=1, stderr="network down"))
    with pytest.raises(DevmanError) as info:
        _python.install(layout, PY_KEY, runner=runner, uv="uv")
    assert info.value.code == "DOWNLOAD_FAILED"
    assert "network down" in str(info.value)
    assert not layout.version_dir("python", PY_KEY).exists()


def test_install_missing_executable(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    runner = _install_runner(layout, create=False)
    with pytest.raises(DevmanError) as info:
        _python.install(layout, PY_KEY, runner=runner, uv="uv")
    assert info.value.code == "UV_UNAVAILABLE"


def test_install_probe_mismatch(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    runner = _install_runner(layout, probe="Python 3.11.0")
    with pytest.raises(DevmanError) as info:
        _python.install(layout, PY_KEY, runner=runner, uv="uv")
    assert "自检失败" in str(info.value)
    assert not layout.version_dir("python", PY_KEY).exists()


def test_install_force_failure_keeps_previous_complete_install(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    existing = _fakes.install_fake_python(layout, PY_KEY, "3.12.15")
    runner = _fakes.FakeRunner(lambda argv, env: _fakes.result(returncode=1, stderr="failed"))
    with pytest.raises(DevmanError):
        _python.install(layout, PY_KEY, runner=runner, uv="uv", force=True)
    assert existing.is_dir()
    assert (existing / "python.exe").is_file()


def test_install_force_passes_reinstall(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _fakes.install_fake_python(layout, PY_KEY, "3.12.15")
    runner = _install_runner(layout)
    result = _python.install(layout, PY_KEY, runner=runner, uv="uv", force=True)
    assert result["changed"] is True
    assert "--reinstall" in result["actions"][0]["argv"]
