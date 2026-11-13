"""paramesh - 参数化模型到仿真网格的中间层（CLI）。

读取 ``dynacalc-model/1``，生成二维轴对称（r–z）三档网格、LS-DYNA 关键字
网格片段与质量报告。不求解、不写材料参数，也不在契约定稿前写入 dynacalc
的 deck 目录。

示例
----
    fcmd paramesh example model.yaml
    fcmd paramesh check model.yaml --config model.mesh.yaml
    fcmd paramesh generate model.yaml meshes --config model.mesh.yaml
    fcmd paramesh report meshes/M001-geometry-coarse-xxxxxxxx
    fcmd paramesh contract
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import fcmd
from fcmd.apps.paramesh._config import LEVELS, load_config
from fcmd.apps.paramesh._errors import ParameshError
from fcmd.apps.paramesh._mesh import build_mesh
from fcmd.apps.paramesh._model import (
    format_model_summary,
    load_models,
    select_model,
)
from fcmd.apps.paramesh._package import (
    CONTRACT_DRAFT,
    build_manifest,
    load_manifest,
    load_quality,
    write_package,
)
from fcmd.apps.paramesh._quality import quality_report
from fcmd.apps.paramesh._scale import format_plan, plan_all
from fcmd.console import print_error

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["check", "contract", "example", "generate", "report"]

__version__ = "0.1.0"

_DESCRIPTION = (
    "参数化模型 → 二维轴对称仿真网格：三档尺度、局部加密、质量报告与 LS-DYNA "
    "网格片段；不做求解与材料参数，不在契约未定稿前写入 deck"
)

_EXAMPLE_MODEL = """\
# 占位示例：取自 dynacalc-model/1 契约第 11 节示例（黄金指纹向量 12.1）
# 所有数值仅用于跑通链路，不是真实配方或产品尺寸。
schema: dynacalc-model/1
meta: {generator: paramesh, generator_version: "0.1.0"}
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

_DEMO_CONFIG = """\
# paramesh 网格配置（与 example 配套的快速预览档位；全部为占位数值）
schema: paramesh/1
dimension: axisymmetric-2d
reaction_zone_mm: 6.0        # 占位：反应区尺度
levels:
  coarse: 1                  # 快速预览：1/1、1/2、1/4；正式使用请改为 8/16/32
  medium: 2
  fine: 4
wall_layers:
  min: 6
  max: 10
growth: 1.2
refinement_factor: 0.5
initiation_zone_mm: 20.0     # 占位：起爆端成长区长度
thin_layer_threshold_mm: 2.0 # 占位：小于该厚度时加密药层
section_angle_deg: null      # null = 自动选择最薄壁 / 最薄药层截面
"""


def _run(action: Callable[[], None]) -> None:
    try:
        action()
    except ParameshError as exc:
        print_error(str(exc))


def _write_new(path: Path, text: str, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise ParameshError(f"文件已存在：{path}；如需覆盖请显式开启 overwrite")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise ParameshError(f"写入 {path} 失败：{exc}") from exc


def _parse_levels(text: str) -> tuple[str, ...]:
    value = text.strip().lower()
    if value in {"", "all"}:
        return LEVELS
    selected: list[str] = []
    for item in value.split(","):
        level = item.strip()
        if not level:
            continue
        if level not in LEVELS:
            raise ParameshError(f"未知档位 {level!r}；可选 {', '.join(LEVELS)} 或 all")
        if level not in selected:
            selected.append(level)
    if not selected:
        raise ParameshError("未选择任何网格档位")
    return tuple(selected)


@fcmd.tool("paramesh", subcommand="example", help="写入占位模型与配套网格配置（虚构，仅用于跑通链路）")
def example(output: Path, overwrite: bool = False) -> None:
    """写出 ``dynacalc-model/1`` 占位模型与 ``paramesh/1`` 配置。

    Parameters
    ----------
    output:
        模型 YAML 路径；配置文件写到同目录 ``<stem>.mesh.yaml``
    overwrite:
        已存在时是否覆盖
    """

    def action() -> None:
        config_path = output.with_name(f"{output.stem}.mesh.yaml")
        for target in (output, config_path):
            if target.exists() and not overwrite:
                raise ParameshError(f"文件已存在：{target}；如需覆盖请显式开启 overwrite")
        _write_new(output, _EXAMPLE_MODEL, overwrite=overwrite)
        _write_new(config_path, _DEMO_CONFIG, overwrite=overwrite)
        print(f"已写入示例模型：{output}")
        print(f"已写入网格配置：{config_path}")
        print("提示：示例档位为 1/1、1/2、1/4，仅用于快速预览；正式使用请改为 8/16/32。")

    _run(action)


@fcmd.tool("paramesh", subcommand="check", help="检查模型与配置，输出三档尺度、壁厚层数与未标定项（不写文件）")
def check(model_file: Path, config: Path | None = None, model_id: str | None = None) -> None:
    """只读检查：模型可解析、特征尺度可确定、三档层数可计算。

    Parameters
    ----------
    model_file:
        ``dynacalc-model/1`` YAML 文件
    config:
        ``paramesh/1`` 网格配置；省略时使用默认值（仍必须由配置提供反应区尺度）
    model_id:
        多模型文件时选择指定 id；省略取第一个
    """

    def action() -> None:
        mesh_config, config_warnings = load_config(config)
        model = select_model(load_models(model_file), model_id)
        print(format_model_summary(model))
        print()
        for warning in config_warnings:
            print(f"配置警告：{warning}")
        for plan in plan_all(model, mesh_config):
            print(f"--- {plan.level} ---")
            print(format_plan(plan))
            print()
        print("检查完成：未写入任何文件。")

    _run(action)


@fcmd.tool("paramesh", subcommand="generate", help="生成三档网格包（LS-DYNA 片段 + 质量报告 + 清单）")
def generate(  # noqa: PLR0913, PLR0917 - CLI 参数由子命令接口固定
    model_file: Path,
    output: Path,
    levels: str = "all",
    config: Path | None = None,
    model_id: str | None = None,
    overwrite: bool = False,
) -> None:
    """生成网格包。

    Parameters
    ----------
    model_file:
        ``dynacalc-model/1`` YAML 文件
    output:
        网格包根目录；每档写到 ``<output>/<mesh_id>/``
    levels:
        ``all`` 或逗号分隔的 ``coarse,medium,fine``
    config:
        ``paramesh/1`` 网格配置
    model_id:
        多模型文件时选择指定 id
    overwrite:
        已存在的网格包是否覆盖
    """

    def action() -> None:
        selected = _parse_levels(levels)
        mesh_config, config_warnings = load_config(config)
        model = select_model(load_models(model_file), model_id)
        for warning in config_warnings:
            print(f"配置警告：{warning}")
        print(f"模型 {model.model_id}  digest={model.digest}")
        for level in selected:
            mesh = build_mesh(model, mesh_config, level)
            report = quality_report(mesh)
            manifest = build_manifest(mesh, report)
            path = write_package(mesh, report, output, overwrite=overwrite)
            print(
                f"{level}: mesh_id={manifest['mesh_id']}  "
                f"节点 {mesh.node_count}  单元 {mesh.element_count}  "
                f"尺寸 {report.size_min_mm:.6g}–{report.size_max_mm:.6g} mm  → {path}"
            )
            print(
                f"  长宽比 {report.aspect_min:.4g}–{report.aspect_max:.4g}，"
                f"退化/负体积 {report.degenerate}/{report.negative_volume}，"
                f"壁厚层数 {report.wall_layers_actual}"
            )
            for warning in mesh.warnings:
                print(f"  警告：{warning}")

    _run(action)


@fcmd.tool("paramesh", subcommand="report", help="读取网格包并输出清单与质量报告")
def report(mesh_dir: Path, as_json: bool = False) -> None:
    """输出已生成网格包的摘要或原始 JSON。

    Parameters
    ----------
    mesh_dir:
        网格包目录（含 manifest.json）
    as_json:
        输出合并后的清单与质量 JSON
    """

    def action() -> None:
        manifest = load_manifest(mesh_dir)
        quality = load_quality(mesh_dir)
        if as_json:
            print(json.dumps({"manifest": manifest, "quality": quality}, ensure_ascii=False, indent=2))
            return
        model = manifest.get("model", {})
        counts = manifest.get("counts", {})
        size_range = manifest.get("size_range_mm", {})
        print(f"网格包：{manifest.get('mesh_id')}")
        print(f"档位 / 维度：{manifest.get('level')} / {manifest.get('dimension')}")
        print(f"模型：{model.get('id')}  digest={model.get('digest')}")
        print(f"模型源文件：{model.get('source')}")
        print(f"节点 / 单元：{counts.get('nodes')} / {counts.get('elements')}")
        print(f"单元尺寸范围：{size_range.get('min')} – {size_range.get('max')} mm")
        print(f"几何指纹：{manifest.get('geometry_key')}")
        sets = manifest.get("sets", {})
        node_sets = sets.get("nodes", {})
        print(f"节点集合：{', '.join(node_sets) if node_sets else '（无）'}")
        tbd = manifest.get("tbd", [])
        if tbd:
            print(f"未标定：{', '.join(tbd)}")
        quality_text = mesh_dir / "quality.txt"
        if quality_text.is_file():
            print()
            print(quality_text.read_text(encoding="utf-8").rstrip())
        else:
            print()
            print(json.dumps(quality, ensure_ascii=False, indent=2))

    _run(action)


@fcmd.tool("paramesh", subcommand="contract", help="输出网格 → deck 交接契约草案（供 dynacalc 评审）")
def contract() -> None:
    """打印交接契约草案。"""

    def action() -> None:
        print(CONTRACT_DRAFT.rstrip())

    _run(action)


@fcmd.main("paramesh")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体


if __name__ == "__main__":  # pragma: no cover
    main()
