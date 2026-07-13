# Phase 3 μArch Review — Trial 5

**Verdict: PASS**

- Arch-A5 SparseCal-SharedPool-CalFork-ZB-NoCombine specified
- SharedPool-BG μArch specified (28 shared + five per-port reserves of 2 = 38)
- CalFork is calendar-native (`cal_fork_expand()`), not a FlooNoC stream-fork
- Calendar zero-buffer isolation documented
- BFM Makefile links RefC sources, so it consumes `BG_SHARED_POOL_SIZE=28`,
  `BG_PER_PORT_RESERVE=2`, and the CalFork alias automatically; smoke PASS
- RefC tests: demote, bg_window, blocked_fork, bg_bound, **shared_pool** PASS
- REQ-U iron + 100% traceability
- Analytic PPA matches XB 0.380 + Buf 0.139 + Cal 0.009 + MC 0.025 + Ctrl
  0.193 = **0.746×**; power **0.90×** (better than A4 0.822× / 0.92×)
- No SystemVerilog / no Phase 4
