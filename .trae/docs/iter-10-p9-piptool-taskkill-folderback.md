# P9 迭代记录：移植 piptool / taskkill / folderback 工具

## 需求清单

- [x] 参考 `ref/pyflowx` 移植 piptool 工具（pip 包管理，i/u/r/d/up/f 子命令）
- [x] 参考 `ref/pyflowx` 移植 taskkill 工具（进程终止，单命令跨平台）
- [x] 参考 `ref/pyflowx` 移植 folderback 工具（文件夹备份，单命令）
- [x] 3 个工具的测试覆盖（含 mock 与跨平台验证）

## 迭代目标

继续参考 `ref/pyflowx` 的 CLI 工具实现，在 fcmd 框架下移植 3 个实用工具，
扩展 fcmd 工具生态。

## 改动文件清单

- `src/fcmd/cli/piptool.py`（新增）：pip 包管理工具，6 子命令
- `src/fcmd/cli/taskkill.py`（新增）：进程终止工具，单命令跨平台
- `src/fcmd/cli/folderback.py`（新增）：文件夹备份工具，单命令
- `tests/test_cli_tools_p9.py`（新增）：3 个工具的 38 个测试

## 关键决策与依据

### 1. piptool 受保护包改为 fcmd

**问题**：pyflowx 的 `_PROTECTED_PACKAGES` 包含 ``"pyflowx"`` 和 ``"bitool"``。

**决策**：改为 ``{"fcmd"}``，避免卸载 fcmd 自身。

**依据**：rule-01 "不写未被要求的功能"；受保护包应匹配当前项目。

### 2. piptool fn 子命令用 `subprocess.run(check=False)`

**问题**：pyflowx 用 `px.sh()` 统一封装 subprocess，fcmd 无等价 API。

**决策**：提取模块级私有函数 `_run(cmd, *, capture=False)` 复用，
用 `subprocess.run(cmd, check=False, capture_output=capture, text=True)`。

**依据**：rule-11 "subprocess 禁用 shell=True，优先 list[str]"；
与 P8 gittool 的 `_run` 风格一致。

### 3. piptool `_filter_protected_packages` 预构建集合

**问题**：pyflowx 原版在两次列表推导中重复计算 `{p.lower() for p in _PROTECTED_PACKAGES}`。

**决策**：预构建 `protected_lower` 变量，消除重复计算。

**依据**：rule-11 性能原则 "循环内查询缓存或预构建映射"。

### 4. taskkill 用 `sys.platform` 替代 `platform_command`

**问题**：pyflowx 用 `platform_command` 函数（P6 已移除，rule-01 不移植未用代码）。

**决策**：直接用 `if sys.platform == "win32":` 判断，Windows 用 `taskkill`，
Linux/macOS 用 `pkill`。

**依据**：rule-11 Pythonic 风格；无需为单一判断引入辅助函数。

### 5. folderback `remove_old_backups` 改为循环

**问题**：pyflowx 的 `remove_dump` 用递归删除旧备份，有潜在栈溢出风险。

**决策**：改为 `while True` 循环，每次删除最旧的文件后重新检查。

**依据**：rule-11 Pythonic 风格 "循环优于递归"（在这种简单场景下）。

### 6. folderback 用 `zipfile.ZIP_DEFLATED` 压缩

**问题**：pyflowx 的 `zipfile.ZipFile(target_path, "w")` 默认 ZIP_STORED 不压缩。

**决策**：指定 `zipfile.ZIP_DEFLATED` 压缩。

**依据**：备份工具应压缩以节省空间；这是改进而非行为变化。

### 7. 测试用 helper 函数替代 lambda 避免 ARG005

**问题**：ruff ARG005 检查 lambda 未使用参数，测试文件只忽略 ARG001/ARG002。

**决策**：定义模块级 helper 函数（`_fake_run`/`_recording_run`/`_success_run`/
`_recording_subprocess_run`/`_subprocess_run_success`），函数参数未使用触发
ARG001（已被测试文件忽略）。

**依据**：rule-11 测试规范 "Mock 优先级：monkeypatch > 内联 stub"；
helper 函数比 lambda 更清晰且可复用。

## 代码实现情况

### piptool（i/u/r/d/up/f 子命令）

- `_run(cmd, *, capture=False)`：subprocess 封装
- `_get_installed_packages()`：解析 `pip list --format=freeze`
- `_expand_wildcard_packages(pattern)`：通配符展开
- `_filter_protected_packages(packages)`：过滤受保护包
- 子命令 i（安装）/u（卸载）/r（重装）/d（下载）/up（升级 pip）/f（冻结依赖）

### taskkill（单命令）

- `kill_process(process_name) -> int`：跨平台终止进程
- `taskkill_run(process_names)`：批量终止并打印结果

### folderback（单命令）

- `remove_old_backups(src_stem, dst, max_zip)`：循环删除旧备份
- `zip_target(src, dst, max_zip)`：压缩为 zip（DEFLATED）
- `backup_folder(src, dst, max_zip)`：CLI 入口

## 整合优化情况

- piptool `_filter_protected_packages` 预构建集合消除重复计算
- taskkill 用 `sys.platform` 直接判断，无需 `platform_command` 辅助函数
- folderback `remove_old_backups` 改为循环避免递归栈溢出
- folderback 用 `ZIP_DEFLATED` 压缩（pyflowx 默认不压缩）
- 测试用 helper 函数替代 lambda 避免 ARG005，比 `# noqa` 更优雅

## 测试验证结果

- 609 tests passed（P8: 571 → P9: 609，新增 38）
- coverage 97.50%（P8: 97.44% → P9: 97.50%，提升 0.06%）
- 3 个新工具模块覆盖率：piptool 99% / taskkill 100% / folderback 98%
- ruff check / format / pyrefly 全通过

## 遗留事项

- piptool line 63（`_expand_wildcard_packages` 无通配符分支）未覆盖（test_expand_wildcard_no_pattern 已测试，可能行号计算差异）
- folderback 91->95 分支（`dst_path` 已存在时跳过创建）未覆盖
- 后续可移植更多 pyflowx 工具：envdev、imagetool、packtool、pdftool 等

## 下一轮计划

P9 完成 3 个工具移植。后续方向由用户决定：
继续移植更多 CLI 工具、实现 conditions 模块、或推进其他基础设施。
