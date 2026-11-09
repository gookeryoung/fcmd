"""servetool - 本地大模型推理服务编排（llama.cpp / OpenAI 兼容）。

把「下载模型 → 装引擎 → 起服务 → 接入客户端」收成一条命令链，
供 dsh / pi 等 OpenAI 兼容编码 Agent 使用。

子命令
------
* ``doctor`` —— 环境体检（GPU / CUDA / 引擎 / 模型 / 端口 / 磁盘 / 显存预算）
* ``engine`` —— 引擎安装、注册、列表与切换（``install``/``list``/``use``）
* ``config`` —— 模型目录配置（``show``/``add``/``remove``/``reset``）
* ``model`` —— 扫描模型目录，列出 GGUF 与各档位选中的文件
* ``convert`` —— HF safetensors 目录转 GGUF（可选 llama-quantize 量化）
* ``plan`` —— 打印启动计划（命令 + 显存估算），不真正启动
* ``start`` / ``stop`` / ``status`` / ``logs`` —— 服务生命周期
* ``bench`` —— 对运行中的服务实测延迟与 token 吞吐
* ``env`` —— 输出 dsh / pi 需要的 ``OPENAI_*`` 环境变量

示例
----
    fcmd srv doctor
    fcmd srv engine install --tag b12211
    fcmd srv start --profile balanced
    fcmd srv env --shell powershell
    fcmd srv bench
    fcmd srv stop
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import fcmd
from fcmd.console import Table, get_console
from fcmd.tools.ai import serve_core as core
from fcmd.tools.ai.serve_core import (
    DEFAULT_ALIAS,
    DEFAULT_API_KEY,
    DEFAULT_ENGINE_TAG,
    DEFAULT_HOST,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_PROFILE,
    DEFAULT_THREADS,
    PROFILES,
    LaunchPlan,
    ProfileName,
    ServeError,
    ServeState,
)

__all__ = ["main"]

__tool_aliases__ = ["srv"]

_ENGINE_ACTION = Literal["install", "list", "use"]
_CONFIG_ACTION = Literal["show", "add", "remove", "reset"]
_ENV_SHELL = Literal["plain", "powershell", "bash"]
_STATUS_TEXT = {"ok": "[green]ok[/green]", "warn": "[yellow]warn[/yellow]", "fail": "[red]fail[/red]"}


def _print_plan(plan: LaunchPlan) -> None:
    """打印启动计划与显存估算。"""
    estimate = core.plan_vram(plan)
    table = Table(title=f"启动计划: {plan.name}", box="ascii")
    table.add_column("项目", style="cyan", no_wrap=True)
    table.add_column("值")
    table.add_row("引擎", f"{plan.engine.exe}（{plan.engine.tag}）")
    table.add_row("模型", str(plan.model))
    table.add_row("档位", f"{plan.profile.name} / {plan.profile.quant} / ctx {plan.context} / ub {plan.profile.ubatch}")
    table.add_row("监听", core.http_base_url(plan.host, plan.port))
    table.add_row("模型别名", plan.alias)
    table.add_row("显存估算", core.human_size(estimate))
    table.add_row("日志", str(plan.log))
    get_console().print(table)
    get_console().print("命令: " + core.format_command(plan.argv()))

    gpu = core.detect_gpu()
    if gpu is not None and estimate > gpu.memory_mib * 1024 * 1024:
        get_console().print(
            f"[yellow]警告: 估算 {core.human_size(estimate)} 超过显存 {gpu.memory_mib} MiB，"
            "可能 OOM；可改用 --profile speed 或减小 --ctx[/yellow]"
        )


def _print_state(state: ServeState, status: str) -> None:
    """打印单个服务详情。"""
    table = Table(title=f"服务: {state.name}", box="ascii")
    table.add_column("项目", style="cyan", no_wrap=True)
    table.add_column("值")
    table.add_row("状态", status)
    table.add_row("地址", state.base_url)
    table.add_row("模型", state.model)
    table.add_row("档位", f"{state.profile} / ctx {state.context}")
    table.add_row("引擎", state.engine)
    table.add_row("PID", str(state.pid))
    table.add_row("启动时间", state.started_at)
    table.add_row("日志", state.log)
    get_console().print(table)


def _state_status(state: ServeState) -> str:
    if not core.process_alive(state.pid, state.create_time):
        return "[red]已退出[/red]"
    if core.check_health(state.host, state.port, state.api_key):
        return "[green]运行中[/green]"
    return "[yellow]启动中/无响应[/yellow]"


@fcmd.tool(
    "servetool",
    subcommand="doctor",
    help="环境体检（GPU / CUDA / 引擎 / 模型 / 端口 / 磁盘）",
    description="本地大模型推理服务：引擎/模型/进程/健康检查/接入 OpenAI 兼容客户端",
)
def doctor_cmd(
    port: int = DEFAULT_PORT,
    engine_tag: str | None = None,
    models_root: Path | None = None,
) -> None:
    """运行环境体检。

    Parameters
    ----------
    port:
        计划使用的服务端口（默认 8080）。
    engine_tag:
        只体检指定引擎 tag；默认按 current / 最新安装解析。
    models_root:
        只体检该目录；默认用 ``FCMD_MODELS``、配置文件或内置默认目录。
    """
    checks = core.doctor(port=port, engine_tag=engine_tag, models_root=models_root)
    table = Table(title="servetool 体检", box="ascii")
    table.add_column("检查项", style="cyan", no_wrap=True)
    table.add_column("状态", justify="center", no_wrap=True)
    table.add_column("详情")
    for check in checks:
        table.add_row(check.name, _STATUS_TEXT[check.status], check.detail)
    get_console().print(table)

    failures = [check for check in checks if check.status == "fail"]
    if failures:
        raise ServeError(f"{len(failures)} 项检查失败：" + "；".join(f"{c.name}: {c.detail}" for c in failures))


@fcmd.tool(
    "servetool",
    subcommand="engine",
    help="引擎安装/注册/列表/切换（install/list/use）",
    description="本地大模型推理服务：引擎/模型/进程/健康检查/接入 OpenAI 兼容客户端",
)
def engine_cmd(
    action: _ENGINE_ACTION,
    tag: str | None = None,
    path: Path | None = None,
    variant: Literal["cuda", "cpu"] = "cuda",
    url: str | None = None,
) -> None:
    """管理 llama.cpp 引擎。

    Parameters
    ----------
    action:
        ``install`` 安装/注册；``list`` 列出；``use`` 设为默认。
    tag:
        引擎 tag，如 ``b12211``；``install`` 默认用内置推荐版本。
    path:
        ``install`` 时注册本地已编译的引擎目录（不复制文件）。
    variant:
        ``install`` 下载的官方包类型：``cuda``（含 cudart）或 ``cpu``。
    url:
        ``install`` 时直接指定 zip 资产地址（非 Windows 或自定义构建用）。
    """
    if action == "list":
        infos = core.list_engines()
        if not infos:
            get_console().print("未安装任何引擎：fcmd srv engine install")
            return
        table = Table(title="已安装引擎", box="ascii")
        table.add_column("tag", style="cyan", no_wrap=True)
        table.add_column("可执行文件")
        table.add_column("状态", justify="center", no_wrap=True)
        for info in infos:
            mark = "[green]当前[/green]" if info.source == "current" else "-"
            table.add_row(info.tag, str(info.exe), mark)
        get_console().print(table)
        return

    if not tag:
        raise ServeError(f"请用 --tag 指定引擎版本，推荐 {DEFAULT_ENGINE_TAG}")

    if action == "use":
        info = core.use_engine(tag)
        get_console().print(f"已切换默认引擎: {info.tag} -> {info.exe}")
        return

    if path is not None:
        get_console().print(f"注册本地引擎 {tag}: {path}")
    else:
        get_console().print(f"安装引擎 {tag}（variant={variant}），下载可能耗时数分钟…")
    info = core.install_engine(tag, variant=variant, url=url, path=path)
    get_console().print(f"引擎就绪: {info.tag} -> {info.exe}")
    if not any(item.source == "current" for item in core.list_engines()):
        core.use_engine(tag)
        get_console().print(f"已设为默认引擎: {tag}")


@fcmd.tool(
    "servetool",
    subcommand="config",
    help="模型目录配置（show/add/remove/reset）",
    description="本地大模型推理服务：引擎/模型/进程/健康检查/接入 OpenAI 兼容客户端",
)
def config_cmd(action: _CONFIG_ACTION, path: Path | None = None) -> None:
    """查看或修改持久化的模型目录列表。

    Parameters
    ----------
    action:
        ``show`` 查看；``add`` 新增；``remove`` 移除；``reset`` 清空回到默认。
    path:
        ``add`` / ``remove`` 时的模型目录。
    """
    if action in ("add", "remove"):
        if path is None:
            raise ServeError(f"action={action} 需要用 --path 指定模型目录")
        target = path.expanduser()
        if action == "add":
            core.add_models_root(target)
            get_console().print(f"已登记模型目录: {target}")
            if not target.is_dir():
                get_console().print(f"[yellow]提示: 目录当前不存在 {target}[/yellow]")
        else:
            core.remove_models_root(target)
            get_console().print(f"已移除模型目录: {target}")
    elif action == "reset":
        core.reset_models_roots()
        get_console().print("已清空模型目录配置，回到默认目录")
    _print_roots()


def _print_roots() -> None:
    """打印当前生效的模型目录、来源与 GGUF 数量。"""
    env = os.environ.get("FCMD_MODELS")
    if env:
        source = "FCMD_MODELS（覆盖配置文件）"
    elif core.configured_roots():
        source = f"配置文件 {core.config_path()}"
    else:
        source = "默认目录（未配置）"

    roots = core.models_roots()
    table = Table(title=f"模型目录（来源: {source}）", box="ascii")
    table.add_column("目录", style="cyan", no_wrap=True)
    table.add_column("状态", justify="center", no_wrap=True)
    table.add_column("GGUF", justify="right", no_wrap=True)
    for root in roots:
        exists = root.is_dir()
        count = len(core.scan_models([root])) if exists else 0
        table.add_row(str(root), "[green]存在[/green]" if exists else "[red]不存在[/red]", str(count))
    get_console().print(table)
    get_console().print("增删目录: fcmd srv config add --path D:/models（remove / reset 同理）")


@fcmd.tool(
    "servetool",
    subcommand="model",
    help="扫描模型目录，列出本地 GGUF 与匹配档位",
    description="本地大模型推理服务：引擎/模型/进程/健康检查/接入 OpenAI 兼容客户端",
)
def model_cmd(models_root: Path | None = None) -> None:
    """扫描所有已配置的模型目录，列出 GGUF 文件与被选中的档位。

    Parameters
    ----------
    models_root:
        只扫描指定目录；默认用 ``FCMD_MODELS``、配置文件或内置默认目录。
    """
    roots = core.models_roots(models_root)
    missing = [root for root in roots if not root.is_dir()]
    existing = [root for root in roots if root.is_dir()]
    if not existing:
        raise ServeError(f"模型目录不存在: {', '.join(str(root) for root in roots)}；先用 fcmd mdl download 下载 GGUF")

    entries = core.scan_models(existing)
    if not entries:
        raise ServeError(f"{', '.join(str(root) for root in existing)} 下没有 .gguf 文件；先用 fcmd mdl download 下载")

    table = Table(title=f"本地模型（{len(entries)} 个 / {len(existing)} 个目录）", box="ascii")
    table.add_column("目录", style="cyan", no_wrap=True)
    table.add_column("文件", no_wrap=True)
    table.add_column("大小", justify="right", no_wrap=True)
    table.add_column("匹配档位")
    for entry in entries:
        table.add_row(str(entry.root), entry.relative, core.human_size(entry.size), ", ".join(entry.profiles) or "-")
    get_console().print(table)

    get_console().print("各档位将选用的模型:")
    for name in PROFILES:
        quant = PROFILES[name].quant
        matched = [entry for entry in entries if quant.lower() in entry.path.name.lower()]
        if matched:
            chosen = max(matched, key=lambda entry: entry.size)
            get_console().print(f"  {name:<12} {quant:<7} {chosen.path}")
        else:
            get_console().print(f"  {name:<12} {quant:<7} [yellow]缺失[/yellow]")

    for root in missing:
        get_console().print(f"[yellow]提示: 配置中的目录不存在 {root}[/yellow]")


@fcmd.tool(
    "servetool",
    subcommand="convert",
    help="HF safetensors 目录转 GGUF（可选再量化）",
    description="本地大模型推理服务：引擎/模型/进程/健康检查/接入 OpenAI 兼容客户端",
)
def convert_cmd(  # noqa: PLR0913, PLR0917
    source: Path,
    output: Path | None = None,
    outtype: str = "f16",
    quant: str | None = None,
    script: Path | None = None,
    python: str | None = None,
    models_root: Path | None = None,
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
        量化类型（如 ``Q4_K_M``）；需要 ``llama-quantize``（官方 release 引擎自带）。
    script:
        ``convert_hf_to_gguf.py`` 路径；默认取引擎目录缓存，缺失时浅克隆 llama.cpp 源码。
    python:
        运行转换脚本的解释器（需已安装 torch / safetensors / numpy）；缺省自动探测。
    models_root:
        默认输出位置使用的模型目录。
    """
    plan = core.plan_convert(
        source,
        output=output,
        outtype=outtype,
        quant=quant,
        script=script,
        python=python or core.find_convert_python(),
        models_root=models_root,
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

    paths = core.convert_model(plan)
    for path in paths:
        get_console().print(f"已生成: {path}")
    get_console().print(f"启动服务: fcmd srv start --model {paths[-1]}")


@fcmd.tool(
    "servetool",
    subcommand="plan",
    help="打印启动计划（命令 + 显存估算），不启动",
    description="本地大模型推理服务：引擎/模型/进程/健康检查/接入 OpenAI 兼容客户端",
)
def plan_cmd(  # noqa: PLR0913, PLR0917
    name: str = DEFAULT_NAME,
    profile: ProfileName = DEFAULT_PROFILE,
    model: Path | None = None,
    models_root: Path | None = None,
    port: int = DEFAULT_PORT,
    host: str = DEFAULT_HOST,
    ctx: int | None = None,
    engine_tag: str | None = None,
    alias: str = DEFAULT_ALIAS,
    api_key: str = DEFAULT_API_KEY,
    threads: int = DEFAULT_THREADS,
) -> None:
    """打印启动计划，便于确认参数与显存占用。

    Parameters
    ----------
    name:
        服务名（决定状态与日志文件），默认 ``qwen``。
    profile:
        档位：``speed`` / ``balanced`` / ``long`` / ``max-context``。
    model:
        GGUF 文件或包含 GGUF 的目录；默认按档位量化在模型根目录查找。
    models_root:
        只在该目录查找；默认用 ``FCMD_MODELS``、配置文件或内置默认目录。
    port:
        监听端口（默认 8080）。
    host:
        监听地址（默认 127.0.0.1，只本机可访问）。
    ctx:
        覆盖档位上下文长度。
    engine_tag:
        指定引擎 tag，默认 current / 最新安装。
    alias:
        OpenAI 模型名（``model`` 字段），默认 ``qwen3-coder-30b-a3b``。
    api_key:
        本地 API Key，默认 ``sk-fcmd-local``。
    threads:
        CPU 线程数（默认 8）。
    """
    plan = core.make_plan(
        name,
        profile,
        model=model,
        models_root=models_root,
        port=port,
        host=host,
        ctx=ctx,
        engine_tag=engine_tag,
        alias=alias,
        api_key=api_key,
        threads=threads,
    )
    _print_plan(plan)


@fcmd.tool(
    "servetool",
    subcommand="start",
    help="启动推理服务（等待健康检查通过）",
    description="本地大模型推理服务：引擎/模型/进程/健康检查/接入 OpenAI 兼容客户端",
)
def start_cmd(  # noqa: PLR0913, PLR0917
    name: str = DEFAULT_NAME,
    profile: ProfileName = DEFAULT_PROFILE,
    model: Path | None = None,
    models_root: Path | None = None,
    port: int = DEFAULT_PORT,
    host: str = DEFAULT_HOST,
    ctx: int | None = None,
    engine_tag: str | None = None,
    alias: str = DEFAULT_ALIAS,
    api_key: str = DEFAULT_API_KEY,
    threads: int = DEFAULT_THREADS,
    wait: float = 180.0,
) -> None:
    """启动服务并等待 ``/health`` 就绪。

    Parameters
    ----------
    name:
        服务名（决定状态与日志文件），默认 ``qwen``。
    profile:
        档位：``speed`` / ``balanced`` / ``long`` / ``max-context``。
    model:
        GGUF 文件或包含 GGUF 的目录；默认按档位量化在模型根目录查找。
    models_root:
        只在该目录查找；默认用 ``FCMD_MODELS``、配置文件或内置默认目录。
    port:
        监听端口（默认 8080）。
    host:
        监听地址（默认 127.0.0.1，只本机可访问）。
    ctx:
        覆盖档位上下文长度。
    engine_tag:
        指定引擎 tag，默认 current / 最新安装。
    alias:
        OpenAI 模型名（``model`` 字段），默认 ``qwen3-coder-30b-a3b``。
    api_key:
        本地 API Key，默认 ``sk-fcmd-local``。
    threads:
        CPU 线程数（默认 8）。
    wait:
        等待模型加载与健康检查的秒数（默认 180）。
    """
    plan = core.make_plan(
        name,
        profile,
        model=model,
        models_root=models_root,
        port=port,
        host=host,
        ctx=ctx,
        engine_tag=engine_tag,
        alias=alias,
        api_key=api_key,
        threads=threads,
    )
    _print_plan(plan)
    state = core.start_server(plan, wait_s=wait)
    _print_state(state, "[green]运行中[/green]")
    get_console().print("接入 dsh / pi: fcmd srv env（--shell powershell|bash）")


@fcmd.tool(
    "servetool",
    subcommand="stop",
    help="停止推理服务并清理状态",
    description="本地大模型推理服务：引擎/模型/进程/健康检查/接入 OpenAI 兼容客户端",
)
def stop_cmd(name: str = DEFAULT_NAME) -> None:
    """停止服务（进程已退出时清理残留状态）。

    Parameters
    ----------
    name:
        服务名，默认 ``qwen``。
    """
    result = core.stop_server(name)
    if result.was_running:
        get_console().print(f"已停止服务 {name}（pid={result.state.pid}）")
    else:
        get_console().print(f"服务 {name} 未在运行，已清理残留状态")


@fcmd.tool(
    "servetool",
    subcommand="status",
    help="查看服务状态（不指定 name 时列出全部）",
    description="本地大模型推理服务：引擎/模型/进程/健康检查/接入 OpenAI 兼容客户端",
)
def status_cmd(name: str | None = None) -> None:
    """查看服务进程与健康状态。

    Parameters
    ----------
    name:
        服务名；省略时列出全部已登记服务。
    """
    if name:
        state = core.require_state(name)
        _print_state(state, _state_status(state))
        return

    states = core.list_states()
    if not states:
        get_console().print("没有已登记的服务：fcmd srv start")
        return
    table = Table(title="服务列表", box="ascii")
    table.add_column("服务", style="cyan", no_wrap=True)
    table.add_column("状态", justify="center", no_wrap=True)
    table.add_column("地址", no_wrap=True)
    table.add_column("档位", no_wrap=True)
    table.add_column("PID", justify="right", no_wrap=True)
    for state in states:
        table.add_row(
            state.name, _state_status(state), state.base_url, f"{state.profile}/ctx{state.context}", str(state.pid)
        )
    get_console().print(table)


@fcmd.tool(
    "servetool",
    subcommand="logs",
    help="查看服务日志末尾若干行",
    description="本地大模型推理服务：引擎/模型/进程/健康检查/接入 OpenAI 兼容客户端",
)
def logs_cmd(name: str = DEFAULT_NAME, lines: int = 50) -> None:
    """打印服务日志尾部。

    Parameters
    ----------
    name:
        服务名，默认 ``qwen``。
    lines:
        打印的行数（默认 50）。
    """
    get_console().print(core.read_log(name, lines), markup=False)


@fcmd.tool(
    "servetool",
    subcommand="bench",
    help="对运行中的服务实测延迟与 token 吞吐",
    description="本地大模型推理服务：引擎/模型/进程/健康检查/接入 OpenAI 兼容客户端",
)
def bench_cmd(
    name: str = DEFAULT_NAME,
    prompt: str = core.DEFAULT_PROMPT,
    max_tokens: int = 128,
    timeout: float = 180.0,
) -> None:
    """发一次补全请求，输出延迟与吞吐。

    Parameters
    ----------
    name:
        服务名，默认 ``qwen``。
    prompt:
        测试提示词。
    max_tokens:
        生成 token 上限（默认 128）。
    timeout:
        请求超时秒数（默认 180）。
    """
    state = core.require_state(name)
    if not core.process_alive(state.pid, state.create_time):
        raise ServeError(f"服务 {name!r} 未在运行：fcmd srv start")
    result = core.bench(state, prompt=prompt, max_tokens=max_tokens, timeout=timeout)
    table = Table(title=f"实测: {name}", box="ascii")
    table.add_column("指标", style="cyan", no_wrap=True)
    table.add_column("值", justify="right", no_wrap=True)
    table.add_row("总延迟", f"{result.latency_s:.2f} s")
    table.add_row("输入 tokens", str(result.prompt_tokens))
    table.add_row("输出 tokens", str(result.completion_tokens))
    table.add_row("输出吞吐", f"{result.tokens_per_s:.2f} tok/s")
    get_console().print(table)


@fcmd.tool(
    "servetool",
    subcommand="env",
    help="输出 dsh/pi 需要的 OPENAI_* 环境变量",
    description="本地大模型推理服务：引擎/模型/进程/健康检查/接入 OpenAI 兼容客户端",
)
def env_cmd(name: str = DEFAULT_NAME, shell: _ENV_SHELL = "plain") -> None:
    """按指定 shell 语法输出客户端环境变量。

    Parameters
    ----------
    name:
        服务名，默认 ``qwen``。
    shell:
        输出格式：``plain``（``KEY=VALUE``）/ ``powershell`` / ``bash``。
    """
    state = core.require_state(name)
    for key, value in core.client_env(state).items():
        if shell == "powershell":
            get_console().print(f'$env:{key}="{value}"', markup=False)
        elif shell == "bash":
            get_console().print(f'export {key}="{value}"', markup=False)
        else:
            get_console().print(f"{key}={value}", markup=False)


@fcmd.main("servetool")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
