"""wslman P4：``_source`` 下载源，以及 available / download / unregister / shutdown。

网络与外部命令全部注入假实现：下载用假 opener 返回内存响应，
``wsl --install`` / ``--unregister`` / ``--shutdown`` 走假执行器。
"""

from __future__ import annotations

import hashlib
import io
import json
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from typing import Any, override

import pytest

from fcmd.apps.wslman import _source, _transfer
from fcmd.apps.wslman import wslman as wslman_module
from fcmd.apps.wslman._config import WslmanConfig
from fcmd.apps.wslman._errors import WslmanError
from fcmd.core.toolkit import _TOOL_REGISTRY, list_subcommands, run_tool
from tests.apps.wslman import _fakes

_TABLE = """\
  NAME            STATE           VERSION
* Ubuntu          Running         2
  Debian          Stopped         2
"""

_EMPTY_TABLE = """\
  NAME            STATE           VERSION
"""

_ONLINE = """\
NAME            FRIENDLY NAME
Ubuntu          Ubuntu
Debian          Debian GNU/Linux
"""


def _env(runner: Any, *, tmp_path: Path, config: WslmanConfig | None = None) -> wslman_module.Environment:
    return wslman_module.Environment(
        runner=runner,
        capabilities=_fakes.capabilities(),
        registry=_fakes.snapshot(),
        config=config or WslmanConfig(path=tmp_path / "wslman.json"),
        platform_ok=True,
        lock_dir=tmp_path / "locks",
    )


def _use(monkeypatch: pytest.MonkeyPatch, env: wslman_module.Environment) -> None:
    monkeypatch.setattr(wslman_module, "_build_environment", lambda *args, **kwargs: env)


def _run(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, str, str]:
    code = run_tool("wslman", argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.out + captured.err


def _list(text: str = _TABLE) -> Any:
    return _fakes.make_result(stdout=_fakes.utf16(text))


class _FakeResponse:
    def __init__(self, body: bytes = b"", *, status: int = 200, headers: dict[str, str] | None = None) -> None:
        self._body = io.BytesIO(body)
        self.status = status
        self.headers = headers or {}
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)

    def close(self) -> None:
        self.closed = True


def _opener(response: _FakeResponse | BaseException) -> tuple[Any, list[urllib.request.Request]]:
    requests: list[urllib.request.Request] = []

    def open_(request: urllib.request.Request, timeout: float) -> _FakeResponse:
        requests.append(request)
        if isinstance(response, BaseException):
            raise response
        return response

    return open_, requests


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _manifest(**overrides: Any) -> bytes:
    entry: dict[str, Any] = {
        "name": "Ubuntu",
        "arch": "x86_64",
        "url": "https://example.com/ubuntu.tar.gz",
        "sha256": _sha(b"ubuntu"),
        "size": 6,
    }
    entry.update(overrides)
    return json.dumps({"distros": [entry]}).encode()


# ---------------------------------------------------------------------- #
# _source 单测
# ---------------------------------------------------------------------- #


def test_current_arch_normalises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_source.platform, "machine", lambda: "AMD64")
    assert _source.current_arch() == "x86_64"
    monkeypatch.setattr(_source.platform, "machine", lambda: "ARM64")
    assert _source.current_arch() == "aarch64"
    monkeypatch.setattr(_source.platform, "machine", lambda: "")
    assert _source.current_arch() == "unknown"


def test_parse_manifest_filters_and_aliases() -> None:
    raw = json.dumps(
        {
            "distros": [
                {"name": "Ubuntu", "arch": "amd64", "url": "https://e/u.tar", "checksum": "sha256:ABC"},
                {"name": "ArmOnly", "architecture": "arm64", "url": "https://e/a.tar"},
                {"name": "Store", "url": "https://e/store.appxbundle"},
                {"name": "NoUrl"},
            ]
        }
    )
    manifest = _source.parse_manifest(raw, "x86_64")

    assert [entry.name for entry in manifest.entries] == ["Ubuntu"]
    assert manifest.entries[0].sha256 == "abc"
    assert "Store" in manifest.unsupported


def test_parse_manifest_mapping_and_invalid() -> None:
    manifest = _source.parse_manifest({"Ubuntu": {"url": "https://e/u.tar", "size_bytes": 12}}, "x86_64")
    assert manifest.entries[0].name == "Ubuntu"
    assert manifest.entries[0].size == 12

    with pytest.raises(WslmanError) as excinfo:
        _source.parse_manifest(b"{broken")
    assert excinfo.value.code == "RUNTIME_ERROR"


def test_find_entry_variants() -> None:
    manifest = _source.parse_manifest(_manifest(), "x86_64")
    assert _source.find_entry(manifest, "ubuntu").name == "Ubuntu"

    store = _source.parse_manifest(json.dumps({"distros": [{"name": "Store", "url": "https://e/s.appx"}]}), "x86_64")
    with pytest.raises(WslmanError) as excinfo:
        _source.find_entry(store, "Store")
    assert "--source cli" in (excinfo.value.hint or "")

    with pytest.raises(WslmanError) as excinfo:
        _source.find_entry(manifest, "Fedora")
    assert excinfo.value.code == "NOT_FOUND"
    assert "import" in (excinfo.value.hint or "")


def test_download_name() -> None:
    assert _source.download_name("Ubuntu", "https://e/a/ubuntu-24.04.tar.gz") == "ubuntu-24.04.tar.gz"
    assert _source.download_name("Ubuntu 24", "https://e/download?id=1", "x86_64") == "Ubuntu_24-x86_64.tar"


def test_download_fresh_success(tmp_path: Path) -> None:
    body = b"ubuntu"
    opener, requests = _opener(_FakeResponse(body, headers={"Content-Length": str(len(body))}))
    target = tmp_path / "u.tar.gz"

    result = _source.download("https://example.com/u.tar.gz", target, expected_sha256=_sha(body), opener=opener)

    assert result.path == target
    assert result.size == len(body)
    assert result.verified is True
    assert result.resumed is False
    assert target.read_bytes() == body
    assert not _transfer.part_path(target).exists()
    assert not _transfer.part_path(target).with_name(_transfer.part_path(target).name + ".json").exists()
    assert len(requests) == 1


def test_download_resume_and_range_headers(tmp_path: Path) -> None:
    target = tmp_path / "u.tar"
    part = _transfer.part_path(target)
    part.write_bytes(b"abc")
    part.with_name(part.name + ".json").write_text(
        json.dumps({"etag": 'W/"1"', "last_modified": None, "total": 6}), encoding="utf-8"
    )
    opener, requests = _opener(_FakeResponse(b"def", status=206, headers={"Content-Range": "bytes 3-5/6"}))

    result = _source.download("https://example.com/u.tar", target, opener=opener)

    assert result.resumed is True
    assert target.read_bytes() == b"abcdef"
    assert requests[0].headers.get("Range") == "bytes=3-"
    assert requests[0].headers.get("If-range") == 'W/"1"'


def test_download_server_ignores_range(tmp_path: Path) -> None:
    target = tmp_path / "u.tar"
    part = _transfer.part_path(target)
    part.write_bytes(b"old")
    opener, _requests = _opener(_FakeResponse(b"complete", headers={"Content-Length": "8"}))

    result = _source.download("https://example.com/u.tar", target, opener=opener)

    assert result.resumed is False
    assert target.read_bytes() == b"complete"


def test_download_sha_mismatch_deletes_part(tmp_path: Path) -> None:
    body = b"ubuntu"
    opener, _requests = _opener(_FakeResponse(body, headers={"Content-Length": str(len(body))}))
    target = tmp_path / "u.tar"

    with pytest.raises(WslmanError) as excinfo:
        _source.download("https://example.com/u.tar", target, expected_sha256="0" * 64, opener=opener)

    assert excinfo.value.code == "CHECK_FAILED"
    assert not _transfer.part_path(target).exists()
    assert not target.exists()


def test_download_interrupt_keeps_part(tmp_path: Path) -> None:
    class _Interrupting(_FakeResponse):
        @override
        def read(self, size: int = -1) -> bytes:
            raise KeyboardInterrupt

    target = tmp_path / "u.tar"
    opener, _requests = _opener(_Interrupting(headers={"Content-Length": "10"}))

    with pytest.raises(KeyboardInterrupt):
        _source.download("https://example.com/u.tar", target, opener=opener)

    assert _transfer.part_path(target).exists()
    assert not target.exists()


def test_download_http_and_scheme_errors(tmp_path: Path) -> None:
    opener, _requests = _opener(urllib.error.HTTPError("https://e/u", 404, "not found", Message(), None))
    with pytest.raises(WslmanError) as excinfo:
        _source.download("https://e/u", tmp_path / "u.tar", opener=opener)
    assert excinfo.value.code == "RUNTIME_ERROR"

    with pytest.raises(WslmanError) as excinfo:
        _source.download("http://e/u", tmp_path / "u.tar")
    assert excinfo.value.code == "INVALID_ARGUMENT"
    assert excinfo.value.field == "url"


def test_file_source(tmp_path: Path) -> None:
    path = tmp_path / "u.tar"
    path.write_bytes(b"ubuntu")

    verified = _source.file_source(path, expected_sha256=_sha(b"ubuntu"))
    assert verified.verified is True

    with pytest.raises(WslmanError) as excinfo:
        _source.file_source(path, expected_sha256="0" * 64)
    assert excinfo.value.code == "CHECK_FAILED"
    assert path.exists()

    with pytest.raises(WslmanError) as excinfo:
        _source.file_source(tmp_path / "missing.tar")
    assert excinfo.value.code == "NOT_FOUND"


def test_fetch_manifest_errors() -> None:
    opener, _requests = _opener(_FakeResponse(b"{}"))
    assert _source.fetch_manifest("https://e/m.json", opener=opener) == b"{}"

    with pytest.raises(WslmanError) as excinfo:
        _source.fetch_manifest("http://e/m.json")
    assert excinfo.value.code == "INVALID_ARGUMENT"

    big, _ = _opener(_FakeResponse(b"x" * 10))
    with pytest.raises(WslmanError) as excinfo:
        _source.fetch_manifest("https://e/m.json", opener=big, max_bytes=5)
    assert excinfo.value.code == "RUNTIME_ERROR"


# ---------------------------------------------------------------------- #
# available
# ---------------------------------------------------------------------- #


def test_available_table_and_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _fakes.FakeRunner({("--list", "--online"): _fakes.make_result(stdout=_fakes.utf16(_ONLINE))})
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, out, _err = _run(capsys, ["available"])
    assert code == 0
    assert "Ubuntu" in out and "Debian GNU/Linux" in out

    code, out, _err = _run(capsys, ["available", "--json"])
    assert code == 0
    data = json.loads(out)["data"]
    assert data["count"] == 2
    assert data["distros"][0] == {"name": "Ubuntu", "friendly_name": "Ubuntu"}


def test_available_empty_and_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = _fakes.FakeRunner({("--list", "--online"): _fakes.make_result(stdout=_fakes.utf16("NAME\n"))})
    _use(monkeypatch, _env(empty, tmp_path=tmp_path))
    code, out, _err = _run(capsys, ["available"])
    assert code == 0
    assert "在线清单为空" in out

    failed = _fakes.FakeRunner(
        {("--list", "--online"): _fakes.make_result(returncode=1, stderr=_fakes.utf16("network down"))}
    )
    _use(monkeypatch, _env(failed, tmp_path=tmp_path))
    code, _out, err = _run(capsys, ["available"])
    assert code == 1
    assert "network down" in err
    assert "--source file" in err


# ---------------------------------------------------------------------- #
# download
# ---------------------------------------------------------------------- #


def _download_env(tmp_path: Path, runner: Any, **config: Any) -> wslman_module.Environment:
    return _env(runner, tmp_path=tmp_path, config=WslmanConfig(path=tmp_path / "wslman.json", **config))


def test_download_cli_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _fakes.FakeRunner(
        {
            ("--list", "--verbose"): _list(_EMPTY_TABLE),
            ("--install", "-d", "Ubuntu", "--no-launch"): _fakes.make_result(),
        }
    )
    _use(monkeypatch, _download_env(tmp_path, runner))

    code, out, _err = _run(capsys, ["download", "Ubuntu"])

    assert code == 0
    assert "wsl --install" in out
    assert "UAC" in out
    assert runner.calls[-1][1:] == ("--install", "-d", "Ubuntu", "--no-launch")


def test_download_cli_failure_falls_back_to_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    download_dir = tmp_path / "dl"
    runner = _fakes.FakeRunner(
        {
            ("--list", "--verbose"): _list(_EMPTY_TABLE),
            ("--install", "-d", "Ubuntu", "--no-launch"): _fakes.make_result(returncode=1, stderr=b"boom"),
        }
    )
    _use(monkeypatch, _download_env(tmp_path, runner, manifest_url="https://e/m.json", download_dir=str(download_dir)))
    monkeypatch.setattr(_source, "current_arch", lambda: "x86_64")
    monkeypatch.setattr(_source, "fetch_manifest", lambda url, **kwargs: _manifest())
    monkeypatch.setattr(
        _source,
        "download",
        lambda url, target, **kwargs: _source.DownloadResult(
            path=target, sha256=_sha(b"ubuntu"), size=6, verified=True, resumed=False
        ),
    )

    code, out, _err = _run(capsys, ["download", "Ubuntu"])

    assert code == 0
    assert "sbom" not in out
    assert "ubuntu.tar.gz" in out
    assert "下一步" in out


def test_download_cli_explicit_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _fakes.FakeRunner(
        {
            ("--list", "--verbose"): _list(_EMPTY_TABLE),
            ("--install", "-d", "Ubuntu", "--no-launch"): _fakes.make_result(returncode=1, stderr=b"denied"),
        }
    )
    _use(monkeypatch, _download_env(tmp_path, runner))

    code, _out, err = _run(capsys, ["download", "Ubuntu", "--source", "cli"])

    assert code == 1
    assert "denied" in err
    assert "UAC" in err


def test_download_file_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = tmp_path / "ubuntu.tar"
    archive.write_bytes(b"ubuntu")
    _use(monkeypatch, _download_env(tmp_path, _fakes.FakeRunner({})))

    code, out, _err = _run(
        capsys, ["download", "Ubuntu", "--source", "file", "--file", str(archive), "--sha256", _sha(b"ubuntu")]
    )

    assert code == 0
    assert str(archive) in out
    assert "已校验" in out

    code, _out, err = _run(capsys, ["download", "Ubuntu", "--source", "file"])
    assert code == 3
    assert "--file" in err

    code, _out, err = _run(capsys, ["download", "Ubuntu", "--source", "file", "--file", str(tmp_path / "no.tar")])
    assert code == 4


def test_download_manifest_requires_url_and_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _use(monkeypatch, _download_env(tmp_path, _fakes.FakeRunner({})))

    code, _out, err = _run(capsys, ["download", "Ubuntu", "--source", "manifest"])
    assert code == 3
    assert "manifest_url" in err

    code, _out, err = _run(capsys, ["download", "Ubuntu", "--source", "manifest", "--manifest-url", "https://e/m.json"])
    assert code == 3
    assert "--dir" in err


def test_download_invalid_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _use(monkeypatch, _download_env(tmp_path, _fakes.FakeRunner({})))

    code, _out, err = _run(capsys, ["download", "Ubuntu", "--source", "ftp"])

    assert code == 2
    assert "invalid choice" in err or "--source" in err


def test_download_preview_no_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _fakes.FakeRunner({})
    _use(monkeypatch, _download_env(tmp_path, runner))

    code, out, _err = _run(capsys, ["download", "Ubuntu", "--preview", "--json"])

    assert code == 0
    data = json.loads(out)["data"]
    assert data["method"] == "cli"
    assert data["system_install"] is True
    assert data["commands"]
    assert runner.calls == []


def test_download_preview_manifest_does_not_fetch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _use(monkeypatch, _download_env(tmp_path, _fakes.FakeRunner({})))

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("preview 不应联网")

    monkeypatch.setattr(_source, "fetch_manifest", forbidden)

    code, out, _err = _run(
        capsys,
        [
            "download",
            "Ubuntu",
            "--source",
            "manifest",
            "--manifest-url",
            "https://e/u.tar",
            "--dir",
            str(tmp_path / "dl"),
            "--preview",
        ],
    )

    assert code == 0
    assert "https://e/u.tar" in out
    assert not (tmp_path / "dl").exists()


# ---------------------------------------------------------------------- #
# unregister
# ---------------------------------------------------------------------- #


def test_unregister_preview_has_no_side_effects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _fakes.FakeRunner({("--list", "--verbose"): _list()})
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))
    backup = tmp_path / "backup.tar"

    code, out, _err = _run(
        capsys,
        ["unregister", "Ubuntu", "--preview", "--terminate-first", "--backup", str(backup), "--yes"],
    )

    assert code == 0
    assert "BasePath" in out
    assert "--unregister" in out
    assert [call[1] for call in runner.calls] == ["--list"]
    assert not backup.exists()
    assert not (tmp_path / "locks").exists()


def test_unregister_guards(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    runner = _fakes.FakeRunner({("--list", "--verbose"): _list()})
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, _out, err = _run(capsys, ["unregister", "Ubuntu", "--yes"])
    assert code == 3
    assert "--terminate-first" in err

    code, _out, err = _run(capsys, ["unregister", "Debian"])
    assert code == 2
    assert "--yes" in err and "--backup" in err


def test_unregister_success_without_backup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _fakes.FakeRunner(
        {
            ("--list", "--verbose"): _list(),
            ("--unregister", "Debian"): _fakes.make_result(),
        }
    )
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, out, _err = _run(capsys, ["unregister", "Debian", "--yes"])

    assert code == 0
    assert "已注销 Debian" in out
    assert "无回收站" in out
    assert [call[1] for call in runner.calls] == ["--list", "--unregister"]


def test_unregister_backup_then_remove(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    backup = tmp_path / "debian.tar"

    def export(argv: tuple[str, ...]) -> Any:
        _write_backup(argv)
        return _fakes.make_result()

    runner = _fakes.FakeRunner(
        {
            ("--list", "--verbose"): _list(),
            ("--unregister", "Debian"): _fakes.make_result(),
        },
        fallback=export,
    )
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, out, _err = _run(capsys, ["unregister", "Debian", "--yes", "--backup", str(backup)])

    assert code == 0
    assert "已备份" in out
    assert backup.read_bytes() == b"backup-bytes"
    assert [call[1] for call in runner.calls] == ["--list", "--export", "--unregister"]


def _write_backup(argv: tuple[str, ...]) -> None:
    target = Path(argv[-1])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"backup-bytes")


def test_unregister_backup_failure_aborts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    backup = tmp_path / "debian.tar"
    runner = _fakes.FakeRunner(
        {
            ("--list", "--verbose"): _list(),
            ("--export", "Debian", str(_transfer.part_path(backup))): _fakes.make_result(
                returncode=2, stderr=b"disk full"
            ),
        }
    )
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, _out, err = _run(capsys, ["unregister", "Debian", "--yes", "--backup", str(backup)])

    assert code == 1
    assert "已取消注销" in err
    assert [call[1] for call in runner.calls] == ["--list", "--export"]
    assert not _transfer.part_path(backup).exists()
    assert not backup.exists()


def test_unregister_backup_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    backup = tmp_path / "debian.tar"
    backup.write_bytes(b"old")
    runner = _fakes.FakeRunner({("--list", "--verbose"): _list()})
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, _out, err = _run(capsys, ["unregister", "Debian", "--yes", "--backup", str(backup)])

    assert code == 3
    assert "已存在" in err
    assert backup.read_bytes() == b"old"


# ---------------------------------------------------------------------- #
# shutdown
# ---------------------------------------------------------------------- #


def test_shutdown_preview_zero_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _fakes.FakeRunner({})
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, out, _err = _run(capsys, ["shutdown", "--preview", "--json"])

    assert code == 0
    data = json.loads(out)["data"]
    assert data["preview"] is True
    assert data["shut_down"] is False
    assert data["destructive"] is True
    assert runner.calls == []


def test_shutdown_requires_yes_and_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _fakes.FakeRunner({})
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, _out, err = _run(capsys, ["shutdown"])
    assert code == 2
    assert "--yes" in err

    stopped = _fakes.FakeRunner(
        {
            ("--shutdown",): _fakes.make_result(),
            ("--list", "--verbose"): _list(_EMPTY_TABLE),
        }
    )
    _use(monkeypatch, _env(stopped, tmp_path=tmp_path))
    code, out, _err = _run(capsys, ["shutdown", "--yes"])
    assert code == 0
    assert "已执行" in out
    assert "运行中发行版: 无" in out
    assert [call[1] for call in stopped.calls] == ["--shutdown", "--list"]


# ---------------------------------------------------------------------- #
# 注册
# ---------------------------------------------------------------------- #


def test_available_not_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _fakes.FakeRunner(
        {("--list", "--online"): _fakes.make_result(returncode=1, stderr=_fakes.utf16("WSL is not installed"))}
    )
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, _out, err = _run(capsys, ["available"])

    assert code == 1
    assert "未启用" in err


def test_download_invalid_source_direct(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _use(monkeypatch, _download_env(tmp_path, _fakes.FakeRunner({})))

    wslman_module.download("Ubuntu", source="ftp")  # type: ignore[arg-type]

    assert "不支持的 --source" in capsys.readouterr().out


def test_download_cli_without_no_launch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = _download_env(tmp_path, _fakes.FakeRunner({("--list", "--verbose"): _list(_EMPTY_TABLE)}))
    runner = _fakes.FakeRunner(
        {
            ("--list", "--verbose"): _list(_EMPTY_TABLE),
            ("--install", "-d", "Ubuntu"): _fakes.make_result(),
        }
    )
    env = wslman_module.Environment(
        runner=runner,
        capabilities=_fakes.capabilities(has_install_no_launch=False),
        registry=_fakes.snapshot(),
        config=env.config,
        platform_ok=True,
    )
    _use(monkeypatch, env)

    code, _out, _err = _run(capsys, ["download", "Ubuntu", "--source", "cli"])

    assert code == 0
    assert runner.calls[-1][1:] == ("--install", "-d", "Ubuntu")


def test_download_cli_already_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _fakes.FakeRunner({("--list", "--verbose"): _list()})
    _use(monkeypatch, _download_env(tmp_path, runner))

    code, _out, err = _run(capsys, ["download", "Ubuntu"])

    assert code == 3
    assert "已安装" in err
    assert [call[1] for call in runner.calls] == ["--list"]


def test_download_manifest_existing_target_and_preflight(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    download_dir = tmp_path / "dl"
    download_dir.mkdir()
    (download_dir / "ubuntu.tar.gz").write_bytes(b"old")
    _use(monkeypatch, _download_env(tmp_path, _fakes.FakeRunner({}), manifest_url="https://e/m.json"))
    monkeypatch.setattr(_source, "current_arch", lambda: "x86_64")
    monkeypatch.setattr(_source, "fetch_manifest", lambda url, **kwargs: _manifest())

    code, _out, err = _run(capsys, ["download", "Ubuntu", "--source", "manifest", "--dir", str(download_dir)])
    assert code == 3
    assert "已存在" in err

    def short(*args: Any, **kwargs: Any) -> Any:
        return wslman_module.SpacePreflight(
            target=download_dir / "x.tar",
            free_bytes=1,
            required_bytes=2,
            estimated=False,
            reserve_bytes=0,
            ok=False,
            detail="空间不足（测试）",
        )

    monkeypatch.setattr(wslman_module, "preflight_space", short)
    code, _out, err = _run(
        capsys, ["download", "Ubuntu", "--source", "manifest", "--dir", str(download_dir), "--overwrite"]
    )
    assert code == 1
    assert "空间不足" in err


def test_download_manifest_interrupt_and_oserror(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    download_dir = tmp_path / "dl"
    _use(monkeypatch, _download_env(tmp_path, _fakes.FakeRunner({}), manifest_url="https://e/m.json"))
    monkeypatch.setattr(_source, "current_arch", lambda: "x86_64")
    monkeypatch.setattr(_source, "fetch_manifest", lambda url, **kwargs: _manifest())
    args = ["download", "Ubuntu", "--source", "manifest", "--dir", str(download_dir)]

    def interrupt(*args: Any, **kwargs: Any) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr(_source, "download", interrupt)
    code, _out, _err = _run(capsys, args)
    assert code == 130

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise OSError("磁盘错误")

    monkeypatch.setattr(_source, "download", fail)
    code, _out, err = _run(capsys, args)
    assert code == 1
    assert "磁盘错误" in err


def test_download_preview_file_and_missing_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = tmp_path / "u.tar"
    archive.write_bytes(b"u")
    _use(monkeypatch, _download_env(tmp_path, _fakes.FakeRunner({})))

    code, out, _err = _run(capsys, ["download", "Ubuntu", "--source", "file", "--file", str(archive), "--preview"])
    assert code == 0
    assert str(archive) in out

    code, _out, err = _run(capsys, ["download", "Ubuntu", "--source", "manifest", "--preview"])
    assert code == 3
    assert "manifest_url" in err


def test_unregister_terminate_first_and_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _fakes.FakeRunner(
        {
            ("--list", "--verbose"): _list(),
            ("--terminate", "Ubuntu"): _fakes.make_result(),
            ("--unregister", "Ubuntu"): _fakes.make_result(),
        }
    )
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, _out, _err = _run(capsys, ["unregister", "Ubuntu", "--yes", "--terminate-first"])
    assert code == 0
    assert [call[1] for call in runner.calls] == ["--list", "--terminate", "--unregister"]


def test_unregister_interrupt_and_oserror(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _fakes.FakeRunner({("--list", "--verbose"): _list()})
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    def interrupt(*args: Any, **kwargs: Any) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr(wslman_module, "_run_step", interrupt)
    code, _out, _err = _run(capsys, ["unregister", "Debian", "--yes"])
    assert code == 130

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise OSError("磁盘错误")

    monkeypatch.setattr(wslman_module, "_run_step", fail)
    code, _out, err = _run(capsys, ["unregister", "Debian", "--yes"])
    assert code == 1
    assert "文件操作失败" in err


def test_unregister_backup_stage_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    backup = tmp_path / "debian.tar"
    runner = _fakes.FakeRunner({("--list", "--verbose"): _list()})
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    monkeypatch.setattr(wslman_module, "volume_problem", lambda path: "目标卷是 FAT32")
    code, _out, err = _run(capsys, ["unregister", "Debian", "--yes", "--backup", str(backup)])
    assert code == 3
    assert "FAT32" in err
    monkeypatch.setattr(wslman_module, "volume_problem", _transfer.volume_problem)

    monkeypatch.setattr(
        wslman_module,
        "preflight_space",
        lambda *args, **kwargs: wslman_module.SpacePreflight(
            target=backup,
            free_bytes=1,
            required_bytes=2,
            estimated=False,
            reserve_bytes=0,
            ok=False,
            detail="空间不足（测试）",
        ),
    )
    code, _out, err = _run(capsys, ["unregister", "Debian", "--yes", "--backup", str(backup)])
    assert code == 1
    assert "已取消注销" in err


def test_unregister_backup_export_without_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    backup = tmp_path / "debian.tar"
    runner = _fakes.FakeRunner(
        {
            ("--list", "--verbose"): _list(),
            ("--export", "Debian", str(_transfer.part_path(backup))): _fakes.make_result(),
        }
    )
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, _out, err = _run(capsys, ["unregister", "Debian", "--yes", "--backup", str(backup)])

    assert code == 1
    assert "未生成备份文件" in err


def test_parse_manifest_skips_bad_items_and_handles_string() -> None:
    manifest = _source.parse_manifest(
        [
            "not-a-mapping",
            {"url": "https://e/no-name.tar"},
            {"name": "NoUrl"},
            {"name": " ", "url": "https://e/blank.tar"},
            {"name": "Ok", "url": "https://e/ok.tar"},
        ],
        "x86_64",
    )
    assert [entry.name for entry in manifest.entries] == ["Ok"]
    assert _source._entries_of(42) == []

    with pytest.raises(WslmanError) as excinfo:
        _source.parse_manifest("{broken")
    assert excinfo.value.code == "RUNTIME_ERROR"


def test_redirect_handler_rejects_non_https() -> None:
    handler = _source._HTTPSOnlyRedirect()
    with pytest.raises(urllib.error.HTTPError):
        handler.redirect_request(None, None, 302, "Found", {}, "http://e/a.tar")


def test_fetch_manifest_http_and_network_errors() -> None:
    opener, _requests = _opener(urllib.error.HTTPError("https://e/m", 500, "boom", Message(), None))
    with pytest.raises(WslmanError) as excinfo:
        _source.fetch_manifest("https://e/m", opener=opener)
    assert excinfo.value.code == "RUNTIME_ERROR"

    failing, _requests = _opener(urllib.error.URLError("no route"))
    with pytest.raises(WslmanError) as excinfo:
        _source.fetch_manifest("https://e/m", opener=failing)
    assert "no route" in str(excinfo.value)


def test_download_preview_cli_without_no_launch_and_missing_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = _download_env(tmp_path, _fakes.FakeRunner({}))
    no_no_launch = wslman_module.Environment(
        runner=_fakes.FakeRunner({}),
        capabilities=_fakes.capabilities(has_install_no_launch=False),
        registry=base.registry,
        config=base.config,
        platform_ok=True,
    )
    _use(monkeypatch, no_no_launch)

    code, out, _err = _run(capsys, ["download", "Ubuntu", "--source", "cli", "--preview", "--json"])
    assert code == 0
    assert json.loads(out)["data"]["commands"][0][-1] == "Ubuntu"

    no_install = wslman_module.Environment(
        runner=_fakes.FakeRunner({}),
        capabilities=_fakes.capabilities(has_install=False),
        registry=base.registry,
        config=base.config,
        platform_ok=True,
    )
    _use(monkeypatch, no_install)
    wslman_module.download("Ubuntu", source="cli")
    assert "不支持 --install" in capsys.readouterr().out


def test_registers_p4_subcommands() -> None:
    expected = {
        "available",
        "backup-remove",
        "backup-verify",
        "backups",
        "compress",
        "config",
        "copy",
        "doctor",
        "download",
        "export",
        "import",
        "info",
        "list",
        "open",
        "set-default",
        "shutdown",
        "status",
        "terminate",
        "unregister",
    }
    assert set(_TOOL_REGISTRY["wslman"]) == expected
    assert set(list_subcommands("wslman")) == expected
