# iter-45 P44 死代码清理与覆盖率补测

## 需求清单

- [x] 补测 timetool 282-284 未覆盖行（convert --from-tz 无效时区分支）
- [x] 删除 randtool 两处不可达 raise（死代码）

## 迭代目标

R1：清理 P1-P3 阶段工具的剩余覆盖率盲区与死代码。timetool 96%（282-284）、randtool 96%（84, 154）均已达 ≥95% 阈值，但存在真实分支未覆盖与死代码两类问题，本轮一并处理。

## 改动文件清单

- src/fcmd/cli/randtool.py：删除 generate_password 第 82-84 行 `if not full_pool: raise` 与 generate_string 第 152-154 行 `if not pool: raise` 两处死代码；同步两函数 docstring Raises 说明
- tests/test_cli_timetool.py：新增 `test_convert_invalid_from_tz` 用例

## 关键决策与依据

1. **randtool 两处 raise 为死代码**：
   - `generate_password`：`pools = [_LOWERCASE, _UPPERCASE, _DIGITS]` 三个模块级常量（均来自 `string.xxx`，恒非空），`symbols=True` 时追加 `_SYMBOLS`。`full_pool = "".join(pools)` 恒非空，`if not full_pool: raise` 不可达。
   - `generate_string`：`pool = chars if chars else (string.ascii_letters + string.digits)`。当 `chars=""`（falsy）走 else 分支，`pool = ascii_letters + digits` 恒非空；`if not pool: raise` 不可达。
   - 按 memory 约定"Remove unreachable guard code — rule-11 requires clean code without unreachable sections"，删除两处死代码而非用 `# pragma: no cover` 掩盖。
2. **docstring 与实现不一致**：`generate_string` 原 docstring 写"chars 为空串（显式传入空串）时抛 ValueError"——实际实现 `chars=""` 走 fallback 不抛错。删除死代码时同步修正 docstring Raises 为"`length` 小于等于 0 时"。
3. **timetool 282-284 为真实分支**：`convert_cmd` 的 `--from-tz` 参数无效时区名时，`_resolve_tz` 抛 ValueError 被 except 捕获打印错误。现有测试只覆盖 `--from-tz UTC`（有效），缺一个无效 from_tz 用例。补 `test_convert_invalid_from_tz` 用 `Invalid/Zone`（跨平台确定无效），触发 282-284。
4. **main() 入口不重复补测**：与上轮一致，randtool/timetool 的 `main()` 由 test_cli.py:1381 `test_tool_main_delegates_run_tool_main` 集中参数化覆盖，单模块测试文件不重复。

## 代码实现情况

- randtool `generate_password`：删除 `if not full_pool: raise ValueError("字符集为空（无法生成密码）")`，保留 `full_pool = "".join(pools)`（后续 `secrets.choice(full_pool)` 使用）
- randtool `generate_string`：删除 `if not pool: raise ValueError("字符集不能为空")`
- 两函数 docstring Raises 同步精简
- timetool `TestTimetoolCLI.test_convert_invalid_from_tz`：`convert 12:30:00 UTC --from-tz Invalid/Zone` 断言输出含"无效或不可用的时区"

## 测试验证结果

- 针对性运行（randtool+timetool）：73 passed, 3 skipped（Asia/Shanghai 不可用跳过），randtool 98% / timetool 97%（单模块运行 main() 未覆盖系未触发 test_cli.py）
- `make check` 全套通过：lint（ruff check + format）+ typecheck（pyrefly 0 errors, 42 suppressed）+ 2281 passed, 3 skipped, 2 deselected
- 总覆盖率 98.98% → **99.06%**（+0.08%）
- randtool.py / timetool.py 均达 100% 覆盖
- Stmts 6504 → 6500（删除 4 行死代码）

## 遗留事项

- 无明确遗留。所有 CLI 工具模块覆盖率已达 100% 或 ≥95%（仅 console.py 92%、dag.py 96%、context.py 98% 等框架核心模块略低，属可接受范围）。

## 下一轮计划

无明确下一轮方向。覆盖率优化已接近上限（99.06%），可转向新需求或其他质量维度。
