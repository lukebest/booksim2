# DSE Trial 3 报告 — Arch-A3 SparseCal-Hybrid-ZB-NoCombine

- **日期：** 2026-07-10
- **工作区：** `/home/luke/workspace/booksim2-dse-trial3`
- **决策来源：** USER_CONFIRMED
- **阶段范围：** Phase 1 轻触更新 + Phase 2/3 完整文档；**无 Phase 4**

---

## 1. 架构名称与定位

**Arch-A3 SparseCal-Hybrid-ZB-NoCombine** 是 Trial 2 Arch-A2 的稀疏日历演进：

| 维度 | Trial 2 Arch-A2 | **Trial 3 Arch-A3** |
|---|---|---|
| 日历存储 | 稠密 `2×1024×13` SRAM | **稀疏有序事件表 `2×128×23`** |
| 调度方式 | slot 索引直读 | **next-event 匹配**（`entry.slot == counter`） |
| BG 仲裁 | 硬 1-in-16 TDM | **软优先级**（无匹配周期 BG 可用） |
| combine/DCA | 无（Tier A） | 无（Tier A，不变） |
| 面积 vs IQ-XY | 1.028× | **1.000×** |
| 功耗 vs IQ-XY | 0.96× | **0.95×** |

保留不变：零缓冲日历路径、原子多播 fork、watchdog demote→XY、单 512b 网络、6×8、H=7 V=9、2 GHz。

---

## 2. PPA 对比

归一化基线：五端口 512-bit IQ-XY = 1.00（`ppa-analytic.md`，`utils/ppa_analytic_model.py`）。

| 候选 | 面积 | 功耗 | vs IQ-XY | vs Trial 2 |
|---|---:|---:|---|---|
| IQ-XY 基线 | 1.000 | 1.00 | — | — |
| Trial 1 Arch-A | 1.065 | 0.98 | +6.5% / −2% | — |
| Trial 2 Arch-A2 | 1.028 | 0.96 | +2.8% / −4% | — |
| **Trial 3 Arch-A3** | **1.000** | **0.95** | **0% / −5%** | **−0.028 / −0.01** |

### 面积分解（Arch-A3）

| 组件 | 相对面积 | 说明 |
|---|---:|---|
| Crossbar | 0.380 | 不变 |
| VC buffers | 0.365 | 100 flit BG/escape（不变） |
| Calendar | **0.009** | 稀疏 2×128×23（Trial 2 为 0.040） |
| Multicast | 0.058 | FlooNoC +5.8% |
| Combine/DCA | 0.000 | Tier A |
| Control | **0.188** | +0.003 next-event 匹配 |
| **合计** | **1.000** | |

主要收益：日历 SRAM −0.031；控制 +0.003；净 −0.028。

---

## 3. 稀疏深度 128 与证据

硬件每路由器每 bank **128 条** 23-bit 事件：`{slot[9:0], valid, in_port, out_port_mask, opcode}`。

数据来源：`results/calendars/*_m1.json`

| 集合通信 | 总条目 | 均值/路由器 | 最大/路由器 | max_slot |
|---|---:|---:|---:|---:|
| broadcast | 48 | 1 | 1 | 99 |
| allgather | 192 | 4 | 4 | 699 |
| gather / reduce | 336 | 7 | 48 | 851 |
| allreduce | 384 | 8 | **49** | **951** |

- 相对稠密 `48×1024` 密度 **≪ 1%**
- allreduce 单路由器最大 **49** 条 → 深度 128 余量 **>2×**
- 全局 slot 计数器回绕 **1024**，max_slot≈951 仍安全
- 双 bank 热切换（epoch handoff @ slot 0）与 Trial 2 一致

---

## 4. 软优先级决策

**已选定：软优先级（soft-prio）**

- 有匹配稀疏事件时，日历拥有该周期（零缓冲路径）
- 无匹配时，BG/escape 可用（信用 XY-DOR VC）
- BG **永不** displaces 正在触发的日历事件
- 硬 1-in-16 TDM **放宽**为保守上界参考，仍可用于合规论证

### BG 递送上界（12-hop，|dx|=5，|dy|=7）

| 策略 | 周期 |
|---|---:|
| 保守硬 1-in-16 | **328** |
| 软优先级占用感知 | **~160** |

---

## 5. Tier A 与不变项

- **无** `combine_unit`、**无** DCA 数据通路
- reduce = calendar gather + PE 本地计算
- allreduce = gather → PE → calendar broadcast
- 共享 BG buffer pool：**不在范围内**（仅作未来 Trial 3b 提及）

ADR-003（Tier A）实质不变。

---

## 6. 图表路径

| 类型 | 路径 |
|---|---|
| 架构规格 | `docs/phase-2-architecture/architecture.md` |
| 架构 Mermaid 图 | `docs/phase-2-architecture/architecture-diagram.md` |
| 候选对比 | `docs/phase-2-architecture/architecture-candidates.md` |
| PPA 分析 | `docs/phase-2-architecture/ppa-analytic.md` |
| PPA 公式 | `docs/phase-2-architecture/ppa-workbook.md` |
| Phase 2 摘要（中文） | `docs/phase-2-architecture/phase-2-summary.md` |
| μArch 规格 | `docs/phase-3-uarch/uarch.md` |
| μArch Mermaid 图 | `docs/phase-3-uarch/uarch-diagram.md` |
| 日历 JSON 模式 | `docs/phase-3-uarch/calendar-export-schema.md` |
| Phase 3 摘要（中文） | `docs/phase-3-uarch/phase-3-summary.md` |

---

## 7. 决策记录与 Iron

| ADR | 内容 |
|---|---|
| ADR-002 | Arch-A3 选定（Trial 2 被取代） |
| ADR-003 | Tier A（不变） |
| ADR-004 | SparseCal PPA 1.000× |

Iron 更新（ID 保留，trial=3）：

- Phase 2：`docs/phase-2-architecture/iron-requirements.json`（REQ-A-001..006）
- Phase 3：`docs/phase-3-uarch/iron-requirements.json`（REQ-U-001..006）
- 可追溯性：`docs/phase-3-uarch/req-uarch-traceability.md`（100%）

Phase 1 轻触：`phase-1-summary.md` 顶部注记 + `domain-analysis.md` §1 SparseCal 候选。

---

## 8. 对比就绪状态

Trial 3 文档集可用于与 Trial 1/2 横向对比：

- 架构名、PPA 数值、稀疏证据、软优先级上界均已写入 Phase 2/3 工件
- **无 Phase 4**（无 SystemVerilog、无 RTL 实现）
- 可复现 PPA：`python3 utils/ppa_analytic_model.py`

### 关键验证数值速查

| 声明 | 值 |
|---|---|
| 架构名 | Arch-A3 SparseCal-Hybrid-ZB-NoCombine |
| 面积 vs IQ-XY | **1.000×** |
| 功耗 vs IQ-XY | **0.95×** |
| vs Trial 2 面积/功耗 | **−0.028 / −0.01** |
| 日历存储 | 2×128×23 = **5,888 bit**（面积类 **0.009**） |
| 控制 | **0.188**（+0.003 match） |
| 稀疏深度 | **128**/bank |
| allreduce max/router | **49** |
| max_slot | **951** |
| 硬 BG 上界 | **328** cy |
| 软 BG 上界 | **~160** cy |
