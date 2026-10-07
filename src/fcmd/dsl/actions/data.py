"""数据类 DSL 动作：csvtool / inifile / jsontool / tomltool / xmltool / yamtool。

六个工具原属 ``fcmd.cli.data`` 包，全部为纯 Python 实现（标准库
csv/configparser/json/xml.etree.ElementTree + 可选 yaml/tomli），无子进程
调用。迁移至本模块后由 ``@action`` 装饰器注册，TOML 声明 ``action = "<名>"``
直接引用；CLI 参数 schema 从动作签名自动推导（:mod:`fcmd.dsl.synth` 层拷贝）。

公共函数（无下划线前缀）保持原模块对外 API 不变，供测试 import：
read_csv / write_csv / csv_to_json / json_to_csv / select_columns / merge_csvs /
format_table（csvtool）、read_ini / get_ini_value / set_ini_value /
list_ini_sections / list_ini_keys（inifile）、read_json / write_json /
pretty_json / minify_json / query_json / sort_keys（jsontool）、read_toml /
get_toml_value / keys_toml / format_toml / validate_toml（tomltool）、
read_xml / write_xml / pretty_xml / minify_xml / extract_xml / validate_xml
（xmltool）、read_yaml / write_yaml / pretty_yaml / get_yaml / keys_yaml /
validate_yaml（yamtool）。
"""

from __future__ import annotations

import configparser
import copy
import csv
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from ..actions import action

# ============================================================================
# csvtool 常量
# ============================================================================

# 表格输出中每列最大宽度，超出截断
_MAX_COL_WIDTH = 30


# ============================================================================
# csvtool 公共函数
# ============================================================================


def read_csv(filepath: Path, has_header: bool = True) -> tuple[list[str] | None, list[list[str]]]:
    """读取 CSV 文件，返回表头与数据行。"""
    if not filepath.exists():
        raise FileNotFoundError(f"文件不存在: {filepath}")
    with filepath.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        all_rows = list(reader)
    if not all_rows:
        return (None, [])
    if has_header:
        header = all_rows[0]
        return (header, all_rows[1:])
    return (None, all_rows)


def write_csv(filepath: Path, rows: list[list[str]], header: list[str] | None = None) -> None:
    """写入 CSV 文件。"""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with filepath.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if header is not None:
            writer.writerow(header)
        writer.writerows(rows)


def csv_to_json(filepath: Path, indent: int = 2) -> str:
    """CSV 转 JSON 字符串。"""
    header, rows = read_csv(filepath, has_header=True)
    if header is None:
        return "[]"
    items: list[dict[str, str]] = []
    for row in rows:
        padded = row + [""] * (len(header) - len(row))
        items.append({header[i]: padded[i] for i in range(len(header))})
    return json.dumps(items, ensure_ascii=False, indent=indent)


def json_to_csv(filepath: Path) -> tuple[list[str], list[list[str]]]:
    """JSON 文件转 CSV 数据。"""
    if not filepath.exists():
        raise FileNotFoundError(f"文件不存在: {filepath}")
    with filepath.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("JSON 顶层必须是数组")
    header_keys: list[str] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("JSON 数组元素必须是对象")
        for key in item:
            if key not in seen:
                seen.add(key)
                header_keys.append(key)
    rows = [[str(item.get(key, "")) for key in header_keys] for item in data]
    return (header_keys, rows)


def select_columns(rows: list[list[str]], header: list[str], columns: list[str]) -> tuple[list[str], list[list[str]]]:
    """按列名筛选并重排列。"""
    index_map = {name: idx for idx, name in enumerate(header)}
    missing = [col for col in columns if col not in index_map]
    if missing:
        raise ValueError(f"列不存在: {', '.join(missing)}")
    indices = [index_map[col] for col in columns]
    new_rows = [[row[i] if i < len(row) else "" for i in indices] for row in rows]
    return (list(columns), new_rows)


def _merge_headers_union(headers: list[list[str]]) -> list[str]:
    """计算 union 模式的合并表头（保持首次出现顺序）。"""
    merged: list[str] = []
    seen: set[str] = set()
    for h in headers:
        for col in h:
            if col not in seen:
                seen.add(col)
                merged.append(col)
    return merged


def _merge_headers_intersection(headers: list[list[str]]) -> list[str]:
    """计算 intersection 模式的合并表头（按第一个 CSV 顺序，仅保留共有列）。"""
    if not headers[0]:
        return []
    common: set[str] = set(headers[0])
    for h in headers[1:]:
        common &= set(h)
    return [col for col in headers[0] if col in common]


def merge_csvs(files: list[Path], mode: str = "union") -> tuple[list[str], list[list[str]]]:
    """合并多个 CSV 文件。"""
    if len(files) < 2:
        raise ValueError("合并至少需要 2 个 CSV 文件")
    if mode not in ("union", "intersection"):
        raise ValueError(f"不支持的合并模式: {mode}（可选: union/intersection）")

    headers: list[list[str]] = []
    all_rows: list[list[list[str]]] = []
    for f in files:
        header, rows = read_csv(f, has_header=True)
        headers.append(header if header is not None else [])
        all_rows.append(rows)

    if mode == "union":
        merged_header = _merge_headers_union(headers)
    else:
        merged_header = _merge_headers_intersection(headers)

    merged_rows: list[list[str]] = []
    for header, rows in zip(headers, all_rows, strict=True):
        index_map = {name: idx for idx, name in enumerate(header)}
        indices: list[int | None] = [index_map.get(col) for col in merged_header]
        for row in rows:
            merged_rows.append([row[i] if i is not None and i < len(row) else "" for i in indices])

    return (merged_header, merged_rows)


def format_table(header: list[str] | None, rows: list[list[str]], max_width: int = _MAX_COL_WIDTH) -> str:
    """格式化为对齐的文本表格输出。"""
    if header is None and not rows:
        return "（空）"
    all_rows = ([header] if header is not None else []) + rows
    num_cols = max(len(r) for r in all_rows)
    widths = [0] * num_cols
    for r in all_rows:
        for i, cell in enumerate(r):
            widths[i] = max(widths[i], len(cell))
    widths = [min(w, max_width) for w in widths]

    def _format_row(r: list[str]) -> str:
        cells = []
        for i in range(num_cols):
            cell = r[i] if i < len(r) else ""
            if len(cell) > max_width:
                cell = cell[: max_width - 3] + "..."
            cells.append(cell.ljust(widths[i]))
        return "  ".join(cells).rstrip()

    lines = []
    if header is not None:
        lines.append(_format_row(header))
        lines.append("  ".join("-" * w for w in widths))
    for r in rows:
        lines.append(_format_row(r))
    return "\n".join(lines)


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
)
def csvtool_show(file: Path, rows: int = 5, header: bool = True) -> None:
    """预览 CSV 前几行（show 子命令）。"""
    try:
        hdr, data = read_csv(file, has_header=header)
    except FileNotFoundError as exc:
        print(f"错误: {exc}")
        return
    preview_rows = data[:rows]
    print(format_table(hdr, preview_rows))
    print(f"\n共 {len(data)} 行（显示前 {len(preview_rows)} 行）")


@action(
    "csvtool_to_json",
    param_help={
        "file": "CSV 文件路径",
        "indent": "JSON 缩进空格数（默认 2）",
    },
)
def csvtool_to_json(file: Path, indent: int = 2) -> None:
    """CSV 转 JSON（to-json 子命令）。"""
    try:
        text = csv_to_json(file, indent=indent)
    except FileNotFoundError as exc:
        print(f"错误: {exc}")
        return
    print(text)


@action(
    "csvtool_from_json",
    param_help={
        "file": "JSON 文件路径",
        "output": "输出 CSV 路径（默认: 同名 .csv 文件）",
    },
)
def csvtool_from_json(file: Path, output: str = "") -> None:
    """JSON 转 CSV（from-json 子命令）。"""
    try:
        header, rows = json_to_csv(file)
    except (FileNotFoundError, ValueError) as exc:
        print(f"错误: {exc}")
        return
    out_path = Path(output) if output else file.with_suffix(".csv")
    write_csv(out_path, rows, header=header)
    print(f"转换完成: {file} -> {out_path}（{len(rows)} 行）")


@action(
    "csvtool_select",
    param_help={
        "file": "CSV 文件路径",
        "columns": "欲保留的列名（按输出顺序）",
        "output": "输出 CSV 路径（默认: 打印到标准输出）",
    },
)
def csvtool_select(file: Path, columns: list[str], output: str = "") -> None:
    """按列筛选 CSV（select 子命令）。"""
    try:
        header, rows = read_csv(file, has_header=True)
    except FileNotFoundError as exc:
        print(f"错误: {exc}")
        return
    if header is None:
        print("CSV 文件为空")
        return
    try:
        new_header, new_rows = select_columns(rows, header, columns)
    except ValueError as exc:
        print(f"错误: {exc}")
        return
    if output:
        write_csv(Path(output), new_rows, header=new_header)
        print(f"筛选完成: {file} -> {output}（{len(new_rows)} 行）")
    else:
        print(format_table(new_header, new_rows))


@action(
    "csvtool_merge",
    param_help={
        "files": "CSV 文件路径列表（至少 2 个）",
        "mode": "合并模式（union 或 intersection，默认 union）",
        "output": "输出 CSV 路径（默认: 打印到标准输出）",
    },
)
def csvtool_merge(files: list[Path], mode: str = "union", output: str = "") -> None:
    """合并多个 CSV（merge 子命令）。"""
    try:
        header, rows = merge_csvs(files, mode=mode)
    except (ValueError, FileNotFoundError) as exc:
        print(f"错误: {exc}")
        return
    if output:
        write_csv(Path(output), rows, header=header)
        print(f"合并完成: {len(files)} 个文件 -> {output}（{len(rows)} 行）")
    else:
        print(format_table(header, rows))


# ============================================================================
# inifile 公共函数
# ============================================================================


def read_ini(filepath: Path) -> configparser.ConfigParser:
    """读取 INI 文件并解析为 ConfigParser 对象。"""
    if not filepath.exists():
        raise FileNotFoundError(f"文件不存在: {filepath}")
    config = configparser.ConfigParser()
    config.read(filepath, encoding="utf-8")
    return config


def get_ini_value(filepath: Path, section: str, key: str) -> str:
    """获取 INI 文件中指定 section.key 的值。"""
    config = read_ini(filepath)
    if not config.has_section(section):
        raise KeyError(f"section {section!r} 不存在")
    if not config.has_option(section, key):
        raise configparser.NoOptionError(section, key)
    return config.get(section, key)


def set_ini_value(filepath: Path, section: str, key: str, value: str) -> None:
    """设置 INI 文件中指定 section.key 的值并写回文件。"""
    config = read_ini(filepath)
    if not config.has_section(section):
        config.add_section(section)
    config.set(section, key, value)
    with filepath.open("w", encoding="utf-8") as f:
        config.write(f)


def list_ini_sections(filepath: Path) -> list[str]:
    """列出 INI 文件的所有 section（不含 DEFAULT）。"""
    config = read_ini(filepath)
    return config.sections()


def list_ini_keys(filepath: Path, section: str) -> list[str]:
    """列出 INI 文件指定 section 的所有 key（不含 DEFAULT 继承的键）。"""
    config = read_ini(filepath)
    if not config.has_section(section):
        raise KeyError(f"section {section!r} 不存在")
    return list(config[section].keys())


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
)
def inifile_get(file: Path, section: str, key: str) -> None:
    """获取指定 section.key 的值（get 子命令）。"""
    try:
        value = get_ini_value(file, section, key)
    except (FileNotFoundError, KeyError, configparser.NoOptionError, configparser.Error) as exc:
        print(f"错误: {exc}")
        return
    print(value)


@action(
    "inifile_set",
    param_help={
        "file": "INI 文件路径",
        "section": "section 名",
        "key": "key 名",
        "value": "要设置的值",
    },
)
def inifile_set(file: Path, section: str, key: str, value: str) -> None:
    """设置 section.key 的值并写回文件（set 子命令）。"""
    try:
        set_ini_value(file, section, key, value)
    except (FileNotFoundError, configparser.Error) as exc:
        print(f"错误: {exc}")
        return
    print(f"已设置 [{section}] {key} = {value}")


@action(
    "inifile_list",
    param_help={"file": "INI 文件路径"},
)
def inifile_list(file: Path) -> None:
    """列出所有 section（list 子命令）。"""
    try:
        sections = list_ini_sections(file)
    except (FileNotFoundError, configparser.Error) as exc:
        print(f"错误: {exc}")
        return
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
)
def inifile_keys(file: Path, section: str) -> None:
    """列出 section 的所有 key（keys 子命令）。"""
    try:
        keys = list_ini_keys(file, section)
    except (FileNotFoundError, KeyError, configparser.Error) as exc:
        print(f"错误: {exc}")
        return
    if not keys:
        print(f"section {section!r} 无 key")
        return
    for key in keys:
        print(key)


# ============================================================================
# jsontool 公共函数
# ============================================================================


def read_json(filepath: Path) -> Any:
    """读取 JSON 文件并解析为 Python 对象。"""
    if not filepath.exists():
        raise FileNotFoundError(f"文件不存在: {filepath}")
    with filepath.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(filepath: Path, data: Any, indent: int = 2) -> None:
    """写入 JSON 文件。"""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with filepath.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)


def pretty_json(data: Any, indent: int = 2) -> str:
    """格式化为多行 JSON 字符串。"""
    return json.dumps(data, ensure_ascii=False, indent=indent)


def minify_json(data: Any) -> str:
    """压缩为单行 JSON 字符串（无空白）。"""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def query_json(data: Any, path: str) -> Any:
    """按点路径查询 JSON 对象。"""
    if not path:
        return data
    segments = path.split(".")
    if any(seg == "" for seg in segments):
        raise ValueError(f"路径格式错误（含空段）: {path}")
    current: Any = data
    for seg in segments:
        if isinstance(current, list):
            try:
                idx = int(seg)
            except ValueError as exc:
                raise TypeError(f"列表索引必须是整数，得到: {seg}") from exc
            if idx < 0 or idx >= len(current):
                raise IndexError(f"列表索引越界: {idx}（长度 {len(current)}）")
            current = current[idx]
        elif isinstance(current, dict):
            if seg not in current:
                raise KeyError(f"键不存在: {seg}")
            current = current[seg]
        else:
            raise TypeError(f"无法对非容器类型 {type(current).__name__} 取子项: {seg}")
    return current


def sort_keys(data: Any) -> Any:
    """递归按键名排序（仅影响对象，不影响数组顺序）。"""
    if isinstance(data, dict):
        return {key: sort_keys(data[key]) for key in sorted(data)}
    if isinstance(data, list):
        return [sort_keys(item) for item in data]
    return data


# ============================================================================
# jsontool DSL 动作
# ============================================================================


@action(
    "jsontool_pretty",
    param_help={
        "file": "JSON 文件路径",
        "indent": "缩进空格数（默认 2）",
    },
)
def jsontool_pretty(file: Path, indent: int = 2) -> None:
    """格式化打印 JSON（pretty 子命令）。"""
    try:
        data = read_json(file)
    except FileNotFoundError as exc:
        print(f"错误: {exc}")
        return
    print(pretty_json(data, indent=indent))


@action(
    "jsontool_minify",
    param_help={"file": "JSON 文件路径"},
)
def jsontool_minify(file: Path) -> None:
    """压缩 JSON 为单行（minify 子命令）。"""
    try:
        data = read_json(file)
    except FileNotFoundError as exc:
        print(f"错误: {exc}")
        return
    print(minify_json(data))


@action(
    "jsontool_query",
    param_help={
        "file": "JSON 文件路径",
        "path": "点分路径（如 a.b.0.c）",
    },
)
def jsontool_query(file: Path, path: str) -> None:
    """按点路径查询 JSON（query 子命令）。"""
    try:
        data = read_json(file)
    except FileNotFoundError as exc:
        print(f"错误: {exc}")
        return
    try:
        result = query_json(data, path)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        print(f"错误: {exc}")
        return
    if isinstance(result, (dict, list)):
        print(pretty_json(result))
    else:
        print(result)


@action(
    "jsontool_sort",
    param_help={
        "file": "JSON 文件路径",
        "output": "输出 JSON 路径（默认: 打印到标准输出）",
    },
)
def jsontool_sort(file: Path, output: str = "") -> None:
    """按键排序 JSON（sort 子命令）。"""
    try:
        data = read_json(file)
    except FileNotFoundError as exc:
        print(f"错误: {exc}")
        return
    sorted_data = sort_keys(data)
    if output:
        write_json(Path(output), sorted_data)
        print(f"排序完成: {file} -> {output}")
    else:
        print(pretty_json(sorted_data))


# ============================================================================
# tomltool 常量与公共函数
# ============================================================================

# TOML 库可用性探测
try:
    import tomllib as _tomllib_mod  # type: ignore[import-not-found]
except ImportError:
    _tomllib_mod = None
    _TOMLLIB_AVAILABLE = False
else:
    _TOMLLIB_AVAILABLE = True

try:
    import tomli as _tomli_mod  # pyrefly 豁免 try/except ImportError 块
except ImportError:
    _tomli_mod = None
    _TOMLI_AVAILABLE = False
else:
    _TOMLI_AVAILABLE = True


def _require_toml_loader() -> Any:
    """获取 TOML 解析器模块（tomllib 3.11+ 或 tomli 3.8-3.10）。"""
    if _tomllib_mod is not None:
        return _tomllib_mod
    if _tomli_mod is not None:
        return _tomli_mod
    raise ImportError("TOML 解析需要 Python 3.11+ 或安装 tomli: pip install tomli")


def read_toml(filepath: Path) -> dict[str, Any]:
    """读取 TOML 文件并解析为字典。"""
    if not filepath.exists():
        raise FileNotFoundError(f"文件不存在: {filepath}")
    toml_mod = _require_toml_loader()
    with filepath.open("rb") as f:
        return toml_mod.load(f)


def get_toml_value(filepath: Path, key: str) -> Any:
    """按点路径获取 TOML 文件中指定键的值。"""
    data = read_toml(filepath)
    result: Any = data
    for part in key.split("."):
        if not isinstance(result, dict):
            raise KeyError(f"键 {key!r} 不存在：{part!r} 处不是字典")
        if part not in result:
            raise KeyError(f"键 {key!r} 不存在：{part!r} 未找到")
        result = result[part]
    return result


def keys_toml(filepath: Path) -> list[str]:
    """列出 TOML 文件的顶层键。"""
    data = read_toml(filepath)
    return list(data.keys())


def format_toml(filepath: Path, indent: int = 2) -> str:
    """将 TOML 文件转为 JSON 格式化字符串。"""
    data = read_toml(filepath)
    return json.dumps(data, indent=indent, ensure_ascii=False, default=str)


def validate_toml(filepath: Path) -> None:
    """校验 TOML 文件语法是否正确。"""
    read_toml(filepath)


# ============================================================================
# tomltool DSL 动作
# ============================================================================


@action(
    "tomltool_get",
    param_help={
        "file": "TOML 文件路径",
        "key": "点分路径（如 project.name）",
    },
)
def tomltool_get(file: Path, key: str) -> None:
    """按点路径取值（get 子命令）。"""
    try:
        result = get_toml_value(file, key)
    except (FileNotFoundError, KeyError, ImportError) as exc:
        print(f"错误: {exc}")
        return
    if isinstance(result, (dict, list)):
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    else:
        print(result)


@action(
    "tomltool_keys",
    param_help={"file": "TOML 文件路径"},
)
def tomltool_keys(file: Path) -> None:
    """列出顶层键（keys 子命令）。"""
    try:
        keys = keys_toml(file)
    except (FileNotFoundError, ImportError) as exc:
        print(f"错误: {exc}")
        return
    if not keys:
        print("无顶层键")
        return
    for k in keys:
        print(k)


@action(
    "tomltool_format",
    param_help={"file": "TOML 文件路径"},
)
def tomltool_format(file: Path) -> None:
    """转为 JSON 格式化输出（format 子命令）。"""
    try:
        result = format_toml(file)
    except (FileNotFoundError, ImportError) as exc:
        print(f"错误: {exc}")
        return
    print(result)


@action(
    "tomltool_validate",
    param_help={"file": "TOML 文件路径"},
)
def tomltool_validate(file: Path) -> None:
    """语法校验（validate 子命令）。"""
    try:
        validate_toml(file)
    except (FileNotFoundError, ValueError, ImportError) as exc:
        print(f"错误: {exc}")
        return
    print(f"语法校验通过: {file}")


# ============================================================================
# xmltool 公共函数
# ============================================================================


def _indent_xml(element: ET.Element, space: str = "  ") -> None:
    """给 XML 元素树添加缩进。"""
    ET.indent(element, space=space)


def read_xml(filepath: Path) -> ET.Element:
    """读取 XML 文件并解析为根元素。"""
    if not filepath.exists():
        raise FileNotFoundError(f"文件不存在: {filepath}")
    return ET.parse(filepath).getroot()


def write_xml(filepath: Path, element: ET.Element, indent: int = 2) -> None:
    """写入 XML 文件（自动缩进）。"""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    clone = copy.deepcopy(element)
    _indent_xml(clone, " " * indent)
    tree = ET.ElementTree(clone)
    tree.write(filepath, encoding="utf-8", xml_declaration=True)


def pretty_xml(element: ET.Element, indent: int = 2) -> str:
    """格式化为多行 XML 字符串。"""
    clone = copy.deepcopy(element)
    _indent_xml(clone, " " * indent)
    return ET.tostring(clone, encoding="unicode")


def minify_xml(element: ET.Element) -> str:
    """压缩为单行 XML 字符串。"""
    clone = copy.deepcopy(element)
    for elem in clone.iter():
        if elem.text is not None and not elem.text.strip():
            elem.text = None
        if elem.tail is not None and not elem.tail.strip():
            elem.tail = None
    return ET.tostring(clone, encoding="unicode")


def extract_xml(element: ET.Element, xpath: str) -> list[str]:
    """按 XPath 提取文本值列表。"""
    if "/@" in xpath:
        idx = xpath.rfind("/@")
        element_path = xpath[:idx]
        attr_name = xpath[idx + 2 :]
        if not attr_name:
            raise ET.ParseError(f"XPath 语法错误: {xpath}")
        if not element_path:
            element_path = "."
        try:
            elements = element.findall(element_path)
        except (SyntaxError, TypeError) as exc:
            raise ET.ParseError(f"XPath 语法错误: {xpath}") from exc
        values: list[str] = []
        for el in elements:
            val = el.get(attr_name)
            if val is not None:
                values.append(val)
        return values

    try:
        result = element.findall(xpath)
    except (SyntaxError, TypeError) as exc:
        raise ET.ParseError(f"XPath 语法错误: {xpath}") from exc
    return [item.text.strip() if item.text else "" for item in result]


def validate_xml(filepath: Path) -> None:
    """校验 XML 文件是否良构。"""
    if not filepath.exists():
        raise FileNotFoundError(f"文件不存在: {filepath}")
    ET.parse(filepath)


# ============================================================================
# xmltool DSL 动作
# ============================================================================


def _read_xml_or_print(filepath: Path) -> ET.Element | None:
    """读取 XML 文件，失败时打印错误并返回 None。"""
    try:
        return read_xml(filepath)
    except FileNotFoundError as exc:
        print(f"错误: {exc}")
        return None
    except ET.ParseError as exc:
        print(f"XML 解析失败: {exc}")
        return None


@action(
    "xmltool_pretty",
    param_help={
        "file": "XML 文件路径",
        "indent": "缩进空格数（默认 2）",
    },
)
def xmltool_pretty(file: Path, indent: int = 2) -> None:
    """格式化打印 XML（pretty 子命令）。"""
    root = _read_xml_or_print(file)
    if root is None:
        return
    print(pretty_xml(root, indent=indent))


@action(
    "xmltool_minify",
    param_help={"file": "XML 文件路径"},
)
def xmltool_minify(file: Path) -> None:
    """压缩 XML 为单行（minify 子命令）。"""
    root = _read_xml_or_print(file)
    if root is None:
        return
    print(minify_xml(root))


@action(
    "xmltool_extract",
    param_help={
        "file": "XML 文件路径",
        "xpath": "XPath 表达式（如 .//item/@id 或 .//item/title）",
    },
)
def xmltool_extract(file: Path, xpath: str) -> None:
    """按 XPath 提取值（extract 子命令）。"""
    root = _read_xml_or_print(file)
    if root is None:
        return
    try:
        values = extract_xml(root, xpath)
    except ET.ParseError as exc:
        print(f"错误: {exc}")
        return
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
        validate_xml(file)
    except FileNotFoundError as exc:
        print(f"错误: {exc}")
        return
    except ET.ParseError as exc:
        print(f"良构校验失败: {exc}")
        return
    print(f"良构校验通过: {file}")


# ============================================================================
# yamtool 常量与公共函数
# ============================================================================

# yaml 导入探测
try:
    import yaml as _yaml_mod  # type: ignore[import-not-found]
except ImportError:
    _yaml_mod = None


def _require_yaml_loader() -> Any:
    """获取 yaml 模块（若不可用则抛 ImportError）。"""
    if _yaml_mod is not None:
        return _yaml_mod
    raise ImportError("YAML 处理需要 PyYAML: pip install pyyaml")


def read_yaml(filepath: Path) -> Any:
    """读取 YAML 文件并解析为 Python 对象。"""
    if not filepath.exists():
        raise FileNotFoundError(f"文件不存在: {filepath}")
    yaml_mod = _require_yaml_loader()
    with filepath.open("r", encoding="utf-8") as f:
        return yaml_mod.safe_load(f)


def write_yaml(filepath: Path, data: Any, sort_keys: bool = False) -> None:
    """写入 YAML 文件。"""
    yaml_mod = _require_yaml_loader()
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with filepath.open("w", encoding="utf-8") as f:
        yaml_mod.safe_dump(
            data,
            f,
            sort_keys=sort_keys,
            indent=2,
            allow_unicode=True,
            default_flow_style=False,
        )


def pretty_yaml(data: Any, sort_keys: bool = False, indent: int = 2) -> str:
    """格式化为 YAML 字符串。"""
    yaml_mod = _require_yaml_loader()
    return yaml_mod.safe_dump(
        data,
        sort_keys=sort_keys,
        indent=indent,
        allow_unicode=True,
        default_flow_style=False,
    )


def get_yaml(data: Any, path: str) -> Any:
    """按点路径查询 YAML 对象。"""
    if not path:
        return data
    segments = path.split(".")
    if any(seg == "" for seg in segments):
        raise ValueError(f"路径格式错误（含空段）: {path}")
    current: Any = data
    for seg in segments:
        if isinstance(current, list):
            try:
                idx = int(seg)
            except ValueError as exc:
                raise TypeError(f"列表索引必须是整数，得到: {seg}") from exc
            if idx < 0 or idx >= len(current):
                raise IndexError(f"列表索引越界: {idx}（长度 {len(current)}）")
            current = current[idx]
        elif isinstance(current, dict):
            if seg not in current:
                raise KeyError(f"键不存在: {seg}")
            current = current[seg]
        else:
            raise TypeError(f"无法对非容器类型 {type(current).__name__} 取子项: {seg}")
    return current


def keys_yaml(data: Any) -> list[str]:
    """列出顶层键。"""
    if isinstance(data, dict):
        return list(data.keys())
    if isinstance(data, list):
        return [str(i) for i in range(len(data))]
    raise TypeError(f"无法对非容器类型 {type(data).__name__} 列键")


def validate_yaml(filepath: Path) -> None:
    """校验 YAML 文件是否语法正确。"""
    if not filepath.exists():
        raise FileNotFoundError(f"文件不存在: {filepath}")
    yaml_mod = _require_yaml_loader()
    with filepath.open("r", encoding="utf-8") as f:
        yaml_mod.safe_load(f)


# ============================================================================
# yamtool DSL 动作
# ============================================================================


def _read_yaml_or_print(filepath: Path) -> Any | None:
    """读取 YAML 文件，失败时打印错误并返回 None。"""
    try:
        return read_yaml(filepath)
    except FileNotFoundError as exc:
        print(f"错误: {exc}")
        return None
    except ImportError as exc:
        print(f"错误: {exc}")
        return None
    except Exception as exc:
        print(f"YAML 解析失败: {exc}")
        return None


@action(
    "yamtool_pretty",
    param_help={
        "file": "YAML 文件路径",
        "sort_keys": "是否按键名排序（默认 False）",
        "indent": "缩进空格数（默认 2）",
    },
)
def yamtool_pretty(file: Path, sort_keys: bool = False, indent: int = 2) -> None:
    """格式化打印 YAML（pretty 子命令）。"""
    data = _read_yaml_or_print(file)
    if data is None:
        return
    try:
        print(pretty_yaml(data, sort_keys=sort_keys, indent=indent), end="")
    except ImportError as exc:
        print(f"错误: {exc}")


@action(
    "yamtool_get",
    param_help={
        "file": "YAML 文件路径",
        "path": "点分路径（如 a.b.0.c）",
    },
)
def yamtool_get(file: Path, path: str) -> None:
    """按点路径取值（get 子命令）。"""
    data = _read_yaml_or_print(file)
    if data is None:
        return
    try:
        result = get_yaml(data, path)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        print(f"错误: {exc}")
        return
    if isinstance(result, (dict, list)):
        try:
            print(pretty_yaml(result), end="")
        except ImportError as exc:
            print(f"错误: {exc}")
    else:
        print(result)


@action(
    "yamtool_keys",
    param_help={"file": "YAML 文件路径"},
)
def yamtool_keys(file: Path) -> None:
    """列出顶层键（keys 子命令）。"""
    data = _read_yaml_or_print(file)
    if data is None:
        return
    try:
        keys = keys_yaml(data)
    except TypeError as exc:
        print(f"错误: {exc}")
        return
    for k in keys:
        print(k)


@action(
    "yamtool_validate",
    param_help={"file": "YAML 文件路径"},
)
def yamtool_validate(file: Path) -> None:
    """校验 YAML 语法（validate 子命令）。"""
    try:
        validate_yaml(file)
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
