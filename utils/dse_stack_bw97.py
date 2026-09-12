#!/usr/bin/env python3
"""Widen link / bridge / outstanding (not buffers) until S0 hits 97% of bound.

The original stacked-fabric S0 sits at 91.6% write / 86.8% read of the
composite lower bound. Those gaps are handshake bubbles and
turn/D2D/inject back-pressure, not a missing buffer. This sweep keeps
every FIFO depth exactly as in the published setup and only doubles
bandwidths the user named: outstanding, D2D, bridge boarding, and
optionally the other fabrics.

    stage 1  `--stage sweep`    every config, 1 tile, write and read
    stage 2  `--stage confirm`  survivors, 4 tiles
    --emit                      fold the 4-tile winner into the report blob

Results are keyed by `cfg|op|tiles` and merged on every run.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import time
from pathlib import Path
from typing import Any

from dse_stack_write_fair import (FABRIC, M_RSP, M_WDATA, die_board_table,
                                  run_scheme)
from rg_stack_topo import (StackTopology, build_tiled_read, build_tiled_write,
                           ha_histogram)

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "results" / "stack_bw97_sweep.json"
FOCUS = ROOT / "results" / "stack_bw97_focus.json"
TARGET = 0.97

OPS = ("write", "read")
SWEEP_TILES = 1
CONFIRM_TILES = 4

# Name -> knobs. FIFO depths are never in here.
GRID: dict[str, dict[str, Any]] = {
    "base": {},
    "oc256": dict(core_outstanding=256),
    "d2d2": dict(d2d_bw=2),
    "br2": dict(bridge_bw=2),
    "oc256-d2d2": dict(core_outstanding=256, d2d_bw=2),
    "oc256-d2d2-br2": dict(core_outstanding=256, d2d_bw=2, bridge_bw=2),
    "oc256-d2d2-br2-inj2": dict(core_outstanding=256, d2d_bw=2,
                                bridge_bw=2, inject_bw=2),
    "oc256-d2d2-br2-turn2": dict(core_outstanding=256, d2d_bw=2,
                                 bridge_bw=2, turn_bw=2),
    "oc256-d2d2-br2-h2": dict(core_outstanding=256, d2d_bw=2,
                              bridge_bw=2, h_bw=2),
    "oc256-d2d2-br2-h2-turn2": dict(core_outstanding=256, d2d_bw=2,
                                    bridge_bw=2, h_bw=2, turn_bw=2),
    "oc256-d2d2-br2-h2-v2": dict(core_outstanding=256, d2d_bw=2,
                                 bridge_bw=2, h_bw=2, v_bw=2),
    "oc256-all2": dict(core_outstanding=256, d2d_bw=2, bridge_bw=2,
                       h_bw=2, v_bw=2, top_bw=2, inject_bw=2, eject_bw=2),
    # Stay on the published h:dat bound and squeeze the leftover.
    "d2d2-br2-turn2": dict(d2d_bw=2, bridge_bw=2, turn_bw=2),
    "oc256-d2d2-br2-turn2-inj2": dict(core_outstanding=256, d2d_bw=2,
                                     bridge_bw=2, turn_bw=2, inject_bw=2),
    "oc256-d2d2-br2-turn4": dict(core_outstanding=256, d2d_bw=2,
                                bridge_bw=2, turn_bw=4),
    "oc256-d2d2-br2-turn2-inj2-ej2": dict(core_outstanding=256, d2d_bw=2,
                                         bridge_bw=2, turn_bw=2,
                                         inject_bw=2, eject_bw=2),
    "oc256-all2-turn2": dict(core_outstanding=256, d2d_bw=2, bridge_bw=2,
                             h_bw=2, v_bw=2, top_bw=2, inject_bw=2,
                             eject_bw=2, turn_bw=2),
    # Outstanding is the remaining tradeoff: 128 writes at 95.6% but
    # reads collapse to 86%; 256 keeps read ~95% and write ~93%.
    "oc160-d2d2-br2-turn2": dict(core_outstanding=160, d2d_bw=2,
                                 bridge_bw=2, turn_bw=2),
    "oc192-d2d2-br2-turn2": dict(core_outstanding=192, d2d_bw=2,
                                 bridge_bw=2, turn_bw=2),
    "oc224-d2d2-br2-turn2": dict(core_outstanding=224, d2d_bw=2,
                                 bridge_bw=2, turn_bw=2),
    "oc256-d2d4-br4-turn2": dict(core_outstanding=256, d2d_bw=4,
                                 bridge_bw=4, turn_bw=2),
    "oc224-d2d2-br2-turn4": dict(core_outstanding=224, d2d_bw=2,
                                 bridge_bw=2, turn_bw=4),
    "oc224-d2d2-br2-h2-turn2": dict(core_outstanding=224, d2d_bw=2,
                                    bridge_bw=2, h_bw=2, turn_bw=2),
    "d2d2-br2-h2-turn2": dict(d2d_bw=2, bridge_bw=2, h_bw=2, turn_bw=2),
    "oc192-d2d2-br2-turn4": dict(core_outstanding=192, d2d_bw=2,
                                 bridge_bw=2, turn_bw=4),
    "oc224-all2-turn2": dict(core_outstanding=224, d2d_bw=2, bridge_bw=2,
                             h_bw=2, v_bw=2, top_bw=2, inject_bw=2,
                             eject_bw=2, turn_bw=2),
}

# Prefer the cheapest combo that clears TARGET on both ops.
# Cost is "how many things we doubled", then outstanding last
# (it is free in wires, just a register width).
COST = {
    "base": 0,
    "oc256": 1,
    "d2d2": 2,
    "br2": 2,
    "oc256-d2d2": 3,
    "oc256-d2d2-br2": 4,
    "oc256-d2d2-br2-inj2": 5,
    "oc256-d2d2-br2-turn2": 5,
    "oc256-d2d2-br2-h2": 6,
    "oc256-d2d2-br2-h2-turn2": 7,
    "oc256-d2d2-br2-h2-v2": 7,
    "oc256-all2": 9,
    "d2d2-br2-turn2": 4,
    "oc256-d2d2-br2-turn2-inj2": 6,
    "oc256-d2d2-br2-turn4": 6,
    "oc256-d2d2-br2-turn2-inj2-ej2": 7,
    "oc256-all2-turn2": 10,
    "oc160-d2d2-br2-turn2": 5,
    "oc192-d2d2-br2-turn2": 5,
    "oc224-d2d2-br2-turn2": 5,
    "oc256-d2d4-br4-turn2": 7,
    "oc224-d2d2-br2-turn4": 6,
    "oc224-d2d2-br2-h2-turn2": 8,
    "d2d2-br2-h2-turn2": 7,
    "oc192-d2d2-br2-turn4": 6,
    "oc224-all2-turn2": 10,
}


def fab_bw_of(kw: dict[str, Any]) -> dict[str, int]:
    return {
        "top": int(kw.get("top_bw", 1)),
        "d2d": int(kw.get("d2d_bw", 1)),
        "h": int(kw.get("h_bw", 1)),
        "v": int(kw.get("v_bw", 1)),
    }


def job_key(cfg: str, op: str, tiles: int) -> str:
    return f"{cfg}|{op}|{tiles}"


_TOPO: StackTopology | None = None


def _txns(op: str, tiles: int):
    assert _TOPO is not None
    if op == "read":
        return build_tiled_read(_TOPO, n_tiles=tiles, seed=0)
    return build_tiled_write(_TOPO, n_tiles=tiles, seed=0)


def _light(r: dict[str, Any]) -> dict[str, Any]:
    g = r.get("group") or {}
    return {
        "makespan": r.get("makespan"),
        "completed": r.get("completed"),
        "n_txn_done": r.get("n_txn_done"),
        "stall": r.get("stall"),
        "retry": (r.get("retry") or {}).get("n_retry", r.get("retry", 0)),
        "deflections": r.get("n_deflections", 0),
        "board_fail": r.get("n_board_fail", 0),
        "goodput_total": g.get("goodput_total"),
        "finish_spread": g.get("max_min"),
        "group_finish": g.get("finish_by_group"),
        "max_core_outstanding": r.get("max_core_outstanding"),
        "n_d2d_stall": (r.get("fifo") or {}).get("n_d2d_stall"),
        "n_turn_board_fail": r.get("n_turn_board_fail"),
    }


def run_job(spec: tuple[str, str, int, bool]) -> tuple[str, dict[str, Any]]:
    cfg, op, tiles, full = spec
    kw = dict(GRID.get(cfg, {}))
    txns = _txns(op, tiles)
    hist = ha_histogram(_TOPO, txns)
    stall = max(80_000, 160 * hist["per_core_txn"])
    t0 = time.time()
    extra = dict(FABRIC)
    extra.update(kw)
    r = run_scheme(_TOPO, txns, "s0", route="bound", seed=0,
                   keep_trace=False, stall_after=stall, **extra)
    bound = _TOPO.write_bounds(
        txns, m_req=1, m_rsp=M_RSP, m_wdata=M_WDATA,
        fab_bw=fab_bw_of(kw), inject_bw=int(kw.get("inject_bw", 1)))
    mk = max(1, r["makespan"])
    rec = _light(r)
    rec.update({
        "cfg": cfg, "op": op, "tiles": tiles, "knobs": kw,
        "bounds": {k: bound[k] for k in (
            "link_lb", "port_lb", "cut_lb", "txn_lb", "bound",
            "link_by_vc", "fabric_lb", "fab_bw", "inject_bw")},
        "eff": round(bound["bound"] / mk, 4),
        "wall_s": round(time.time() - t0, 1),
    })
    if full:
        rec["full"] = {
            "group": r.get("group"),
            "done_series": r.get("done_series"),
            "bw_series": r.get("bw_series"),
            "fifo": r.get("fifo"),
            "board": die_board_table(_TOPO, r.get("board_by_core_dir") or {},
                                     die=0),
        }
    return job_key(cfg, op, tiles), rec


def _init():
    global _TOPO
    _TOPO = StackTopology(route_mode="bound")


def load_store() -> dict[str, Any]:
    if STORE.exists():
        return json.loads(STORE.read_text())
    return {"runs": {}, "meta": {}}


def save_store(store: dict[str, Any]) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(store, indent=1, ensure_ascii=False))


def run_all(specs: list[tuple], jobs: int, store: dict[str, Any]) -> None:
    todo = [s for s in specs if job_key(s[0], s[1], s[2]) not in store["runs"]]
    if not todo:
        print("[bw97] nothing to do")
        return
    print(f"[bw97] {len(todo)} jobs, {jobs} workers", flush=True)
    ctx = mp.get_context("fork")
    with ctx.Pool(processes=jobs, initializer=_init) as pool:
        for key, rec in pool.imap_unordered(run_job, todo):
            store["runs"][key] = rec
            save_store(store)
            print(f"  {key}  mk={rec['makespan']}  "
                  f"eff={rec['eff']:.3f}  bound={rec['bounds']['bound']}  "
                  f"{rec['wall_s']}s", flush=True)


def sweep_specs() -> list[tuple]:
    return [(c, op, SWEEP_TILES, False) for c in GRID for op in OPS]


def pick_winner(store: dict[str, Any], tiles: int) -> str | None:
    """Cheapest config whose write AND read efficiency are >= TARGET."""
    hits: list[tuple[int, str]] = []
    for cfg in GRID:
        recs = [store["runs"].get(job_key(cfg, op, tiles)) for op in OPS]
        if not all(recs) or not all(r.get("completed") for r in recs):
            continue
        if all(float(r["eff"]) >= TARGET for r in recs):
            hits.append((COST[cfg], cfg))
    if not hits:
        return None
    hits.sort()
    return hits[0][1]


def best_so_far(store: dict[str, Any], tiles: int) -> str:
    """If nobody cleared TARGET, the one with the worst-op efficiency maxed."""
    scored: list[tuple[float, int, str]] = []
    for cfg in GRID:
        recs = [store["runs"].get(job_key(cfg, op, tiles)) for op in OPS]
        if not all(recs) or not all(r.get("completed") for r in recs):
            continue
        lo = min(float(r["eff"]) for r in recs)
        scored.append((lo, -COST[cfg], cfg))
    if not scored:
        return "oc256-d2d2-br2"
    scored.sort(reverse=True)
    return scored[0][2]


def confirm_specs(store: dict[str, Any]) -> list[tuple]:
    """4-tile is the real 97% test.

    1-tile write cannot reach 97% of an *h:dat* bound: the CHI handshake
    is ~400 cycle against a 7,712-cycle floor. 4-tile amortises that to
    ~1.3% and leaves the back-pressure the 1-tile D2D/bridge knobs never
    saw. Confirm in cost order, skipping rows already in the store.

    Width-1 + D2D/bridge/oc keeps the published bound. H×2 is the only
    1-tile knob that moved write makespan, but sigma=1 means the dest
    hop then wants two flits/cycle while the H↔V tap still drains one
    -- so H×2 is paired with turn×2 before H+V / all-2.
    """
    order = [
        "oc256-d2d2-br2",
        "oc256-d2d2-br2-h2",
        "oc256-d2d2-br2-h2-turn2",
        "oc256-d2d2-br2-h2-v2",
        "oc256-all2",
        "oc256-d2d2-br2-turn2",
        "d2d2-br2-turn2",
        "oc256-d2d2-br2-turn2-inj2",
        "oc256-d2d2-br2-turn4",
        "oc256-d2d2-br2-turn2-inj2-ej2",
        "oc256-all2-turn2",
        "oc160-d2d2-br2-turn2",
        "oc192-d2d2-br2-turn2",
        "oc224-d2d2-br2-turn2",
        "oc256-d2d4-br4-turn2",
        "oc224-d2d2-br2-turn4",
        "oc224-d2d2-br2-h2-turn2",
        "d2d2-br2-h2-turn2",
        "oc192-d2d2-br2-turn4",
        "oc224-all2-turn2",
    ]
    return [(c, op, CONFIRM_TILES, True) for c in order for op in OPS
            if job_key(c, op, CONFIRM_TILES) not in store["runs"]]


def _seed_base_from_cc_focus(store: dict[str, Any]) -> None:
    """Copy the published 4-tile S0 so §7 can sit next to §0 without a rerun."""
    src = ROOT / "results" / "stack_cc_focus.json"
    if not src.exists():
        return
    old = json.loads(src.read_text())
    for op in OPS:
        key = job_key("base", op, CONFIRM_TILES)
        if key in store["runs"]:
            continue
        light = (old.get("confirm") or {}).get(f"s0|base|{op}|4") or {}
        fullrec = ((old.get("schemes") or {}).get(op) or {}).get("s0") or {}
        rec = dict(light)
        if fullrec.get("full") and not rec.get("full"):
            rec["full"] = fullrec["full"]
        if not rec.get("makespan") and fullrec.get("makespan"):
            rec.update({k: fullrec[k] for k in (
                "makespan", "completed", "n_txn_done", "goodput_total",
                "group_finish", "finish_spread", "eff") if k in fullrec})
        if not rec.get("makespan"):
            continue
        bd = ((rec.get("full") or {}).get("bounds")
              or fullrec.get("bounds") or {})
        mk = max(1, int(rec.get("makespan") or 0))
        bound = int(bd.get("bound") or 0)
        store["runs"][key] = {
            "cfg": "base", "op": op, "tiles": CONFIRM_TILES, "knobs": {},
            "makespan": rec.get("makespan"),
            "completed": rec.get("completed", True),
            "n_txn_done": rec.get("n_txn_done"),
            "retry": rec.get("retry"),
            "goodput_total": rec.get("goodput_total"),
            "group_finish": rec.get("group_finish"),
            "finish_spread": rec.get("finish_spread"),
            "bounds": {k: bd.get(k) for k in (
                "link_lb", "port_lb", "cut_lb", "txn_lb", "bound",
                "link_by_vc", "fabric_lb")} | {
                    "fab_bw": {"top": 1, "d2d": 1, "h": 1, "v": 1},
                    "inject_bw": 1},
            "eff": round(bound / mk, 4) if bound else rec.get("eff"),
            "full": rec.get("full"),
            "seeded_from": "stack_cc_focus.json",
        }


def emit(store: dict[str, Any]) -> None:
    _seed_base_from_cc_focus(store)
    # Do not crown the published S0 as the widened winner just because
    # it is the only 4-tile row we have copied in.
    widened = [c for c in GRID if c != "base"
               and all(job_key(c, op, CONFIRM_TILES) in store["runs"]
                       for op in OPS)]
    win = None
    if widened:
        win = pick_winner(store, CONFIRM_TILES)
        if not win or win == "base":
            scored = []
            for c in widened:
                recs = [store["runs"][job_key(c, op, CONFIRM_TILES)]
                        for op in OPS]
                scored.append((min(float(r["eff"]) for r in recs),
                               -COST[c], c))
            scored.sort(reverse=True)
            win = scored[0][2]
    if not win:
        win = pick_winner(store, SWEEP_TILES) or best_so_far(store, SWEEP_TILES)
    blob: dict[str, Any] = {
        "meta": {
            "target": TARGET,
            "winner": win,
            "winner_knobs": dict(GRID.get(win, {})),
            "tiles": CONFIRM_TILES,
            "sweep_tiles": SWEEP_TILES,
            "note": "FIFO depths unchanged; only outstanding / link / "
                    "bridge / turn / inject widths move.",
        },
        "grid": {c: dict(k) for c, k in GRID.items()},
        "sweep": {},
        "confirm": {},
    }
    for key, rec in store["runs"].items():
        cfg, op, tiles = key.split("|")
        tiles = int(tiles)
        slot = "confirm" if tiles == CONFIRM_TILES else "sweep"
        blob[slot].setdefault(cfg, {})[op] = rec
    FOCUS.write_text(json.dumps(blob, indent=1, ensure_ascii=False))
    print(f"wrote {FOCUS}  winner={win}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=("sweep", "confirm", "all", "emit"),
                    default="all")
    ap.add_argument("--jobs", type=int, default=max(1, os.cpu_count() or 2))
    args = ap.parse_args()
    store = load_store()
    store.setdefault("meta", {})["target"] = TARGET
    if args.stage in ("sweep", "all"):
        run_all(sweep_specs(), args.jobs, store)
        w = pick_winner(store, SWEEP_TILES)
        pick = w or best_so_far(store, SWEEP_TILES)
        tag = "" if w else " (best-so-far, under target)"
        print(f"[bw97] 1-tile winner: {pick}{tag}")
    if args.stage in ("confirm", "all"):
        run_all(confirm_specs(store), args.jobs, store)
        w = pick_winner(store, CONFIRM_TILES)
        pick = w or best_so_far(store, CONFIRM_TILES)
        tag = "" if w else " (best-so-far, under target)"
        print(f"[bw97] 4-tile winner: {pick}{tag}")
    if args.stage in ("emit", "all"):
        emit(store)


if __name__ == "__main__":
    main()
