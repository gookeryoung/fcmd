# P13: conditions 条件表达式与矩阵展开模块

## 需求清单

- [x] 实现 `conditions` 模块，为 YAML 任务编排提供条件判断（`if` 表达式）与矩阵扇出（`matrix` 展开）能力
- [x] 基于 `ast` 模块安全求值，禁用 `eval`（rule-11 安全约束）
- [x] 扩展 `executors.py` 在上下文中注入上游任务状态，支持 `success()`/`failure()`/`always()` 状态检查
- [x] 扩展 `yaml_loader.py` 支持 `if` 字段与 `matrix` 字段解析
- [x] 全套门禁通过（ruff/pyrefly/pytest/coverage），覆盖率不低于 P12 的 97.80%

## 迭代目标

1. 新建 `src/fcmd/conditions.py`，提供 `parse_if`/`expand_matrix`/`substitute_matrix_vars`/`matrix_suffix` + `ConditionError`
2. 扩展 `executors.py`：`_ExecContext` 增加 `statuses` 字段，`_build_context` 接受 `global_statuses` 参数并为有 conditions 的任务注入 `__status__`
3. 扩展 `yaml_loader.py`：解析 `if` 字段为 `conditions` 元组，解析 `matrix` 字段并笛卡尔积展开为多个 TaskSpec
4. 新增 `tests/test_conditions.py`（51+2=53 测试）与 `tests/test_yaml_loader.py` 增补 21 测试

## 改动文件清单

### 新增
- `src/fcmd/conditions.py` — 条件表达式与矩阵展开工具
  - `ConditionError` 异常类
  - `parse_if(expr)` — 基于 `ast` 安全求值，返回 `Condition` 函数（带 `_reason` 属性）
  - `_eval_node` 字典分发求值器（白名单：BoolOp/UnaryOp/Compare/Call/Attribute/Name/Constant）
  - `_eval_boolop`/`_eval_unaryop`/`_eval_compare`/`_eval_call`/`_eval_attribute`/`_eval_name` 节点处理器
  - `_check_success`/`_check_failure`/`_check_always` 状态检查函数
  - `expand_matrix(matrix)` — 笛卡尔积展开
  - `substitute_matrix_vars(text, combo)` — `${{ matrix.X }}` 占位符替换
  - `matrix_suffix(combo)` — 任务名后缀 `(key1-value1)(key2-value2)`
- `tests/test_conditions.py` — 53 测试覆盖全部公共 API 与错误场景

### 修改
- `src/fcmd/executors.py`
  - `_ExecContext` 增加 `statuses: dict[str, str]` 字段
  - `_build_context` 增加 `global_statuses` 参数，为有 conditions 的任务注入 `__status__`（仅含硬依赖状态）
  - `_store_result` 同步 `ctx.statuses[spec.name] = result.status.value`
  - 4 个层运行器（Sequential/Threaded/Async/Dependency）与 `run()` 传递 `ctx.statuses`
- `src/fcmd/yaml_loader.py`
  - 模块 docstring 增补 `if`/`matrix` schema 文档
  - 导入 `fcmd.conditions` 的 5 个公共 API
  - 新增 `_parse_matrix`/`_substitute` 辅助函数
  - `_build_specs` 支持 matrix 展开（每个组合产生独立 TaskSpec，任务名追加后缀）
  - `_build_spec` 增加 `combo` 参数，解析 `if` 字段为 `conditions` 元组
  - `_parse_cmd`/`_parse_optional_fields` 支持 matrix 变量替换
- `tests/test_yaml_loader.py`
  - 新增 `TestIfCondition`（8 测试）：success/failure/always/ctx 比较/vars 比较/逻辑组合
  - 新增 `TestMatrixExpansion`（10 测试）：单键/多键/变量替换/后缀/图级默认值
  - 新增 `TestIfWithMatrix`（2 测试）：if + matrix 组合
  - 新增 `TestMatrixExecution`（1 测试）：matrix 任务实际执行验证
  - 抽取 `_echo_cmd` 跨平台 helper（Windows 用 `cmd /c echo`，Unix 直接 `echo`）

## 关键决策与依据

1. **AST 安全求值替代 eval**：rule-11 禁用 `eval`/`exec`，采用 `ast.parse(expr, mode='eval')` + 递归节点 walker + 白名单节点类型，仅允许 BoolOp/UnaryOp/Compare/Call/Attribute/Name/Constant。

2. **`__status__` 上下文注入**：状态检查函数需访问上游任务状态。扩展 `_ExecContext` 增加 `statuses` 字段，`_build_context` 为有 conditions 的任务注入 `__status__`（仅含硬依赖状态，避免软依赖污染）。

3. **字典分发消除 PLR0911**：`_eval_node` 原有 7 个 return（>6 触发 PLR0911），重构为 `_NODE_HANDLERS` 字典分发，降至 1 个 return + 1 个 raise。

4. **lambda 适配消除 ARG001**：`_eval_name` 与 `_check_always` 不需要 context 参数，但 `_STATUS_FUNCS`/`_NODE_HANDLERS` 需统一签名。用 `lambda _ctx: ...`/`lambda n, _: ...` 适配，避免显式 `# type: ignore` 或 `# noqa`。

5. **删除不可达分支（rule-11）**：
   - `_eval_boolop` 末尾 `raise ConditionError("不支持的布尔运算")` 不可达（AST 中 BoolOp.op 仅 And/Or），改为 else 分支处理 Or
   - `_eval_name` 中 True/False/None 处理不可达（Python 3.8+ 这些是 `ast.Constant` 不是 `ast.Name`），简化为仅处理未知标识符

6. **matrix needs 不替换变量**：`needs` 字段不替换 `${{ matrix.X }}`（matrix 任务间不能直接引用，需手动指定展开后任务名）。限制在模块 docstring 文档化，避免过度设计。

7. **ruff RUF043 修复**：`pytest.raises(match=...)` 中含元字符（`()`/`.`）需 raw string 或简化匹配子串。采用简化匹配子串（如 `match="仅支持 success"` 替代 `match="仅支持 success()/failure()/always()"`），避免 `re.escape` 的视觉噪音。

## 代码实现情况

### conditions.py 核心结构

```python
# 状态检查函数映射（白名单）；_check_always 无 context 参数，用 lambda 适配签名
_STATUS_FUNCS: dict[str, Any] = {
    "success": _check_success,
    "failure": _check_failure,
    "always": lambda _ctx: _check_always(),
}

# AST 节点处理器映射（白名单）；Constant/Name 无需 context，用 lambda 适配签名
_NODE_HANDLERS: dict[type[ast.AST], Any] = {
    ast.BoolOp: _eval_boolop,
    ast.UnaryOp: _eval_unaryop,
    ast.Compare: _eval_compare,
    ast.Call: _eval_call,
    ast.Attribute: _eval_attribute,
    ast.Name: lambda n, _: _eval_name(n),
    ast.Constant: lambda n, _: n.value,
}

def _eval_node(node: ast.AST, context: Context) -> Any:
    """递归求值 AST 节点（白名单分发）。"""
    handler = _NODE_HANDLERS.get(type(node))
    if handler is None:
        raise ConditionError(f"不支持的表达式节点: {type(node).__name__}")
    return handler(node, context)
```

### executors.py 状态注入

```python
@dataclass(frozen=True)
class _ExecContext:
    context: dict[str, Any]
    report: RunReport
    on_event: EventCallback | None
    statuses: dict[str, str]  # 新增字段

def _build_context(
    spec: TaskSpec[Any],
    global_context: Mapping[str, Any],
    global_statuses: Mapping[str, str] | None = None,  # 新增参数
) -> dict[str, Any]:
    has_deps = bool(spec.depends_on) or bool(spec.soft_depends_on)
    needs_status = bool(spec.conditions) and global_statuses is not None
    if not has_deps and not needs_status:
        return {}
    ctx: dict[str, Any] = {}
    if needs_status:
        ctx["__status__"] = {
            dep: global_statuses[dep]
            for dep in spec.depends_on
            if dep in global_statuses
        }
    # ... 其余依赖注入 ...
```

### yaml_loader.py matrix 展开

```python
def _build_specs(jobs: Mapping[str, Any]) -> list[TaskSpec[Any]]:
    """从 jobs 构建 TaskSpec 列表，支持 matrix 展开。"""
    specs: list[TaskSpec[Any]] = []
    for job_id, job_data in jobs.items():
        matrix = _parse_matrix(job_data)
        if matrix is None:
            specs.append(_build_spec(job_id, job_data))
            continue
        combos = expand_matrix(matrix)
        for combo in combos:
            expanded_id = f"{job_id}{matrix_suffix(combo)}"
            specs.append(_build_spec(expanded_id, job_data, combo))
    return specs
```

## 整合优化情况

1. **字典分发模式**：`_eval_node` 与 `_STATUS_FUNCS` 均采用字典分发，消除长 if-elif 链，便于扩展新节点类型/状态函数
2. **lambda 适配签名**：统一处理器签名 `(node, context) -> Any`，无需为不需要 context 的处理器添加 `# noqa: ARG001`
3. **跨平台测试 helper**：`_echo_cmd` 抽取到模块级，避免每个测试重复 Windows/Unix 分支

## 测试验证结果

| 检查项 | 结果 |
|--------|------|
| ruff check | All checks passed |
| ruff format --check | 59 files already formatted |
| pyrefly check | 0 errors (16 suppressed, 6 warnings not shown) |
| pytest | 819 passed (P12: 745, +74) |
| 覆盖率 | 97.91%（P12: 97.80%, +0.11%） |
| conditions.py 覆盖率 | 100% |
| yaml_loader.py 覆盖率 | 100% |

新增测试分布：
- `test_conditions.py`：53 测试（状态检查 8 + 上下文访问 6 + 逻辑组合 10 + 错误场景 11 + matrix 展开 7 + 变量替换 7 + 后缀生成 4）
- `test_yaml_loader.py`：+21 测试（if 条件 8 + matrix 展开 10 + if+matrix 组合 2 + matrix 执行 1）

## 遗留事项

1. **gittool isub 未实现**：P8 移除了 `isub` 子命令（依赖 conditions 模块），P13 已实现 conditions 但 isub 语义在 `ref/pyflowx` 缺失情况下不明确，待用户确认是否需要重新实现
2. **matrix needs 不替换变量**：当前限制 matrix 任务间不能直接通过 `needs` 引用（需手动指定展开后任务名），如需复杂场景考虑编程式 API 或后续扩展
3. **if 表达式功能边界**：当前仅支持 `==`/`!=` 比较，不支持 `<`/`>`/`in`/`contains` 等；如需扩展可新增 ast.Compare 操作符处理器

## 下一轮计划

P13 完成 conditions 模块核心功能。可选方向：
1. 移植更多 CLI 工具（envdev/imagetool/pdftool/screenshot/dockercmd）—— 需 `ref/pyflowx` 源码
2. 实现 gittool isub 子命令（需用户确认语义）
3. 增强 fcmd 参数解析支持 Literal/int/union 类型
4. 扩展 if 表达式支持更多比较运算符（`<`/`>`/`in` 等）
