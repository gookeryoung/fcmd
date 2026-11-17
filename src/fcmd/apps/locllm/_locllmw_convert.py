"""locllmw「模型」页的转换 / 量化共享逻辑（Qt 子页复用）。

两者在模型页中是同级标签页（「转换」之后是「量化」），职责严格分开，不出现重复
或交叉的控件：

* 转换：只负责 HF safetensors → GGUF，扫描模型目录中的可转换目录，配置精度 /
  解释器 / 源码 / 引擎 tag，输出默认 ``<models_dir>/converted/<源目录名>/<源目录名>.gguf``；
* 量化：完整承载 GGUF 量化，选择输入模型、量化类型与输出目录（默认
  ``<models_dir>/converted/``）、覆盖策略，展示量化器环境、进度与日志。

两块都复用 :mod:`llmconvert` / :mod:`llmqnt` 的核心逻辑，不在 GUI 里重写命令。
本模块只保留纯逻辑（目录扫描、输出路径、表单快照、进度解析）与 Qt 子页共用。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from fcmd.apps.locllm import _llmconfig, llmconvert, llmengine, llmmodel, llmqnt
from fcmd.apps.locllm._llmgguf import GgufPrecision
from fcmd.apps.locllm._locllmw_ui import _detect_precisions
from fcmd.apps.locllm.llmbase import ModelEntry, ServeError, human_size

__all__ = [
    "ModelEnv",
    "default_output_path",
    "output_path_from_form",
    "scan_model_env",
]


@dataclass(frozen=True, slots=True)
class ModelEnv:
    """转换 / 量化页共用的环境快照。

    一次扫描同时给出引擎与量化器状态、可转换 HF 目录、已配置 GGUF 与精度，
    避免两个子页各自重复读盘与探测。
    """

    info: dict[str, str]
    tag: str | None
    convertible: list[tuple[Path, int]]
    entries: list[ModelEntry]
    precisions: dict[str, GgufPrecision]


def scan_model_env() -> ModelEnv:
    """扫描模型目录、引擎与量化器状态（阻塞，供线程池调用）。"""
    info = {"engine": "-", "quantizer": "-", "version": "-"}
    tag: str | None = None
    try:
        engine = llmengine.resolve_engine()
    except ServeError as exc:
        info["engine"] = f"未找到（{exc}）"
    else:
        tag = engine.tag
        info["engine"] = f"{engine.tag} · {engine.exe}"
        try:
            info["version"] = llmengine.probe_version(engine)
        except ServeError:
            info["version"] = "-"
    try:
        info["quantizer"] = str(llmqnt.resolve_quantize_exe())
    except llmqnt.QuantizeError as exc:
        info["quantizer"] = f"未找到（{exc}）"

    root = _llmconfig.models_dir()
    convertible = _find_convertible(root)
    entries: list[ModelEntry] = []
    try:
        if root.is_dir():
            entries = llmmodel.scan_models(root)
    except OSError:
        entries = []
    precisions = _detect_precisions(entries)
    return ModelEnv(info=info, tag=tag, convertible=convertible, entries=entries, precisions=precisions)


_SCRIPT_PLACEHOLDER = "留空自动获取与引擎 tag 匹配的 llama.cpp 源码"
_PERCENT_RE = re.compile(r"(\d{1,3})%\|")


# ---------------------------------------------------------------------- #
# 纯逻辑：可独立测试，不创建窗口
# ---------------------------------------------------------------------- #
def _progress_percent(line: str) -> int | None:
    """从 tqdm 进度行（如 `` 45%|████▌ |``）提取百分比，非进度行返回 ``None``。"""
    match = _PERCENT_RE.search(line)
    if match is None:
        return None
    return min(100, int(match.group(1)))


def _hf_size(path: Path) -> int:
    """统计目录下 safetensors 文件总字节数（不可读文件按 0 计）。"""
    total = 0
    for item in path.glob("*.safetensors"):
        try:
            total += item.stat().st_size
        except OSError:
            continue
    return total


def _dir_stats(path: Path) -> str:
    """统计目录下 safetensors 文件数量与总大小（用于转换前的规模预估）。"""
    count = len(list(path.glob("*.safetensors")))
    return f"{count} 个 safetensors，共 {human_size(_hf_size(path))}"


def default_output_path(source: Path, root: Path | None = None) -> Path:
    """默认输出路径：``<模型目录>/converted/<源目录名>/<源目录名>.gguf``。"""
    return _llmconfig.products_dir(root) / source.name / f"{source.name}.gguf"


def output_path_from_form(source_text: str, output_text: str, root: Path | None = None) -> Path | None:
    """按表单文本解析输出路径：显式输出优先，留空时落到模型目录的 converted 子目录。"""
    text = source_text.strip()
    if not text:
        return None
    output = output_text.strip()
    if output:
        return Path(output).expanduser()
    return default_output_path(Path(text).expanduser(), root)


def _find_convertible(root: Path) -> list[tuple[Path, int]]:
    """在模型目录中查找可转换的 HF safetensors 目录（含根目录及向下两层）。

    兼容 ``<root>/<模型>`` 与 ``<root>/<组织>/<模型>`` 两种常见布局；命中 HF 目录后
    不再继续向下扫描，避免把模型内部文件误判为可转换目录。
    """
    found: list[tuple[Path, int]] = []
    if not root.is_dir():
        return found
    try:
        if llmconvert.is_hf_safetensors_dir(root):
            return [(root, _hf_size(root))]
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            if llmconvert.is_hf_safetensors_dir(child):
                found.append((child, _hf_size(child)))
                continue
            for grandchild in sorted(child.iterdir()):
                if grandchild.is_dir() and llmconvert.is_hf_safetensors_dir(grandchild):
                    found.append((grandchild, _hf_size(grandchild)))
    except OSError:
        return found
    return found


@dataclass(frozen=True, slots=True)
class _ConvertForm:
    """主线程快照的表单值；后台线程不得直接读取界面控件。"""

    source: str
    output: str
    outtype: str
    script: str
    python: str
    tag: str
