"""llmpaint - 本地文生图（stable-diffusion.cpp）。

独立命令：``fcmd llmpaint <models|gen|serve|stop|status|engine-*>``。
与 ``llmw`` 对话页的画图能力共用 ``_llmpaint*`` 的纯逻辑、引擎安装与服务管理。
"""

from __future__ import annotations

import json as jsonlib
from pathlib import Path

import fcmd
from fcmd.apps.llm import _llmconfig, _llmpaint, _llmpaint_engine, _llmpaint_server
from fcmd.apps.llm.llmbase import ServeError
from fcmd.console import Table, get_console

__all__ = ["main"]


def _scan_roots(directory: Path | None = None) -> list[Path]:
    if directory is not None:
        return [directory]
    roots = [*_llmconfig.models_dirs(), _llmconfig.products_dir()]
    return list(dict.fromkeys(roots))


def _scan(directory: Path | None = None) -> _llmpaint.PaintAssets:
    return _llmpaint.scan_assets(_scan_roots(directory))


def _pick_model(model: Path | None, assets: _llmpaint.PaintAssets) -> Path:
    if model is not None:
        return model.expanduser()
    candidates = assets.main_models()
    if len(candidates) == 1:
        return candidates[0].path
    listing = "\n".join(f"  {item.path}（{item.family_label}）" for item in candidates) or "  （无）"
    raise ServeError(f"请用 --model 指定主模型；当前候选：\n{listing}")


def _components(  # noqa: PLR0913, PLR0917 - 与 CLI 参数一一对应
    model: Path,
    vae: Path | None,
    clip_l: Path | None,
    clip_g: Path | None,
    t5xxl: Path | None,
    llm: Path | None,
    mode: str,
    extra: list[str] | None,
) -> _llmpaint.PaintComponents:
    return _llmpaint.PaintComponents(
        model=model,
        vae=vae,
        clip_l=clip_l,
        clip_g=clip_g,
        t5xxl=t5xxl,
        llm=llm,
        mode=mode,
        extra_args=tuple(extra or ()),
    )


def _ensure_running(plan: _llmpaint_server.PaintPlan, *, no_start: bool) -> _llmpaint_server.PaintState:
    """复用指纹一致的服务；必要时自动重启（``no_start`` 时只报错）。"""
    state, alive = _llmpaint_server.status()
    if alive and state is not None and state.fingerprint == _llmpaint_server.fingerprint(plan):
        return state
    if no_start:
        raise ServeError("画图服务未按当前模型/参数运行：去掉 --no-start，或先执行 fcmd llmpaint serve")
    if state is not None and alive:
        _llmpaint_server.stop_paint()
    return _llmpaint_server.start_paint(plan)


def _print_paths(paths: list[Path]) -> None:
    get_console().print(f"已生成 {len(paths)} 张图片：")
    for path in paths:
        get_console().print(f"  {path}")


@fcmd.tool("llmpaint", subcommand="models", help="列出本地扩散模型与组件")
def models_cmd(directory: Path | None = None, json: bool = False) -> None:
    """扫描本地模型目录，列出可用的扩散模型、VAE 与文本编码器。

    Parameters
    ----------
    directory:
        扫描目录；省略时扫描模型目录与 ``converted`` 产物目录。
    json:
        输出机器可读 JSON。
    """
    assets = _scan(directory)
    if json:
        payload = [
            {
                "path": str(asset.path),
                "role": asset.role,
                "family": asset.family,
                "size": asset.size,
            }
            for asset in assets.assets
        ]
        get_console().print(jsonlib.dumps(payload, ensure_ascii=False, indent=2))
        return
    if not assets.assets:
        roots = "、".join(str(root) for root in assets.roots)
        get_console().print(f"没有找到扩散模型（扫描目录：{roots}）")
        return
    table = Table(title="本地扩散模型与组件")
    table.add_column("家族")
    table.add_column("角色")
    table.add_column("大小", justify="right", no_wrap=True)
    table.add_column("文件")
    for asset in assets.assets:
        table.add_row(asset.family_label, asset.role_label, _human(asset.size), str(asset.path))
    get_console().print(table)


def _human(size: int) -> str:
    from fcmd.apps.llm.llmbase import human_size

    return human_size(size)


@fcmd.tool("llmpaint", subcommand="gen", help="用本地模型生成图片")
def gen_cmd(  # noqa: PLR0913, PLR0917 - CLI 参数较多，与 sd.cpp 启动参数对应
    prompt: str = "",
    negative: str = "",
    model: Path | None = None,
    vae: Path | None = None,
    clip_l: Path | None = None,
    clip_g: Path | None = None,
    t5xxl: Path | None = None,
    llm: Path | None = None,
    width: int = 512,
    height: int = 512,
    steps: int = 25,
    cfg_scale: float = 7.0,
    sampler: str = "euler_a",
    scheduler: str = "",
    seed: int = -1,
    batch: int = 1,
    out: Path | None = None,
    engine: str | None = None,
    port: int = _llmpaint_server.DEFAULT_PORT,
    wait: float = 300.0,
    no_start: bool = False,
    extra: list[str] | None = None,
    mode: str = "auto",
) -> None:
    """生成图片；服务未运行时默认自动拉起 ``sd-server``。

    Parameters
    ----------
    prompt:
        正向提示词。
    negative:
        负面提示词。
    model:
        主模型（checkpoint / diffusion model）；省略时要求候选唯一。
    vae:
        VAE 文件；checkpoint 家族可省略。
    clip_l:
        CLIP-L 文本编码器（SD3 / FLUX）。
    clip_g:
        CLIP-G 文本编码器（SDXL / SD3）。
    t5xxl:
        T5-XXL 文本编码器（SD3 / FLUX）。
    llm:
        额外 LLM 文本编码器（qwen-image / z-image 等）。
    width:
        图片宽度（64–4096，8 的倍数）。
    height:
        图片高度（64–4096，8 的倍数）。
    steps:
        采样步数（1–150）。
    cfg_scale:
        CFG 引导强度（0–30）。
    sampler:
        采样器名（如 ``euler_a``）。
    scheduler:
        调度器名；留空则用引擎默认。
    seed:
        随机种子；-1 表示随机。
    batch:
        单次生成张数（1–8）。
    out:
        输出目录，默认 ``${FCMD_HOME}/paint/out``。
    engine:
        指定画图引擎 tag；省略时用当前 / 最新引擎。
    port:
        服务端口，默认 1234。
    wait:
        健康检查等待秒数。
    no_start:
        只连接已在运行的服务，不自动启动。
    extra:
        原样追加到 sd-server 的额外参数（可重复）。
    mode:
        加载方式：auto / checkpoint / diffusion。
    """
    assets = _scan()
    selected = _pick_model(model, assets)
    components = _components(selected, vae, clip_l, clip_g, t5xxl, llm, mode, extra)
    _llmpaint.validate_components(components)
    request = _llmpaint.PaintRequest(
        prompt=prompt,
        negative=negative,
        width=width,
        height=height,
        steps=steps,
        cfg_scale=cfg_scale,
        seed=seed,
        sampler=sampler,
        scheduler=scheduler,
        batch=batch,
    )
    _llmpaint.validate_request(request)
    plan = _llmpaint_server.make_paint_plan(components, port=port, engine_tag=engine, wait_s=wait)
    _ensure_running(plan, no_start=no_start)
    images = _llmpaint_server.txt2img(plan.host, plan.port, _llmpaint.build_payload(request))
    paths = _llmpaint.save_images(images, out or _llmpaint_server.default_out_dir(), seed=seed)
    _print_paths(paths)


@fcmd.tool("llmpaint", subcommand="serve", help="启动画图服务")
def serve_cmd(  # noqa: PLR0913, PLR0917 - CLI 参数较多
    model: Path | None = None,
    vae: Path | None = None,
    clip_l: Path | None = None,
    clip_g: Path | None = None,
    t5xxl: Path | None = None,
    llm: Path | None = None,
    engine: str | None = None,
    port: int = _llmpaint_server.DEFAULT_PORT,
    wait: float = 300.0,
    extra: list[str] | None = None,
    mode: str = "auto",
) -> None:
    """启动 ``sd-server`` 并等待健康检查通过。

    Parameters
    ----------
    model:
        主模型；省略时要求候选唯一。
    vae:
        VAE 文件。
    clip_l:
        CLIP-L 文本编码器。
    clip_g:
        CLIP-G 文本编码器。
    t5xxl:
        T5-XXL 文本编码器。
    llm:
        额外 LLM 文本编码器。
    engine:
        指定画图引擎 tag。
    port:
        服务端口，默认 1234。
    wait:
        健康检查等待秒数。
    extra:
        原样追加到 sd-server 的额外参数（可重复）。
    mode:
        加载方式：auto / checkpoint / diffusion。
    """
    assets = _scan()
    selected = _pick_model(model, assets)
    components = _components(selected, vae, clip_l, clip_g, t5xxl, llm, mode, extra)
    _llmpaint.validate_components(components)
    plan = _llmpaint_server.make_paint_plan(components, port=port, engine_tag=engine, wait_s=wait)
    state = _ensure_running(plan, no_start=False)
    get_console().print(f"画图服务就绪: {state.base_url}（pid={state.pid}，引擎 {state.engine_tag}）")


@fcmd.tool("llmpaint", subcommand="stop", help="停止画图服务")
def stop_cmd() -> None:
    """停止画图服务并清理状态文件。"""
    result = _llmpaint_server.stop_paint()
    if result.state is None:
        get_console().print("画图服务未运行")
    elif result.stopped:
        get_console().print(f"已停止画图服务（pid={result.state.pid}）")
    else:
        get_console().print(f"画图服务已退出，清理残留状态（pid={result.state.pid}）")


@fcmd.tool("llmpaint", subcommand="status", help="查看画图服务状态")
def status_cmd(json: bool = False) -> None:
    """查看画图服务状态。

    Parameters
    ----------
    json:
        输出机器可读 JSON。
    """
    state, alive = _llmpaint_server.status()
    if json:
        payload = {
            "running": alive,
            "pid": None if state is None else state.pid,
            "host": None if state is None else state.host,
            "port": None if state is None else state.port,
            "engine": None if state is None else state.engine_tag,
            "model": None if state is None else state.model,
            "started_at": None if state is None else state.started_at,
        }
        get_console().print(jsonlib.dumps(payload, ensure_ascii=False, indent=2))
        return
    if state is None:
        get_console().print("画图服务未运行")
        return
    table = Table(title="画图服务")
    table.add_column("项目", style="cyan", no_wrap=True)
    table.add_column("值")
    table.add_row("状态", "运行中" if alive else "已退出（残留状态）")
    table.add_row("地址", state.base_url)
    table.add_row("pid", str(state.pid))
    table.add_row("引擎", state.engine_tag)
    table.add_row("模型", state.model)
    table.add_row("启动时间", state.started_at)
    get_console().print(table)


@fcmd.tool("llmpaint", subcommand="engine-list", help="列出已安装画图引擎")
def engine_list_cmd() -> None:
    """列出已安装的 stable-diffusion.cpp 引擎。"""
    infos = _llmpaint_engine.list_sd_engines()
    if not infos:
        get_console().print("未安装画图引擎：fcmd llmpaint engine-install --tag <tag>")
        return
    table = Table(title="stable-diffusion.cpp 引擎")
    table.add_column("TAG")
    table.add_column("当前")
    table.add_column("后端")
    table.add_column("可执行文件")
    for info in infos:
        table.add_row(info.tag, "*" if info.source == "current" else "", info.backend or "-", str(info.exe))
    get_console().print(table)


@fcmd.tool("llmpaint", subcommand="engine-install", help="安装或注册画图引擎")
def engine_install_cmd(  # noqa: PLR0913, PLR0917 - CLI 参数较多
    tag: str = "",
    variant: str = "auto",
    url: str | None = None,
    path: Path | None = None,
    archives: list[Path] | None = None,
    use: bool = False,
) -> None:
    """从 GitHub release 安装画图引擎，或注册本地目录 / zip。

    Parameters
    ----------
    tag:
        引擎 tag（安装目录名）；本地导入时可省略，按文件名推断。
    variant:
        预编译包变体（``auto`` / ``cuda12`` / ``vulkan`` / ``cpu``）。
    url:
        直接指定 zip 地址。
    path:
        已编译引擎目录，或单个本地 ``.zip`` 离线包。
    archives:
        本地 zip 列表（引擎包 + cudart 运行库包），可重复。
    use:
        安装后设为默认引擎。
    """
    if path is not None and archives is not None:
        raise ServeError("--path 与 --archives 不能同时使用")
    if not tag and path is None and archives is None and url is None:
        raise ServeError("请用 --tag 指定版本；或用 --path / --archives 导入本地引擎包")
    engine = _llmpaint_engine.install_sd_engine(
        tag or None,
        variant=variant,
        url=url,
        path=path,
        archives=archives,
        use=use,
    )
    get_console().print(f"画图引擎就绪: {engine.tag} -> {engine.exe}")


@fcmd.tool("llmpaint", subcommand="engine-use", help="切换默认画图引擎")
def engine_use_cmd(tag: str) -> None:
    """把指定 tag 设为默认画图引擎。"""
    engine = _llmpaint_engine.use_sd_engine(tag)
    get_console().print(f"默认画图引擎: {engine.tag} -> {engine.exe}")


@fcmd.tool("llmpaint", subcommand="engine-check", help="验证画图引擎可运行")
def engine_check_cmd(tag: str | None = None) -> None:
    """执行 ``sd-server --help`` 并输出体检结果。"""
    engine = _llmpaint_engine.resolve_sd_engine(tag)
    check = _llmpaint_engine.check_sd_engine(engine)
    color = {"ok": "green", "warn": "yellow", "fail": "red"}[check.status]
    get_console().print(f"[{color}]{check.status}[/{color}] {check.name}: {check.detail}")


@fcmd.main("llmpaint")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
