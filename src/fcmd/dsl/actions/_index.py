"""动作名 → 子模块索引（生成文件，勿手改）。

``fcmd.dsl.actions`` 包按需导入子模块时据此定位动作实现所属模块，
避免 ``import fcmd.dsl.actions`` 触发全部 17 个子模块的传递依赖
（实测 ``import fcmd.dsl`` 127.5ms → 约 25ms）。

新增或删除动作后重新生成::

    uv run python -m fcmd.dsl.actions._index

``tests/dsl/test_dsl_actions.py`` 中的一致性测试会校验本表与实际
``@action`` 注册结果完全一致，缺项或多出都会失败。
"""

from __future__ import annotations

__all__ = ["ACTION_MODULES"]

ACTION_MODULES: dict[str, str] = {
    "asciitool_char": "text",
    "asciitool_code": "text",
    "asciitool_table": "text",
    "calcdate_add": "calcdate",
    "calcdate_compare": "calcdate",
    "calcdate_diff": "calcdate",
    "calcdate_workdays": "calcdate",
    "casetool_camel": "conv",
    "casetool_kebab": "conv",
    "casetool_pascal": "conv",
    "casetool_snake": "conv",
    "codetool_base64": "codec",
    "codetool_hex": "codec",
    "codetool_html": "codec",
    "codetool_rot13": "codec",
    "codetool_url": "codec",
    "colortool_hex2rgb": "conv",
    "colortool_hsl2rgb": "conv",
    "colortool_rgb2hex": "conv",
    "colortool_rgb2hsl": "conv",
    "convtool_datasize": "codec",
    "convtool_length": "codec",
    "convtool_temp": "codec",
    "convtool_weight": "codec",
    "cryptool_decrypt": "crypto",
    "cryptool_encrypt": "crypto",
    "cryptool_genkey": "crypto",
    "csvtool_from_json": "data",
    "csvtool_merge": "data",
    "csvtool_select": "data",
    "csvtool_show": "data",
    "csvtool_to_json": "data",
    "dateprefix_add": "filenames",
    "dateprefix_clear": "filenames",
    "filelevel_set": "filenames",
    "filerename_case": "filenames",
    "filerename_insert": "filenames",
    "filerename_replace": "filenames",
    "filesearch_content": "fileops",
    "filesearch_name": "fileops",
    "folderback": "system",
    "folderzip": "archive",
    "hashfile_d": "hashing",
    "hashfile_f": "hashing",
    "hashtool_md5": "hashing",
    "hashtool_sha1": "hashing",
    "hashtool_sha256": "hashing",
    "hashtool_sha512": "hashing",
    "idtool_random": "genid",
    "idtool_timestamp": "genid",
    "idtool_uuid": "genid",
    "imagetool_c": "media",
    "imagetool_co": "media",
    "imagetool_cp": "media",
    "imagetool_cv": "media",
    "imagetool_e": "media",
    "imagetool_fl": "media",
    "imagetool_hi": "media",
    "imagetool_i": "media",
    "imagetool_r": "media",
    "imagetool_ro": "media",
    "imagetool_wm": "media",
    "img2ico_gen": "media",
    "inifile_get": "data",
    "inifile_keys": "data",
    "inifile_list": "data",
    "inifile_set": "data",
    "iptool_parse": "net",
    "iptool_subnet": "net",
    "iptool_validate": "net",
    "jsontool_minify": "data",
    "jsontool_pretty": "data",
    "jsontool_query": "data",
    "jsontool_sort": "data",
    "mathtool_eval": "conv",
    "mathtool_factorial": "conv",
    "mathtool_pow": "conv",
    "mathtool_sqrt": "conv",
    "nettool_get": "net",
    "nettool_head": "net",
    "nettool_post": "net",
    "padtool_center": "text",
    "padtool_justify": "text",
    "padtool_left": "text",
    "padtool_right": "text",
    "pathtool_diff": "fileops",
    "pathtool_norm": "fileops",
    "pathtool_rel": "fileops",
    "pathtool_show": "fileops",
    "pip_expand": "compute",
    "pip_filter": "compute",
    "randtool_bytes": "calc",
    "randtool_number": "calc",
    "randtool_password": "calc",
    "randtool_string": "calc",
    "regextool_find": "text",
    "regextool_match": "text",
    "regextool_replace": "text",
    "regextool_split": "text",
    "setenv": "basic",
    "stattool_mean": "calc",
    "stattool_median": "calc",
    "stattool_stddev": "calc",
    "stattool_summarize": "calc",
    "stattool_variance": "calc",
    "sysinfo": "system",
    "taskkill": "system",
    "textdiff_dir": "text",
    "textdiff_file": "text",
    "timetool_convert": "calc",
    "timetool_fromunix": "calc",
    "timetool_now": "calc",
    "timetool_parse": "calc",
    "timetool_unix": "calc",
    "tomltool_format": "data",
    "tomltool_get": "data",
    "tomltool_keys": "data",
    "tomltool_validate": "data",
    "txttool_case": "text",
    "txttool_count": "text",
    "txttool_sort": "text",
    "txttool_unique": "text",
    "urlcheck_r": "net",
    "urlcheck_u": "net",
    "urltool_addquery": "codec",
    "urltool_baseurl": "codec",
    "urltool_parse": "codec",
    "urltool_query": "codec",
    "websave": "net",
    "which": "system",
    "writefile": "basic",
    "xmltool_extract": "data",
    "xmltool_minify": "data",
    "xmltool_pretty": "data",
    "xmltool_validate": "data",
    "yamtool_get": "data",
    "yamtool_keys": "data",
    "yamtool_pretty": "data",
    "yamtool_validate": "data",
}


def _regenerate() -> None:  # pragma: no cover - 仅在开发者显式执行时运行
    """按当前 ``@action`` 注册结果重写本模块的 ``ACTION_MODULES`` 表。"""
    import importlib
    import pkgutil
    from pathlib import Path

    import fcmd.dsl.actions as pkg

    for mod in sorted(m.name for m in pkgutil.iter_modules(pkg.__path__) if not m.name.startswith("_")):
        importlib.import_module(f"{pkg.__name__}.{mod}")
    mapping = {name: act.func.__module__.rsplit(".", 1)[1] for name, act in pkg._ACTIONS.items()}

    path = Path(__file__)
    text = path.read_text(encoding="utf-8")
    start = text.index("ACTION_MODULES: dict[str, str] = {")
    body = "\n".join(f'    "{name}": "{mapping[name]}",' for name in sorted(mapping))
    path.write_text(f"{text[:start]}ACTION_MODULES: dict[str, str] = {{\n{body}\n}}\n", encoding="utf-8")


if __name__ == "__main__":  # pragma: no cover
    _regenerate()
