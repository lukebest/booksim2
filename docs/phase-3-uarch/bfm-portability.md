# BFM Portability — Trial 5 Arch-A5

Trial 5 continues the portable C cycle BFM (links RefC) as the SystemC substitute.
BFM Makefile compiles `../refc/*.c` with `-I../refc/include`, so it automatically
consumes:

- `BG_SHARED_POOL_SIZE=28`, `BG_PER_PORT_RESERVE=2`, `BG_TOTAL_FLITS=38`
- `cal_fork_expand()` / CalFork calendar path
- Tier A (no combine/DCA)

Evidence:
- `make -C refc test` — all PASS including `test_shared_pool pool=28`
- `make -C bfm test` — mesh_bfm_smoke PASS
- `python3 utils/ppa_analytic_model.py` — area 0.746× / power 0.90×

No SystemVerilog RTL in DSE. DPI bridge template retained for future Phase 4.
