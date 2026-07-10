# Phase 3 摘要 — DSE Trial 4（Arch-A4 μArch + BFM）

- **μArch：** Arch-A4 SparseCal-SharedPool-ZB-NoCombine
- **BFM：** C 模型（链接 RefC）；**无 SystemVerilog / 无 Phase 4**
- **缓冲：** `BG_SHARED_POOL=40`，`BG_PER_PORT_RESERVE=2`，合计 50 flit

## μArch 要点

- 稀疏日历路径：S0/S1 next-event match，零缓冲
- SharedPool-BG：`shared_used = Σ max(0, count−2)`；预留保证前进
- 软优先级仲裁；watchdog demote→池/预留
- Tier A：无 combine_unit / 无 DCA

## 测试状态

| 测试 | 结果 |
|---|---|
| mesh_router_smoke / mesh_bfm_smoke | PASS |
| test_demote_noloss | PASS |
| test_bg_window | PASS |
| test_blocked_fork | PASS |
| test_bg_bound (≤328) | PASS |
| test_shared_pool（新增） | PASS |

## 关键工件

- `uarch.md` / `uarch-diagram.md`
- `iron-requirements.json`（REQ-U-001..006）
- `req-uarch-traceability.md`
- RefC：`router.c` / `noc_types.h` SharedPool 实现
