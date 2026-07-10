# Arch-A2 Architecture Diagrams (DSE Trial 2)

**Architecture:** Arch-A2 CalSlot-Hybrid-ZB-NoCombine  
**Decision:** USER_CONFIRMED — area-first + DCA Tier A (no in-router combine/DCA)

These diagrams are the dedicated Trial 2 architecture deliverable. The same Mermaid
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

---

## 2. Router block diagram — calendar vs BG, no combine/DCA

```mermaid
flowchart LR
  subgraph ingress["Ingress"]
    mesh_in["Five 512-bit mesh ports"]
    pe_ni["pe_ni local inject/eject"]
  end

  calendar_store[("calendar_store<br/>2 × 1024 × 13-bit SRAM")]
  calendar_replay["calendar_replay<br/>slot → port/mask/opcode"]
  calendar_store --> calendar_replay

  mesh_in --> classify{"calendar slot<br/>or BG/escape?"}
  pe_ni --> classify
  calendar_replay --> classify

  classify -->|"legal calendar<br/>zero-buffer path"| multicast_fork["multicast_fork<br/>atomic 5-bit fork"]
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

  absent["ABSENT in Trial 2:<br/>combine_unit = none<br/>DCA datapath = none<br/>Reduce = gather → PE → (bcast)"]
  multicast_fork -.->|"no arithmetic path"| absent
  pe_ni -.->|"Tier-A PE compute only<br/>outside router datapath"| absent
```

---

## 3. VC / TDM isolation

```mermaid
flowchart TB
  subgraph slots["Hybrid TDM on each egress (period = 16)"]
    direction LR
    S0["slots 0..14<br/>calendar-owned if valid"]
    S15["slot 15<br/>non-borrowable BG window"]
  end
  subgraph classes["Traffic classes"]
    CAL["Calendar class<br/>zero payload VC / slot-owned"]
    BG["BG + escape class<br/>one credited XY-DOR VC"]
  end
  S0 --> CAL
  S15 --> BG
  CAL -->|"never waits on BG FIFOs"| XBAR["crossbar grant"]
  BG -->|"credit > 0 and eligible"| XBAR
  note2["BG may also use calendar-idle slots;<br/>never displaces a valid calendar transfer.<br/>12-hop bound = 328 cycles"]
  BG --- note2
```

---

## 4. Multicast fork + watchdog demote

```mermaid
sequenceDiagram
  participant CR as calendar_replay
  participant MF as multicast_fork
  participant WD as watchdog_demote
  participant VC as vc_buffers (escape)
  participant XB as crossbar
  CR->>MF: legal mask + flit
  alt all selected egress available
    MF->>XB: atomic multi-port grant
  else mismatch / timeout / blocked
    MF->>WD: preserve flit + remaining_leaf_mask
    WD->>VC: one XY escape per remaining leaf (no drop)
    VC->>XB: credited BG/escape grant
  end
```

---

## 5. Explicit non-goals (Trial 2)

| Block | Trial 1 | Trial 2 Arch-A2 |
|---|---|---|
| `combine_unit` | Tier-B 2-input lane combine | **Absent** |
| DCA interface | Disabled stub | **Absent** (no stub datapath) |
| Reduce semantic | In-router merge | Gather → PE-local compute |
| Allreduce semantic | Reduce + bcast (in-net) | Gather → PE → scheduled broadcast |
