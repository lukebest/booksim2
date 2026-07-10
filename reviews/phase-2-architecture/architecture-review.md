# Architecture Self-Review — Arch-A CalSlot-Hybrid-ZB

**Verdict: PASS**

This verdict covers architecture completeness and internal consistency.  It does not
replace Phase-3 BFM evidence, physical timing closure, or synthesis PPA.

## Review checklist

| Review area | Evidence | Result |
|---|---|---|
| System constraints | One 512-bit NoC, 6×8 mesh, 2 GHz, H=7/V=9 and ramp=1 are stated in the architecture. | Pass |
| Block boundaries | All requested blocks have named responsibilities and refC-facing ownership boundaries. | Pass |
| Calendar storage | Two banks × 1,024 × 13 bits, inactive-bank write ownership, epoch handoff, and 3.25 KiB estimate are specified. | Pass |
| Calendar replay | Legal replay has an explicit two-stage registered path and no payload queue. | Pass |
| BG safety/progress | Dedicated credited XY class, 1/16 protected window, 16-slot per-hop grant, and 212-cycle eligible longest-route bound are stated. | Pass |
| Deadlock freedom | Calendar holds only reserved slot resources; BG/escape has X-before-Y ordering; demotion releases before requesting escape. | Pass |
| Multicast/no loss | Atomic fork and remaining-leaf accounting prevent partial replay, overwrite, or dropped leaves. | Pass |
| Reduction | Tier-B opcode, lanes, arithmetic semantics, 3-cycle merge, Tier-A fallback, and disabled Tier-C are concrete. | Pass |
| Demotion | Immediate mismatch detection, 32-cycle timeout, one release/action rule, and queue-capacity behavior are defined. | Pass |
| Traceability | Feature coverage maps every REQ-F/P/A requirement, including all five OPEN-1 resolutions. | Pass |
| PPA model | Arch-A area/power are labeled analytic and compared with B/C and baseline. | Pass |

## Findings and resolutions

| ID | Finding | Resolution |
|---|---|---|
| SR-001 | Calendar header/epoch widths were previously unspecified. | Fixed to calendar ID[1:0], epoch bit, 4-bit opcode, and a 16-bit logical flit control header in REQ-A-001 and `architecture.md`. |
| SR-002 | BG progress had no end-to-end numeric bound. | Fixed to one protected opportunity per 16 slots and 212 cycles for a 12-hop eligible one-flit request. |
| SR-003 | Violation recovery lacked timeout and multicast accounting. | Fixed to a 32-cycle timeout, immediate mismatch detection, and one escape packet per remaining leaf. |
| SR-004 | Tier-B numerical semantics were incomplete. | Fixed to eight 64-bit lanes and explicit integer/bitwise operations; FP remains Tier-A. |

## Residual verification obligations

The Phase-3 BFM must demonstrate the stated credit, progress, replay, combine, and
demotion acceptance criteria using post-window calendars.  These are verification
tasks, not unresolved architecture decisions.
