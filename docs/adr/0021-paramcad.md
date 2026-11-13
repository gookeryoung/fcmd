# 0021 paramcad：参数化建模与几何预览

- 状态：v1.1 契约已对齐（最小原型 + 新增字段）
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
| `_defects.py` | 缺陷参数：间隙、脱粘、局部缺陷、端部疏松（长度占比 + 密度） |
| `_model.py` | 几何两态、拉拔工艺、推导规则、摘要与一致性校验 |
| `_preview.py` | 纯逻辑预览几何：截面、轴向剖面、分布曲线（不依赖 Tk） |
| `_export.py` | `dynacalc-model/1` 导出、解析、校验、指纹（唯一格式边界） |
| `paramcad.py` | CLI：`example` / `inspect` / `export` / `digest` / `contract` |
| `paramcadw.py` | Tk 预览窗口，复用 `fcmd.gui` 主题与设置持久化 |

协作约束：本阶段不修改共享工具注册表，也不重生成 `_manifest_data.py`；工具发现走
动态路径，注册与清单由后续统一处理。不触碰 `src/fcmd/apps/dynacalc/` 内任何文件。

### D2 几何参数语义：两态 + 显式推导

一个状态由 `GeometryState` 的 9 个量描述（长度 mm、角度 deg）：外径、内径、长度、
名义壁厚、最小/最大壁厚、径向/周向偏心分量、偏心方向角；外加可选的周向壁厚分布
`wall_circumferential`（见 D3）。派生量：

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
「未考虑」伪装成 0。周向壁厚分布同样不参与「前态 + 工艺」推导：`derive_after`
把它置为 `kind: tbd`，需要时由调用方显式给出（导出时 `kind: tbd` 归一为省略）。

### D3 分布场与周向壁厚：解析或实测，来源必填

`Field(axis, profile, source, note, unit)`，`axis ∈ {axial, radial, circumferential}`，
同一轴最多一个场，`source` 必填。

- `AnalyticProfile`：`constant`、`linear`、`end_loose`（前段常量、末段线性过渡到端值）。
  全部是分段线性，`vertices()` 给出**逐点等价**的折线顶点，导出时不引入采样误差。
- `MeasuredProfile`：`(position, density)` 序列，位置严格递增且落在 0–1，线性插值，
  端点外按端点值夹紧。`density` 允许为 `None`（契约的 `TBD`）；此时 `is_calibrated`
  为假，采样跨过该样本返回 `None`，预览标注「未标定」而不是报 schema 错误。

周向壁厚单独用 `CircumferentialWall` 表达，对齐 `geometry.wall_thickness.circumferential`：

| `kind` | 数据 | 约束 |
|---|---|---|
| `uniform` | `value`（mm） | 必须给出具体值；**禁止 TBD** |
| `polyline` | `WallSample(angle_deg, thickness)` | ≥2 点，角度 `[0, 360)` 严格递增，壁厚可为 TBD |
| `tbd` | 无 | 归一为整节省略 |

契约的 `density_field` 没有来源字段，导出时把来源写入 `meta.field_sources`；
`meta` 不参与指纹，因此追溯信息不影响 dynacalc 的 deck 目录。

### D4 缺陷参数化

`DefectParams` 以参数而非散落数值表达：

- `gap_mm`：药管间隙；
- `debond_state ∈ {bonded, debonded, tbd}` 与 `debond_extent_deg`：局部脱粘；
- `local_defects`：`void` / `thin_layer` / `crack` / `debond`，位置为轴向 / 径向
  归一化 0–1 与周向角 `[0, 360)`，可带特征尺寸与严重度；
- `end_loose_length_fraction`（`(0, 1]`）与 `end_loose_density`：端部疏松段，对应
  `density_field.end_loose`，与轴向密度折线并列；两者都可单独给出或留 TBD。

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
| 折线密度样本 TBD | 该曲线不画点，标注「样本含未标定值（TBD）」 |
| 周向壁厚样本 TBD | 该曲线不画点，标注「周向壁厚样本含未标定值（TBD）」 |

**预览最小必填集不是文件有效性**：绘制截面至少需要外径、内径、名义壁厚为具体值，
但解析层接受合法但含 TBD 的文件（`length`、密度样本、周向壁厚样本都可 TBD），
只是预览把相应部分标为未标定。

分布曲线 = 显式密度场 + 「壁厚（周向）」+「药层厚度（周向）」。周向壁厚曲线优先
使用模型显式给出的 `circumferential` 分布（`uniform` / `polyline`），没有给出时才
由偏心双圆推导；派生曲线注明「由几何与偏心推导」，药层厚度注明「未建模周向变化，
按常量显示」。

### D6 导出契约：单一格式边界

导出/解析/校验全部集中在 `_export.py`，导出器可整体替换。映射到
`dynacalc-model/1`：

| 内部模型 | 契约字段 |
|---|---|
| `geometry` | `geometry`（必填写出；可选字段只写已标定值） |
| `geometry.wall_circumferential` | `geometry.wall_thickness.circumferential`（`uniform` / `polyline`；`tbd` 省略） |
| `process` | `drawing.passes`，`cumulative_reduction` 按道次算好写出 |
| `density_fields` | `density_field.<axis>`：`constant` → `uniform`，其余 → `polyline` 顶点 |
| `defects.end_loose_*` | `density_field.end_loose{length_fraction,density}`，只写已给出的键 |
| `defects.local_defects` | `density_field.local_defects[]` |
| `defects`（间隙 / 脱粘） | `interface`，全空时整节省略 |
| `case_scope` | `case_scope.include / exclude` |
| `confidence` | 四域补齐，缺省 `uncalibrated` |
| `Field.source`、`notes` | `meta.field_sources` / `meta.model_notes`（不参与指纹） |

TBD 与单位（按契约第 5 节三分法）：

- **必须给出**：`id`、`units.*`、外径 / 内径 / 长度、`wall_thickness.nominal`、
  每个 `*.kind`、样本 `position` / `angle_deg`、缺陷 `id` / `kind`、`passes[].index`；
  缺失或 TBD 都报错；
- **可留 TBD**：其余全部 `num`，可写 `TBD` 也可整键省略（导出统一省略，不写 `null`）；
- **不允许 TBD**：只有 `uniform` 的 `value`（密度三轴与周向壁厚），未标定请改用
  `kind: tbd`；
- 单位固定 mm / g·cm⁻³ / m·s⁻¹，逐项相等校验，不做换算；
- `tbd` 清单由导出载荷自动收集（相对模型节的路径，不含 `models[i]` 前缀），仅作文档。

归一化细则（契约第 8.1 节 12 条，导出即归一）：

1. `id` / `units` / `confidence`（四域展开，缺省 `uncalibrated`）/ `geometry` 始终存在；
2. 可选段内容为空时整节省略（`drawing` / `density_field` / `interface`）；
3. 可选字段为 TBD 时不写键（含 `wall_thickness.min/max`、三个偏心分量、道次可选参数、
   `size_mm` / `severity`、`end_loose` 两字段）；偏心三分量全 TBD 时整节省略；
4. 枚举 `kind: tbd` / `debond.state: tbd` 等于省略；
5. `uniform` 的 `value` 必须是具体数值，`TBD` 是非法输入；
6. 折线样本保留原序，`position` / `density` 转 `float`；`density` 允许 `"TBD"` 字符串，
   原样参与指纹；
7. 道次按 `index` 升序写入，`index` 保持整数，其余数值转 `float`；
8. `cumulative_reduction` 显式值优先，否则按 `1 - Π(1 - reduction_i)` 推导后写入，
   推不出时省略（不写 `TBD`）；
9. 局部缺陷只写给出的 `location` 分量与已标定的 `size_mm` / `severity`；
10. `end_loose` 只写具体字段，两字段都 TBD 时整节省略；
11. 周向壁厚 `kind: tbd` 整节省略；`uniform` 写 `{kind, value}`；`polyline` 写
    `{kind, samples}`，样本只含 `angle_deg` 与 `thickness`（可为 `"TBD"`）；
12. 浮点用最短往返表示，不做四舍五入（如 `0.08` 推导出
    `0.07999999999999996`）。

模型指纹按契约第 8 节：功能载荷（`id` / `units` / `confidence` / `geometry` /
`drawing` / `density_field` / `interface`）排除 `meta`、`tbd`、`case_scope` 后，
`json.dumps(ensure_ascii=False, sort_keys=True, separators=(",", ":"))` 取 SHA-256，
保留全部 64 位小写十六进制。

`case_scope` 只做结构 / 类型 / 区间自检（字符串列表、非空编码、模型 `id` 与缺陷 `id`
文件内唯一）。未知工况编码与「一个工况命中多个模型」需要完整工况清单，**归 dynacalc
判定**；paramcad 不再提供第二套语义实现。

### D7 测试与门禁

`tests/apps/paramcad/` 覆盖：几何推导与 TBD 传播、分布场采样（含 TBD 样本）、缺陷
参数、预览几何（截面极值与几何一致、未标定曲线不画点）、导出结构与往返、解析错误
（缺单位 / 错单位 / 必填缺失 `null` / 枚举未知 / 样本非递增 / `uniform` 值 TBD）、
`case_scope` 结构自检、CLI 与窗口纯逻辑。

黄金指纹逐字节对拍契约第 12 节三个向量：`12.1` 示例模型、`12.2` 最小模型、
`12.3` v1.1 新增字段（`end_loose` + `circumferential`）。测试同时断言
`canonical_payload` 的完整 JSON 序列化文本与 64 位 digest 均与契约原文一致。

## 不变量

1. 不内置真实配方、材料参数、产品尺寸或「典型值」。
2. 不做求解、网格生成、LS-DYNA 关键字输出。
3. 缺工艺参数只产生 TBD，不做体积守恒、壁厚不变等隐式假设。
4. TBD 永远不是 `null`、空串、`0` 或 `-1`；TBD 必须带原因。
5. 分布场来源必填，导出后仍可在 `meta` 中追溯。
6. 导出、解析、校验、指纹只存在于 `_export.py` 一个边界。
7. 预览不使用外部图形库；无显示器时纯逻辑仍可测试。
8. 应用平铺在 `apps/paramcad/` 两层内，不加深、不新增子包。

## 契约对齐与遗留

v1.1 加法扩展已按契约落地，原先的缺口 1–5 均已关闭：

1. **端部疏松**：`density_field.end_loose{length_fraction,density}` 已导出 / 解析，
   与 `axial` 折线并列；
2. **周向壁厚**：`geometry.wall_thickness.circumferential` 支持 `uniform` / `polyline`
   / `tbd`，预览优先使用显式分布；
3. **折线样本 TBD**：文件层合法，解析不再报错，预览标「未标定」；
4. **指纹归一化**：按 8.1 的 12 条细则实现，并以第 12 节三个向量逐字节对拍；
5. **`case_scope` 语义**：只保留结构自检，未知编码与互斥交给 dynacalc。

仍需上报、未自行处理的一处契约歧义：

- 契约第 5 节「必须给出」表把 `geometry.length` 列为缺失或 TBD 都报错，但同节段落
  又说「`length` 与密度值可以是 TBD」。paramcad 按后者允许 `length: TBD`（预览
  相应标注），等待两侧统一措辞。

共享文件归属：`pyproject.toml` 的 `[project.scripts]` 与 `src/fcmd/cli/_manifest_data.py`
由 dynacalc 会话维护（DIGEST `db8e1fd15b44c6616340dcc305b72251`），paramcad 不再改动；
`docs/adr/README.md` 的 0020/0021 登记行也由该会话提交，如需修订先上报。

## 验收

- `fcmd paramcad example m.yaml` 写出可读模型；`inspect` 打印几何、工艺、分布场、
  缺陷、一致性与 TBD 清单；
- `fcmd paramcad digest m.yaml` 输出 64 位指纹，与 `export` 打印的指纹一致；
- 示例模型截面在 `θ=0 / 180°` 的壁厚分别等于 `wall_thickness.max / min`；
- 导出 YAML 能被本应用按契约重新解析，再次导出的文本与指纹逐字节一致；
- 契约第 12 节 `12.1` / `12.2` / `12.3` 三个向量的 canonical JSON 与 digest 逐字节一致；
- `ruff check`、`ruff format --check`、`pyrefly check`、`pytest tests/apps/paramcad` 通过。

## 落地状态

- 设计：已定稿（本 ADR）。
- 最小原型：已落地（示例 → 截面 / 分布预览 → 导出 → 再解析 / 指纹）。
- v1.1 对齐：已落地（端部疏松、周向壁厚、TBD 三分法、12 条归一化、黄金向量）。
- 待办：`length` TBD 的契约措辞统一；工具注册与静态清单统一处理。
