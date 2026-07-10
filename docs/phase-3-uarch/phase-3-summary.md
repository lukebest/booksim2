# Phase 3 摘要 — DSE Trial 3

**架构：** Arch-A3 SparseCal-Hybrid-ZB-NoCombine  
**层级：** DCA Tier A（无路由器内 combine/DCA）  
**PPA：** 面积 **1.000×**、功耗 **0.95×**（相对 IQ-XY）；相对 Trial 2 **−0.028 面积 / −0.01 功耗**

## 交付物

| 工件 | 路径 |
|---|---|
| μArch 规格 | `docs/phase-3-uarch/uarch.md` |
| μArch 图 | `docs/phase-3-uarch/uarch-diagram.md` |
| Iron（REQ-U-*） | `docs/phase-3-uarch/iron-requirements.json`（6 条） |
| 可追溯性 | `docs/phase-3-uarch/req-uarch-traceability.md`（100%） |
| 日历导出模式 | `docs/phase-3-uarch/calendar-export-schema.md`（深度 128 对齐） |
| BFM | `bfm/` — 编译通过；匹配 RefC；日历 PASS |
| RefC | `refc/` — 无 `combine_unit` |

## μArch 要点

- **稀疏日历** S0/S1 next-event 匹配路径（2×128×23b）；替代稠密 slot 表
- **软优先级** BG：无匹配周期可用；保守硬上界 328 周期，软上界约 160 周期
- BG RC→SA→ST 信用 XY 路径；watchdog demote → escape VC
- 原子多播 fork
- **combine_unit / DCA 明确缺席**；PE 本地 Tier-A 计算

## 稀疏度验证

`results/calendars/allreduce_m1.json`：单路由器最大 **49** 条，max_slot **951**；
硬件深度 **128**/bank，余量 >2×。

## 门禁

- uarch-review：PASS
- BFM ↔ RefC：PASS
- 合规 P1+P2→P3：见 `.rat/state/`
- Phase 4 RTL：**未启动**（DSE 止步）
