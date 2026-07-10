# Phase 2 Feature Coverage — Arch-A

Coverage is structural: every Phase-1 functional/performance requirement and every
Phase-2 architecture requirement maps to an architectural block and a measurable
acceptance target.  `Covered` means architected, not that Phase-3 BFM evidence
already exists.

| Requirement | Architecture block(s) | Coverage evidence | Status |
|---|---|---|---|
| REQ-F-001 | `calendar_store`, `calendar_replay`, `pe_ni` | Double-bank 1,024×13-bit replay and epoch handoff | Covered |
| REQ-F-002 | `calendar_replay`, `multicast_fork`, `switch_alloc` | Calendar mask drives atomic scheduled broadcast/allgather fork | Covered |
| REQ-F-003 | `calendar_replay`, `combine_unit`, `pe_ni` | Tree convergence; Tier B or Tier-A PE fallback | Covered |
| REQ-F-004 | `combine_unit`, `pe_ni`, `dca_stub` | Tier-B selected; Tier-A fallback; Tier-C compared/deferred | Covered |
| REQ-F-005 | `xy_route`, `vc_buffers`, `switch_alloc`, `credit_fc` | XY-DOR and 16-slot protected service bound | Covered |
| REQ-F-006 | `multicast_fork`, `watchdog_demote`, `credit_fc` | Five-bit atomic fork and remaining-leaf recovery mask | Covered |
| REQ-F-007 | `calendar_replay`, `xy_route`, `vc_buffers`, `watchdog_demote` | Slot ownership plus acyclic X→Y escape dependency | Covered |
| REQ-F-008 | `vc_buffers`, `credit_fc`, `multicast_fork` | RTT-sized credits; no overwrite; atomic availability check | Covered |
| REQ-F-009 | `watchdog_demote`, `xy_route`, `vc_buffers` | Immediate mismatch detection, 32-cycle watchdog, lossless escape | Covered |
| REQ-F-010 | `combine_unit`, `pe_ni`, `dca_stub` | Tier A/B/C comparison in candidate and PPA reports | Covered |
| REQ-F-011 | `pe_ni`, `dca_stub` | Disabled optional DCA boundary retains base-router operation | Covered |
| REQ-F-012 | `calendar_store`, `calendar_replay`, `pe_ni` | Phase-3 BFM replay boundary and baseline reporting contract | Covered |
| REQ-P-001 | `switch_alloc`, `crossbar`, `vc_buffers`, `credit_fc` | 512-bit 1-flit/cycle granted-path contract | Covered |
| REQ-P-002 | all blocks | Single 2 GHz domain; H=7/V=9/ramp=1 analytic constraints | Covered |
| REQ-P-003 | `calendar_store`, `calendar_replay`, `switch_alloc` | Window-aware recompilation and baseline-overhead reporting | Covered |
| REQ-P-004 | `calendar_store`, `vc_buffers`, `crossbar`, `combine_unit` | Componentized analytic PPA assumptions and calibration classes | Covered |
| REQ-P-005 | `dca_stub`, `pe_ni` | Tier-C router/tile comparison retained while DCA is disabled | Covered |
| REQ-A-001 | `calendar_store`, `calendar_replay` | Slot format, bank ownership, and epoch acceptance criteria | Covered |
| REQ-A-002 | `xy_route`, `switch_alloc`, `vc_buffers`, `credit_fc` | 1/16 BG window and 212-cycle eligible longest-route bound | Covered |
| REQ-A-003 | `vc_buffers`, `credit_fc`, `switch_alloc` | H/V RTT storage minima and full-rate credited transfer | Covered |
| REQ-A-004 | `combine_unit`, `pe_ni`, `dca_stub` | Defined Tier-B lanes/opcodes, Tier-A fallback, disabled DCA | Covered |
| REQ-A-005 | `watchdog_demote`, `multicast_fork`, `vc_buffers`, `xy_route` | 32-cycle timeout and exact-once remaining-leaf escape | Covered |

**Coverage: 22 / 22 requirements (100%).**
