# Wonder Log

| Round | Assumption | Domain | Risk(H/M/L) | Resolution |
|---|---|---|---|---|
| 1 | A portable C BFM is acceptable when SystemC is unavailable. | verification | M | Resolved: BFM links RefC types/logic and documents future DPI bridge. |
| 1 | A two-stage calendar path fits the compiled timing model. | timing | M | Resolved: S0 SRAM read and S1 qualification are explicit. |
| 2 | RefC smoke is a shared behavioral vector for the BFM. | model consistency | L | Resolved: both binaries produce the same PASS result and cycle count. |
