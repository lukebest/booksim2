# DSE μArch 优化结论文档（中文）

**日期：** 2026-07-13  
**结论：** 在现行绑定约束下，最优微架构为 **Arch-A5 SparseCal-SharedPool-CalFork-ZB-NoCombine**。  
**分析 PPA：** 相对 IQ-XY 基线 **面积 0.746×、功耗 0.90×**。  
**Loop：** 已停止（不再继续自动 Trial）。

---

## 1. 绑定约束（未放宽）

- 6×8 mesh；单物理 NoC；flit **64 B（512b）** @ 2 GHz  
- Calendar 集合通信 + XY 背景流共享网络  
- Tier A：无 router 内归约 / 无 DCA  
- 高鲁棒：死锁自由、不丢包、违规 demote→XY  
- 多播：日历原子 `out_port_mask` fork  
- 不进 Phase 4 RTL（本轮 DSE 范围）

---

## 2. 为何 Arch-A5 是最优

逐步吃掉可微架构化的面积：

1. 去掉 combine（Tier A）  
2. 稠密日历 → **稀疏事件表 2×128**  
3. 分端口 BG FIFO → **共享池**  
4. 通用多播 → **日历原生 CalFork**  
5. 池再瘦到 **28+2=38 flit**

剩余最大头是 **5×5×512b 交叉开关（相对面积 0.380，约占 Arch-A5 的 51%）**。在不改 flit 位宽与端口数时，缩 XB 会破坏「每拍一 flit」与零缓冲日历/原子多播语义，故判定为 **物理下限而非未优化杠杆**。

边际选项（池 24+2 → 约 0.731×）RefC 可通过，但收益 <2%、风险更高，不作为默认最优。

---

## 3. Arch-A5 一句话画像

稀疏日历事件表 + 软优先级 BG + 共享池（28+2）+ 日历原生多播 fork + watchdog 无损降级；无网内归约。

---

## 4. 相关路径

| 文档 | 路径 |
|---|---|
| Trial 5 报告 | `.rat/scratch/trial5-report-zh.md` |
| XB 可行性 | `.rat/scratch/xb-cut-feasibility.md` |
| Loop 状态 | `.rat/scratch/loop-uarch-opt-status.md` |
| 架构图 | `docs/phase-2-architecture/architecture-diagram.md` |
| 微架构图 | `docs/phase-3-uarch/uarch-diagram.md` |
| PPA | `docs/phase-2-architecture/ppa-analytic.md` |

若要继续降面积，必须 **显式改物理假设**（例如更窄 flit、双网、或接受多拍开关），那是新一轮规格而非本 loop 的微架构微调。
