# P39 - 测试文件结构重组

## 需求清单

- [x] 把聚合测试文件 `test_cli_tools.py` / `test_cli_tools_pXX.py`（7 个）拆分为单工具测试文件
- [x] 重命名 `test_pymake_tool.py` → `test_cli_pymake.py`，命名风格统一
- [x] 全套门禁通过（ruff/pyrefly/pytest/coverage ≥ 95%）

## 迭代目标

测试文件命名不符合最佳实践：
1. `test_cli_tools_pXX.py` 用迭代阶段编号命名（P8/P9/P10/P11/P17/P21/P22），迭代编号是过程信息不属于代码组织
2. 每个聚合文件混合多个工具的测试类，不利于按工具定位
3. `test_pymake_tool.py` 命名风格与既有 `test_cli_<工具名>.py` 不一致

按「每工具一文件」原则重组，命名统一为 `test_cli_<工具名>.py`，与已有 `test_cli_archivex.py` / `test_cli_csvtool.py` 等 10 个单工具文件保持一致。

## 改动文件清单

### 删除（9 个）

- `tests/test_cli_tools.py`（hashfile/filedate/writefile/folderzip）
- `tests/test_cli_tools_p8.py`（which/sysinfo/portcheck/gittool）
- `tests/test_cli_tools_p9.py`（piptool/taskkill/folderback）
- `tests/test_cli_tools_p10.py`（filelevel/bumpversion/autofmt）
- `tests/test_cli_tools_p11.py`（clr/packtool）
- `tests/test_cli_tools_p17.py`（setenv/reseticoncache/sshcopyid/screenshot/envdev/gittool isub）
- `tests/test_cli_tools_p21.py`（imagetool/pdftool）
- `tests/test_cli_tools_p22.py`（dockercmd/lscalc）
- `tests/test_pymake_tool.py`（pymake）

### 新增（26 个）

| 工具名 | 来源 | 文件 |
|--------|------|------|
| hashfile | test_cli_tools.py | test_cli_hashfile.py |
| filedate | test_cli_tools.py | test_cli_filedate.py |
| writefile | test_cli_tools.py | test_cli_writefile.py |
| folderzip | test_cli_tools.py | test_cli_folderzip.py |
| which | P8 | test_cli_which.py |
| sysinfo | P8 | test_cli_sysinfo.py |
| portcheck | P8 | test_cli_portcheck.py |
| gittool | P8 + P17 isub 合并 | test_cli_gittool.py |
| piptool | P9 | test_cli_piptool.py |
| taskkill | P9 | test_cli_taskkill.py |
| folderback | P9 | test_cli_folderback.py |
| filelevel | P10 | test_cli_filelevel.py |
| bumpversion | P10 | test_cli_bumpversion.py |
| autofmt | P10 | test_cli_autofmt.py |
| clr | P11 | test_cli_clr.py |
| packtool | P11 | test_cli_packtool.py |
| setenv | P17 | test_cli_setenv.py |
| reseticoncache | P17 | test_cli_reseticoncache.py |
| sshcopyid | P17 | test_cli_sshcopyid.py |
| screenshot | P17 | test_cli_screenshot.py |
| envdev | P17 | test_cli_envdev.py |
| imagetool | P21 | test_cli_imagetool.py |
| pdftool | P21 | test_cli_pdftool.py |
| dockercmd | P22 | test_cli_dockercmd.py |
| lscalc | P22 | test_cli_lscalc.py |
| pymake | test_pymake_tool.py 重命名 | test_cli_pymake.py |

### 清理

- 删除 `iter-35-p34-pathtool-tool.md`（iter 文件超 5，清理最旧）

## 关键决策与依据

### 1. 每工具一文件而非按主题分组
- **依据**：与既有 10 个 `test_cli_<工具名>.py` 文件保持一致；按工具定位测试更直观；符合 pytest 最佳实践
- **替代方案**：保留聚合仅去 `_pXX` 后缀、按主题分组——均被否决（前者未解决「多工具混在一个文件」问题，后者分组依据主观）

### 2. 不提取共享辅助到 conftest.py
- **依据**：rule-01「三处相似才考虑提取，不过早抽象」。当前 stub 辅助（`_make_result` / `_stub_success` 等）仅在 dockercmd/lscalc 两个文件中使用，未达 3 处阈值。各文件保留自己的辅助副本
- **替代方案**：抽到 `tests/conftest.py` 或 `tests/_helpers.py`——均被否决（过早抽象）

### 3. TestToolsRegistration 元组收窄
- **问题**：原 `test_all_tools_registered` 在每个聚合文件中循环断言所有工具，拆分后单工具文件只 import 自己的模块，其他工具未注册，断言必失败
- **解决**：每个单工具文件保留 `test_all_tools_registered` 方法但元组收窄为单元素（如 `("hashfile",)`），断言逻辑与错误消息格式保持原样
- **依据**：保留测试方法名一致性 + 单工具隔离，避免依赖其他模块导入

### 4. gittool 合并到单文件
- **问题**：P8 的 gittool 与 P17 的 gittool isub 在两个聚合文件中
- **解决**：合并到 `test_cli_gittool.py`，包含 TestGittool / TestGittoolCmdSpecs（来自 P8）+ TestGittoolIsub（来自 P17）
- **依据**：单工具的测试集中到一个文件，便于维护

### 5. test_pymake_tool.py → test_cli_pymake.py
- **依据**：统一命名为 `test_cli_<工具名>.py` 风格；仅更新顶部 docstring 首行描述，测试内容不变

### 6. 并行 subagent 拆分
- **依据**：26 个文件拆分工作量较大，按源文件分 5 个并行 subagent 处理避免主上下文膨胀
- **冲突避免**：subagent 5 单独处理 gittool 合并（从 P8 + P17 提取），其他 subagent 跳过 gittool

## 代码实现情况

### 拆分原则（所有 subagent 共同遵守）

1. 读取源文件全部内容
2. 识别属于每个工具的所有 `class Test*`（按工具名前缀识别）
3. 裁剪 imports：只保留该工具实际使用的符号
4. 保留 `import fcmd.cli.<tool_module>` 行
5. 不修改测试逻辑（断言、参数、测试函数体原样保留）
6. 每个目标文件顶部写中文 docstring 说明该工具测试范围
7. Python 3.8 兼容：`from __future__ import annotations`
8. 行宽 120，ruff 默认双引号

### 各文件结构（统一模式）

```python
"""<工具名> 工具测试。

验证 ``fcmd.cli.<工具名>`` 模块：
- 工具注册
- <功能1>
- <功能2>
"""

from __future__ import annotations

# 标准库 imports
# 第三方 imports（pytest 等）
# fcmd imports

# 辅助函数（如有）

class TestToolsRegistration:
    """<工具名> 工具注册验证。"""
    # ...

class Test<功能>:
    # ...
```

## 测试验证结果

```
uv run ruff check src tests           # All checks passed!
uv run ruff format --check src tests  # 106 files already formatted
uv run pyrefly check                  # 0 errors (35 suppressed)
uv run pytest -m "not slow" --cov=fcmd --cov-fail-under=95
# 1480 passed, 1 skipped, 2 deselected
# Total coverage: 99.39%
```

### 测试数量对比

- 拆分前：1468 passed
- 拆分后：1480 passed（+12）
- 增加 12 个测试来自：每个单工具文件的 `TestToolsRegistration` 保留了 `test_all_tools_registered`（元组收窄为单工具），相当于每个工具多出 1 个注册断言

### 覆盖率对比

- 拆分前：99.39%
- 拆分后：99.39%（无回归）

## 整合优化情况

- 26 个新文件命名统一为 `test_cli_<工具名>.py`，与既有 10 个单工具文件风格一致
- 测试逻辑零修改，所有断言、参数、fixture 原样保留
- 无新风险引入，无重复代码（辅助函数副本仅在 2 处使用，未达提取阈值）
- gittool 测试集中到一个文件，便于后续维护

## 遗留事项

- 无。CLI 工具测试结构统一为 `test_cli_<工具名>.py`，覆盖所有 26 个工具（含 builtin 命令测试 test_cli.py / test_cli_profiler.py）

## 下一轮计划

- 框架核心测试（test_command.py / test_task.py 等共 12 个）已按 `test_<模块名>.py` 命名，无需调整
- 测试结构重组完成，可继续推进 pyflowx 未迁移模块（核心 API 层 + pypack 子包）或新功能开发
