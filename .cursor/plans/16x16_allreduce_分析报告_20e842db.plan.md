---
name: 16x16 AllReduce 分析报告
overview: 在 16x16 mesh（H=4, V=6, ramp=1, 0-buffer router）上推导 allreduce 理论下界，探索并实现最优无冲突零缓冲 allreduce 调度，再对链路/节点/reticle（象限）故障给出处理算法与降级数据，最终生成 HTML 报告。
todos:
  - id: bound
    content: 实现 utils/allreduce_bound.py:16x16 allreduce 多重理论下界
    status: completed
  - id: schemes
    content: 实现 utils/sim_allreduce_16x16.py:三类零缓冲无冲突方案并选优
    status: completed
  - id: faults
    content: 实现 utils/run_allreduce_fault.py:链路/节点/reticle 故障恢复与扫描,输出 CSV
    status: completed
  - id: report
    content: 实现 utils/gen_allreduce_report.py 并生成 results/allreduce_report.html
    status: completed
isProject: false
---

# 16x16 AllReduce 下界、最优零缓冲方案与故障分析报告

## 假设（沿用现有 ring 故障研究口径）
- 拓扑：16x16 2D mesh（非 torus），N=256；链路延迟 H=4（横向）、V=6（纵向）；PE↔router ramp=1 cycle，`ramp_bw=1`（双向方案沿用 `sched_zerobuf_compare` 的 ramp_bw=2 口径做对照）。
- Router 0 buffer：复用 `utils/sched_zerobuf_compare.py` 的 rigid footprint packer（`pack`/`verify`），唯一自由度是源端注入偏移；每条有向链路每周期至多 1 flit。
- 消息规模 M 做 sweep（M=1~6），各方案与下界均按 M 参数化给出。
- 归约（reduce op）时延参数化 `R_LAT`（默认 2 cycle）：flit 在归约点合并需额外 R_LAT 周期，计入路径 footprint 与下界推导。
- 纯 mesh 模型，不含 reticle/AFIFO 跨界延迟；"reticle 故障" = 8x8 象限整体失效（64 节点），与 `hamilton_ring.quadrant_fault_scenarios` 定义一致。

## 阶段 1：理论下界分析
新建 `utils/allreduce_bound.py`，推导并输出 16x16 allreduce 的多个下界并取 max：
- 带宽/ramp 下界：每节点需吐出并吸收的最小 flit 量（reduce-scatter+allgather 视角：每节点下行 ramp 至少 (N-1)·M/N·2 量级，按 ramp_bw 折算），按 M 参数化。
- 直径/延迟下界：最远两节点信息必须相互影响 → ≥ 2×(半径路径延迟) + 2×ramp + 路径上必要的归约次数×R_LAT（16x16 直径 = 15H+15V = 150 cy）。
- 二分带宽下界：跨 bisection 的最小数据量 / 跨界链路数。
- 所有下界对 M=1~6、R_LAT（默认 2）参数化输出表格。
- 参考 `src/mesh_graph.cpp::TheoBound("allreduce")` 与 `utils/gen_report.py::theory_table()` 的 12x16 公式，移植到 16x16。

## 阶段 2：最优无冲突零缓冲方案探索
新建 `utils/sim_allreduce_16x16.py`，实现并对比 3 类候选（均过 0-buffer packer 验证无冲突）：
1. **Ring RS+AG**：Hamilton snake ring（`hamilton_ring.snake_cycle`）上 reduce-scatter + allgather，单向/双向两版。
2. **树形 reduce + broadcast**：移植 `src/collective.cpp::PlanAllReduce` 思路到 Python，中心根，时移拼接。
3. **维序多树 / hybrid**：借鉴 `sched_zerobuf_compare` 的 multitree 与 `sim_hybrid_v_fault` 的 B=2 竖带混合结构，做 reduce 方向反演（allgather 树反向即 reduce 树）。

每个方案在 M=1~6 下输出 makespan、对下界的效率比、busiest link 占用（归约点计入 R_LAT=2 时延）；选出最优方案并保留其完整调度表（可导出 events 供报告绘图）。用 `verify` 断言链路/ramp 无冲突，作为正确性验收。

## 阶段 3：故障处理算法与评估
新建 `utils/run_allreduce_fault.py`，对最优方案在三类故障下给出恢复算法与量化结果：
- **链路故障**（1~3 条，corner/edge/center）：复用 `hamilton_ring.find_ring` 重找路径 / 树重挂载，重新 pack 得故障 makespan。
- **节点故障**（1x1 / 2x2 / 3x3 洞）：死节点退出成员集；ring 方案用 `find_ring_rebalanced`（含牺牲节点策略），树方案重建生成树。
- **reticle 故障**（整象限失效）：退化为 192 节点 allreduce，沿用 `quadrant_fault_scenarios` 的恢复思路。
- 故障扫描以 M=1 为主档、M=6 为对照档（避免全 M×全场景组合爆炸）。
- 输出 CSV：`results/allreduce_results.csv`（fault_class, detail, M, makespan, golden, slowdown_pct, feasible 等，列结构对齐 `ring_results.csv`）。

## 阶段 4：报告生成
新建 `utils/gen_allreduce_report.py`（模板参考 `utils/gen_ring_report.py`），生成自包含中文 HTML `results/allreduce_report.html`：
- 第 1 节：模型假设 + 理论下界推导（含公式与各下界对比表）。
- 第 2 节：候选方案对比 + 最优方案调度说明（SVG 示意图：ring/树结构、时空图）。
- 第 3 节：三类故障的处理算法描述 + 降级数据表/柱状图 + 故障拓扑 SVG（红=死件、蓝=恢复结构）。

## 验收标准
- packer `verify` 对所有健康/故障场景通过（无链路/ramp 冲突、0 router buffer）。
- 最优方案 makespan ≥ 理论下界，效率比在报告中给出。
- 报告可直接在浏览器打开，风格与 `ring_report.html` 一致。