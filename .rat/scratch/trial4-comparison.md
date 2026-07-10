# Trial 3 vs Trial 4

| | Trial 3 Arch-A3 | Trial 4 Arch-A4 |
|---|---|---|
| BG buffers | 100 flits (5×20) | **50 flits (pool 40 + 5×2)** |
| Area | 1.000× | **0.822×** |
| Power | 0.95× | **0.92×** |
| SparseCal | 2×128×23 | same |
| Tier | A | A |
| BG bound | hard 328 / soft ~160 | hard 328 / soft ~160 / soft+pool ~200 |
| Promote | — | **YES (user-requested SharedPool)** |
