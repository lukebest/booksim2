# Wonder Log — Trial 2

| ID | Wonder | Resolution |
|---|---|---|
| W1 | Can calendar depth drop to 512 for area? | No — max_slot 951 in m=1 exports |
| W2 | Should Arch-B win on area (~1.008)? | No — breaks deterministic ZB replay |
| W3 | Is PE handoff latency modeled in BFM? | Tagged (`pe_handoffs`); PE compute outside router is stub/zero-cost in replay |
| W4 | Retain DCA stub? | No — Trial 2 removes stub datapath entirely |
