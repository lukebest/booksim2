#!/usr/bin/env python3
"""Two-stage congestion-control sweep on the 3D-stacked fabric.

The question the report has to answer is which *configuration* of each
scheme is worth showing, and the honest way to answer it is to measure every
candidate rather than quote the ring study's operating point. A full 4-tile
batch is 122,880 transactions and takes tens of minutes, so a 37-configuration
grid across write and read is not affordable at that size. It is affordable at
one tile, where the same 60 cores cover the same 96 HAs with a quarter of the
transactions and the ordering between configurations is what is being asked
for, not the absolute number. So:

    stage 1  `--stage sweep`    every configuration, 1 tile, write and read
    stage 2  `--stage confirm`  the survivors, 4 tiles, the numbers that ship

Stage 2 is where every figure and table in the report comes from. Stage 1 only
decides which knob settings get to appear there, and the report prints its
whole grid so the choice can be audited.

Results are keyed by `scheme|config|op|tiles` and merged into the store on
every run, so an interrupted sweep resumes instead of restarting.

Usage:
    python3 dse_stack_cc_sweep.py --stage sweep   --jobs 3
    python3 dse_stack_cc_sweep.py --stage confirm --jobs 3 --top-k 2
    python3 dse_stack_cc_sweep.py --stage base    --jobs 2
    python3 dse_stack_cc_sweep.py --emit
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

from dse_stack_write_fair import (BURST_LEN, BW_WINDOW, FABRIC, M_RSP,
                                  M_WDATA, ROUTE_LABEL, STRIDE, TILING_SIZE,
                                  binding_table, die_board_table, root_cause,
                                  run_scheme, topology_summary, vseat_load)
from rg_stack_topo import (StackTopology, build_tiled_read, build_tiled_write,
                           ha_histogram)

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "results" / "stack_cc_sweep.json"
FOCUS = ROOT / "results" / "stack_cc_focus.json"

OPS = ("write", "read")
SWEEP_TILES = 1
CONFIRM_TILES = 4
OC = 128
POS = 512

# Equal-share DAT rate per core at the 4-tile bound: 2048 txn x 4 flits over
# the 30,752-cycle composite bound. The bus-free S22 variant accrues this
# instead of learning the mean from the broadcast.
TARGET_PER_CORE = 0.266
TARGET_PER_GROUP = TARGET_PER_CORE * 10


# ---------------------------------------------------------------------------
# the grid
# ---------------------------------------------------------------------------

# S1: the AIMD window, the band that maps congestion level to grow/shrink, and
# the two hardware-cheaper readings of the same controller.
S1_GRID: dict[str, dict[str, Any]] = {
    "w32-spec": dict(window=32),
    "w64-spec": dict(window=64),
    "w128-spec": dict(window=128),
    "w64-gentle": dict(window=64, band="gentle"),
    "w64-harsh": dict(window=64, band="harsh"),
    "w32-gentle": dict(window=32, band="gentle"),
    "w128-gentle": dict(window=128, band="gentle"),
    "w64-nopath": dict(window=64, path_credit=False),
    "w64-local": dict(window=64, path_credit=False, use_bus=False),
    "w64-both": dict(window=64, scope="both"),
}

# S22 stands in one of two places, and the grid keeps them apart because the
# actuator differs. Core grain yields on the top-die ring, where the ring
# study's geometric dodge is the one that means anything. Group grain has no
# purchase there -- one ring is one group -- so it acts at the HAs, where
# responses for six groups share a port, and its dodge is by destination.
S22_GRID: dict[str, dict[str, Any]] = {
    "core-w64-t2": dict(dfc_grain="core", dfc_dest_pref=False),
    "core-w64-t2-d8": dict(dfc_grain="core", dfc_dest_pref=False,
                           dfc_dodge=8),
    "core-w64-t05-d8": dict(dfc_grain="core", dfc_dest_pref=False,
                            dfc_thresh=0.5, dfc_dodge=8),
    "core-w16-t05-d8": dict(dfc_grain="core", dfc_dest_pref=False,
                            dfc_window=16, dfc_thresh=0.5, dfc_dodge=8),
    "core-w64-t2-h16-m3-d8": dict(dfc_grain="core", dfc_dest_pref=False,
                                  dfc_hold=16, dfc_margin=3.0, dfc_dodge=8),
    "core-w64-t05-h16-m3-d8": dict(dfc_grain="core", dfc_dest_pref=False,
                                   dfc_thresh=0.5, dfc_hold=16,
                                   dfc_margin=3.0, dfc_dodge=8),
    "core-busfree-d8": dict(dfc_grain="core", dfc_dest_pref=False,
                            dfc_target=TARGET_PER_CORE, dfc_thresh=0.5,
                            dfc_dodge=8),
    "core-w64-t2-deepq": dict(dfc_grain="core", dfc_dest_pref=False,
                              dfc_dodge=32, dir_inj_depth=32),
    # On a read batch a core injects nothing but REQ -- the CompData it is
    # waiting for is issued by the HA -- so a DAT-only actuator has nothing
    # to act on and the scheme degenerates to S0. Letting the actuator reach
    # REQ gives the core-grain variant its only read-side lever: a core that
    # has already banked more than its share holds its next request back.
    "core-req-t05-d8": dict(dfc_grain="core", dfc_dest_pref=False,
                            dfc_act_vcs=("dat", "req"), dfc_thresh=0.5,
                            dfc_dodge=8),
    "core-req-t2-d8": dict(dfc_grain="core", dfc_dest_pref=False,
                           dfc_act_vcs=("dat", "req"), dfc_dodge=8),
    "core-req-t05-h16-m3-d8": dict(dfc_grain="core", dfc_dest_pref=False,
                                   dfc_act_vcs=("dat", "req"),
                                   dfc_thresh=0.5, dfc_hold=16,
                                   dfc_margin=3.0, dfc_dodge=8),
    "grp-ha-dat-d8": dict(dfc_grain="group", dfc_scope_nodes="ha_only",
                          dfc_dodge=8, dfc_thresh=0.5),
    "grp-ha-datrsp-d8": dict(dfc_grain="group", dfc_scope_nodes="ha_only",
                             dfc_act_vcs=("dat", "rsp"), dfc_dodge=8,
                             dfc_thresh=0.5),
    "grp-both-datrsp-d8": dict(dfc_grain="group", dfc_scope_nodes="both",
                               dfc_act_vcs=("dat", "rsp"), dfc_dodge=8,
                               dfc_thresh=0.5),
    "grp-ha-datrsp-w16-d8": dict(dfc_grain="group", dfc_scope_nodes="ha_only",
                                 dfc_act_vcs=("dat", "rsp"), dfc_window=16,
                                 dfc_dodge=8, dfc_thresh=0.5),
    "grp-ha-datrsp-m3-d8": dict(dfc_grain="group", dfc_scope_nodes="ha_only",
                                dfc_act_vcs=("dat", "rsp"), dfc_dodge=8,
                                dfc_thresh=0.5, dfc_margin=3.0),
    "grp-ha-busfree-d8": dict(dfc_grain="group", dfc_scope_nodes="ha_only",
                              dfc_act_vcs=("dat", "rsp"), dfc_dodge=8,
                              dfc_thresh=0.5, dfc_target=TARGET_PER_GROUP),
    "grp-ha-datrsp-deepq": dict(dfc_grain="group", dfc_scope_nodes="ha_only",
                                dfc_act_vcs=("dat", "rsp"), dfc_dodge=32,
                                dfc_thresh=0.5, dir_inj_depth=32),
}

# S16 at group granularity. `overcommit` is the only fairness/throughput knob
# the receiver has; the rest say how the window is shared out.
S16G_GRID: dict[str, dict[str, Any]] = {
    "oc8": dict(overcommit=8),
    "oc16": dict(overcommit=16),
    "oc32": dict(overcommit=32),
    "oc64": dict(overcommit=64),
    "oc8-quota": dict(overcommit=8, group_quota=True),
    "oc16-quota": dict(overcommit=16, group_quota=True),
    "oc32-quota": dict(overcommit=32, group_quota=True),
    "oc16-rr": dict(overcommit=16, policy="round_robin"),
    "oc16-noeager": dict(overcommit=16, eager=False),
    "oc16-bus30": dict(overcommit=16, grant_lat=30),
}

# The per-core receiver, kept as the reference the group grain is an
# improvement *over*. Same knob, same datapath, different arbitration axis.
S16_GRID: dict[str, dict[str, Any]] = {
    "oc16": dict(overcommit=16),
    "oc32": dict(overcommit=32),
}

GRID: dict[str, dict[str, dict[str, Any]]] = {
    "s1": S1_GRID, "s22": S22_GRID, "s16g": S16G_GRID, "s16": S16_GRID,
}
SWEEP_SCHEMES = ("s1", "s22", "s16g", "s16")

LABEL = {
    "s0": "S0 基线（无源端流控）",
    "s1": "S1 源端 AIMD 控速",
    "s22": "S22 赤字流控（让行 / 绕行）",
    "s16": "S16 目的端授权（per-core）",
    "s16g": "S16G 目的端授权（per-group）",
}


# ---------------------------------------------------------------------------
# one job
# ---------------------------------------------------------------------------

_TOPO: StackTopology | None = None
_TXNS: dict[tuple[str, int], list] = {}


def _init_worker() -> None:
    global _TOPO
    _TOPO = StackTopology()


def _txns(op: str, tiles: int) -> list:
    key = (op, tiles)
    got = _TXNS.get(key)
    if got is None:
        build = build_tiled_write if op == "write" else build_tiled_read
        got = build(_TOPO, n_tiles=tiles, seed=0)
        _TXNS[key] = got
    return got


def job_key(scheme: str, cfg: str, op: str, tiles: int) -> str:
    return f"{scheme}|{cfg}|{op}|{tiles}"


def _light(r: dict[str, Any]) -> dict[str, Any]:
    """The stage-1 record: enough to rank, small enough to keep them all."""
    g = r.get("group") or {}
    ds = r.get("done_series") or {}
    fin = ds.get("finish_by_group") or g.get("finish_by_group") or {}
    vals = [v for v in fin.values() if v]
    return {
        "makespan": r["makespan"],
        "completed": r["completed"],
        "n_txn_done": r["n_txn_done"],
        "stall": r.get("stall_detected", False),
        "retry": (r.get("retry") or {}).get("n_retry", 0),
        "deflections": r.get("n_deflections", 0),
        "board_fail": r.get("n_board_fail", 0),
        "goodput_total": g.get("goodput_total", 0.0),
        "goodput_jain": g.get("goodput_jain", 0.0),
        "group_finish": fin,
        "finish_spread": (round(max(vals) / min(vals), 4)
                          if vals and min(vals) else 0.0),
        "eff": r.get("eff", 0.0),
        "wall_s": r.get("wall_s", 0.0),
        "fc": {k: v for k, v in (r.get("fc") or {}).items()
               if k not in ("trace", "final_deficit")},
    }


def run_job(spec: tuple[str, str, str, int, bool]) -> tuple[str, dict[str, Any]]:
    scheme, cfg, op, tiles, full = spec
    kw = dict(GRID.get(scheme, {}).get(cfg, {}))
    txns = _txns(op, tiles)
    hist = ha_histogram(_TOPO, txns)
    stall = max(80_000, 160 * hist["per_core_txn"])
    t0 = time.time()
    r = run_scheme(_TOPO, txns, scheme, route="bound", seed=0,
                   keep_trace=False, core_outstanding=OC, ha_pos_depth=POS,
                   stall_after=stall, **kw)
    bound = _TOPO.write_bounds(txns, m_req=1, m_rsp=M_RSP, m_wdata=M_WDATA)
    r["eff"] = round(bound["bound"] / max(1, r["makespan"]), 4)
    rec = _light(r)
    rec.update({"scheme": scheme, "config": cfg, "op": op, "tiles": tiles,
                "knobs": {k: (list(v) if isinstance(v, tuple) else v)
                          for k, v in kw.items()},
                "wall_s": round(time.time() - t0, 1)})
    if full:
        rec["full"] = {
            "bounds": bound,
            "group": r.get("group"),
            "fairness": r.get("fairness"),
            "done_series": r.get("done_series"),
            "bw_series": r.get("bw_series"),
            "fabric_series": r.get("fabric_series"),
            "fabric": r.get("fabric"),
            "fifo": r.get("fifo"),
            "retry": r.get("retry"),
            "board": die_board_table(_TOPO, r.get("board_by_core_dir") or {},
                                     die=0),
            "board_fail_by_src": r.get("board_fail_by_src"),
            "counters": {k: r.get(k) for k in (
                "n_swaps", "n_swaps_hv", "n_swaps_d2d", "n_deflections",
                "n_board_fail", "n_itag_raised", "n_itag_yield",
                "n_aimd_increase", "n_aimd_decrease", "n_fc_deny",
                "max_core_outstanding", "max_srcq", "max_turn_q", "max_d2d_q",
                "max_d2d_buf", "n_delivered_flits", "backlog", "in_flight",
                "lat_mean", "lat_p50", "lat_p99", "lat_max", "net_mean")},
            "fc": r.get("fc"),
        }
    return job_key(scheme, cfg, op, tiles), rec


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------

def load_store() -> dict[str, Any]:
    if STORE.exists():
        return json.loads(STORE.read_text())
    return {"runs": {}, "meta": {}}


def save_store(store: dict[str, Any]) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(store, indent=1, ensure_ascii=False))


def use_store(path: str) -> None:
    """Point the store elsewhere, so a rehearsal cannot disturb the real run."""
    global STORE
    STORE = Path(path)


def use_focus(path: str) -> None:
    global FOCUS
    FOCUS = Path(path)


def run_all(specs: Sequence[tuple], jobs: int, store: dict[str, Any]) -> None:
    todo = [s for s in specs
            if job_key(s[0], s[1], s[2], s[3]) not in store["runs"]]
    if not todo:
        print("[sweep] nothing to do; every job is already in the store")
        return
    print(f"[sweep] {len(todo)} jobs on {jobs} workers "
          f"({len(specs) - len(todo)} already done)", flush=True)
    t0 = time.time()
    done = 0
    ctx = mp.get_context("fork")
    with ctx.Pool(jobs, initializer=_init_worker) as pool:
        for key, rec in pool.imap_unordered(run_job, todo):
            store["runs"][key] = rec
            save_store(store)
            done += 1
            el = time.time() - t0
            print(f"[{done:3d}/{len(todo)}] {key:44s} "
                  f"t={rec['makespan']:7d} done={rec['n_txn_done']} "
                  f"{'OK' if rec['completed'] else 'COLLAPSE'} "
                  f"retry={rec['retry']} spread={rec['finish_spread']:.3f} "
                  f"({rec['wall_s']:.0f}s, elapsed {el/60:.1f}m)", flush=True)


# ---------------------------------------------------------------------------
# stages
# ---------------------------------------------------------------------------

def sweep_specs(schemes: Iterable[str], tiles: int) -> list[tuple]:
    out = []
    for s in schemes:
        for cfg in GRID[s]:
            for op in OPS:
                out.append((s, cfg, op, tiles, False))
    return out


def rank(store: dict[str, Any], scheme: str, tiles: int
         ) -> list[tuple[str, int, dict[str, int]]]:
    """Configs of one scheme by summed write+read makespan, best first.

    A configuration that fails to drain either batch is not ranked at all: a
    short makespan on an unfinished batch is not a result.
    """
    rows = []
    for cfg in GRID[scheme]:
        per = {}
        ok = True
        for op in OPS:
            r = store["runs"].get(job_key(scheme, cfg, op, tiles))
            if r is None or not r["completed"]:
                ok = False
                break
            per[op] = r["makespan"]
        if ok:
            rows.append((cfg, sum(per.values()), per))
    rows.sort(key=lambda x: x[1])
    return rows


def confirm_specs(store: dict[str, Any], schemes: Iterable[str], top_k: int,
                  tiles: int = CONFIRM_TILES) -> list[tuple]:
    out = []
    for s in schemes:
        picks = [c for c, _, _ in rank(store, s, SWEEP_TILES)[:top_k]]
        if not picks:
            print(f"[confirm] {s}: no drained configuration at "
                  f"{SWEEP_TILES} tile(s); skipped")
            continue
        print(f"[confirm] {s}: {', '.join(picks)}")
        for cfg in picks:
            for op in OPS:
                out.append((s, cfg, op, tiles, True))
    return out


def base_specs(tiles: int) -> list[tuple]:
    return [("s0", "base", op, tiles, True) for op in OPS]


# ---------------------------------------------------------------------------
# the blob the report reads
# ---------------------------------------------------------------------------

def pick_final(store: dict[str, Any], scheme: str,
               tiles: int = CONFIRM_TILES) -> str | None:
    """The confirmed config with the lowest write+read makespan."""
    best, best_t = None, None
    for cfg in GRID.get(scheme, {"base": {}}):
        per = []
        for op in OPS:
            r = store["runs"].get(job_key(scheme, cfg, op, tiles))
            if r is None or not r["completed"]:
                per = []
                break
            per.append(r["makespan"])
        if not per:
            continue
        tot = sum(per)
        if best_t is None or tot < best_t:
            best, best_t = cfg, tot
    return best


def emit(store: dict[str, Any], tiles: int = CONFIRM_TILES) -> None:
    topo = StackTopology()
    schemes = ["s0"] + [s for s in SWEEP_SCHEMES]
    chosen: dict[str, str] = {}
    per_op: dict[str, dict[str, Any]] = {op: {} for op in OPS}
    for s in schemes:
        cfg = "base" if s == "s0" else pick_final(store, s, tiles)
        if cfg is None:
            print(f"[emit] {s}: no confirmed run; left out")
            continue
        rows = {}
        for op in OPS:
            r = store["runs"].get(job_key(s, cfg, op, tiles))
            if r is None or "full" not in r:
                rows = {}
                break
            rows[op] = r
        if not rows:
            print(f"[emit] {s}: confirmed run has no full record; left out")
            continue
        chosen[s] = cfg
        for op in OPS:
            per_op[op][s] = rows[op]

    wr = build_tiled_write(topo, n_tiles=tiles, seed=0)
    rd = build_tiled_read(topo, n_tiles=tiles, seed=0)
    fabric = dict(FABRIC)
    fabric.update({"core_outstanding": OC, "ha_pos_depth": POS})
    topology = topology_summary(topo)
    blob = {
        "meta": {
            "tiles": tiles, "sweep_tiles": SWEEP_TILES,
            "n_tiles": tiles,
            "core_outstanding": OC, "pos_depth": POS,
            "m_req": 1, "m_rsp": M_RSP, "m_wdata": M_WDATA,
            "bw_window": BW_WINDOW, "fabric": fabric,
            "burst_len": BURST_LEN, "stride": STRIDE,
            "tiling_size": TILING_SIZE,
            "route_label": ROUTE_LABEL, "rtt": topology["rtt"],
            "n_txn": len(wr), "txn_per_core": len(wr) // len(topo.cores),
            "chosen": chosen, "label": LABEL,
            "wall_s": round(sum(r.get("wall_s", 0.0)
                                for r in store["runs"].values()), 1),
        },
        "topology": topology,
        "binding": binding_table(topo),
        "grid": {s: {c: {k: (list(v) if isinstance(v, tuple) else v)
                         for k, v in kw.items()}
                     for c, kw in g.items()} for s, g in GRID.items()},
        "sweep": {k: {kk: vv for kk, vv in r.items() if kk != "full"}
                  for k, r in store["runs"].items()
                  if r["tiles"] == SWEEP_TILES},
        "confirm": {k: {kk: vv for kk, vv in r.items() if kk != "full"}
                    for k, r in store["runs"].items()
                    if r["tiles"] == tiles},
        "schemes": {op: per_op[op] for op in OPS},
        "workload": {
            "kind": "tiled_separate", "n_tiles": tiles,
            "write": {"n_txn": len(wr), "ha_hist": ha_histogram(topo, wr)},
            "read": {"n_txn": len(rd), "ha_hist": ha_histogram(topo, rd)},
        },
    }
    s0w = per_op["write"].get("s0")
    s0r = per_op["read"].get("s0")
    if s0w and s0r:
        blob["root_cause"] = {
            "write": root_cause(topo, _rehydrate(s0w), vseat_load(topo, wr)),
            "read": root_cause(topo, _rehydrate(s0r), vseat_load(topo, rd)),
        }
    FOCUS.write_text(json.dumps(blob, indent=1, ensure_ascii=False))
    size = FOCUS.stat().st_size / 1e6
    print(f"[emit] {FOCUS} ({size:.1f} MB); chosen = {chosen}")


def _rehydrate(rec: dict[str, Any]) -> dict[str, Any]:
    """`root_cause` reads a scheme-run dict; hand it back the fields it uses."""
    full = rec.get("full") or {}
    out = dict(full.get("counters") or {})
    out.update({"group": full.get("group"), "fairness": full.get("fairness"),
                "makespan": rec["makespan"],
                "board_fail_by_src": full.get("board_fail_by_src") or {},
                "board_by_core_dir": {}})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=("sweep", "confirm", "base"),
                    default="sweep")
    ap.add_argument("--jobs", type=int,
                    default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--schemes", nargs="*", default=list(SWEEP_SCHEMES))
    ap.add_argument("--top-k", type=int, default=2)
    ap.add_argument("--tiles", type=int, default=0,
                    help="override the stage's tile count")
    ap.add_argument("--emit", action="store_true",
                    help="build the report blob from the store and stop")
    ap.add_argument("--rank", action="store_true",
                    help="print the stage-1 ranking and stop")
    ap.add_argument("--store", default="",
                    help="use a different result store (for rehearsals)")
    ap.add_argument("--focus-out", default="",
                    help="write the report blob somewhere other than the "
                         "default")
    args = ap.parse_args()

    if args.store:
        use_store(args.store)
    if args.focus_out:
        use_focus(args.focus_out)
    store = load_store()
    if args.emit:
        emit(store, args.tiles or CONFIRM_TILES)
        return
    if args.rank:
        for s in args.schemes:
            print(f"\n{s}")
            for cfg, tot, per in rank(store, s, SWEEP_TILES):
                print(f"  {cfg:26s} sum={tot:7d}  "
                      + "  ".join(f"{op}={per[op]}" for op in OPS))
        return

    if args.stage == "sweep":
        tiles = args.tiles or SWEEP_TILES
        specs = sweep_specs(args.schemes, tiles)
    elif args.stage == "confirm":
        specs = confirm_specs(store, args.schemes, args.top_k,
                              args.tiles or CONFIRM_TILES)
    else:
        specs = base_specs(args.tiles or CONFIRM_TILES)
    run_all(specs, args.jobs, store)
    save_store(store)


if __name__ == "__main__":
    main()
