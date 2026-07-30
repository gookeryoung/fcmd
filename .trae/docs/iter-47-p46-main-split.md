# iter-47 P46 main.py 拆类

## 需求清单

- [x] 评估 FcmdApp 类（~1035 行）的拆分可行性并实施提取
- [x] 将 shell 补全脚本生成器提取到独立模块
- [x] 将 profiler 辅助函数提取到独立模块
- [x] _BUILTIN_COMMANDS 移至 _common.py 单一处定义

## 迭代目标

R1：缩小 main.py 与 FcmdApp 类体积——提取两个内聚代码块到独立私有模块，
FcmdApp 保留路由分发与依赖 self 的工具方法。

## 改动文件清单

- src/fcmd/cli/_common.py：新增 _BUILTIN_COMMANDS（从 main.py 迁入），加入 __all__
- src/fcmd/cli/_completion_scripts.py（新增）：3 个 shell 补全脚本生成器
  - gen_bash_script / gen_zsh_script / gen_fish_script（纯函数，依赖 _BUILTIN_COMMANDS）
- src/fcmd/cli/_profiler_helpers.py（新增）：3 个 profiler 辅助函数
  - inject_run_hook / run_target_script / output_profile（原 FcmdApp 静态/实例方法）
- src/fcmd/cli/main.py：
  - 删除内联 _BUILTIN_COMMANDS 定义，改从 _common 导入
  - 删除 _gen_bash_script / _gen_zsh_script / _gen_fish_script 三个静态方法（~120 行）
  - 删除 _inject_run_hook / _run_target_script / _output_profile 三个方法（~115 行）
  - _builtin_completion / _builtin_profiler 改调模块函数
  - 文件 1139 → 906 行；FcmdApp 类 ~1035 → ~800 行
- tests/test_cli_profiler.py：7 个调用点改调 _profiler_helpers 模块函数，移除未用 FcmdApp 实例
- .trae/docs/iter-42-p41-xmltool-xml.md：删除（保持迭代记录数 ≤ 5）

## 关键决策与依据

1. **不拆 _builtin_xxx 分发器本身**：每个 _builtin_xxx 依赖 self._resolve_tool /
   self._print_unknown_tool / self._aliases_for / self._tool_description /
   self._load_tool_subs 等 app 级方法（这些方法直接读写 _TOOL_ALIASES /
   _TOOL_MODULES 模块状态）。提取 builtin 分发器需传递 app 或复制状态，
   违反 rule-01「不过早抽象」。保留为 FcmdApp 实例方法，内聚于路由层。
2. **提取 completion 脚本生成器（3 个静态方法）**：纯函数，仅依赖 _BUILTIN_COMMANDS
   与入参 tools_data，无 self 依赖。提取后无任何测试调用点变化（仅经 app.run()
   间接调用），改动零回归。
3. **提取 profiler 辅助函数（3 个方法）**：_inject_run_hook 实为无 self 依赖的
   实例方法（原始签名冗余）；_run_target_script / _output_profile 已是 @staticmethod。
   三者无 _TOOL_ALIASES / _TOOL_MODULES 访问，提取零耦合。
4. **_BUILTIN_COMMANDS 迁入 _common.py**：原在 main.py 内联，新模块
   _completion_scripts.py 需访问 → 直接导入 main.py 会循环导入。迁入 _common.py
   （CLI 共享常量逻辑归宿），main.py 反向导入保持向后兼容（test_cli_profiler.py
   仍可 `from fcmd.cli.main import _BUILTIN_COMMANDS`）。
5. **不保留 FcmdApp 薄包装方法**：rule-01「禁预留扩展点」——为已迁移方法保留 1 行
   delegate 会成为死代码（仅服务测试 API）。直接更新 test_cli_profiler.py 7 个调用点
   改调模块函数（inject_run_hook / output_profile / run_target_script），消除间接层。
6. **__all__ 排序按 ruff RUF022 isort 风格**：大写优先 → 下划线前缀 → 小写。
   _common.py: ["IGNORE_DIRS", "IGNORE_EXT", "_BUILTIN_COMMANDS", "run_tool_main"]

## 代码实现情况

- _completion_scripts.py（60 行）：3 个生成函数，docstring 保留原说明
- _profiler_helpers.py（71 行）：3 个辅助函数，inject_run_hook 不再冗余 self 参数
- main.py 改为路由 + builtin 分发骨架：
  - 导入：from fcmd.cli._common import _BUILTIN_COMMANDS
         from fcmd.cli._completion_scripts import gen_bash_script, gen_fish_script, gen_zsh_script
         from fcmd.cli._profiler_helpers import inject_run_hook, output_profile, run_target_script
  - _builtin_completion 内 3 处 self._gen_xxx_script → gen_xxx_script
  - _builtin_profiler 内 self._inject_run_hook → inject_run_hook 等 3 处

## 测试验证结果

- 针对性运行（test_cli.py + test_cli_profiler.py）：189 passed
- `make check` 全套通过：
  - ruff check All checks passed + format 148 files already formatted
  - pyrefly 0 errors (42 suppressed, 10 warnings not shown)
  - 2281 passed, 3 skipped, 2 deselected
- 覆盖率：99.07%（与 iter-46 持平）
- 模块覆盖率：
  - _common.py: 100%
  - _completion_scripts.py: 100%
  - _profiler_helpers.py: 98%（63->exit, 99->101 两处 partial branch，与原 FcmdApp 静态方法时期一致）
  - main.py: 100%
- Stmts 6519 → 6528（+9：3 个新模块的纯函数 stmts 略多于 main.py 中删除的方法 stmts，因每个函数自带 docstring + __all__）

## 遗留事项

- _profiler_helpers.py 两处 partial branch（63->exit / 99->101）与原 main.py 时期
  的 partial 一致——属正常 hook 还原与浏览器打开的 try/except 路径，不补测
- main.py FcmdApp 类仍 ~800 行，剩余 _builtin_xxx 分发器内聚于路由层不再拆分
  （依赖 self 方法），未来若新增 builtin 可继续按「_builtin_xxx 留在 main.py，
  纯辅助函数提模块」的模式扩展

## 下一轮计划

无明确下一轮方向。main.py 拆类已实现可提取部分（纯函数 + 静态方法），
剩余分发器为路由层内聚代码。后续可考虑：
- _info_overview / _info_tool / _info_subcommand 是否提取为 InfoRenderer 类（有 rich Table 渲染逻辑内聚）
- _builtin_doctor 的检查项构造提取为 _check_items() 辅助
但这些都是小幅整理，非必要。
