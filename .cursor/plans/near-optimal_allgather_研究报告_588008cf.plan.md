---
name: Near-optimal Allgather 研究报告
overview: 在 2D mesh（4x4~64x64、6x8、12x16）× 数据大小（1~5 flit）× 下 ramp 带宽（1/2 flit/cycle/node）的全空间上，做 allgather 下界分析 + 方案仿真扫描，生成热力图与研究报告，并产出按 (规模, 数据量, 带宽) 自动选择最优方案的 autogen 生成器。
todos:
  - id: lb
    content: 实现下界分析脚本 allgather_lower_bounds.py 并输出 JSON
    status: completed
  - id: sweep
    content: 实现规模×数据量×带宽扫描脚本 sweep_allgather_scale.py（复用 sched_zerobuf_compare）
    status: completed
  - id: big
    content: 优化 64x64 大规模打包性能并完成全量扫描
    status: completed
  - id: ilp
    content: 小规模 CP-SAT 交叉验证
    status: completed
  - id: report
    content: 生成热力图与 HTML 研究报告
    status: completed
  - id: autogen
    content: 实现 autogen_allgather.py 并跑 70 组合回归验证
    status: completed
isProject: false
---

# Near-optimal Allgather 研究报告与 Autogen 方案

## 物理条件（全程固定）
- 链路带宽 1 flit/cycle/方向；横向 link delay H=4 cy，纵向 V=6 cy；ramp 延迟 1 cy
- 下 ramp 带宽 `ramp_bw ∈ {1, 2}` flit/cycle/node
- 规模：4x4, 8x8, 16x16, 32x32, 64x64（边缘节点 ×2 步进），外加 6x8, 12x16
- 数据大小 m ∈ {1..5} flit/节点

## 阶段 1：下界分析（新脚本 `utils/allgather_lower_bounds.py`）
对每个 (MX, MY, m, ramp_bw) 计算三类下界并取 max 作为理论值 T：
- **弹出下界**：`ceil((N-1)*m / ramp_bw)`（每节点必须下 ramp 收 (N-1)*m flit）
- **角节点链路下界**：角节点只有 2 条入链路（1 横 + 1 纵），须经其接收全部其他节点数据，`ceil((N-1)*m / 2)`（ramp_bw≥2 时成为紧下界）
- **延迟下界**：最远节点对的最短路延迟 `(MX-1)*H + (MY-1)*V + up/down ramp`
- **二分带宽下界**：`ceil((N/2)*m*(N/2) / MY_cut_links)` 型的跨切割流量下界（横/纵两切各算一次取 max）
输出 `results/allgather_lb.json`。

## 阶段 2：方案仿真扫描（新脚本 `utils/sweep_allgather_scale.py`）
复用 [utils/sched_zerobuf_compare.py](utils/sched_zerobuf_compare.py) 的 `cfg()`、footprint 构建器与 `pack(..., flits=)` / `verify()`：
- 方案族：multitree、ring_uni/bi（Hamilton snake，各规模均偶数维可行）、hybrid_uni/bi（B 扫 MY 的因子）、hybrid_v_uni/bi（B 扫 MX 的因子）、quad_uni/bi、border（偶数维规模）
- 对每个 (规模, m, ramp_bw) 跑全部方案 + 既有源顺序启发式，记录各方案 makespan、最优方案及其参数（如 B 值、bidir、源顺序）
- 64x64（4096 节点）打包量大：贪心打包按需做增量占用表（参考 `search_hybrid_v_opt.py` 的 `OccTracker`），必要时对 64x64 只跑经验上占优的方案族（hybrid_v_bi / hybrid_bi / multitree）
- 小规模（4x4，可选 6x8）用 [utils/sched_ilp.py](utils/sched_ilp.py) CP-SAT 求最优值交叉验证贪心结果
输出 `results/allgather_scale_sweep.json`（含每格 best_mk、best_scheme、lb、ratio=mk/T）。

## 阶段 3：热力图 + 研究报告（新脚本 `utils/gen_allgather_scale_report.py`）
仿照 [utils/gen_fork_msg_size_heatmap.py](utils/gen_fork_msg_size_heatmap.py) 的内联 SVG/HTML 风格，生成 `results/report_allgather_scale.html`：
- 每个 ramp_bw 一张热力图：横轴 m=1..5，纵轴规模（按节点数排序：4x4, 6x8, 8x8, 12x16, 16x16, 32x32, 64x64），颜色 = best_mk / T（越接近 1 越优）
- 单元格标注比值和最优方案名；附最优方案分布图（同网格，颜色按方案族）
- 报告章节：物理模型与假设 → 下界推导 → 各方案族原理图 → 扫描结果与热力图 → 结论（何种规模/数据量/带宽下哪类方案 near-optimal）

## 阶段 4：Autogen 方案生成器（新脚本 `utils/autogen_allgather.py`）
- 从 `allgather_scale_sweep.json` 读取查找表，接口 `gen_schedule(mx, my, flits, ramp_bw)` 返回最优方案的完整调度（每源注入 offset + footprint 槽位，格式与 `apply_offsets()` 一致），并内置 `verify()` 自检
- 提供 CLI：`python utils/autogen_allgather.py --mx 16 --my 16 --m 3 --bw 2 --json out.json`
- 对全部 7 规模 × 5 数据量 × 2 带宽 = 70 组合做一遍生成+验证回归

## 验证标准
- 所有扫描点 `verify()` 通过（链路/ramp 容量不超、每节点收满 (N-1)*m flit）
- 4x4 上贪心最优值与 CP-SAT 最优值差距记录在报告中
- 热力图比值 ≥ 1（若出现 <1 说明下界或仿真有 bug，需排查）