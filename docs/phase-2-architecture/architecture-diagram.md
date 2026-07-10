# Arch-A3 Architecture Diagrams (DSE Trial 3)

**Architecture:** Arch-A3 SparseCal-Hybrid-ZB-NoCombine  
**Decision:** USER_CONFIRMED — sparse calendar + soft-prio + DCA Tier A (no in-router combine/DCA)

These diagrams are the dedicated Trial 3 architecture deliverable. The same Mermaid
sources are embedded in [`architecture.md`](architecture.md).

---

## 1. Mesh context (6×8, single 512-bit NoC)

```mermaid
flowchart TB
  subgraph mesh["6×8 mesh — 48 tiles, one physical 512b network @ 2 GHz"]
    direction LR
    R00["R(0,0)"] --- R10["R(1,0)"] --- R50["R(5,0)"]
    R00 --- R01["R(0,1)"]
    R01 --- R11["R(1,1)"]
    R11 --- R51["R(5,1)"]
    R07["R(0,7)"] --- R57["R(5,7)"]
  end
  note["Per-router ports: N/E/S/W/Local<br/>H_LINK=7, V_LINK=9, ramp_bw=1<br/>NO second network / NO CDC"]
  mesh --- note
```

> 中文：6×8 网格，单条 512b 物理 NoC，每 tile 一个路由器，五向端口。

---

## 2. Router block diagram — sparse calendar vs BG, no combine/DCA

```mermaid
flowchart LR
  subgraph ingress["Ingress"]
    mesh_in["Five 512-bit mesh ports"]
    pe_ni["pe_ni local inject/eject"]
  end

  slot_ctr["global slot counter<br/>wrap 1024"]
  calendar_store[("calendar_store<br/>2 × 128 × 23-bit<br/>sparse event list / CAM")]
  next_match["next_event_match<br/>entry.slot == counter?"]
  calendar_store --> next_match
  slot_ctr --> next_match

  mesh_in --> classify{"matching calendar<br/>event or BG/escape?"}
  pe_ni --> classify
  next_match --> classify

  classify -->|"matching event<br/>zero-buffer path"| multicast_fork["multicast_fork<br/>atomic 5-bit fork"]
  multicast_fork --> switch_alloc["switch_alloc"]

  classify -->|"BG or demoted<br/>credited XY VC"| vc_buffers["vc_buffers<br/>BG + escape FIFOs"]
  vc_buffers --> xy_route["xy_route X-then-Y"]
  xy_route --> switch_alloc

  classify -->|"mismatch / timeout"| watchdog_demote["watchdog_demote FSM"]
  watchdog_demote --> vc_buffers

  switch_alloc --> crossbar["5×5 × 512-bit crossbar"]
  crossbar --> mesh_out["Five 512-bit egress ports"]
  credit_fc["credit_fc<br/>per-egress BG/escape credits"] <--> vc_buffers
  credit_fc --> switch_alloc

  absent["ABSENT in Trial 3:<br/>combine_unit = none<br/>DCA datapath = none<br/>Reduce = gather → PE → (bcast)"]
  multicast_fork -.->|"no arithmetic path"| absent
  pe_ni -.->|"Tier-A PE compute only<br/>outside router datapath"| absent
```

> 中文：稀疏事件表（2×128×23b）替代稠密 2×1024×13b SRAM；`next_event_match` 在全局 slot 计数器匹配时触发日历路径。

---

## 3. Soft priority vs hard TDM (selected: soft-prio)

```mermaid
flowchart TB
  subgraph cycle["Each noc_clk cycle"]
    CTR["slot counter advances"]
    MATCH{"sparse head<br/>slot == counter?"}
    CAL["Calendar owns cycle<br/>zero-buffer fork"]
    BG["BG / escape eligible<br/>credited XY VC"]
  end
  CTR --> MATCH
  MATCH -->|yes + valid flit| CAL
  MATCH -->|no match| BG
  CAL --> XBAR["crossbar grant"]
  BG -->|"credit > 0"| XBAR
  note2["Soft-prio: BG uses idle/non-matching slots.<br/>Calendar never displaced by BG.<br/>Conservative hard 1-in-16 bound = 328 cy;<br/>occupancy-aware soft bound ≈ 160 cy."]
  BG --- note2
```

> 中文：**软优先级**已选定——有匹配稀疏事件时日历优先；无匹配时 BG 可用。硬 1-in-16 TDM 仅作保守上界参考。

---

## 4. Sparse event list structure

```mermaid
flowchart LR
  subgraph bank["One calendar bank (depth 128)"]
    E0["entry[0]: slot=17, in, mask, opcode"]
    E1["entry[1]: slot=42, ..."]
    E2["..."]
    E127["entry[≤127]: slot=951, ..."]
  end
  LOAD["offline loader<br/>sort by slot"] --> bank
  bank --> READ["registered read + compare"]
  READ --> FIRE["fire when slot == counter"]
```

> 中文：每路由器每 bank 最多 128 条有序事件；allreduce 实测单 router 最大 49 条，max_slot≈951。

---

## 5. Multicast fork + watchdog demote

```mermaid
sequenceDiagram
  participant NE as next_event_match
  participant MF as multicast_fork
  participant WD as watchdog_demote
  participant VC as vc_buffers (escape)
  participant XB as crossbar
  NE->>MF: legal mask + flit (slot match)
  alt all selected egress available
    MF->>XB: atomic multi-port grant
  else mismatch / timeout / blocked
    MF->>WD: preserve flit + remaining_leaf_mask
    WD->>VC: one XY escape per remaining leaf (no drop)
    VC->>XB: credited BG/escape grant
  end
```

---

## 6. Explicit non-goals (Trial 3)

| Block | Trial 2 Arch-A2 | Trial 3 Arch-A3 |
|---|---|---|
| Calendar store | Dense 2×1024×13 SRAM | **Sparse 2×128×23 event list** |
| Dispatch | Slot-indexed table read | **Next-event match on counter** |
| BG arbitration | Hard 1-in-16 primary | **Soft priority** (hard bound retained) |
| `combine_unit` | Absent | **Absent** |
| DCA interface | Absent | **Absent** |
| Shared BG buffer pool | — | **Out of scope** (Trial 3b future) |
