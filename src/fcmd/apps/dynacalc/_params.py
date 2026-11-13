"""dynacalc 参数模型：受控参数表、矩阵展开与工况指纹（纯逻辑，不依赖 Tk）。

参数文件为 YAML，带版本化 ``schema``。读取口径与 filescan 配置一致：

- 非 YAML / 非对象 / ``schema`` 不匹配 → 整份失败（:class:`ParamError`）；
- 缺字段 → 回落默认值并记录警告；
- 未知字段 → 忽略；
- 单字段非法 → 该字段失败（主矩阵引用未知因子等）或回落默认（``run`` /
  ``defaults`` 的标量选项），并记录警告。

这里不内置任何真实配方、材料参数或产品尺寸：连续量水平默认就是 ``TBD``，
矩阵展开只做笛卡尔积与 OFAT，不做物理判断。
"""

from __future__ import annotations

import hashlib
import itertools
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "CONFIDENCE_LEVELS",
    "CONFIDENCE_TEXT",
    "DEFAULT_SETTINGS",
    "FACTOR_KINDS",
    "RUN_DEFAULTS",
    "SCHEMA",
    "TBD",
    "Case",
    "Factor",
    "Level",
    "ParamError",
    "ParamTable",
    "build_cases",
    "canonical_json",
    "example_params",
    "expand_main_cases",
    "expand_supplementary_cases",
    "is_tbd",
    "load_params",
    "params_to_mapping",
    "parse_params",
    "save_params",
    "summary_rows",
]

SCHEMA = "dynacalc/1"
"""参数文件格式版本；与读取实现不一致时整份失败。"""

TBD = "TBD"
"""未冻结数值的统一写法：原样写入 deck，绝不替换成公开材料卡数值。"""

CONFIDENCE_LEVELS = ("public", "controlled", "uncalibrated")
CONFIDENCE_TEXT = {
    "public": "公开通用",
    "controlled": "受控试验",
    "uncalibrated": "未标定假设",
}

FACTOR_KINDS = ("continuous", "discrete")

DEFAULT_SETTINGS: dict[str, Any] = {
    "skip_fraction": 0.2,
    "tail_fraction": 0.1,
    "probes_mm": [50.0, 100.0, 150.0],
    "quench_fraction": 0.99,
    "gates": {
        "velocity_rel_diff": 0.02,
        "arrival_l2_rel": 0.03,
        "wall_impulse_rel": 0.05,
        "reference": "",
        "pairs": [],
    },
}
"""后处理与门控默认值。默认值只描述算法口径，不是材料参数。"""

RUN_DEFAULTS: dict[str, Any] = {
    "solver_path": "",
    "ncpu": 4,
    "memory": "auto",
    "parallel": "smp",
    "mpi": "intel",
    "extra_args": [],
    "env": {},
    "timeout_s": 3600,
    "retries": 0,
    "concurrency": 1,
    "fake": False,
}
"""求解调度默认值：与 ``lscalc`` 的命令口径保持一致。"""


class ParamError(ValueError):
    """参数文件不可用：消息为可直接展示给用户的中文原因。"""


def is_tbd(value: Any) -> bool:
    """判断水平值是否未冻结（``None`` 与 ``"TBD"`` 都视为 TBD）。"""
    return value is None or (isinstance(value, str) and value.strip().upper() == TBD)


def canonical_json(payload: Any) -> str:
    """稳定的 JSON 文本：排序键、紧凑分隔符、保留中文。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _text(value: Any) -> str:
    """把标量值转成 deck 里的稳定文本（数值不留多余精度）。"""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        return repr(value)
    return str(value)


@dataclass(frozen=True)
class Level:
    """一个因子的一个离散水平。"""

    code: str
    value: Any
    label: str = ""

    @property
    def tbd(self) -> bool:
        """该水平是否尚未冻结。"""
        return is_tbd(self.value)

    def display(self) -> str:
        """供清单/报告展示的文本：``L1(TBD)`` 或 ``ID1(12.5)``。"""
        return f"{self.code}(TBD)" if self.tbd else f"{self.code}({_text(self.value)})"


@dataclass(frozen=True)
class Factor:
    """一个受控因子：连续量或离散状态，带单位与置信等级。"""

    key: str
    label: str
    unit: str
    kind: str
    confidence: str
    levels: tuple[Level, ...]

    def level(self, code: str) -> Level:
        """按水平编码取水平；不存在时抛 :class:`ParamError`。"""
        for item in self.levels:
            if item.code == code:
                return item
        raise ParamError(f"因子 {self.key} 没有水平 {code}")

    @property
    def tbd_levels(self) -> int:
        """未冻结水平数量。"""
        return sum(1 for item in self.levels if item.tbd)

    @property
    def confidence_text(self) -> str:
        """置信等级中文名。"""
        return CONFIDENCE_TEXT.get(self.confidence, self.confidence)


@dataclass(frozen=True)
class Case:
    """一个待求解工况：编码、分组、因子水平编码与解析值。"""

    code: str
    group: str
    level_codes: tuple[tuple[str, str], ...]
    values: tuple[tuple[str, Any], ...]
    fingerprint: str = ""

    def level_of(self, key: str) -> str:
        """取因子水平编码；不存在时抛 :class:`ParamError`。"""
        for name, code in self.level_codes:
            if name == key:
                return code
        raise ParamError(f"工况 {self.code} 不含因子 {key}")

    def value_of(self, key: str, default: Any = None) -> Any:
        """取因子解析值；不存在时返回 ``default``。"""
        for name, value in self.values:
            if name == key:
                return value
        return default

    def level_mapping(self) -> dict[str, str]:
        """因子键 → 水平编码。"""
        return dict(self.level_codes)

    def value_mapping(self) -> dict[str, Any]:
        """因子键 → 解析值。"""
        return dict(self.values)

    @property
    def tbd(self) -> list[str]:
        """该工况中未冻结的因子键列表。"""
        return [key for key, value in self.values if is_tbd(value)]

    def slug(self) -> str:
        """目录名：``M001-abcdef12``；指纹缺失时只用编码。"""
        return f"{self.code}-{self.fingerprint[:8]}" if self.fingerprint else self.code


@dataclass(frozen=True)
class ParamTable:
    """一份完整参数表（已通过校验，警告单独保留）。"""

    factors: tuple[Factor, ...]
    main_keys: tuple[str, ...]
    supplementary: tuple[tuple[str, ...], ...]
    baseline: Mapping[str, str]
    defaults: Mapping[str, Any]
    run: Mapping[str, Any]
    meta: Mapping[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    source: Path | None = None

    def factor(self, key: str) -> Factor:
        """按因子键取因子。"""
        for item in self.factors:
            if item.key == key:
                return item
        raise ParamError(f"参数表没有因子 {key}")

    @property
    def factor_keys(self) -> tuple[str, ...]:
        """全部因子键（声明顺序）。"""
        return tuple(item.key for item in self.factors)

    def confidence_counts(self) -> dict[str, int]:
        """各置信等级的因子数量。"""
        counts = dict.fromkeys(CONFIDENCE_LEVELS, 0)
        for item in self.factors:
            counts[item.confidence] = counts.get(item.confidence, 0) + 1
        return counts

    def tbd_counts(self) -> dict[str, int]:
        """各因子的未冻结水平数量（只列非零项）。"""
        return {item.key: item.tbd_levels for item in self.factors if item.tbd_levels}

    @property
    def gates(self) -> Mapping[str, Any]:
        """门控配置（已按默认值补齐）。"""
        return self.defaults["gates"]


# ---------------------------------------------------------------------- #
# 读取与校验
# ---------------------------------------------------------------------- #
def load_params(path: Path) -> ParamTable:
    """读取 YAML 参数文件；文件缺失、编码错误或 YAML 语法错误都抛 :class:`ParamError`。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ParamError(f"无法读取参数文件 {path}: {exc}") from exc
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ParamError(f"参数文件 {path} 不是合法 YAML: {exc}") from exc
    return parse_params(raw, source=path)


def parse_params(raw: Any, *, source: Path | None = None) -> ParamTable:
    """校验并规范化参数表，返回 :class:`ParamTable`。"""
    if not isinstance(raw, Mapping):
        raise ParamError("参数文件顶层必须是映射（YAML 对象）")
    schema = raw.get("schema")
    if schema != SCHEMA:
        raise ParamError(f"参数文件 schema 应为 {SCHEMA}，实际为 {schema!r}")
    warnings: list[str] = []

    factors = _parse_factors(raw.get("factors"), warnings)
    by_key = {item.key: item for item in factors}

    matrix = raw.get("matrix")
    if not isinstance(matrix, Mapping):
        raise ParamError("缺少 matrix 段（至少需要 matrix.main 因子列表）")
    main_keys = _parse_keys(matrix.get("main"), "matrix.main", by_key)
    if not main_keys:
        raise ParamError("matrix.main 不能为空")
    supplementary = _parse_supplementary(matrix.get("supplementary"), by_key)
    baseline = _parse_baseline(matrix.get("baseline"), main_keys, by_key, warnings)

    defaults = _parse_settings(raw.get("defaults"), warnings)
    run = _parse_run(raw.get("run"), warnings)
    raw_meta = raw.get("meta")
    meta = raw_meta if isinstance(raw_meta, Mapping) else {}

    used = set(main_keys)
    for group in supplementary:
        used.update(group)
    unused = [item.key for item in factors if item.key not in used]
    if unused:
        warnings.append(f"因子未参与任何矩阵: {', '.join(unused)}")

    return ParamTable(
        factors=factors,
        main_keys=main_keys,
        supplementary=supplementary,
        baseline=baseline,
        defaults=defaults,
        run=run,
        meta=dict(meta),
        warnings=tuple(warnings),
        source=source,
    )


def _parse_factors(raw: Any, warnings: list[str]) -> tuple[Factor, ...]:
    """校验 factors 列表：键唯一、水平编码唯一、连续量只能是数值或 TBD。"""
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ParamError("factors 必须是列表")
    if not raw:
        raise ParamError("factors 不能为空")
    factors: list[Factor] = []
    seen: set[str] = set()
    for index, item in enumerate(raw, 1):
        if not isinstance(item, Mapping):
            raise ParamError(f"factors[{index}] 必须是映射")
        key = item.get("key")
        if not isinstance(key, str) or not key.strip():
            raise ParamError(f"factors[{index}] 缺少 key")
        key = key.strip()
        if key in seen:
            raise ParamError(f"因子 key 重复: {key}")
        seen.add(key)
        kind = item.get("kind", "continuous")
        if kind not in FACTOR_KINDS:
            raise ParamError(f"因子 {key} 的 kind 必须是 {'/'.join(FACTOR_KINDS)}，实际为 {kind!r}")
        confidence = item.get("confidence", "uncalibrated")
        if confidence not in CONFIDENCE_LEVELS:
            raise ParamError(f"因子 {key} 的 confidence 必须是 {'/'.join(CONFIDENCE_LEVELS)}，实际为 {confidence!r}")
        levels = _parse_levels(key, kind, item.get("levels"), warnings)
        factors.append(
            Factor(
                key=key,
                label=str(item.get("label") or key),
                unit=str(item.get("unit") or ""),
                kind=kind,
                confidence=confidence,
                levels=levels,
            )
        )
    return tuple(factors)


def _parse_levels(key: str, kind: str, raw: Any, warnings: list[str]) -> tuple[Level, ...]:
    """校验单个因子的水平列表。"""
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or not raw:
        raise ParamError(f"因子 {key} 的 levels 必须是非空列表")
    levels: list[Level] = []
    seen: set[str] = set()
    for index, item in enumerate(raw, 1):
        if not isinstance(item, Mapping):
            raise ParamError(f"因子 {key} 的 levels[{index}] 必须是映射")
        code = item.get("code")
        if not isinstance(code, str) or not code.strip():
            raise ParamError(f"因子 {key} 的 levels[{index}] 缺少 code")
        code = code.strip()
        if code in seen:
            raise ParamError(f"因子 {key} 的水平编码重复: {code}")
        seen.add(code)
        value = item.get("value", TBD)
        if value is None:
            value = TBD
        if (
            kind == "continuous"
            and not is_tbd(value)
            and (isinstance(value, bool) or not isinstance(value, (int, float)))
        ):
            raise ParamError(f"因子 {key} 是连续量，水平 {code} 的值必须是数值或 TBD，实际为 {value!r}")
        if kind == "discrete" and isinstance(value, (list, Mapping)):
            raise ParamError(f"因子 {key} 是离散量，水平 {code} 的值必须是标量，实际为 {type(value).__name__}")
        if is_tbd(value) and item.get("value", TBD) != TBD:
            warnings.append(f"因子 {key} 的水平 {code} 用 null 表示 TBD，已统一写成 {TBD}")
        levels.append(Level(code=code, value=TBD if is_tbd(value) else value, label=str(item.get("label") or "")))
    return tuple(levels)


def _parse_keys(raw: Any, where: str, by_key: Mapping[str, Factor]) -> tuple[str, ...]:
    """校验因子键列表引用。"""
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ParamError(f"{where} 必须是因子键列表")
    keys: list[str] = []
    for item in raw:
        if not isinstance(item, str) or item not in by_key:
            raise ParamError(f"{where} 引用了未定义因子: {item!r}")
        if item in keys:
            raise ParamError(f"{where} 中因子重复: {item}")
        keys.append(item)
    return tuple(keys)


def _parse_supplementary(raw: Any, by_key: Mapping[str, Factor]) -> tuple[tuple[str, ...], ...]:
    """校验补充组：每个内层列表是一组 OFAT / 小规模全因子。"""
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ParamError("matrix.supplementary 必须是「因子键列表」的列表")
    groups: list[tuple[str, ...]] = []
    for index, item in enumerate(raw, 1):
        keys = _parse_keys(item, f"matrix.supplementary[{index}]", by_key)
        if not keys:
            raise ParamError(f"matrix.supplementary[{index}] 不能为空")
        groups.append(keys)
    return tuple(groups)


def _parse_baseline(
    raw: Any,
    main_keys: Sequence[str],
    by_key: Mapping[str, Factor],
    warnings: list[str],
) -> dict[str, str]:
    """补充组基准：缺省取主矩阵各因子首个水平。"""
    baseline = {key: by_key[key].levels[0].code for key in main_keys}
    if raw is None:
        return baseline
    if not isinstance(raw, Mapping):
        raise ParamError("matrix.baseline 必须是「因子键 → 水平编码」的映射")
    for key, code in raw.items():
        if key not in by_key:
            warnings.append(f"matrix.baseline 引用了未定义因子 {key!r}，已忽略")
            continue
        if key not in baseline:
            warnings.append(f"matrix.baseline 的因子 {key} 不在主矩阵中，已忽略")
            continue
        if not isinstance(code, str):
            raise ParamError(f"matrix.baseline.{key} 必须是水平编码文本")
        by_key[key].level(code)
        baseline[key] = code
    return baseline


def _parse_gates(raw: Any, warnings: list[str]) -> dict[str, Any]:
    """门控配置：非法标量逐项回落默认值。"""
    gates = dict(DEFAULT_SETTINGS["gates"])
    empty_pairs: list[tuple[str, str]] = []
    gates["pairs"] = empty_pairs
    if raw is None:
        return gates
    if not isinstance(raw, Mapping):
        warnings.append("defaults.gates 不是映射，已使用默认门控配置")
        return gates
    for key in ("velocity_rel_diff", "arrival_l2_rel", "wall_impulse_rel"):
        value = raw.get(key, gates[key])
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            gates[key] = float(value)
        else:
            warnings.append(f"defaults.gates.{key} 非法，已回落默认 {gates[key]}")
    reference = raw.get("reference", gates["reference"])
    if isinstance(reference, str):
        gates["reference"] = reference.strip()
    else:
        warnings.append("defaults.gates.reference 非法，已回落空值")
    pairs = raw.get("pairs", [])
    if isinstance(pairs, Sequence) and not isinstance(pairs, (str, bytes)):
        normalized: list[tuple[str, str]] = []
        for index, item in enumerate(pairs, 1):
            if (
                isinstance(item, Sequence)
                and not isinstance(item, (str, bytes))
                and len(item) == 2
                and all(isinstance(name, str) and name.strip() for name in item)
            ):
                normalized.append((item[0].strip(), item[1].strip()))
            else:
                warnings.append(f"defaults.gates.pairs[{index}] 非法，已忽略")
        gates["pairs"] = normalized
    else:
        warnings.append("defaults.gates.pairs 非法，已忽略")
    return gates


def _parse_settings(  # noqa: PLR0912 - 逐字段校验与回落，展开写比表驱动更易审计
    raw: Any, warnings: list[str]
) -> dict[str, Any]:
    """后处理设置：非法字段逐项回落默认值。"""
    settings = dict(DEFAULT_SETTINGS)
    settings["gates"] = _parse_gates(None, warnings)
    settings["probes_mm"] = list(DEFAULT_SETTINGS["probes_mm"])
    if raw is None:
        return settings
    if not isinstance(raw, Mapping):
        warnings.append("defaults 不是映射，已使用默认后处理设置")
        return settings
    for key in ("skip_fraction", "tail_fraction"):
        value = raw.get(key, settings[key])
        if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value < 1:
            settings[key] = float(value)
        else:
            warnings.append(f"defaults.{key} 非法，已回落默认 {settings[key]}")
    if settings["skip_fraction"] + settings["tail_fraction"] >= 1:
        warnings.append("defaults.skip_fraction + tail_fraction 必须小于 1，已回落默认值")
        settings["skip_fraction"] = DEFAULT_SETTINGS["skip_fraction"]
        settings["tail_fraction"] = DEFAULT_SETTINGS["tail_fraction"]
    probes = raw.get("probes_mm", settings["probes_mm"])
    if isinstance(probes, Sequence) and not isinstance(probes, (str, bytes)):
        normalized: list[float] = []
        for index, item in enumerate(probes, 1):
            if isinstance(item, (int, float)) and not isinstance(item, bool) and item > 0:
                normalized.append(float(item))
            else:
                warnings.append(f"defaults.probes_mm[{index}] 非法，已忽略")
        if normalized:
            settings["probes_mm"] = normalized
        else:
            warnings.append("defaults.probes_mm 为空，已回落默认探针")
    else:
        warnings.append("defaults.probes_mm 非法，已回落默认探针")
    quench = raw.get("quench_fraction", settings["quench_fraction"])
    if isinstance(quench, (int, float)) and not isinstance(quench, bool) and 0 < quench <= 1:
        settings["quench_fraction"] = float(quench)
    else:
        warnings.append("defaults.quench_fraction 非法，已回落默认值")
    settings["gates"] = _parse_gates(raw.get("gates"), warnings)
    return settings


def _parse_run(  # noqa: PLR0912 - 逐字段校验与回落，展开写比表驱动更易审计
    raw: Any, warnings: list[str]
) -> dict[str, Any]:
    """求解配置：非法字段逐项回落默认值。"""
    run = {
        key: (list(value) if isinstance(value, list) else dict(value) if isinstance(value, dict) else value)
        for key, value in RUN_DEFAULTS.items()
    }
    if raw is None:
        return run
    if not isinstance(raw, Mapping):
        warnings.append("run 不是映射，已使用默认求解配置")
        return run

    solver_path = raw.get("solver_path", "")
    if isinstance(solver_path, str):
        run["solver_path"] = solver_path.strip()
    else:
        warnings.append("run.solver_path 非法，已回落自动探测")
    for key, minimum in (("ncpu", 1), ("timeout_s", 1), ("retries", 0), ("concurrency", 1)):
        value = raw.get(key, run[key])
        if isinstance(value, int) and not isinstance(value, bool) and value >= minimum:
            run[key] = value
        else:
            warnings.append(f"run.{key} 非法，已回落默认 {run[key]}")
    memory = raw.get("memory", "auto")
    if memory is None or memory == "":
        run["memory"] = None
    elif isinstance(memory, str):
        run["memory"] = memory.strip()
    else:
        warnings.append("run.memory 非法，已回落默认 auto")
    parallel = raw.get("parallel", "smp")
    if parallel in ("smp", "mpp"):
        run["parallel"] = parallel
    else:
        warnings.append("run.parallel 必须是 smp/mpp，已回落 smp")
    mpi = raw.get("mpi", "intel")
    if mpi in ("intel", "msmpi", "platform"):
        run["mpi"] = mpi
    else:
        warnings.append("run.mpi 必须是 intel/msmpi/platform，已回落 intel")
    extra = raw.get("extra_args", [])
    if isinstance(extra, list) and all(isinstance(item, str) for item in extra):
        run["extra_args"] = list(extra)
    else:
        warnings.append("run.extra_args 必须是文本列表，已忽略")
    env = raw.get("env", {})
    if isinstance(env, Mapping) and all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        run["env"] = dict(env)
    else:
        warnings.append("run.env 必须是「文本 → 文本」映射，已忽略")
    fake = raw.get("fake", False)
    if isinstance(fake, bool):
        run["fake"] = fake
    else:
        warnings.append("run.fake 必须是布尔值，已回落 false")
    return run


# ---------------------------------------------------------------------- #
# 矩阵展开与指纹
# ---------------------------------------------------------------------- #
def expand_main_cases(table: ParamTable) -> list[Case]:
    """主矩阵：按 ``matrix.main`` 声明顺序做笛卡尔积。"""
    combos = itertools.product(*(table.factor(key).levels for key in table.main_keys))
    cases: list[Case] = []
    for index, combo in enumerate(combos, 1):
        levels = tuple((key, level.code) for key, level in zip(table.main_keys, combo, strict=True))
        values = tuple((key, level.value) for key, level in zip(table.main_keys, combo, strict=True))
        cases.append(Case(code=f"M{index:03d}", group="main", level_codes=levels, values=values))
    return cases


def expand_supplementary_cases(table: ParamTable) -> list[Case]:
    """补充组：以 baseline 为底，每组因子做 OFAT / 组内全因子。"""
    cases: list[Case] = []
    for group_index, group in enumerate(table.supplementary, 1):
        factors = [table.factor(key) for key in group]
        combos = itertools.product(*(item.levels for item in factors))
        for index, combo in enumerate(combos, 1):
            levels: dict[str, str] = {}
            values: dict[str, Any] = {}
            for key in table.main_keys:
                baseline_level = table.factor(key).level(table.baseline[key])
                levels[key] = baseline_level.code
                values[key] = baseline_level.value
            for factor, level in zip(factors, combo, strict=True):
                levels[factor.key] = level.code
                values[factor.key] = level.value
            cases.append(
                Case(
                    code=f"S{group_index}-{index:03d}",
                    group=f"supplementary:{'-'.join(group)}",
                    level_codes=tuple(levels.items()),
                    values=tuple(values.items()),
                )
            )
    return cases


def build_cases(table: ParamTable, template_digest: str = "") -> list[Case]:
    """展开全部工况并写入计算指纹（含模板摘要）。"""
    cases = [*expand_main_cases(table), *expand_supplementary_cases(table)]
    return [
        Case(
            code=item.code,
            group=item.group,
            level_codes=item.level_codes,
            values=item.values,
            fingerprint=case_fingerprint(item, template_digest),
        )
        for item in cases
    ]


def case_fingerprint(case: Case, template_digest: str = "", model_digest: str = "") -> str:
    """工况指纹：schema + 编码 + 分组 + 水平 + 解析值 + 模板摘要 + 模型指纹。"""
    payload = {
        "schema": SCHEMA,
        "code": case.code,
        "group": case.group,
        "levels": [[key, code] for key, code in case.level_codes],
        "values": [[key, _text(value)] for key, value in case.values],
        "template": template_digest,
        "model": model_digest,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------- #
# 导入导出与摘要
# ---------------------------------------------------------------------- #
def params_to_mapping(table: ParamTable) -> dict[str, Any]:
    """把参数表还原成可再导出的映射（保留因子顺序与注释性字段）。"""
    return {
        "schema": SCHEMA,
        "meta": dict(table.meta),
        "factors": [
            {
                "key": factor.key,
                "label": factor.label,
                "unit": factor.unit,
                "kind": factor.kind,
                "confidence": factor.confidence,
                "levels": [{"code": level.code, "value": level.value, "label": level.label} for level in factor.levels],
            }
            for factor in table.factors
        ],
        "matrix": {
            "main": list(table.main_keys),
            "supplementary": [list(group) for group in table.supplementary],
            "baseline": dict(table.baseline),
        },
        "defaults": {
            "skip_fraction": table.defaults["skip_fraction"],
            "tail_fraction": table.defaults["tail_fraction"],
            "probes_mm": list(table.defaults["probes_mm"]),
            "quench_fraction": table.defaults["quench_fraction"],
            "gates": {
                "velocity_rel_diff": table.gates["velocity_rel_diff"],
                "arrival_l2_rel": table.gates["arrival_l2_rel"],
                "wall_impulse_rel": table.gates["wall_impulse_rel"],
                "reference": table.gates["reference"],
                "pairs": [list(pair) for pair in table.gates["pairs"]],
            },
        },
        "run": dict(table.run),
    }


def save_params(table: ParamTable, path: Path) -> None:
    """导出参数表：UTF-8、保留中文、不排序键，失败抛 :class:`ParamError`。"""
    try:
        path.write_text(
            yaml.safe_dump(params_to_mapping(table), allow_unicode=True, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
            newline="\n",
        )
    except OSError as exc:
        raise ParamError(f"无法写入参数文件 {path}: {exc}") from exc


def example_params() -> str:
    """生成全 TBD 的占位参数表示例文本（不含任何真实配方、尺寸或材料值）。"""
    return """# dynacalc 参数表（示例）：所有数值均为占位符 TBD，禁止填入真实配方/尺寸后提交到公开仓库。
schema: dynacalc/1
meta:
  project: 占位项目
  revision: r1
  note: 全部水平未冻结；TBD 会原样写入 deck，不会被替换成公开材料卡数值。
factors:
  - key: density
    label: 装药平均密度
    unit: g/cm^3
    kind: continuous
    confidence: controlled
    levels:
      - {code: L1, value: TBD, label: 低密度}
      - {code: H1, value: TBD, label: 高密度}
  - key: inner_diameter
    label: 装药内径
    unit: mm
    kind: continuous
    confidence: controlled
    levels:
      - {code: ID1, value: TBD, label: 小内径}
      - {code: ID2, value: TBD, label: 标称内径}
      - {code: ID3, value: TBD, label: 大内径}
  - key: wall_thickness
    label: 管壁平均厚度
    unit: mm
    kind: continuous
    confidence: controlled
    levels:
      - {code: T1, value: TBD, label: 薄壁}
      - {code: T2, value: TBD, label: 厚壁}
  - key: axial_density
    label: 轴向密度状态
    unit: ""
    kind: discrete
    confidence: uncalibrated
    levels:
      - {code: U, value: uniform, label: 均匀}
      - {code: E, value: end_porous, label: 端部疏松}
  - key: eccentricity
    label: 管壁偏心状态
    unit: ""
    kind: discrete
    confidence: uncalibrated
    levels:
      - {code: S, value: symmetric, label: 对称}
      - {code: X, value: eccentric, label: 偏心}
  - key: gap_mm
    label: 药管间隙
    unit: mm
    kind: continuous
    confidence: uncalibrated
    levels:
      - {code: G0, value: 0, label: 理想接触}
      - {code: G1, value: TBD, label: 小间隙}
      - {code: G2, value: TBD, label: 大间隙}
  - key: initiation_strength
    label: 起爆输入强度
    unit: ""
    kind: discrete
    confidence: uncalibrated
    levels:
      - {code: W, value: weak, label: 弱}
      - {code: N, value: nominal, label: 标称}
      - {code: S, value: strong, label: 强}
matrix:
  main: [density, inner_diameter, wall_thickness, axial_density, eccentricity]
  supplementary:
    - [gap_mm]
    - [initiation_strength]
  baseline:
    density: L1
    inner_diameter: ID1
    wall_thickness: T1
    axial_density: U
    eccentricity: S
defaults:
  skip_fraction: 0.2
  tail_fraction: 0.1
  probes_mm: [50, 100, 150]
  quench_fraction: 0.99
  gates:
    velocity_rel_diff: 0.02
    arrival_l2_rel: 0.03
    wall_impulse_rel: 0.05
    reference: ""
    pairs: []
run:
  solver_path: ""
  ncpu: 4
  memory: auto
  parallel: smp
  mpi: intel
  extra_args: []
  env: {}
  timeout_s: 3600
  retries: 0
  concurrency: 1
  fake: false
"""


def summary_rows(table: ParamTable) -> list[tuple[str, str, str, str, str, str]]:
    """参数摘要行：因子、单位、类型、置信等级、水平数、TBD 数。"""
    return [
        (
            factor.key,
            factor.label,
            factor.unit or "-",
            factor.confidence_text,
            str(len(factor.levels)),
            str(factor.tbd_levels),
        )
        for factor in table.factors
    ]
