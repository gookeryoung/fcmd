# iter-50 P49 移除 rich 依赖，自实现轻量 Console + Table

## 需求清单

- [x] 移除 rich 依赖，用轻量自实现替代
- [x] 保持 get_console() / print_verbose() 接口兼容
- [x] 保持 markup 语法兼容（[cyan]/[red]/[bold]/[bold cyan] 等）
- [x] 保持 Table API 兼容（add_column/add_row + style/justify/no_wrap）
- [x] Win7/8 conhost 兼容（SetConsoleTextAttribute 16色 + ASCII 边框）
- [x] 测试覆盖率 ≥ 95%，0 lint/type 错误

## 迭代目标

移除 rich 依赖（pyproject.toml 的 `rich>=13.0.0`），用自实现轻量 Console + Table
替代，覆盖项目内全部 60+ 处 markup 调用点与 8 处 Table 调用点，零功能回归。

## 根因分析

P47/P48 通过 ``legacy_windows=True`` + ``ascii_only=True`` + ``_AsciiBoxStream``
三层兜底修复 Win7 下 rich box-drawing 字符乱码。但 rich 本身是重量级依赖
（冷启动开销、Win7 兼容性 hacks 复杂），且项目仅使用其 Table + markup 子集
（未用 Panel/Progress/Tree/Markdown/Columns/Traceback），自实现更轻量。

## 改动文件清单

- `src/fcmd/console.py`：完全重写，自实现 Console + Table + markup 解析
- `src/fcmd/cli/main.py`：6 处 `from rich.table import Table` → `from fcmd.console import Table`；`_list_tools` 移除 Panel/Text，用 console.print 直接输出
- `src/fcmd/apis/toolkit.py`：2 处 `from rich.table import Table` → `from fcmd.console import Table`
- `src/fcmd/executors.py`：更新 docstring 注释（移除 "rich" 字样）
- `pyproject.toml`：移除 `rich>=13.0.0` 依赖
- `tests/test_console.py`：完全重写，移除 _AsciiBoxStream/inspect.signature 测试，新增 Console/Table/markup/ANSI/Win16/Legacy 测试
- `tests/test_cli.py`：更新注释（rich Table → Table）
- `tests/test_command.py`：更新注释（rich console → console）

## 关键设计与依据

### Console API 设计

- `Console.print(*args, **kwargs)`：支持 `end`/`sep`/`style` 关键字，其他 rich
  关键字（highlight/justify/soft_wrap/overflow/no_wrap）签名兼容但忽略。
- `Console._out` 属性动态解析 `sys.stdout`：未显式传 `file=` 时每次 print 取
  `sys.stdout`，确保 pytest capsys 等运行时替换 stdout 的场景能正确捕获输出
  （关键修复：构造时捕获会导致 capsys 失效，80 个测试失败）。
- 颜色策略：非 tty → 纯文本无颜色码；非 Windows tty → ANSI 转义；Win10+ tty
  → 启用 VT 处理后 ANSI；Win7/8 → ctypes SetConsoleTextAttribute 16色。

### Table 渲染

- ASCII 边框（`+`/`-`/`|`），`box=None` 无边框对齐输出。
- 中文宽度对齐：`unicodedata.east_asian_width` 判断 W/A 字符占 2 列。
- markup 标签不影响宽度计算：`_strip_markup` 去标签后计算可见宽度。

### markup 解析

- 栈模型：开标签压入样式集合，闭标签弹出，当前样式 = 栈中所有集合并集。
- 支持 `[cyan]`/`[red]`/`[bold]`/`[bold cyan]`/`[/]` 等。
- 闭标签按栈顺序弹（不按名称精确匹配），对成对使用场景足够。

### Win7/8 兼容性

- 移除 rich 后 box-drawing 乱码问题自动消失（自实现 Table 仅用 ASCII）。
- 保留 `_is_legacy_windows` 用于颜色路径切换（VT vs SetConsoleTextAttribute）。
- `_styles_to_win_attr` 颜色位替换保留强度位：`(attr & 8) | color`，避免 bold
  + color 组合时颜色覆盖强度位。

## 测试验证结果

- `uv run ruff check src tests`：0 错误
- `uv run ruff format --check src tests`：150 文件已格式化
- `uv run pyrefly check`：0 错误（42 suppressed）
- `uv run pytest -m "not slow" --cov=fcmd --cov-fail-under=95`：2340 passed，
  3 skipped，覆盖率 98.85%

## 遗留事项

- console.py 覆盖率 93%（部分 Win7/8 legacy 路径与 VT 启用分支在非 Windows 测试
  环境下难覆盖），通过 mock 测试覆盖核心逻辑。
- Table 的 `show_lines`/`no_wrap` 参数签名兼容但当前忽略（项目内未使用）。

## 下一轮计划

无（本次迭代目标全部达成，等待用户新需求）。
