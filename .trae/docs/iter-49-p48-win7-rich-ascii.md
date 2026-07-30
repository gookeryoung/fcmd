# iter-49 P48 Win7 rich 乱码修复（含旧版 rich 兼容）

## 需求清单

- [x] 修复 Win7 cmd.exe 下 rich 输出 box-drawing 字符显示为方块/乱码的问题
- [x] 兼容旧版 rich（13.x 早期，无 ``ascii_only`` 参数）的 Win7 + Python 3.8 环境

## 迭代目标

R1：定位 P47 修复宽度问题后仍残留的乱码根因，并实施 ASCII 强制输出兼容。
R2：解决 R1 修复在用户 Win7 实机触发的 ``TypeError: Console.__init__()
got an unexpected keyword argument 'ascii_only'`` 崩溃。

## 根因分析

### R1：乱码根因

P47（iter-48）通过 ``legacy_windows=True`` + ``width=cols-2`` 修复了 Win7 conhost
下 rich 表格右边框超出窗口的问题，但用户反馈"依然没有解决 win7 下 rich 在控制台
打印乱码问题"。重新分析根因：

1. **Win7 conhost 默认使用点阵字体（Raster Fonts）**：不支持 box-drawing 字符
   （圆角边框 ``╭─╮``、阴影线 ``░▒▓``、双线 ``═╣`` 等），渲染为方块/乱码。
2. **rich 的 ``ascii_only`` 自动推断依赖 ``sys.stdout.encoding``**：
   - rich 在初始化时检查 ``sys.stdout.encoding``，若非 utf* 系列（如 cp936/gbk）
     则启用 ``ascii_only=True``，把 box 字符替换为 ASCII（``+``/``-``/``|``）。
   - 但 Python 3.6+ PEP 528 改用 ``_WindowsConsoleIO`` + ``WriteConsoleW``，
     使 ``sys.stdout.encoding`` 在 Windows 下默认为 ``'utf-8'``（无论代码页是 936
     还是 65001）。
   - 导致 rich 误判以为可输出 Unicode box 字符，实际被点阵字体渲染为乱码。
3. **P47 仅修宽度，未触及字符集**：``legacy_windows=True`` 只切颜色路径
   （``SetConsoleTextAttribute`` + ``file.write``），不影响 box 字符替换逻辑。

### R2：旧版 rich 兼容性崩溃

R1 修复直接传 ``ascii_only=True``，用户 Win7 实机验证时触发：

```
TypeError: Console.__init__() got an unexpected keyword argument 'ascii_only'
```

根因：用户 Win7 + Python 3.8 环境下 pip 通常只能装到 rich 13.x 早期版本
（13.0~13.4），``ascii_only`` 参数在 rich 13.x 中后期才引入。本地开发环境
rich 15.0.0 支持，但用户实机不支持，直接传参崩溃。

## 改动文件清单

- src/fcmd/console.py：
  - 模块 docstring 新增「Win7/8 乱码修复」与「旧版 rich 兼容」两段
  - 新增 ``_force_ascii_box()``：monkey-patch ``rich.box`` 模块所有 Box 实例
    常量（ROUNDED/SQUARE/HEAVY/DOUBLE 等）为 ``box.ASCII``
  - ``get_console()`` 用 ``inspect.signature(Console.__init__)`` 检测是否支持
    ``ascii_only`` 参数：支持则传参；不支持则调用 ``_force_ascii_box()`` 降级
  - ``get_console()`` docstring 重写，分项说明三个参数的传入依据与降级路径
- tests/test_console.py：
  - 模块 docstring 同步更新
  - 新增 ``_make_fake_console`` / ``_make_sig`` 两个模块级 helper：构造假
    Console 与可控签名，覆盖两条分支
  - ``TestGetConsoleLegacyWindows`` 4 个测试 mock ``inspect.signature`` 返回
    含 ``ascii_only`` 的签名，断言 kwargs 含 ``ascii_only=True``
  - 新增 ``test_legacy_windows_old_rich_falls_back_to_force_ascii_box``：
    mock 签名不含 ``ascii_only``，断言 ``_force_ascii_box`` 被调用 + kwargs
    不含 ``ascii_only``
  - 新增 ``TestForceAsciiBox``：测试 ``_force_ascii_box`` 把所有 Box 常量替换
    为 ``box.ASCII``，并在 finally 中恢复原始值避免污染其他测试
- .trae/docs/iter-44-p43-coverage-blindspots.md：删除（保持迭代记录数 ≤ 5）

## 关键决策与依据

1. **``inspect.signature`` 检测而非 try/except TypeError**：
   - try/except 会在 ``Console(**kwargs)`` 实际构造时崩溃，可能留下半初始化
     状态；signature 检测在构造前判断，干净。
   - signature 检测开销极低（微秒级），仅 Win7/8 路径触发，不影响冷启动。
2. **降级用 ``_force_ascii_box`` monkey-patch ``rich.box`` 模块常量**：
   - rich 15.0.0 的 box 是模块级 Box 实例常量（``ROUNDED``/``SQUARE``/``HEAVY``
     等），不是 ``BOXES`` 字典（初版假设错误，已修正）。
   - 用 ``dir(box)`` + ``isinstance(value, box.Box)`` 遍历所有 Box 实例，
     ``setattr(box, name, box.ASCII)`` 全部替换为 ASCII。
   - 副作用：影响当前进程内所有 rich Table/Panel 渲染。fcmd 单进程场景下可接受，
     且仅 Win7/8 + 旧版 rich 触发，影响范围可控。
3. **不强制 ``safe_box=False``**：``safe_box=False`` 仅禁用 box 字符的安全替换
   回退（``ROUNDED→SQUARE→ASCII``），反而会让原始 Unicode 圆角字符直接输出。
4. **不依赖 ``sys.stdout.encoding`` 自动推断**：PEP 528 使其默认 'utf-8'，
   推断结果不可靠；显式 ``ascii_only=True`` 或 monkey-patch box 绕过自动检测。
5. **Win10+ 不传 ``ascii_only``**：现代 conhost + TrueType 字体支持完整 Unicode
   box 字符，强制 ASCII 会降低视觉质量。

## 测试验证结果

- ``make check`` 全套门禁通过：
  - ruff check：All checks passed
  - ruff format：150 files already formatted
  - pyrefly：0 errors (42 suppressed)
  - pytest：2295 passed, 3 skipped, 2 deselected
  - coverage：99.08%（≥95% 门禁），console.py 100%
- tests/test_console.py：14 passed（含旧版 rich 降级分支 + ``_force_ascii_box`` 测试）

## 遗留事项

- 待用户在真实 Win7 cmd.exe 环境下验证修复效果（当前仅通过 mock 测试覆盖
  逻辑分支，未在 Win7 实机验证）。若点阵字体下仍有方块，可考虑：
  1. 进一步检查用户是否手动切换到 TrueType 字体（cmd.exe 属性 → 字体 →
     Consolas/新宋体），根治字体层问题；
  2. 引入 ``FCMD_NO_ASCII`` 环境变量让用户在已切 TrueType 字体时禁用强制 ASCII。

## 下一轮计划

根据用户 Win7 实机验证反馈决定：
- 若修复生效，回归常规迭代；
- 若仍乱码，排查是否为颜色序列残留或 Python stdout 编码被外部工具（如
  ConEmu/wrapping pyinstaller）改写。
