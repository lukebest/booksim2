# Calendar export schema v1 (Trial 3 — SparseCal aligned)

`results/superpose_6x8.json` is a makespan-study result, not a router-slot
table.  Trial 1 therefore uses this JSON schema as a reproducible offline
schedule-export boundary.  `utils/export_calendar_slots.py` emits deterministic
synthetic schedules that are topology-correct for the declared mesh; it does
not claim to reconstruct the research schedules.

**Trial 3 alignment:** hardware stores a **sparse ordered event list** per router
(depth **128** per bank, 23 bits/entry). JSON exports are naturally sparse — only
valid events are listed; no dense 1024-slot padding required.

## Top-level object

```json
{
  "schema": "calendar-export/v1",
  "topology": {"mx": 6, "my": 8, "h": 7, "v": 9, "ramp_bw": 1},
  "collective": "broadcast",
  "message_flits": 1,
  "routers": [{"router": [0, 0], "slots": []}],
  "injections": [{"source": [0, 0], "slot": 0, "value": 1}],
  "expected_ejections": [[5, 7]],
  "expected_makespan": 100
}
```

- `collective` is one of `broadcast`, `allgather`, `gather`, `reduce`, or
  `allreduce`.
- `message_flits` is the per-source message length. Exported vectors use one flit.
- A router coordinate is `[x,y]`, with `0 <= x < mx`, `0 <= y < my`.
- `slot` is an absolute router-send cycle. An injection at slot `s` becomes
  available at the local router in `s + 1` due to the PE ramp.
- `expected_makespan` is the last expected terminal delivery cycle, including
  the PE down-ramp.

## Slot entry (sparse event)

Each router owns a list of these records (sorted by `slot` on load):

```json
{"slot":17,"valid":true,"in_port":3,"out_port_mask":6,"opcode":0}
```

The fields map to the 23-bit hardware entry `{slot[9:0], valid, in_port,
out_port_mask, opcode}`:

- `slot`: explicit 10-bit cycle index (0..1023; counter wraps at 1024).
- `in_port`: `north=0`, `east=1`, `south=2`, `west=3`, `local=4`.
- `out_port_mask`: bit `0..4` selects the same ordered port encoding. Multiple
  bits are an atomic multicast fork.
- `opcode`: `0=FORWARD`; `1=PE_HANDOFF` (Tier A tag); `2..6` reserved (legacy combine encodings illegal for router compute).
- Invalid slots are **omitted** (sparse). A loader must reject duplicate `(router,slot)`
  entries, ports/masks outside the five-port range, and lists exceeding **128 entries
  per bank**.

The replayer applies explicit H=7 horizontal, V=9 vertical, and one-cycle PE
ramp delays. `baseline` contains any comparable value from the makespan-only
research JSON and always labels its comparison as non-equivalent.

## Sparsity evidence (`results/calendars/*_m1.json`)

| Collective | Total entries | Avg/router | Max/router | Max slot |
|---|---:|---:|---:|---:|
| broadcast | 48 | 1 | 1 | 99 |
| allgather | 192 | 4 | 4 | 699 |
| gather / reduce | 336 | 7 | 48 | 851 |
| allreduce | 384 | 8 | **49** | **951** |

Hardware depth 128 per bank covers max observed 49 with >2× margin.

## Trial-3 scope

Reduce and allreduce vectors use gather/forward schedules with
`CAL_OP_PE_HANDOFF` tags where applicable. The replayer counts PE handoffs for
observability; it does **not** perform in-router arithmetic (Tier A). Full
48-operand PE numerical reduction remains outside the router BFM.

Dispatch model: **next-event match** — at each cycle, compare global slot counter
against the next sparse entry; fire calendar path on match; BG eligible otherwise
(soft priority).
