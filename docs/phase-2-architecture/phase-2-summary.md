# Phase 2 摘要 — DSE Trial 5（Arch-A5）

- **架构：** Arch-A5 SparseCal-SharedPool-CalFork-ZB-NoCombine
- **决策：** USER_CONFIRMED（在 Arch-A4 上继续面积优化）
- **无 Phase 4 / 无 combine / 无 DCA**

## 相对 Trial 4 的两项杠杆

| 杠杆 | Trial 4 | Trial 5 | 面积 Δ |
|---|---|---|---:|
| 多播 | FlooNoC-class MC 0.058 | **CalFork 0.025** | **−0.033** |
| BG 缓冲 | 共享池 40 + 预留 2 = 50 | **共享池 28 + 预留 2 = 38** | **−0.043** |
| **合计** | **0.822×** | **0.746×** | **−0.076** |

## PPA 分解

| 组件 | 相对面积 |
|---|---:|
| Crossbar | 0.380 |
| VC buffers（38 flit） | 0.139 |
| SparseCal | 0.009 |
| CalFork MC | 0.025 |
| Control | 0.193 |
| **总计 / 功耗** | **0.746× / 0.90×** |

## P0 保持

- 稀疏日历零缓冲回放；软优先级 BG；XY-DOR demote 无损
- Tier A；日历永不占用共享池
- 死锁论证：XY-DOR + 预留=2 + 日历隔离
- BG 上界：硬 328 / 软 ~160 / 软+池 ~188

## 灵敏度

- 池 **24+2=34**：RefC PASS，面积 ~0.731（未作默认，保留余量）
- 仅 CalFork（池仍 40）：~0.789

## 关键工件

- `architecture.md` / `architecture-diagram.md` / `architecture-candidates.md`
- `ppa-analytic.md` / `utils/ppa_analytic_model.py`
- ADR-002 / ADR-004 / ADR-005
