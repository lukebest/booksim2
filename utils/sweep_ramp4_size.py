#!/usr/bin/env python3
"""Two extra studies for the border short-arc report:

1. Down-ramp = 4 flit/cycle/node curve: same makespan-vs-AFIFO-depth sweep as
   sweep_afifo_depth, but the eject (down-ramp) bandwidth is forced to 4 for
   every size/direction.  Output: results/ramp4_afifo_depth_sweep.json (same
   structure as border_afifo_depth_sweep.json so gen_afifo_depth_curve can
   reuse line_chart/table_rows).

2. Message (data) size m = 1..5 flit: each src->dst delivery becomes an m-flit
   wormhole message (0 router buffer -> m consecutive cycles on every link and
   m eject cycles, capped at ramp_bw/cy/node).  We report the best makespan with
   the border AFIFO depth constrained to <= 5 FLITS, for ramp in {1, 2}.
   Reported mk = min(wormhole single-collective search, m × mk(m=1) replay bound).
   Output: results/msg_size_sweep.json
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import sched_ring_zerobuf as S
import sim_fused_rings as fr
from sweep_afifo_depth import CAPS, SIZES, collect_atomic, shape_cfg, sweep_config
from sweep_quad_ring_shapes import cfg_str, make_quads

ROOT = Path(__file__).resolve().parents[1]
RAMP4_OUT = ROOT / "results" / "ramp4_afifo_depth_sweep.json"
SIZE_OUT = ROOT / "results" / "msg_size_sweep.json"

RAMP4 = 4
FLIT_BYTES = 64
BUS_WIDTH_BYTES = 64
MSG_SIZES = tuple(range(1, 6))
SIZE_RAMPS = (1, 2)
SIZE_CROSS_LAT = 6       # border AFIFO link latency (cy); H=4, V=6
SIZE_CAP = 5             # border AFIFO depth constrained to <= 5 FLITS
# Atomic-cap pool for the size study: atomic runs paced at afifo_cap 0..5 yield
# the depth<=5 candidates; merged with the (rarely feasible) spread=0 schedule
# and filtered at depth<=SIZE_CAP.  Verified to reproduce
# border_afifo_depth_sweep cap=5 for m=1 on every size/direction.
SIZE_ATOMIC_CAPS = (0, 1, 2, 3, 4, 5)


def run_ramp4(sizes=SIZES, caps=CAPS):
    out = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "scheme": "border",
        "ramp_bw": RAMP4,
        "model": f"border short-arc, router_buf=0, per-link AFIFO cap, down-ramp={RAMP4}",
        "caps": list(caps),
        "configs": {},
    }
    t0 = time.time()
    for sz in sizes:
        for bidir, tag in ((False, "uni"), (True, "bi")):
            key = f"{sz}x{sz}_{tag}"
            print(f"== ramp4 {key} ==", flush=True)
            out["configs"][key] = sweep_config(sz, bidir, RAMP4, caps, "border")
    out["elapsed_s"] = time.time() - t0
    RAMP4_OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {RAMP4_OUT} ({out['elapsed_s']:.0f}s)")
    return out


def best_makespan_wormhole(sz, bidir, ramp_bw, quads, flits, cap=SIZE_CAP):
    """Min makespan from single-collective wormhole search (AFIFO depth <= cap)."""
    fr.cfg(sz, sz, 4, 6, cross=SIZE_CROSS_LAT)
    deliv = lambda s, b, q=quads: S.deliv_border_quads(s, b, q)
    cands = []
    for lb in (False, True):
        r = S.schedule(sz, bidir, ramp_bw, deliv, spread=0, quads=quads,
                       lb_cross=lb, flits=flits)
        if r.get("ok"):
            cands.append(r)
    for r in collect_atomic(sz, bidir, ramp_bw, quads, SIZE_ATOMIC_CAPS,
                            "border", flits=flits):
        cands.append(r)
    feas = [c for c in cands if c["afifo_depth"] <= cap]
    return min((c["makespan"] for c in feas), default=None)


def best_makespan(sz, bidir, ramp_bw, quads, flits, cap=SIZE_CAP, mk1=None):
    """Alias: wormhole-only search (use merge_replay_baseline for final mk)."""
    return best_makespan_wormhole(sz, bidir, ramp_bw, quads, flits, cap=cap)


def merge_replay_baseline(wormhole_mks, msg_sizes):
    """Apply m×mk(m=1) replay upper bound: m sequential m=1 allgathers.

    Returns (final_mks, replay_mks, methods) where methods[m] is 'wormhole' or
    'replay' depending on which bound is tighter.
    """
    if not wormhole_mks or wormhole_mks[0] is None:
        return wormhole_mks, [None] * len(msg_sizes), ["none"] * len(msg_sizes)
    mk1 = wormhole_mks[0]
    replay = [m * mk1 for m in msg_sizes]
    final, methods = [], []
    for wh, rp, m in zip(wormhole_mks, replay, msg_sizes):
        if wh is None:
            final.append(rp if m == 1 else None)
            methods.append("replay" if m > 1 else "none")
        elif m == 1 or wh <= rp:
            final.append(wh)
            methods.append("wormhole")
        else:
            final.append(rp)
            methods.append("replay")
    return final, replay, methods


def apply_config_baselines(cfg_entry, msg_sizes):
    """Add by_ramp_wormhole / by_ramp_replay / by_ramp_method; refresh by_ramp."""
    if "by_ramp_wormhole" not in cfg_entry:
        cfg_entry["by_ramp_wormhole"] = {
            rb: list(row) for rb, row in cfg_entry["by_ramp"].items()
        }
    for rb, wh in cfg_entry["by_ramp_wormhole"].items():
        final, replay, methods = merge_replay_baseline(wh, msg_sizes)
        cfg_entry.setdefault("by_ramp_replay", {})[rb] = replay
        cfg_entry.setdefault("by_ramp_method", {})[rb] = methods
        cfg_entry["by_ramp"][rb] = final
    return cfg_entry


def patch_size_json(path=SIZE_OUT):
    """Recompute replay baseline on existing wormhole sweep without re-simulating."""
    data = json.loads(path.read_text(encoding="utf-8"))
    msg_sizes = data["msg_sizes"]
    for cfg in data["configs"].values():
        apply_config_baselines(cfg, msg_sizes)
    data["model"] = (
        "border short-arc, router_buf=0, wormhole m-flit; AFIFO cap=5; "
        "reported mk = min(wormhole search, m×mk(m=1) replay)"
    )
    data["updated"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def run_size(sizes=SIZES, msg_sizes=MSG_SIZES, ramps=SIZE_RAMPS):
    out = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "scheme": "border",
        "model": ("border short-arc, router_buf=0, wormhole m-flit; AFIFO cap=5; "
                  "reported mk = min(wormhole search, m×mk(m=1) replay); "
                  f"cross_lat={SIZE_CROSS_LAT}, flit={FLIT_BYTES}B"),
        "flit_bytes": FLIT_BYTES,
        "bus_width_bytes": BUS_WIDTH_BYTES,
        "cross_lat": SIZE_CROSS_LAT,
        "msg_sizes": list(msg_sizes),
        "ramps": list(ramps),
        "cap": SIZE_CAP,
        "configs": {},
    }
    t0 = time.time()
    for sz in sizes:
        for bidir, tag in ((False, "uni"), (True, "bi")):
            key = f"{sz}x{sz}_{tag}"
            n = sz * sz
            cfg = shape_cfg(sz, "border", tag)
            quads = make_quads(cfg, sz)
            native = 2 if bidir else 1
            by_ramp = {}
            eject_lbs = {}
            by_wormhole, by_replay, by_method = {}, {}, {}
            for rb in ramps:
                wh = []
                for m in msg_sizes:
                    t1 = time.time()
                    mk = best_makespan_wormhole(sz, bidir, rb, quads, m)
                    wh.append(mk)
                    print(f"  {key} ramp={rb} m={m}: wormhole={mk} ({time.time()-t1:.1f}s)",
                          flush=True)
                final, replay, methods = merge_replay_baseline(wh, msg_sizes)
                rs = str(rb)
                by_wormhole[rs] = wh
                by_replay[rs] = replay
                by_method[rs] = methods
                by_ramp[rs] = final
                for m, f, w, r, meth in zip(msg_sizes, final, wh, replay, methods):
                    if meth == "replay":
                        print(f"    m={m}: corrected {w} -> {f} (replay {r})", flush=True)
                # eject lower bound for m-flit all-to-all: ceil((n-1)*m / ramp)
                eject_lbs[str(rb)] = [((n - 1) * m + rb - 1) // rb for m in msg_sizes]
            out["configs"][key] = {
                "size": sz,
                "bidir": bidir,
                "n": n,
                "native_ramp": native,
                "ring_shape": {"cfg": list(cfg), "cfg_str": cfg_str(cfg)},
                "by_ramp": by_ramp,
                "by_ramp_wormhole": by_wormhole,
                "by_ramp_replay": by_replay,
                "by_ramp_method": by_method,
                "eject_lb": eject_lbs,
            }
            SIZE_OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    out["elapsed_s"] = time.time() - t0
    SIZE_OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {SIZE_OUT} ({out['elapsed_s']:.0f}s)")
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", type=int, nargs="+", default=list(SIZES))
    ap.add_argument("--only", choices=("ramp4", "size", "both", "patch"), default="both")
    args = ap.parse_args()
    sizes = tuple(args.sizes)
    if args.only == "patch":
        data = patch_size_json()
        print(f"Patched {SIZE_OUT} ({len(data['configs'])} configs)")
        return
    if args.only in ("ramp4", "both"):
        run_ramp4(sizes)
    if args.only in ("size", "both"):
        run_size(sizes)


if __name__ == "__main__":
    main()
