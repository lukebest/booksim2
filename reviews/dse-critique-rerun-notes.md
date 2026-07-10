# DSE Trial 1 Critique Rerun Notes

## Closed by this RefC/docs update

- **HIGH-02:** RefC now models H=7 and V=9 forward link pipelines, one-cycle PE
  ramps, delayed H=16/V=20 credit returns, direction-sized credits, and the
  protected BG-window/calendar-idle arbitration policy.
- **HIGH-04:** Demotion retains per-transaction epoch/sequence, full remaining
  leaf bitmap, accepted leaves, and release-once state. An armed record cannot be
  overwritten; one escape is emitted for each retained leaf.
- **HIGH-05:** A blocked calendar fork is retained in watchdog context rather than
  cleared with ingress state. `test_blocked_fork` verifies both leaves remain.
- **HIGH-06:** Tier-B combine is a tagged three-cycle pipeline. The all-operation,
  eight-lane test covers ADD, AND, OR, XOR, MIN, and MAX. Operand order is
  compiler-defined left-to-right; identities are documented in `architecture.md`.
- **HIGH-07 / MEDIUM-02:** The published eligible 12-hop BG bound is 328 cycles.
  Buffer ownership is five 20-flit per-input FIFOs (100 flits, 51,200 bits), with
  independent 16-H/20-V per-egress credit counters. The PPA buffer contribution
  and total are updated accordingly.
- **MEDIUM-01:** Calendar entries are encoded and decoded as checked 13-bit
  physical SRAM values; RefC retains an unpacked struct only at its interface.

## Executed RefC evidence

`make -C refc test` passes:

- `mesh_router_smoke`
- `test_demote_noloss`
- `test_combine_ops`
- `test_bg_window`
- `test_blocked_fork`
