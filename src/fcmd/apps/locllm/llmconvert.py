"""llmconvert - HuggingFace safetensors 转 GGUF（可复用本地源码 zip、可选再量化）。

独立命令：``llmconvert <源目录> [--output ...]``。
"""

from __future__ import annotations

import ast
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import fcmd
from fcmd.apps.locllm import llmbase, llmengine, llmenv
from fcmd.apps.locllm.llmbase import (
    CHUNK_SIZE,
    DEFAULT_ENGINE_TAG,
    MSVC_HINT,
    Opener,
    Runner,
    ServeError,
    format_returncode,
    run_command,
)
from fcmd.console import Table, get_console

__all__ = [
    "CONVERT_OUTTYPES",
    "CONVERT_REPO_URL",
    "CONVERT_SCRIPT",
    "ConvertPlan",
    "check_convert_source",
    "convert_model",
    "convert_source_root",
    "ensure_convert_script",
    "find_convert_python",
    "is_hf_safetensors_dir",
    "plan_convert",
    "root_error_line",
    "summarize_convert_line",
]

CONVERT_SCRIPT = "convert_hf_to_gguf.py"
CONVERT_REPO_URL = "https://github.com/ggml-org/llama.cpp"
CONVERT_OUTTYPES = ("f32", "f16", "bf16", "q8_0")
CONVERT_TIMEOUT = 6 * 3600.0
QUANTIZE_TIMEOUT = 2 * 3600.0
CLONE_TIMEOUT = 900.0
_SOURCE_ATTEMPTS = 5
_SOURCE_TIMEOUT = 30.0
_SOURCE_RETRY_DELAY = 1.0
_BUILD_TAG_RE = re.compile(r"^b\d+$")
_ACCESS_VIOLATION = 0xC0000005
_ARCH_MAPPING_RE = re.compile(r"""^\s*["']([\w.]+)["']\s*:\s*["']([\w.-]+)["']\s*,\s*$""", re.MULTILINE)
_ERROR_LINE_RE = re.compile(r"^[A-Za-z_][\w.]*(?:Error|Exception):\s")
_TOKENIZER_HINT = "tokenizer.json 或 tokenizer.model 或 vocab.json + merges.txt"


@dataclass(frozen=True, slots=True)
class ConvertPlan:
    """一次 ``safetensors → GGUF`` 转换所需的全部参数。"""

    source: Path
    output: Path
    outtype: str
    script: Path
    python: str
    quant: str | None = None
    quantize_exe: Path | None = None

    @property
    def intermediate(self) -> str:
        """转换脚本直接产出的文件（量化时作为临时中间文件）。"""
        if self.quant is None:
            return str(self.output)
        return str(self.output.with_name(f"{self.output.stem}-{self.outtype}.gguf"))

    def convert_argv(self) -> list[str]:
        """转换脚本命令行。"""
        return [
            self.python,
            str(self.script),
            str(self.source),
            "--outfile",
            self.intermediate,
            "--outtype",
            self.outtype,
        ]

    def quantize_argv(self) -> list[str]:
        """量化命令行。"""
        if self.quant is None or self.quantize_exe is None:
            raise ServeError("未指定量化类型或缺少 llama-quantize")
        return [str(self.quantize_exe), self.intermediate, str(self.output), self.quant]


def is_hf_safetensors_dir(path: Path) -> bool:
    """判断目录是否为 HuggingFace safetensors 模型目录。"""
    target = path.expanduser()
    return target.is_dir() and (target / "config.json").is_file() and any(target.glob("*.safetensors"))


def _registered_architectures(text: str) -> set[str]:
    """解析 ``@ModelBase.register(...)`` 装饰器声明的架构名（支持跨行多参数）。"""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        decorators = getattr(node, "decorator_list", ())
        for decorator in decorators:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            if not (isinstance(func, ast.Attribute) and func.attr == "register"):
                continue
            names.update(
                arg.value for arg in decorator.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
            )
    return names


def _engine_architectures(repo: Path) -> tuple[set[str], dict[str, str]]:
    """引擎源码里已注册的转换架构与「架构 → model_type」映射。

    注册以 ``conversion/*.py`` 的 ``@ModelBase.register("...")`` 为准，是
    ``convert_hf_to_gguf.py`` 实际可转换的架构集合；映射来自
    ``conversion/__init__.py`` 的 ``ARCH_NAMES`` 字面量，仅用于提示缺失字段时的候选。
    """
    conversion = repo / "conversion"
    registered: set[str] = set()
    mapping: dict[str, str] = {}
    if not conversion.is_dir():
        return registered, mapping
    for path in sorted(conversion.glob("*.py")):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        registered.update(_registered_architectures(text))
        if path.name == "__init__.py":
            for match in _ARCH_MAPPING_RE.finditer(text):
                mapping[match.group(1)] = match.group(2)
    return registered, mapping


def _tokenizer_missing(source: Path) -> bool:
    """是否缺少任何一种可用的令牌器资源。"""
    if any((source / name).is_file() for name in ("tokenizer.json", "tokenizer.model", "spiece.model")):
        return False
    return not ((source / "vocab.json").is_file() and (source / "merges.txt").is_file())


def check_convert_source(source: Path, repo: Path | None = None, *, tag: str | None = None) -> None:
    """转换前置检查：元数据、令牌器资源与引擎架构支持。

    ``repo`` 是 llama.cpp 源码目录（内含 ``conversion/``）；无法定位源码时只做
    本地文件检查。不满足条件时抛出带可执行建议的 :class:`ServeError`，避免把
    转换脚本的 Python 栈直接抛给用户。
    """
    target = source.expanduser()
    config_path = target / "config.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ServeError(f"无法读取模型配置 {config_path}：{exc}") from exc
    if not isinstance(config, dict):
        raise ServeError(f"模型配置不是 JSON 对象：{config_path}")
    if _tokenizer_missing(target):
        raise ServeError(f"模型目录缺少转换必需的令牌器文件（{_TOKENIZER_HINT}）：{target}")

    registered, mapping = _engine_architectures(repo) if repo is not None else (set(), {})
    arches = config.get("architectures")
    arch = arches[0] if isinstance(arches, list) and arches else None
    if not isinstance(arch, str) or not arch:
        model_type = str(config.get("model_type") or "未知")
        candidates = sorted(
            name for name, kind in mapping.items() if kind == model_type and (not registered or name in registered)
        )
        hint = ""
        if candidates:
            hint = f'；若该模型确为 {model_type}，可在 config.json 增加 "architectures": ["{candidates[0]}"]'
        raise ServeError(
            f"模型配置缺少 architectures 字段（model_type={model_type}），转换脚本无法判定架构。"
            f"该目录可能是测试桩或导出不完整{hint}，或改用完整模型目录重试"
        )
    if registered and arch not in registered:
        where = f"引擎 {tag} 的转换脚本" if tag else "当前引擎的转换脚本"
        raise ServeError(
            f"{where}不支持架构 {arch}（该版本已注册 {len(registered)} 个架构）："
            f"请升级引擎（fcmd llmengine install --tag <新版>）或换用已支持架构的模型"
        )


def convert_source_root(tag: str, home: Path | None = None) -> Path:
    """llama.cpp 源码目录（按引擎 tag 隔离，内含转换脚本与 ``gguf-py``）。"""
    return llmenv.engines_root(home) / tag / "llama.cpp"


def _source_archive_urls(ref: str) -> list[str]:
    """llama.cpp 源码包地址：官方 codeload 优先，GitHub archive 兜底。

    单流 zip 比 ``git clone`` 更耐受网络抖动，且官方 release 引擎包只带二进制，
    转换脚本必须另行获取源码。
    """
    path = f"refs/tags/{ref}" if _BUILD_TAG_RE.fullmatch(ref) else "refs/heads/master"
    return [
        f"https://codeload.github.com/ggml-org/llama.cpp/zip/{path}",
        f"https://github.com/ggml-org/llama.cpp/archive/{path}.zip",
    ]


def _local_source_archives(ref: str, repo: Path) -> list[Path]:
    """引擎目录下已有的源码 zip，按 tag 精确匹配优先。"""
    preferred = [repo.parent / f"llama.cpp-{ref}.zip", repo.parent / "llama.cpp.zip"]
    found = [item for item in preferred if item.is_file()]
    for folder in (repo.parent, repo.parent.parent):
        if folder.is_dir():
            for candidate in sorted(folder.glob("llama.cpp*.zip")):
                if candidate.is_file() and candidate not in found:
                    found.append(candidate)
    return found


def _source_archive_ready(archive: Path) -> bool:
    """校验 zip 是否为可用源码包：含 ``convert_hf_to_gguf.py``（可带一层顶层目录）。"""
    try:
        with zipfile.ZipFile(archive) as bundle:
            return any(name.rstrip("/").endswith(CONVERT_SCRIPT) and name.count("/") <= 1 for name in bundle.namelist())
    except (OSError, zipfile.BadZipFile):
        return False


def _extract_source_archive(archive: Path, repo: Path) -> bool:
    """把源码 zip 解压为 ``repo``（兼容带顶层目录与扁平打包），返回是否含转换脚本。"""
    staging = repo.parent / f".{repo.name}.extract"
    shutil.rmtree(staging, ignore_errors=True)
    try:
        staging.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(staging)
        entries = list(staging.iterdir())
        roots = [item for item in entries if item.is_dir()]
        if len(roots) == 1 and len(entries) == 1:
            shutil.rmtree(repo, ignore_errors=True)
            roots[0].replace(repo)
        else:
            shutil.rmtree(repo, ignore_errors=True)
            repo.mkdir(parents=True)
            for item in entries:
                item.replace(repo / item.name)
        return (repo / CONVERT_SCRIPT).is_file()
    except (OSError, zipfile.BadZipFile):
        return False
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _download_resumable(url: str, dest: Path, opener: Opener | None = None) -> int:
    """带 Range 续传与重试的下载，专用于体积较大的源码包。

    GitHub 在部分网络下会在传输中重置连接；这里把未完成数据留在 ``.part``，
    下次尝试用 ``Range`` 从断点续传，避免每次都从零开始。
    """
    from urllib.request import Request, urlopen

    part = dest.with_name(dest.name + ".part")
    part.parent.mkdir(parents=True, exist_ok=True)
    open_fn = opener or (lambda request, timeout: urlopen(request, timeout=timeout))
    last_error: OSError | None = None
    for attempt in range(_SOURCE_ATTEMPTS):
        start = part.stat().st_size if part.is_file() else 0
        headers = {"User-Agent": "fcmd-llm/0.3.10"}
        if start:
            headers["Range"] = f"bytes={start}-"
        try:
            with open_fn(Request(url, headers=headers), _SOURCE_TIMEOUT) as resp:
                status = getattr(resp, "status", 200)
                mode = "ab" if start and status == 206 else "wb"
                expected = getattr(resp, "headers", None)
                total = None
                if expected is not None:
                    with contextlib.suppress(TypeError, ValueError):
                        total = int(expected.get("Content-Length") or 0) + (start if mode == "ab" else 0)
                with part.open(mode) as out:
                    while chunk := resp.read(CHUNK_SIZE):
                        out.write(chunk)
        except OSError as exc:
            last_error = exc
            time.sleep(_SOURCE_RETRY_DELAY * (attempt + 1))
            continue
        size = part.stat().st_size
        if total and size < total:
            last_error = OSError(f"传输中断（{size}/{total} 字节）")
            time.sleep(_SOURCE_RETRY_DELAY * (attempt + 1))
            continue
        part.replace(dest)
        return size
    part.unlink(missing_ok=True)
    raise ServeError(f"下载失败: {url}（重试 {_SOURCE_ATTEMPTS} 次；{last_error or '未知错误'}）")


def _download_convert_source(ref: str, repo: Path, opener: Opener | None) -> bool:
    """下载并解压源码包；地址不可用时逐个尝试，全部失败返回 ``False``。"""
    archive = repo.parent / f"{repo.name}-{ref}.zip"
    for url in _source_archive_urls(ref):
        try:
            _download_resumable(url, archive, opener)
        except ServeError:
            archive.unlink(missing_ok=True)
            continue
        extracted = zipfile.is_zipfile(archive) and _extract_source_archive(archive, repo)
        archive.unlink(missing_ok=True)
        if extracted:
            return True
    return False


def ensure_convert_script(
    tag: str | None = None,
    script: Path | None = None,
    home: Path | None = None,
    runner: Runner = run_command,
    opener: Opener | None = None,
) -> Path:
    """定位转换脚本：显式 ``--script``（脚本或本地源码 zip）→ ``FCMD_CONVERT_SCRIPT``
    → 本地源码 → 引擎目录已有 ``llama.cpp*.zip`` → 源码包下载 → 浅克隆。

    现代 llama.cpp 的 ``convert_hf_to_gguf.py`` 依赖仓库内的 ``conversion`` 包与
    ``gguf-py``，因此不能单独下载脚本，必须保留完整源码目录；官方 release 引擎
    包只有二进制，首次转换会自动获取与引擎 tag 匹配的源码。若 ``--script`` 指向
    本地源码 zip，或引擎目录（及其根目录）内已放好 ``llama.cpp*.zip``，则校验后
    直接解压复用，不联网也不 clone。
    """
    root = llmenv.engines_root(home)
    resolved = tag or llmengine.current_engine_tag(root) or DEFAULT_ENGINE_TAG
    cached = convert_source_root(resolved, home) / CONVERT_SCRIPT

    raw = script or (
        Path(os.environ["FCMD_CONVERT_SCRIPT"]).expanduser() if os.environ.get("FCMD_CONVERT_SCRIPT") else None
    )
    if raw is not None:
        target = Path(raw).expanduser()
        if target.suffix.lower() == ".zip":
            if not target.is_file():
                raise ServeError(f"源码压缩包不存在: {target}")
            if not _source_archive_ready(target) or not _extract_source_archive(target, cached.parent):
                raise ServeError(f"不是可用的 llama.cpp 源码包（缺少 {CONVERT_SCRIPT}）: {target}")
            return cached
        if not target.is_file():
            raise ServeError(f"转换脚本不存在: {target}")
        return target

    if cached.is_file():
        return cached

    ref = resolved if _BUILD_TAG_RE.fullmatch(resolved) else "master"
    repo = cached.parent
    repo.parent.mkdir(parents=True, exist_ok=True)
    for archive in _local_source_archives(ref, repo):
        if _source_archive_ready(archive) and _extract_source_archive(archive, repo):
            return cached
    if _download_convert_source(ref, repo, opener):
        return cached

    shutil.rmtree(repo, ignore_errors=True)
    result = runner(
        ["git", "clone", "--depth", "1", "--branch", ref, CONVERT_REPO_URL, str(repo)],
        timeout=CLONE_TIMEOUT,
    )
    if result.returncode != 0 or not cached.is_file():
        raise ServeError(
            f"获取 llama.cpp 源码失败（源码包与 git clone 均不可用，{format_returncode(result.returncode)}）："
            f"{_tail(result.stderr or result.stdout) or '无输出'}；"
            f"可手动 clone {CONVERT_REPO_URL} 后用 --script 指定 convert_hf_to_gguf.py"
        )
    return cached


def plan_convert(  # noqa: PLR0913, PLR0917
    source: Path,
    output: Path | None = None,
    outtype: str = "f16",
    quant: str | None = None,
    script: Path | None = None,
    python: str | None = None,
    models_root: Path | None = None,
    tag: str | None = None,
    home: Path | None = None,
) -> ConvertPlan:
    """构造转换计划。

    默认输出到模型目录下的 ``converted`` 子目录：
    ``<模型目录>/converted/<源目录名>/<源目录名>[-<量化>].gguf``。
    """
    src = source.expanduser()
    if not is_hf_safetensors_dir(src):
        raise ServeError(f"不是 HF safetensors 目录（需要 config.json 与 *.safetensors）: {src}")
    if outtype not in CONVERT_OUTTYPES:
        raise ServeError(f"不支持的 outtype {outtype!r}：可选 {', '.join(CONVERT_OUTTYPES)}")
    resolved_tag = tag or llmengine.current_engine_tag(llmenv.engines_root(home)) or DEFAULT_ENGINE_TAG
    repo = (
        script.expanduser().parent
        if script is not None and script.suffix.lower() == ".py"
        else convert_source_root(resolved_tag, home)
    )
    check_convert_source(src, repo, tag=resolved_tag)

    if output is None:
        root = llmenv.products_dir(models_root, home)
        suffix = f"-{quant}" if quant else ""
        target = root / src.name / f"{src.name}{suffix}.gguf"
    else:
        target = output.expanduser()
    if target.suffix.lower() != ".gguf":
        raise ServeError(f"输出文件必须是 .gguf: {target}")

    quant_exe: Path | None = None
    if quant:
        # 指定 --script 时仍需要引擎解析量化器；失败时给出更明确的提示
        try:
            quant_exe = llmengine.quantize_exe(llmengine.resolve_engine(tag, home))
        except ServeError as e:
            if script is not None:
                raise ServeError(
                    "指定量化时无法解析引擎：安装官方 llama.cpp release 引擎，或用 FCMD_LLAMA_QUANTIZE 指定，"
                    "或使用 --script 指定不依赖引擎的脚本"
                ) from e
            raise e
        if quant_exe is None:
            raise ServeError("未找到 llama-quantize：安装官方 llama.cpp release 引擎，或用 FCMD_LLAMA_QUANTIZE 指定")

    return ConvertPlan(
        source=src.resolve(),
        output=target.resolve(),
        outtype=outtype,
        script=ensure_convert_script(tag=tag, script=script, home=home),
        python=python or os.environ.get("FCMD_CONVERT_PYTHON") or sys.executable,
        quant=quant,
        quantize_exe=quant_exe,
    )


def _python_candidates() -> list[str]:
    """候选解释器：当前解释器 + PATH 上的 python（去重）。

    ``uv run`` 会把虚拟环境放到 PATH 首位，仅看 ``shutil.which`` 找不到系统 conda
    里的 torch，因此遍历 PATH 中的每个目录。
    """
    names = ("python.exe", "python3.exe") if os.name == "nt" else ("python3", "python")
    raw = [sys.executable, shutil.which("python"), shutil.which("python3")]
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        raw.extend(str(Path(entry) / name) for name in names if (Path(entry) / name).is_file())
    seen: set[str] = set()
    candidates: list[str] = []
    for candidate in raw:
        if not candidate:
            continue
        key = os.path.normcase(candidate)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)
    return candidates


def find_convert_python(runner: Runner = run_command) -> str:
    """找到一个已安装 ``torch`` / ``safetensors`` / ``numpy`` 的 Python 解释器。"""
    for candidate in _python_candidates():
        result = runner([candidate, "-c", "import torch, safetensors, numpy"], timeout=120.0)
        if result.returncode == 0:
            return candidate
    raise ServeError("未找到已安装 torch / safetensors / numpy 的 Python；请用 --python 指定")


def _tail(text: str, lines: int = 6) -> str:
    """取输出的最后若干行（用于错误摘要）。"""
    parts = [line.strip() for line in text.strip().splitlines() if line.strip()]
    return " | ".join(parts[-lines:])


def _is_stack_noise(line: str) -> bool:
    """是否为 ``~~~~^^`` 一类只指示位置的栈标记行。"""
    stripped = line.strip()
    return bool(stripped) and not stripped.strip("~^")


def root_error_line(text: str) -> str | None:
    """提取脚本输出里真正的异常行（如 ``ValueError: ...``），忽略栈标记片段。"""
    for raw in reversed(text.splitlines()):
        line = raw.strip()
        if not line or _is_stack_noise(line):
            continue
        if _ERROR_LINE_RE.match(line):
            return line
    return None


def _stack_frames(text: str, limit: int = 3) -> list[str]:
    """从输出中取末尾若干帧（``File "...", line N, in func``）。"""
    frames = [line.strip() for line in text.splitlines() if line.strip().startswith('File "')]
    return frames[-limit:]


def _convert_failure(result: llmbase.CommandResult, *, streamed: bool) -> str:
    """整理转换失败信息：真正异常行在前，栈只在无实时日志时精简附带。"""
    output = f"{result.stderr}\n{result.stdout}"
    hint = "；请用 --python 指定已安装 torch / safetensors / numpy 的解释器" if "ModuleNotFoundError" in output else ""
    reason = root_error_line(output)
    if reason is None:
        detail = _tail(output) or "无输出"
    elif streamed:
        detail = f"{reason}；完整调用栈见上方日志"
    else:
        frames = _stack_frames(output)
        detail = reason if not frames else f"{reason}；调用栈：{' <- '.join(frames)}"
    return f"safetensors 转 GGUF 失败（{format_returncode(result.returncode)}）：{detail}{hint}"


def _relay_output(line: str, sink: Callable[[str], None]) -> None:
    """实时日志回调：折叠 chat template，并丢弃 ``~~~~^^`` 一类栈标记行。"""
    if _is_stack_noise(line):
        return
    sink(summarize_convert_line(line))


def summarize_convert_line(line: str) -> str:
    """折叠转换脚本打印的整段 chat template 日志。

    ``convert_hf_to_gguf.py`` 会把模型 tokenizer 里的完整 Jinja 模板（可达数千字符）
    以 INFO 日志整行打出，在窗口日志里表现为一大片无法阅读的模板源码；这里替换为
    长度摘要，模板原文仍完整写入 GGUF 元数据。
    """
    marker = "Setting chat_template to "
    index = line.find(marker)
    if index < 0:
        return line
    template = line[index + len(marker) :]
    return f"{line[:index]}{marker}<已省略 {len(template)} 字符的 Jinja 模板，完整内容已写入 GGUF 元数据>"


def convert_model(
    plan: ConvertPlan,
    runner: Runner = run_command,
    on_output: Callable[[str], None] | None = None,
) -> list[Path]:
    """执行转换（可选再量化），返回最终产出的 GGUF 列表。

    传入 ``on_output`` 时改为逐行读取转换脚本输出（兼容 tqdm 的 ``\\r`` 进度），
    可实时回调日志；否则沿用一次性捕获的 ``runner``。
    """
    plan.output.parent.mkdir(parents=True, exist_ok=True)
    if on_output is None:
        result = runner(plan.convert_argv(), timeout=CONVERT_TIMEOUT)
    else:
        try:
            result = llmbase.stream_command(
                plan.convert_argv(),
                timeout=CONVERT_TIMEOUT,
                on_output=lambda line: _relay_output(line, on_output),
            )
        except subprocess.TimeoutExpired as exc:
            raise ServeError(f"safetensors 转 GGUF 超时（{CONVERT_TIMEOUT:.0f}s）") from exc
        except OSError as exc:
            raise ServeError(f"无法启动转换脚本: {exc}") from exc
    if result.returncode != 0:
        raise ServeError(_convert_failure(result, streamed=on_output is not None))
    if plan.quant is None:
        return [plan.output]

    if not Path(plan.intermediate).is_file():
        raise ServeError(f"转换命令已结束但未生成中间文件: {plan.intermediate}")
    result = runner(plan.quantize_argv(), timeout=QUANTIZE_TIMEOUT)
    if result.returncode != 0:
        output = _tail(result.stderr or result.stdout) or "无输出"
        hint = ""
        crashed = result.returncode in (_ACCESS_VIOLATION, _ACCESS_VIOLATION - 2**32)
        if crashed and output == "无输出":
            exe_dir = plan.quantize_exe.parent if plan.quantize_exe else Path()
            hint = f"；{MSVC_HINT} {exe_dir}"
        raise ServeError(f"量化失败（{format_returncode(result.returncode)}）：{output}{hint}")
    if not plan.output.is_file():
        raise ServeError(f"量化命令已结束但未生成: {plan.output}")
    with contextlib.suppress(OSError):
        Path(plan.intermediate).unlink()
    return [plan.output]


@fcmd.tool("llmconvert", help="HF safetensors 目录转 GGUF（可复用本地源码 zip、可选再量化）")
def convert_cmd(  # noqa: PLR0913, PLR0917
    source: Path,
    output: Path | None = None,
    outtype: str = "f16",
    quant: str | None = None,
    script: Path | None = None,
    python: str | None = None,
    models_root: Path | None = None,
    tag: str | None = None,
) -> None:
    """把 HuggingFace safetensors 目录转换为 GGUF，供 llama.cpp 加载。

    Parameters
    ----------
    source:
        含 ``config.json`` 与 ``*.safetensors`` 的模型目录。
    output:
        输出 GGUF 路径；默认 ``<模型目录>/<源目录名>/<源目录名>[-<量化>].gguf``。
    outtype:
        转换精度（``f32``/``f16``/``bf16``/``q8_0``，默认 ``f16``）。
    quant:
        量化类型（如 ``Q4_K_M``）；需要 ``llama-quantize``。
    script:
        转换脚本 ``convert_hf_to_gguf.py`` 或本地 llama.cpp 源码 ``.zip``。
    python:
        运行转换脚本的解释器（需已安装 torch / safetensors / numpy）；缺省自动探测。
    models_root:
        默认输出位置使用的模型目录。
    tag:
        按指定引擎 tag 匹配 llama.cpp 源码版本；默认用 current 引擎。
    """
    plan = plan_convert(
        source,
        output=output,
        outtype=outtype,
        quant=quant,
        script=script,
        python=python or find_convert_python(),
        models_root=models_root,
        tag=tag,
    )
    table = Table(title="转换计划", box="ascii")
    table.add_column("项", style="cyan", no_wrap=True)
    table.add_column("值")
    table.add_row("源目录", str(plan.source))
    table.add_row("输出", str(plan.output))
    if plan.quant:
        table.add_row("中间文件", plan.intermediate)
    table.add_row("精度", plan.outtype)
    table.add_row("量化", plan.quant or "-")
    table.add_row("脚本", str(plan.script))
    table.add_row("解释器", plan.python)
    get_console().print(table)

    for path in convert_model(plan):
        get_console().print(f"已生成: {path}")


@fcmd.main("llmconvert")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行
