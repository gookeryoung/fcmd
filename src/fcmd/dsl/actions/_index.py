"""动作名 → 子模块索引与静态签名表（生成文件，勿手改）。

``fcmd.dsl.actions`` 包按需导入子模块时据此定位动作实现所属模块，
避免 ``import fcmd.dsl.actions`` 触发全部子模块的传递依赖。

* ``ACTION_MODULES``：动作名 → 子模块路径。通用动作模块在包顶层
  （如 ``"basic"``），工具专用动作模块在 :mod:`fcmd.dsl.actions.toolcmd`
  （如 ``"toolcmd.data"``）。
* ``ACTION_SPECS``：动作名 → (参数帮助, ((参数名, 注解标记, 是否带默认值,
  默认值), ...))。声明解析与 CLI 参数 schema 合成只读本表，不导入子模块；
  仅动作真正执行时才由 ``get_action`` 导入实现模块。

新增或删除动作、修改参数帮助或签名后重新生成::

    uv run python -m fcmd.dsl.actions._index

``tests/dsl/test_dsl_actions.py`` 中的一致性测试会校验本表与实际
``@action`` 注册结果完全一致，缺项或多出都会失败。
"""

from __future__ import annotations

__all__ = ["ACTION_MODULES", "ACTION_SPECS"]

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


ACTION_SPECS: dict[str, tuple[dict[str, str], tuple[tuple[str, str, bool, object], ...]]] = {
    "asciitool_char": (
        {"char": "单个字符（多字符时报错）"},
        (("char", "str", False, None),),
    ),
    "asciitool_code": (
        {"code": "ASCII 码值（0..0x10FFFF）"},
        (("code", "int", False, None),),
    ),
    "asciitool_table": (
        {"start": "起始码值（默认 32）", "end": "结束码值（默认 126，包含）"},
        (
            ("start", "int", True, 32),
            ("end", "int", True, 126),
        ),
    ),
    "calcdate_add": (
        {"date": "起始日期（YYYY-MM-DD）", "days": "偏移天数（正数加、负数减）"},
        (
            ("date", "str", False, None),
            ("days", "int", False, None),
        ),
    ),
    "calcdate_compare": (
        {"date1": "第一个日期（YYYY-MM-DD）", "date2": "第二个日期（YYYY-MM-DD）"},
        (
            ("date1", "str", False, None),
            ("date2", "str", False, None),
        ),
    ),
    "calcdate_diff": (
        {"date1": "第一个日期（YYYY-MM-DD）", "date2": "第二个日期（YYYY-MM-DD）"},
        (
            ("date1", "str", False, None),
            ("date2", "str", False, None),
        ),
    ),
    "calcdate_workdays": (
        {"start": "起始日期（YYYY-MM-DD）", "end": "结束日期（YYYY-MM-DD）"},
        (
            ("start", "str", False, None),
            ("end", "str", False, None),
        ),
    ),
    "casetool_camel": (
        {"text": "待转换的字符串"},
        (("text", "str", False, None),),
    ),
    "casetool_kebab": (
        {"text": "待转换的字符串"},
        (("text", "str", False, None),),
    ),
    "casetool_pascal": (
        {"text": "待转换的字符串"},
        (("text", "str", False, None),),
    ),
    "casetool_snake": (
        {"text": "待转换的字符串"},
        (("text", "str", False, None),),
    ),
    "codetool_base64": (
        {"text": "待处理的文本", "decode": "为 True 时解码，否则编码（默认 False）"},
        (
            ("text", "str", False, None),
            ("decode", "bool", True, False),
        ),
    ),
    "codetool_hex": (
        {"text": "待处理的文本", "decode": "为 True 时解码，否则编码（默认 False）"},
        (
            ("text", "str", False, None),
            ("decode", "bool", True, False),
        ),
    ),
    "codetool_html": (
        {"text": "待处理的文本", "decode": "为 True 时反转义，否则转义（默认 False）"},
        (
            ("text", "str", False, None),
            ("decode", "bool", True, False),
        ),
    ),
    "codetool_rot13": (
        {"text": "待转换的文本"},
        (("text", "str", False, None),),
    ),
    "codetool_url": (
        {"text": "待处理的文本", "decode": "为 True 时解码，否则编码（默认 False）"},
        (
            ("text", "str", False, None),
            ("decode", "bool", True, False),
        ),
    ),
    "colortool_hex2rgb": (
        {"hex_color": "十六进制颜色（#RRGGBB）"},
        (("hex_color", "str", False, None),),
    ),
    "colortool_hsl2rgb": (
        {"h": "色相（0-360）", "s": "饱和度（0-100）", "light": "亮度（0-100）"},
        (
            ("h", "float", False, None),
            ("s", "float", False, None),
            ("light", "float", False, None),
        ),
    ),
    "colortool_rgb2hex": (
        {"r": "红色分量（0-255）", "g": "绿色分量（0-255）", "b": "蓝色分量（0-255）"},
        (
            ("r", "int", False, None),
            ("g", "int", False, None),
            ("b", "int", False, None),
        ),
    ),
    "colortool_rgb2hsl": (
        {"r": "红色分量（0-255）", "g": "绿色分量（0-255）", "b": "蓝色分量（0-255）"},
        (
            ("r", "int", False, None),
            ("g", "int", False, None),
            ("b", "int", False, None),
        ),
    ),
    "convtool_datasize": (
        {
            "value": "待换算的数值",
            "from_unit": "源单位（B/KB/MB/GB/TB/PB）",
            "to_unit": "目标单位",
            "base": "进制基础（binary=1024 默认；decimal=1000）",
        },
        (
            ("value", "float", False, None),
            ("from_unit", "str", False, None),
            ("to_unit", "str", False, None),
            ("base", "str", True, "binary"),
        ),
    ),
    "convtool_length": (
        {"value": "待换算的数值", "from_unit": "源单位（如 m/km/mile/ft/in）", "to_unit": "目标单位"},
        (
            ("value", "float", False, None),
            ("from_unit", "str", False, None),
            ("to_unit", "str", False, None),
        ),
    ),
    "convtool_temp": (
        {"value": "待换算的数值", "from_unit": "源单位（C/F/K）", "to_unit": "目标单位（C/F/K）"},
        (
            ("value", "float", False, None),
            ("from_unit", "str", False, None),
            ("to_unit", "str", False, None),
        ),
    ),
    "convtool_weight": (
        {"value": "待换算的数值", "from_unit": "源单位（如 g/kg/lb/oz）", "to_unit": "目标单位"},
        (
            ("value", "float", False, None),
            ("from_unit", "str", False, None),
            ("to_unit", "str", False, None),
        ),
    ),
    "cryptool_decrypt": (
        {
            "text": "待解密的密文",
            "password": "解密密码（与 key 二选一）",
            "key": "加密密钥（与 password 二选一）",
            "env": "为 True 时从 FCMD_CRYPT_PASSWORD / FCMD_CRYPT_KEY 环境变量读取",
        },
        (
            ("text", "str", False, None),
            ("password", "str", True, ""),
            ("key", "str", True, ""),
            ("env", "bool", True, False),
        ),
    ),
    "cryptool_encrypt": (
        {
            "text": "待加密的明文",
            "password": "加密密码（与 key 二选一）",
            "key": "加密密钥（与 password 二选一）",
            "env": "为 True 时从 FCMD_CRYPT_PASSWORD / FCMD_CRYPT_KEY 环境变量读取",
        },
        (
            ("text", "str", False, None),
            ("password", "str", True, ""),
            ("key", "str", True, ""),
            ("env", "bool", True, False),
        ),
    ),
    "cryptool_genkey": (
        {},
        (),
    ),
    "csvtool_from_json": (
        {"file": "JSON 文件路径", "output": "输出 CSV 路径（默认: 同名 .csv 文件）"},
        (
            ("file", "path", False, None),
            ("output", "str", True, ""),
        ),
    ),
    "csvtool_merge": (
        {
            "files": "CSV 文件路径列表（至少 2 个）",
            "mode": "合并模式（union 或 intersection，默认 union）",
            "output": "输出 CSV 路径（默认: 打印到标准输出）",
        },
        (
            ("files", "list:path", False, None),
            ("mode", "str", True, "union"),
            ("output", "str", True, ""),
        ),
    ),
    "csvtool_select": (
        {
            "file": "CSV 文件路径",
            "columns": "欲保留的列名（按输出顺序）",
            "output": "输出 CSV 路径（默认: 打印到标准输出）",
        },
        (
            ("file", "path", False, None),
            ("columns", "list:str", False, None),
            ("output", "str", True, ""),
        ),
    ),
    "csvtool_show": (
        {
            "file": "CSV 文件路径",
            "rows": "显示的行数（默认 5）",
            "header": "首行是否为表头（默认 True，使用 --no-header 关闭）",
        },
        (
            ("file", "path", False, None),
            ("rows", "int", True, 5),
            ("header", "bool", True, True),
        ),
    ),
    "csvtool_to_json": (
        {"file": "CSV 文件路径", "indent": "JSON 缩进空格数（默认 2）"},
        (
            ("file", "path", False, None),
            ("indent", "int", True, 2),
        ),
    ),
    "dateprefix_add": (
        {"files": "文件路径列表"},
        (("files", "list:path", False, None),),
    ),
    "dateprefix_clear": (
        {"files": "文件路径列表"},
        (("files", "list:path", False, None),),
    ),
    "filelevel_set": (
        {"files": "文件路径列表", "level": "文件等级（0-4），0 用于清除等级"},
        (
            ("files", "list:path", False, None),
            ("level", "int", True, 0),
        ),
    ),
    "filerename_case": (
        {
            "files": "待重命名的文件列表",
            "mode": "转换模式：lower / upper / title（默认 lower）",
            "preview": "仅预览不实际执行",
        },
        (
            ("files", "list:path", False, None),
            ("mode", "str", True, "lower"),
            ("preview", "bool", True, False),
        ),
    ),
    "filerename_insert": (
        {
            "files": "待重命名的文件列表",
            "text": "待插入文本",
            "position": "插入位置（0=开头，负数从末尾计算，超出范围自动截断）",
            "preview": "仅预览不实际执行",
        },
        (
            ("files", "list:path", False, None),
            ("text", "str", False, None),
            ("position", "int", True, 0),
            ("preview", "bool", True, False),
        ),
    ),
    "filerename_replace": (
        {
            "files": "待重命名的文件列表",
            "pattern": "正则表达式（Python re 语法，支持反向引用）",
            "replacement": "替换字符串（默认空字符串，即删除匹配部分）",
            "preview": "仅预览不实际执行",
        },
        (
            ("files", "list:path", False, None),
            ("pattern", "str", False, None),
            ("replacement", "str", True, ""),
            ("preview", "bool", True, False),
        ),
    ),
    "filesearch_content": (
        {"directory": "搜索根目录", "pattern": "正则表达式", "extension": "限定扩展名（如 .py），空串不限"},
        (
            ("directory", "path", False, None),
            ("pattern", "str", False, None),
            ("extension", "str", True, ""),
        ),
    ),
    "filesearch_name": (
        {
            "directory": "搜索根目录",
            "pattern": "文件名 glob 模式（fnmatch 语法，如 *.py）",
            "include_dirs": "是否同时返回匹配的目录",
        },
        (
            ("directory", "path", False, None),
            ("pattern", "str", False, None),
            ("include_dirs", "bool", True, False),
        ),
    ),
    "folderback": (
        {
            "src": "源文件夹路径（默认: 当前目录）",
            "dst": "目标文件夹路径（默认: ./backup）",
            "max_zip": "最大备份数量（默认: 5，超出时删除最旧的）",
        },
        (
            ("src", "str", True, "."),
            ("dst", "str", True, "./backup"),
            ("max_zip", "int", True, 5),
        ),
    ),
    "folderzip": (
        {"directory": "目标目录（默认当前目录）"},
        (("directory", "str", True, "."),),
    ),
    "hashfile_d": (
        {"directory": "目标目录路径", "algorithm": "哈希算法（md5/sha256/sha1，默认 sha256）"},
        (
            ("directory", "str", False, None),
            ("algorithm", "str", True, "sha256"),
        ),
    ),
    "hashfile_f": (
        {"path": "目标文件路径", "algorithm": "哈希算法（md5/sha256/sha1，默认 sha256）"},
        (
            ("path", "str", False, None),
            ("algorithm", "str", True, "sha256"),
        ),
    ),
    "hashtool_md5": (
        {"text": "待哈希的字符串"},
        (("text", "str", False, None),),
    ),
    "hashtool_sha1": (
        {"text": "待哈希的字符串"},
        (("text", "str", False, None),),
    ),
    "hashtool_sha256": (
        {"text": "待哈希的字符串"},
        (("text", "str", False, None),),
    ),
    "hashtool_sha512": (
        {"text": "待哈希的字符串"},
        (("text", "str", False, None),),
    ),
    "idtool_random": (
        {"length": "随机字符串长度（默认 16）"},
        (("length", "int", True, 16),),
    ),
    "idtool_timestamp": (
        {"fmt": "时间戳格式（iso 或 unix，默认 iso）"},
        (("fmt", "str", True, "iso"),),
    ),
    "idtool_uuid": (
        {"version": "UUID 版本（1 或 4，默认 4）"},
        (("version", "int", True, 4),),
    ),
    "imagetool_c": (
        {
            "input_path": "输入图片路径",
            "output_path": "输出图片路径",
            "left": "裁剪矩形左边界",
            "top": "裁剪矩形上边界",
            "right": "裁剪矩形右边界",
            "bottom": "裁剪矩形下边界",
        },
        (
            ("input_path", "path", False, None),
            ("output_path", "path", False, None),
            ("left", "int", False, None),
            ("top", "int", False, None),
            ("right", "int", False, None),
            ("bottom", "int", False, None),
        ),
    ),
    "imagetool_co": (
        {"input_path": "输入图片路径", "count": "提取的颜色数（默认 5）"},
        (
            ("input_path", "path", False, None),
            ("count", "int", True, 5),
        ),
    ),
    "imagetool_cp": (
        {"input_path": "输入图片路径", "output_path": "输出图片路径", "quality": "压缩质量（1-100，默认 85）"},
        (
            ("input_path", "path", False, None),
            ("output_path", "path", False, None),
            ("quality", "int", True, 85),
        ),
    ),
    "imagetool_cv": (
        {
            "input_path": "输入图片路径",
            "output_path": "输出图片路径（后缀决定格式）",
            "format": "目标格式（如 PNG/JPEG/WEBP），None 时按 output_path 后缀推断",
            "quality": "压缩质量（1-100，仅对 JPEG/WEBP 有效，默认 85）",
        },
        (
            ("input_path", "path", False, None),
            ("output_path", "path", False, None),
            ("format", "opt:str", True, None),
            ("quality", "int", True, 85),
        ),
    ),
    "imagetool_e": (
        {
            "input_path": "输入图片路径",
            "output_path": "输出路径（None 时原地覆盖；仅 show=True 时可省略）",
            "show": "是否打印 EXIF 标签（默认 True）",
            "set": "设置标签，格式 KEY=VALUE（KEY 为数字标签号）",
            "clear": "清空所有 EXIF 标签（在 set 之前执行）",
        },
        (
            ("input_path", "path", False, None),
            ("output_path", "opt:path", True, None),
            ("show", "bool", True, True),
            ("set", "opt:list:str", True, None),
            ("clear", "bool", True, False),
        ),
    ),
    "imagetool_fl": (
        {
            "input_path": "输入图片路径",
            "output_path": "输出图片路径",
            "direction": "翻转方向：horizontal 或 vertical（默认 horizontal）",
        },
        (
            ("input_path", "path", False, None),
            ("output_path", "path", False, None),
            ("direction", "str", True, "horizontal"),
        ),
    ),
    "imagetool_hi": (
        {"input_path": "输入图片路径", "channel": "通道：rgb 或 luminance（默认 rgb）"},
        (
            ("input_path", "path", False, None),
            ("channel", "str", True, "rgb"),
        ),
    ),
    "imagetool_i": (
        {"input_path": "输入图片路径", "json": "是否以 JSON 格式输出（默认 False=纯文本）"},
        (
            ("input_path", "path", False, None),
            ("json", "bool", True, False),
        ),
    ),
    "imagetool_r": (
        {
            "input_path": "输入图片路径",
            "output_path": "输出图片路径",
            "width": "目标宽度",
            "height": "目标高度（keep_ratio=True 时仅作上限，None 表示按宽度等比）",
            "keep_ratio": "是否保持宽高比（默认 True）",
        },
        (
            ("input_path", "path", False, None),
            ("output_path", "path", False, None),
            ("width", "int", False, None),
            ("height", "opt:int", True, None),
            ("keep_ratio", "bool", True, True),
        ),
    ),
    "imagetool_ro": (
        {
            "input_path": "输入图片路径",
            "output_path": "输出图片路径",
            "degrees": "旋转角度（正值逆时针）",
            "expand": "是否扩展画布以容纳整个旋转后的图片（默认 False）",
        },
        (
            ("input_path", "path", False, None),
            ("output_path", "path", False, None),
            ("degrees", "float", False, None),
            ("expand", "bool", True, False),
        ),
    ),
    "imagetool_wm": (
        {
            "input_path": "输入图片路径",
            "output_path": "输出图片路径",
            "text": "水印文字",
            "position": "水印位置：top-left/top-right/bottom-left/bottom-right/center（默认 bottom-right）",
            "opacity": "不透明度（0.0-1.0，默认 0.5）",
            "font_size": "字体大小（默认 32）",
        },
        (
            ("input_path", "path", False, None),
            ("output_path", "path", False, None),
            ("text", "str", False, None),
            ("position", "str", True, "bottom-right"),
            ("opacity", "float", True, 0.5),
            ("font_size", "int", True, 32),
        ),
    ),
    "img2ico_gen": (
        {
            "input_path": "输入图像/SVG 文件或目录路径",
            "output_path": "输出图标文件或目录（目录输入时输出到该目录）",
            "format": "目标格式：win 或 mac（默认根据输出后缀推断，再默认 Windows）",
            "sizes": "自定义尺寸（逗号或空格分隔，不传使用格式默认）",
            "quality": "重采样质量：low / medium / high（默认 high）",
        },
        (
            ("input_path", "path", False, None),
            ("output_path", "path", False, None),
            ("format", "opt:str", True, None),
            ("sizes", "opt:list:str", True, None),
            ("quality", "literal:low|medium|high", True, "high"),
        ),
    ),
    "inifile_get": (
        {"file": "INI 文件路径", "section": "section 名", "key": "key 名"},
        (
            ("file", "path", False, None),
            ("section", "str", False, None),
            ("key", "str", False, None),
        ),
    ),
    "inifile_keys": (
        {"file": "INI 文件路径", "section": "section 名"},
        (
            ("file", "path", False, None),
            ("section", "str", False, None),
        ),
    ),
    "inifile_list": (
        {"file": "INI 文件路径"},
        (("file", "path", False, None),),
    ),
    "inifile_set": (
        {"file": "INI 文件路径", "section": "section 名", "key": "key 名", "value": "要设置的值"},
        (
            ("file", "path", False, None),
            ("section", "str", False, None),
            ("key", "str", False, None),
            ("value", "str", False, None),
        ),
    ),
    "iptool_parse": (
        {"ip": "IP 地址字符串（IPv4 或 IPv6）"},
        (("ip", "str", False, None),),
    ),
    "iptool_subnet": (
        {"ip": "网络地址字符串", "prefix": "前缀长度"},
        (
            ("ip", "str", False, None),
            ("prefix", "int", False, None),
        ),
    ),
    "iptool_validate": (
        {"ip": "待校验的 IP 地址字符串"},
        (("ip", "str", False, None),),
    ),
    "jsontool_minify": (
        {"file": "JSON 文件路径"},
        (("file", "path", False, None),),
    ),
    "jsontool_pretty": (
        {"file": "JSON 文件路径", "indent": "缩进空格数（默认 2）"},
        (
            ("file", "path", False, None),
            ("indent", "int", True, 2),
        ),
    ),
    "jsontool_query": (
        {"file": "JSON 文件路径", "path": "点分路径（如 a.b.0.c）"},
        (
            ("file", "path", False, None),
            ("path", "str", False, None),
        ),
    ),
    "jsontool_sort": (
        {"file": "JSON 文件路径", "output": "输出 JSON 路径（默认: 打印到标准输出）"},
        (
            ("file", "path", False, None),
            ("output", "str", True, ""),
        ),
    ),
    "mathtool_eval": (
        {"expr": "数学表达式字符串（如 1 + 2 * 3）"},
        (("expr", "str", False, None),),
    ),
    "mathtool_factorial": (
        {"n": "非负整数"},
        (("n", "int", False, None),),
    ),
    "mathtool_pow": (
        {"base": "底数", "exp": "指数"},
        (
            ("base", "float", False, None),
            ("exp", "float", False, None),
        ),
    ),
    "mathtool_sqrt": (
        {"x": "待开方的数（须非负）"},
        (("x", "float", False, None),),
    ),
    "nettool_get": (
        {"url": "目标 URL", "timeout": "超时秒数（默认 30）"},
        (
            ("url", "str", False, None),
            ("timeout", "int", True, 30),
        ),
    ),
    "nettool_head": (
        {"url": "目标 URL", "timeout": "超时秒数（默认 30）"},
        (
            ("url", "str", False, None),
            ("timeout", "int", True, 30),
        ),
    ),
    "nettool_post": (
        {"url": "目标 URL", "data": "请求体（默认空串）", "timeout": "超时秒数（默认 30）"},
        (
            ("url", "str", False, None),
            ("data", "str", True, ""),
            ("timeout", "int", True, 30),
        ),
    ),
    "padtool_center": (
        {"text": "待对齐的文本", "width": "对齐宽度（默认 20，非负）"},
        (
            ("text", "str", False, None),
            ("width", "int", True, 20),
        ),
    ),
    "padtool_justify": (
        {"text": "待对齐的文本（可多行）", "width": "对齐宽度（默认 20，非负）"},
        (
            ("text", "str", False, None),
            ("width", "int", True, 20),
        ),
    ),
    "padtool_left": (
        {"text": "待对齐的文本", "width": "对齐宽度（默认 20，非负）"},
        (
            ("text", "str", False, None),
            ("width", "int", True, 20),
        ),
    ),
    "padtool_right": (
        {"text": "待对齐的文本", "width": "对齐宽度（默认 20，非负）"},
        (
            ("text", "str", False, None),
            ("width", "int", True, 20),
        ),
    ),
    "pathtool_diff": (
        {"p1": "待比较的路径 1", "p2": "待比较的路径 2"},
        (
            ("p1", "path", False, None),
            ("p2", "path", False, None),
        ),
    ),
    "pathtool_norm": (
        {"path": "待规范化的路径"},
        (("path", "path", False, None),),
    ),
    "pathtool_rel": (
        {"path": "目标路径", "base": "基准路径"},
        (
            ("path", "path", False, None),
            ("base", "path", False, None),
        ),
    ),
    "pathtool_show": (
        {"path": "待解析的路径"},
        (("path", "path", False, None),),
    ),
    "pip_expand": (
        {"packages": "包名通配符模式列表（支持 * ? [ ]）"},
        (("packages", "list:str", False, None),),
    ),
    "pip_filter": (
        {"packages": "包名列表"},
        (("packages", "list:str", False, None),),
    ),
    "randtool_bytes": (
        {"length": "字节长度（> 0）", "encoding": "输出编码：hex 或 base64"},
        (
            ("length", "int", False, None),
            ("encoding", "str", True, "hex"),
        ),
    ),
    "randtool_number": (
        {"min_val": "最小值（含）", "max_val": "最大值（含）"},
        (
            ("min_val", "int", False, None),
            ("max_val", "int", False, None),
        ),
    ),
    "randtool_password": (
        {"length": "密码长度（默认 16，>= 4）", "symbols": "是否包含符号字符"},
        (
            ("length", "int", True, 16),
            ("symbols", "bool", True, True),
        ),
    ),
    "randtool_string": (
        {"length": "字符串长度（默认 16，> 0）", "chars": "自定义字符集（空串=字母+数字）"},
        (
            ("length", "int", True, 16),
            ("chars", "str", True, ""),
        ),
    ),
    "regextool_find": (
        {"pattern": "正则表达式字符串", "text": "待查找的文本"},
        (
            ("pattern", "str", False, None),
            ("text", "str", False, None),
        ),
    ),
    "regextool_match": (
        {"pattern": "正则表达式字符串", "text": "待匹配的文本"},
        (
            ("pattern", "str", False, None),
            ("text", "str", False, None),
        ),
    ),
    "regextool_replace": (
        {"pattern": "正则表达式字符串", "replacement": "替换字符串（支持反向引用）", "text": "待替换的文本"},
        (
            ("pattern", "str", False, None),
            ("replacement", "str", False, None),
            ("text", "str", False, None),
        ),
    ),
    "regextool_split": (
        {"pattern": "正则表达式字符串（分隔符模式）", "text": "待分割的文本"},
        (
            ("pattern", "str", False, None),
            ("text", "str", False, None),
        ),
    ),
    "setenv": (
        {"name": "环境变量名", "value": "环境变量值", "default": "为 True 时使用 setdefault 不覆盖已有值"},
        (
            ("name", "str", False, None),
            ("value", "str", False, None),
            ("default", "bool", True, False),
        ),
    ),
    "stattool_mean": (
        {"file": "数据文件路径（每行一个数字）"},
        (("file", "path", False, None),),
    ),
    "stattool_median": (
        {"file": "数据文件路径（每行一个数字）"},
        (("file", "path", False, None),),
    ),
    "stattool_stddev": (
        {"file": "数据文件路径（每行一个数字）"},
        (("file", "path", False, None),),
    ),
    "stattool_summarize": (
        {"file": "数据文件路径（每行一个数字）"},
        (("file", "path", False, None),),
    ),
    "stattool_variance": (
        {"file": "数据文件路径（每行一个数字）"},
        (("file", "path", False, None),),
    ),
    "sysinfo": (
        {},
        (),
    ),
    "taskkill": (
        {"names": "进程名称列表（按名称前缀匹配）"},
        (("names", "list:str", False, None),),
    ),
    "textdiff_dir": (
        {
            "dir1": "待比较目录路径",
            "dir2": "待比较目录路径",
            "pattern": "文件名 glob 模式（默认所有文件）",
            "recursive": "是否递归比较子目录（默认 True）",
        },
        (
            ("dir1", "path", False, None),
            ("dir2", "path", False, None),
            ("pattern", "str", True, "*"),
            ("recursive", "bool", True, True),
        ),
    ),
    "textdiff_file": (
        {
            "file1": "待比较文件路径",
            "file2": "待比较文件路径",
            "context": "上下文行数（默认 3）",
            "color": "启用 ANSI 彩色输出（默认关闭）",
        },
        (
            ("file1", "path", False, None),
            ("file2", "path", False, None),
            ("context", "int", True, 3),
            ("color", "bool", True, False),
        ),
    ),
    "timetool_convert": (
        {
            "time": "时间字符串",
            "target_tz": "目标时区名（如 Asia/Shanghai / UTC）",
            "format": "strftime 格式（默认 %%Y-%%m-%%d %%H:%%M:%%S）",
            "from_tz": "源时区名（空串=视为本地时间）",
        },
        (
            ("time", "str", False, None),
            ("target_tz", "str", False, None),
            ("format", "str", True, "%Y-%m-%d %H:%M:%S"),
            ("from_tz", "str", True, ""),
        ),
    ),
    "timetool_fromunix": (
        {"ts": "Unix 时间戳（秒）", "format": "strftime 格式（默认 %%Y-%%m-%%d %%H:%%M:%%S）"},
        (
            ("ts", "int", False, None),
            ("format", "str", True, "%Y-%m-%d %H:%M:%S"),
        ),
    ),
    "timetool_now": (
        {"utc": "使用 UTC 时间（默认 False=本地时间）", "format": "strftime 格式（默认 %%Y-%%m-%%d %%H:%%M:%%S）"},
        (
            ("utc", "bool", True, False),
            ("format", "str", True, "%Y-%m-%d %H:%M:%S"),
        ),
    ),
    "timetool_parse": (
        {"time": "时间字符串", "format": "strftime 格式（默认 %%Y-%%m-%%d %%H:%%M:%%S）"},
        (
            ("time", "str", False, None),
            ("format", "str", True, "%Y-%m-%d %H:%M:%S"),
        ),
    ),
    "timetool_unix": (
        {"time": "时间字符串（视为本地时间）", "format": "strftime 格式（默认 %%Y-%%m-%%d %%H:%%M:%%S）"},
        (
            ("time", "str", False, None),
            ("format", "str", True, "%Y-%m-%d %H:%M:%S"),
        ),
    ),
    "tomltool_format": (
        {"file": "TOML 文件路径"},
        (("file", "path", False, None),),
    ),
    "tomltool_get": (
        {"file": "TOML 文件路径", "key": "点分路径（如 project.name）"},
        (
            ("file", "path", False, None),
            ("key", "str", False, None),
        ),
    ),
    "tomltool_keys": (
        {"file": "TOML 文件路径"},
        (("file", "path", False, None),),
    ),
    "tomltool_validate": (
        {"file": "TOML 文件路径"},
        (("file", "path", False, None),),
    ),
    "txttool_case": (
        {"path": "目标文件路径", "mode": "转换模式（upper/lower/title/capitalize/swapcase，默认 upper）"},
        (
            ("path", "str", False, None),
            ("mode", "str", True, "upper"),
        ),
    ),
    "txttool_count": (
        {"path": "目标文件路径"},
        (("path", "str", False, None),),
    ),
    "txttool_sort": (
        {"path": "目标文件路径", "reverse": "是否逆序排序（默认 False）"},
        (
            ("path", "str", False, None),
            ("reverse", "bool", True, False),
        ),
    ),
    "txttool_unique": (
        {"path": "目标文件路径"},
        (("path", "str", False, None),),
    ),
    "urlcheck_r": (
        {"urls": "目标 URL 列表", "timeout": "单个 URL 的超时秒数（默认 5）"},
        (
            ("urls", "list:str", False, None),
            ("timeout", "float", True, 5.0),
        ),
    ),
    "urlcheck_u": (
        {"url": "目标 URL", "timeout": "超时秒数（默认 5）"},
        (
            ("url", "str", False, None),
            ("timeout", "float", True, 5.0),
        ),
    ),
    "urltool_addquery": (
        {"url": "原 URL 字符串", "key": "参数名", "value": "参数值"},
        (
            ("url", "str", False, None),
            ("key", "str", False, None),
            ("value", "str", False, None),
        ),
    ),
    "urltool_baseurl": (
        {"url": "完整 URL 字符串"},
        (("url", "str", False, None),),
    ),
    "urltool_parse": (
        {"url": "待解析的 URL 字符串"},
        (("url", "str", False, None),),
    ),
    "urltool_query": (
        {"url": "URL 字符串", "key": "查询参数名"},
        (
            ("url", "str", False, None),
            ("key", "str", False, None),
        ),
    ),
    "websave": (
        {
            "url": "主页 URL",
            "output_dir": "输出目录（默认 saved_website）",
            "depth": "爬取深度（默认 2）",
            "timeout": "请求超时秒数（默认 30）",
        },
        (
            ("url", "str", False, None),
            ("output_dir", "str", True, "saved_website"),
            ("depth", "int", True, 2),
            ("timeout", "int", True, 30),
        ),
    ),
    "which": (
        {"commands": "要查找的命令名称列表"},
        (("commands", "list:str", False, None),),
    ),
    "writefile": (
        {"path": "目标文件路径", "content": "写入内容", "encoding": "文件编码（默认 utf-8）"},
        (
            ("path", "str", False, None),
            ("content", "str", False, None),
            ("encoding", "str", True, "utf-8"),
        ),
    ),
    "xmltool_extract": (
        {"file": "XML 文件路径", "xpath": "XPath 表达式（如 .//item/@id 或 .//item/title）"},
        (
            ("file", "path", False, None),
            ("xpath", "str", False, None),
        ),
    ),
    "xmltool_minify": (
        {"file": "XML 文件路径"},
        (("file", "path", False, None),),
    ),
    "xmltool_pretty": (
        {"file": "XML 文件路径", "indent": "缩进空格数（默认 2）"},
        (
            ("file", "path", False, None),
            ("indent", "int", True, 2),
        ),
    ),
    "xmltool_validate": (
        {"file": "XML 文件路径"},
        (("file", "path", False, None),),
    ),
    "yamtool_get": (
        {"file": "YAML 文件路径", "path": "点分路径（如 a.b.0.c）"},
        (
            ("file", "path", False, None),
            ("path", "str", False, None),
        ),
    ),
    "yamtool_keys": (
        {"file": "YAML 文件路径"},
        (("file", "path", False, None),),
    ),
    "yamtool_pretty": (
        {"file": "YAML 文件路径", "sort_keys": "是否按键名排序（默认 False）", "indent": "缩进空格数（默认 2）"},
        (
            ("file", "path", False, None),
            ("sort_keys", "bool", True, False),
            ("indent", "int", True, 2),
        ),
    ),
    "yamtool_validate": (
        {"file": "YAML 文件路径"},
        (("file", "path", False, None),),
    ),
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


def _ann_token(annotation: object) -> str:  # pragma: no cover - 仅生成期使用
    """注解对象 → 静态标记（``""`` 表示无注解；未知形态直接报错，避免静默失真）。"""
    import inspect
    import types
    import typing
    from pathlib import Path

    if annotation is inspect.Parameter.empty:
        return ""
    simple = {str: "str", int: "int", float: "float", bool: "bool", Path: "path"}
    if annotation in simple:
        return simple[annotation]
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin is list and len(args) == 1:
        return f"list:{_ann_token(args[0])}"
    if origin is types.UnionType and len(args) == 2 and type(None) in args:
        inner = args[0] if args[1] is type(None) else args[1]
        return f"opt:{_ann_token(inner)}"
    if origin is typing.Literal:
        return "literal:" + "|".join(str(a) for a in args)
    raise ValueError(f"动作参数注解形态未支持: {annotation!r}")


def _replace_table(text: str, header: str, body: str) -> str:
    """把 ``header`` 起始的表整体替换为 ``body``（保留表前注释与表后代码）。"""
    start = text.index(header)
    close = text.find("\n}\n", start)
    end = text.index("\n", start) + 1 if close == -1 else close + len("\n}\n")
    return f"{text[:start]}{header}\n{body}\n}}\n{text[end:]}"


def _regenerate() -> None:  # pragma: no cover - 仅在开发者显式执行时运行
    """按当前 ``@action`` 注册结果重写本模块的 ``ACTION_MODULES`` / ``ACTION_SPECS`` 表。"""
    import importlib
    import inspect
    import json
    import typing
    from pathlib import Path

    import fcmd.dsl.actions as pkg

    for mod in _iter_action_modules():
        importlib.import_module(f"{pkg.__name__}.{mod}")
    prefix = f"{pkg.__name__}."
    modules = {name: act.func.__module__.removeprefix(prefix) for name, act in pkg._ACTIONS.items()}

    def py_literal(value: object) -> str:
        """Python 字面量源码（字符串双引号，与 ruff format 口径一致）。"""
        if value is None:
            return "None"
        if isinstance(value, bool):
            return "True" if value else "False"
        if isinstance(value, str):
            return json.dumps(value, ensure_ascii=False)
        return repr(value)

    def display_width(text: str) -> int:
        """按 ruff 口径计算显示宽度（东亚全角字符按 2 列）。"""
        import unicodedata

        return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)

    def help_block(help_text: dict[str, str], indent: str = "        ") -> str:
        """参数帮助 dict 的源码（超过 120 列时逐键展开，保持 ruff format 稳定）。"""
        if not help_text:
            return f"{indent}{{}}"
        inline = "{" + ", ".join(f"{py_literal(k)}: {py_literal(v)}" for k, v in help_text.items()) + "}"
        if display_width(f"{indent}{inline}") <= 120:
            return f"{indent}{inline}"
        rows = [f"{indent}{{"]
        rows += [f"{indent}    {py_literal(k)}: {py_literal(v)}," for k, v in help_text.items()]
        rows.append(f"{indent}}}")
        return "\n".join(rows)

    spec_rows: list[str] = []
    for name in sorted(pkg._ACTIONS):
        act = pkg._ACTIONS[name]
        impl = inspect.unwrap(act.func)
        hints = typing.get_type_hints(impl)
        params: list[str] = []
        for p in inspect.signature(act.func).parameters.values():
            if p.kind is not inspect.Parameter.POSITIONAL_OR_KEYWORD:
                raise ValueError(f"动作 {name!r} 参数 {p.name!r} 非 POSITIONAL_OR_KEYWORD，静态表不支持")
            has_default = p.default is not inspect.Parameter.empty
            token = _ann_token(hints.get(p.name, p.annotation))
            default_repr = py_literal(p.default) if has_default else "None"
            params.append(f'            ("{p.name}", "{token}", {has_default}, {default_repr}),')
        if len(params) == 1:
            body = f"        ({params[0].strip().rstrip(',')},),"
        elif params:
            body = "        (\n" + "\n".join(params) + "\n        ),"
        else:
            body = "        (),"
        spec_rows.append(f'    "{name}": (\n{help_block(dict(act.param_help))},\n{body}\n    ),')

    path = Path(__file__)
    text = path.read_text(encoding="utf-8")
    module_body = "\n".join(f'    "{name}": "{modules[name]}",' for name in sorted(modules))
    text = _replace_table(text, "ACTION_MODULES: dict[str, str] = {", module_body)
    text = _replace_table(
        text,
        "ACTION_SPECS: dict[str, tuple[dict[str, str], tuple[tuple[str, str, bool, object], ...]]] = {",
        "\n".join(spec_rows),
    )
    path.write_text(text, encoding="utf-8")


if __name__ == "__main__":  # pragma: no cover
    _regenerate()
