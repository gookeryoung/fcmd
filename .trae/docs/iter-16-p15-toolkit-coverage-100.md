# P15: toolkit.py 覆盖率提升至 100%

## 需求清单

- [x] 修复 toolkit.py 既有未覆盖分支（P14 遗留事项）
- [x] 为 build_tool_graph 公共 API 补充测试
- [x] 简化 Literal 空选择死分支（rule-11 清理不可达代码）

## 迭代目标

P14 完成后 toolkit.py 覆盖率为 98%，存在多处未覆盖分支（578-579 / 632->636 / 672-684 / 695-696 / 490->492 / 453->466 / 464->466 / 500->502）。本次迭代通过补充测试覆盖可测分支，并简化 Literal 空选择死分支，使 toolkit.py 达到 100% 覆盖率。

## 改动文件清单

- `src/fcmd/apis/toolkit.py`：简化 `_add_optional_arg`/`_add_positional_arg` 的 Literal 分支（删除 `if choices:` 死分支）
- `tests/test_toolkit.py`：新增导入 `build_tool_graph`/`FcmdError`，添加 10 个测试覆盖未测分支

## 关键决策与依据

### 1. build_tool_graph 公共 API 必须测试（P15a）

`build_tool_graph` 是 `__all__` 导出的公共 API，但 P14 前完全未测试（672-684 全部未覆盖）。补充 4 个测试：
- `test_build_tool_graph_unknown_tool`：未注册工具抛 FcmdError
- `test_build_tool_graph_unknown_subcommand`：未注册子命令抛 FcmdError
- `test_build_tool_graph_all_subcommands`：target=None 返回全部子命令（含 hidden）
- `test_build_tool_graph_target_with_deps`：target 指定子命令返回依赖链

验证通过 `len(graph)` 和 `"name" in graph` 断言图结构。

### 2. _print_subcommands 无可见子命令分支（P15b）

工具仅有 hidden 子命令且无 argv 时，run_tool 走 else 分支调用 `_print_subcommands`，后者检测 `visible` 为空时打印"无可见子命令"（695-696）。用 `capsys` 捕获输出验证。

### 3. run_tool 混合 None 单命令 + 命名子命令（P15c）

run_tool 第三个分支 `elif None in subs:`（578-579）触发条件：
- 第一个条件 `None in subs and len(subs) == 1` 为 False（len > 1，即同时有 None 和命名子命令）
- 第二个条件 `argv and not argv[0].startswith("-")` 为 False（argv 为空或以 `-` 开头）
- 第三个条件 `None in subs` 为 True

补充 2 个测试：argv 以 `-` 开头 / argv 为空。

### 4. TaskFailedError report=None 分支（P15d）

`run` 实际抛出 TaskFailedError 时总带 `report=ctx.report`，但 TaskFailedError 是公共类，report 默认 None。monkeypatch run 抛 `TaskFailedError(report=None)` 触发 `if e.report is not None:` 的 false 分支（632->636 跳转）。

### 5. list 非标准内部类型分支（P15e）

`_add_positional_arg`/`_add_optional_arg` 的 list 分支中，inner 类型不是 Path/int/float/str 时跳过 type 设置（490->492）。用 `List[bool]` 触发：bool 不在已知类型中，argparse 默认按 str 解析。

### 6. 简化 Literal 空选择死分支（P15f）

`_is_literal_annotation` 返回 True 当且仅当 `annotation.__origin__ is typing.Literal`。对于 `typing.Literal[X]`，`__args__` 一定存在且非空（`Literal[]` 和 `Literal[()]` 是无效语法）。因此 `if choices:` 的 false 分支是不可达死分支，按 rule-11 "Remove unreachable guard code" 删除。

简化前后对比：
```python
# 简化前
elif _is_literal_annotation(annotation):
    choices = _literal_choices(annotation)
    if choices:  # 死分支：__args__ 一定非空
        kwargs["choices"] = list(choices)

# 简化后
elif _is_literal_annotation(annotation):
    # _is_literal_annotation 为 True 时 __args__ 一定存在且非空
    kwargs["choices"] = list(_literal_choices(annotation))
```

`_literal_choices` 保留 `getattr(annotation, "__args__", ())` 防御性默认值（作为独立辅助函数的健壮性），但调用处已通过 `_is_literal_annotation` 保证 `__args__` 存在。

## 代码实现情况

### toolkit.py 改动

`_add_optional_arg` Literal 分支（451-453）：
```python
elif _is_literal_annotation(annotation):
    # _is_literal_annotation 为 True 时 __args__ 一定存在且非空
    kwargs["choices"] = list(_literal_choices(annotation))
```

`_add_positional_arg` Literal 分支（496-499）：
```python
elif _is_literal_annotation(annotation):
    # _is_literal_annotation 为 True 时 __args__ 一定存在且非空
    kwargs = {"help": pname, "choices": list(_literal_choices(annotation))}
    parser.add_argument(pname, **kwargs)
```

### test_toolkit.py 新增测试

10 个测试分 5 组：
- P15a: build_tool_graph（4 个）
- P15b: 无可见子命令（1 个）
- P15c: 混合 None + 命名子命令（2 个）
- P15d: TaskFailedError report=None（1 个）
- P15e: list 非标准内部类型（2 个）

## 整合优化情况

- 删除 Literal 空选择死分支，toolkit.py 语句数从 309 降至 304，分支数从 143 降至 139
- 无新增重复代码

## 测试验证结果

- ruff check: All checks passed
- ruff format --check: 59 files already formatted
- pyrefly check: 0 errors (16 suppressed, 6 warnings not shown)
- pytest: 851 passed (+10 from P14's 841)
- coverage: 98.18% (+0.27% from P14's 97.91%)
- toolkit.py: 100% coverage (from P14's 98%)

## 遗留事项

无。toolkit.py 已达 100% 覆盖率，所有公共 API 均有测试覆盖。

## 下一轮计划

P15 完成 toolkit.py 覆盖率提升。可选方向：
1. 实现 gittool isub 子命令（需用户确认语义）
2. 移植更多 CLI 工具（envdev/imagetool/pdftool/screenshot/dockercmd - 需 ref/pyflowx 源码）
3. 提升其他模块覆盖率（executors.py 94% / dag.py 96% / cli/main.py 99%）
