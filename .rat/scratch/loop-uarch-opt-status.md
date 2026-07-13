# μArch 优化 Loop 状态 — 已收敛停止

- **模式：** 动态 self-pacing（已停止，不再 arm wake）
- **最终最优：** **Arch-A5 SparseCal-SharedPool-CalFork-ZB-NoCombine**
- **面积 / 功耗：** **0.746× / 0.90×** vs IQ-XY（相对基线 −25.4% / −10%）
- **基线 commit：** `0c58e3e`
- **停止原因：** tick 2 判定 crossbar（0.380）在绑定 512b/5-port 下不可再砍

## 迭代轨迹

| Trial | 架构要点 | Area | Power |
|---:|---|---:|---:|
| 1 | CalSlot + Tier B combine | 1.065 | 0.98 |
| 2 | 去 combine（Tier A） | 1.028 | 0.96 |
| 3 | SparseCal 2×128 | 1.000 | 0.95 |
| 4 | SharedPool 40+2 | 0.822 | 0.92 |
| **5** | **CalFork + pool 28+2** | **0.746** | **0.90** |

## Crossbar 评估（tick 2）

见 [`xb-cut-feasibility.md`](xb-cut-feasibility.md)。缩位宽/减端口违反规格；分时 XB 破坏零缓冲日历与原子多播。**无 Trial 6。**

## 面积剩余（接受为物理下限）

| 部件 | 相对面积 | 状态 |
|---|---:|---|
| Crossbar 5×5×512b | 0.380（~51%） | **绑定物理下限** |
| Control | 0.193 | 边际 |
| Buffers 38 flit | 0.139 | 已优化（24+2 可选更激进） |
| CalFork MC | 0.025 | 已优化 |
| SparseCal | 0.009 | 已优化 |

## 交付入口

- 中文结论文档：[`loop-optimal-conclusion-zh.md`](loop-optimal-conclusion-zh.md)
- Trial 5 报告：[`trial5-report-zh.md`](trial5-report-zh.md)
- 架构/微架构图：`docs/phase-2-architecture/architecture-diagram.md`、`docs/phase-3-uarch/uarch-diagram.md`
