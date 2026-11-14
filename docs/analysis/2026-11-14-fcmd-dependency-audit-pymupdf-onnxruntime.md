# fcmd 依赖审计：PyMuPDF 与 onnxruntime

- 审计对象：`F:\Dev\fcmd`，HEAD `4dbe4e6`（2026-11-13，`v0.3.12-98-g4dbe4e6`）
- 审计日期：2026-11-14
- 关联文档：[GUI 框架选型分析](2026-11-14-fcmd-ui-framework-selection.md) §1.4 / §7
- 边界：**只读分析，仅新增本文件**。未改业务代码、未改 `pyproject.toml`、未提交。
- 并行会话：工作区已有 wslman 下载/安装链路的未提交改动（`wslmanw.py`、`_manifest_data.py`、`uv.lock`、`2026-11-13-wslman-gap-analysis.md`），本轮未触碰、未回滚、未纳入。
- 标注约定：**【实测】** 本轮在本机运行测得；**【代码】** 静态阅读；**【提交】** git 历史；**【清单】** 发布清单/已安装分发元数据。

---

## 0. 结论摘要

| 依赖 | 是否被现有功能真实使用 | 进入安装包的路径 | 结论 | 实测体积（raw / deflate） |
|---|---|---|---|---|
| **PyMuPDF** | **是**。`pdfbase.py` 有 12 个 PDF 操作直接调用 `fitz`，413 个 PDF 测试依赖它 | `pyproject.toml` 的 `pdf` extra；`office` / `full` 再引用；fspack 默认 extras 含 `pdf` | **保留**，但把约束恢复为 `pymupdf>=1.23,<2` | 1.28.2：**48.1 / 20.5 MB**；2.1：**97.8 MB** |
| **pypdf** | **是**。`pdfbase.py` 7 个操作 + `filescan` 的 PDF 文本抽取 | 同一个 `pdf` extra | **保留**，无需改动 | 6.21.2：**3.6 / 1.1 MB** |
| **onnxruntime** | **否**。全仓 0 处 import / 字符串引用 / CLI 调用 | **从未写入 `pyproject.toml`**；由 PyMuPDF 2.1 的 `Requires-Dist: onnxruntime` 被动带入 | **不要单独删**；恢复 `pymupdf<2` 后会一并消失 | 1.31.1：**45.8 / 16.4 MB** |

**一句话结论：** PyMuPDF 是真依赖，onnxruntime 是 PyMuPDF 2.x 的“搭车依赖”。把 `pdf` extra 从 `pymupdf>=1.23,<3` 恢复为 `pymupdf>=1.23,<2`，即可同时移除 onnxruntime、numpy、networkx、pymupdf-layout、pymupdf4llm、protobuf、flatbuffers、tabulate，实测少 **143.3 MB raw / 72.3 MB deflate**，真实打包的 NSIS 安装包从 **149.10 MB 降到 89.84 MB（-59.26 MB，-39.7%）**，且 413 个 PDF 测试在 1.28.2 上全过。

---

## 1. 审计口径

- **声明**：读 `pyproject.toml`、`uv.lock`、`dist/.dep_cache.json`，区分“直接声明”和“传递依赖”。
- **引用**：全仓搜 `pymupdf` / `fitz` / `onnxruntime` / `pypdf`，覆盖直接 import、`TYPE_CHECKING`、`importlib` / 字符串动态导入、`find_spec` 探测、console script 调用链。
- **归属**：读已安装分发的 `METADATA` 中的 `Requires-Dist`，并在 `dist/site-packages` 做反向依赖扫描。
- **实跑**：用隔离 venv 分别装 1.x / 2.x / 2.x-去-onnx / 无-pdf，跑同一套测试与 CLI 命令，记录真实失败点。
- **体积**：`uv pip install --target` 后统计安装目录字节；用 `System.IO.Compression.ZipFile`（Optimal/Deflate）压缩同一目录，作为进安装包的增量保守估计。

---

## 2. 声明与提交历史

### 2.1 PyMuPDF：功能引入，后来被人工锁 1.x，又被 dependabot 放宽

| 提交 | 日期 | 作者 | 对 `pyproject.toml` 的改动 | 动机 |
|---|---|---|---|---|
| `ab2284c` | 2026-07-17 | gookeryoung | **首次加入** `pdf = ["pymupdf>=1.24.11", "pypdf>=5.9.0"]` | 移植 imagetool / pdftool（15 个子命令），提交说明明确“依赖 PyMuPDF+pypdf，通过 `fcmd[pdf]` 可选依赖提供” |
| 后续若干提交 | 2026-07~09 | gookeryoung | 随 Python 3.11/3.12/3.13 升级调整下限，最终为 `>=1.23` | 兼容性维护 |
| `9c54f2b` | 2026-11-09 13:19 +0800 | gooker_young | `pymupdf>=1.23` → `pymupdf>=1.23,<2`，并加注释 | 提交说明：“pymupdf 锁 1.x”；行内注释：“pymupdf 2.x 强依赖 onnxruntime / pymupdf-layout / pymupdf4llm；本项目只用基础 PDF 读写，锁 1.x” |
| `7d66ab0` | 2026-11-09 | gooker_young | 格式化 `pyproject.toml`，**保留 `<2`，但删掉了那行解释注释** | 纯格式化 |
| `11c4ba2` | 2026-11-12 15:16 UTC | **dependabot[bot]** | `pymupdf>=1.23,<2` → **`pymupdf>=1.23,<3`** | “chore(deps-dev): update pymupdf requirement”，把版本放宽到允许 2.1 |

**根因链：** `9c54f2b` 已经用注释写明“2.x 会带 onnxruntime，所以锁 1.x”；`11c4ba2` 是 dependabot 的机械放宽，覆盖了这条人工决策。当前 `pyproject.toml:162` 为：

```toml
pdf = [ "pymupdf>=1.23,<3", "pypdf>=5.9.0" ]
```

补充证据：在 `9c54f2b` 之前，`pyproject.toml` 是 `pymupdf>=1.23`（无上限）。`28e7bd9`（2026-10-26）的 `uv.lock` 因此已经解析出 **onnxruntime 1.31.0**；`9c54f2b` 锁 `<2` 后重新生成 lock，onnxruntime 才从 lock 中消失。也就是说，**onnxruntime 在 2026-10-26 就进过一次 lock，2026-11-09 被人为清掉，2026-11-12 又被 dependabot 放回来**。

当前 `uv.lock`（HEAD 版本）为 `pymupdf 1.28.2`、约束记录 `>=1.23,<2`、**0 处 onnxruntime**。工作区的 `uv.lock` 有并行会话的未提交改动，约束记录已变成 `<3`，但 pymupdf 仍是 1.28.2、仍无 onnxruntime；本轮未触碰该文件。

### 2.2 pypdf：与 PyMuPDF 同批引入，真实使用

- `ab2284c` 与 PyMuPDF 一起加入，约束 `>=5.9.0`，至今未变。
- 用途见 §3.2：7 个 PDF 操作 + `filescan` 的 PDF 文本抽取，**与 PyMuPDF 是互补关系，不是替代关系**。

### 2.3 onnxruntime：从未被直接声明

- `grep -n "onnxruntime" pyproject.toml`：**0 处**（连注释都没有，`7d66ab0` 格式化时删掉了）。
- `git log --all -S"onnxruntime" -- uv.lock`：只有 `28e7bd9`（旧 lock，已被 `9c54f2b` 清掉）、`9c54f2b` / `7d66ab0`（注释/格式化）。
- `dist/.dep_cache.json` 的 `declared` 列表包含 `pymupdf>=1.23,<3`，**不包含 onnxruntime**；`installed.packages` 也不包含。
- PyMuPDF 2.1 的 `dist-info/METADATA` 明确写着：

```text
Requires-Dist: networkx
Requires-Dist: numpy
Requires-Dist: onnxruntime
Requires-Dist: pyyaml
Requires-Dist: pymupdf-layout==2.1
Requires-Dist: pymupdf_layout==2.1
Requires-Dist: psutil
Requires-Dist: tabulate
Requires-Dist: pymupdf4llm==2.1
```

- 对照：PyMuPDF **1.28.2 的 `METADATA` 没有任何 `Requires-Dist`**（零依赖）；`pypdf 6.21.2` 也基本零依赖。

**结论：onnxruntime 不是 fcmd 的依赖，是 PyMuPDF 2.1 的硬依赖。** 只要允许 2.x，pip/uv 就一定会装它；单独从 `dist` 里删掉，下一次打包会再装回来。

---

## 3. 运行期真实引用点

### 3.1 PyMuPDF：12 个操作，全部集中在 `pdfbase.py`

全仓 `pymupdf` / `fitz` 搜索只命中：

| 文件:行 | 性质 |
|---|---|
| [`pdfbase.py:21`](../../src/fcmd/tools/pdf/pdfbase.py#L21) | `TYPE_CHECKING` 下的 `import fitz`（不产生运行期加载） |
| [`pdfbase.py:49`](../../src/fcmd/tools/pdf/pdfbase.py#L49) | `HAS_PYMUPDF = importlib.util.find_spec("fitz") is not None` |
| [`pdfbase.py:53-57`](../../src/fcmd/tools/pdf/pdfbase.py#L53-L57) | `_require_pymupdf()`：用 `lazy_import("fitz", "PyMuPDF", "pdf", ...)` 惰性导入 |
| [`_env_helpers.py:21`](../../src/fcmd/cli/_env_helpers.py#L21) | `("pdf", "fitz", "pymupdf")`，只做 `find_spec` 探测，不 import |
| [`_deps.py:3`](../../src/fcmd/tools/_deps.py#L3) | 注释提到 PyMuPDF |

`pdfbase.py` 中 12 个操作在入口处调用 `_require_pymupdf()`，缺失时打印 `未安装 PyMuPDF 库，请安装: pip install fcmd[pdf]` 并返回：

| 操作 | 定义行 | 守卫行 | CLI 子命令 / 调用方 |
|---|---:|---:|---|
| `pdf_compress` | 157 | 159 | `pdftool c` |
| `pdf_extract_text` | 228 | 234 | `pdftool xt` |
| `pdf_extract_images` | 252 | 258 | `pdftool xi` |
| `pdf_add_watermark` | 287 | 307 | `pdftool w` |
| `pdf_rotate` | 359 | 366 | `pdftool r` |
| `pdf_crop` | 397 | 409 | `pdftool crop` |
| `pdf_info` | 463 | 465 | `pdftool i` |
| `pdf_ocr` | 505 | 518 | `pdftool ocr`（另需系统 tesseract） |
| `pdf_to_images` | 632 | 644 | `pdftool img` |
| `pdf_repair` | 675 | 677 | `pdftool repair` |
| `pdf_compare` | 752 | 758 | `pdftool compare` |
| `pdf_layout` | 804 | 815 | `pdftool layout` |

调用链：`pyproject.toml` 的 console script `pdftool` → [`pdftool.py:25`](../../src/fcmd/tools/pdf/pdftool.py#L25) 导入上述函数 → 各子命令 → `pdfbase`。窗口版 `pdfsplitw` / `pdfcryptw` 不直接 import `pdfbase`，而是走 `pdfsplit` / `pdfcrypt`，最终仍落到 `pdfbase`。`img2pdf` 不用 PyMuPDF（用 Pillow）。

**测试覆盖：**
- [`test_cli_pdftool.py:305-356`](../../tests/tools/pdf/test_cli_pdftool.py#L305-L356) 的 `TestNoDepsGuards` 用 `HAS_PYMUPDF=False` 参数化覆盖 9 个子命令；加上 `compress`（230-243）、`info`（273-284），**12 个 PyMuPDF 操作里 11 个有“缺库降级”测试**；仅 `ocr` 因依赖系统 tesseract 未列入（代码仍有守卫）。
- [`test_cli_pdftool.py:1589`](../../tests/tools/pdf/test_cli_pdftool.py#L1589) 断言 `HAS_PYMUPDF is True`，保证 PDF 测试自身不会在缺库环境里“假绿”。
- 本轮 `.venv`（pymupdf 1.28.2）实跑：**413 passed in 3.28s**。

### 3.2 pypdf：7 个操作 + filescan，不能跟着 PyMuPDF 一起删

| 操作 / 模块 | 定义行 | 守卫行 |
|---|---:|---:|
| `pdf_merge` | 79 | 87 |
| `pdf_split` | 124 | 132 |
| `pdf_encrypt` | 175 | 184 |
| `pdf_decrypt` | 201 | 210 |
| `pdf_reorder` | 582 | 594 |
| `pdf_attach_add` | 692 | 694 |
| `pdf_attach_extract` | 725 | 727 |

另外，[`filescan/_extract.py:443-445`](../../src/fcmd/apps/filescan/_extract.py#L443-L445) 的 `_extract_pdf()` 使用 pypdf 做 PDF 文本抽取；缺 pypdf 时在 [`436-440`](../../src/fcmd/apps/filescan/_extract.py#L436-L440) 抛 `KIND_UNSUPPORTED` 并附带 `PDF_MISSING_HINT`（[`79`](../../src/fcmd/apps/filescan/_extract.py#L79)）。

`pypdf` 的 7 个操作全部有 `HAS_PYPDF=False` 降级测试（[`test_cli_pdftool.py:335-356`](../../tests/tools/pdf/test_cli_pdftool.py#L335-L356)、[`test_cli_pdfsplit.py:277-281`](../../tests/tools/pdf/test_cli_pdfsplit.py#L277-L281)、[`test_cli_pdfcrypt.py:285-289`](../../tests/tools/pdf/test_cli_pdfcrypt.py#L285-L289)）。

### 3.3 onnxruntime：0 处引用

- `src/` 全仓搜索 `onnxruntime`：**0 处**。
- 没有任何 `import onnxruntime`、`importlib.import_module("onnxruntime")`、子进程调用或字符串拼接。
- `fcmd env` / `fcmd doctor` 的可选依赖清单（[`_env_helpers.py:17-22`](../../src/fcmd/cli/_env_helpers.py#L17-L22)）只列 `PIL` / `fitz` / `pypdf` / `pytesseract`，也没有 onnxruntime。
- 反向依赖扫描（`dist/site-packages`）：`onnxruntime` 只被 `pymupdf 2.1` 需要；`numpy` / `networkx` / `protobuf` / `flatbuffers` / `tabulate` 也主要或完全由 PyMuPDF 2.x 这条链带入。

---

## 4. 打包链路：为什么 `uv.lock` 是 1.28.2，发布物却是 2.1

[`packtool.py:215-223`](../../src/fcmd/tools/dev/packtool.py#L215-L223) 的 `packtool deps` 用的是：

```python
cmd = ["pip", "install", "--target", str(lib_dir), "--no-compile", "--no-warn-script-location", *packages]
```

它**不读 `uv.lock`**，而是按 `pyproject.toml` 的约束现场解析。于是：

1. `pyproject.toml:162` 写的是 `<3`；
2. `pip` 解析到 PyMuPDF 2.1；
3. 2.1 的 `Requires-Dist` 再拉 onnxruntime / numpy / networkx / pymupdf-layout / pymupdf4llm；
4. 这些文件被写进 `dist/site-packages`，最终进安装包。

而 `.venv` 和 HEAD 的 `uv.lock` 仍是 `9c54f2b` 锁定的 1.28.2，所以**开发环境干净、发布物脏**。这也是为什么“本地测试全过、安装包却多出 45.8 MB onnxruntime”。

---

## 5. 体积归因（实测）

### 5.1 当前发布物里的 PDF 相关项

| 项 | 未压缩 | 压缩（Deflate） |
|---|---:|---:|
| pymupdf 2.1 | 97.8 MB | — |
| onnxruntime 1.31.1 | 45.8 MB | **16.4 MB** |
| numpy + numpy.libs | 35.0 MB | — |
| networkx | 8.3 MB | — |
| pymupdf4llm | 1.3 MB | — |
| google/protobuf | 2.7 MB | — |
| flatbuffers / tabulate / dist-info | 约 0.5 MB | — |
| **PDF2 依赖树合计**（上述目录，排除公共的 packaging） | **191.4 MB / 1,982 文件** | **92.8 MB** |

### 5.2 切回 PyMuPDF 1.28.2 后

| 项 | 未压缩 | 压缩（Deflate） |
|---|---:|---:|
| pymupdf 1.28.2（零 `Requires-Dist`） | **48.1 MB** | **20.5 MB** |
| pypdf 6.21.2（两种方案都保留） | 3.6 MB | 1.1 MB |

**净收益：**

- raw：191.4 − 48.1 = **143.3 MB**
- deflate：92.8 − 20.5 = **72.3 MB**
- 安装包实测：149.10 → **89.84 MB（-59.26 MB，-39.7%）**；deflate 估算的 72.3 MB 偏乐观，NSIS 实际压缩率更低

> 该净收益成立的前提是 numpy / networkx / protobuf / flatbuffers / tabulate 没有其他消费者。本轮反向依赖扫描与 `src/` 全仓搜索均确认：这些库只被 PyMuPDF 2.x 这条链引用；`pymupdf4llm` / `pymupdf_layout` / `numpy` / `networkx` 在 `src/` 中也是 0 处 import。

> 说明：76.8 MB 是早期按 deflate 增量做的线性推算，偏乐观；2026-11-14 实际打包为 **89.84 MB**（见 §5.3）。raw 口径下，未压缩总量从约 342 MB 降到约 199 MB。

### 5.3 真实打包实测（2026-11-14）

| 项 | 改前（PyMuPDF 2.1） | 改后（PyMuPDF 1.28.2） | 变化 |
|---|---:|---:|---:|
| `site-packages` | 280.9 MB / 6,126 文件 | **133.6 MB / 4,179 文件** | **-147.3 MB（-52.4%）** |
| NSIS 安装包（py3.14.6） | 156,343,392 B（149.10 MB） | **94,202,043 B（89.84 MB）** | **-59.26 MB（-39.7%）** |
| pymupdf | 2.1 | 1.28.2 | major -1 |
| onnxruntime | 45.8 MB | 不存在 | 移除 |
| numpy / networkx | 35.0 / 8.3 MB | 不存在 | 移除 |

构建命令（本地 fspack 0.6.12，py3.14.6 与改前安装包同版本）：

```powershell
uvx --from fspack==0.6.12 --with pip fspack b . --target windows --py-version 3.14.6 --extra-index-url https://pypi.org/simple/
uvx --from fspack==0.6.12 --with pip fspack p . --target windows --py-version 3.14.6 --format nsis --no-build
```

产物冒烟：`dist/pdftool.exe i` / `c` 在打包后的 embed runtime 上正常工作；`dist/fcmd.exe --version` 输出 `fcmd 0.3.12`。

剩余占用 top（`dist/site-packages`）：pymupdf 46.4、PIL 15.4、cryptography 10.4、pygments 8.1、Cryptodome 6.6、lxml 6.2、win32 5.2、pythonwin 4.6、pypdf 3.7 MB。

### 5.4 如果进一步把 `pdf` extra 从默认打包中拆出

- 再省 pymupdf 1.28.2 48.1 MB raw / 20.5 MB deflate + pypdf 3.6 MB raw / 1.1 MB deflate = **51.7 MB raw / 21.6 MB deflate**。
- 按实测压缩比推算，安装包可到约 **69 MB**。
- 代价：PDF 工具与 `filescan` 的 PDF 抽取变成“按需安装/首次提示”，见 §7.3。

---

## 6. 隔离实跑记录

| 环境 | pymupdf | onnxruntime | pypdf | 命令 | 结果 |
|---|---|---|---|---|---|
| 仓库 `.venv` | 1.28.2 | **无** | 6.21.2 | `pytest tests/tools/pdf -q` | **413 passed in 3.28s**；`import fitz` 同样打印弃用警告 |
| `venv21` | 2.1 | **已卸载** | 有 | `import pymupdf; import pymupdf4llm` | 均成功；`find_spec("onnxruntime") is None` |
| `venv21` | 2.1 | **已卸载** | 有 | `pytest tests/tools/pdf -q` | **413 passed, 1 warning in 4.42s**（warning 是 `fitz` 弃用提示） |
| `venv_nopdf` | 无 | 无 | 无 | `import fcmd` | 成功，无 pdf/fitz/onnxruntime |
| `venv_nopdf` | 无 | 无 | 无 | 非 PDF 全量测试 | **6,959 passed, 75 skipped, 3 failed**（见下） |
| `venv_nopdf` | 无 | 无 | 无 | `pdftool i dummy.pdf` | `未安装 PyMuPDF 库，请安装: pip install fcmd[pdf]`，退出码 0 |
| `venv_nopdf` | 无 | 无 | 无 | `pdfsplit dummy.pdf --parts 2` | `未安装 pypdf 库，请安装: pip install fcmd[pdf]` + `错误: 缺少依赖: pypdf（pip install fcmd[pdf]）`，退出码 0 |

**3 个失败逐条定性：**

1. `tests/apps/filescan/test_extract.py::test_extract_pdf_corrupt`：期望 `KIND_CORRUPT`，实际 `KIND_UNSUPPORTED`。原因是缺 **pypdf**，`_extract_pdf` 走了“缺库降级”分支。属于测试断言未适配“pdf extra 未安装”的场景，**不是代码回归**。
2. `tests/cli/test_tools_manifest.py::test_digest_matches_sources`
3. `tests/cli/test_tools_manifest.py::test_list_rows_match_discovery`

第 2、3 条在 `.venv` 中同样失败（本轮复跑确认），是并行会话改 `_manifest_data.py` 后未重生成摘要的既有噪音，**与本次依赖审计无关**。

**关键反证：** 在 pymupdf 2.1 的 `venv21` 里卸载 onnxruntime 后，`import pymupdf`、`import pymupdf4llm`、创建/写入/保存 PDF、413 个 PDF 测试全部正常。说明 **fcmd 的 PDF 功能不依赖 onnxruntime**；onnxruntime 只服务 PyMuPDF 2.x 的 layout/LLM 能力，而 fcmd 没有使用这些能力。

---

## 7. 处理建议与代价

### 7.1 推荐（P0）：恢复 `pymupdf>=1.23,<2`，保留 `pdf` extra

```diff
-pdf = [ "pymupdf>=1.23,<3", "pypdf>=5.9.0" ]
+# pymupdf 2.x 强依赖 onnxruntime / pymupdf-layout / pymupdf4llm；本项目只用基础 PDF 读写，锁 1.x
+pdf = [ "pymupdf>=1.23,<2", "pypdf>=5.9.0" ]
```

- **代价**：一行配置 + 一条注释；HEAD 的 `uv.lock` 已经是 1.28.2 / `<2`，无需重新解析。若并行会话的 `uv.lock` 改动最终保留，需要在其提交前跑一次 `uv lock` 同步约束记录。
- **行为影响**：无。1.28.2 与 2.1 在 fcmd 用到的 API 上行为一致，413 个测试两边都过。两者都会打印 `fitz` 弃用警告（fcmd 仍写 `import fitz`），该警告与锁版本无关，后续可单独迁移到 `import pymupdf`。
- **体积收益**：raw **-143.3 MB**、deflate **-72.3 MB**、安装包实测 **149.10 → 89.84 MB（-59.26 MB）**。
- **附带收益**：onnxruntime、numpy、networkx、pymupdf-layout、pymupdf4llm、protobuf、flatbuffers、tabulate 一起消失，SBOM、杀软扫描、启动 import 开销都下降。
- **防回归**：给 dependabot 加 ignore（`pymupdf` 的 major 2），否则下一次依赖更新还会把 `<2` 改回 `<3`。

### 7.2 必须补的回归检查（P0）

现状是“`pyproject.toml` 说 `<3`，lock 说 1.28.2，dist 装 2.1”，三方不一致却没有任何检查报警。建议加一条轻量断言，放在打包/发布测试里（不改业务代码）：

```python
# 伪代码：发布物依赖策略断言
from importlib import metadata
from importlib.util import find_spec

assert int(metadata.version("pymupdf").split(".")[0]) < 2, "PyMuPDF 必须锁 1.x"
assert find_spec("onnxruntime") is None, "onnxruntime 不应进入发布物"
```

同时建议在 CI 增加：

- `uv lock --check`（确保 `pyproject.toml` 与 `uv.lock` 的约束记录一致）；
- 对 `dist/.dep_cache.json` 的 `installed.packages` 断言不含 `onnxruntime`。

### 7.3 可选（P1）：把 `pdf` extra 从默认 fspack extras 拆出

- 现状：`[tool.fspack] extras` 同时列了 `office` 和 `pdf`，而 `office` 本身又包含 `pdf`；PDF 依赖是默认必装。
- 拆出后再省 **51.7 MB raw / 21.6 MB deflate**，按实测压缩比推算安装包可到约 **69 MB**。
- 行为影响：
  - `pdftool` / `pdfsplit` / `pdfcrypt` / `img2pdf` 在缺库时已能打印友好提示并返回（实测见 §6）；
  - `filescan` 的 PDF 文本抽取会降级为 `KIND_UNSUPPORTED` 并带安装提示；
  - 需要把 `test_extract_pdf_corrupt` 改成“缺 pypdf 时 skip 或期望 `unsupported`”，否则无-pdf 环境会红。
- 这是产品决策（“安装包默认带不带 PDF”），不是纯技术问题；建议先做 §7.1，再单独评估。

### 7.4 不推荐的方案

| 方案 | 为什么不推荐 |
|---|---|
| 保留 2.1，只从 `dist` 删 onnxruntime | `Requires-Dist: onnxruntime` 是硬依赖，下次 `pip install --target` 会再装回来；且 2.1 仍带 numpy/networkx/pymupdf4llm 等 45+ MB，治标不治本 |
| 保留 2.1，用 `pip install --no-deps` 绕过 | 依赖图被人为破坏，后续任何 `pip check` / 重新安装都会失效；`pymupdf4llm` 等组件在特定路径仍可能 import onnxruntime |
| 用 pypdf 完全替代 PyMuPDF | pypdf 不具备压缩（`garbage/deflate/clean`）、文本/图片提取、水印、旋转、裁剪、渲染成图、OCR、版面调整、PDF 比较等能力；12 个操作会直接失效 |
| 把 onnxruntime 降级为可选 extra | 它不是 fcmd 的直接依赖，没有必要为它维护 extra；正确做法是不要让 PyMuPDF 2.x 进来 |

---

## 8. 需要补的测试 / 替代实现

1. **依赖策略断言**（§7.2）：`pymupdf` major < 2；发布物无 `onnxruntime`；`uv.lock` 与 `pyproject.toml` 约束一致。
2. **dependabot 配置**：忽略 `pymupdf` 的 major 2 更新，或把 `pdf` extra 标记为需要人工评审。
3. **若执行 §7.3（拆包）**：
   - 修改 `test_extract_pdf_corrupt`，在 `HAS_PYPDF=False` 时 `pytest.skip` 或断言 `KIND_UNSUPPORTED` + `PDF_MISSING_HINT`；
   - 给 `pdftool` 的 12 个操作补一条“缺库时退出码为 0 且不写输出文件”的统一断言（现有测试已覆盖 11 个，可复用参数化）。
4. **不需要补的**：PyMuPDF/pypdf 的功能实现无需替代；1.28.2 已覆盖全部现有用法。

---

## 9. 决策点

| # | 决策 | 建议 | 代价 |
|---|---|---|---|
| 1 | 是否恢复 `pymupdf<2` | **是**（P0） | 一行配置；无行为影响；-72.3 MB deflate |
| 2 | 是否给 dependabot 加 ignore | **是** | 一行配置；防止再次被放宽 |
| 3 | 是否把 `pdf` extra 从默认打包拆出 | **先不做**，等 §7.1 落地并观察一个版本 | 再省约 21.6 MB，但需要产品决策 + 测试调整 |
| 4 | 是否升级到 PyMuPDF 2.x | **暂不**；如未来需要 2.x 的 layout/LLM 能力，先迁移 `import fitz` → `import pymupdf`，再评估是否接受 onnxruntime 体积 | 代码迁移 + 45.8 MB 起 |

---

## 10. 复测命令

```powershell
# 1) 当前环境：确认 pymupdf 版本与 onnxruntime 是否存在
.\.venv\Scripts\python.exe -c "import pymupdf, importlib.util; print(pymupdf.__version__, importlib.util.find_spec('onnxruntime'))"

# 2) PDF 测试（1.28.2）
.\.venv\Scripts\python.exe -m pytest tests/tools/pdf -q

# 3) 2.1 去 onnxruntime 的隔离环境（需先创建并安装 pymupdf==2.1 / pymupdf4llm / pypdf）
& $env:TEMP\fcmd-dep-audit\venv21\Scripts\python.exe -c "import pymupdf, importlib.util; print(pymupdf.__version__, importlib.util.find_spec('onnxruntime'))"

# 4) 无 PDF 依赖环境下的 CLI 降级
& $env:TEMP\fcmd-dep-audit\venv_nopdf\Scripts\python.exe -c "import sys; sys.argv=['pdftool','i','dummy.pdf']; from fcmd.tools.pdf.pdftool import main; main()"

# 5) 依赖树体积（安装到临时目录后统计 + Deflate）
uv pip install --target $env:TEMP\pymupdf1 "pymupdf>=1.23,<2"
```

---

## 附录 A：证据索引

| 结论 | 证据 |
|---|---|
| PyMuPDF 首次引入 | `ab2284c`（2026-07-17），`pdf = ["pymupdf>=1.24.11", "pypdf>=5.9.0"]` |
| 人工锁 1.x 的理由 | `9c54f2b`（2026-11-09），注释点名 onnxruntime / pymupdf-layout / pymupdf4llm |
| 约束被放宽 | `11c4ba2`（2026-11-12，dependabot），`<2` → `<3` |
| 当前约束 | [`pyproject.toml:162`](../../pyproject.toml#L162) |
| fspack 默认 extras 含 pdf | [`pyproject.toml:182-192`](../../pyproject.toml#L182-L192) |
| PyMuPDF 运行期引用 | [`pdfbase.py:21/49/53-57`](../../src/fcmd/tools/pdf/pdfbase.py#L21) + 12 个操作守卫（§3.1 表） |
| pypdf 运行期引用 | [`pdfbase.py`](../../src/fcmd/tools/pdf/pdfbase.py) 7 个操作 + [`filescan/_extract.py:443-445`](../../src/fcmd/apps/filescan/_extract.py#L443-L445) |
| onnxruntime 0 引用 | 全仓 `src/` 搜索 0 处；[`_env_helpers.py:17-22`](../../src/fcmd/cli/_env_helpers.py#L17-L22) 也未列它 |
| onnxruntime 来源 | `dist/site-packages/pymupdf-2.1.dist-info/METADATA` 的 `Requires-Dist: onnxruntime` |
| 1.x 零依赖 | `.venv/Lib/site-packages/pymupdf-1.28.2.dist-info/METADATA` 无 `Requires-Dist` |
| 打包不走 lock | [`packtool.py:215-223`](../../src/fcmd/tools/dev/packtool.py#L215-L223) |
| 缺库降级 | [`test_cli_pdftool.py:305-356`](../../tests/tools/pdf/test_cli_pdftool.py#L305-L356)、`venv_nopdf` 实跑输出 |
| 体积 | §5 实测；`dist` 清单 + deflate 压缩 |
