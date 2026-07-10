# DSE Trial 4 报告 — Arch-A4 SparseCal-SharedPool-ZB-NoCombine

- **日期：** 2026-07-10
- **工作区：** `/home/luke/workspace/booksim2-dse-trial4`
- **分支：** `dse-trial4`
- **决策来源：** USER_CONFIRMED（Trial 3b SharedPool-BG 面积续减）
- **阶段范围：** Phase 1 继承 + Phase 2/3 完整更新；**无 Phase 4**

---

## 1. 架构名称与选型

**Arch-A4 SparseCal-SharedPool-ZB-NoCombine** = Trial 3 Arch-A3 + SharedPool-BG。

| 维度 | Trial 3 Arch-A3 | **Trial 4 Arch-A4** |
|---|---|---|
| 日历 | 稀疏 `2×128×23`，next-event | **不变** |
| BG 仲裁 | 软优先级 | **不变** |
| BG 缓冲 | 专用 5×20=**100** flit | **共享池 40 + 预留 5×2=50** |
| combine/DCA | 无（Tier A） | **无（Tier A）** |
| 面积 vs IQ-XY | 1.000× | **0.822×** |
| 功耗 vs IQ-XY | 0.95× | **0.92×** |

保留：零缓冲日历、原子多播 fork、watchdog demote→XY、单 512b 网络、6×8、H=7 V=9、ramp_bw=1、2 GHz。

### 池/预留选型理由

- **默认：共享池 40 + 每端口预留 2 → 合计 50 flit**
- 缓冲面积 `0.365×(50/100)=0.182` ∈ 目标 **0.15–0.22**
- 总面积 **0.822** 优于目标带 **0.85–0.92**（更低更好）
- 备选 48+2≈58 flit（~0.852）仅在死锁/前进证据不足时启用——本 trial 不需要

---

## 2. PPA 对比

归一化基线：五端口 512-bit IQ-XY = 1.00。

| 候选 | 面积 | 功耗 | vs IQ-XY | vs Trial 3 |
|---|---:|---:|---|---|
| IQ-XY | 1.000 | 1.00 | — | — |
| Trial 3 Arch-A3 | 1.000 | 0.95 | 0% / −5% | — |
| **Trial 4 Arch-A4** | **0.822** | **0.92** | **−17.8% / −8%** | **−0.178 / −0.03** |

### 面积分解（Arch-A4）

| 组件 | 相对面积 | 说明 |
|---|---:|---|
| Crossbar | 0.380 | 不变 |
| VC buffers | **0.182** | 50 flit SharedPool |
| Calendar | 0.009 | 稀疏 2×128×23 |
| Multicast | 0.058 | 不变 |
| Combine/DCA | 0.000 | Tier A |
| Control | **0.193** | +0.005 池记账 |
| **合计** | **0.822** | |

---

## 3. 必须证明的四点

### 3.1 死锁自由（共享池 + XY-DOR）

1. XY-DOR 在 mesh 上无环（经典结论）
2. 日历零缓冲，**永不占用**池信用 → 不参与池信用环
3. 每端口预留 2：共享池耗尽时端口仍可前进（防饿死）
4. 池槽在 flit 沿 DOR 离站时释放；下游 credit_fc 独立限流
5. Demote→XY 进入池/预留，同属死锁自由 XY 类

⇒ **不存在对池信用的循环等待。**

### 3.2 BG 递送上界（12-hop，|dx|=5，|dy|=7）

| 策略 | 周期 |
|---|---:|
| 保守硬 1-in-16 | **328** |
| 软优先级（预留覆盖，单 flit/≤2 深） | **~160** |
| 软 + 共享池争用（保守） | **~200**（160+≤40 池周转） |

### 3.3 日历路径不受影响

- 仍为零缓冲事件路径
- 分类器对日历 flit **永不** `enqueue` 进共享池
- 软优先级：匹配事件时日历独占周期

### 3.4 Demote→XY 无损

- watchdog 释放一次预约
- escape 进入 **池/预留**（非日历存储）
- `test_demote_noloss` / `test_blocked_fork` PASS

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
| 本报告 | `.rat/scratch/trial4-report-zh.md` |

---

## 5. 决策与 Iron

| ADR | 内容 |
|---|---|
| ADR-002 | Arch-A4 SharedPool 选定 |
| ADR-003 | Tier A（再确认） |
| ADR-004 | PPA 0.822× / 0.92× |

Iron：`docs/phase-2-architecture/iron-requirements.json`、`docs/phase-3-uarch/iron-requirements.json`（trial=4）；可追溯性 100%。

---

## 6. 测试状态

| 测试 | 结果 |
|---|---|
| RefC smoke / demote / bg_window / blocked_fork / bg_bound | **PASS** |
| `test_shared_pool`（新增） | **PASS** |
| BFM smoke | **PASS** |

复现 PPA：`python3 utils/ppa_analytic_model.py`

---

## 7. 提升就绪

- 预实现包完整（P1 继承 + P2/P3 更新）
- **无 Phase 4**（无 RTL）
- 可与 Trial 1/2/3 对比后 promote
