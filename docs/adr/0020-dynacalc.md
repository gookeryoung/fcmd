# 0020 dynacalc：装药密度—尺寸—工艺缺陷→爆轰传递参数化算例工具

- 状态：已实现（CLI + GUI + 测试落地）
- 日期：2026-11-13
- 网格交接：[paramesh-mesh/1 评审](../dynacalc-paramesh-mesh-review.md)（deck 侧接入排在 GUI / 测试之后）

## 背景

现有 `lscalc` 只解决「给一个 `.k` 文件，起 LS-DYNA 进程」；`filescan` / `renamer`
等应用解决的是文件批处理。爆轰传递仿真真正的重复劳动在四段：

1. 受控参数表（密度 / 尺寸 / 缺陷 / 起爆）按因子与水平展开成扫描矩阵；
2. 每个工况渲染成独立、可 diff 的 LS-DYNA deck；
3. 批量调度求解器并回收日志、支持断点续跑；
4. 从 `glstat` / `matsum` / 自定义历史曲线提取指标，做对比、差异与门控判定。

本应用只承担上述「参数化、模板化、调度、汇总」，**不做物理求解、不做公式推导、
不做三维前处理或网格生成**。参考材料中的 48 组主矩阵、局部爆速提取算法、收敛判据
与参数待填表是需求来源；本应用不复刻其公式，也不内置任何真实配方、材料参数或
产品尺寸。

模型描述（几何与分布场）由并行应用 paramcad 提供，接口契约见
[`docs/dynacalc-model-contract.md`](../dynacalc-model-contract.md)（版本
`dynacalc-model/1`）。

## 决策

### D1 应用边界与命名

新增应用包 `fcmd/apps/dynacalc/`，按 ADR 0003 逐公开模块注册（不声明
`__tool_name__`）：

- `dynacalc`（CLI）：`params` / `matrix` / `generate` / `run` / `report` /
  `compare` / `gates` / `template` / `example`；
- `dynacalcw`（Tk）：参数文件与输出目录选择、生成清单预览、dry-run、执行、
  作业状态与汇总/门控结果查看。

明确不做：材料模型推导、JWL / 点火—增长参数标定、网格生成、三维工艺映射、
AUTODYN 双路线耦合、任何「未填即取公开值」的默认材料卡。

### D2 参数文件选 YAML

参数文件格式固定为 **YAML**（`pyyaml` 已是主依赖，不新增依赖），理由：

- 因子 / 水平天然是嵌套列表，YAML 可读性明显优于 JSON；
- 允许注释，受控参数表需要就地标注「为什么 TBD」「来源哪份试验」；
- 仍是纯文本，可 diff、可版本化、可由 Excel 导出后人工整理。

文件带版本化 `schema: dynacalc/1`。读取语义沿用 filescan 配置约定：

- 非 YAML / 非对象 / `schema` 不匹配 → 整份失败，给出中文错误；
- 缺字段 → 回落到默认值并给出提示；
- 未知字段 → 忽略（不报错），保证向后兼容；
- 单字段非法（如水平值类型不对）→ 该字段失败，不静默修正。

文件结构：

```yaml
schema: dynacalc/1
meta:
  project: 占位项目          # 不得写真实型号
  revision: r1
factors:
  - key: density
    label: 装药平均密度
    unit: g/cm^3
    kind: continuous          # continuous | discrete
    confidence: controlled    # public | controlled | uncalibrated
    levels:
      - { code: L1, value: TBD, label: 低密度 }
      - { code: H1, value: TBD, label: 高密度 }
  - key: inner_diameter
    label: 装药内径
    unit: mm
    kind: continuous
    confidence: controlled
    levels:
      - { code: ID1, value: TBD, label: 小内径 }
      - { code: ID2, value: TBD, label: 标称内径 }
      - { code: ID3, value: TBD, label: 大内径 }
  - key: wall_thickness      # 2 水平
  - key: axial_density       # 2 水平：均匀 / 端部疏松
  - key: eccentricity        # 2 水平：对称 / 偏心
  - key: gap_mm              # 补充组因子（示例）
matrix:
  main: [density, inner_diameter, wall_thickness, axial_density, eccentricity]
  supplementary:
    - [gap_mm]
    - [initiation_strength]
  baseline:                  # 补充组 OFAT 的基准；缺省取主矩阵各因子首个水平
    density: L1
defaults:
  skip_fraction: 0.2         # 稳态拟合前跳过的时程比例
  tail_fraction: 0.1
  probes_mm: [50, 100, 150]
  quench_fraction: 0.99
  gates:
    velocity_rel_diff: 0.02
    arrival_l2_rel: 0.03
    wall_impulse_rel: 0.05
run:
  solver_path: ""
  ncpu: 4
  memory: auto
  parallel: smp              # smp | mpp
  mpi: intel                 # intel | msmpi | platform
  extra_args: []
  env: {}                    # 许可证等环境变量，如 LSTC_FILE
  timeout_s: 3600
  retries: 0
  concurrency: 1
  fake: false
```

`confidence` 三态对应「公开通用 / 受控试验 / 未标定假设」；`value: TBD` 表示未冻结。
`params` 子命令输出各因子的 TBD 数量与置信等级分布，**不包含任何数值默认值**。

### D3 矩阵展开与工况编码

- 主矩阵：对 `matrix.main` 列出的因子按声明顺序做笛卡尔积。参考工况为
  2×3×2×2×2 时恰好 48 组；用户增删因子或水平即得到自定义矩阵，不硬编码 48。
- 补充组：`matrix.supplementary` 每个内层列表是一组 **OFAT**（one-factor-at-a-time）
  工况，以 `baseline` 为底只改该组因子；一个内层列表含多个因子时对该组做笛卡尔积。
  补充组不并入主矩阵的统计自由度。
- 编码：主矩阵 `M001`…（按因子声明顺序的乘积顺序），补充组 `S<组号>-<序号>`
  （如 `S1-001`）。编码在参数文件不变时稳定。
- 每个工况的计算指纹 `fingerprint` = SHA-256（规范 JSON：schema 版本、因子水平编码
  与解析值、模板摘要、矩阵分组）前 12 位；deck 目录名 `<编码>-<指纹前 8 位>`，
  同一参数重复生成得到同名目录。

### D4 Deck 生成：纯文本、可 diff、可复现

- 内置一份 **占位模板**（`_template.py` 的字符串常量），覆盖 `*KEYWORD`、
  `*CONTROL_*`、材料与 EOS（`*MAT_HIGH_EXPLOSIVE_BURN` / `*EOS_JWL` /
  `*MAT_PLASTIC_KINEMATIC` / 空气 `*MAT_NULL` + `*EOS_IDEAL_GAS`）、
  `*SECTION_SOLID` + `*ALE_MULTI_MATERIAL_GROUP` +
  `*CONSTRAINED_LAGRANGE_IN_SOLID`、`*INITIAL_DETONATION`、
  `*DATABASE_GLSTAT` / `*DATABASE_MATSUM` / `*DATABASE_HISTORY_NODE`。
  所有数值字段一律 `${因子键}` 占位，模板内出现的真实数字只允许是几何/控制
  占位（如 0、1）并在注释中标注「占位」。
- 占位替换：`${key}` 从「工况参数 + 内建键」取值。内建键：
  `case_code` / `case_group` / `case_hash` / `schema`。模板中出现无法解析的占位符
  → 生成失败并列出占位符与模板行号，不写半成品目录。
- TBD 语义：值为 `TBD` 时原样写入 deck，并在生成清单中计数；`--strict` 时
  TBD 直接失败。**任何路径都不会把 TBD 替换成公开材料卡数值。**
- 可复现：deck 文件统一 UTF-8、`\n` 换行、末尾换行；同一参数重复生成逐字节一致；
  生成物不含时间戳。`case.json` 记录编码、分组、水平、参数值、指纹、模板摘要。
- 输出根目录写 `cases.json`（工况清单）与 `manifest.json`（计数、TBD 数、模板摘要、
  生成器版本），均排序输出、不含时间戳。

### D5 求解调度：真实求解器可选，假求解器必须可用

- `_solver.py` 只负责命令构造与进程生命周期，不解释物理：
  - SMP：`<solver> i=<input> ncpu=<n> memory=<m> <extra...>`；
  - MPP：`mpirun|mpiexec -np <n> [平台附加参数] <solver> i=<input> ...`，
    与 `lscalc` 的命名与参数口径保持一致（不 import 其实现，避免工具域耦合）；
  - 许可证 / 环境：`run.env` 原样并入子进程环境（如 `LSTC_FILE`）。
- 假求解器：`_fakesolver.py`（下划线前缀，不参与工具发现），以
  `python -m fcmd.apps.dynacalc._fakesolver <deck_dir>` 子进程方式运行，按
  `case.json` 确定性地生成 `glstat` / `matsum` / `history.csv`。失败与延迟通过
  `DYNACALC_FAKE_FAIL_CODES` / `DYNACALC_FAKE_DELAY_MS` 注入，便于测试超时、
  重试与失败回收，无需安装 LS-DYNA。`--fake` 使用该求解器。
- 调度：线程池 + 并发上限；每工况独立子进程，超时杀进程（POSIX 杀进程组）；
  失败按 `retries` 重试；stdout/stderr 追加写入工况目录 `run.log`（带时间戳，
  仅日志，不影响 deck 复现）。
- 作业清单：输出根目录 `jobs.json`，逐工况记录状态
  `pending / running / done / failed / skipped`、尝试次数、退出码、耗时与错误摘要；
  每次状态变化后原子重写（临时文件 + `os.replace`）。
- 断点续跑：`run` 读取 `jobs.json`，指纹一致的 `done` 默认跳过，`failed` 重跑；
  `--force` 全量重跑；参数变化导致指纹不一致时旧记录作废并重跑。
- dry-run：只打印工况、命令、超时与并发，不启动进程、不写 `jobs.json`。

### D6 结果回收：缺失与损坏必须报错

每个工况目录要求存在：

| 文件 | 用途 |
|---|---|
| `glstat` | 时间 / 动能 / 内能 / 总能量 |
| `matsum` | 各 part 内能（取最大 part 内能与 part 数） |
| `history.csv` | 项目标准历史量：`time,front_z,reaction_fraction,wall_velocity,wall_strain` |

`history.csv` 是本应用约定的后处理产物（真实求解时由项目后处理脚本或
`*DATABASE_ASCII` 转换得到），单位固定为 s / m / 无量纲 / m·s⁻¹ / 无量纲。

提取口径：

- 稳态爆速：在 `[skip_fraction, 1 - tail_fraction]` 时程窗内对 `front_z(t)` 做最小二乘
  线性拟合，斜率即爆速，同时输出 R²；
- 末端反应分数：`reaction_fraction` 末值；
- 波面到达时程：对 `defaults.probes_mm` 每个探针取首次 `front_z ≥ z/1000` 的时刻
  （µs）；未到达的探针记为空并在报告中标注，不伪造数值；
- 管壁速度：最大值与时间积分（梯形法）；管壁应变：最大值；
- glstat / matsum：峰值内能、末态总能量、part 数与最大 part 内能。

失败语义：文件缺失、列缺失、非数值、时间非单调、无数据块 → 该工况结果为
`status=error` 并附中文原因；汇总 CSV/JSON 保留该行与错误列，CLI 以退出码 2
结束。**不允许把解析失败的指标写成 0 或空值后当作成功。**

`report` 输出 CSV / JSON / 文本对比报告；`compare --a CODE --b CODE` 输出两工况
各指标差值与相对差，指标缺失时明确标注不可比。

### D7 门控检查：工程建议值可配置

`gates` 子命令按参数文件中的阈值与配对做检查，输出通过/不通过清单：

| 检查 | 默认阈值 | 口径 |
|---|---|---|
| 稳态爆速相对差 | 2% | `|v_t - v_r| / |v_r|` |
| 波面到达时程 L2 相对差 | 3% | 共享探针上 `sqrt(mean((Δt/t_r)^2))` |
| 管壁速度积分相对差 | 5% | `|I_t - I_r| / |I_r|` |
| 熄爆分类一致 | — | 末端反应分数 < `quench_fraction` 判为熄爆，比较分类是否一致 |

- 配对：`gates.pairs: [[A, B], ...]` 显式指定（「相邻网格」用不同网格工况配对表达）；
  缺省为「基准工况 vs 其余全部工况」，基准取 `defaults.gates.reference` 或首个主工况。
- 阈值、`quench_fraction`、配对均可配置；缺数据或指标缺失时该项判 `SKIP` 并给出原因，
  不判通过。
- CLI 与 GUI 输出固定附带声明：**这些阈值是本项目工程建议值，不是物理常数，
  不构成安全裕量或放行依据。**

### D8 模型描述导入（paramcad 对接）

参数矩阵负责「工况组合」，模型描述负责「单个工况的几何与场」。完整契约见
[`docs/dynacalc-model-contract.md`](../dynacalc-model-contract.md)，此处只记 ADR 级决策：

- 模型文件格式 YAML，`schema: dynacalc-model/1`，单位固定为 mm / g·cm⁻³ / m·s⁻¹，
  v1 不做单位换算；单位不符、必填缺失、枚举未知、schema 不认识一律整次失败。
- 一个文件可含多个模型，用 `case_scope.include/exclude` 绑定工况；一个工况最多匹配
  一个模型，命中多个直接报错；没有匹配则回落矩阵值，不视为错误。
- 模型解析成扁平派生键（`inner_diameter`、`wall_thickness`、`gap_mm`、
  `density_axial_mean`、`drawing_reduction_total`、`wall_thickness_circumferential_mean`、
  `density_end_loose_density` 等）供模板占位使用。同名键的
  覆盖顺序：模型非 TBD 值 → 矩阵同名因子 → TBD。
- 指纹：模型指纹排除 `meta` / `tbd` / `case_scope` 后按规范 JSON 计算 SHA-256；
  **可选段 / 可选字段为 TBD 时不写键**（与 paramcad 导出的省略形态逐字节一致），
  `passes[].index` 保持整数，`cumulative_reduction` 由道次推导、推不出则省略键；
  工况指纹追加模型指纹，模型内容不同即生成不同 deck 目录。
- v1.1 只做加法：新增 `density_field.end_loose`（端部疏松）与
  `geometry.wall_thickness.circumferential`（周向壁厚分布），并明确 TBD 三分法
  （必须给出 / 可留 TBD / 不允许 TBD）与减径率 `[0, 1)` 区间。
- dynacalc 不回写模型文件；绑定结果写入 `manifest.json` 与各工况 `case.json`。
- 该能力不修改 `src/fcmd/apps/paramcad/` 下任何文件；paramcad 是并行会话的实现域。
- 共享文件归属：本应用负责 `pyproject.toml` 的 `[project.scripts]` 与
  `src/fcmd/cli/_manifest_data.py` 的统一重生成；paramcad 侧不再单独改动这两处，
  避免互相覆盖。

### D9 模块划分与依赖

| 模块 | 职责 |
|---|---|
| `_params.py` | YAML 读写与逐字段校验、因子/水平模型、主矩阵与补充组展开、编码与指纹 |
| `_model.py` | `dynacalc-model/1` 读取与逐字段校验、工况绑定、派生键、模型指纹 |
| `_template.py` | 内置 deck 模板、占位符解析与必填校验、渲染、deck/清单原子写入 |
| `_solver.py` | 求解器配置、命令构造、dry-run 计划、并发/超时/重试调度、`jobs.json`、续跑 |
| `_fakesolver.py` | 确定性假求解器（子进程入口，不注册工具） |
| `_results.py` | glstat/matsum/history 解析、指标提取、CSV/JSON/文本报告、两工况对比 |
| `_gates.py` | 门控检查与声明文案 |
| `dynacalc.py` | CLI 注册与输出 |
| `dynacalcw.py` | Tk 窗口、设置持久化（`~/.fcmd/ui/dynacalcw.json`） |

依赖方向：应用只依赖标准库 + `fcmd.gui` + `fcmd.console` + `pyyaml`。
`_params` / `_model` / `_template` / `_results` / `_gates` 为纯逻辑模块，不导入 Tk。

### D10 测试与门禁

- 纯逻辑单测覆盖：参数校验与容错、48 组矩阵、编码/指纹稳定性、占位符缺失报错、
  TBD 计数与 `--strict`、deck 逐字节复现、glstat/matsum/history 正常与损坏输入、
  门控四类判定与 SKIP、两工况对比、模型绑定与覆盖优先级。
- 端到端（假求解器）：`example → generate（48 组）→ dry-run → run --fake → report
  → gates`。48 组真实起子进程的用例超过 0.5 s，按项目约定标记 `slow`；主门禁保留
  小规模（2 组）同路径用例。
- 窗口类 `dynacalcw` 的 Tk 类标 `pragma: no cover`，设置读写与行格式化函数单测。
- 改动后重生成 `fcmd/cli/_manifest_data.py`，`make check-fast` 必过，收尾 `make test`。

## 不变量

1. 本应用不内置任何真实配方、材料参数、产品尺寸或「典型值」默认材料卡。
2. 生成物只含参数与模板的确定性函数；同一参数重复生成逐字节一致。
3. TBD 永远保持 TBD；生成 deck 不是暗示参数已标定。
4. 结果解析失败必须显式报错，不得输出 0 / 空值冒充成功。
5. 门控阈值只是工程建议值，输出必须附带声明。
6. dry-run 不启动求解器、不写作业状态。
7. 求解调度不修改已生成的 deck（日志与结果只写工况目录内的运行期文件）。

## 验收

- `dynacalc example` 能生成一份全 TBD 占位参数表；`params` 校验通过并统计 TBD；
- 参考参数表 `generate` 产出 48 个 `M###-<hash>` 目录与 `cases.json` / `manifest.json`，
  重复生成逐字节一致；
- `run --plan-only`（dry-run；全局 `--dry-run` 由 fcmd 任务框架占用）打印 48 条命令且不启动进程；
- `run --fake` 完成假求解，`jobs.json` 状态正确，中断后重跑只补未完成项；
- `report` 生成 CSV/JSON/文本；缺 `glstat` 的工况明确报错且退出码非零；
- `compare` 输出两工况差异；`gates` 输出四项结论与工程建议值声明；
- `dynacalcw` 可选择参数文件、预览清单、dry-run、执行并查看作业状态与门控结果。

## 落地状态

- 设计：已定稿（本 ADR）。
- 实现与测试：已落地；`tests/apps/dynacalc/` 覆盖 48 组主矩阵 + 补充组、deck 生成、
  dry-run、假求解、续跑/重试/超时、结果解析、汇总、对比、门控与 GUI 纯函数；
  全仓库 `ruff` / `ruff format` / `pyrefly` / `pytest`（含覆盖率 95% 门禁）通过。
