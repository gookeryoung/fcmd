"""数据域公共实现：csvtool / inifile / jsontool / tomltool / xmltool / yamtool。

六类工具全部为纯 Python 实现（标准库 csv/configparser/json/tomllib/
xml.etree.ElementTree + 可选 yaml），无子进程调用。业务实现只在本模块保留
一份；``fcmd.dsl.toolcmd.data`` 仅保留 ``@action`` 声明与参数转接。

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
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


def _require_file(filepath: Path) -> None:
    """校验文件存在，缺失时抛 ``FileNotFoundError``（消息含路径）。"""
    if not filepath.exists():
        raise FileNotFoundError(f"文件不存在: {filepath}")


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
    _require_file(filepath)
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
    _require_file(filepath)
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
# inifile 公共函数
# ============================================================================


def read_ini(filepath: Path) -> configparser.ConfigParser:
    """读取 INI 文件并解析为 ConfigParser 对象。"""
    _require_file(filepath)
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
# jsontool 公共函数
# ============================================================================


def read_json(filepath: Path) -> Any:
    """读取 JSON 文件并解析为 Python 对象。"""
    _require_file(filepath)
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
# tomltool 公共函数
# ============================================================================


def read_toml(filepath: Path) -> dict[str, Any]:
    """读取 TOML 文件并解析为字典。"""
    _require_file(filepath)
    with filepath.open("rb") as f:
        return tomllib.load(f)


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
# xmltool 公共函数
# ============================================================================


def _indent_xml(element: ET.Element, space: str = "  ") -> None:
    """给 XML 元素树添加缩进。"""
    ET.indent(element, space=space)


def read_xml(filepath: Path) -> ET.Element:
    """读取 XML 文件并解析为根元素。"""
    _require_file(filepath)
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
    _require_file(filepath)
    ET.parse(filepath)


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


# ============================================================================
# yamtool 常量与公共函数
# ============================================================================

# yaml 导入探测
try:
    import yaml as _yaml_mod  # type: ignore[import-not-found]
except ImportError:
    _yaml_mod = None

# libyaml C 加速（PyYAML 官方 wheel 自带）；缺失时回退纯 Python 实现
_YAML_LOADER = getattr(_yaml_mod, "CSafeLoader", None) if _yaml_mod else None
_YAML_DUMPER = getattr(_yaml_mod, "CSafeDumper", None) if _yaml_mod else None


def _require_yaml_loader() -> Any:
    """获取 yaml 模块（若不可用则抛 ImportError）。"""
    if _yaml_mod is not None:
        return _yaml_mod
    raise ImportError("YAML 处理需要 PyYAML: pip install pyyaml")


def read_yaml(filepath: Path) -> Any:
    """读取 YAML 文件并解析为 Python 对象。"""
    _require_file(filepath)
    yaml_mod = _require_yaml_loader()
    with filepath.open("r", encoding="utf-8") as f:
        return yaml_mod.load(f, Loader=_YAML_LOADER or yaml_mod.SafeLoader)


def write_yaml(filepath: Path, data: Any, sort_keys: bool = False) -> None:
    """写入 YAML 文件。"""
    yaml_mod = _require_yaml_loader()
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with filepath.open("w", encoding="utf-8") as f:
        yaml_mod.dump(
            data,
            f,
            Dumper=_YAML_DUMPER or yaml_mod.SafeDumper,
            sort_keys=sort_keys,
            indent=2,
            allow_unicode=True,
            default_flow_style=False,
        )


def pretty_yaml(data: Any, sort_keys: bool = False, indent: int = 2) -> str:
    """格式化为 YAML 字符串。"""
    yaml_mod = _require_yaml_loader()
    return yaml_mod.dump(
        data,
        Dumper=_YAML_DUMPER or yaml_mod.SafeDumper,
        sort_keys=sort_keys,
        indent=indent,
        allow_unicode=True,
        default_flow_style=False,
    )


def get_yaml(data: Any, path: str) -> Any:
    """按点路径查询 YAML 对象（与 :func:`query_json` 同一套点路径语义）。"""
    return query_json(data, path)


def keys_yaml(data: Any) -> list[str]:
    """列出顶层键。"""
    if isinstance(data, dict):
        return list(data.keys())
    if isinstance(data, list):
        return [str(i) for i in range(len(data))]
    raise TypeError(f"无法对非容器类型 {type(data).__name__} 列键")


def validate_yaml(filepath: Path) -> None:
    """校验 YAML 文件是否语法正确。"""
    _require_file(filepath)
    yaml_mod = _require_yaml_loader()
    with filepath.open("r", encoding="utf-8") as f:
        yaml_mod.safe_load(f)


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
