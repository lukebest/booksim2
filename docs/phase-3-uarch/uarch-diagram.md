# Arch-A3 Microarchitecture Diagrams (DSE Trial 3)

**μArch:** Arch-A3 SparseCal-Hybrid-ZB-NoCombine  
**Tier:** DCA Tier A — **no combine_unit, no DCA datapath**

Companion to [`uarch.md`](uarch.md). Architecture-level diagrams:
[`../phase-2-architecture/architecture-diagram.md`](../phase-2-architecture/architecture-diagram.md).

---

## 1. Mesh / 5-port router context

```mermaid
flowchart TB
  subgraph tile["One mesh tile"]
    PE["PE / NI"]
    R["Router Arch-A3<br/>ports: N E S W L"]
    PE <-->|"ramp=1, ramp_bw=1"| R
  end
  RN["Neighbor N<br/>V_LINK=9"] --- R
  RE["Neighbor E<br/>H_LINK=7"] --- R
  RS["Neighbor S"] --- R
  RW["Neighbor W"] --- R
  note["6×8 = 48 tiles · single 512b NoC @ 2 GHz · no CDC"]
  tile --- note
```

> 中文：单 tile 五向路由器，PE 经 1 周期 ramp 接入。

---

## 2. Sparse calendar vs BG pipelines

```mermaid
flowchart LR
  subgraph cal["Calendar path — sparse next-event match"]
    CTR["slot counter"] --> S0["S0: event SRAM read"]
    S0 --> S1["S1: slot==counter qualify"]
    S1 --> MF["multicast_fork"]
    MF --> ST1["ST / crossbar"]
  end
  subgraph bg["BG / escape path — soft-prio on non-match cycles"]
    FIFO["vc_buffers FIFO"] --> RC["RC: xy_route"]
    RC --> SA["SA: switch_alloc"]
    SA --> ST2["ST / crossbar"]
  end
  ST1 --> OUT["egress + H/V pipe"]
  ST2 --> OUT
  WD["watchdog_demote"] -.->|"escape enqueue"| FIFO
  S1 -.->|"violation"| WD
  NO["ABSENT: combine_unit 3-cycle pipe<br/>ABSENT: DCA req/rsp"]
  MF -.-> NO
```

> 中文：S0/S1 从稀疏表读取并匹配 slot；无匹配时 BG 路径可用（软优先级）。

---

## 3. Soft priority isolation

```mermaid
flowchart TB
  subgraph tdm["Per-cycle arbitration (soft-prio)"]
    MATCH["slot == sparse entry?"]
    CALOWN["yes → calendar owns (ZB)"]
    BGELIG["no → BG / escape eligible"]
  end
  MATCH --> CALOWN
  MATCH --> BGELIG
  CALOWN --> GRANT["crossbar grant"]
  BGELIG --> GRANT
  note["Calendar never waits on BG FIFOs.<br/>BG never displaces firing calendar.<br/>Hard bound 328 cy; soft bound ~160 cy."]
```

> 中文：软优先级——匹配事件优先日历；否则 BG 可用。硬 1-in-16 仅作保守上界。

---

## 4. Sparse event list μArch

```mermaid
flowchart LR
  subgraph store["calendar_store per router"]
    BANK0["bank 0: 128 × 23b sorted events"]
    BANK1["bank 1: 128 × 23b (inactive load)"]
  end
  CTR["slot counter wrap 1024"] --> CMP["compare head entry.slot"]
  BANK0 --> CMP
  CMP -->|match| QUAL["S1 qualify in/mask/opcode"]
  CMP -->|no match| BG["BG path eligible"]
  HOTSWAP["epoch handoff @ slot 0"] --> BANK0
  HOTSWAP --> BANK1
```

> 中文：双 bank 热切换保留；每 bank 深度 128，23 bit/条，按 slot 排序。

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
  EmitEscape --> Idle: all remaining leaves enqueued to BG VC
```

---

## 6. Explicit absence of combine / DCA

```mermaid
flowchart LR
  GATHER["Calendar gather tree"] --> PE["PE-local compute<br/>Tier A"]
  PE --> BCAST["Calendar broadcast<br/>(allreduce only)"]
  ROUTER["Router datapath"] -.->|"forwards only"| GATHER
  ROUTER -.->|"forwards only"| BCAST
  X1["combine_unit"] -.->|"NOT PRESENT"| ROUTER
  X2["DCA interface"] -.->|"NOT PRESENT"| ROUTER
```

| Trial 2 μArch | Trial 3 μArch |
|---|---|
| Dense 2×1024×13 SRAM | **Sparse 2×128×23 event list** |
| Slot-indexed read | **next-event match** |
| Hard 1-in-16 BG | **Soft priority** |
| `combine_unit` removed | **Still absent** |
| DCA removed | **Still absent** |
