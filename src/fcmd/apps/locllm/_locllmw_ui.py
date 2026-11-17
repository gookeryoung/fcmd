"""locllmw 的共享界面逻辑：模型扫描、分组与已加载服务的判定（纯函数）。

Qt 页面（``_locllmw_qt_*``）与运行页复用这里的口径；本模块不创建任何窗口，
CI 无显示环境也能直接单测。
"""

from __future__ import annotations

import re
import socket
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from fcmd.apps.locllm import llmenv, llmmodel, llmserve
from fcmd.apps.locllm._llmgguf import GgufPrecision, detect_precision
from fcmd.apps.locllm._llmgguf_cache import PrecisionCache
from fcmd.apps.locllm._locllmw_monitor import _FIT_BUSY, _FIT_UNSUPPORTED
from fcmd.apps.locllm.llmbase import (
    DEFAULT_HOST,
    PROFILES,
    ModelEntry,
    ServeState,
)
from fcmd.apps.locllm.llmserve import DEFAULT_NAME

_HINT_PLACEHOLDER = "选择模型后显示能力标签（代码/推理/对话/视觉/数学/重排/嵌入/通用）、硬件适配与最佳实践建议"
_QUANT_SUFFIX_RE = re.compile(r"[-_.](?:I?Q\d+(?:_[A-Za-z0-9]+)*|BF16|F16|F32)$", re.IGNORECASE)

_PRECISION_WORKERS = 4
"""并行识别 GGUF 精度的线程数：单文件 200–400 ms，4 路即可覆盖磁盘与 CPU 等待。"""


def _detect_precisions(entries: list[ModelEntry], *, cache: PrecisionCache | None = None) -> dict[str, GgufPrecision]:
    """识别每个 GGUF 的精度：缓存命中直接复用，未命中的并行解析（最多 4 路）。

    结果按 ``entries`` 顺序组装，与串行实现的唯一区别是解析并发；``cache`` 省略时
    使用默认缓存文件，写入失败会自动降级。列表只需要区分全量 / 量化，因此走
    ``tensors=False`` 快路径，读到 ``general.file_type`` 就已结束，不解析张量表。
    """
    store = cache if cache is not None else PrecisionCache()
    results: list[GgufPrecision | None] = [None] * len(entries)
    pending: list[int] = []
    for index, entry in enumerate(entries):
        hit = store.get(entry.path)
        if hit is not None:
            results[index] = hit
        else:
            pending.append(index)
    if pending:
        workers = min(_PRECISION_WORKERS, len(pending))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="gguf-precision") as pool:
            parsed = pool.map(lambda item: detect_precision(entries[item].path, tensors=False), pending)
            for index, precision in zip(pending, parsed, strict=True):
                results[index] = precision
                store.remember(entries[index].path, precision)
    store.save()
    return {
        llmenv.path_key(entry.path): precision
        for entry, precision in zip(entries, results, strict=True)
        if precision is not None
    }


def _scan_dir(root: Path) -> tuple[list[ModelEntry], str | None]:
    """扫描模型目录并分类问题；空目录由调用方结合运行中服务再判断。"""
    if not root.is_dir():
        return [], f"模型目录不存在：{root}"
    try:
        return llmmodel.scan_models(root), None
    except PermissionError:
        return [], f"没有权限读取模型目录：{root}"
    except OSError as exc:
        return [], f"扫描模型目录失败：{exc}"


_GROUP_ROOT_LABEL = "模型目录（根）"
"""直接放在模型目录下、未归入子目录的模型所在分组的名称。"""


def state_tag(state: str) -> tuple[str, ...]:
    """把硬件状态文案映射成列表行的着色标签（红 / 橙，其余不着色）。"""
    return {_FIT_UNSUPPORTED: ("unfit",), _FIT_BUSY: ("busy",)}.get(state, ())


def _group_name(entry: ModelEntry, root: Path | None) -> str:
    """取模型的一级子目录作为分组名，这是清单里最稳定的天然归属。

    运行页清单来自 ``llmenv.models_dir()`` 的递归扫描，一级子目录对应下载仓库
    （``<组织>/<模型>``）或用户自己的归档目录；直接放在根下的模型归入
    ``_GROUP_ROOT_LABEL``。不在模型目录下的模型（运行中服务的模型可能来自别处）
    退回父目录名。
    """
    base = root if root is not None else entry.root
    try:
        relative = entry.path.relative_to(base)
    except ValueError:
        parent = entry.path.parent
        return parent.name or str(parent)
    if len(relative.parts) <= 1:
        return _GROUP_ROOT_LABEL
    return relative.parts[0]


def _group_models(
    entries: list[ModelEntry],
    root: Path | None,
) -> list[tuple[str, list[tuple[int, ModelEntry]]]]:
    """按一级子目录分组；组内保持传入清单的原有顺序（已按大小降序）。"""
    groups: dict[str, list[tuple[int, ModelEntry]]] = {}
    for index, entry in enumerate(entries):
        groups.setdefault(_group_name(entry, root), []).append((index, entry))
    return list(groups.items())


def _default_open_groups(
    groups: list[tuple[str, list[tuple[int, ModelEntry]]]],
    running_keys: set[str],
    selected: Path | None,
) -> set[str]:
    """首屏默认展开的组：有运行中实例的组，以及当前选中模型所在的组。

    其余组保持收起，让首屏只看到组名与数量；选中组必须展开，否则已有选中项
    会隐藏在收起的分组里。
    """
    selected_key = llmenv.path_key(selected) if selected is not None else None
    return {
        label
        for label, items in groups
        if any(
            llmenv.path_key(entry.path) in running_keys
            or (selected_key is not None and llmenv.path_key(entry.path) == selected_key)
            for _index, entry in items
        )
    }


@dataclass(frozen=True)
class _ScanOutcome:
    """一次后台扫描的结果：模型清单 + 运行环境状态。"""

    entries: list[ModelEntry]
    gpu_mib: int | None
    free_mib: int | None
    precisions: dict[str, GgufPrecision]
    issue: str | None = None
    engine_missing: bool = False
    port_busy: bool = False
    models_root: Path | None = None


def service_name_from_model(path: Path) -> str:
    """按模型文件名生成服务名：去掉量化后缀，把非法字符转为 ``-``。

    各前端共用这一口径，服务名保持一致。
    """
    stem = _QUANT_SUFFIX_RE.sub("", path.stem)
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-._")
    return cleaned or DEFAULT_NAME


def entries_with_running_models(
    entries: list[ModelEntry],
    states: list[ServeState] | None = None,
) -> list[ModelEntry]:
    """把运行中服务的模型并入清单，便于停止已加载模型时在列表中找到。

    只并入进程仍存活、且不在模型目录清单里的模型；结果按大小降序，
    与目录扫描的排序口径一致。
    """
    seen = {llmenv.path_key(entry.path) for entry in entries}
    for state in states if states is not None else llmenv.list_states():
        path = Path(state.model)
        key = llmenv.path_key(path)
        if key in seen or not llmserve.process_alive(state.pid, state.create_time):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        seen.add(key)
        entries.append(
            ModelEntry(
                root=path.parent,
                path=path,
                size=size,
                profiles=tuple(name for name, prof in PROFILES.items() if prof.quant.lower() in path.name.lower()),
                tags=llmmodel.detect_model_tags(path.name),
            )
        )
    entries.sort(key=lambda entry: (-entry.size, str(entry.path)))
    return entries


def default_port_busy(states: list[ServeState], port: int | None, *, host: str = DEFAULT_HOST) -> bool:
    """默认端口被其它进程占用时返回 ``True``。

    本窗口登记且存活的同端口服务不算占用（否则刚启动的服务会被误判为端口冲突）。
    """
    if port is None:
        return False
    if any(state.port == port and llmserve.process_alive(state.pid, state.create_time) for state in states):
        return False
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False
