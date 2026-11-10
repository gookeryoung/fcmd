"""数据类 DSL 动作：csvtool / inifile / jsontool / tomltool / xmltool / yamtool。

业务实现统一在 :mod:`fcmd.tools.data.database`；本模块只保留 ``@action``
声明与参数转接，避免 DSL 层出现第二套数据实现。TOML 声明通过
``action = "<名>"`` 引用这些名字。
"""

from __future__ import annotations

from pathlib import Path

from fcmd.tools.data import database

from .. import action

# ============================================================================
# csvtool DSL 动作
# ============================================================================


@action(
    "csvtool_show",
    param_help={
        "file": "CSV 文件路径",
        "rows": "显示的行数（默认 5）",
        "header": "首行是否为表头（默认 True，使用 --no-header 关闭）",
    },
    errors=(FileNotFoundError,),
)
def csvtool_show(file: Path, rows: int = 5, header: bool = True) -> None:
    """预览 CSV 前几行（show 子命令）。"""
    hdr, data = database.read_csv(file, has_header=header)
    preview_rows = data[:rows]
    print(database.format_table(hdr, preview_rows))
    print(f"\n共 {len(data)} 行（显示前 {len(preview_rows)} 行）")


@action(
    "csvtool_to_json",
    param_help={
        "file": "CSV 文件路径",
        "indent": "JSON 缩进空格数（默认 2）",
    },
    errors=(FileNotFoundError,),
)
def csvtool_to_json(file: Path, indent: int = 2) -> None:
    """CSV 转 JSON（to-json 子命令）。"""
    print(database.csv_to_json(file, indent=indent))


@action(
    "csvtool_from_json",
    param_help={
        "file": "JSON 文件路径",
        "output": "输出 CSV 路径（默认: 同名 .csv 文件）",
    },
    errors=(FileNotFoundError, ValueError),
)
def csvtool_from_json(file: Path, output: str = "") -> None:
    """JSON 转 CSV（from-json 子命令）。"""
    header, rows = database.json_to_csv(file)
    out_path = Path(output) if output else file.with_suffix(".csv")
    database.write_csv(out_path, rows, header=header)
    print(f"转换完成: {file} -> {out_path}（{len(rows)} 行）")


@action(
    "csvtool_select",
    param_help={
        "file": "CSV 文件路径",
        "columns": "欲保留的列名（按输出顺序）",
        "output": "输出 CSV 路径（默认: 打印到标准输出）",
    },
    errors=(FileNotFoundError, ValueError),
)
def csvtool_select(file: Path, columns: list[str], output: str = "") -> None:
    """按列筛选 CSV（select 子命令）。"""
    header, rows = database.read_csv(file, has_header=True)
    if header is None:
        print("CSV 文件为空")
        return
    new_header, new_rows = database.select_columns(rows, header, columns)
    if output:
        database.write_csv(Path(output), new_rows, header=new_header)
        print(f"筛选完成: {file} -> {output}（{len(new_rows)} 行）")
    else:
        print(database.format_table(new_header, new_rows))


@action(
    "csvtool_merge",
    param_help={
        "files": "CSV 文件路径列表（至少 2 个）",
        "mode": "合并模式（union 或 intersection，默认 union）",
        "output": "输出 CSV 路径（默认: 打印到标准输出）",
    },
    errors=(ValueError, FileNotFoundError),
)
def csvtool_merge(files: list[Path], mode: str = "union", output: str = "") -> None:
    """合并多个 CSV（merge 子命令）。"""
    header, rows = database.merge_csvs(files, mode=mode)
    if output:
        database.write_csv(Path(output), rows, header=header)
        print(f"合并完成: {len(files)} 个文件 -> {output}（{len(rows)} 行）")
    else:
        print(database.format_table(header, rows))


# ============================================================================
# inifile DSL 动作
# ============================================================================


@action(
    "inifile_get",
    param_help={
        "file": "INI 文件路径",
        "section": "section 名",
        "key": "key 名",
    },
    errors=(
        FileNotFoundError,
        KeyError,
        database.configparser.NoOptionError,
        database.configparser.Error,
    ),
)
def inifile_get(file: Path, section: str, key: str) -> None:
    """获取指定 section.key 的值（get 子命令）。"""
    print(database.get_ini_value(file, section, key))


@action(
    "inifile_set",
    param_help={
        "file": "INI 文件路径",
        "section": "section 名",
        "key": "key 名",
        "value": "要设置的值",
    },
    errors=(FileNotFoundError, database.configparser.Error),
)
def inifile_set(file: Path, section: str, key: str, value: str) -> None:
    """设置 section.key 的值并写回文件（set 子命令）。"""
    database.set_ini_value(file, section, key, value)
    print(f"已设置 [{section}] {key} = {value}")


@action(
    "inifile_list",
    param_help={"file": "INI 文件路径"},
    errors=(FileNotFoundError, database.configparser.Error),
)
def inifile_list(file: Path) -> None:
    """列出所有 section（list 子命令）。"""
    sections = database.list_ini_sections(file)
    if not sections:
        print("无 section")
        return
    for section in sections:
        print(section)


@action(
    "inifile_keys",
    param_help={
        "file": "INI 文件路径",
        "section": "section 名",
    },
    errors=(FileNotFoundError, KeyError, database.configparser.Error),
)
def inifile_keys(file: Path, section: str) -> None:
    """列出 section 的所有 key（keys 子命令）。"""
    keys = database.list_ini_keys(file, section)
    if not keys:
        print(f"section {section!r} 无 key")
        return
    for key in keys:
        print(key)


# ============================================================================
# jsontool DSL 动作
# ============================================================================


@action(
    "jsontool_pretty",
    param_help={
        "file": "JSON 文件路径",
        "indent": "缩进空格数（默认 2）",
    },
    errors=(FileNotFoundError,),
)
def jsontool_pretty(file: Path, indent: int = 2) -> None:
    """格式化打印 JSON（pretty 子命令）。"""
    print(database.pretty_json(database.read_json(file), indent=indent))


@action(
    "jsontool_minify",
    param_help={"file": "JSON 文件路径"},
    errors=(FileNotFoundError,),
)
def jsontool_minify(file: Path) -> None:
    """压缩 JSON 为单行（minify 子命令）。"""
    print(database.minify_json(database.read_json(file)))


@action(
    "jsontool_query",
    param_help={
        "file": "JSON 文件路径",
        "path": "点分路径（如 a.b.0.c）",
    },
    errors=(FileNotFoundError, KeyError, IndexError, TypeError, ValueError),
)
def jsontool_query(file: Path, path: str) -> None:
    """按点路径查询 JSON（query 子命令）。"""
    result = database.query_json(database.read_json(file), path)
    if isinstance(result, (dict, list)):
        print(database.pretty_json(result))
    else:
        print(result)


@action(
    "jsontool_sort",
    param_help={
        "file": "JSON 文件路径",
        "output": "输出 JSON 路径（默认: 打印到标准输出）",
    },
    errors=(FileNotFoundError,),
)
def jsontool_sort(file: Path, output: str = "") -> None:
    """按键排序 JSON（sort 子命令）。"""
    sorted_data = database.sort_keys(database.read_json(file))
    if output:
        database.write_json(Path(output), sorted_data)
        print(f"排序完成: {file} -> {output}")
    else:
        print(database.pretty_json(sorted_data))


# ============================================================================
# tomltool DSL 动作
# ============================================================================


@action(
    "tomltool_get",
    param_help={
        "file": "TOML 文件路径",
        "key": "点分路径（如 project.name）",
    },
    errors=(FileNotFoundError, KeyError, ImportError),
)
def tomltool_get(file: Path, key: str) -> None:
    """按点路径取值（get 子命令）。"""
    result = database.get_toml_value(file, key)
    if isinstance(result, (dict, list)):
        print(database.json.dumps(result, indent=2, ensure_ascii=False, default=str))
    else:
        print(result)


@action(
    "tomltool_keys",
    param_help={"file": "TOML 文件路径"},
    errors=(FileNotFoundError, ImportError),
)
def tomltool_keys(file: Path) -> None:
    """列出顶层键（keys 子命令）。"""
    keys = database.keys_toml(file)
    if not keys:
        print("无顶层键")
        return
    for k in keys:
        print(k)


@action(
    "tomltool_format",
    param_help={"file": "TOML 文件路径"},
    errors=(FileNotFoundError, ImportError),
)
def tomltool_format(file: Path) -> None:
    """转为 JSON 格式化输出（format 子命令）。"""
    print(database.format_toml(file))


@action(
    "tomltool_validate",
    param_help={"file": "TOML 文件路径"},
    errors=(FileNotFoundError, ValueError, ImportError),
)
def tomltool_validate(file: Path) -> None:
    """语法校验（validate 子命令）。"""
    database.validate_toml(file)
    print(f"语法校验通过: {file}")


# ============================================================================
# xmltool DSL 动作
# ============================================================================


@action(
    "xmltool_pretty",
    param_help={
        "file": "XML 文件路径",
        "indent": "缩进空格数（默认 2）",
    },
)
def xmltool_pretty(file: Path, indent: int = 2) -> None:
    """格式化打印 XML（pretty 子命令）。"""
    root = database._read_xml_or_print(file)
    if root is None:
        return
    print(database.pretty_xml(root, indent=indent))


@action(
    "xmltool_minify",
    param_help={"file": "XML 文件路径"},
)
def xmltool_minify(file: Path) -> None:
    """压缩 XML 为单行（minify 子命令）。"""
    root = database._read_xml_or_print(file)
    if root is None:
        return
    print(database.minify_xml(root))


@action(
    "xmltool_extract",
    param_help={
        "file": "XML 文件路径",
        "xpath": "XPath 表达式（如 .//item/@id 或 .//item/title）",
    },
    errors=(database.ET.ParseError,),
)
def xmltool_extract(file: Path, xpath: str) -> None:
    """按 XPath 提取值（extract 子命令）。"""
    root = database._read_xml_or_print(file)
    if root is None:
        return
    values = database.extract_xml(root, xpath)
    if not values:
        print(f"无命中: {xpath}")
        return
    for value in values:
        print(value)


@action(
    "xmltool_validate",
    param_help={"file": "XML 文件路径"},
)
def xmltool_validate(file: Path) -> None:
    """校验 XML 良构性（validate 子命令）。"""
    try:
        database.validate_xml(file)
    except FileNotFoundError as exc:
        print(f"错误: {exc}")
        return
    except database.ET.ParseError as exc:
        print(f"良构校验失败: {exc}")
        return
    print(f"良构校验通过: {file}")


# ============================================================================
# yamtool DSL 动作
# ============================================================================


@action(
    "yamtool_pretty",
    param_help={
        "file": "YAML 文件路径",
        "sort_keys": "是否按键名排序（默认 False）",
        "indent": "缩进空格数（默认 2）",
    },
    errors=(ImportError,),
)
def yamtool_pretty(file: Path, sort_keys: bool = False, indent: int = 2) -> None:
    """格式化打印 YAML（pretty 子命令）。"""
    data = database._read_yaml_or_print(file)
    if data is None:
        return
    print(database.pretty_yaml(data, sort_keys=sort_keys, indent=indent), end="")


@action(
    "yamtool_get",
    param_help={
        "file": "YAML 文件路径",
        "path": "点分路径（如 a.b.0.c）",
    },
    errors=(KeyError, IndexError, TypeError, ValueError, ImportError),
)
def yamtool_get(file: Path, path: str) -> None:
    """按点路径取值（get 子命令）。"""
    data = database._read_yaml_or_print(file)
    if data is None:
        return
    result = database.get_yaml(data, path)
    if isinstance(result, (dict, list)):
        print(database.pretty_yaml(result), end="")
    else:
        print(result)


@action(
    "yamtool_keys",
    param_help={"file": "YAML 文件路径"},
    errors=(TypeError,),
)
def yamtool_keys(file: Path) -> None:
    """列出顶层键（keys 子命令）。"""
    data = database._read_yaml_or_print(file)
    if data is None:
        return
    for k in database.keys_yaml(data):
        print(k)


@action(
    "yamtool_validate",
    param_help={"file": "YAML 文件路径"},
)
def yamtool_validate(file: Path) -> None:
    """校验 YAML 语法（validate 子命令）。"""
    try:
        database.validate_yaml(file)
    except FileNotFoundError as exc:
        print(f"错误: {exc}")
        return
    except ImportError as exc:
        print(f"错误: {exc}")
        return
    except Exception as exc:
        print(f"语法校验失败: {exc}")
        return
    print(f"语法校验通过: {file}")
