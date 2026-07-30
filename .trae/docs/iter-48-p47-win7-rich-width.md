# iter-48 P47 Win7 rich 显示宽度修复

## 需求清单

- [x] 分析 Win7 cmd.exe 下 rich 表格右边框超出窗口的根因
- [x] 修复 console.py 使 Win7/8 下表格渲染不再超出终端窗口

## 迭代目标

R1：定位 Win7 conhost 下 rich 表格宽度超出根因，并实施兼容性修复。

## 根因分析

Win7 cmd.exe（conhost）不支持 ANSI VT 序列，rich 15.0.0 通过
`detect_legacy_windows()` 检测到 `vt=False`，自动进入 legacy 模式：

- 颜色：`SetConsoleTextAttribute` + `file.write()` 纯文本输出（非 VT 序列）
- 宽度：`os.get_terminal_size().columns - 1`（减 1 规避 conhost 最后一列自动换行）
- box：`LEGACY_WINDOWS_SUBSTITUTIONS` 把 `ROUNDED` 替换为 `SQUARE`（Unicode
  box-drawing 字符）；当 `encoding` 非 utf（GBK 代码页）时 `ascii_only=True`，
  进一步替换为 `ASCII`（`+--+-+|`）

**超出根因**：Win7 conhost 在最后一列写入字符时触发自动换行的边界行为，
使 rich 减 1 的余量在部分场景（窗口宽度变化、box 字符宽度计算、光标移动
组合）仍不足以容纳表格右边框，导致右边框被推到下一行或超出窗口可视区域。

## 改动文件清单

- src/fcmd/console.py：
  - 新增 `_is_legacy_windows()`：通过 `sys.getwindowsversion().major < 10`
    检测 Win7/8（Win10+ major=10）
  - `get_console()` 在 Win7/8 下显式传 `legacy_windows=True` 和
    `width=os.get_terminal_size(1).columns - 2`：显式传 width 时 rich 的
    `size` 属性直接用 `_width`（不再减 legacy_windows），故传入 cols-2
    等价于默认 cols-1 再额外让 1 列给 conhost 余量（总余量 2 列）
  - `os.get_terminal_size(1)` 失败（stdout 重定向/IDE 管道）时不传 width，
    由 rich 自行 fallback 到 80，保持非交互环境兼容
- tests/test_console.py（新增）：12 个测试覆盖
  - `TestIsLegacyWindows`（6）：非 Windows / Win7 / Win8 / Win10 / Win11 /
    `getwindowsversion` 缺失的安全降级
  - `TestGetConsoleCache`（1）：多次调用返回同一缓存实例
  - `TestGetConsoleLegacyWindows`（4）：Win7 传 width=cols-2 / 极窄终端
    下限保护（cols=2 → width=1）/ OSError 时省略 width / Win10+ 不传额外参数
  - `TestPrintVerbose`（1）：委托 Console.print
- .trae/docs/iter-43-p42-yamtool-yaml.md：删除（保持迭代记录数 ≤ 5）

## 关键决策与依据

1. **只传 width 不传 height 触发 rich 的非对称分支**：rich 的 `size` 属性
   对 `(_width, _height)` 均非 None 走减 `legacy_windows` 分支，对仅 `_width`
   非 None 走直接返回 `_width` 分支。本修复利用后者：传 `width=cols-2` 使
   `size.width = cols-2`，精确控制最终渲染宽度，不依赖 rich 内部的减 1 逻辑。
2. **不用 `COLUMNS` 环境变量**：rich 读 `COLUMNS` 后仍会减 `legacy_windows`
   导致减 2 次（rich 已知行为），且会影响其他工具的终端宽度感知，副作用大。
3. **不 monkey-patch `LEGACY_WINDOWS_SUBSTITUTIONS`**：box 字符宽度问题
   不是根因（GBK 代码页下 rich 已自动替换为 ASCII box），hack rich 内部
   表违反 rule-01「优先标准库/既有约定」，且升级 rich 时易碎。
4. **不强制 `safe_box=False`**：`safe_box=True`（默认）在 legacy windows 下
   触发 `ROUNDED→SQUARE` 替换，是 rich 对 raster 字体的兼容保护，禁用反而
   会让 Unicode 圆角字符在 Win7 点阵字体下显示为方块。
5. **Win10+ 完全不传参数**：保持默认行为，避免影响主流平台的动态宽度检测
   （用户调整窗口大小时 rich 自动跟随）。

## 测试验证结果

- `make check` 全套门禁通过：
  - ruff check / format：All checks passed
  - pyrefly：0 errors
  - pytest：2293 passed, 3 skipped, 2 deselected
  - coverage：99.09%（≥95% 门禁），console.py 100%
- tests/test_console.py：12 passed

## 遗留事项

- 待用户在真实 Win7 cmd.exe 环境下验证修复效果（当前仅通过 mock 测试覆盖
  逻辑分支，未在 Win7 实机验证）。若 cols-2 余量仍不足，可考虑引入
  `FCMD_COLUMNS` 环境变量作为 escape hatch，让用户显式指定渲染宽度。

## 下一轮计划

根据用户 Win7 实机验证反馈决定：
- 若修复生效，回归常规迭代；
- 若仍超出，引入 `FCMD_COLUMNS` 环境变量并扩展 `_list_tools`/`_info_overview`
  等关键表格的 `max_width` 显式约束。
