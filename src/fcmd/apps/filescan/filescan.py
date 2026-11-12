"""filescan - 目录敏感内容扫描（CLI）。

三个子命令：

- ``scan``：扫描一个或多个目录，按「关键词 / 正则 + and/or/not 表达式」判定
  敏感内容，按文件聚合输出命中规则、行号与上下文；默认打码，``--raw`` 才输出
  原文。支持递归深度、后缀包含/排除、大小与修改时间过滤、跳过二进制、单文件
  读取上限、并行数与进度统计，``Ctrl+C`` 可中断；``--save-config`` 把本次生效
  的参数（含规则集）存成可复用配置。
- ``run``：按配置文件执行扫描（扫描选项、规则与报告口径都以配置为准）。
- ``rules``：列出、校验或导出规则集（默认内置敏感规则）。

示例
----
    filescan scan D:/data --include-ext ".txt;.log" --format json --output report.json
    filescan scan D:/data --rules my-rules.json --expr "cn_id and not allow_list"
    filescan scan D:/data --save-config team.json
    filescan run --config team.json --format csv --output report.csv
    filescan rules --action export --output rules.json

安全口径：扫描只读，不创建临时文件、不修改被扫文件；报告默认打码，仅 ``--raw``
输出原文。
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from typing import Literal, NoReturn

import fcmd
from fcmd.apps.filescan._cache import ScanCache, cache_fingerprint
from fcmd.apps.filescan._config import (
    ScanConfig,
    load_config,
    parse_timestamp,
)
from fcmd.apps.filescan._config import (
    save_config as save_config_file,
)
from fcmd.apps.filescan._engine import (
    DEFAULT_MAX_FILE_BYTES,
    DEFAULT_SCAN_EXTENSIONS,
    CancelToken,
    ScanOptions,
    ScanResult,
    parse_ext_text,
    scan_paths,
)
from fcmd.apps.filescan._report import (
    filter_result,
    format_text_report,
    result_to_csv,
    result_to_json,
    write_report,
)
from fcmd.apps.filescan._rules import (
    RuleSet,
    builtin_ruleset,
    default_expression,
    group_names,
    load_ruleset,
    resolve_expression,
    save_ruleset,
)
from fcmd.console import print_error

__all__ = ["rules_command", "run_command", "scan_command"]

__version__ = "0.1.0"

_DESCRIPTION = (
    "目录敏感内容扫描：关键词/正则与 and·or·not 表达式组合判定，按文件聚合命中行，默认打码，可导出文本/JSON/CSV"
)


def _fail(message: str) -> NoReturn:
    """输出中文错误并以失败退出。"""
    print_error(message)
    raise SystemExit(2)


def _warn(message: str) -> None:
    """输出中文警告到标准错误（配置逐字段容错的提示走这里）。"""
    print(f"警告：{message}", file=sys.stderr)


def _open_cache(enabled: bool, clear: bool) -> ScanCache | None:
    """按需打开扫描缓存；``clear`` 先清空。打不开或清理失败只提示，不中断。"""
    if not enabled and not clear:
        return None
    store = ScanCache()
    if store.error:
        _warn(store.error)
    if clear:
        removed = store.clear()
        print(f"扫描缓存已清理（{removed} 条）")
        if store.error:
            _warn(store.error)
    if not enabled or not store.enabled:
        store.close()
        return None
    return store


def _close_cache(store: ScanCache | None) -> None:
    """关闭缓存并输出运行期降级提示。"""
    if store is None:
        return
    if store.error:
        _warn(store.error)
    store.close()


def _load_ruleset(path: Path | None) -> RuleSet:
    """加载规则集：未指定时使用内置规则，加载失败给出中文错误。"""
    if path is None:
        return builtin_ruleset()
    try:
        return load_ruleset(path)
    except ValueError as exc:
        _fail(str(exc))


def _parse_whitelist(text: str) -> tuple[str, ...]:
    """解析后缀白名单文本；留空使用内置默认白名单（文本 + 文档后缀）。"""
    stripped = text.strip()
    if not stripped:
        return DEFAULT_SCAN_EXTENSIONS
    return parse_ext_text(stripped)


def _build_options(  # noqa: PLR0913, PLR0917 - 直接映射 CLI 选项，保持扁平
    ext_whitelist: str,
    include_ext: str,
    force_ext: str,
    exclude_ext: str,
    recursive: bool,
    max_depth: int,
    follow_symlinks: bool,
    min_size: int,
    max_size: int,
    modified_after: str,
    modified_before: str,
    skip_binary: bool,
    max_file_bytes: int,
    workers: int,
    context_chars: int,
) -> ScanOptions:
    """把 CLI 参数转换成扫描选项；``max_size<=0`` 表示不限。"""
    try:
        after = parse_timestamp(modified_after)
        before = parse_timestamp(modified_before)
    except ValueError as exc:
        _fail(f"时间过滤无效: {exc}")
    if after is not None and before is not None and after > before:
        _fail("时间过滤无效: --modified-after 晚于 --modified-before")
    return ScanOptions(
        recursive=recursive,
        max_depth=max(0, max_depth),
        follow_symlinks=follow_symlinks,
        ext_whitelist=_parse_whitelist(ext_whitelist),
        include_exts=parse_ext_text(include_ext),
        force_exts=parse_ext_text(force_ext),
        exclude_exts=parse_ext_text(exclude_ext),
        min_size=max(0, min_size),
        max_size=None if max_size <= 0 else max_size,
        modified_after=after,
        modified_before=before,
        skip_binary=skip_binary,
        max_file_bytes=max(1, max_file_bytes),
        workers=max(1, workers),
        context_chars=max(0, context_chars),
    )


@fcmd.tool("filescan", subcommand="scan", help="扫描目录中的敏感内容", description=_DESCRIPTION)
def scan_command(  # noqa: PLR0913, PLR0917 - CLI 参数由 argparse 逐个生成，保持扁平签名
    paths: list[Path],
    rules: Path | None = None,
    expr: str = "",
    recursive: bool = True,
    max_depth: int = 0,
    follow_symlinks: bool = False,
    ext_whitelist: str = "",
    include_ext: str = "",
    force_ext: str = "",
    exclude_ext: str = "",
    min_size: int = 0,
    max_size: int = 0,
    modified_after: str = "",
    modified_before: str = "",
    skip_binary: bool = True,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    workers: int = 4,
    context_chars: int = 40,
    format: Literal["text", "json", "csv"] = "text",
    output: Path | None = None,
    raw: bool = False,
    rule_id: list[str] | None = None,
    path_filter: str = "",
    save_config: Path | None = None,
    cache: bool = True,
    clear_cache: bool = False,
) -> None:
    """扫描目录并输出敏感内容报告。

    Parameters
    ----------
    paths:
        一个或多个待扫描目录
    rules:
        规则集 JSON 文件，留空使用内置敏感规则
    expr:
        组合表达式（覆盖规则集内表达式），如 ``cn_id and not allow_list``
    recursive:
        递归扫描子目录，默认开启；``--no-recursive`` 只扫描根目录
    max_depth:
        最大目录深度，0 表示不限，1 表示只下探一层子目录
    follow_symlinks:
        跟随符号链接（默认不跟随，避免扫描越出指定目录）
    ext_whitelist:
        后缀白名单，分号或逗号分隔；留空使用内置白名单（文本 + docx/xlsx/pptx/odt/ods/pdf），
        ``*`` 表示全部后缀
    include_ext:
        在白名单内进一步收窄的后缀（如 ``.txt;.log``），留空表示不收窄
    force_ext:
        强制扫描的后缀（逃生口），优先于白名单，如 ``.pdf``
    exclude_ext:
        排除的后缀，优先于白名单与强制列表
    min_size:
        最小文件大小（字节）
    max_size:
        最大文件大小（字节），0 表示不限
    modified_after:
        只扫描修改时间不早于该时刻的文件；ISO-8601 文本（如 ``2026-01-01``）或 epoch 秒
    modified_before:
        只扫描修改时间不晚于该时刻的文件；格式同 ``modified_after``
    skip_binary:
        跳过二进制文件，默认开启（``--no-skip-binary`` 关闭）
    max_file_bytes:
        单文件读取上限（字节），超出记入跳过
    workers:
        并行线程数
    context_chars:
        命中片段左右各保留的上下文字符数
    fmt:
        输出格式：text=文本（默认）、json=结构化、csv=表格
    output:
        报告写入文件；留空输出到标准输出
    raw:
        输出敏感原文（默认打码）
    rule_id:
        仅保留指定规则 id 的命中，可重复
    path_filter:
        仅保留路径包含该子串的命中
    save_config:
        把本次生效的参数与规则集存成配置文件，供 ``filescan run --config`` 复用
    cache:
        使用扫描结果缓存（默认开启，``--no-cache`` 关闭）；未变化的文件直接复用上次命中
    clear_cache:
        扫描前清空扫描缓存
    """
    if not paths:
        _fail("请至少指定一个待扫描目录")
    ruleset = _load_ruleset(rules)
    if expr.strip():
        ruleset = replace(ruleset, expression=expr)
    try:
        resolve_expression(ruleset)
    except ValueError as exc:
        _fail(f"规则表达式无效: {exc}")
    options = _build_options(
        ext_whitelist,
        include_ext,
        force_ext,
        exclude_ext,
        recursive,
        max_depth,
        follow_symlinks,
        min_size,
        max_size,
        modified_after,
        modified_before,
        skip_binary,
        max_file_bytes,
        workers,
        context_chars,
    )
    if save_config is not None:
        config = ScanConfig(
            roots=tuple(paths),
            options=options,
            ruleset=ruleset,
            format=format,
            mask=not raw,
        )
        try:
            save_config_file(config, save_config)
        except OSError as exc:
            _fail(f"无法写入配置 {save_config}: {exc}")
        print(f"扫描配置已保存到 {save_config}（{len(paths)} 个目录，规则 {len(ruleset.rules)} 条）")
    store = _open_cache(cache, clear_cache)
    token = CancelToken()
    try:
        result = scan_paths(
            paths,
            ruleset,
            options,
            token=token,
            cache=store,
            cache_key=cache_fingerprint(ruleset, options, mask=not raw) if store is not None else "",
        )
    except KeyboardInterrupt:
        token.cancel()
        print_error("扫描已取消")
        raise SystemExit(130) from None
    finally:
        _close_cache(store)
    if rule_id or path_filter.strip():
        result = filter_result(result, rule_ids=rule_id, path_contains=path_filter)
    _emit_result(result, format, output, raw)


def _emit_result(result: ScanResult, format: str, output: Path | None, include_raw: bool) -> None:
    """按格式输出报告：写文件或打到标准输出。"""
    if output is not None:
        try:
            write_report(result, output, format, include_raw=include_raw)
        except (OSError, ValueError) as exc:
            _fail(f"无法写入报告 {output}: {exc}")
        print(f"报告已写入 {output}（格式 {format}，命中 {result.stats.hit_files} 个文件）")
        return
    if format == "json":
        print(result_to_json(result, include_raw), end="")
    elif format == "csv":
        print(result_to_csv(result, include_raw).lstrip("\ufeff"), end="")
    else:
        print(format_text_report(result, include_raw))


@fcmd.tool("filescan", subcommand="run", help="按配置文件执行扫描", description=_DESCRIPTION)
def run_command(  # noqa: PLR0913, PLR0917 - CLI 参数由 argparse 逐个生成，保持扁平签名
    config: Path | None = None,
    format: Literal["text", "json", "csv"] | None = None,
    output: Path | None = None,
    raw: bool = False,
    rule_id: list[str] | None = None,
    path_filter: str = "",
    cache: bool = True,
    clear_cache: bool = False,
) -> None:
    """按配置文件执行扫描。

    扫描选项、根目录、规则集与报告口径都以配置为准；命令行只覆盖报告输出。
    配置中非法字段会回落到默认值，并在标准错误逐条给出中文提示。

    Parameters
    ----------
    config:
        配置文件路径（``filescan scan --save-config`` 生成）
    fmt:
        覆盖配置里的 ``report.format``；留空沿用配置
    output:
        报告写入文件；留空输出到标准输出
    raw:
        强制输出敏感原文（配置 ``report.mask=false`` 时也默认输出原文）
    rule_id:
        仅保留指定规则 id 的命中，可重复
    path_filter:
        仅保留路径包含该子串的命中
    cache:
        使用扫描结果缓存（默认开启，``--no-cache`` 关闭）
    clear_cache:
        扫描前清空扫描缓存
    """
    if config is None:
        _fail("请用 --config 指定配置文件")
    try:
        loaded = load_config(config)
    except ValueError as exc:
        _fail(str(exc))
    for message in loaded.warnings:
        _warn(message)
    if not loaded.roots:
        _fail(f"配置 {config} 未包含扫描目录（roots 为空）")
    ruleset = loaded.ruleset or _load_ruleset(None)
    try:
        resolve_expression(ruleset)
    except ValueError as exc:
        _fail(f"规则表达式无效: {exc}")
    fmt = format or loaded.format
    include_raw = raw or not loaded.mask
    store = _open_cache(cache, clear_cache)
    token = CancelToken()
    try:
        result = scan_paths(
            loaded.roots,
            ruleset,
            loaded.options,
            token=token,
            cache=store,
            cache_key=cache_fingerprint(ruleset, loaded.options, mask=not include_raw) if store is not None else "",
        )
    except KeyboardInterrupt:
        token.cancel()
        print_error("扫描已取消")
        raise SystemExit(130) from None
    finally:
        _close_cache(store)
    if rule_id or path_filter.strip():
        result = filter_result(result, rule_ids=rule_id, path_contains=path_filter)
    _emit_result(result, fmt, output, include_raw)


@fcmd.tool("filescan", subcommand="rules", help="查看、校验或导出规则集", description=_DESCRIPTION)
def rules_command(
    action: Literal["list", "export", "validate"] = "list",
    rules: Path | None = None,
    output: Path | None = None,
) -> None:
    """管理规则集。

    Parameters
    ----------
    action:
        list=列出规则（默认）、export=导出规则集、validate=校验规则集
    rules:
        规则集 JSON 文件，留空使用内置敏感规则
    output:
        export 的输出文件，默认为当前目录 ``filescan-rules.json``
    """
    ruleset = _load_ruleset(rules)
    if action == "validate":
        try:
            resolve_expression(ruleset)
        except ValueError as exc:
            _fail(f"规则集无效: {exc}")
        print(f"规则集有效：{len(ruleset.rules)} 条规则（启用 {ruleset.enabled_count()}）")
        return
    if action == "export":
        target = output or Path("filescan-rules.json")
        try:
            save_ruleset(ruleset, target)
        except OSError as exc:
            _fail(f"无法写入规则集 {target}: {exc}")
        print(f"规则集已导出到 {target}（{len(ruleset.rules)} 条）")
        return
    expression = ruleset.expression.strip() or default_expression(ruleset) or "（无启用规则）"
    print(f"规则集：{ruleset.name}（{len(ruleset.rules)} 条，启用 {ruleset.enabled_count()}）")
    print(f"表达式：{expression}")
    for group in group_names(ruleset):
        print(f"[{group}]")
        for rule in ruleset.rules:
            if rule.group != group:
                continue
            mark = "启用" if rule.enabled else "禁用"
            print(f"  {rule.id:<16} {rule.name}（{mark}）{rule.matcher.describe()}")
    if not ruleset.rules:
        print("（规则集为空）")


@fcmd.main("filescan")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
