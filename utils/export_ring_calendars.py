#!/usr/bin/env python3
"""Export ring collective calendars as replay vectors (`calendar-export/v2`).

v2 keeps v1's per-station slot record -- `{slot, valid, in_port, out_port_mask,
opcode}` -- and only changes the port alphabet, because v1's two mechanisms turn
out to be exactly what a bufferless ring station needs:

  * `out_port_mask` is a mask, so copy-and-continue needs no new field: an
    intermediate member of a multicast arc drives `{ring_out, leave}` in the
    same cycle and the flit both lands in L1 and keeps going.
  * `opcode` already distinguishes forward from add, so L1 accumulation is
    `OP_ADD` on a `leave` entry.

The port alphabet becomes the ring station's real ports, four per ring plus the
two local ones:

    row_in_cw  row_in_ccw  row_out_cw  row_out_ccw
    col_in_cw  col_in_ccw  col_out_cw  col_out_ccw
    board (L1 -> ring)     leave (ring -> L1)

A slot record is keyed by (node, slot, in_port) rather than v1's (node, slot).
That is not a schema loosening, it is the station being physically wider: a
station can pass a row-clockwise flit and a column-counterclockwise flit in the
same cycle through different ports, and collapsing them into one record would
make a legal schedule look like a conflict.

Two assertions guard the export, and they are independent of the D-R checker
that produced the schedule:

  1. no two records share (node, slot, in_port) -- one input port carries one
     flit per cycle
  2. no two records at one (node, slot) drive the same output port -- an output
     port is written by one source per cycle

If a D-R-legal calendar ever fails those, the disagreement is real and one of
the two models is wrong. Checking it here is the cheapest place to find out.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Sequence

from rg_ring_calendar import Calendar, build_calendar
from rg_ring_collectives import build_ring_collective
from rg_ring_topo import RingMcastFootprint, RingTopology, verify_dr
from rg_topo import RAMP, RAMP_BW, coord

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "calendars"

PORT: dict[str, int] = {
    "row_in_cw": 0, "row_in_ccw": 1, "row_out_cw": 2, "row_out_ccw": 3,
    "row_board": 4, "row_leave": 5,
    "col_in_cw": 6, "col_in_ccw": 7, "col_out_cw": 8, "col_out_ccw": 9,
    "col_board": 10, "col_leave": 11,
}
OP_FORWARD, OP_ADD = 0, 1

# The set worth shipping: one per collective, taking the best static scheme this
# study found, plus the flat T0 form of each so a consumer can replay what the
# paper mechanism would have had to do.
EXPORTS: tuple[tuple[str, str, str, str], ...] = (
    ("broadcast", "dim_2phase", "T1", "arc multicast, bidirectional half-arcs"),
    ("broadcast", "flat", "T0", "root unicasts to all 47"),
    ("allgather", "dim_2phase", "T1", "row then column arc multicast"),
    ("allgather", "flat", "T0", "flat unicast"),
    ("reduce", "dim_2phase", "T0", "L1 accumulate chain, row then column"),
    ("gather", "flat", "T0", "root ejects every contribution"),
    ("allreduce", "dim_2phase", "T1", "L1 chain reduce then arc multicast"),
    ("alltoall", "flat", "T0", "no structure to exploit"),
)


def _side(topo: RingTopology, ring, direction: int, io: str) -> str:
    return f"{ring[0]}_{io}_{'cw' if direction > 0 else 'ccw'}"


def station_records(topo: RingTopology, cal: Calendar) -> list[dict[str, Any]]:
    """One record per (station, input cycle, input port) implied by the calendar.

    `slot` is the send cycle, as in v1. `in_slot` is when the input port is
    occupied, and the two differ only at a TURN: R4 pins the column boarding
    exactly `t_turn` after the row extract, so a turning flit holds its input
    port one cycle before it drives its output. Folding those into one number
    would report a phantom collision every time another flit legitimately passes
    the same station on the same ring one cycle later, which is exactly what
    happens on a flat broadcast.
    """
    recs: dict[tuple[int, int, str], dict[str, Any]] = {}

    def emit(node: int, slot: int, in_port: str, outs: Iterable[str],
             opcode: int, flow: int, in_slot: int | None = None) -> None:
        isl = slot if in_slot is None else in_slot
        key = (node, isl, in_port)
        outs = sorted(set(outs))
        if key in recs:
            prev = recs[key]
            raise AssertionError(
                f"input port collision: node {node} in_slot {isl} port "
                f"{in_port} wanted by flows {prev['flow']} and {flow}")
        recs[key] = {"node": node, "slot": slot, "in_slot": isl,
                     "in_port": in_port, "outs": outs, "opcode": opcode,
                     "flow": flow}

    for xid, t0 in cal.starts.items():
        fp = cal.fps[xid]
        opcode = OP_ADD if getattr(fp, "op", "FWD") == "ADD" else OP_FORWARD
        members = set(fp.arrivals)
        if isinstance(fp, RingMcastFootprint):
            arcs = [fp.arc]
        else:
            arcs = [a for a in (fp.path.a1, fp.path.a2) if not a.empty]
        for ai, arc in enumerate(arcs):
            in_at_start = (f"{arc.ring[0]}_board" if ai == 0
                           else _side(topo, arcs[0].ring, arcs[0].dir, "in"))
            out_here = _side(topo, arc.ring, arc.dir, "out")
            leave_here = f"{arc.ring[0]}_leave"
            base = 0
            for k, off in fp.boards:
                if k[1] == arc.start and k[2] == arc.ring:
                    base = off
                    break
            emit(arc.start, t0 + base, in_at_start, [out_here], opcode, xid,
                 in_slot=(t0 + base - topo.t_turn) if ai else None)
            lat = topo.ring_lat(arc.ring)
            for hop in range(arc.hops):
                node = arc.nodes[hop + 1]
                slot = t0 + base + (hop + 1) * lat
                last = hop + 1 == arc.hops
                in_here = _side(topo, arc.ring, arc.dir, "in")
                outs: list[str] = []
                if node in members:
                    outs.append(leave_here)
                if not last:
                    outs.append(out_here)
                elif ai + 1 < len(arcs):
                    # the turn: this record is written by the next arc's start
                    continue
                if not outs:
                    continue
                emit(node, slot, in_here, outs, opcode, xid)
    return sorted(recs.values(), key=lambda r: (r["node"], r["in_slot"],
                                                r["in_port"]))


def check_output_ports(recs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """No output port driven twice in one cycle at one station."""
    seen: dict[tuple[int, int, str], int] = {}
    bad: list[dict[str, Any]] = []
    for r in recs:
        for o in r["outs"]:
            key = (r["node"], r["slot"], o)
            if key in seen:
                bad.append({"node": r["node"], "slot": r["slot"], "out": o,
                            "flows": [seen[key], r["flow"]]})
            else:
                seen[key] = r["flow"]
    return bad


def check_ramp(recs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """A station's two rings can both leave in one cycle, but the L1 ramp still
    only takes RAMP_BW flits. This catches the case D-R charges to ("ej", node)
    rather than to a per-ring extract point, so it is a genuinely separate
    check on the same schedule."""
    ins: dict[tuple[int, int], int] = {}
    outs: dict[tuple[int, int], int] = {}
    bad: list[dict[str, Any]] = []
    for r in recs:
        if r["in_port"].endswith("_board"):
            k0 = (r["node"], r["slot"])
            ins[k0] = ins.get(k0, 0) + 1
        n_leave = sum(1 for o in r["outs"] if o.endswith("_leave"))
        if n_leave:
            k = (r["node"], r["slot"])
            outs[k] = outs.get(k, 0) + n_leave
    for tag, tbl in (("inject", ins), ("eject", outs)):
        for (node, slot), c in tbl.items():
            if c > RAMP_BW:
                bad.append({"kind": tag, "node": node, "slot": slot,
                            "count": c, "ramp_bw": RAMP_BW})
    return bad


def export_one(topo: RingTopology, pattern: str, algo: str, tier: str, m: int,
               note: str, *, root: int = 27) -> dict[str, Any]:
    col = build_ring_collective(topo, pattern, m=m, tier=tier, algo=algo,
                                root=root)
    cal = build_calendar(topo, col)
    v = verify_dr(topo, cal.items)
    recs = station_records(topo, cal)
    port_bad = check_output_ports(recs)
    if port_bad:
        raise AssertionError(f"{pattern}/{algo}/{tier}: output port collision "
                             f"in a D-R-legal calendar: {port_bad[:3]}")
    ramp_bad = check_ramp(recs)
    if ramp_bad:
        raise AssertionError(f"{pattern}/{algo}/{tier}: L1 ramp overrun in a "
                             f"D-R-legal calendar: {ramp_bad[:3]}")

    by_node: dict[int, list[dict[str, Any]]] = {}
    for r in recs:
        by_node.setdefault(r["node"], []).append({
            "slot": r["slot"], "in_slot": r["in_slot"], "valid": True,
            "in_port": PORT[r["in_port"]],
            "out_port_mask": sum(1 << PORT[o] for o in r["outs"]),
            "opcode": r["opcode"]})
    stations = [{"station": [*coord(n, topo.mx)], "node": n,
                 "slots": by_node[n]} for n in sorted(by_node)]

    injections = sorted({(t, cal.fps[x].src) for x, t in cal.starts.items()})
    ejections = sorted(cal.node_done.items())

    payload = {
        "schema": "calendar-export/v2",
        "topology": {
            "kind": "dimension_sliced_2d_bufferless_ring",
            "mx": topo.mx, "my": topo.my, "h": topo.H, "v": topo.V,
            "sigma": topo.sigma, "t_turn": topo.t_turn,
            "board_ports": topo.board_ports, "leave_ports": topo.leave_ports,
            "ramp": RAMP, "ramp_bw": RAMP_BW,
            "n_directed_segments": len(topo.directed_links),
        },
        "ports": PORT,
        "opcodes": {"OP_FORWARD": OP_FORWARD, "OP_ADD": OP_ADD},
        "collective": pattern,
        "algorithm": algo,
        "capability_tier": tier,
        "note": note,
        "message_flits": m,
        "root": root if col.root is not None else None,
        "schedule_kind": "rigid_static_calendar",
        "timing_model": ("slots are station-send cycles; H/V segment delay and "
                         "the PE ramp are explicit; zero in-ring buffering, so "
                         "a record either fires on its slot or the schedule is "
                         "invalid"),
        "expected_makespan": cal.makespan,
        "bounds": {k: cal.bounds[k] for k in
                   ("makespan_lb", "binding_lb", "arc_load_lb", "port_lb",
                    "ramp_lb", "latency_lb")},
        "utilization": cal.utilization(topo),
        "slack": cal.slack(),
        "dr_verify": {k: v[k] for k in (
            "R1_link_violations", "R2_board_violations",
            "R3_leave_violations", "R4_turn_violations", "R5_voq_violations",
            "MC_shape_violations", "max_turn_residency", "n_mcast_grants",
            "n_mcast_copies", "conflict_free")},
        "n_records": len(recs),
        "n_stations": len(stations),
        "stations": stations,
        "injections": [{"node": s, "slot": t} for t, s in injections],
        "expected_ejections": [{"node": n, "ready_at": t}
                               for n, t in ejections],
        "phases": [{"name": p.name, "n_xfers": len(p.xfers),
                    "n_mcast": p.n_mcast, "barrier": p.barrier}
                   for p in col.phases],
        "phase_window": cal.phase_window,
        "notes": [
            "an out_port_mask with both a ring_out bit and the leave bit is a "
            "copy-and-continue multicast station: the flit lands in L1 and "
            "keeps travelling in the same cycle",
            "opcode=OP_ADD on a leave record means the PE accumulates into L1 "
            "instead of storing a separate copy; it changes no network timing",
            "records are keyed by (node, in_slot, in_port) because a station "
            "passes independent flits on its row and column ports in the same "
            "cycle",
            "in_slot equals slot except at a turn, where R4 pins the boarding "
            "t_turn after the extract, so the input port is held one cycle "
            "before the output is driven",
        ],
    }
    return payload


def main() -> None:
    topo = RingTopology()
    OUT.mkdir(parents=True, exist_ok=True)
    index: list[dict[str, Any]] = []
    for m in (1, 13):
        for pattern, algo, tier, note in EXPORTS:
            p = export_one(topo, pattern, algo, tier, m, note)
            name = f"ring_{pattern}_{algo}_{tier}_m{m}.json"
            (OUT / name).write_text(
                json.dumps(p, separators=(",", ":")) + "\n", encoding="utf-8")
            index.append({"file": name, "collective": pattern, "algo": algo,
                          "tier": tier, "m": m,
                          "makespan": p["expected_makespan"],
                          "makespan_lb": p["bounds"]["makespan_lb"],
                          "binding_lb": p["bounds"]["binding_lb"],
                          "n_records": p["n_records"],
                          "conflict_free": p["dr_verify"]["conflict_free"]})
            print(f"  {name:52} makespan={p['expected_makespan']:>6} "
                  f"records={p['n_records']:>6} "
                  f"mcast_copies={p['dr_verify']['n_mcast_copies']:>5} "
                  f"cf={int(p['dr_verify']['conflict_free'])}")
    (OUT / "ring_index.json").write_text(
        json.dumps({"schema": "calendar-export/v2",
                    "generated_by": "utils/export_ring_calendars.py",
                    "entries": index}, indent=1) + "\n", encoding="utf-8")
    print(f"\nwrote {len(index)} calendars + ring_index.json to "
          f"{OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
