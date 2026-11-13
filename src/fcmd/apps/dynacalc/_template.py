"""dynacalc deck 模板：占位符渲染、必填校验与可复现生成（纯逻辑）。

模板是纯文本，占位符写法：

- ``${key}``：必填。工况参数或内建键缺该键时生成失败并给出模板行号；
- ``${key?}``：可选。缺该键时写入 ``TBD``，并计入 TBD 字段。

内建键：``case_code`` / ``case_group`` / ``case_hash`` / ``schema``。因子值原样
渲染（数值用最短表示），``TBD`` 永远保持 ``TBD``，不会被替换成任何材料卡数值。

生成物可复现：UTF-8、``\\n`` 换行、末尾换行、无时间戳；同一参数重复生成逐字节
一致。``case.json`` 与根目录清单同样排序输出。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from fcmd.apps.dynacalc._mesh import AXISYMMETRIC_DIMENSION, MeshPackage, MeshSet
from fcmd.apps.dynacalc._model import ModelSet, ModelSpec, apply_model
from fcmd.apps.dynacalc._params import SCHEMA, TBD, Case, ParamTable, build_cases, case_fingerprint, is_tbd

__all__ = [
    "AXISYMMETRIC_TEMPLATE",
    "BUILTIN_KEYS",
    "DEFAULT_TEMPLATE",
    "DEFAULT_TEMPLATE_FAMILY",
    "GENERATOR_VERSION",
    "TEMPLATE_FAMILIES",
    "DeckError",
    "DeckPlan",
    "GenerationResult",
    "Manifest",
    "ManifestCase",
    "default_template",
    "generate_decks",
    "load_manifest",
    "load_template",
    "plan_deck",
    "render_template",
    "template_digest",
    "template_for",
    "template_placeholders",
    "write_manifest",
]

GENERATOR_VERSION = "0.1.0"

BUILTIN_KEYS = ("case_code", "case_group", "case_hash", "schema")

_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(\?)?\}")

DEFAULT_TEMPLATE = """$ dynacalc 生成：工况 ${case_code}（${case_group}），参数指纹 ${case_hash}
$ 本文件由模板渲染，禁止手改后回灌参数表；所有 TBD 必须由受控材料卡填入。
$ 结构仅演示关键字组织方式（材料 / EOS / ALE 多物质 / 起爆 / 输出），不含任何真实参数。
*KEYWORD
*TITLE
dynacalc ${case_code}
*CONTROL_TERMINATION
$ endtim（占位）
TBD, 0, 0.0, 0.0, 0.0
*CONTROL_TIMESTEP
$ dtinit, tssfac
0.0, 0.9
*CONTROL_ENERGY
$ hgen, rwen, slnt
2, 2, 2
*CONTROL_ALE
$ dct, nadv, meth, afac, bfac
2, 1, 2, -1.0, 0.0
*DATABASE_BINARY_D3PLOT
$ dt（占位）
TBD
*DATABASE_GLSTAT
$ dt（占位）
TBD
*DATABASE_MATSUM
$ dt（占位）
TBD
*DATABASE_HISTORY_NODE
$ 波面与管壁探针节点号（占位，网格冻结后填写）
1, 2, 3
*PART
charge
$ pid, secid, mid, eosid, hgid
1, 1, 1, 1, 0
*PART
tube
$ pid, secid, mid, eosid, hgid
2, 2, 2, 0, 0
*PART
air
$ pid, secid, mid, eosid, hgid
3, 3, 3, 3, 0
*SECTION_SOLID
$ secid, elform（11 = ALE 多物质单点积分，占位）
1, 11
*SECTION_SOLID
2, 1
*SECTION_SOLID
3, 11
*MAT_HIGH_EXPLOSIVE_BURN
$ rho0 = 装药平均密度（受控参数）
${density?}
$ D, PCJ, BETA（占位）
TBD, TBD, 0.0
*EOS_JWL
$ A, B, R1, R2, OMEGA, E0, V0（占位）
TBD, TBD, TBD, TBD, TBD, TBD, 1.0
*MAT_PLASTIC_KINEMATIC
$ rho0, E, PR, SIGY, ETAN（占位）
TBD, TBD, TBD, TBD, TBD
*MAT_NULL
$ rho0, PC, MU（占位）
TBD, 0.0, 0.0
*EOS_IDEAL_GAS
$ C0..C6, E0, V0（占位）
TBD, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, TBD, 1.0
*ALE_MULTI_MATERIAL_GROUP
$ sid, idtype, 材料号
1, 1
1, 2
1, 3
*CONSTRAINED_LAGRANGE_IN_SOLID
$ slave, master, sstyp, mstyp, nquad, ctype, direc, ...（占位）
2, 1, 0, 0, 2, 4, 2, 0
$ 初始几何与缺陷状态（模型描述优先；未提供则回落参数矩阵；仍未填即 TBD）
$ outer_diameter(mm) = ${outer_diameter?}
$ inner_diameter(mm) = ${inner_diameter?}
$ length(mm) = ${length?}
$ wall_thickness(mm) = ${wall_thickness?}
$ wall_thickness_range(mm) = ${wall_thickness_min?} .. ${wall_thickness_max?}
$ eccentricity radial/circum(mm) = ${eccentricity_radial?} / ${eccentricity_circumferential?}
$ eccentricity angle(deg) = ${eccentricity_angle_deg?}
$ drawing passes / reduction / wall change = ${drawing_passes?} / ${drawing_reduction_total?} / ${drawing_wall_change_total?}
$ density axial kind / min / max / mean = ${density_axial_kind?} / ${density_axial_min?} / ${density_axial_max?} / ${density_axial_mean?}
$ density radial / circumferential kind = ${density_radial_kind?} / ${density_circumferential_kind?}
$ defects count / ids = ${defect_count?} / ${defect_ids?}
$ axial_density = ${axial_density?}
$ eccentricity = ${eccentricity?}
$ gap_mm = ${gap_mm?}
$ debond state / extent(deg) = ${debond_state?} / ${debond_extent_deg?}
$ initiation_strength = ${initiation_strength?}
$ model = ${model_id?} (${model_digest?})
*INITIAL_DETONATION
$ x, y, z, t0（占位，实际坐标由几何 / 工艺映射冻结后填写）
0.0, 0.0, 0.0, 0.0
*END
"""

AXISYMMETRIC_TEMPLATE = """$ dynacalc 生成：工况 ${case_code}（${case_group}），参数指纹 ${case_hash}
$ 模板族：axisymmetric-2d（x = 轴向，y = 径向，z = 0；ELFORM = 14 轴对称壳）
$ 网格片段由 paramesh 生成并通过网格包校验；*PART / *SECTION_SHELL / 材料卡由本 deck 生成。
$ 本文件由模板渲染，禁止手改后回灌参数表；所有 TBD 必须由受控材料卡填入。
*KEYWORD
*TITLE
dynacalc ${case_code} axisymmetric-2d
$ 网格包：${mesh_id}（level ${mesh_level}，geometry_key ${mesh_geometry_key}）
$ mesh.k 只含节点 / 单元 / 集合；不得重编号，也不得在本 deck 里重复其关键字
*INCLUDE
${mesh_include}
*CONTROL_TERMINATION
$ endtim（占位）
TBD, 0, 0.0, 0.0, 0.0
*CONTROL_TIMESTEP
$ dtinit, tssfac
0.0, 0.9
*CONTROL_ENERGY
$ hgen, rwen, slnt
2, 2, 2
*DATABASE_BINARY_D3PLOT
$ dt（占位）
TBD
*DATABASE_GLSTAT
$ dt（占位）
TBD
*DATABASE_MATSUM
$ dt（占位）
TBD
*DATABASE_HISTORY_NODE
$ 探针节点号（manifest.probes：轴面站位 / 壁面 / 界面），按节点号升序
${history_nodes}
$ 部件与截面：pid = part_id、secid = section.secid，材料号按 role 映射
$ 壳厚固定为周向单位厚度 1.0，区域径向总厚度只出现在 manifest 的 section.region_thickness_mm
${parts_block}
${sections_block}
*MAT_HIGH_EXPLOSIVE_BURN
$ rho0 = 装药平均密度（受控参数）
${density?}
$ D, PCJ, BETA（占位）
TBD, TBD, 0.0
*EOS_JWL
$ A, B, R1, R2, OMEGA, E0, V0（占位）
TBD, TBD, TBD, TBD, TBD, TBD, 1.0
*MAT_PLASTIC_KINEMATIC
$ rho0, E, PR, SIGY, ETAN（占位）
TBD, TBD, TBD, TBD, TBD
*MAT_NULL
$ rho0, PC, MU（占位）
TBD, 0.0, 0.0
*EOS_IDEAL_GAS
$ C0..C6, E0, V0（占位）
TBD, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, TBD, 1.0
$ 初始几何与缺陷状态（模型描述优先；未提供则回落参数矩阵；仍未填即 TBD）
$ outer_diameter(mm) = ${outer_diameter?}
$ inner_diameter(mm) = ${inner_diameter?}
$ length(mm) = ${length?}
$ wall_thickness(mm) = ${wall_thickness?}
$ wall_thickness_range(mm) = ${wall_thickness_min?} .. ${wall_thickness_max?}
$ gap_mm = ${gap_mm?}
$ debond state / extent(deg) = ${debond_state?} / ${debond_extent_deg?}
$ model = ${model_id?} (${model_digest?})
*INITIAL_DETONATION
$ x = 轴向、y = 径向、z = 0（manifest.detonation，起爆端轴线）
${detonation_x}, ${detonation_y}, ${detonation_z}, ${detonation_t0}
*END
"""

DEFAULT_TEMPLATE_FAMILY = "3d-ale"

TEMPLATE_FAMILIES: dict[str, str] = {
    DEFAULT_TEMPLATE_FAMILY: DEFAULT_TEMPLATE,
    "axisymmetric-2d": AXISYMMETRIC_TEMPLATE,
}
"""内置模板族：键为 ``dimension``，值为模板文本（``3d-ale`` 文本保持不变）。"""


class DeckError(ValueError):
    """deck 生成失败：模板占位符缺失、参数未冻结（``--strict``）或写入失败。"""


def default_template() -> str:
    """内置占位模板文本。"""
    return DEFAULT_TEMPLATE


def template_for(dimension: str) -> str:
    """按维度取内置模板族文本；未支持的维度显式报错（不静默回落 3D）。"""
    try:
        return TEMPLATE_FAMILIES[dimension]
    except KeyError as exc:
        supported = " / ".join(sorted(TEMPLATE_FAMILIES))
        raise DeckError(f"未支持的模板维度 {dimension!r}（可选：{supported}）") from exc


def template_digest(text: str) -> str:
    """模板摘要：按规范化换行计算 SHA-256。"""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def load_template(path: Path) -> str:
    """读取自定义模板；文件缺失或编码错误抛 :class:`DeckError`。"""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DeckError(f"无法读取模板 {path}: {exc}") from exc


def template_placeholders(text: str) -> list[tuple[str, bool]]:
    """列出模板占位符：``(键, 是否可选)``，保持出现顺序并去重。"""
    seen: dict[tuple[str, bool], None] = {}
    for match in _PLACEHOLDER.finditer(text):
        seen.setdefault((match.group(1), match.group(2) == "?"), None)
    return list(seen)


def _render_value(value: Any) -> str:
    """把参数值渲染成 deck 文本；TBD 原样保留。"""
    if is_tbd(value):
        return TBD
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        return repr(value)
    return str(value)


def render_template(
    text: str,
    case: Case,
    *,
    schema: str = SCHEMA,
    extra: Mapping[str, Any] | None = None,
) -> tuple[str, tuple[str, ...]]:
    """渲染模板，返回 ``(deck 文本, TBD 字段列表)``。

    ``extra`` 提供模板族所需的外部取值（如网格包派生的 ``mesh_include`` /
    ``parts_block`` / ``detonation_*``）；同名时覆盖工况值，内建键仍优先。

    Raises
    ------
    DeckError:
        必填占位符无法解析时，消息列出字段与模板行号。
    """
    values: dict[str, Any] = {
        "schema": schema,
        "case_code": case.code,
        "case_group": case.group,
        "case_hash": case.fingerprint,
        **case.value_mapping(),
    }
    if extra:
        values.update(extra)
    tbd_fields: dict[str, None] = {}
    missing: list[str] = []

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        optional = match.group(2) == "?"
        if name in values:
            value = values[name]
            if is_tbd(value):
                tbd_fields.setdefault(name, None)
            return _render_value(value)
        if optional:
            tbd_fields.setdefault(name, None)
            return TBD
        line = text.count("\n", 0, match.start()) + 1
        missing.append(f"第 {line} 行 ${{{name}}}")
        return match.group(0)

    rendered = _PLACEHOLDER.sub(replace, text)
    if missing:
        raise DeckError("模板存在无法解析的必填占位符: " + "；".join(missing))
    normalized = rendered.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.endswith("\n"):
        normalized += "\n"
    return normalized, tuple(sorted(tbd_fields))


@dataclass(frozen=True)
class DeckPlan:
    """单个工况的生成计划：目录、输入文件名、deck 文本与 TBD 字段。"""

    case: Case
    directory: Path
    input_name: str
    text: str
    tbd_fields: tuple[str, ...]
    model: ModelSpec | None = None
    template_family: str = DEFAULT_TEMPLATE_FAMILY
    mesh: MeshPackage | None = None


def plan_deck(  # noqa: PLR0913 - 供 generate_decks 与测试直接组合，参数保持显式
    case: Case,
    template: str,
    out_root: Path,
    model: ModelSpec | None = None,
    *,
    extra: Mapping[str, Any] | None = None,
    template_family: str = DEFAULT_TEMPLATE_FAMILY,
    mesh: MeshPackage | None = None,
) -> DeckPlan:
    """计算单个工况的目录与渲染结果（不落盘）。"""
    directory = out_root / case.slug()
    values = dict(extra or {})
    if mesh is not None:
        values["mesh_include"] = Path(os.path.relpath(mesh.mesh_file, directory)).as_posix()
    text, tbd_fields = render_template(template, case, extra=values)
    return DeckPlan(
        case=case,
        directory=directory,
        input_name="case.k",
        text=text,
        tbd_fields=tbd_fields,
        model=model,
        template_family=template_family,
        mesh=mesh,
    )


@dataclass(frozen=True)
class ManifestCase:
    """生成清单中的单个工况记录。"""

    code: str
    group: str
    slug: str
    fingerprint: str
    levels: Mapping[str, str]
    tbd: tuple[str, ...]
    model_id: str = ""
    model_digest: str = ""
    model_source: str = ""
    template_family: str = ""
    mesh_id: str = ""
    mesh_digest: str = ""
    geometry_key: str = ""
    mesh_level: str = ""

    def to_mapping(self) -> dict[str, Any]:
        """序列化为清单条目。"""
        return {
            "code": self.code,
            "group": self.group,
            "slug": self.slug,
            "fingerprint": self.fingerprint,
            "levels": dict(self.levels),
            "tbd": list(self.tbd),
            "model_id": self.model_id,
            "model_digest": self.model_digest,
            "model_source": self.model_source,
            "template_family": self.template_family,
            "mesh_id": self.mesh_id,
            "mesh_digest": self.mesh_digest,
            "geometry_key": self.geometry_key,
            "mesh_level": self.mesh_level,
        }


@dataclass(frozen=True)
class Manifest:
    """输出根目录的生成清单。"""

    template_name: str
    template_digest: str
    settings: Mapping[str, Any]
    run: Mapping[str, Any]
    cases: tuple[ManifestCase, ...]
    tbd_cases: int
    tbd_fields: int
    models: tuple[Mapping[str, Any], ...] = ()

    def case(self, code: str) -> ManifestCase:
        """按工况编码取清单条目。"""
        for item in self.cases:
            if item.code == code:
                return item
        raise DeckError(f"生成清单中没有工况 {code}")

    @property
    def total(self) -> int:
        """工况总数。"""
        return len(self.cases)

    @property
    def model_bound(self) -> int:
        """绑定到模型的工况数。"""
        return sum(1 for item in self.cases if item.model_id)

    @property
    def model_fallback(self) -> int:
        """回落矩阵的工况数。"""
        return self.total - self.model_bound


@dataclass(frozen=True)
class GenerationResult:
    """生成结果：计划、输出根目录、清单与 TBD 统计。"""

    root: Path
    manifest: Manifest
    plans: tuple[DeckPlan, ...]

    @property
    def tbd_fields(self) -> int:
        """全部 deck 中 TBD 字段出现次数。"""
        return sum(len(plan.tbd_fields) for plan in self.plans)

    def tbd_cases(self) -> tuple[str, ...]:
        """含 TBD 字段的工况编码。"""
        return tuple(plan.case.code for plan in self.plans if plan.tbd_fields)


def _atomic_write(path: Path, text: str) -> None:
    """同目录临时文件 + ``Path.replace`` 原子写入（UTF-8、LF）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    os.close(descriptor)
    temporary = Path(name)
    try:
        temporary.write_text(text, encoding="utf-8", newline="\n")
        temporary.replace(path)
    except BaseException:
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise


def _pretty_json(payload: Any) -> str:
    """稳定、可读、带末尾换行的 JSON。"""
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def generate_decks(  # noqa: PLR0912, PLR0913 - 生成开关由 CLI/GUI 直接透传，显式优于配置对象
    table: ParamTable,
    out_root: Path,
    *,
    template: str | None = None,
    template_name: str | None = None,
    only: Sequence[str] | None = None,
    strict: bool = False,
    models: ModelSet | None = None,
    meshes: MeshSet | None = None,
    dimension: str | None = None,
    mesh_level: str | None = None,
) -> GenerationResult:
    """生成全部（或指定）工况的 deck 与清单。

    ``models`` 按 ``case_scope`` 绑定到工况，模型非 TBD 派生值覆盖矩阵同名因子；
    没有匹配模型的工况回落矩阵。``strict`` 为真时，任何 TBD 字段都会让生成整体
    失败，且不写任何文件。

    给定 ``meshes`` 时按工况模型指纹选择网格包（``mesh_level`` 消歧），渲染
    ``axisymmetric-2d`` 模板族；此时每个工况必须绑定模型。
    """
    if template is not None:
        text = template
        family = dimension or "custom"
    else:
        if meshes is not None:
            family = dimension or AXISYMMETRIC_DIMENSION
            if family != AXISYMMETRIC_DIMENSION:
                raise DeckError(f"网格包只支持 {AXISYMMETRIC_DIMENSION}，不能用于 {family}")
        else:
            family = dimension or DEFAULT_TEMPLATE_FAMILY
        text = template_for(family)
    digest = template_digest(text)
    cases = build_cases(table, digest)
    if only:
        wanted = {code.strip() for code in only if code.strip()}
        unknown = sorted(wanted - {case.code for case in cases})
        if unknown:
            raise DeckError(f"未知工况编码: {', '.join(unknown)}")
        cases = [case for case in cases if case.code in wanted]
    if models is not None:
        models.validate_against([case.code for case in cases])
    resolved: list[tuple[Case, ModelSpec | None, MeshPackage | None]] = []
    for case in cases:
        spec = models.resolve(case.code) if models is not None else None
        merged = apply_model(case, spec, digest) if spec is not None else case
        mesh: MeshPackage | None = None
        if meshes is not None:
            if spec is None:
                raise DeckError(f"工况 {case.code} 没有模型描述，无法选择网格包")
            mesh = meshes.for_model(spec.digest, level=mesh_level)
            merged = replace(merged, fingerprint=case_fingerprint(merged, digest, spec.digest, mesh.digest))
        resolved.append((merged, spec, mesh))
    plans = [
        plan_deck(
            case,
            text,
            out_root,
            spec,
            extra=mesh.deck_values() if mesh is not None else None,
            template_family=family,
            mesh=mesh,
        )
        for case, spec, mesh in resolved
    ]
    if strict:
        offenders = [plan.case.code for plan in plans if plan.tbd_fields]
        if offenders:
            raise DeckError(f"--strict 拒绝生成含 TBD 的工况（共 {len(offenders)} 个）: {', '.join(offenders)}")

    manifest_cases = tuple(
        ManifestCase(
            code=plan.case.code,
            group=plan.case.group,
            slug=plan.case.slug(),
            fingerprint=plan.case.fingerprint,
            levels=plan.case.level_mapping(),
            tbd=plan.tbd_fields,
            model_id=plan.model.id if plan.model else "",
            model_digest=plan.model.digest if plan.model else "",
            model_source=plan.model.source if plan.model else "",
            template_family=plan.template_family,
            mesh_id=plan.mesh.mesh_id if plan.mesh else "",
            mesh_digest=plan.mesh.digest if plan.mesh else "",
            geometry_key=plan.mesh.geometry_key if plan.mesh else "",
            mesh_level=plan.mesh.level if plan.mesh else "",
        )
        for plan in plans
    )
    manifest = Manifest(
        template_name=template_name or family,
        template_digest=digest,
        settings=dict(table.defaults),
        run=dict(table.run),
        cases=manifest_cases,
        tbd_cases=sum(1 for plan in plans if plan.tbd_fields),
        tbd_fields=sum(len(plan.tbd_fields) for plan in plans),
        models=_model_summary(manifest_cases, models),
    )
    for plan in plans:
        _write_deck(plan)
    write_manifest(out_root, manifest)
    return GenerationResult(root=out_root, manifest=manifest, plans=tuple(plans))


def _model_summary(cases: Sequence[ManifestCase], models: ModelSet | None) -> tuple[Mapping[str, Any], ...]:
    """生成清单里的模型绑定摘要（未使用的模型也列出，bound_cases 为空）。"""
    if models is None or not models:
        return ()
    bound: dict[str, list[str]] = {}
    for item in cases:
        if item.model_id:
            bound.setdefault(item.model_id, []).append(item.code)
    return tuple(
        {
            "id": spec.id,
            "digest": spec.digest,
            "source": spec.source,
            "bound_cases": bound.get(spec.id, []),
        }
        for spec in models.models
    )


def _write_deck(plan: DeckPlan) -> None:
    """写入单个 deck 目录：``case.k`` 与 ``case.json``（不动运行期文件）。"""
    plan.directory.mkdir(parents=True, exist_ok=True)
    _atomic_write(plan.directory / plan.input_name, plan.text)
    payload = {
        "schema": SCHEMA,
        "generator_version": GENERATOR_VERSION,
        "code": plan.case.code,
        "group": plan.case.group,
        "fingerprint": plan.case.fingerprint,
        "levels": plan.case.level_mapping(),
        "values": plan.case.value_mapping(),
        "tbd": list(plan.tbd_fields),
        "template_family": plan.template_family,
        "mesh_id": plan.mesh.mesh_id if plan.mesh else "",
        "mesh_digest": plan.mesh.digest if plan.mesh else "",
        "geometry_key": plan.mesh.geometry_key if plan.mesh else "",
        "level": plan.mesh.level if plan.mesh else "",
        "model": {
            "id": plan.model.id,
            "digest": plan.model.digest,
            "source": plan.model.source,
        }
        if plan.model
        else None,
    }
    _atomic_write(plan.directory / "case.json", _pretty_json(payload))


def write_manifest(root: Path, manifest: Manifest) -> None:
    """写根目录 ``manifest.json`` 与 ``cases.json``。"""
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": SCHEMA,
        "generator_version": GENERATOR_VERSION,
        "template": {"name": manifest.template_name, "digest": manifest.template_digest},
        "settings": dict(manifest.settings),
        "run": dict(manifest.run),
        "counts": {
            "total": manifest.total,
            "main": sum(1 for item in manifest.cases if item.group == "main"),
            "supplementary": sum(1 for item in manifest.cases if item.group.startswith("supplementary")),
            "typical": sum(1 for item in manifest.cases if item.group == "typical"),
            "tbd_cases": manifest.tbd_cases,
            "tbd_fields": manifest.tbd_fields,
            "model_bound": manifest.model_bound,
            "model_fallback": manifest.model_fallback,
        },
        "models": [dict(item) for item in manifest.models],
        "cases": [item.to_mapping() for item in manifest.cases],
    }
    _atomic_write(root / "manifest.json", _pretty_json(payload))
    _atomic_write(
        root / "cases.json",
        _pretty_json({"schema": SCHEMA, "cases": [item.to_mapping() for item in manifest.cases]}),
    )


def _as_mapping(value: Any) -> Mapping[str, Any]:
    """清单中的映射段：缺失或类型不符时返回空映射。"""
    return value if isinstance(value, Mapping) else {}


def load_manifest(root: Path) -> Manifest:
    """读取根目录 ``manifest.json``；缺失或损坏抛 :class:`DeckError`。"""
    path = root / "manifest.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DeckError(f"输出目录缺少生成清单 {path}，请先执行 generate") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise DeckError(f"生成清单 {path} 无法读取: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise DeckError(f"生成清单 {path} 顶层不是对象")
    if raw.get("schema") != SCHEMA:
        raise DeckError(f"生成清单 schema 应为 {SCHEMA}，实际为 {raw.get('schema')!r}")
    cases: list[ManifestCase] = []
    raw_cases = raw.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise DeckError(f"生成清单 {path} 没有工况记录")
    for index, item in enumerate(raw_cases, 1):
        if not isinstance(item, Mapping):
            raise DeckError(f"生成清单 cases[{index}] 不是对象")
        try:
            cases.append(
                ManifestCase(
                    code=str(item["code"]),
                    group=str(item["group"]),
                    slug=str(item["slug"]),
                    fingerprint=str(item["fingerprint"]),
                    levels={str(k): str(v) for k, v in dict(item.get("levels") or {}).items()},
                    tbd=tuple(str(name) for name in item.get("tbd") or ()),
                    model_id=str(item.get("model_id") or ""),
                    model_digest=str(item.get("model_digest") or ""),
                    model_source=str(item.get("model_source") or ""),
                    template_family=str(item.get("template_family") or ""),
                    mesh_id=str(item.get("mesh_id") or ""),
                    mesh_digest=str(item.get("mesh_digest") or ""),
                    geometry_key=str(item.get("geometry_key") or ""),
                    mesh_level=str(item.get("mesh_level") or ""),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise DeckError(f"生成清单 cases[{index}] 字段非法: {exc}") from exc
    counts = _as_mapping(raw.get("counts"))
    template = _as_mapping(raw.get("template"))
    settings = _as_mapping(raw.get("settings"))
    run = _as_mapping(raw.get("run"))
    raw_models = raw.get("models")
    model_items = raw_models if isinstance(raw_models, list) else []
    models = tuple(dict(item) for item in model_items if isinstance(item, Mapping))
    return Manifest(
        template_name=str(template.get("name") or ""),
        template_digest=str(template.get("digest") or ""),
        settings=dict(settings),
        run=dict(run),
        cases=tuple(cases),
        tbd_cases=int(counts.get("tbd_cases") or 0),
        tbd_fields=int(counts.get("tbd_fields") or 0),
        models=models,
    )
