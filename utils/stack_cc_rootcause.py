#!/usr/bin/env python3
"""Why the six groups do not finish together, decomposed per fabric.

The write batch and the read batch behave completely differently under S0,
and the report has to say why rather than assert it. Both questions are
answered from the workload and the binding alone, with no simulation:

  * **Demand.** Every transaction's forward and reverse routes are walked and
    the flits are attributed to the top-die group that owns the AI core. That
    gives, per group and per fabric class, the flit-hops it asks for. If the
    six rows are equal the fabric is symmetric for that operation and the
    spread has to come from contention, not from geometry.

  * **The link each group is stuck behind.** A group cannot finish before the
    busiest directed link on any of its own routes has carried *everybody's*
    flits across it -- one flit per link per VC per cycle. Taking the maximum
    over the links a group uses gives a per-group lower bound that is a pure
    structural quantity, and it is the one to correlate against the measured
    finish times. Where it tracks them, the spread is the topology; where it
    does not, the spread is arbitration.

  * **The eight D2D crossings a group owns.** Every one of a group's flits
    leaves its die through one of eight bridges, and which bridge is fixed by
    the destination column, not chosen. So the down and up directions of those
    eight links are the narrowest thing each group owns outright, and an
    imbalance across the eight is a self-inflicted serialisation that no amount
    of fairness between groups can relieve.

Routes are counted per (core, HA) pair rather than per transaction -- there
are 5,760 pairs against 122,880 transactions and each pair's route is fixed
-- and split evenly across the two top-die planes, which is what
`least_occupied` does on a batch this balanced.

Usage:
    python3 stack_cc_rootcause.py            # 4 tiles, read only
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from dse_ring2_write_fair import pearson, spearman
from rg_stack_topo import N_TILES, StackTopology, Txn, build_tiled_read

ROOT = Path(__file__).resolve().parents[1]
FOCUS = ROOT / "results" / "stack_cc_read_focus.json"
OUT = ROOT / "results" / "stack_cc_rootcause.json"

M_REQ, M_RSP, M_WDATA = 1, 2, 4
TOP_PLANES = 2


def _phases(op: str) -> tuple[tuple[str, str, int], ...]:
    """(VC, direction, flits) for each leg of one transaction.

    WriteNoSnp is REQ out, DBIDResp back, WriteData out, Comp back; the two
    responses are the `m_rsp = 2` the simulator charges. ReadNoSnp is REQ out
    and a four-flit CompData back, and it is that reversal -- the bulk moving
    *up* the fabric instead of down -- that makes the two batches load the
    same links so differently.
    """
    if op == "write":
        return (("req", "fwd", M_REQ), ("rsp", "rev", M_RSP),
                ("dat", "fwd", M_WDATA))
    return (("req", "fwd", M_REQ), ("dat", "rev", M_WDATA))


def _spread(loads: Counter) -> dict[str, Any]:
    """Totals and the busiest-to-quietest ratio over one set of links."""
    v = sorted(loads.values(), reverse=True)
    if not v:
        return {"links": 0, "total": 0, "max": 0, "min": 0, "spread": 0.0}
    return {"links": len(v), "total": sum(v), "max": v[0], "min": v[-1],
            "spread": round(v[0] / v[-1], 4) if v[-1] else 0.0}


def analyse(topo: StackTopology, txns: list[Txn], op: str) -> dict[str, Any]:
    pairs: Counter = Counter()
    for x in txns:
        pairs[(x.core, x.ha)] += 1
    n_group = topo.n_die

    # (edge, vc) -> flits, and the same split by group
    load: Counter = Counter()
    by_group: dict[int, Counter] = {g: Counter() for g in range(n_group)}
    fab_group: dict[int, Counter] = {g: Counter() for g in range(n_group)}
    edges_of_group: dict[int, set] = {g: set() for g in range(n_group)}
    d2d_group: dict[int, dict[str, Counter]] = {
        g: {"down": Counter(), "up": Counter()} for g in range(n_group)}

    for (core, ha), cnt in pairs.items():
        g = topo.nodes[core].die
        # least_occupied alternates planes on a balanced batch
        share = (cnt // TOP_PLANES, cnt - cnt // TOP_PLANES)
        for plane in range(TOP_PLANES):
            k = share[plane]
            if not k:
                continue
            fwd = topo.route(core, ha, plane)
            rev = topo.route(ha, core, plane)
            for vc, direction, m in _phases(op):
                path = fwd if direction == "fwd" else rev
                w = k * m
                for e in path:
                    load[(e, vc)] += w
                    by_group[g][(e, vc)] += w
                    fab_group[g][topo.fabric_of(e)] += w
                    edges_of_group[g].add((e, vc))
                    if topo.is_d2d(e):
                        side = "down" if direction == "fwd" else "up"
                        d2d_group[g][side][e] += w

    rows = []
    for g in range(n_group):
        crit, crit_load = None, 0
        for key in edges_of_group[g]:
            if load[key] > crit_load:
                crit, crit_load = key, load[key]
        eid, vc = crit if crit else (0, "dat")
        own = by_group[g][crit] if crit else 0
        rows.append({
            "group": g,
            "flit_hops": {k: v for k, v in sorted(fab_group[g].items())},
            "flit_hops_total": sum(fab_group[g].values()),
            "crit_edge": eid, "crit_vc": vc,
            "crit_fabric": topo.fabric_of(eid),
            "crit_load": crit_load,
            "crit_own": own,
            "crit_own_frac": round(own / crit_load, 4) if crit_load else 0.0,
            "n_edges": len(edges_of_group[g]),
            "d2d": {side: _spread(d2d_group[g][side])
                    for side in ("down", "up")},
        })

    # The busiest link anywhere, for the batch-wide bound the report quotes.
    top_edges = sorted(load.items(), key=lambda kv: -kv[1])[:8]
    return {
        "op": op,
        "n_txn": len(txns),
        "n_pairs": len(pairs),
        "groups": rows,
        "fabric_totals": {f: sum(fab_group[g][f] for g in range(n_group))
                          for f in sorted({k for g in range(n_group)
                                           for k in fab_group[g]})},
        "hottest": [{"edge": e, "vc": vc, "fabric": topo.fabric_of(e),
                     "load": v,
                     "by_group": {str(g): by_group[g][(e, vc)]
                                  for g in range(n_group)}}
                    for (e, vc), v in top_edges],
    }


def correlate(rows: list[dict[str, Any]],
              finish: dict[str, int]) -> dict[str, Any]:
    """Does the structural bound explain the measured finish order?"""
    gs = [r["group"] for r in rows if str(r["group"]) in finish]
    if len(gs) < 3:
        return {}
    lb = [rows[g]["crit_load"] for g in gs]
    own = [rows[g]["crit_own"] for g in gs]
    dem = [rows[g]["flit_hops_total"] for g in gs]
    fin = [finish[str(g)] for g in gs]
    return {
        "groups": gs,
        "finish": fin,
        "crit_load": lb,
        "crit_own": own,
        "demand": dem,
        "pearson_crit_finish": round(pearson(lb, fin), 4),
        "spearman_crit_finish": round(spearman(lb, fin), 4),
        "pearson_own_finish": round(pearson(own, fin), 4),
        "spearman_own_finish": round(spearman(own, fin), 4),
        "pearson_demand_finish": round(pearson(dem, fin), 4),
        "finish_spread": (round(max(fin) / min(fin), 4) if min(fin) else 0.0),
        "demand_spread": (round(max(dem) / min(dem), 4) if min(dem) else 0.0),
        "crit_spread": (round(max(lb) / min(lb), 4) if min(lb) else 0.0),
    }


def _measured(blob: dict[str, Any], op: str) -> dict[str, int]:
    s0 = ((blob.get("schemes") or {}).get(op) or {}).get("s0") or {}
    ds = (s0.get("full") or {}).get("done_series") or {}
    return {k: int(v) for k, v in (ds.get("finish_by_group") or {}).items()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tiles", type=int, default=N_TILES)
    args = ap.parse_args()
    topo = StackTopology()
    blob = json.loads(FOCUS.read_text()) if FOCUS.exists() else {}
    out: dict[str, Any] = {"tiles": args.tiles, "ops": {}}
    for op, build in (("read", build_tiled_read),):
        txns = build(topo, n_tiles=args.tiles, seed=0)
        res = analyse(topo, txns, op)
        fin = _measured(blob, op)
        if fin:
            res["measured"] = correlate(res["groups"], fin)
        out["ops"][op] = res
        print(f"\n{op}: {res['n_txn']} txn, fabric flit-hops "
              + ", ".join(f"{k}={v:,}" for k, v in
                          res["fabric_totals"].items()))
        for r in res["groups"]:
            up, down = r["d2d"]["up"], r["d2d"]["down"]
            print(f"  group {r['group']}  demand={r['flit_hops_total']:>9,}  "
                  f"crit {r['crit_fabric']}:{r['crit_vc']} "
                  f"load={r['crit_load']:>7,} own={r['crit_own_frac']:.3f}  "
                  f"d2d down max={down['max']:>6,}/{down['spread']:.2f} "
                  f"up max={up['max']:>6,}/{up['spread']:.2f}")
        m = res.get("measured")
        if m:
            print(f"  finish {m['finish']}  spread={m['finish_spread']}  "
                  f"rho(crit,finish)={m['spearman_crit_finish']}  "
                  f"rho(own,finish)={m['spearman_own_finish']}")
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
