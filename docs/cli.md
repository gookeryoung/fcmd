# CLI 最短命令序列

本页只列跑通「参数化建模 → 网格 → 装药算例」链路的最短命令，详细参数用
`fcmd <tool> <subcommand> --help` 查看。所有示例模型与示例参数都是**示例占位**，
不含真实配方、材料参数或产品尺寸。

## dynacalc 一键演示

```powershell
fcmd dynacalc demo --out demo
```

内部依次执行：写示例参数（`demo/params.yaml`）→ 生成典型工况 01/02/03
（`demo/decks`）→ 假求解器 → 汇总（`demo/report.txt`）→ 对比 T01 vs T02 →
四项门控。不需要安装 LS-DYNA；假求解器数据只验证链路，不代表物理结论。

加 `--full` 则生成并运行全部工况（主矩阵 48 组 + 五项补充组 12 组 + 典型工况 3 组）：

```powershell
fcmd dynacalc demo --out demo --full
```

## 接入 paramcad 示例模型与 paramesh 三档网格

```powershell
fcmd paramcad example model.yaml --overwrite
fcmd paramesh example meshcfg.yaml --overwrite
fcmd paramesh generate model.yaml meshes --config meshcfg.mesh.yaml
fcmd dynacalc demo --out demo --model model.yaml --mesh-root meshes --mesh-level medium
```

- paramcad 示例模型的 digest 为
  `ed6f597a09a71381bfc86998daaade08e3276fa721a2e54d66abf51463f28dc4`；它没有
  `case_scope`，因此绑定示例参数里的全部工况（含 T01/02/03）。
- `paramesh generate` 需要网格配置；`paramesh example` 写出的占位配置可直接用
  （档位 1/1、1/2、1/4 只用于快速预览，正式使用改成 8/16/32）。
- 给出 `--mesh-root` 时必须同时给 `--model`：网格按模型指纹绑定，模板自动切到
  `axisymmetric-2d`；`--mesh-level` 取 `coarse` / `medium` / `fine`。

## 分步执行（不依赖一键演示）

```powershell
fcmd dynacalc example --out params.yaml
fcmd dynacalc params params.yaml
fcmd dynacalc matrix params.yaml
fcmd dynacalc generate params.yaml out --model model.yaml
fcmd dynacalc run out --plan-only
fcmd dynacalc run out --fake --concurrency 4
fcmd dynacalc report out --report-out report.csv --output-format csv
fcmd dynacalc compare out T01 T02
fcmd dynacalc gates out
```

有网格时：

```powershell
fcmd dynacalc generate params.yaml out --model model.yaml --mesh-root meshes --mesh-level medium
```

`gates` 的基准工况与配对来自参数文件的 `defaults.gates.reference` / `pairs`
（示例是 `T01` 对 `T02`、`T03`）；典型工况在汇总与门控里会标注「典型工况」，
与主矩阵、「补充组」区分。
