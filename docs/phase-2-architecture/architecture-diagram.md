# Arch-A5 Architecture Diagrams (DSE Trial 5)

**Architecture:** Arch-A5 SparseCal-SharedPool-CalFork-ZB-NoCombine  
**Decision:** USER_CONFIRMED — CalFork + aggressive SharedPool on Arch-A4 base (Tier A)

Companion: [`architecture.md`](architecture.md).

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

> 中文：6×8 网格，单条 512b 物理 NoC（与 Trial 4 相同）。

---

## 2. Router block diagram — SparseCal + CalFork + SharedPool

```mermaid
flowchart LR
  subgraph ingress["Ingress"]
    mesh_in["Five 512-bit mesh ports"]
    pe_ni["pe_ni local inject/eject"]
  end

  slot_ctr["global slot counter<br/>wrap 1024"]
  calendar_store[("calendar_store<br/>2 × 128 × 23-bit<br/>sparse event list")]
  next_match["next_event_match<br/>entry.slot == counter?"]
  calendar_store --> next_match
  slot_ctr --> next_match

  mesh_in --> classify{"matching calendar<br/>event or BG/escape?"}
  pe_ni --> classify
  next_match --> classify

  classify -->|"matching event<br/>zero-buffer path"| cal_fork["CalFork<br/>lean out_port_mask fork"]
  cal_fork --> switch_alloc["switch_alloc"]

  classify -->|"BG or demoted"| vc_buffers["vc_buffers SharedPool<br/>pool 28 + reserve 5×2 = 38"]
  vc_buffers --> xy_route["xy_route X-then-Y"]
  xy_route --> switch_alloc

  classify -->|"mismatch / timeout"| watchdog_demote["watchdog_demote FSM"]
  watchdog_demote -->|"escape via pool/reserves"| vc_buffers

  switch_alloc --> crossbar["5×5 × 512-bit crossbar"]
  crossbar --> mesh_out["Five 512-bit egress ports"]
  credit_fc["credit_fc<br/>per-egress BG/escape credits"] <--> vc_buffers
  credit_fc --> switch_alloc

  absent["ABSENT: combine_unit / DCA / FlooNoC stream_fork<br/>Calendar NEVER uses shared pool"]
  cal_fork -.-> absent
```

> 中文：日历经 CalFork 零缓冲分叉；BG/escape 走共享池 28 + 预留 2。

---

## 3. Shared pool + per-port reserve

```mermaid
flowchart TB
  subgraph pool["vc_buffers — SharedPool-BG"]
    RES["Per-port reserve<br/>5 × 2 = 10 flits"]
    SHR["Shared free pool<br/>28 flits"]
    ACC{"enqueue(port)"}
  end
  ACC -->|"count < 2"| RES
  ACC -->|"count ≥ 2 and shared_used < 28"| SHR
  ACC -->|"else"| BP["backpressure"]
  CAL["Calendar path"] -.->|"never"| pool
  DEM["watchdog demote escape"] --> ACC
```

> 中文：预留保证各端口在共享池耗尽时仍可前进；日历路径不占用池。

---

## 4. Soft priority + BG bounds

```mermaid
flowchart TB
  CTR["slot counter"] --> MATCH{"sparse head<br/>slot == counter?"}
  MATCH -->|yes| CAL["Calendar owns cycle (ZB + CalFork)"]
  MATCH -->|no| BG["BG / escape eligible"]
  CAL --> XBAR["crossbar"]
  BG --> XBAR
  note["Hard TDM bound 328 cy<br/>Soft reserve-covered ~160 cy<br/>Soft+pool contention ~188 cy"]
  BG --- note
```

---

## 5. Deadlock freedom sketch

```mermaid
flowchart LR
  DOR["XY-DOR acyclic"] --> OK["no routing cycle"]
  RES2["per-port reserve ≥ 1"] --> PROG["no permanent pool starve"]
  ZB["calendar zero-buffer"] --> ISO["no cal↔pool credit cycle"]
  OK --> DF["deadlock-free"]
  PROG --> DF
  ISO --> DF
```

---

## 6. Trial 4 → Trial 5 deltas

| Block | Trial 4 Arch-A4 | Trial 5 Arch-A5 |
|---|---|---|
| Calendar | Sparse 2×128×23 | **Same** |
| Multicast | FlooNoC-class MC 0.058 | **CalFork MC 0.025** |
| BG buffers | Shared 40 + reserve 5×2=50 | **Shared 28 + reserve 5×2=38** |
| Area | 0.822× | **0.746×** |
| Power | 0.92× | **0.90×** |
| combine/DCA | Absent | **Absent** |
