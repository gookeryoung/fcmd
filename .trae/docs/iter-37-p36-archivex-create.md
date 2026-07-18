# P36 - archivex create 子命令

## 需求清单

- [x] 为 archivex 新增 `create` 子命令，支持创建 zip/tar.gz/tar.bz2/tar.xz/tar/gz/bz2/xz 归档
- [x] 目录模式递归收集文件打包，应用 `IGNORE_DIRS`/`IGNORE_EXT` 忽略规则
- [x] 文件模式支持单文件压缩（gz/bz2/xz）与单文件打包（zip/tar）
- [x] 不支持创建 7z/rar（外部命令创建超范围）
- [x] 全套门禁通过（ruff/pyrefly/pytest/coverage）

## 迭代目标

为 archivex 补齐创建归档能力，与现有 extract/list 形成完整三子命令工具。沿用 P31 archivex 的 stdlib-first 策略：zip/tar/gz/bz2/xz 走标准库，7z/rar 不支持创建（与 extract 不对称——extract 调用外部 7z/unrar 解压，create 不引入外部命令创建以避免参数复杂度）。

## 改动文件清单

| 文件 | 类型 | 行数变化 | 说明 |
|------|------|---------|------|
| `src/fcmd/cli/archivex.py` | 修改 | +170 | 新增 `create_archive` 公共函数 + `_should_skip_part`/`_collect_files`/`_compress_single`/`_tar_mode_for` 辅助 + `create` CLI 子命令 |
| `tests/test_cli_archivex.py` | 修改 | +260 | 新增 `TestCreateArchive` 类（23 测试）+ `TestCLISubcommands` 追加 6 个 create CLI 测试；修改 `test_subcommands` 期望为三子命令 |

## 关键决策与依据

### 1. 不支持 7z/rar 创建
- **依据**：7z 命令创建归档支持 AES 加密、多卷分卷等复杂参数，超出了「stdlib-first + 简洁 CLI」的范围。extract 调用 7z/unrar 是只读操作（参数简单 `x -o`），create 引入外部命令会带来参数设计负担。
- **实现**：`create_archive` 在 fmt 为 "7z"/"rar" 时抛 `ValueError("create 不支持格式 7z: 仅支持 zip/tar/gz/bz2/xz")`。

### 2. `_should_skip_part` 在 archivex 内复制（不提取到 _common）
- **依据**：rule-01「三处相似才考虑提取，不过早抽象」。目前两处使用（filesearch + archivex），且函数仅 6 行，复制成本低于跨模块依赖成本。第三处出现时再提取到 `_common.py`。
- **注释**：在 docstring 中标注「与 filesearch._should_skip_part 实现一致；两处使用暂未提取到 _common」。

### 3. `_tar_mode_for` 返回 `Literal["w", "w:gz", "w:bz2", "w:xz"]`
- **依据**：pyrefly 对 `tarfile.open(path, mode)` 的重载解析要求 `mode` 为 Literal 类型，不接受 `str`（与 P31 memory 记录一致）。
- **实现**：`from typing import Literal`，函数返回类型显式标注 Literal 联合。

### 4. 单文件压缩格式（gz/bz2/xz）拒绝目录输入
- **依据**：gz/bz2/xz 是单文件压缩格式，无法打包目录。目录压缩需用 tar.gz/tar.bz2/tar.xz。
- **实现**：`if is_dir: raise ValueError("目录无法压缩为单文件格式 {fmt}: 请使用 tar.gz/tar.bz2/tar.xz")`。

### 5. 归档内文件名使用 POSIX 分隔符
- **依据**：跨平台兼容（Windows 上 `Path.relative_to` 返回 `\` 分隔符，但 zip/tar 标准使用 `/`）。
- **实现**：`zf.write(f, f.relative_to(source).as_posix())` 与 `tf.add(f, f.relative_to(source).as_posix())`。测试 `test_create_tar_gz_directory` 验证 `sub/b.txt` 而非 `sub\b.txt`。

### 6. 复用 P31 的 `detect_format` 与 `_ARCHIVE_EXTS`
- **依据**：create 与 extract/list 共用格式检测逻辑，避免重复。`detect_format(output)` 根据输出路径扩展名决定创建格式。
- **优点**：长扩展名（`.tar.gz`）优先匹配，避免被截为 `.gz`（P31 已处理的 bug）。

### 7. 往返测试验证（create → extract → 比对内容）
- **依据**：创建归档后立即用 `extract_archive` 解压并比对原始内容，端到端验证 create 实现正确性。
- **覆盖**：`test_create_zip_directory` 与 `test_create_gz_single_file` 含完整往返验证。

## 代码实现情况

### archivex.py 新增结构（170 行）

```
导入区新增：fnmatch, Literal
__all__ 新增：create_archive

辅助函数（4 个）：
  _should_skip_part(parts, ignore_dirs) -> bool    # 复制自 filesearch
  _collect_files(source, ignore_dirs=None, ignore_ext=None) -> list[Path]
  _compress_single(source, output, opener) -> None
  _tar_mode_for(name) -> Literal["w", "w:gz", "w:bz2", "w:xz"]

公共函数：
  create_archive(source, output, ignore_dirs=None, ignore_ext=None) -> None
    - 检测源存在 + 检测输出格式
    - 单文件压缩（gz/bz2/xz）：拒绝目录，调用 _compress_single
    - zip：zipfile.ZIP_DEFLATED 写入，目录模式 _collect_files
    - tar：_tar_mode_for 选模式，tarfile.open 写入
    - 7z/rar：抛 ValueError

CLI 子命令：
  @fcmd.tool("archivex", subcommand="create")
  archivex_create(source: Path, output: Path) -> None
    - 捕获 FileNotFoundError/ValueError 打印提示
```

### 测试覆盖（29 新测试）

| 类 | 测试数 | 覆盖点 |
|----|--------|--------|
| TestCreateArchive | 23 | zip/tar.gz/tar.bz2/tar.xz/tar/.tgz 目录打包 + zip/tar.gz/gz/bz2/xz 单文件 + 目录→单文件格式抛错 + 7z/rar 不支持 + 不支持扩展名 + 源不存在 + 父目录自动创建 + 默认 ignore_dirs/ignore_ext + 通配模式 *.egg-info + 自定义 ignore_dirs/ignore_ext + 空目录 + 往返验证 |
| TestCLISubcommands（追加） | 6 | create zip/tar.gz/gz CLI + 源不存在 + 不支持格式 + 目录→gz 提示 |

## 整合优化情况

- 复用 P31 的 `detect_format`/`_ARCHIVE_EXTS`/`_TAR_EXTS`，无重复格式检测逻辑。
- 复用 `_common.IGNORE_DIRS`/`IGNORE_EXT` 跨工具共享常量。
- `_should_skip_part` 与 filesearch 实现一致，注释标注「两处使用暂未提取」。
- 往返测试设计：create + extract 联合验证，确保 create 产出可被 extract 正确还原。

## 测试验证结果

| 检查项 | 结果 |
|--------|------|
| ruff check | All checks passed |
| ruff format --check | 2 files already formatted |
| pyrefly check | 0 errors (35 suppressed, 10 warnings) |
| pytest | 1424 passed, 1 skipped, 2 deselected |
| 总覆盖率 | 99.38%（≥99.37% P35 基线，提升 0.01%） |
| archivex.py 覆盖率 | 100%（194 stmts, 103 branches, 0 miss） |

### 修复过程

#### 1. pyrefly 重载不匹配：`tarfile.open(output, mode)` 
- 错误：`mode: str` 不匹配 `tarfile.open` 重载（期望 Literal）
- 依据：P31 memory 已记录「pyrefly overload resolution: `tarfile.open(path, mode)` where `mode: str` fails pyrefly's overload matching」
- 修复：`_tar_mode_for` 返回类型改为 `Literal["w", "w:gz", "w:bz2", "w:xz"]`，`from typing import Literal`

#### 2. 覆盖率 99.35% < 99.37% 基线
- 原因：`_should_skip_part` 的 line 304（通配分支 `return True`）未覆盖
- 修复：添加 `test_create_applies_ignore_dirs_glob` 测试，创建 `fcmd.egg-info` 目录触发 `*.egg-info` 通配匹配
- 结果：archivex.py 100%，总覆盖率 99.38%

## 遗留事项

- 无

## 下一轮计划

候选方向（按优先级）：
1. csvtool 新增 `merge` 子命令（多 CSV 按列合并，union/intersection 两种模式）
2. jsontool 新增 `query` 子命令（jq 简化版，支持点路径 + 过滤表达式）
3. 提取 `_should_skip_part` 到 `_common.py`（若第三处出现）
4. 增强现有工具的边界场景测试
