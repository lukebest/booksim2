# Phase 3 摘要 — DSE Trial 5（Arch-A5 μArch + BFM）

- **μArch：** Arch-A5 SparseCal-SharedPool-CalFork-ZB-NoCombine
- **BFM：** C 模型（链接 RefC）；**无 SystemVerilog / 无 Phase 4**
- **缓冲：** `BG_SHARED_POOL_SIZE=28`，`BG_PER_PORT_RESERVE=2`，五端口合计 38 flit

## μArch 要点

- 稀疏日历路径：S0/S1 next-event match，零缓冲
- CalFork：由日历 `out_port_mask[4:0]` 驱动的原子分叉；RefC 路由器使用
  `cal_fork_expand()`，不是通用 FlooNoC `stream_fork`
- SharedPool-BG：`shared_used = Σ max(0, count−2)`；28 个共享槽加每端口 2 个
  预留槽，日历永不进入该池
- 软优先级仲裁；watchdog demote→池/预留
- Tier A：无 combine_unit / 无 DCA

## PPA 结果

| 项目 | 相对面积 |
|---|---:|
| Crossbar / Buffer / SparseCal | 0.380 / 0.139 / 0.009 |
| CalFork MC / Control | 0.025 / 0.193 |
| **总面积 / 功耗** | **0.746× / 0.90×** |

相较 Arch-A4 的 0.822× / 0.92×，Trial 5 通过更小的共享池和 CalFork 降低面积与功耗；
Tier A、SparseCal、软优先级及日历零缓冲隔离保持不变。

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
- RefC：`router.c` 通过 `cal_fork_expand()` 使用 CalFork；`noc_types.h` 定义
  SharedPool 28+2=38 宏。BFM Makefile 直接链接这些 RefC 源文件。
