"""动作名 → 子模块索引（生成文件，勿手改）。

``fcmd.dsl.actions`` 包按需导入子模块时据此定位动作实现所属模块，
避免 ``import fcmd.dsl.actions`` 触发全部子模块的传递依赖。

映射值为相对 ``fcmd.dsl.actions`` 的模块路径：通用动作模块在包顶层
（如 ``"basic"``），工具专用动作模块在 :mod:`fcmd.dsl.actions.toolcmd`
（如 ``"toolcmd.data"``）。

新增或删除动作后重新生成::

    uv run python -m fcmd.dsl.actions._index

``tests/dsl/test_dsl_actions.py`` 中的一致性测试会校验本表与实际
``@action`` 注册结果完全一致，缺项或多出都会失败。
"""

from __future__ import annotations

__all__ = ["ACTION_MODULES"]

ACTION_MODULES: dict[str, str] = {
    "asciitool_char": "toolcmd.text",
    "asciitool_code": "toolcmd.text",
    "asciitool_table": "toolcmd.text",
    "calcdate_add": "toolcmd.calcdate",
    "calcdate_compare": "toolcmd.calcdate",
    "calcdate_diff": "toolcmd.calcdate",
    "calcdate_workdays": "toolcmd.calcdate",
    "casetool_camel": "toolcmd.conv",
    "casetool_kebab": "toolcmd.conv",
    "casetool_pascal": "toolcmd.conv",
    "casetool_snake": "toolcmd.conv",
    "codetool_base64": "toolcmd.codec",
    "codetool_hex": "toolcmd.codec",
    "codetool_html": "toolcmd.codec",
    "codetool_rot13": "toolcmd.codec",
    "codetool_url": "toolcmd.codec",
    "colortool_hex2rgb": "toolcmd.conv",
    "colortool_hsl2rgb": "toolcmd.conv",
    "colortool_rgb2hex": "toolcmd.conv",
    "colortool_rgb2hsl": "toolcmd.conv",
    "convtool_datasize": "toolcmd.codec",
    "convtool_length": "toolcmd.codec",
    "convtool_temp": "toolcmd.codec",
    "convtool_weight": "toolcmd.codec",
    "cryptool_decrypt": "toolcmd.crypto",
    "cryptool_encrypt": "toolcmd.crypto",
    "cryptool_genkey": "toolcmd.crypto",
    "csvtool_from_json": "toolcmd.data",
    "csvtool_merge": "toolcmd.data",
    "csvtool_select": "toolcmd.data",
    "csvtool_show": "toolcmd.data",
    "csvtool_to_json": "toolcmd.data",
    "dateprefix_add": "filenames",
    "dateprefix_clear": "filenames",
    "filelevel_set": "filenames",
    "filerename_case": "filenames",
    "filerename_insert": "filenames",
    "filerename_replace": "filenames",
    "filesearch_content": "fileops",
    "filesearch_name": "fileops",
    "folderback": "system",
    "folderzip": "toolcmd.archive",
    "hashfile_d": "toolcmd.hashing",
    "hashfile_f": "toolcmd.hashing",
    "hashtool_md5": "toolcmd.hashing",
    "hashtool_sha1": "toolcmd.hashing",
    "hashtool_sha256": "toolcmd.hashing",
    "hashtool_sha512": "toolcmd.hashing",
    "idtool_random": "genid",
    "idtool_timestamp": "genid",
    "idtool_uuid": "genid",
    "imagetool_c": "toolcmd.media",
    "imagetool_co": "toolcmd.media",
    "imagetool_cp": "toolcmd.media",
    "imagetool_cv": "toolcmd.media",
    "imagetool_e": "toolcmd.media",
    "imagetool_fl": "toolcmd.media",
    "imagetool_hi": "toolcmd.media",
    "imagetool_i": "toolcmd.media",
    "imagetool_r": "toolcmd.media",
    "imagetool_ro": "toolcmd.media",
    "imagetool_wm": "toolcmd.media",
    "img2ico_gen": "toolcmd.media",
    "inifile_get": "toolcmd.data",
    "inifile_keys": "toolcmd.data",
    "inifile_list": "toolcmd.data",
    "inifile_set": "toolcmd.data",
    "iptool_parse": "toolcmd.net",
    "iptool_subnet": "toolcmd.net",
    "iptool_validate": "toolcmd.net",
    "jsontool_minify": "toolcmd.data",
    "jsontool_pretty": "toolcmd.data",
    "jsontool_query": "toolcmd.data",
    "jsontool_sort": "toolcmd.data",
    "mathtool_eval": "toolcmd.conv",
    "mathtool_factorial": "toolcmd.conv",
    "mathtool_pow": "toolcmd.conv",
    "mathtool_sqrt": "toolcmd.conv",
    "nettool_get": "toolcmd.net",
    "nettool_head": "toolcmd.net",
    "nettool_post": "toolcmd.net",
    "padtool_center": "toolcmd.text",
    "padtool_justify": "toolcmd.text",
    "padtool_left": "toolcmd.text",
    "padtool_right": "toolcmd.text",
    "pathtool_diff": "fileops",
    "pathtool_norm": "fileops",
    "pathtool_rel": "fileops",
    "pathtool_show": "fileops",
    "pip_expand": "toolcmd.compute",
    "pip_filter": "toolcmd.compute",
    "randtool_bytes": "toolcmd.calc",
    "randtool_number": "toolcmd.calc",
    "randtool_password": "toolcmd.calc",
    "randtool_string": "toolcmd.calc",
    "regextool_find": "toolcmd.text",
    "regextool_match": "toolcmd.text",
    "regextool_replace": "toolcmd.text",
    "regextool_split": "toolcmd.text",
    "setenv": "basic",
    "stattool_mean": "toolcmd.calc",
    "stattool_median": "toolcmd.calc",
    "stattool_stddev": "toolcmd.calc",
    "stattool_summarize": "toolcmd.calc",
    "stattool_variance": "toolcmd.calc",
    "sysinfo": "system",
    "taskkill": "system",
    "textdiff_dir": "toolcmd.text",
    "textdiff_file": "toolcmd.text",
    "timetool_convert": "toolcmd.calc",
    "timetool_fromunix": "toolcmd.calc",
    "timetool_now": "toolcmd.calc",
    "timetool_parse": "toolcmd.calc",
    "timetool_unix": "toolcmd.calc",
    "tomltool_format": "toolcmd.data",
    "tomltool_get": "toolcmd.data",
    "tomltool_keys": "toolcmd.data",
    "tomltool_validate": "toolcmd.data",
    "txttool_case": "toolcmd.text",
    "txttool_count": "toolcmd.text",
    "txttool_sort": "toolcmd.text",
    "txttool_unique": "toolcmd.text",
    "urlcheck_r": "toolcmd.net",
    "urlcheck_u": "toolcmd.net",
    "urltool_addquery": "toolcmd.codec",
    "urltool_baseurl": "toolcmd.codec",
    "urltool_parse": "toolcmd.codec",
    "urltool_query": "toolcmd.codec",
    "websave": "toolcmd.net",
    "which": "system",
    "writefile": "basic",
    "xmltool_extract": "toolcmd.data",
    "xmltool_minify": "toolcmd.data",
    "xmltool_pretty": "toolcmd.data",
    "xmltool_validate": "toolcmd.data",
    "yamtool_get": "toolcmd.data",
    "yamtool_keys": "toolcmd.data",
    "yamtool_pretty": "toolcmd.data",
    "yamtool_validate": "toolcmd.data",
}


def _iter_action_modules() -> list[str]:
    """返回全部动作子模块相对路径（顶层模块 + ``toolcmd`` 子包内模块）。"""
    import pkgutil

    import fcmd.dsl.actions as pkg
    import fcmd.dsl.actions.toolcmd as toolcmd_pkg

    names = [m.name for m in pkgutil.iter_modules(pkg.__path__) if not m.name.startswith("_") and not m.ispkg]
    names += [
        f"{toolcmd_pkg.__name__.rsplit('.', 1)[1]}.{m.name}"
        for m in pkgutil.iter_modules(toolcmd_pkg.__path__)
        if not m.name.startswith("_")
    ]
    return sorted(names)


def _regenerate() -> None:  # pragma: no cover - 仅在开发者显式执行时运行
    """按当前 ``@action`` 注册结果重写本模块的 ``ACTION_MODULES`` 表。"""
    import importlib
    from pathlib import Path

    import fcmd.dsl.actions as pkg

    for mod in _iter_action_modules():
        importlib.import_module(f"{pkg.__name__}.{mod}")
    prefix = f"{pkg.__name__}."
    mapping = {name: act.func.__module__.removeprefix(prefix) for name, act in pkg._ACTIONS.items()}

    path = Path(__file__)
    text = path.read_text(encoding="utf-8")
    start = text.index("ACTION_MODULES: dict[str, str] = {")
    end = text.index("\n}\n", start) + len("\n}\n")
    body = "\n".join(f'    "{name}": "{mapping[name]}",' for name in sorted(mapping))
    path.write_text(f"{text[:start]}ACTION_MODULES: dict[str, str] = {{\n{body}\n}}\n{text[end:]}", encoding="utf-8")


if __name__ == "__main__":  # pragma: no cover
    _regenerate()
