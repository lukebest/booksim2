# Calendar export schema v1

`results/superpose_6x8.json` is a makespan-study result, not a router-slot
table.  Trial 1 therefore uses this JSON schema as a reproducible offline
schedule-export boundary.  `utils/export_calendar_slots.py` emits deterministic
synthetic schedules that are topology-correct for the declared mesh; it does
not claim to reconstruct the research schedules.

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
- `message_flits` is the per-source message length. Trial-1 exported vectors
  use one flit.
- A router coordinate is `[x,y]`, with `0 <= x < mx`, `0 <= y < my`.
- `slot` is an absolute router-send cycle. An injection at slot `s` becomes
  available at the local router in `s + 1` due to the PE ramp.
- `expected_makespan` is the last expected terminal delivery cycle, including
  the PE down-ramp.

## Slot entry

Each router owns a list of these records:

```json
{"slot":17,"valid":true,"in_port":3,"out_port_mask":6,"opcode":0}
```

The fields are the physical calendar entry `{slot, valid, in_port,
out_port_mask, opcode}`:

- `in_port`: `north=0`, `east=1`, `south=2`, `west=3`, `local=4`.
- `out_port_mask`: bit `0..4` selects the same ordered port encoding. Multiple
  bits are an atomic multicast fork.
- `opcode`: `0=FORWARD`; `1..6=COMBINE_ADD/AND/OR/XOR/MIN/MAX`.
- Invalid slots are omitted. A loader must reject duplicate `(router,slot)`
  entries and ports/masks outside the five-port range.

The replayer applies explicit H=7 horizontal, V=9 vertical, and one-cycle PE
ramp delays. `baseline` contains any comparable value from the makespan-only
research JSON and always labels its comparison as non-equivalent.

## Trial-1 scope

Reduce and allreduce vectors place `COMBINE_ADD` opcodes on their generated
converge paths, exercising the Tier-B replay opcode and its three-cycle
replayer delay. They are structural schedule tests, not a proof of full
48-operand numerical reduction; that remains a separate HIGH-06 closure item.
