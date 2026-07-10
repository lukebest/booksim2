# Arch-A2 Microarchitecture Diagrams (DSE Trial 2)

**μArch:** Arch-A2 CalSlot-Hybrid-ZB-NoCombine  
**Tier:** DCA Tier A — **no combine_unit, no DCA datapath**

Companion to [`uarch.md`](uarch.md). Architecture-level diagrams:
[`../phase-2-architecture/architecture-diagram.md`](../phase-2-architecture/architecture-diagram.md).

---

## 1. Mesh / 5-port router context

```mermaid
flowchart TB
  subgraph tile["One mesh tile"]
    PE["PE / NI"]
    R["Router Arch-A2<br/>ports: N E S W L"]
    PE <-->|"ramp=1, ramp_bw=1"| R
  end
  RN["Neighbor N<br/>V_LINK=9"] --- R
  RE["Neighbor E<br/>H_LINK=7"] --- R
  RS["Neighbor S"] --- R
  RW["Neighbor W"] --- R
  note["6×8 = 48 tiles · single 512b NoC @ 2 GHz · no CDC"]
  tile --- note
```

---

## 2. Calendar vs BG pipelines

```mermaid
flowchart LR
  subgraph cal["Calendar path — zero payload buffer"]
    S0["S0: SRAM read"] --> S1["S1: qualify"]
    S1 --> MF["multicast_fork"]
    MF --> ST1["ST / crossbar"]
  end
  subgraph bg["BG / escape path — credited XY VC"]
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

---

## 3. VC / TDM isolation (1-in-16)

```mermaid
flowchart TB
  subgraph tdm["Per-egress slot period = 16"]
    CALOWN["slots with valid calendar entry → calendar owns"]
    BGWIN["every 16th slot → non-borrowable BG window"]
    IDLE["calendar-idle slots → BG may borrow"]
  end
  CALOWN --> GRANT["crossbar grant"]
  BGWIN --> GRANT
  IDLE --> GRANT
  note["Calendar never waits on BG FIFOs.<br/>BG never displaces valid calendar.<br/>12-hop bound = 328 cycles."]
```

---

## 4. Multicast fork + watchdog demote

```mermaid
stateDiagram-v2
  [*] --> Idle
  Idle --> ForkHold: legal mask, wait availability
  ForkHold --> Commit: all selected egress ready
  Commit --> [*]
  ForkHold --> Demote: timeout / mismatch / blocked
  Idle --> Demote: early or wrong-port
  Demote --> EmitEscape: release once + preserve leaf mask
  EmitEscape --> Idle: all remaining leaves enqueued to BG VC
```

---

## 5. Explicit absence of combine / DCA

```mermaid
flowchart LR
  GATHER["Calendar gather tree"] --> PE["PE-local compute<br/>Tier A"]
  PE --> BCAST["Calendar broadcast<br/>(allreduce only)"]
  ROUTER["Router datapath"] -.->|"forwards only"| GATHER
  ROUTER -.->|"forwards only"| BCAST
  X1["combine_unit"] -.->|"NOT PRESENT"| ROUTER
  X2["DCA interface"] -.->|"NOT PRESENT"| ROUTER
```

| Trial 1 μArch | Trial 2 μArch |
|---|---|
| `combine_unit` 3-cycle lane ALU | **Removed** |
| DCA stub tied inactive | **Removed** (no stub) |
| Reduce via in-router merge | Gather → PE |
| Allreduce via merge + bcast | Gather → PE → bcast |
