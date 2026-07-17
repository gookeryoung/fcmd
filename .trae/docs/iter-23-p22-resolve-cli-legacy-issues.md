# P22: 解决 P21 遗留的 CLI 框架限制

## 需求清单

- [x] P22a: 框架支持 `X | None` / `Optional[X]` 注解自动解包（PEP 604 兼容 Python 3.8）
- [x] P22b: 框架支持 `bool=True` 默认值 → `--no-name` store_false 取反
- [x] P22c: 还原 imagetool.py 中为绕过框架限制而反转的语义（`stretch`→`keep_ratio`、`hide`→`show`）
- [x] P22d: 补充框架测试与端到端回归测试

## 迭代目标

解决 P21 迭代记录中列出的两项 CLI 框架遗留问题（第三项 pdf_ocr 需系统 tesseract 属环境限制，已 `# pragma: no cover`，不在本轮范围）：
1. `int | None` 注解不自动转 int — Python 3.8 下 PEP 604 字符串注解无法被 `get_type_hints` 求值，导致 `--height` 等参数保持字符串类型。
2. `bool=True` 不支持取反 — 需用反转语义绕过（`stretch=False` 替代 `keep_ratio=True`），降低 API 直觉性。

本轮通过在 `toolkit.py` 增强 `_add_optional_arg`/`_add_positional_arg` 的注解解析能力一次性消除这两个限制，并还原 imagetool.py 的反转语义。

## 改动文件清单

| 文件 | 改动 |
|------|------|
| `src/fcmd/apis/toolkit.py` | 新增 `_unwrap_optional()` 与 `_annotation_str_to_type()`；`_add_optional_arg` 支持 `bool=True` → `--no-name` store_false（带 `dest=pname`）；`_add_positional_arg` 同步解包 Optional |
| `src/fcmd/cli/imagetool.py` | `image_resize`：`stretch: bool = False` 还原为 `keep_ratio: bool = True`；`image_exif`：`hide: bool = False` 还原为 `show: bool = True` |
| `tests/test_toolkit.py` | 新增 8 个框架测试：`_unwrap_optional`/`_annotation_str_to_type` 各路径 + `bool=True` store_false + `int|None` 经 CLI 解包为 int 回归 |
| `tests/test_cli_tools_p21.py` | 4 处参数名同步（`--stretch`→`--no-keep-ratio`、`--hide`→`--no-show`、`stretch=True`→`keep_ratio=False`）；新增 `test_image_resize_with_height_via_cli` 端到端验证 `--height` 经 CLI 转 int |

## 关键决策与依据

### 1. `_unwrap_optional()` 同时处理 typing 对象与字符串注解

Python 3.8 下 `from __future__ import annotations` 使所有注解延迟为字符串，`typing.get_type_hints` 尝试 `eval` 字符串时遇到 PEP 604 `int | None` 会抛 `TypeError`（需 3.10+）。fcmd 的 `_resolve_hints` 捕获异常后回退到原始字符串注解，导致下游 `argparse.add_argument` 拿到的是字符串 `"int | None"` 而非 `int`。

`_unwrap_optional()` 同时处理两种形式：
- 实际 typing 对象：`typing.Union[X, None]` / `typing.Optional[X]`（Python 3.8 原生支持），通过 `typing.get_origin`/`get_args` 提取非 None 参数。
- 字符串注解：`"int | None"` / `"Optional[int]"` / `"None | int"`，通过字符串切分 + `_annotation_str_to_type` 类型名映射。

多参数 Union（如 `Union[int, str, None]` 或 `"int | str | None"`）不处理，原样返回（保持现有行为，避免误判）。

### 2. `bool=True` → `--no-name` store_false + `dest=pname`

argparse `action="store_false"` 默认会将 `--no-keep-ratio` 映射到属性 `no_keep_ratio`（dash→underscore），导致 `run_tool` 第 651 行 `vars(parsed)` 字典里只有 `no_keep_ratio` 键，函数调用时找不到 `keep_ratio` 形参。

解决方案：显式传 `dest=pname`，让 argparse 把 `--no-keep-ratio` 的值写入 `keep_ratio` 属性，函数调用时正确匹配形参名。

### 3. 还原 imagetool 反转语义

P21 为绕过 `bool=True` 限制将两个参数反转：
- `image_resize` 的 `keep_ratio: bool = True` → `stretch: bool = False`
- `image_exif` 的 `show: bool = True` → `hide: bool = False`

反转语义降低 API 直觉性（"默认拉伸"比"默认保持宽高比"更难理解）。本轮框架支持 `bool=True` 后立即还原，恢复原始 pyflowx 命名，与上游保持一致便于后续同步。

## 代码实现情况

### `_unwrap_optional` 实现

```python
def _unwrap_optional(annotation: Any) -> Any:
    """从 X | None / Optional[X] 注解中提取非 None 类型 X。"""
    # 实际 typing 对象
    origin = typing.get_origin(annotation)
    if origin is typing.Union:
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0]
        return annotation
    # 字符串注解
    if isinstance(annotation, str):
        ann = annotation.strip()
        if ann.startswith("Optional[") and ann.endswith("]"):
            inner = ann[len("Optional[") : -1].strip()
            return _annotation_str_to_type(inner)
        if "|" in ann:
            parts = [p.strip() for p in ann.split("|")]
            non_none = [p for p in parts if p != "None"]
            if len(non_none) == 1:
                return _annotation_str_to_type(non_none[0])
    return annotation
```

### `_add_optional_arg` 的 bool=True 分支

```python
if isinstance(default, bool) and default is True:
    cli_name = f"--no-{pname.replace('_', '-')}"
    parser.add_argument(cli_name, dest=pname, action="store_false",
                        default=True, help=f"关闭 {pname}")
```

## 整合优化情况

- `_unwrap_optional` 在 `_add_optional_arg` 与 `_add_positional_arg` 入口各调用一次，避免重复解包逻辑。
- `_annotation_str_to_type` 提取为独立函数，便于单测覆盖各类型名映射。
- bool 默认值判断顺序优化：先 `_unwrap_optional` 再判 `bool`，确保 `Optional[bool]` 也能正确识别（虽然实际很少用）。

## 测试验证结果

### 框架测试（test_toolkit.py 新增 8 项）

| 测试 | 覆盖路径 |
|------|---------|
| `test_unwrap_optional_typing_union` | `typing.Union[X, None]` / `Optional[X]` / 多参数不处理 |
| `test_unwrap_optional_str_pep604` | `"X \| None"` / `"None \| X"` / `"Path \| None"` |
| `test_unwrap_optional_str_optional_form` | `"Optional[X]"` / 未知类型保留字符串 |
| `test_unwrap_optional_non_optional_passthrough` | int / `"int"` / `"list[str]"` / `"int \| str"` 原样返回 |
| `test_annotation_str_to_type_mapping` | int/float/str/bool/Path/pathlib.Path / 未知 |
| `test_build_parser_bool_default_true_store_false` | `bool=True` → `--no-name` store_false + dest=pname |
| `test_build_parser_optional_int_none_via_cli` | `int \| None` 经 CLI `--height 20` 解包为 int（回归核心） |

### 端到端测试（test_cli_tools_p21.py）

- `test_image_resize_with_height_via_cli`：通过 `run_tool` 调用 `imagetool r in out 30 --height 20 --no-keep-ratio`，验证输出包含 `30x20`（即 `--height` 经 CLI 正确转为 int 20）。
- 4 处旧参数名测试同步更新：`test_image_resize_stretch`→`test_image_resize_no_keep_ratio`、`test_image_exif_hide_only`→`test_image_exif_no_show`、`test_image_resize_stretch_with_height_direct`→`test_image_resize_no_keep_ratio_with_height_direct`。

### 门禁结果

- ruff check: All checks passed
- ruff format --check: 68 files already formatted
- pyrefly check: 0 errors (27 suppressed, 8 warnings)
- pytest: 1005 passed, 2 deselected, 5 warnings in 5.00s
- coverage: 99.18%（≥99.17% 基线），imagetool.py 100%、pdftool.py 100%、toolkit.py 99%（仅 748 行 `if not report.results: return` 防御分支未覆盖，预先存在）

## 遗留事项

1. **pdf_ocr 需系统级 tesseract**：环境限制，已 `# pragma: no cover`，无 actionable 项。
2. **toolkit.py 第 748 行**：`_print_task_summary` 中 `force=True` 且 `report.results` 为空的防御分支，预先存在，可考虑后续补一个 mock 测试。
3. **`int | None` 仅支持基本类型**：`_annotation_str_to_type` 仅映射 int/float/str/bool/Path，复杂类型（如 `"list[str] | None"`）不处理，保持原字符串。当前无工具用到这种组合，按 rule-01 不预留扩展点。

## 下一轮计划

- 检查 `.trae/req/` 是否有未完成需求
- 考虑补 toolkit.py 748 行 mock 测试（小幅提升覆盖率）
- 或继续移植其他 pyflowx 工具
