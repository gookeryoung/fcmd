# P28: 新增 zipencrypt 工具

## 需求清单

- [x] P28a: 移植 bitool_office 的 zipencrypt 到 fcmd，作为单命令工具
- [x] P28b: 按优先级使用 7z/zip/rar 外部工具加密，无工具时回退到 stdlib zipfile（无加密）
- [x] P28c: 跳过隐藏目录（`.`/`__` 前缀）和已有压缩包文件（避免自引用）
- [x] P28d: 支持 `--replace` 覆盖已有 ZIP
- [x] P28e: 全套门禁通过，覆盖率不低于基线

## 迭代目标

从 bitool_office 移植 ZIP 加密工具到 fcmd，遵循 fcmd 的单命令 `@fcmd.tool` 模式（参考 writefile），使用 `fcmd.models.run_command` 封装外部命令调用。

## 改动文件清单

| 文件 | 改动 |
|------|------|
| `src/fcmd/cli/zipencrypt.py` | 新增：ZIP 加密工具（76 行），含 `_get_valid_entries`/`_detect_encrypt_tool`/`_build_encrypt_cmd`/`_create_unencrypted_zip`/`_make_archive`/`zip_encrypt` |
| `tests/test_cli_zipencrypt.py` | 新增：29 个测试，覆盖注册/过滤/检测/命令构造/zipfile 回退/加密成功/跳过/覆盖/失败/端到端 |

## 关键决策与依据

1. **单命令工具模式**：zipencrypt 只有一个功能（加密），使用 `@fcmd.tool("zipencrypt")` 无 subcommand，位置参数 `directory password`（参考 writefile 的位置参数模式）。纯单命令工具（注册表中只有 `None` 子命令）在 `run_tool` 中跳过子命令匹配，所有 argv 直接传给 parser。

2. **外部工具优先级 7z > zip > rar**：7z 支持 AES256 加密（`-mem=AES256`），zip 用 `-P` 标准加密，rar 用 `-p` + `-m5`。通过 `shutil.which` 检测可用性。

3. **stdlib zipfile 回退**：无外部工具时用 `zipfile.ZipFile` 创建无加密 ZIP（标准库不支持加密），作为尽力而为的回退方案。

4. **`_ARCHIVE_EXTS` 过滤**：`_get_valid_entries` 跳过 `.zip`/`.rar`/`.7z`/`.tar`/`.gz`/`.tgz`/`.bz2` 文件，避免对已有压缩包再压缩。关键修复：若不过滤，`a.zip` 会被当作源文件处理，其目标路径 `a.zip`（stem `a` + `.zip`）与自身冲突。

5. **跳过隐藏目录但保留点文件**：目录名以 `.` 或 `__` 开头的跳过（`.git`/`__pycache__`），但文件（含 `.env` 等点文件）不受此限制——加密场景下用户通常需要加密配置文件。

6. **从 `fcmd.models` 导入 `run_command`**：与 imagetool/pdftool 一致，使用简化的 `subprocess.run` 包装（返回 `CommandResult`），而非 `fcmd.command.run_command`（接受 `TaskSpec`）。

## 代码实现情况

- `zip_encrypt(directory, password, replace=False)`：主入口，校验输入 → 检测工具 → 遍历条目 → 逐个加密
- `_get_valid_entries(dirpath)`：过滤压缩包文件 + 隐藏目录
- `_detect_encrypt_tool()`：`shutil.which` 检测 7z/zip/rar
- `_build_encrypt_cmd(filepath, target_path, password, tool)`：按工具类型构造命令
- `_create_unencrypted_zip(filepath, target_path)`：zipfile 回退
- `_make_archive(filepath, password, tool, replace)`：单条目加密，处理跳过/覆盖/失败

## 测试验证结果

- 29 个测试全部通过（7 个测试类）
- ruff check: 0 错误
- ruff format --check: 通过
- pyrefly check: 0 错误
- pytest: 1115 passed, 1 skipped, 2 deselected
- coverage: 99.27%（zipencrypt.py 99%，仅剩 `69->exit` 不可达分支——filepath 既非文件也非目录的边缘情况）

## 遗留事项

- `69->exit` 分支（filepath 既非文件也非目录）不可达，不添加 `# pragma: no cover`（会排除周围语句），与 P25 的 coverage.py 分支跟踪怪癖一致
- 无新遗留事项

## 下一轮计划

- 检查 `.trae/req/` 是否有未完成需求
- 考虑其他新功能开发或现有工具增强
