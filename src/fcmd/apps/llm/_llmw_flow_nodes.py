"""内置工作流节点：文本输入、LLM 对话、sd.cpp 文生图、图片 / 文本保存。

节点只做「调用已有能力 + 类型转换」，不复制服务启动 / 模型扫描 / 下载逻辑；
LLM 与画图服务由运行页启动，节点在服务未就绪时给出可操作的错误。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fcmd.apps.llm import _llmpaint, _llmpaint_server, llmbase, llmchat, llmserve
from fcmd.apps.llm._llmw_flow_model import (
    FILES,
    IMAGES,
    TEXT,
    FlowContext,
    FlowError,
    NodeDefinition,
    ParamSpec,
    PortSpec,
)

__all__ = [
    "IMAGE_SAVE",
    "IMAGE_TXT2IMG",
    "LLM_CHAT",
    "TEXT_INPUT",
    "TEXT_SAVE",
    "register_builtin_nodes",
]

TEXT_INPUT = "text.input"
LLM_CHAT = "llm.chat"
IMAGE_TXT2IMG = "image.txt2img"
IMAGE_SAVE = "image.save"
TEXT_SAVE = "text.save"


def _text_param(params: Mapping[str, Any], name: str, default: str = "") -> str:
    value = params.get(name, default)
    return str(value) if value is not None else default


def _int_param(params: Mapping[str, Any], name: str, default: int) -> int:
    value = params.get(name, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        raise FlowError(f"参数 {name} 必须是整数: {value!r}") from None


def _float_param(params: Mapping[str, Any], name: str, default: float) -> float:
    value = params.get(name, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        raise FlowError(f"参数 {name} 必须是数字: {value!r}") from None


def _bool_param(params: Mapping[str, Any], name: str, default: bool) -> bool:
    value = params.get(name, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _ensure_paint_service() -> tuple[str, int]:
    """按运行页「图像」配置启动 / 复用 sd-server，返回实际 host / port。

    只在 GUI 环境可用：配置来自 ``ui/llmw.json`` 的 ``paint`` 字段，启动逻辑复用
    运行页同一套 :func:`start_paint_service`，因此自动配对的 VAE / 文本编码器会一起生效。
    """
    try:
        from fcmd.apps.llm._llmw_qt_paint import parse_paint_settings, start_paint_service
        from fcmd.apps.llm._llmw_winsettings import load_settings
    except ImportError as exc:  # pragma: no cover - 无 Qt 的 CLI / CI 环境
        raise FlowError("画图服务未就绪，且当前环境无法自动启动（缺少 GUI 配置）") from exc
    settings = parse_paint_settings(load_settings().paint or {})
    try:
        start_paint_service(settings)
    except Exception as exc:
        raise FlowError(f"自动启动画图服务失败: {exc}") from exc
    state, alive = _llmpaint_server.status()
    if state is None or not alive:
        raise FlowError("自动启动画图服务失败：服务未登记或未就绪")
    return state.host, state.port


def _optional_path(params: Mapping[str, Any], name: str) -> Path | None:
    text = _text_param(params, name).strip()
    return Path(text).expanduser() if text else None


def _run_text_input(_ctx: FlowContext, _inputs: Mapping[str, Any], params: Mapping[str, Any]) -> dict[str, Any]:
    text = _text_param(params, "text")
    return {"text": text}


def _run_llm_chat(ctx: FlowContext, inputs: Mapping[str, Any], params: Mapping[str, Any]) -> dict[str, Any]:
    prompt = str(inputs.get("prompt") or "").strip()
    if not prompt:
        raise FlowError("LLM 输入提示词为空")
    host = _text_param(params, "host", llmbase.DEFAULT_HOST)
    port = _int_param(params, "port", llmbase.DEFAULT_PORT)
    api_key = _text_param(params, "api_key", llmbase.DEFAULT_API_KEY)
    if not llmserve.check_health(host, port, api_key):
        raise FlowError(f"LLM 服务未就绪: {llmbase.http_base_url(host, port)}，请先在运行页启动模型")
    ctx.check_cancelled()
    endpoint = llmbase.Endpoint(
        llmbase.http_base_url(host, port),
        api_key,
        _text_param(params, "model"),
    )
    session = llmchat.ChatSession(
        endpoint,
        system=_text_param(params, "system") or None,
        max_tokens=_int_param(params, "max_tokens", 2048),
        temperature=_float_param(params, "temperature", 0.7),
        timeout=_float_param(params, "timeout", 180.0),
    )
    reply = session.ask(prompt)
    return {"text": reply}


def _run_image_txt2img(ctx: FlowContext, inputs: Mapping[str, Any], params: Mapping[str, Any]) -> dict[str, Any]:
    prompt = str(inputs.get("prompt") or "").strip()
    if not prompt:
        raise FlowError("画图提示词为空")
    host = _text_param(params, "host", _llmpaint_server.DEFAULT_HOST)
    port = _int_param(params, "port", _llmpaint_server.DEFAULT_PORT)
    if not _llmpaint_server.check_paint_health(host, port):
        if not _bool_param(params, "auto_start", True):
            raise FlowError(
                f"画图服务未就绪: {llmbase.http_base_url(host, port)}；"
                "请先在运行页启动画图服务，或在节点开启「服务未运行时自动启动」"
            )
        host, port = _ensure_paint_service()
        if not _llmpaint_server.check_paint_health(host, port):
            raise FlowError(f"画图服务自动启动后仍未就绪: {llmbase.http_base_url(host, port)}")
    ctx.check_cancelled()
    request = _llmpaint.PaintRequest(
        prompt=prompt,
        negative=str(inputs.get("negative") or ""),
        width=_int_param(params, "width", 512),
        height=_int_param(params, "height", 512),
        steps=_int_param(params, "steps", 25),
        cfg_scale=_float_param(params, "cfg_scale", 7.0),
        sampler=_text_param(params, "sampler", "euler_a"),
        scheduler=_text_param(params, "scheduler"),
        seed=_int_param(params, "seed", -1),
        batch=_int_param(params, "batch", 1),
    )
    images = _llmpaint_server.txt2img(host, port, _llmpaint.build_payload(request))
    return {"images": images}


def _run_image_save(_ctx: FlowContext, inputs: Mapping[str, Any], params: Mapping[str, Any]) -> dict[str, Any]:
    images = inputs.get("images")
    if not isinstance(images, list) or not images:
        raise FlowError("没有可保存的图片")
    directory = _text_param(params, "directory").strip()
    if not directory:
        raise FlowError("请填写图片输出目录")
    paths = _llmpaint.save_images([str(item) for item in images], Path(directory).expanduser())
    return {"files": [str(path) for path in paths]}


def _run_text_save(_ctx: FlowContext, inputs: Mapping[str, Any], params: Mapping[str, Any]) -> dict[str, Any]:
    text = str(inputs.get("text") or "")
    target = _text_param(params, "path").strip()
    if not target:
        raise FlowError("请填写文本保存路径")
    path = Path(target).expanduser()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise FlowError(f"写入文本失败: {path}: {exc}") from exc
    return {"files": [str(path)]}


def register_builtin_nodes(registry: Any) -> None:
    """把内置节点登记到注册表（注册表类型见 :class:`NodeRegistry`）。"""
    registry.register(
        NodeDefinition(
            type=TEXT_INPUT,
            label="文本输入",
            category="输入",
            description="工作流入口：固定文本作为提示词或上下文。",
            inputs=(),
            outputs=(PortSpec("text", TEXT),),
            params=(ParamSpec("text", "文本", kind="text", default="", hint="作为下游节点输入"),),
            run=_run_text_input,
        )
    )
    registry.register(
        NodeDefinition(
            type=LLM_CHAT,
            label="LLM 对话",
            category="模型",
            description="调用运行中的 llama.cpp 服务完成一轮对话；服务未启动时直接报错。",
            inputs=(PortSpec("prompt", TEXT),),
            outputs=(PortSpec("text", TEXT),),
            params=(
                ParamSpec("host", "地址", default=llmbase.DEFAULT_HOST),
                ParamSpec("port", "端口", kind="int", default=llmbase.DEFAULT_PORT, minimum=1, maximum=65535),
                ParamSpec("model", "模型名", default="", hint="留空由服务端决定"),
                ParamSpec("system", "系统提示", kind="text", default=""),
                ParamSpec("temperature", "温度", kind="float", default=0.7, minimum=0, maximum=2),
                ParamSpec("max_tokens", "输出上限", kind="int", default=2048, minimum=1),
                ParamSpec("timeout", "超时秒数", kind="float", default=180.0, minimum=1, maximum=3600),
            ),
            run=_run_llm_chat,
        )
    )
    registry.register(
        NodeDefinition(
            type=IMAGE_TXT2IMG,
            label="sd.cpp 文生图",
            category="模型",
            description="调用运行中的 sd-server 文生图；服务未启动时直接报错。",
            inputs=(PortSpec("prompt", TEXT), PortSpec("negative", TEXT, required=False)),
            outputs=(PortSpec("images", IMAGES),),
            params=(
                ParamSpec("host", "地址", default=_llmpaint_server.DEFAULT_HOST),
                ParamSpec("port", "端口", kind="int", default=_llmpaint_server.DEFAULT_PORT, minimum=1, maximum=65535),
                ParamSpec(
                    "auto_start",
                    "服务未运行时自动启动",
                    kind="bool",
                    default=True,
                    hint="按运行页「图像」配置启动 sd.cpp，自动配对组件也会生效",
                ),
                ParamSpec("width", "宽度", kind="int", default=512, minimum=64, maximum=4096),
                ParamSpec("height", "高度", kind="int", default=512, minimum=64, maximum=4096),
                ParamSpec("steps", "步数", kind="int", default=25, minimum=1, maximum=200),
                ParamSpec("cfg_scale", "CFG", kind="float", default=7.0, minimum=0, maximum=30),
                ParamSpec("sampler", "采样器", default="euler_a"),
                ParamSpec("scheduler", "调度器", default=""),
                ParamSpec("seed", "种子", kind="int", default=-1, minimum=-1),
                ParamSpec("batch", "批量", kind="int", default=1, minimum=1, maximum=16),
            ),
            run=_run_image_txt2img,
        )
    )
    registry.register(
        NodeDefinition(
            type=IMAGE_SAVE,
            label="保存图片",
            category="保存",
            description="把上游图片写到目录，文件名为 paint-<时间>-<序号>.png。",
            inputs=(PortSpec("images", IMAGES),),
            outputs=(PortSpec("files", FILES),),
            params=(ParamSpec("directory", "输出目录", kind="path", default="", directory=True),),
            run=_run_image_save,
        )
    )
    registry.register(
        NodeDefinition(
            type=TEXT_SAVE,
            label="保存文本",
            category="保存",
            description="把上游文本写入 UTF-8 文件。",
            inputs=(PortSpec("text", TEXT),),
            outputs=(PortSpec("files", FILES),),
            params=(ParamSpec("path", "保存路径", kind="path", default="", directory=False),),
            run=_run_text_save,
        )
    )
