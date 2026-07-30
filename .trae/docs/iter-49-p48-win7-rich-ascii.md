# iter-49 P48 Win7 rich 乱码修复

## 需求清单

- [x] 修复 Win7 cmd.exe 下 rich 输出 box-drawing 字符显示为方块/乱码的问题

## 迭代目标

R1：定位 P47 修复宽度问题后仍残留的乱码根因，并实施 ASCII 强制输出兼容。

## 根因分析

P47（iter-48）通过 `legacy_windows=True` + `width=cols-2` 修复了 Win7 conhost 下
rich 表格右边框超出窗口的问题，但用户反馈"依然没有解决 win7 下 rich 在控制台打印
乱码问题"。重新分析根因：

1. **Win7 conhost 默认使用点阵字体（Raster Fonts）**：不支持 box-drawing 字符
   （圆角边框 `╭─╮`、阴影线 `░▒▓`、双线 `═╣` 等），渲染为方块/乱码。
2. **rich 的 `ascii_only` 自动推断依赖 `sys.stdout.encoding`**：
   - rich 在初始化时检查 `sys.stdout.encoding`，若非 utf* 系列（如 cp936/gbk）
     则启用 `ascii_only=True`，把 box 字符替换为 ASCII（`+`/`-`/`|`）。
   - 但 Python 3.6+ PEP 528 改用 `_WindowsConsoleIO` + `WriteConsoleW`，
     使 `sys.stdout.encoding` 在 Windows 下默认为 `'utf-8'`（无论代码页是 936
     还是 65001）。
   - 导致 rich 误判以为可输出 Unicode box 字符，实际被点阵字体渲染为乱码。
3. **P47 仅修宽度，未触及字符集**：`legacy_windows=True` 只切颜色路径
   （`SetConsoleTextAttribute` + `file.write`），不影响 box 字符替换逻辑。

## 改动文件清单

- src/fcmd/console.py：
  - 模块 docstring 新增「Win7/8 乱码修复」段说明点阵字体限制与 PEP 528 误判
  - `get_console()` 在 Win7/8 分支额外传 `ascii_only=True`，强制 rich 用 ASCII
    box 字符
  - `get_console()` docstring 重写，分项说明 `legacy_windows`/`ascii_only`/`width`
    三个参数的传入依据
- tests/test_console.py：
  - 模块 docstring 同步更新（"ASCII 强制逻辑"）
  - `test_legacy_windows_passes_width_and_legacy_flag`：新增 `ascii_only=True`
    断言
  - `test_legacy_windows_min_width_floor`：新增 `ascii_only=True` 断言
  - `test_legacy_windows_oserror_omits_width`：新增 `ascii_only=True` 断言
    （验证 OSError 路径下 ascii_only 仍传入，仅 width 缺省）
  - `test_modern_windows_no_extra_kwargs`：docstring 同步提及 `ascii_only`，
    断言 `captured == {}` 保持 Win10+ 默认行为
- .trae/docs/iter-44-p43-coverage-blindspots.md：删除（保持迭代记录数 ≤ 5）

## 关键决策与依据

1. **`ascii_only=True` 而非 `safe_box=False`**：
   - `safe_box=False` 仅禁用 box 字符的安全替换回退（`ROUNDED→SQUARE→ASCII`），
     反而会让原始 Unicode 圆角字符直接输出，在点阵字体下显示为方块。
   - `ascii_only=True` 在 rich 渲染管线早期阶段就把所有非 ASCII 字符替换为
     ASCII 等价物，覆盖 box 字符、emoji、Unicode 装饰符等全部场景。
2. **不依赖 `sys.stdout.encoding` 自动推断**：PEP 528 使其默认 'utf-8'，
   推断结果不可靠；显式 `ascii_only=True` 绕过自动检测。
3. **不强制 `color_system=None`**：颜色仍走 `SetConsoleTextAttribute`（Win7
   conhost 支持的 16 色），保留表格着色效果；乱码仅源于字符集，不源于颜色。
4. **Win10+ 不传 `ascii_only`**：现代 conhost + TrueType 字体支持完整 Unicode
   box 字符，强制 ASCII 会降低视觉质量。
5. **不引入 `FCMD_COLUMNS`/`FCMD_ASCII` 环境变量**：当前修复覆盖 Win7/8 全部
   场景，无需 escape hatch；若用户实机验证后仍有特殊需求再引入。

## 测试验证结果

- `make check` 全套门禁通过：
  - ruff check：All checks passed
  - ruff format：150 files already formatted
  - pyrefly：0 errors (42 suppressed)
  - pytest：2293 passed, 3 skipped, 2 deselected
  - coverage：99.08%（≥95% 门禁），console.py 100%
- tests/test_console.py：12 passed（含 4 个 ascii_only 断言）

## 遗留事项

- 待用户在真实 Win7 cmd.exe 环境下验证修复效果（当前仅通过 mock 测试覆盖
  逻辑分支，未在 Win7 实机验证）。若点阵字体下仍有方块，可考虑：
  1. 进一步检查用户是否手动切换到 TrueType 字体（cmd.exe 属性 → 字体 →
     Consolas/新宋体），根治字体层问题；
  2. 引入 `FCMD_NO_ASCII` 环境变量让用户在已切 TrueType 字体时禁用强制 ASCII。

## 下一轮计划

根据用户 Win7 实机验证反馈决定：
- 若修复生效，回归常规迭代；
- 若仍乱码，排查是否为颜色序列残留或 Python stdout 编码被外部工具（如
  ConEmu/wrapping pyinstaller）改写。
