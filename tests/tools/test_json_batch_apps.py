"""`--json` 接入批次 5：领域应用（dynacalc / paramcad / paramesh / lscalc）。

只验证机读信封（schema、关键字段、退出码）；人读输出由 ``tests/apps`` 覆盖。
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from fcmd.apps.dynacalc import dynacalc
from fcmd.apps.paramcad import paramcad
from fcmd.apps.paramesh import paramesh
from fcmd.core.toolkit import run_tool
from fcmd.tools.calc import lscalc

SMALL_PARAMS = textwrap.dedent(
    """\
    schema: dynacalc/1
    factors:
      - key: density
        label: 装药密度
        unit: g/cm^3
        kind: continuous
        confidence: controlled
        levels:
          - {code: L1, value: 1.60}
          - {code: H1, value: 1.70}
      - key: inner_diameter
        label: 内径
        unit: mm
        kind: continuous
        confidence: controlled
        levels:
          - {code: ID1, value: 30.0}
      - key: gap_mm
        label: 间隙
        unit: mm
        kind: continuous
        confidence: uncalibrated
        levels:
          - {code: G0, value: 0.0}
          - {code: G1, value: TBD}
    matrix:
      main: [density, inner_diameter]
      supplementary:
        - [gap_mm]
      baseline:
        density: L1
        inner_diameter: ID1
        gap_mm: G0
    defaults:
      skip_fraction: 0.2
      tail_fraction: 0.1
      probes_mm: [50, 100]
      quench_fraction: 0.99
      gates:
        velocity_rel_diff: 0.02
        arrival_l2_rel: 0.03
        wall_impulse_rel: 0.05
        reference: M001
        pairs: []
    run:
      solver_path: ""
      ncpu: 2
      memory: auto
      parallel: smp
      mpi: intel
      extra_args: []
      env: {}
      timeout_s: 60
      retries: 1
      concurrency: 2
      fake: true
    """
)


def _payload(raw: str) -> dict[str, Any]:
    return json.loads(raw[raw.index("{") :])


@pytest.fixture()
def dynacalc_out(tmp_path: Path) -> Path:
    """小矩阵 4 工况：generate + 假求解器跑完。"""
    params = tmp_path / "small.yaml"
    params.write_text(SMALL_PARAMS, encoding="utf-8", newline="\n")
    out = tmp_path / "out"
    dynacalc.generate_command(params, out)
    dynacalc.run_command(out, fake=True, concurrency=2)
    return out


def test_dynacalc_report_json(dynacalc_out: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_tool("dynacalc", ["report", str(dynacalc_out), "--json"]) == 0
    payload = _payload(capsys.readouterr().out)
    data = payload["data"]
    assert payload["schema"] == "fcmd-result/1"
    assert data["schema"] == "dynacalc-report/1"
    assert data["total"] == 4
    assert data["report_path"] is None
    assert {case["status"] for case in data["cases"]} == {"ok"}


def test_dynacalc_compare_json(dynacalc_out: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_tool("dynacalc", ["report", str(dynacalc_out), "--json"]) == 0
    cases = _payload(capsys.readouterr().out)["data"]["cases"]
    left, right = cases[0]["code"], cases[1]["code"]
    assert run_tool("dynacalc", ["compare", str(dynacalc_out), left, right, "--json"]) == 0
    data = _payload(capsys.readouterr().out)["data"]
    assert data["left"] == left
    assert data["right"] == right
    assert data["count"] == len(data["rows"])


def test_dynacalc_gates_json(dynacalc_out: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        run_tool("dynacalc", ["gates", str(dynacalc_out), "--json"])
    assert excinfo.value.code == 1
    payload = _payload(capsys.readouterr().out)
    data = payload["data"]
    assert payload["ok"] is False
    assert data["reference"] == "M001"
    assert isinstance(data["checks"], list)
    assert data["failed"] >= 1


def test_dynacalc_report_missing_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_tool("dynacalc", ["report", str(tmp_path / "missing"), "--json"]) == 4
    assert _payload(capsys.readouterr().err)["code"] == "FILE_NOT_FOUND"


def test_paramcad_digest_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    model = tmp_path / "model.yaml"
    paramcad.example(model)
    capsys.readouterr()
    assert run_tool("paramcad", ["digest", str(model), "--json"]) == 0
    data = _payload(capsys.readouterr().out)["data"]
    assert data["count"] == 1
    assert data["models"][0]["id"] == "example"
    assert len(data["models"][0]["digest"]) == 64


def test_paramcad_inspect_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    model = tmp_path / "model.yaml"
    paramcad.example(model)
    capsys.readouterr()
    assert run_tool("paramcad", ["inspect", str(model), "--json"]) == 0
    data = _payload(capsys.readouterr().out)["data"]
    assert data["models"]
    assert data["checks"][0]["model"] == "example"
    assert isinstance(data["checks"][0]["problems"], list)


def test_paramcad_inspect_missing_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_tool("paramcad", ["inspect", str(tmp_path / "missing.yaml"), "--json"]) == 4
    assert _payload(capsys.readouterr().err)["code"] == "FILE_NOT_FOUND"


def test_paramesh_check_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    model = tmp_path / "model.yaml"
    paramesh.example(model)
    capsys.readouterr()
    assert run_tool("paramesh", ["check", str(model), "--config", str(tmp_path / "model.mesh.yaml"), "--json"]) == 0
    data = _payload(capsys.readouterr().out)["data"]
    assert data["model"]["id"]
    assert [plan["level"] for plan in data["plans"]] == ["coarse", "medium", "fine"]
    assert data["levels"] == ["coarse", "medium", "fine"]


def test_paramesh_report_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    model = tmp_path / "model.yaml"
    paramesh.example(model)
    capsys.readouterr()
    output = tmp_path / "meshes"
    paramesh.generate(model, output, levels="coarse", config=tmp_path / "model.mesh.yaml")
    capsys.readouterr()
    package = next(item for item in output.iterdir() if item.is_dir())
    assert run_tool("paramesh", ["report", str(package), "--json"]) == 0
    data = _payload(capsys.readouterr().out)["data"]
    assert data["manifest"]["model"]["id"]
    assert "quality" in data


def test_paramesh_report_missing_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_tool("paramesh", ["report", str(tmp_path / "missing"), "--json"]) == 4
    assert _payload(capsys.readouterr().err)["code"] == "FILE_NOT_FOUND"


def test_lscalc_status_json_windows(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    stdout = textwrap.dedent(
        """\
        映像名称                       PID 会话名              会话#       内存使用
        ls-dyna.exe                   1234 Console                    1     10,000 K
        ls-dyna_dbg.exe                 99 Console                    1      1,000 K
        """
    )
    monkeypatch.setattr(lscalc.sys, "platform", "win32")
    monkeypatch.setattr(
        lscalc, "run_command", lambda *args, **kwargs: SimpleNamespace(succeeded=True, returncode=0, stdout=stdout)
    )
    assert run_tool("lscalc", ["status", "--json"]) == 0
    data = _payload(capsys.readouterr().out)["data"]
    assert data["platform"] == "win32"
    assert data["running"] is True
    assert data["pids"] == [1234, 99]


def test_lscalc_status_json_linux_no_process(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(lscalc.sys, "platform", "linux")
    monkeypatch.setattr(
        lscalc, "run_command", lambda *args, **kwargs: SimpleNamespace(succeeded=False, returncode=1, stdout="")
    )
    assert run_tool("lscalc", ["status", "--json"]) == 0
    data = _payload(capsys.readouterr().out)["data"]
    assert data == {"platform": "linux", "running": False, "processes": [], "pids": []}
