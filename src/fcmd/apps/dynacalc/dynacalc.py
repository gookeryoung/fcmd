"""dynacalc - 装药密度 / 尺寸 / 工艺缺陷 → 爆轰传递的参数化算例（LS-DYNA deck 批处理）。

本工具不求解物理、不生成网格、不内置任何真实配方或材料参数：参数表里的数值
全部由使用者以受控文件填入，未冻结一律写 ``TBD`` 并原样进入 deck。

子命令
------
- ``example``：输出全 TBD 的示例参数表（可直接保存后填写）。
- ``template``：输出内置 LS-DYNA 关键字模板。
- ``params``：校验参数文件并打印因子 / 水平 / 置信等级 / TBD 摘要。
- ``matrix``：展开主矩阵与补充组，列出工况编码（可导出 JSON）。
- ``model``：校验 ``dynacalc-model/1`` 模型描述并打印指纹。
- ``generate``：按参数矩阵 + 模型描述生成每个工况的 deck 目录与清单。
- ``run``：调用 LS-DYNA（或假求解器）执行，支持 dry-run / 并发 / 超时 / 重试 / 续跑。
- ``report``：回收 glstat / matsum / history，输出 CSV / JSON / 文本汇总。
- ``compare``：对比两个工况的关键指标。
- ``gates``：按工程建议值做四项门控检查（不通过时退出码 1）。
- ``demo``：一键演示（示例参数 → 生成典型工况 → 假求解 → 汇总 → 对比 → 门控）。

示例
----
    fcmd dynacalc demo --out demo
    fcmd dynacalc example --out params.yaml
    fcmd dynacalc params params.yaml
    fcmd dynacalc matrix params.yaml --only M001 M002
    fcmd dynacalc generate params.yaml out --model model.yaml
    fcmd dynacalc run out --plan-only
    fcmd dynacalc run out --fake --concurrency 4
    fcmd dynacalc report out --report-out report.csv --output-format csv
    fcmd dynacalc compare out M001 M002
    fcmd dynacalc gates out
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import fcmd
from fcmd.apps.dynacalc._gates import FAIL, evaluate, format_gate_report
from fcmd.apps.dynacalc._mesh import MeshError, scan_mesh_root
from fcmd.apps.dynacalc._model import ModelError, load_models
from fcmd.apps.dynacalc._params import (
    SCHEMA,
    ParamError,
    build_cases,
    example_params,
    expand_main_cases,
    expand_supplementary_cases,
    expand_typical_cases,
    load_params,
    summary_rows,
)
from fcmd.apps.dynacalc._results import (
    CompareRow,
    ResultParseError,
    collect_results,
    compare_cases,
    format_text_report,
    write_report,
)
from fcmd.apps.dynacalc._solver import (
    SolverError,
    config_for_manifest,
    format_run_report,
    run_jobs,
)
from fcmd.apps.dynacalc._template import (
    DeckError,
    generate_decks,
    load_manifest,
    load_template,
    template_for,
)
from fcmd.console import print_error

__all__ = [
    "compare_command",
    "demo_command",
    "example_command",
    "gates_command",
    "generate_command",
    "main",
    "matrix_command",
    "model_command",
    "params_command",
    "report_command",
    "run_command",
    "template_command",
]

__version__ = "0.1.0"

_DESCRIPTION = (
    "装药密度—尺寸—工艺缺陷→爆轰传递的参数化算例：YAML 参数矩阵 + dynacalc-model/1 模型映射，"
    "批量生成 LS-DYNA 关键字 deck，调度求解（支持 dry-run 与假求解器），回收 glstat/matsum/history "
    "并做汇总、两工况对比与工程门控；不含任何真实配方或材料参数"
)

_ERRORS = (ParamError, DeckError, MeshError, ModelError, SolverError, ResultParseError, OSError)


def _fail(message: str, code: int = 2) -> None:
    """打印中文错误并以指定退出码结束。"""
    print_error(message)
    raise SystemExit(code)


def _write_or_print(text: str, out: Path | None) -> None:
    """有 ``--out`` 时写文件，否则打印到标准输出。"""
    if out is None:
        print(text, end="" if text.endswith("\n") else "\n")
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8", newline="\n")
    print(f"已写入 {out}")


def _dump(payload: Any) -> str:
    """稳定 JSON 文本。"""
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


@fcmd.tool("dynacalc", subcommand="example", help="输出全 TBD 的示例参数表", description=_DESCRIPTION)
def example_command(out: Path | None = None) -> None:
    """输出示例参数表；所有连续量水平均为 TBD。

    Parameters
    ----------
    out:
        写入路径；省略时打印到标准输出
    """
    _write_or_print(example_params(), out)


@fcmd.tool("dynacalc", subcommand="template", help="输出内置 LS-DYNA deck 模板", description=_DESCRIPTION)
def template_command(
    out: Path | None = None,
    dimension: Literal["3d-ale", "axisymmetric-2d"] = "3d-ale",
) -> None:
    """输出内置关键字模板（含材料 / EOS / 起爆 / 输出占位）。

    Parameters
    ----------
    out:
        写入路径；省略时打印到标准输出
    dimension:
        模板族：``3d-ale``（默认，自包含 ALE）或 ``axisymmetric-2d``（含网格包占位符）
    """
    _write_or_print(template_for(dimension), out)


@fcmd.tool("dynacalc", subcommand="params", help="校验并摘要参数文件", description=_DESCRIPTION)
def params_command(params_file: Path, json_output: bool = False) -> None:
    """校验参数文件，打印因子、置信等级与 TBD 统计。

    Parameters
    ----------
    params_file:
        ``dynacalc/1`` YAML 参数文件
    json_output:
        以 JSON 输出摘要
    """
    try:
        table = load_params(params_file)
    except _ERRORS as exc:
        _fail(str(exc))
        return
    main_count = len(expand_main_cases(table))
    supplementary_count = len(expand_supplementary_cases(table))
    typical_count = len(expand_typical_cases(table))
    payload = {
        "schema": SCHEMA,
        "source": str(params_file),
        "factors": [
            {
                "key": factor.key,
                "label": factor.label,
                "unit": factor.unit,
                "kind": factor.kind,
                "confidence": factor.confidence,
                "levels": len(factor.levels),
                "tbd_levels": factor.tbd_levels,
            }
            for factor in table.factors
        ],
        "matrix": {
            "main": list(table.main_keys),
            "supplementary": [
                {"factors": list(group.factors), "question": group.question} for group in table.supplementary
            ],
            "typical": [
                {"code": item.code, "label": item.label, "question": item.question, "levels": dict(item.levels)}
                for item in table.typical
            ],
            "main_cases": main_count,
            "supplementary_cases": supplementary_count,
            "typical_cases": typical_count,
        },
        "confidence": table.confidence_counts(),
        "tbd": table.tbd_counts(),
        "warnings": list(table.warnings),
    }
    if json_output:
        print(_dump(payload), end="")
        return
    print(f"参数文件：{params_file}（schema {SCHEMA}）")
    print(
        f"因子 {len(table.factors)} 个；主矩阵 {' × '.join(table.main_keys)} = {main_count} 组；"
        f"补充组 {len(table.supplementary)} 个 = {supplementary_count} 组；典型工况 {typical_count} 组"
    )
    print("key\tlabel\tunit\tkind\tconfidence\tlevels\ttbd")
    for row in summary_rows(table):
        print("\t".join(row))
    for group in table.supplementary:
        print(f"补充组 {'+'.join(group.factors)}：{group.question or '（未标注问题）'}")
    for item in table.typical:
        print(f"典型工况 {item.code} {item.label}：{item.question or '（未标注问题）'}")
    print("置信等级：" + "，".join(f"{name}={count}" for name, count in table.confidence_counts().items()))
    tbd = table.tbd_counts()
    print("未冻结水平：" + ("无" if not tbd else "，".join(f"{key}={count}" for key, count in tbd.items())))
    for warning in table.warnings:
        print(f"警告：{warning}")


@fcmd.tool("dynacalc", subcommand="matrix", help="展开并列出工况矩阵", description=_DESCRIPTION)
def matrix_command(params_file: Path, only: list[str] | None = None, out: Path | None = None) -> None:
    """展开主矩阵与补充组，列出工况编码、分组与水平。

    Parameters
    ----------
    params_file:
        ``dynacalc/1`` YAML 参数文件
    only:
        只列出指定工况编码
    out:
        导出 JSON 的路径；省略时打印文本
    """
    try:
        table = load_params(params_file)
        cases = build_cases(table)
    except _ERRORS as exc:
        _fail(str(exc))
        return
    if only:
        wanted = {code.strip() for code in only if code.strip()}
        unknown = sorted(wanted - {case.code for case in cases})
        if unknown:
            _fail(f"未知工况编码: {', '.join(unknown)}")
        cases = [case for case in cases if case.code in wanted]
    if out is not None:
        payload = {
            "schema": SCHEMA,
            "cases": [
                {
                    "code": case.code,
                    "group": case.group,
                    "levels": case.level_mapping(),
                    "values": case.value_mapping(),
                    "tbd": case.tbd,
                }
                for case in cases
            ],
        }
        _write_or_print(_dump(payload), out)
        return
    print(f"共 {len(cases)} 个工况")
    for case in cases:
        levels = " ".join(f"{key}={code}" for key, code in case.level_codes)
        suffix = f"（TBD: {', '.join(case.tbd)}）" if case.tbd else ""
        print(f"{case.code}\t{case.group}\t{levels}{suffix}")


@fcmd.tool("dynacalc", subcommand="model", help="校验 dynacalc-model/1 模型描述", description=_DESCRIPTION)
def model_command(model_files: list[Path], json_output: bool = False) -> None:
    """校验一个或多个模型文件，打印模型指纹、绑定范围与 TBD 清单。

    Parameters
    ----------
    model_files:
        ``dynacalc-model/1`` YAML 文件列表
    json_output:
        以 JSON 输出
    """
    try:
        model_set = load_models(model_files)
    except _ERRORS as exc:
        _fail(str(exc))
        return
    summary = model_set.summary()
    if json_output:
        print(_dump({"schema": "dynacalc-model/1", "models": summary, "warnings": list(model_set.warnings)}), end="")
        return
    print(f"模型 {len(summary)} 个（文件 {len(model_files)} 个）")
    for item in summary:
        include = ",".join(item["include"]) or "全部"
        exclude = ",".join(item["exclude"]) or "无"
        print(f"[{item['id']}] {item['digest']}")
        print(f"  来源={item['source'] or '-'}；绑定 include={include} exclude={exclude}")
        confidence = "，".join(f"{name}={value}" for name, value in item["confidence"].items())
        print(f"  置信等级：{confidence}")
        print(f"  TBD：{', '.join(item['tbd']) if item['tbd'] else '无'}")
    for warning in model_set.warnings:
        print(f"警告：{warning}")


@fcmd.tool("dynacalc", subcommand="generate", help="生成工况 deck 与清单", description=_DESCRIPTION)
def generate_command(  # noqa: PLR0913, PLR0917 - 参数与 CLI 一一对应，保持显式
    params_file: Path,
    out: Path,
    template: Path | None = None,
    model: list[Path] | None = None,
    only: list[str] | None = None,
    strict: bool = False,
    mesh_root: Path | None = None,
    dimension: Literal["3d-ale", "axisymmetric-2d"] | None = None,
    mesh_level: str | None = None,
) -> None:
    """按矩阵（及模型描述）生成每个工况的 deck 目录与根清单。

    Parameters
    ----------
    params_file:
        ``dynacalc/1`` YAML 参数文件
    out:
        输出根目录
    template:
        自定义模板文件；省略时按维度用内置模板族
    model:
        ``dynacalc-model/1`` 模型文件（可多个）
    only:
        只生成指定工况编码
    strict:
        存在 TBD 字段时拒绝生成
    mesh_root:
        网格根目录（``<case>/meshes``）；给出时按模型指纹选择网格包并渲染
        ``axisymmetric-2d`` 模板族
    dimension:
        模板族：``3d-ale``（默认，无网格）或 ``axisymmetric-2d``（需 ``--mesh-root``）
    mesh_level:
        档位（coarse / medium / fine）；同一模型有多档时必须显式指定
    """
    try:
        table = load_params(params_file)
        models = load_models(model) if model else None
        template_text = load_template(template) if template is not None else None
        meshes = scan_mesh_root(mesh_root) if mesh_root is not None else None
        result = generate_decks(
            table,
            out,
            template=template_text,
            template_name=template.name if template is not None else None,
            only=only,
            strict=strict,
            models=models,
            meshes=meshes,
            dimension=dimension,
            mesh_level=mesh_level,
        )
        manifest = result.manifest
    except _ERRORS as exc:
        _fail(str(exc))
        return
    print(f"已生成 {manifest.total} 个工况到 {out}")
    print(f"清单：{out / 'manifest.json'}；模板 {manifest.template_name}，摘要 {manifest.template_digest[:12]}")
    main_count = sum(1 for item in manifest.cases if item.group == "main")
    supplementary_count = sum(1 for item in manifest.cases if item.group.startswith("supplementary"))
    typical_count = sum(1 for item in manifest.cases if item.group == "typical")
    print(
        f"主矩阵 {main_count} 组，补充组 {supplementary_count} 组，典型工况 {typical_count} 组；"
        f"TBD 工况 {manifest.tbd_cases} 个 / 字段 {manifest.tbd_fields} 处"
    )
    if meshes is not None:
        print(f"网格包 {len(meshes.packages)} 个（{mesh_root}）")
    if manifest.models:
        print(f"模型绑定：绑定 {manifest.model_bound} 个工况，回落矩阵 {manifest.model_fallback} 个工况")
    for item in manifest.cases:
        tbd = f" TBD={len(item.tbd)}" if item.tbd else ""
        model_note = f" model={item.model_id}@{item.model_digest[:8]}" if item.model_id else ""
        mesh_note = f" mesh={item.mesh_id}@{item.mesh_level}" if item.mesh_id else ""
        print(f"[{item.code}] {item.slug}{tbd}{model_note}{mesh_note}")


@fcmd.tool("dynacalc", subcommand="run", help="执行求解（支持 dry-run / 假求解器）", description=_DESCRIPTION)
def run_command(  # noqa: PLR0913, PLR0917 - 参数与 CLI 一一对应，保持显式
    out: Path,
    plan_only: bool = False,
    force: bool = False,
    only: list[str] | None = None,
    solver: str | None = None,
    ncpu: int | None = None,
    memory: str | None = None,
    parallel: Literal["smp", "mpp"] | None = None,
    mpi: Literal["intel", "msmpi", "platform"] | None = None,
    concurrency: int | None = None,
    timeout_s: int | None = None,
    retries: int | None = None,
    fake: bool = False,
) -> None:
    """按生成清单调度求解；默认跳过指纹一致的已完成工况。

    Parameters
    ----------
    out:
        ``generate`` 的输出根目录
    plan_only:
        只打印命令与计划（dry-run），不启动进程、不写作业清单；
        全局 ``--dry-run`` 由 fcmd 任务框架占用，故此处用 ``--plan-only``
    force:
        忽略已完成状态，全部重跑
    only:
        只运行指定工况编码
    solver:
        求解器可执行文件路径；省略时用清单配置或自动探测
    ncpu:
        求解器核数
    memory:
        内存参数，如 200m / 1g / auto
    parallel:
        并行方式：smp（默认）或 mpp
    mpi:
        MPP 的 MPI 实现
    concurrency:
        同时运行的工况数
    timeout_s:
        单工况超时秒数
    retries:
        失败重试次数
    fake:
        使用假求解器（不调用 LS-DYNA，用于本机测试）
    """
    overrides = {
        "solver_path": solver,
        "ncpu": ncpu,
        "memory": memory,
        "parallel": parallel,
        "mpi": mpi,
        "concurrency": concurrency,
        "timeout_s": timeout_s,
        "retries": retries,
        "fake": True if fake else None,
    }
    try:
        manifest = load_manifest(out)
        config = config_for_manifest(manifest, overrides)
        report = run_jobs(manifest, config, root=out, force=force, only=only, dry_run=plan_only)
    except _ERRORS as exc:
        _fail(str(exc))
        return
    print(format_run_report(report, config), end="")
    if not plan_only and report.failed():
        raise SystemExit(1)


@fcmd.tool("dynacalc", subcommand="report", help="回收结果并输出汇总", description=_DESCRIPTION)
def report_command(
    out: Path,
    report_out: Path | None = None,
    output_format: Literal["text", "csv", "json"] = "text",
) -> None:
    """解析 glstat / matsum / history 并输出汇总；有解析失败时退出码 2。

    Parameters
    ----------
    out:
        ``generate`` 的输出根目录
    report_out:
        汇总文件路径；省略时只打印文本汇总
    output_format:
        汇总格式：text / csv / json
    """
    try:
        manifest = load_manifest(out)
        result_set = collect_results(out, manifest)
        if report_out is not None:
            write_report(result_set, report_out, output_format)
            print(f"已写入 {report_out}（格式 {output_format}）")
    except _ERRORS as exc:
        _fail(str(exc))
        return
    print(format_text_report(result_set), end="")
    if result_set.failed():
        raise SystemExit(2)


@fcmd.tool("dynacalc", subcommand="compare", help="对比两个工况的关键指标", description=_DESCRIPTION)
def compare_command(out: Path, left: str, right: str) -> None:
    """打印两个工况的指标差异（含相对差）。

    Parameters
    ----------
    out:
        ``generate`` 的输出根目录
    left:
        左侧工况编码
    right:
        右侧工况编码
    """
    try:
        manifest = load_manifest(out)
        result_set = collect_results(out, manifest)
        rows = compare_cases(result_set, left, right)
    except _ERRORS as exc:
        _fail(str(exc))
        return
    _print_compare(rows, left, right)


def _print_compare(rows: list[CompareRow], left: str, right: str) -> None:
    """打印对比表（供 ``compare`` 与 ``demo`` 共用）。"""
    print(f"对比：{left} vs {right}")
    print("指标\t左\t右\t差值\t相对差")
    for row in rows:

        def fmt(value: float | None) -> str:
            return "-" if value is None else f"{value:.6g}"

        relative = "-" if row.relative is None else f"{row.relative:+.2%}"
        print(f"{row.label}\t{fmt(row.left)}\t{fmt(row.right)}\t{fmt(row.delta)}\t{relative}")


@fcmd.tool("dynacalc", subcommand="gates", help="执行工程门控检查", description=_DESCRIPTION)
def gates_command(out: Path) -> None:
    """按清单中的阈值执行四项门控；存在不通过项时退出码 1。

    Parameters
    ----------
    out:
        ``generate`` 的输出根目录
    """
    try:
        manifest = load_manifest(out)
        result_set = collect_results(out, manifest)
        gate_report = evaluate(result_set, manifest.settings)
    except _ERRORS as exc:
        _fail(str(exc))
        return
    print(format_gate_report(gate_report), end="")
    if gate_report.count(FAIL):
        raise SystemExit(1)


@fcmd.tool(
    "dynacalc",
    subcommand="demo",
    help="一键演示：示例参数 → 生成 → 假求解 → 汇总 → 对比 → 门控",
    description=_DESCRIPTION,
)
def demo_command(
    out: Path | None = None,
    model: list[Path] | None = None,
    mesh_root: Path | None = None,
    mesh_level: str | None = None,
    full: bool = False,
) -> None:
    """用内置示例参数跑通完整链路；始终调用假求解器，不需要 LS-DYNA。

    Parameters
    ----------
    out:
        演示根目录；省略时用当前目录下的 ``dynacalc-demo``
    model:
        ``dynacalc-model/1`` 模型文件（通常是 paramcad 导出的示例模型）
    mesh_root:
        网格根目录（paramesh 输出）；给出时改用 ``axisymmetric-2d`` 模板族
    mesh_level:
        网格档位（coarse / medium / fine）
    full:
        生成并运行全部工况；默认只跑典型工况 01/02/03
    """
    root = (out if out is not None else Path("dynacalc-demo")).resolve()
    deck_root = root / "decks"
    params_file = root / "params.yaml"
    try:
        root.mkdir(parents=True, exist_ok=True)
        params_file.write_text(example_params(), encoding="utf-8", newline="\n")
        table = load_params(params_file)
        typical = [case.code for case in build_cases(table) if case.group == "typical"]
        only = None if full else typical
        models = load_models(model) if model else None
        meshes = scan_mesh_root(mesh_root) if mesh_root is not None else None
        if meshes is not None and models is None:
            raise MeshError("使用 --mesh-root 时必须同时给出 --model（网格按模型指纹绑定）")
        print(f"步骤 1/6 示例参数：{params_file}（示例占位，TBD 原样写入 deck）")
        result = generate_decks(table, deck_root, only=only, models=models, meshes=meshes, mesh_level=mesh_level)
        manifest = result.manifest
        print(f"步骤 2/6 生成：{manifest.total} 个工况 → {deck_root}（模板 {manifest.template_name}）")
        config = config_for_manifest(manifest, {"fake": True, "concurrency": 4})
        run_report = run_jobs(manifest, config, root=deck_root)
        print("步骤 3/6 假求解：")
        print(format_run_report(run_report, config), end="")
        result_set = collect_results(deck_root, manifest)
        report_file = root / "report.txt"
        write_report(result_set, report_file, "text")
        print(f"步骤 4/6 汇总：{report_file}")
        print(format_text_report(result_set), end="")
        codes = {item.code for item in result_set.results}
        if {"T01", "T02"} <= codes:
            print("步骤 5/6 对比：")
            _print_compare(compare_cases(result_set, "T01", "T02"), "T01", "T02")
        gate_report = evaluate(result_set, manifest.settings)
        print("步骤 6/6 门控：")
        print(format_gate_report(gate_report), end="")
        if gate_report.count(FAIL):
            print("注：假求解器指标不是物理结果，门控不通过属预期；真实评估请换实测求解输出。")
        if run_report.failed() or result_set.failed():
            raise SystemExit(2)
    except (*_ERRORS, ValueError) as exc:
        _fail(str(exc))
        return
    print(f"演示完成：{root}（假求解器只验证链路，不代表物理结论）")


@fcmd.main("dynacalc")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
