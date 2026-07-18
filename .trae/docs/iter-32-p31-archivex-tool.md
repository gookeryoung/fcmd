# P31 - archivex 归档解压工具

## 需求清单

- [x] 支持多格式归档解压：zip/tar/gz/bz2/xz 走标准库
- [x] 支持 7z/rar 通过外部 7z/unrar 命令解压
- [x] 提供 extract/list 两个子命令
- [x] 自动检测归档格式（按扩展名，长扩展名优先）
- [x] 默认输出目录为归档同目录的 `<stem>/` 子目录

## 迭代目标

新增 fcmd archivex 工具，提供多格式归档文件解压与内容列表能力。标准库格式（zip/tar/gz/bz2/xz）走 Python 内置模块，外部格式（7z/rar）走 `subprocess` 调用 7z/unrar 命令。

## 改动文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `src/fcmd/cli/archivex.py` | 新增 | archivex 工具主体（约 330 行）：2 子命令 + 5 公共/辅助函数 |
| `tests/test_cli_archivex.py` | 新增 | 53 测试，覆盖 7 个测试类 |
| `.trae/docs/iter-32-p31-archivex-tool.md` | 新增 | 本迭代记录 |
| `.trae/docs/iter-27-p26-profiler-porting.md` | 删除 | 清理最旧迭代记录（保留最新 5 条） |

## 关键决策与依据

### 1. 子命令设计：extract + list

**决策**：拆为两个子命令而非单命令多模式。

**依据**：
- extract 是写操作（生成文件），list 是读操作（仅打印），语义完全不同
- list 不需要 `--output` 参数；extract 不需要打印归档内容
- 参照 [packtool.py](file:///f:/Dev/fcmd/src/fcmd/cli/packtool.py) 的多子命令模式（src/deps/wheel/embed/zip/clean）

### 2. 格式检测用扩展名映射而非魔术字节

**决策**：按文件扩展名判断格式，长扩展名（`.tar.gz`/`.tar.bz2`/`.tar.xz`）优先于短扩展名（`.gz`/`.bz2`/`.xz`）匹配。

```python
_ARCHIVE_EXTS: tuple[str, ...] = (
    ".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tbz", ".tbz2", ".txz",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
)
```

**依据**：
- fcmd 工具偏向"约定优于配置"，扩展名是用户显式声明
- 魔术字节检测需读取文件头，对大归档无意义且增加 I/O
- 长扩展名优先避免 `.tar.gz` 被截为 `.gz` 误判为单文件压缩

### 3. 标准库格式优先，7z/rar 走外部命令

**决策**：
- zip → `zipfile` 模块
- tar（含 .tar.gz/.tar.bz2/.tar.xz）→ `tarfile` 模块
- 单文件 gz/bz2/xz → `gzip`/`bz2`/`lzma` 模块
- 7z → 调用 `7z` 命令
- rar → 优先 `unrar`，回退 `7z`

**依据**：
- rule-11 '优先标准库'；stdlib 不支持 7z/rar（无 LGPL/专利风险）
- 7z.exe 在 Windows 上常通过 7-Zip 安装，unrar 在 Linux/macOS 上常见
- `shutil.which(tool)` 检测命令是否存在，缺失抛 FileNotFoundError

### 4. tarfile PEP 706 兼容

**问题**：Python 3.12+ 弃用 `extractall()` 不带 `filter` 参数，会发出 DeprecationWarning。

**决策**：版本守卫加 `filter="data"`，低版本回退默认行为。

```python
if sys.version_info >= (3, 12):  # pragma: no cover
    tf.extractall(output, filter="data")
else:
    tf.extractall(output)
```

**依据**：
- `filter="data"` 是 PEP 706 推荐的安全过滤策略
- 低版本（3.8-3.11）不支持 filter 参数，需回退
- `# pragma: no cover` 标注版本守卫分支（测试环境仅 3.8）

### 5. 单文件压缩解压目标命名

**决策**：去掉压缩扩展名后缀作为解压目标文件名（如 `file.txt.gz` → `file.txt`）。

```python
def _strip_compression_ext(name: str) -> str:
    for ext in (".gz", ".bz2", ".xz"):
        if name.lower().endswith(ext):
            return name[: -len(ext)]
    return name
```

**依据**：gzip/bz2/xz 单文件压缩本身不记录原文件名（虽 gz 头有但解析复杂），用扩展名剥离最直观。

### 6. 7z 与 unrar 命令行差异

**决策**：根据工具构造不同命令行：
- `7z x <archive> -o<output> -y`：`-o` 紧贴输出目录，`-y` 自动 yes
- `unrar x <archive> <output>/ -y`：输出目录以 `/` 结尾，`-y` 自动 yes

**依据**：两者命令行语法不兼容，需分支处理。

### 7. 外部命令异常上抛，CLI 层捕获

**决策**：`extract_archive`/`list_archive` 抛 `FileNotFoundError`/`RuntimeError`，CLI 子命令层用 `except (ValueError, FileNotFoundError, RuntimeError)` 统一捕获并打印。

**依据**：保持纯函数无副作用，便于测试与复用。

## 代码实现情况

### 公共函数（5 个）

- `detect_format(filepath) -> str`：扩展名检测，返回 `zip`/`tar`/`gz`/`bz2`/`xz`/`7z`/`rar`
- `extract_archive(filepath, output) -> None`：解压归档到指定目录
- `list_archive(filepath) -> list[str]`：列出归档内容
- `_strip_compression_ext(name) -> str`：去掉 .gz/.bz2/.xz 扩展名
- `_extract_with_external(filepath, output, tool) -> None`：调用 7z/unrar 解压
- `_list_with_external(filepath, tool) -> list[str]`：调用 7z/unrar 列出内容
- `_extract_single(filepath, output, opener) -> None`：解压单文件 gz/bz2/xz

### CLI 子命令（2 个）

- `fcmd archivex extract <archive> [--output DIR]`：解压到指定目录（默认 `<stem>/`）
- `fcmd archivex list <archive>`：列出归档内容

## 整合优化情况

### 覆盖率提升过程

初版 archivex.py 覆盖率 93%，缺 6 处代码路径：
1. `detect_format` 内部 if 链最后的 else 分支（`100->86`）
2. Python 3.12+ 的 `tf.extractall(filter="data")` 分支（line 189）
3. `extract_archive` 的 fall-through 分支（`200->exit`）
4. `_list_with_external` 失败抛 RuntimeError（line 237）
5. `list_archive` 的 rar 分支（lines 278-280）
6. CLI 空归档输出"（空归档）"（lines 328-329）

修复：
- **代码重构**（1-3）：`detect_format` 内部 if 改为 elif 链 + 显式 `else: return "rar"`；py3.12 守卫加 `# pragma: no cover`；`extract_archive` 加显式 `else: raise RuntimeError(...)  # pragma: no cover`
- **补充测试**（4-6）：
  - `test_external_failure`（TestListArchive）：mock subprocess.run 返回 returncode=1
  - `test_rar_mocked`（TestListArchive）：mock unrar 命令列出 rar 内容
  - `test_list_empty_zip`（TestCLISubcommands）：创建空 zip 验证"（空归档）"输出

archivex.py 覆盖率从 93% 提升至 100%。

### 工具链问题修复

- **ruff PLR0911**：`detect_format` 7 个 return 语句 > 6 限制，加 `# noqa: PLR0911`
- **ruff ARG005**：测试中 `lambda cmd: None` 未使用 `cmd` 参数，改为 `lambda _: None`
- **pyrefly no-matching-overload**：`tarfile.open(path, mode)` 中 `mode: str` 不匹配重载，改为 `Literal["w", "w:gz", "w:bz2", "w:xz"]`

## 测试验证结果

| 门禁 | 结果 |
|------|------|
| ruff check | 0 errors |
| ruff format --check | 78 files already formatted |
| pyrefly check | 0 errors (35 suppressed, 10 warnings) |
| pytest | 1239 passed, 1 skipped, 2 deselected |
| coverage | 99.33%（≥99.31% 基线），archivex.py 100% |

测试套件结构（53 测试，7 类）：
- `TestRegistration`（2）：工具注册与子命令结构
- `TestDetectFormat`（2）：15 参数化格式 + 不支持扩展名
- `TestStripExt`（2）：6 参数化剥离 + 无匹配
- `TestExtractArchive`（10）：zip/tar/tar.gz/gz/bz2/xz/不支持/7z mock/rar mock/未找到/失败
- `TestListArchive`（8）：zip/tar/gz/7z mock/不支持/未找到/失败/rar mock
- `TestCLISubcommands`（9）：extract 默认输出/指定输出/不存在/不支持/外部失败 + list zip/不存在/不支持/空归档

## 遗留事项

无。所有功能、边界场景、错误分支均已覆盖。

## 下一轮计划

P31 archivex 工具已完成。后续可继续：
- 扫描 `.trae/req/` 处理未完成需求
- 移植 tomltool TOML 读写工具
- 增强现有工具（如 archivex 增加 create 子命令创建归档）
