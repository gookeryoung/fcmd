# 0021 paramcad：参数化建模与几何预览

- 状态：最小原型已落地（v1 范围）
- 日期：2026-11-13
- 相关文档：[dynacalc 模型契约](../dynacalc-model-contract.md)、[ADR 0020](0020-dynacalc.md)

## 背景

`dynacalc` 负责工况矩阵、deck 生成与求解调度，但「一个工况的几何与分布场从哪来」需要
上游模型。`paramcad` 承担这段：把管坯 / 装药 / 拉拔工艺参数变成可预览、可导出的
参数化模型，**不求解、不生成网格、不输出 LS-DYNA 关键字**。

本应用只做参数语义与几何推导。所有未填数值保持 `TBD` 并附原因，不内置任何真实配方、
材料参数或产品尺寸；示例模型是明确标注的虚构占位。

## 决策

### D1 应用边界与入口

新增包 `src/fcmd/apps/paramcad/`，平铺两层，符合 ADR 0003 与 `tests/test_layout.py`：

| 文件 | 职责 |
|---|---|
| `_quantity.py` | 单位常量、`Quantity`（值或 TBD + 单位 + 原因）、统一错误类型 |
| `_fields.py` | 分布场：解析函数 / 实测点、来源与采样 |
| `_defects.py` | 缺陷参数：间隙、脱粘、局部缺陷、端部疏松密度 |
| `_model.py` | 几何两态、拉拔工艺、推导规则、摘要与一致性校验 |
| `_preview.py` | 纯逻辑预览几何：截面、轴向剖面、分布曲线（不依赖 Tk） |
| `_export.py` | `dynacalc-model/1` 导出、解析、校验、指纹（唯一格式边界） |
| `paramcad.py` | CLI：`example` / `inspect` / `export` / `digest` / `contract` |
| `paramcadw.py` | Tk 预览窗口，复用 `fcmd.gui` 主题与设置持久化 |

协作约束：本阶段不修改共享工具注册表，也不重生成 `_manifest_data.py`；工具发现走
动态路径，注册与清单由后续统一处理。不触碰 `src/fcmd/apps/dynacalc/` 内任何文件。

### D2 几何参数语义：两态 + 显式推导

一个状态由 `GeometryState` 的 9 个量描述（长度 mm、角度 deg）：外径、内径、长度、
名义壁厚、最小/最大壁厚、径向/周向偏心分量、偏心方向角。派生量：

- `wall_mean`：显式名义壁厚优先，否则 `(外径 - 内径) / 2`；
- `offset`：`(最大壁厚 - 最小壁厚) / 2`，即内孔相对外圆的径向偏移。

模型由 `TubeModel.from_before(前态, 工艺)` 构造，`geometry` 恒为**最终几何**，
`before_state` 只记录推导输入（不参与导出与指纹）。`derive_after` 的规则：

1. 外径：`D × Π(1 - 减径率ᵢ)`；任一减径率缺失 → TBD；
2. 壁厚：`t × Π(1 + 壁厚变化率ᵢ)`；任一道次缺失变化率 → TBD；
3. 内径：`D_after - 2 t_after`，非正直接报错（工艺不成立）；
4. 长度：**只在工艺显式给出成品长度时使用**，不做体积守恒等隐式假设；
5. 壁厚极值 / 偏心：必须显式声明偏心传递方式 `absolute`（绝对偏移保持）或
   `factor`（偏心因子按壁厚比例保持），否则 TBD；
6. 没有工艺时后态整体 TBD，原因写「未给出拉拔工艺」。

周向偏心分量与偏心方向角目前不参与推导，固定为 TBD 并注明「未建模」，避免把
「未考虑」伪装成 0。

### D3 分布场表示：解析或实测，来源必填

`Field(axis, profile, source, note, unit)`，`axis ∈ {axial, radial, circumferential}`，
同一轴最多一个场，`source` 必填。

- `AnalyticProfile`：`constant`、`linear`、`end_loose`（前段常量、末段线性过渡到端值）。
  全部是分段线性，`vertices()` 给出**逐点等价**的折线顶点，导出时不引入采样误差。
- `MeasuredProfile`：`(position, density)` 序列，位置严格递增且落在 0–1，线性插值，
  端点外按端点值夹紧。

契约的 `density_field` 没有来源字段，导出时把来源写入 `meta.field_sources`；
`meta` 不参与指纹，因此追溯信息不影响 dynacalc 的 deck 目录。

### D4 缺陷参数化

`DefectParams` 以参数而非散落数值表达：

- `gap_mm`：药管间隙；
- `debond_state ∈ {bonded, debonded, tbd}` 与 `debond_extent_deg`：局部脱粘；
- `local_defects`：`void` / `thin_layer` / `crack` / `debond`，位置为轴向 / 径向
  归一化 0–1 与周向角 `[0, 360)`，可带特征尺寸与严重度；
- `end_loose_density`：端部疏松密度水平。

全部默认 TBD。`validate_model` 只检查真实矛盾（如间隙不小于内腔半径），不把
TBD 当错误。

### D5 预览实现：几何与绘制分离，只用 Tk

`_preview.py` 只产出点列与标注，`paramcadw` 只负责把点画到 `tk.Canvas`；不引入任何
外部图形库，也没有显示器时仍可单测。

二维截面由两个偏心圆确定：外圆半径 `R`、内圆半径 `r_i = R - 名义壁厚`、内圆圆心
沿 `-x` 偏移 `c = (最大壁厚 - 最小壁厚) / 2`。沿角度 `θ` 的径向壁厚用两圆精确交点：

```
wall(θ) = R - (-c·cosθ + sqrt(r_i² - c²·sin²θ))
```

于是 `θ = 0` 取到最大壁厚、`θ = 180°` 取到最小壁厚，与几何极值严格一致，而不是
用「名义壁厚 ± 偏移」近似。药层半径取 `r_i - gap`。

未标定量的预览策略（不静默假设）：

| 缺失项 | 预览行为 |
|---|---|
| 外径 / 内径 / 壁厚 | 截面与轴向剖面不可用，显示原因 |
| 偏心 | 按同心绘制并提示「偏心量未标定」 |
| 药管间隙 | 不绘制药层并提示 |
| 间隙不小于内腔半径 | 不绘制药层并提示 |
| 成品长度 | 轴向剖面不可用；轴向曲线横轴改用 0–1 相对位置 |

分布曲线 = 显式密度场 + 由几何派生的「壁厚（周向）」与「药层厚度（周向）」；
派生曲线注明「由几何与偏心推导」，药层厚度注明「未建模周向变化，按常量显示」。

### D6 导出契约：单一格式边界

导出/解析/校验全部集中在 `_export.py`，导出器可整体替换。映射到
`dynacalc-model/1`：

| 内部模型 | 契约字段 |
|---|---|
| `geometry` | `geometry`（必填写出；可选字段只写已标定值） |
| `process` | `drawing.passes`，`cumulative_reduction` 按道次算好写出 |
| `density_fields` | `density_field.<axis>`：`constant` → `uniform`，其余 → `polyline` 顶点 |
| `defects.local_defects` | `density_field.local_defects[]` |
| `defects`（间隙 / 脱粘） | `interface`，全空时整节省略 |
| `case_scope` | `case_scope.include / exclude` |
| `confidence` | 四域补齐，缺省 `uncalibrated` |
| `Field.source`、`notes` | `meta.field_sources` / `meta.model_notes`（不参与指纹） |

TBD 与单位：

- 必填数值未标定写字符串 `TBD`；解析时 `null`、非 `TBD` 字符串一律报错；
- 可选数值未标定时**省略字段**（契约允许缺失，dynacalc 回落矩阵再回落到 TBD），
  不写 `null`；
- 单位固定 mm / g·cm⁻³ / m·s⁻¹，逐项相等校验，不做换算；
- `tbd` 清单由导出载荷自动收集（相对模型节的路径，不含 `models[i]` 前缀）。

模型指纹按契约第 8 节：功能载荷（`id` / `units` / `confidence` / `geometry` /
`drawing` / `density_field` / `interface`）排除 `meta`、`tbd`、`case_scope` 后，
`json.dumps(ensure_ascii=False, sort_keys=True, separators=(",", ":"))` 取 SHA-256，
保留全部 64 位小写十六进制。

`validate_case_scope(models, known_codes)` 实现契约第 6 节中可离线校验的两条：
引用未知工况编码、一个工况命中多个模型（`id` 唯一性在构造/解析时已校验）。

### D7 测试与门禁

`tests/apps/paramcad/` 覆盖：几何推导与 TBD 传播、分布场采样、缺陷参数、预览几何
（截面极值与几何一致）、导出结构与往返、解析错误（缺单位 / 错单位 / 必填缺失 `null` /
枚举未知 / 样本非递增）、`case_scope` 规则、CLI 与窗口纯逻辑。

黄金指纹在 paramcad 侧先冻结（`example` 与契约第 11 节示例），**待 dynacalc
`model --file` 输出对拍后转为跨实现黄金样本**。

## 不变量

1. 不内置真实配方、材料参数、产品尺寸或「典型值」。
2. 不做求解、网格生成、LS-DYNA 关键字输出。
3. 缺工艺参数只产生 TBD，不做体积守恒、壁厚不变等隐式假设。
4. TBD 永远不是 `null`、空串、`0` 或 `-1`；TBD 必须带原因。
5. 分布场来源必填，导出后仍可在 `meta` 中追溯。
6. 导出、解析、校验、指纹只存在于 `_export.py` 一个边界。
7. 预览不使用外部图形库；无显示器时纯逻辑仍可测试。
8. 应用平铺在 `apps/paramcad/` 两层内，不加深、不新增子包。

## 待统一的契约缺口

以下不是 paramcad 自行改契约，而是需要与 dynacalc 侧确认：

1. **端部疏松密度无槽位**：`end_loose_density` 目前只能内部记录并用于预览，
   契约没有对应字段，导出后信息丢失。
2. **壁厚周向分布不属于 `density_field`**：只能用 `eccentricity` 的偏心量近似，
   无法表达非椭圆形的周向壁厚起伏。
3. **折线样本含 TBD**：契约允许 `samples[].density: TBD`，但 paramcad v1 预览需要
   具体数值，解析时明确报错并要求改用 `kind: tbd` 或补齐样本。
4. **指纹归一化细节未逐条明确**：可选字段省略 vs 填默认、`passes[].index` 保持整数、
   派生 `cumulative_reduction` 的浮点表示、`uniform` 值为 TBD 的处理，需要一份
   dynacalc 黄金向量对拍确认。
5. **`case_scope` 的未知编码与互斥**：需要 dynacalc 的工况清单才能校验；paramcad
   提供 `validate_case_scope(known_codes)`，由调用方传入清单。

## 验收

- `fcmd paramcad example m.yaml` 写出可读模型；`inspect` 打印几何、工艺、分布场、
  缺陷、一致性与 TBD 清单；
- `fcmd paramcad digest m.yaml` 输出 64 位指纹，与 `export` 打印的指纹一致；
- 示例模型截面在 `θ=0 / 180°` 的壁厚分别等于 `wall_thickness.max / min`；
- 导出 YAML 能被本应用按契约重新解析，再次导出的文本与指纹逐字节一致；
- `ruff check`、`ruff format --check`、`pyrefly check`、`pytest tests/apps/paramcad` 通过。

## 落地状态

- 设计：已定稿（本 ADR）。
- 最小原型：已落地（示例 → 截面 / 分布预览 → 导出 → 再解析 / 指纹）。
- 待办：与 dynacalc 对拍黄金指纹并统一上述契约缺口；工具注册与静态清单统一处理。
