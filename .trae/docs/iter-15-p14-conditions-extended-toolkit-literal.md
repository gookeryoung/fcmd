# P14: conditions 比较运算扩展 + toolkit Literal 支持

## 需求清单

- [x] P14a: 扩展 if 表达式支持 `<` / `>` / `<=` / `>=` / `in` / `not in` 比较运算
- [x] P14a: 添加 list / tuple 字面量求值（用于 `in` / `not in` 成员检查）
- [x] P14b: 增强 `_resolve_hints` 支持 `get_type_hints` 失败时逐参数 eval 回退
- [x] P14b: `_add_optional_arg` / `_add_positional_arg` 支持 `Literal[X, Y, ...]` → argparse `choices`
- [x] P14b: 添加 `_is_literal_annotation` / `_literal_choices` 辅助函数
- [x] P14: 全套门禁通过（ruff / pyrefly / pytest --cov）
- [x] P14: 迭代记录 + 提交

## 迭代目标

1. 扩展 conditions 模块的 if 表达式比较运算符，从仅 `==` / `!=` 扩展到全部 8 种（`==` / `!=` / `<` / `>` / `<=` / `>=` / `in` / `not in`），支持链式比较与混合类型错误包装。
2. 增强 toolkit 模块的参数解析：`_resolve_hints` 在 `get_type_hints` 失败时逐参数 eval 回退；`Literal` 类型自动转为 argparse `choices`，消除 bumpversion / filelevel 等工具的内部手动校验。

## 改动文件清单

| 文件 | 改动 |
|------|------|
| `src/fcmd/conditions.py` | P14a: 新增 `_compare_op` / `_COMPARE_OPS` 字典分发；`_eval_compare` 支持链式比较；`_eval_list` / `_eval_tuple` 节点处理器；`_NODE_HANDLERS` 注册 List / Tuple |
| `src/fcmd/apis/toolkit.py` | P14b: `_resolve_hints` 逐参数 eval 回退；`_is_literal_annotation` / `_literal_choices`；`_add_optional_arg` / `_add_positional_arg` Literal 分支 |
| `tests/test_conditions.py` | P14a: `TestParseIfComparison` 新增 12 个测试（含 tuple 成员检查）；修改 `test_unsupported_compare_op_raises` 用 `is` 替代 `<` |
| `tests/test_toolkit.py` | P14b: 新增 11 个测试（Literal 识别 / choices 转换 / _resolve_hints 逐参数回退） |

## 关键决策与依据

### P14a: 比较运算扩展

1. **`_COMPARE_OPS` 字典分发**：8 种比较运算符（Eq/NotEq/Lt/Gt/LtE/GtE/In/NotIn）用 `dict[type[ast.cmpop], Callable]` 映射，消除 if-elif 链，符合 P13 的 `_NODE_HANDLERS` 模式。

2. **`_compare_op` 辅助函数**：提取单个比较运算执行，`TypeError` 包装为 `ConditionError`（如 `1 < "a"` 混合类型比较），便于调用方统一处理。字典分发 + try/except 消除 PLR0911。

3. **链式比较**：`_eval_compare` 用 `left = right` 滚动赋值实现 `a < b < c` 链式语义，与 Python 原生一致。

4. **`_eval_list` / `_eval_tuple`**：新增 `ast.List` / `ast.Tuple` 节点处理器，递归求值元素，用于 `in [1, 2, 3]` / `in (1, 2, 3)` 成员检查。注册到 `_NODE_HANDLERS`。

5. **`test_unsupported_compare_op_raises` 修改**：`<` 已支持，改用 `is`（`ast.Is` 不在 `_COMPARE_OPS` 中）触发"不支持的比较运算"。

### P14b: toolkit Literal 支持

1. **`_resolve_hints` 逐参数 eval 回退**：Python 3.8 `typing.get_type_hints` 对 PEP 604 (`X | Y`) 和 PEP 585 (`list[X]`) 抛 `TypeError`，导致整个函数注解解析失败。新增逐参数回退：`get_type_hints` 失败时用 `eval` 求值每个字符串注解，单个失败不影响其他参数。

2. **`get_type_hints` 对 `NameError` 的行为**：Python 3.8 `ForwardRef._evaluate` 捕获 `NameError` 返回 `ForwardRef` 对象（不抛异常），但对 `TypeError`（如 `int | str` eval）不捕获。因此测试需用 `int | str`（触发 `TypeError`）而非未定义名称（触发 `NameError` 被捕获）来触发回退。

3. **`compile(dont_inherit=True)` 测试技巧**：测试文件有 `from __future__ import annotations`，`exec` 会继承 `__future__` 标志导致所有注解为字符串。用 `compile(code, ..., dont_inherit=True)` 创建不带 `__future__` 的函数，使 `y: int` 存储类型对象（非 str），覆盖 `_resolve_hints` 的非 str 注解分支。

4. **`_is_literal_annotation` / `_literal_choices`**：通过 `__origin__ is typing.Literal` 识别 Literal 类型，`__args__` 提取选项元组。`_add_optional_arg` / `_add_positional_arg` 新增 Literal 分支，设置 `choices` 让 argparse 自动校验取值。

5. **不移除 bumpversion / filelevel 内部校验**：虽然 argparse `choices` 已覆盖 CLI 路径，但 `BumpPart(part)` / `int(level)` 作为公共 API 的防御性校验保留（直接调用函数时仍需校验）。

## 代码实现情况

### conditions.py 核心改动

```python
# 比较运算符映射（白名单）
_COMPARE_OPS: dict[type[ast.cmpop], Any] = {
    ast.Eq: lambda left, right: left == right,
    ast.NotEq: lambda left, right: left != right,
    ast.Lt: lambda left, right: left < right,
    ast.Gt: lambda left, right: left > right,
    ast.LtE: lambda left, right: left <= right,
    ast.GtE: lambda left, right: left >= right,
    ast.In: lambda left, right: left in right,
    ast.NotIn: lambda left, right: left not in right,
}

def _eval_compare(node: ast.Compare, context: Context) -> Any:
    """求值比较运算（支持链式比较 ``a < b < c``）。"""
    left = _eval_node(node.left, context)
    for op, comparator in zip(node.ops, node.comparators):
        right = _eval_node(comparator, context)
        if not _compare_op(op, left, right):
            return False
        left = right
    return True
```

### toolkit.py 核心改动

```python
def _resolve_hints(func: Callable[..., Any]) -> dict[str, Any]:
    try:
        return typing.get_type_hints(func)
    except Exception:
        sig = inspect.signature(func)
        globalns = getattr(func, "__globals__", {})
        hints: dict[str, Any] = {}
        for pname, param in sig.parameters.items():
            if param.annotation is inspect.Parameter.empty:
                continue
            if not isinstance(param.annotation, str):
                hints[pname] = param.annotation
                continue
            try:
                hints[pname] = eval(param.annotation, globalns)
            except Exception:
                hints[pname] = param.annotation
        return hints
```

## 整合优化情况

- P14a 的 `_compare_op` + `_COMPARE_OPS` 复用 P13 的字典分发模式，保持一致性
- P14b 的 `_is_literal_annotation` / `_literal_choices` 与既有 `_is_list_annotation` / `_list_inner_type` 风格统一
- Literal 分支在 `_add_optional_arg` / `_add_positional_arg` 中位置一致（在 list 分支之前/之后），便于维护

## 测试验证结果

| 检查项 | 结果 |
|--------|------|
| ruff check | All checks passed |
| ruff format --check | 59 files already formatted |
| pyrefly check | 0 errors (16 suppressed) |
| pytest | 841 passed (+22 from P13's 819) |
| coverage | 97.91%（与 P13 持平，无下降） |
| conditions.py | 100% coverage |
| toolkit.py | 98% coverage（+3% from P13's 95%） |

## 遗留事项

- toolkit.py 仍有 7 个预存未覆盖分支（578-579 / 632->636 / 695-696 等），非 P14 新增
- gittool `isub` 子命令仍未实现（需用户确认 ref/pyflowx 语义）
- 可选后续：移植更多 CLI 工具（envdev/imagetool/pdftool/screenshot/dockercmd）

## 下一轮计划

P14 完成了 P13 遗留事项中的两项增强。fcmd 核心功能（条件表达式、参数解析、工具注册、YAML 配置、模型抽象）已趋于稳定。后续可选方向：
1. 移植更多 CLI 工具（需 ref/pyflowx 源码）
2. 修复 toolkit.py 预存未覆盖分支
3. 实现 gittool `isub` 子命令（需用户确认语义）
