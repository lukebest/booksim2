# Phase 2 摘要 — DSE Trial 4（Arch-A4）

- **架构：** Arch-A4 SparseCal-SharedPool-ZB-NoCombine
- **决策：** USER_CONFIRMED（在 Arch-A3 SparseCal 上继续面积削减：SharedPool-BG）
- **无 Phase 4**

## 相对 Trial 3 的变化

| 项 | Trial 3 | Trial 4 |
|---|---|---|
| 日历 | 稀疏 2×128×23 | 不变 |
| BG 缓冲 | 专用 5×20=100 | **共享池 40 + 预留 5×2=50** |
| 面积 vs IQ-XY | 1.000× | **0.822×** |
| 功耗 vs IQ-XY | 0.95× | **0.92×** |
| Tier A | 无 combine/DCA | 不变 |

## 关键证明点

1. **死锁自由：** XY-DOR 无环 + 每端口预留 2 + 日历零缓冲隔离（不参与池信用环）。
2. **BG 上界：** 硬 328；软（预留覆盖）~160；软+池争用 ~200。
3. **日历不受影响：** 零缓冲；永不占用共享池。
4. **Demote→XY 无损：** escape 进入池/预留。

## 关键工件

- `architecture.md` / `architecture-diagram.md`
- `ppa-analytic.md` / `utils/ppa_analytic_model.py`
- ADR-002 / ADR-004（ADR-003 Tier A 再确认）
- `iron-requirements.json`（REQ-A-001..006，trial=4）
