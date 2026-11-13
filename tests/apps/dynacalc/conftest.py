"""dynacalc 测试夹具：全 TBD 参数表、小矩阵参数表与模型描述。"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from fcmd.apps.dynacalc._params import example_params

MODEL_YAML = textwrap.dedent(
    """\
    schema: dynacalc-model/1
    meta: {generator: paramcad, generator_version: "0.1.0"}
    models:
      - id: M001-geometry
        case_scope: {include: [M001]}
        units: {length: mm, density: g/cm^3, velocity: m/s}
        confidence: {geometry: controlled, density_field: uncalibrated}
        geometry:
          outer_diameter: 60.0
          inner_diameter: 30.0
          length: 300.0
          wall_thickness: {nominal: 15.0, min: 14.2, max: 15.8}
          eccentricity: {radial_mm: 0.15, circumferential_mm: 0.0, angle_deg: 0.0}
        drawing:
          passes:
            - {index: 1, diameter_reduction: 0.08, wall_thickness_change: -0.03, die_angle_deg: 12.0}
        density_field:
          axial:
            kind: polyline
            samples:
              - {position: 0.0, density: 1.62}
              - {position: 0.5, density: 1.65}
              - {position: 1.0, density: 1.60}
          local_defects:
            - id: D1
              kind: void
              location: {axial: 0.4, radial: 0.9, angle_deg: 30}
              size_mm: 2.0
              severity: TBD
        interface:
          gap_mm: 0.05
          debond: {state: bonded, extent_deg: 0.0}
        tbd: ["density_field.local_defects[0].severity"]
    """
)

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


@pytest.fixture
def params_file(tmp_path: Path) -> Path:
    """示例参数表（7 因子，主矩阵 48 组 + 补充组 6 组，全部 TBD）。"""
    path = tmp_path / "params.yaml"
    path.write_text(example_params(), encoding="utf-8", newline="\n")
    return path


@pytest.fixture
def small_params(tmp_path: Path) -> Path:
    """可填值的小矩阵（4 个工况），用于快速端到端。"""
    path = tmp_path / "small.yaml"
    path.write_text(SMALL_PARAMS, encoding="utf-8", newline="\n")
    return path


@pytest.fixture
def model_text() -> str:
    """模型描述的原始 YAML 文本（供测试按需改动）。"""
    return MODEL_YAML


@pytest.fixture
def model_file(tmp_path: Path, model_text: str) -> Path:
    """合法模型描述：绑定 M001，含拉拔、轴向密度曲线与一个局部缺陷。"""
    path = tmp_path / "model.yaml"
    path.write_text(model_text, encoding="utf-8", newline="\n")
    return path
