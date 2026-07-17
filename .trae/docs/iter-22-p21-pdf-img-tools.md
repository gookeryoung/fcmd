# P21: 移植 imagetool / pdftool + 修复 verbose 回调 Unicode 编码

## 需求清单

- [x] P21a: 移植 imagetool（11 子命令，依赖 Pillow）
- [x] P21b: 移植 pdftool（15 子命令，依赖 PyMuPDF + pypdf）
- [x] P21c: pyproject.toml 增加 img/pdf/ocr/office 可选依赖 extras
- [x] P21d: 修复 verbose 回调 Unicode 字符在 Windows GBK 终端崩溃

## 迭代目标

响应 `req-01-功能需求.md` 第 1 项"增加可选依赖 fcmd[pdf]、fcmd[img]等，继续移植 imgtool, pdftool 等工具"。
从 pyflowx 移植两个基于可选依赖的工具，并修复移植过程中发现的 verbose 回调编码 bug。

## 改动文件清单

| 文件 | 改动 |
|------|------|
| `pyproject.toml` | 新增 `[project.optional-dependencies]` img/pdf/ocr/office/lint/test/dev extras |
| `src/fcmd/cli/imagetool.py` | 新建：11 子命令（r/c/ro/fl/cv/wm/cp/i/e/hi/co），基于 Pillow |
| `src/fcmd/cli/pdftool.py` | 新建：15 子命令（m/s/c/e/d/xt/xi/w/r/crop/i/ocr/reorder/img/repair），基于 PyMuPDF + pypdf |
| `src/fcmd/executors.py` | 修复 verbose 回调 Unicode 字符 `▸✓✗○` → ASCII `>/OK/X/-` |
| `src/fcmd/command.py` | 同上修复 `▸` → `>` |
| `tests/test_cli_tools_p21.py` | 新建：77 测试（注册验证 + 辅助函数 + 子命令 + guard return + 边界分支） |

## 关键决策与依据

### 1. fcmd CLI 参数映射规则适配

fcmd `@fx.tool` 的参数映射与 pyflowx 有差异，移植时做了以下适配：

- **bool 默认值反转**：fcmd 不支持 `bool=True` 的取反（无 `--no-XXX`），将 `keep_ratio: bool = True` 反转为 `stretch: bool = False`，`show: bool = True` 反转为 `hide: bool = False`
- **无短选项别名**：fcmd `@fx.tool` 不支持 `-o` 短选项，统一用 `--output-path`
- **无默认值参数为 positional**：`width: int`（无默认值）映射为位置参数，不是 `--width`
- **`int | None` 不自动转 int**：PEP 604 注解在 Python 3.8 下 `get_type_hints` 失败，保留字符串形式，fcmd 无法识别为 int 类型

### 2. 修复 fitz rotate bug

pyflowx 原始 `pdf_add_watermark` 使用 `rotate=45`，但 fitz `insert_text` 的 rotate 参数仅接受 0/90/180/270。
移植时修正为 `rotate=0`（水平水印）。

### 3. 修复 verbose 回调 Unicode 编码 bug

**问题**：`executors.py` 的 `_verbose_callback` 和 `command.py` 使用 `▸✓✗○` 等 Unicode 字符。
在 Windows GBK 终端下，rich 的 `LegacyWindowsTerm` 写入这些字符时抛 `UnicodeEncodeError`，
导致 `test_image_crop` 单独运行（`-s` 不捕获输出）时崩溃。

**修复**：将 Unicode 字符替换为 ASCII 等效字符（`>` / `OK` / `X` / `-`）。
ASCII 字符在所有编码下都安全，视觉差异最小。

### 4. pdftool 补充 `@fcmd.tool` 装饰器

pyflowx 原始 `pdf_reorder` 函数缺少 `@px.tool` 装饰器，导致无法通过 CLI 调用。
移植时补充 `@fcmd.tool("pdftool", subcommand="reorder", help="重排 PDF 页面")`。

### 5. 测试中文字体问题

fitz 默认字体不支持中文文本提取，测试 PDF fixture 改用 ASCII 内容（`"Page one content"`），
避免提取出乱码 `'·····\n\n\n·····'`。

### 6. Python 3.8 兼容性

- `dict[int, object]` 在 Python 3.8 运行时不可下标化（PEP 585 需 3.9+），测试中改用 `typing.Dict[int, object]`
- `list[str]` / `Path | None` 等注解通过 `from __future__ import annotations` 延迟求值

### 7. pdf_ocr pragma: no cover

`pdf_ocr` 子命令需要系统级 `tesseract` 可执行文件，测试环境不可用，标记 `# pragma: no cover`。
ImportError 分支同理标记。

## 代码实现情况

### imagetool.py（11 子命令）

| 子命令 | 函数 | 功能 |
|--------|------|------|
| r | `image_resize` | 调整尺寸（等比/拉伸） |
| c | `image_crop` | 裁剪矩形 |
| ro | `image_rotate` | 旋转 |
| fl | `image_flip` | 翻转（水平/垂直） |
| cv | `image_convert` | 格式转换 |
| wm | `image_watermark` | 文字水印 |
| cp | `image_compress` | 压缩 |
| i | `image_info` | 查看信息（文本/JSON） |
| e | `image_exif` | 读取/修改 EXIF |
| hi | `image_histogram` | 颜色直方图 |
| co | `image_colors` | 提取主色调 |

### pdftool.py（15 子命令）

| 子命令 | 函数 | 依赖 | 功能 |
|--------|------|------|------|
| m | `pdf_merge` | pypdf | 合并 |
| s | `pdf_split` | pypdf | 拆分单页 |
| c | `pdf_compress` | pymupdf | 压缩 |
| e | `pdf_encrypt` | pypdf | 加密 |
| d | `pdf_decrypt` | pypdf | 解密 |
| xt | `pdf_extract_text` | pymupdf | 提取文本 |
| xi | `pdf_extract_images` | pymupdf | 提取图片 |
| w | `pdf_add_watermark` | pymupdf | 添加水印 |
| r | `pdf_rotate` | pymupdf | 旋转页面 |
| crop | `pdf_crop` | pymupdf | 裁剪边距 |
| i | `pdf_info` | pymupdf | 查看信息 |
| ocr | `pdf_ocr` | pytesseract+pymupdf | OCR 识别 |
| reorder | `pdf_reorder` | pypdf | 重排页面 |
| img | `pdf_to_images` | pymupdf | 转图片 |
| repair | `pdf_repair` | pymupdf | 修复 |

## 测试验证结果

### 测试新增

| 类别 | 数量 | 覆盖目标 |
|------|------|---------|
| TestRegistration | 3 | 工具注册验证 |
| TestImagetoolHelpers | 9 | 私有辅助函数 |
| TestImagetoolCommands | 16 | imagetool 子命令（含 no_pil guard） |
| TestPdftoolCommands | 18 | pdftool 子命令（含 no_pymupdf/no_pypdf guard） |
| TestNoDepsGuards | 20 | parametrized guard return 覆盖 |
| TestEdgeCases | 7 | 边界分支（缺文件/无密码/越界/灰度图/exif 数据等） |
| test_optional_deps_available | 1 | 环境验证 |
| **合计** | **77** | |

### 门禁结果

| 门禁 | 结果 |
|------|------|
| ruff check | All checks passed |
| ruff format --check | 68 files already formatted |
| pyrefly check | 0 errors (27 suppressed) |
| pytest | 997 passed, 2 deselected |
| coverage | **99.17%**（≥ 99.07% 上次值） |

### 文件覆盖率

| 文件 | 覆盖率 |
|------|--------|
| `src/fcmd/cli/imagetool.py` | **100%** |
| `src/fcmd/cli/pdftool.py` | **100%** |
| `src/fcmd/executors.py` | 99% |

## 遗留事项

1. **`int | None` 注解不自动转 int**：fcmd 的 `_resolve_hints` 在 Python 3.8 下无法 eval PEP 604 注解，导致 `int | None` 参数通过 CLI 传入时保持字符串类型。影响 `image_resize --height` 等。未来可增强 `_resolve_hints` 解析 `X | None` 语法。
2. **fcmd 不支持 `bool=True` 取反**：需用反转语义（`stretch=False` 替代 `keep_ratio=True`）。未来可增加 `--no-XXX` 支持。
3. **pdf_ocr 需系统级 tesseract**：测试环境不可用，已 pragma: no cover。

## 下一轮计划

- 检查 `.trae/req/` 是否有未完成需求
- 考虑增强 `_resolve_hints` 支持 `X | None` 注解解析
- 或继续移植其他 pyflowx 工具
