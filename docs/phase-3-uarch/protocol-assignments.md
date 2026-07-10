# Protocol Assignments — Trial 2 Arch-A2

| Interface | Protocol | Width / rule | Rationale |
|---|---|---|---|
| calendar replay → fork/switch | compiled slot ownership | `entry[12:0]`, `flit[511:0]`; no payload queue | Static schedule; **no combine stage** |
| BG/escape ingress → `vc_buffers` | ready/valid admission | `flit[511:0]`; ready when bank has space | Lossless admission |
| `vc_buffers` → RC → SA → ST | valid/ready request | flit + route metadata | One-cycle RC/SA/ST |
| router egress → downstream BG/escape | credit-based | `flit_valid`, `flit[511:0]`, `credit_valid` | H=7/V=9 RTT |
| calendar fork egress | atomic availability / commit | 5-bit mask | All-or-none multicast |
| PE NI inject/eject | ready/valid | `flit[511:0]`, class/header | Tier-A PE handoff outside router |
| combine / DCA | **ABSENT** | — | Trial 2 Tier A |

Credit counters: H 0..16, V 0..20. BG non-borrowable one-in-16; calendar owns other
compiled slots. Escape releases calendar ownership before XY class request.
