# DSE Trial 5 报告 — Arch-A5 SparseCal-SharedPool-CalFork-ZB-NoCombine

- **日期：** 2026-07-13
- **工作区：** `/home/luke/workspace/booksim2-dse-trial5`
- **分支：** `dse-trial5`
- **决策来源：** USER_CONFIRMED（在 Arch-A4 上继续 μArch 面积优化）
- **阶段范围：** Phase 1 继承 + Phase 2/3 完整更新；**无 Phase 4**

---

## 1. 架构名称与选型

**Arch-A5 SparseCal-SharedPool-CalFork-ZB-NoCombine** = Trial 4 Arch-A4 + CalFork + 更激进 SharedPool。

| 维度 | Trial 4 Arch-A4 | **Trial 5 Arch-A5** |
|---|---|---|
| 日历 | 稀疏 `2×128×23`，next-event | **不变** |
| BG 仲裁 | 软优先级 | **不变** |
| 多播 | FlooNoC-class MC **0.058** | **CalFork lean MC 0.025** |
| BG 缓冲 | 共享池 40 + 预留 2 = **50** | **共享池 28 + 预留 2 = 38** |
| combine/DCA | 无（Tier A） | **无（Tier A）** |
| 面积 vs IQ-XY | 0.822× | **0.746×** |
| 功耗 vs IQ-XY | 0.92× | **0.90×** |

保留：零缓冲日历、原子多播 fork（现为 CalFork）、watchdog demote→XY、单 512b 网络、6×8、H=7 V=9、ramp_bw=1、2 GHz。

### 杠杆与选型理由

1. **主杠杆 CalFork：** 日历事件已含 `out_port_mask[4:0]`，用掩码展开 + 全有或全无信用提交，**不做**通用 stream_fork FSM。MC **0.058 → 0.025（−0.033）**。
2. **次杠杆 SharedPool：** 默认 **28+2=38**（RefC 全 PASS）。**24+2=34** 亦 PASS，面积 ~0.731，作灵敏度记录，不作为默认（多保留共享深度）。

---

## 2. PPA 对比

归一化基线：五端口 512-bit IQ-XY = 1.00。

| 候选 | 面积 | 功耗 | vs IQ-XY | vs Trial 4 |
|---|---:|---:|---|---|
| IQ-XY | 1.000 | 1.00 | — | — |
| Trial 4 Arch-A4 | 0.822 | 0.92 | −17.8% / −8% | — |
| **Trial 5 Arch-A5** | **0.746** | **0.90** | **−25.4% / −10%** | **−0.076 / −0.02** |
| 仅 CalFork（池仍 40） | 0.789 | ~0.91 | — | CalFork 单独 |
| 池 24+2 灵敏度 | 0.731 | ~0.90 | — | 更激进（未默认） |

### 面积分解（Arch-A5）

| 组件 | 相对面积 | 说明 |
|---|---:|---|
| Crossbar | 0.380 | 不变，约占总面积 51% |
| VC buffers | **0.139** | 38 flit SharedPool |
| Calendar | 0.009 | 稀疏 2×128×23 |
| Multicast | **0.025** | CalFork（Δ −0.033） |
| Combine/DCA | 0.000 | Tier A |
| Control | 0.193 | 匹配 + 池记账 |
| **合计** | **0.746** | |

**MC 增量：** 0.058 → 0.025（**−0.033**）。

---

## 3. 必须证明的四点（P0）

### 3.1 死锁自由（共享池 + XY-DOR）

与 Trial 4 同构：XY-DOR 无环；日历零缓冲不占池；预留=2 防饿死；demote→池属同一 XY 类。

### 3.2 BG 递送上界（12-hop）

| 策略 | 周期 |
|---|---:|
| 保守硬 1-in-16 | **328** |
| 软优先级（预留覆盖） | **~160** |
| 软 + 共享池争用 | **~188**（160+≤28） |

### 3.3 日历路径不受影响

- 仍为零缓冲；**CalFork** 仅展开 `out_port_mask`
- 分类器对日历 flit **永不** `enqueue` 进共享池

### 3.4 Demote→XY 无损

- `test_demote_noloss` / `test_blocked_fork` **PASS**

---

## 4. 图表与文档路径

| 类型 | 路径 |
|---|---|
| 架构规格 | `docs/phase-2-architecture/architecture.md` |
| 架构图 | `docs/phase-2-architecture/architecture-diagram.md` |
| μArch 规格 | `docs/phase-3-uarch/uarch.md` |
| μArch 图 | `docs/phase-3-uarch/uarch-diagram.md` |
| PPA | `docs/phase-2-architecture/ppa-analytic.md` |
| Phase 2 摘要 | `docs/phase-2-architecture/phase-2-summary.md` |
| Phase 3 摘要 | `docs/phase-3-uarch/phase-3-summary.md` |
| 本报告 | `.rat/scratch/trial5-report-zh.md` |

---

## 5. 决策与 Iron

| ADR | 内容 |
|---|---|
| ADR-002 | Arch-A5 选定 |
| ADR-003 | Tier A（不变） |
| ADR-004 | PPA 0.746× / 0.90× |
| ADR-005 | CalFork lean multicast |

Iron：`docs/phase-2-architecture/iron-requirements.json`、`docs/phase-3-uarch/iron-requirements.json`（trial=5）；可追溯性 100%。

---

## 6. 测试状态

| 测试 | 结果 |
|---|---|
| RefC smoke / demote / bg_window / blocked_fork / bg_bound | **PASS** |
| `test_shared_pool`（pool=28, total=38） | **PASS** |
| 灵敏度：pool=24 | **PASS**（未作默认） |
| BFM smoke | **PASS** |

复现 PPA：`python3 utils/ppa_analytic_model.py [--sensitivity]`

---

## 7. 收敛判断（给外环）

**结论：Arch-A5 已接近本杠杆集下的近优解；建议停止继续抠缓冲/多播，或仅在愿意动交叉开关时开下一 trial。**

| 剩余质量 | 相对面积 | 占 A5 总量 |
|---|---:|---:|
| Crossbar | **0.380** | **~51%** |
| Control | 0.193 | ~26% |
| Buffers | 0.139 | ~19% |
| CalFork MC | 0.025 | ~3% |
| Calendar | 0.009 | ~1% |

- 再砍池到 24：仅再省 **~0.015**（0.746→0.731），收益很小。
- CalFork 已在 0.020–0.030 带中点；再压到 0.020 最多再省 **0.005**。
- **真正大头是 crossbar 0.380**——下一杠杆若继续面积优先，需评估：窄化/串行化交叉开关、分时复用端口、或降低 flit 宽度（均可能伤吞吐/延迟，超出当前 P0 无痛面积微调范围）。

**converge? yes**（就 SharedPool + CalFork 杠杆而言）  
**若 no 的下一杠杆：** Crossbar / 数据通路宽度重构（高风险，需新 trial 明确性能预算）。

---

## 8. 提升就绪

- 预实现包完整（P1 继承 + P2/P3 更新）
- **无 Phase 4**（无 RTL）
- 可与 Trial 4（当前 best 0.822×）对比后 promote
