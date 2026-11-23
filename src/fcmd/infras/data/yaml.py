"""``fcmd.infras.data.yaml``（由 ``infras/data.py`` 拆分）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .csv import _require_file
from .json import query_json

__all__ = ["get_yaml", "keys_yaml", "pretty_yaml", "read_yaml", "validate_yaml", "write_yaml"]


def _yaml_module() -> Any:
    """延迟导入 PyYAML；缺失时抛 ``ImportError`` 并给出安装提示。"""
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ImportError("YAML 处理需要 PyYAML: pip install pyyaml") from exc
    return yaml


def _yaml_loader(yaml_mod: Any) -> Any:
    """取 SafeLoader（优先 libyaml C 加速版本，缺失时回退纯 Python 实现）。"""
    return getattr(yaml_mod, "CSafeLoader", None) or yaml_mod.SafeLoader


def _yaml_dumper(yaml_mod: Any) -> Any:
    """取 SafeDumper（优先 libyaml C 加速版本，缺失时回退纯 Python 实现）。"""
    return getattr(yaml_mod, "CSafeDumper", None) or yaml_mod.SafeDumper


def read_yaml(filepath: Path) -> Any:
    """读取 YAML 文件并解析为 Python 对象。"""
    _require_file(filepath)
    yaml_mod = _yaml_module()
    with filepath.open("r", encoding="utf-8") as f:
        return yaml_mod.load(f, Loader=_yaml_loader(yaml_mod))


def write_yaml(filepath: Path, data: Any, sort_keys: bool = False) -> None:
    """写入 YAML 文件。"""
    yaml_mod = _yaml_module()
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with filepath.open("w", encoding="utf-8") as f:
        yaml_mod.dump(
            data,
            f,
            Dumper=_yaml_dumper(yaml_mod),
            sort_keys=sort_keys,
            indent=2,
            allow_unicode=True,
            default_flow_style=False,
        )


def pretty_yaml(data: Any, sort_keys: bool = False, indent: int = 2) -> str:
    """格式化为 YAML 字符串。"""
    yaml_mod = _yaml_module()
    return yaml_mod.dump(
        data,
        Dumper=_yaml_dumper(yaml_mod),
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
    yaml_mod = _yaml_module()
    with filepath.open("r", encoding="utf-8") as f:
        yaml_mod.safe_load(f)
