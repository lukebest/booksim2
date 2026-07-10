# Arch-A4 Microarchitecture Diagrams (DSE Trial 4)

**μArch:** Arch-A4 SparseCal-SharedPool-ZB-NoCombine  
**Tier:** DCA Tier A — **no combine_unit, no DCA datapath**  
**Buffers:** Shared pool 40 + per-port reserve 2

Companion to [`uarch.md`](uarch.md). Architecture-level diagrams:
[`../phase-2-architecture/architecture-diagram.md`](../phase-2-architecture/architecture-diagram.md).

---

## 1. Mesh / 5-port router context

```mermaid
flowchart TB
  subgraph tile["One mesh tile"]
    PE["PE / NI"]
    R["Router Arch-A4<br/>SparseCal + SharedPool"]
    PE <-->|"ramp=1, ramp_bw=1"| R
  end
  RN["Neighbor N<br/>V_LINK=9"] --- R
  RE["Neighbor E<br/>H_LINK=7"] --- R
  RS["Neighbor S"] --- R
  RW["Neighbor W"] --- R
```

---

## 2. Sparse calendar vs SharedPool BG pipelines

```mermaid
flowchart LR
  subgraph cal["Calendar path — zero-buffer"]
    CTR["slot counter"] --> S0["S0: event SRAM read"]
    S0 --> S1["S1: slot==counter"]
    S1 --> MF["multicast_fork"]
    MF --> ST1["ST / crossbar"]
  end
  subgraph bg["BG / escape — SharedPool-BG"]
    POOL["shared 40 + reserve 5×2"] --> RC["RC: xy_route"]
    RC --> SA["SA: switch_alloc"]
    SA --> ST2["ST / crossbar"]
  end
  ST1 --> OUT["egress + H/V pipe"]
  ST2 --> OUT
  WD["watchdog_demote"] -->|"escape"| POOL
  S1 -.->|"violation"| WD
  CALX["Calendar NEVER uses pool"] -.-> POOL
```

> 中文：日历零缓冲；BG/demote 走共享池+预留。

---

## 3. Shared pool allocator

```mermaid
stateDiagram-v2
  [*] --> Idle
  Idle --> UseReserve: port_count < 2
  Idle --> UseShared: port_count ≥ 2 and shared_used < 40
  Idle --> Backpressure: else
  UseReserve --> Idle: enqueued
  UseShared --> Idle: enqueued
  Backpressure --> Idle: downstream drain frees slot
```

---

## 4. Soft priority + bounds

```mermaid
flowchart TB
  MATCH["slot == sparse entry?"]
  MATCH -->|yes| CAL["calendar owns (ZB)"]
  MATCH -->|no| BG["BG eligible via SharedPool"]
  note["Hard 328 · Soft ~160 · Soft+pool ~200"]
```

---

## 5. Multicast fork + watchdog demote

```mermaid
stateDiagram-v2
  [*] --> Idle
  Idle --> ForkHold: slot match + legal mask
  ForkHold --> Commit: all selected egress ready
  Commit --> [*]
  ForkHold --> Demote: timeout / mismatch / blocked
  Idle --> Demote: early or wrong-port
  Demote --> EmitEscape: release once + preserve leaf mask
  EmitEscape --> Idle: remaining leaves enqueued to SharedPool
```

---

## 6. Trial 3 → Trial 4 μArch deltas

| Item | Trial 3 | Trial 4 |
|---|---|---|
| Calendar | Sparse 2×128×23 | **Same** |
| `vc_buffers` | 5×20 dedicated | **Shared 40 + reserve 2** |
| BG bounds | 328 / ~160 | **328 / ~160 / ~200** |
| combine/DCA | Absent | **Absent** |
| Area | 1.000× | **0.822×** |
