"""启动参数解析测试：目录 / 文件 / 相对路径 / 非法值容错。"""

from __future__ import annotations

from pathlib import Path

from fcmd.tools.system._cmderw_launch import LaunchRequest, resolve_launch


def test_resolves_existing_directories(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    resolved = resolve_launch(LaunchRequest(left=str(left), right=str(right)))
    assert resolved.left == str(left)
    assert resolved.right == str(right)
    assert resolved.messages == ()


def test_relative_paths_use_cwd(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    resolved = resolve_launch(LaunchRequest(left="sub"), cwd=str(tmp_path))
    assert resolved.left == str(tmp_path / "sub")


def test_select_directory_sets_target_dir(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    resolved = resolve_launch(LaunchRequest(select=str(target)))
    assert resolved.select_dir == str(target)
    assert resolved.select_name is None


def test_select_file_sets_parent_and_name(tmp_path: Path) -> None:
    file = tmp_path / "note.txt"
    file.write_text("x", encoding="utf-8")
    resolved = resolve_launch(LaunchRequest(select=str(file)))
    assert resolved.select_dir == str(tmp_path)
    assert resolved.select_name == "note.txt"


def test_missing_paths_are_ignored_with_messages(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    resolved = resolve_launch(
        LaunchRequest(left=str(missing), right=str(missing), select=str(missing)),
        cwd=str(tmp_path),
    )
    assert resolved.left is None
    assert resolved.right is None
    assert resolved.select_dir is None
    assert len(resolved.messages) == 3
    assert all("不存在" in message or "目录" in message for message in resolved.messages)


def test_empty_values_are_ignored(tmp_path: Path) -> None:
    resolved = resolve_launch(LaunchRequest(left="", right="   ", select=""), cwd=str(tmp_path))
    assert resolved.left is None
    assert resolved.right is None
    assert resolved.select_dir is None
    assert resolved.messages == ("--left 为空，已忽略", "--right 为空，已忽略", "--select 为空，已忽略")


def test_directory_passed_as_select_wins_over_file_detection(tmp_path: Path) -> None:
    directory = tmp_path / "d"
    directory.mkdir()
    resolved = resolve_launch(LaunchRequest(select=str(directory)))
    assert resolved.select_name is None
