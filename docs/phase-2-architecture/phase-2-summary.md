# Phase 2 摘要 — DSE Trial 3

**选定架构：** Arch-A3 SparseCal-Hybrid-ZB-NoCombine  
**决策记录：** ADR-002（Arch-A3）、ADR-003（Tier A）、ADR-004（PPA 1.000×）  
**决策来源：** USER_CONFIRMED

## 候选对比

| 候选 | 面积 | 决策 |
|---|---:|---|
| **Arch-A3 SparseCal** | **1.000×** | **选定** |
| Arch-A2（Trial 2 稠密日历） | 1.028× | 被取代 |
| Arch-A（Trial 1 含 combine） | 1.065× | 被取代 |
| Arch-B SrcRoute | ~1.008× | 拒绝（破坏 P0 确定性重放） |
| Arch-C HardTDM-DCA | ~1.237× | 拒绝 |

## 相对 Trial 2 的关键变更

1. **稀疏有序事件表** 替代每路由器 `2×1024×13` 稠密 SRAM：`2×128×23` bit，按 slot 排序存储 `(slot, in_port, out_port_mask, opcode)`。
2. **next-event 匹配**：全局 slot 计数器（回绕 1024）与稀疏表头比较，匹配时触发零缓冲日历路径。
3. **软优先级 BG**：有匹配稀疏事件时日历优先；无匹配时 BG 可用。硬 1-in-16 TDM 放宽为保守上界（328 周期）；软优先级占用感知上界约 **160 周期**。
4. **Tier A 不变**：无 `combine_unit`、无 DCA；reduce = gather + PE；allreduce = gather → PE → bcast。
5. **面积/功耗**：相对 IQ-XY 基线 **1.000× / 0.95×**；相对 Trial 2 **−0.028 面积 / −0.01 功耗**。

## 稀疏度证据（`results/calendars/*_m1.json`）

| 集合通信 | 总条目 | 均值/路由器 | 最大/路由器 | max_slot |
|---|---:|---:|---:|---:|
| broadcast | 48 | 1 | 1 | 99 |
| allgather | 192 | 4 | 4 | 699 |
| gather/reduce | 336 | 7 | 48 | 851 |
| allreduce | 384 | 8 | **49** | **951** |

相对稠密 `48×1024` 密度 ≪ 1%。深度 128 覆盖实测最大 49 条并留有余量。

## 关键交付物

- `architecture.md` + `architecture-diagram.md`（Trial 3 专用 Mermaid 图）
- `architecture-candidates.md`、`ppa-analytic.md`、`ppa-workbook.md`
- `iron-requirements.json`（REQ-A-001..006，trial:3）
- RefC 无 combine（`refc/`）

## 门禁

架构评审 PASS；功能覆盖 100%；OPEN-1-001 以 SparseCal 闭合。无 Phase 4。
