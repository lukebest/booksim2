# Protocol Assignments — Trial 5 Arch-A5

| Interface | Protocol | Width / rule | Rationale |
|---|---|---|---|
| calendar replay → CalFork/switch | compiled slot ownership | sparse 23-bit event; `flit[511:0]`; no payload queue | Static schedule; **CalFork** mask expand |
| BG/escape ingress → `vc_buffers` | ready/valid SharedPool admission | `flit[511:0]`; ready if reserve free or `shared_used < 28` | Lossless admission; calendar never enqueues |
| `vc_buffers` → RC → SA → ST | valid/ready request | flit + route metadata | One-cycle RC/SA/ST |
| router egress → downstream BG/escape | credit-based | `flit_valid`, `flit[511:0]`, `credit_valid` | H=7/V=9 RTT |
| CalFork egress | atomic availability / commit | 5-bit `out_port_mask` | All-or-none multicast; no stream_fork FSM |
| PE NI inject/eject | ready/valid | `flit[511:0]`, class/header | Tier-A PE handoff outside router |
| combine / DCA | **ABSENT** | — | Trial 5 Tier A |

Credit counters: H 0..16, V 0..20. Soft priority: calendar owns matching cycles;
BG uses non-matching cycles via SharedPool. Escape releases calendar ownership
before XY class request into pool/reserves.
