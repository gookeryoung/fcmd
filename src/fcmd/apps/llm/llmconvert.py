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
import threading
import time
import zipfile
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import fcmd
from fcmd.apps.llm import llmbase, llmengine, llmenv
from fcmd.apps.llm.llmbase import (
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
    "import_source_archive",
    "install_convert_deps",
    "is_hf_safetensors_dir",
    "missing_packages",
    "patched_vocab_size",
    "plan_convert",
    "resolve_convert_python",
    "root_error_line",
    "summarize_convert_line",
    "supported_architectures",
]

CONVERT_SCRIPT = "convert_hf_to_gguf.py"
CONVERT_REPO_URL = "https://github.com/ggml-org/llama.cpp"
CONVERT_OUTTYPES = ("f32", "f16", "bf16", "q8_0")
CONVERT_TIMEOUT = 6 * 3600.0
QUANTIZE_TIMEOUT = 2 * 3600.0
CLONE_TIMEOUT = 900.0
_SOURCE_ATTEMPTS = 5
_SOURCE_TIMEOUT = 30.0
_PYTHON_PROBE_TIMEOUT = 60.0
"""探测转换解释器的超时：坏掉的解释器（Windows Store 别名等）不能无限挂住。"""
CONVERT_PYTHON_ENV = "FCMD_CONVERT_PYTHON"
CONVERT_DEPS = ("torch", "safetensors", "numpy")
_CONVERT_IMPORT = f"import {', '.join(CONVERT_DEPS)}"
_MISSING_MODULE_RE = re.compile(r"No module named '([\w.]+)'")
_REQUIRES_PACKAGES_RE = re.compile(
    r"requires the following packages that were not found in your environment:\s*([^\n.]+)"
)
CONVERT_SAFE_DEPS = frozenset(
    {
        "einops",
        "huggingface-hub",
        "mistral-common",
        "numpy",
        "pillow",
        "protobuf",
        "pyyaml",
        "regex",
        "safetensors",
        "sentencepiece",
        "tiktoken",
        "tokenizers",
        "tqdm",
        "transformers",
    }
)
"""可自动补装的转换依赖：只收纯 Python 包。

``torch`` / ``numpy`` 之外的二进制 / GPU 依赖不进白名单：自动降级或重装 torch 会破坏
用户已有的 CUDA 环境（llama.cpp 的 requirements 钉的是 CPU 轮子）。白名单外的缺失模块
只给出带解释器路径的安装提示。
"""
_PIP_NAMES = {"yaml": "pyyaml", "pil": "pillow", "mistral_common": "mistral-common"}
PIP_TIMEOUT = 900.0
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


def _needs_remote_tokenizer_code(source: Path) -> str | None:
    """模型的 tokenizer 是否只能靠 transformers ``trust_remote_code`` 加载。

    GLM-4-9B 这类仓库把分词器实现放在 ``tokenization_chatglm.py``，并在
    ``tokenizer_config.json`` 的 ``auto_map`` 里声明；llama.cpp 的
    ``get_vocab_base`` 调 ``AutoTokenizer.from_pretrained`` 时不传 ``trust_remote_code``，
    于是直接 ValueError。返回声明的类名（用于提示），不构成该情况时返回 ``None``。
    """
    try:
        tokenizer_config = json.loads((source / "tokenizer_config.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(tokenizer_config, dict):
        return None
    auto_map = tokenizer_config.get("auto_map")
    if not isinstance(auto_map, dict) or "AutoTokenizer" not in auto_map:
        return None
    if (source / "tokenizer.json").is_file():
        return None
    if (source / "vocab.json").is_file() and (source / "merges.txt").is_file():
        return None
    value = auto_map.get("AutoTokenizer")
    first = value[0] if isinstance(value, list) and value else value
    return str(first) if first else "tokenizer_config.json 的 auto_map"


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
    remote_tokenizer = _needs_remote_tokenizer_code(target)
    if remote_tokenizer is not None:
        raise ServeError(
            f"模型的 tokenizer 依赖 transformers trust_remote_code（{remote_tokenizer}），"
            "llama.cpp 转换脚本的 get_vocab_base 不传该参数，无法转换："
            f"{target}；建议直接下载官方 GGUF，或用 --script / 「转换脚本」指定已适配的脚本"
        )

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
            f"请升级引擎（fcmd llmengine install --tag <新版>）或换用已支持架构的模型；"
            f"若 ModelScope 已有同名 -GGUF 仓库（如 WeMM-Video-9B-GGUF），建议直接下载 GGUF 而不是转换"
        )


def convert_source_root(tag: str, home: Path | None = None) -> Path:
    """llama.cpp 源码目录（按引擎 tag 隔离，内含转换脚本与 ``gguf-py``）。"""
    return llmenv.engines_root(home) / tag / "llama.cpp"


def _source_archive_urls(ref: str) -> list[str]:
    """llama.cpp 源码包地址：国内 GitHub 镜像优先，官方 codeload / archive 兜底。

    单流 zip 比 ``git clone`` 更耐受网络抖动，且官方 release 引擎包只带二进制，
    转换脚本必须另行获取源码。镜像规则与引擎下载共用
    :func:`fcmd.apps.llm.llmengine.mirror_candidates`（``FCMD_GITHUB_MIRROR``
    可覆盖，设为 ``direct`` 时只走直连）。
    """
    path = f"refs/tags/{ref}" if _BUILD_TAG_RE.fullmatch(ref) else "refs/heads/master"
    # github.com 地址能套 gh-proxy 镜像；codeload.github.com 不在镜像主机表里，
    # 所以先排镜像 archive，再排 codeload 直连。
    archive = f"https://github.com/ggml-org/llama.cpp/archive/{path}.zip"
    codeload = f"https://codeload.github.com/ggml-org/llama.cpp/zip/{path}"
    return [*llmengine.mirror_candidates(archive), codeload]


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


def _download_resumable(
    url: str,
    dest: Path,
    opener: Opener | None = None,
    *,
    attempts: int | None = None,
    cancel: threading.Event | None = None,
) -> int:
    """带 Range 续传与重试的下载，专用于体积较大的源码包。

    GitHub 在部分网络下会在传输中重置连接；这里把未完成数据留在 ``.part``，
    下次尝试用 ``Range`` 从断点续传，避免每次都从零开始。
    """
    from urllib.request import Request, urlopen

    part = dest.with_name(dest.name + ".part")
    part.parent.mkdir(parents=True, exist_ok=True)
    open_fn = opener or (lambda request, timeout: urlopen(request, timeout=timeout))
    last_error: OSError | None = None
    limit = _SOURCE_ATTEMPTS if attempts is None else max(1, attempts)
    for attempt in range(limit):
        if cancel is not None and cancel.is_set():
            raise ServeError("已取消转换")
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
                        if cancel is not None and cancel.is_set():
                            raise ServeError("已取消转换")
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
    raise ServeError(f"下载失败: {url}（重试 {limit} 次；{last_error or '未知错误'}）")


def _download_convert_source(
    ref: str,
    repo: Path,
    opener: Opener | None,
    *,
    on_status: Callable[[str], None] | None = None,
    cancel: threading.Event | None = None,
) -> bool:
    """下载并解压源码包；地址不可用时逐个尝试，全部失败返回 ``False``。"""
    archive = repo.parent / f"{repo.name}-{ref}.zip"
    urls = _source_archive_urls(ref)
    # 镜像候选可能有 6–8 条：每条都重试 5 次会让「准备阶段」看起来卡死。
    attempts = _SOURCE_ATTEMPTS if len(urls) <= 2 else 2
    for index, url in enumerate(urls, start=1):
        if cancel is not None and cancel.is_set():
            raise ServeError("已取消转换")
        if on_status is not None:
            on_status(f"正在下载 llama.cpp 源码包（{index}/{len(urls)}）…")
        try:
            _download_resumable(url, archive, opener, attempts=attempts, cancel=cancel)
        except ServeError:
            archive.unlink(missing_ok=True)
            if cancel is not None and cancel.is_set():
                raise
            continue
        extracted = zipfile.is_zipfile(archive) and _extract_source_archive(archive, repo)
        archive.unlink(missing_ok=True)
        if extracted:
            return True
    return False


def import_source_archive(archive: Path, home: Path | None = None, *, tag: str | None = None) -> Path:
    """把本地 llama.cpp 源码 zip 复制到引擎根，作为转换源码缓存。

    文件名保持 ``llama.cpp*.zip`` 前缀，:func:`ensure_convert_script` 在任意引擎
    tag 下都能从引擎根发现并解压复用；同名文件存在时追加时间戳，不覆盖旧源码包。
    """
    source = Path(archive).expanduser()
    if not source.is_file():
        raise ServeError(f"源码包不存在: {source}")
    if not _source_archive_ready(source):
        raise ServeError(f"不是可用的 llama.cpp 源码包（缺少 {CONVERT_SCRIPT}）: {source}")
    root = llmenv.engines_root(home)
    root.mkdir(parents=True, exist_ok=True)
    raw_tag = re.sub(r"[^A-Za-z0-9._-]+", "-", (tag or source.stem).strip()) or "source"
    if raw_tag.casefold().startswith("llama.cpp"):
        raw_tag = "source"
    dest = root / f"llama.cpp-{raw_tag}.zip"
    if dest.exists():
        dest = root / f"llama.cpp-{raw_tag}-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    try:
        shutil.copy2(source, dest)
    except OSError as exc:
        raise ServeError(f"复制源码包失败: {exc}") from exc
    return dest


def ensure_convert_script(  # noqa: PLR0912, PLR0913, PLR0917 - 兼容旧调用方，参数保持扁平
    tag: str | None = None,
    script: Path | None = None,
    home: Path | None = None,
    runner: Runner = run_command,
    opener: Opener | None = None,
    on_status: Callable[[str], None] | None = None,
    cancel: threading.Event | None = None,
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

    if cancel is not None and cancel.is_set():
        raise ServeError("已取消转换")
    if on_status is not None:
        on_status("未找到本地 llama.cpp 源码，首次转换需要获取（下载源码包或 clone）…")
    ref = resolved if _BUILD_TAG_RE.fullmatch(resolved) else "master"
    repo = cached.parent
    repo.parent.mkdir(parents=True, exist_ok=True)
    for archive in _local_source_archives(ref, repo):
        if _source_archive_ready(archive) and _extract_source_archive(archive, repo):
            return cached
    if _download_convert_source(ref, repo, opener, on_status=on_status, cancel=cancel):
        return cached

    if cancel is not None and cancel.is_set():
        raise ServeError("已取消转换")
    shutil.rmtree(repo, ignore_errors=True)
    if on_status is not None:
        on_status("源码包不可用，正在 git clone llama.cpp 源码（较慢，请稍候）…")
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


def supported_architectures(
    script: Path | None = None,
    *,
    tag: str | None = None,
    home: Path | None = None,
) -> tuple[str, ...]:
    """当前转换脚本 / 源码能识别的模型架构列表（按字母排序）。

    用于设置页「查看支持的架构」：配置了 ``.py`` 脚本时读其所在源码的
    ``conversion/`` 包；留空或源码包尚未解压时读当前引擎 tag 的已缓存源码。
    源码未获取、脚本不支持读取（如未解压的 zip）时返回空元组。
    """
    repo: Path | None = None
    if script is not None:
        target = Path(script).expanduser()
        if target.suffix.lower() == ".py" and (target.parent / "conversion").is_dir():
            repo = target.parent
        elif target.is_dir() and (target / "conversion").is_dir():
            repo = target
    if repo is None:
        root = convert_source_root(
            tag or llmengine.current_engine_tag(llmenv.engines_root(home)) or DEFAULT_ENGINE_TAG,
            home,
        )
        if (root / "conversion").is_dir():
            repo = root
    if repo is None:
        return ()
    registered, _mapping = _engine_architectures(repo)
    return tuple(sorted(registered))


def plan_convert(  # noqa: PLR0912, PLR0913, PLR0917
    source: Path,
    output: Path | None = None,
    outtype: str = "f16",
    quant: str | None = None,
    script: Path | None = None,
    python: str | None = None,
    models_root: Path | None = None,
    tag: str | None = None,
    home: Path | None = None,
    on_status: Callable[[str], None] | None = None,
    cancel: threading.Event | None = None,
) -> ConvertPlan:
    """构造转换计划。

    默认输出到模型目录下的 ``converted`` 子目录：
    ``<模型目录>/converted/<源目录名>/<源目录名>[-<量化>].gguf``。
    """
    if cancel is not None and cancel.is_set():
        raise ServeError("已取消转换")
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
    if on_status is not None:
        on_status("正在检查模型目录、令牌器与引擎架构支持…")
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

    resolved_script = ensure_convert_script(tag=tag, script=script, home=home, on_status=on_status, cancel=cancel)
    # 源码是刚下载 / 解压出来的：用真实转换脚本再检查一次架构支持。
    # 首次转换前 repo 目录可能尚不存在，前置检查拿不到注册表，不能省这一步。
    if (resolved_script.parent / "conversion").is_dir():
        if on_status is not None:
            on_status("正在检查 llama.cpp 转换脚本是否支持该模型架构…")
        check_convert_source(src, resolved_script.parent, tag=resolved_tag)
    return ConvertPlan(
        source=src.resolve(),
        output=target.resolve(),
        outtype=outtype,
        script=resolved_script,
        python=resolve_convert_python(python),
        quant=quant,
        quantize_exe=quant_exe,
    )


def resolve_convert_python(python: str | None = None) -> str:
    """转换脚本使用的解释器：显式参数 > ``FCMD_CONVERT_PYTHON`` > 本应用解释器。

    默认就是运行 fcmd 自身的解释器（``sys.executable``），不再按 PATH 随便挑一个：
    转换脚本与本应用共用同一套站点包与 DLL，混用其它 python 容易因 torch / CUDA
    版本不一致而失败。
    """
    explicit = (python or "").strip()
    if explicit:
        return explicit
    override = os.environ.get(CONVERT_PYTHON_ENV, "").strip()
    return override or sys.executable


def _same_python(left: str, right: str) -> bool:
    """两个解释器路径是否指向同一个文件（Windows 下大小写不敏感）。"""
    return os.path.normcase(str(Path(left).resolve())) == os.path.normcase(str(Path(right).resolve()))


def _missing_modules(text: str) -> list[str]:
    """从探测输出里解析缺失的模块名，按转换依赖顺序返回。"""
    found = {match.group(1).split(".")[0] for match in _MISSING_MODULE_RE.finditer(text)}
    return [name for name in CONVERT_DEPS if name in found] or sorted(found)


def missing_packages(text: str) -> list[str]:
    """从转换脚本输出中解析缺失的依赖（去重、保持出现顺序）。

    兼容两种报错：直白的 ``ModuleNotFoundError: No module named 'x'``，以及
    transformers 加载模型自带建模代码时的
    ``This modeling file requires the following packages that were not found in your
    environment: tiktoken``（GLM-4 等带 ``auto_map`` 的模型就会这样）。
    """
    names: list[str] = []
    for match in _MISSING_MODULE_RE.finditer(text):
        names.append(match.group(1).split(".")[0])
    for match in _REQUIRES_PACKAGES_RE.finditer(text):
        names.extend(part for part in re.split(r"[,\s]+", match.group(1)) if part)
    seen: set[str] = set()
    ordered: list[str] = []
    for name in names:
        key = name.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        ordered.append(key)
    return ordered


def _pip_names(modules: Sequence[str]) -> list[str]:
    """模块名 → pip 包名（``yaml`` → ``pyyaml`` 这类差异单独映射）。"""
    return [_PIP_NAMES.get(name, name.replace("_", "-")) for name in modules]


@contextlib.contextmanager
def patched_vocab_size(source: Path) -> Iterator[bool]:
    """临时给 ``config.json`` 补上缺失的 ``vocab_size``，退出时恢复原文。

    llama.cpp 的 chatglm 转换实现写成
    ``hparams.get("padded_vocab_size", hparams["vocab_size"])``，默认参数会被提前求值，
    GLM-4-9B 这种只有 ``padded_vocab_size`` 的 config 因此直接 ``KeyError: 'vocab_size'``
    （上游 bug，不随解释器变化）。这里在转换期间补一个等值的 ``vocab_size``，
    无论成功失败都会把 config.json 恢复成原始文本。

    ``yield`` 出是否真的做了修补。
    """
    config = source / "config.json"
    try:
        original = config.read_text(encoding="utf-8")
        data = json.loads(original)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        yield False
        return
    if not isinstance(data, dict) or "vocab_size" in data or "padded_vocab_size" not in data:
        yield False
        return
    data["vocab_size"] = data["padded_vocab_size"]
    try:
        config.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        yield False
        return
    try:
        yield True
    finally:
        with contextlib.suppress(OSError):
            config.write_text(original, encoding="utf-8")


def install_convert_deps(
    python: str,
    modules: Sequence[str],
    runner: Runner = run_command,
    *,
    on_status: Callable[[str], None] | None = None,
    cancel: threading.Event | None = None,
) -> bool:
    """给转换解释器补装缺失依赖，返回是否安装成功。

    只处理 :data:`CONVERT_SAFE_DEPS` 白名单内的包：模型自带建模代码（GLM-4、Mistral
    等使用 ``auto_map`` 的仓库）会临时要求 ``tiktoken`` / ``transformers`` 这类纯 Python
    包，它们的缺失与解释器选择无关，重跑一次就能成功；torch 一类的 GPU 依赖不自动
    安装，否则会覆盖用户现有的 CUDA 构建。
    """
    names = _pip_names(modules)
    if not names or any(name not in CONVERT_SAFE_DEPS for name in names):
        return False
    if cancel is not None and cancel.is_set():
        return False
    if on_status is not None:
        on_status(f"转换脚本缺少依赖：{'、'.join(names)}，正在为解释器安装：{python}")
    result = runner([python, "-m", "pip", "install", *names], timeout=PIP_TIMEOUT)
    if result.returncode != 0:
        if on_status is not None:
            on_status(f"依赖安装失败：{_tail(result.stderr or result.stdout) or format_returncode(result.returncode)}")
        return False
    if on_status is not None:
        on_status("依赖安装完成，重新开始转换")
    return True


def _python_candidates() -> list[str]:
    """候选解释器：本应用解释器 + PATH 上的 python（去重）。

    本应用解释器永远排第一，只有它缺依赖时才回退到其它候选。
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


def find_convert_python(
    runner: Runner = run_command,
    *,
    python: str | None = None,
    on_status: Callable[[str], None] | None = None,
    cancel: threading.Event | None = None,
) -> str:
    """确定转换脚本的解释器：优先本应用解释器，缺依赖时才回退其它候选。

    本应用解释器（``sys.executable``）与转换脚本共用同一套站点包与 DLL，混用 PATH
    上找到的其它 python 容易因 torch / CUDA 版本不一致而失败，因此它排在第一且一旦
    可用就直接返回；只有它缺少 ``torch`` / ``safetensors`` / ``numpy`` 时才继续探测
    PATH 上的候选，并在状态行写明改用了哪个解释器。

    传入 ``python``（设置页 / ``--python`` 显式指定）时只探测它，避免路径写错后拖到
    转换阶段才报一个看不懂的 ``WinError``。每次探测都带超时（坏掉的解释器 / Store
    别名不会挂住整个准备阶段），进度通过 ``on_status`` 回传给窗口。
    """
    explicit = (python or "").strip()
    app = sys.executable
    candidates = [explicit] if explicit else _python_candidates()
    app_result: llmbase.CommandResult | None = None
    last_text = ""
    last: str | None = None
    for candidate in candidates:
        if cancel is not None and cancel.is_set():
            raise ServeError("已取消转换")
        is_app = _same_python(candidate, app)
        label = "本应用解释器" if is_app else ("指定解释器" if explicit else "备用解释器")
        if on_status is not None:
            on_status(f"正在检测{label}：{candidate}")
        result = runner([candidate, "-c", _CONVERT_IMPORT], timeout=_PYTHON_PROBE_TIMEOUT)
        if result.returncode == 0:
            if not is_app and not explicit and on_status is not None:
                on_status(f"本应用解释器缺少 {' / '.join(CONVERT_DEPS)}，改用解释器：{candidate}")
            return candidate
        last, last_text = candidate, f"{result.stderr}\n{result.stdout}"
        if is_app:
            app_result = result
    missing = _missing_modules(last_text)
    detail = "、".join(missing) if missing else "、".join(CONVERT_DEPS)
    install = " ".join(missing or list(CONVERT_DEPS))
    if explicit:
        raise ServeError(
            f"指定的转换解释器不可用（{explicit or last}）：缺少 {detail} 或无法运行；"
            f'请安装依赖（"{explicit}" -m pip install {install}），或留空改回本应用解释器'
        )
    app_text = "" if app_result is None else f"{app_result.stderr}\n{app_result.stdout}"
    app_missing = _missing_modules(app_text)
    app_detail = "、".join(app_missing) if app_missing else detail
    app_install = " ".join(app_missing or missing or list(CONVERT_DEPS))
    raise ServeError(
        f"未找到可用的转换解释器：本应用解释器 {app} 缺少 {app_detail}，PATH 上的其它 Python 也不可用；"
        f'请安装依赖（"{app}" -m pip install {app_install}），'
        f"或用 --python / {CONVERT_PYTHON_ENV} 指定解释器"
    )


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


def _convert_failure(result: llmbase.CommandResult, *, streamed: bool, python: str | None = None) -> str:
    """整理转换失败信息：真正异常行在前，栈只在无实时日志时精简附带。"""
    output = f"{result.stderr}\n{result.stdout}"
    missing = _pip_names(missing_packages(output))
    if missing:
        where = python or sys.executable
        hint = (
            f'；转换解释器 {where} 缺少 {"、".join(missing)}："{where}" -m pip install {" ".join(missing)}，'
            f"或用 --python / {CONVERT_PYTHON_ENV} 指定已装好依赖的解释器"
        )
    elif "ModuleNotFoundError" in output:
        hint = f"；请用 --python / {CONVERT_PYTHON_ENV} 指定已安装 torch / safetensors / numpy 的解释器"
    else:
        hint = ""
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


def convert_model(  # noqa: PLR0912, PLR0913 - 取消 / 量化 / 失败分支集中在一处
    plan: ConvertPlan,
    runner: Runner = run_command,
    on_output: Callable[[str], None] | None = None,
    *,
    on_status: Callable[[str], None] | None = None,
    cancel: threading.Event | None = None,
    install_missing: bool = True,
    patch_config: bool = True,
) -> list[Path]:
    """执行转换（可选再量化），返回最终产出的 GGUF 列表。

    传入 ``on_output`` 时改为逐行读取转换脚本输出（兼容 tqdm 的 ``\\r`` 进度），
    可实时回调日志；否则沿用一次性捕获的 ``runner``。

    模型自带建模代码要求的纯 Python 依赖（``tiktoken`` / ``transformers`` 等）缺失时，
    ``install_missing`` 开启会自动装到转换解释器里并重跑一次，不再让用户对着
    ``ImportError`` 自己找命令；``patch_config`` 开启时，GLM-4 这类只有
    ``padded_vocab_size`` 的 config 会临时补上 ``vocab_size`` 再重跑（llama.cpp 的上游 bug）。
    """
    if cancel is not None and cancel.is_set():
        raise ServeError("已取消转换")
    plan.output.parent.mkdir(parents=True, exist_ok=True)
    if on_status is not None:
        on_status(f"正在启动转换脚本：{plan.python}")
    if on_output is None:
        result = runner(plan.convert_argv(), timeout=CONVERT_TIMEOUT)
    else:
        try:
            result = llmbase.stream_command(
                plan.convert_argv(),
                timeout=CONVERT_TIMEOUT,
                on_output=lambda line: _relay_output(line, on_output),
                cancel=cancel,
            )
        except subprocess.TimeoutExpired as exc:
            raise ServeError(f"safetensors 转 GGUF 超时（{CONVERT_TIMEOUT:.0f}s）") from exc
        except OSError as exc:
            raise ServeError(f"无法启动转换脚本: {exc}") from exc
    if cancel is not None and cancel.is_set():
        raise ServeError("已取消转换")
    if result.returncode != 0:
        output = f"{result.stderr}\n{result.stdout}"
        missed = missing_packages(output)
        if (
            install_missing
            and missed
            and install_convert_deps(plan.python, missed, runner, on_status=on_status, cancel=cancel)
        ):
            return convert_model(
                plan,
                runner,
                on_output,
                on_status=on_status,
                cancel=cancel,
                install_missing=False,
                patch_config=patch_config,
            )
        if patch_config and "KeyError: 'vocab_size'" in output:
            with patched_vocab_size(plan.source) as patched:
                if patched:
                    if on_status is not None:
                        on_status(
                            "模型 config.json 缺少 vocab_size，已临时补齐（llama.cpp 脚本的上游 bug），转换完成后恢复"
                        )
                    return convert_model(
                        plan,
                        runner,
                        on_output,
                        on_status=on_status,
                        cancel=cancel,
                        install_missing=False,
                        patch_config=False,
                    )
        raise ServeError(_convert_failure(result, streamed=on_output is not None, python=plan.python))
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
        运行转换脚本的解释器（需已安装 torch / safetensors / numpy）；缺省用运行 fcmd
        的本应用解释器，它缺依赖时才回退 PATH 上的其它 Python。
    models_root:
        默认输出位置使用的模型目录。
    tag:
        按指定引擎 tag 匹配 llama.cpp 源码版本；默认用 current 引擎。
    """
    console = get_console()

    def report(text: str) -> None:
        console.print(text)

    plan = plan_convert(
        source,
        output=output,
        outtype=outtype,
        quant=quant,
        script=script,
        python=find_convert_python(python=python, on_status=report),
        models_root=models_root,
        tag=tag,
        on_status=report,
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
    console.print(table)

    for path in convert_model(plan, on_status=report):
        console.print(f"已生成: {path}")


@fcmd.main("llmconvert")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行
