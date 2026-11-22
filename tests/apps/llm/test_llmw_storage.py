"""llmw 存储卡片的数据层：多模型目录的盘符 / 容量与主目录切换。"""

from __future__ import annotations

from pathlib import Path

import pytest

from fcmd.apps.llm import _llmconfig
from fcmd.apps.llm import _llmw_storage as storage


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "home"
    monkeypatch.setenv("FCMD_HOME", str(target))
    for name in ("FCMD_MODELS", "FCMD_CONFIG"):
        monkeypatch.delenv(name, raising=False)
    return target


def test_locations_marks_primary_and_reads_capacity(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    _llmconfig.set_models_dirs([first, second], home)

    def fake_usage(path: Path) -> object:
        return type("Usage", (), {"total": 1000, "free": 250})()

    monkeypatch.setattr("shutil.disk_usage", fake_usage)
    locations = storage.locations(home)
    assert [item.path for item in locations] == [first, second]
    assert locations[0].primary is True
    assert locations[1].primary is False
    assert locations[0].total == 1000
    assert locations[0].free == 250
    assert locations[0].percent == 75


def test_locations_marks_missing_dir(home: Path, tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    _llmconfig.set_models_dir(missing, home)
    locations = storage.locations(home)
    assert locations[0].exists is False
    assert locations[0].disk_label


def test_add_remove_and_make_primary(home: Path, tmp_path: Path) -> None:
    first, second, third = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    _llmconfig.set_models_dirs([first, second, third], home)
    assert [item.path for item in storage.make_primary(third, home)] == [third, first, second]
    assert storage.locations(home)[0].path == third
    assert [item.path for item in storage.remove_location(third, home)] == [first, second]
    assert [item.path for item in storage.add_location(first, home)] == [first, second]
