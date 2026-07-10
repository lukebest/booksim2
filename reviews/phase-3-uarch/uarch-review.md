# Phase 3 μArch Review — DSE Trial 1

## Round 1

| Area | Finding | Resolution |
|---|---|---|
| Calendar timing | SRAM read and qualification must not be collapsed into one cycle. | Accepted: documented S0/S1 two-stage path. |
| Mixed traffic | Calendar must not queue behind BG. | Accepted: calendar remains zero-buffer and BG owns protected windows only. |
| Demotion | Multicast recovery must distinguish accepted leaves. | Accepted: remaining-leaf mask and release-once latch specified. |
| BFM portability | SystemC may be unavailable. | Accepted: portable C BFM directly links the RefC model and reserves DPI ABI. |

## Round 2

The C BFM built with `-Wall -Wextra -Werror`, ran the shared RefC smoke
scenario, and produced 11 timestamped per-module logs. Calendar forward/fork,
BG XY, watchdog demotion, and Tier-B ADD matched RefC's deterministic result:
`PASS cycles=45 calendar=1 bg=2 demotions=1`. No new critical or high finding
was introduced; Round-2 finding delta is zero.

## Cross-module audit

- One `noc_clk` domain: no CDC.
- Calendar uses S0/S1; BG uses RC→SA→ST; the combine contract reserves three
  calendar cycles.
- Credit counters gate BG/escape transfer, and demotion releases calendar state
  before requesting XY storage.
- DCA is explicitly inactive.

## Verdict

**PASS.** The μArch preserves every Arch-A block boundary, maps all upstream
requirements, and the portable BFM passes against the RefC smoke scenario.
