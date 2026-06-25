#!/usr/bin/env python3
"""Resume msg_size_sweep from size_1to10.log after an interrupted run."""

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import sweep_ramp4_size as sw
from sweep_afifo_depth import shape_cfg
from sweep_quad_ring_shapes import cfg_str, make_quads

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "results" / "size_1to10.log"
OUT = ROOT / "results" / "msg_size_sweep.json"

PAT = re.compile(r"^\s+(\d+x\d+_(?:uni|bi)) ramp=(\d+) m=(\d+): mk=(\d+|None)")


def parse_log():
    done = {}
    for line in LOG.read_text(encoding="utf-8").splitlines():
        m = PAT.match(line)
        if not m:
            continue
        key, ramp, msg, mk = m.groups()
        done.setdefault(key, {}).setdefault(ramp, {})[int(msg)] = (
            None if mk == "None" else int(mk)
        )
    return done


def build_config(key, by_ramp, n, msg_sizes):
    sz = int(key.split("x")[0])
    bidir = key.endswith("_bi")
    tag = "bi" if bidir else "uni"
    cfg = shape_cfg(sz, "border", tag)
    quads = make_quads(cfg, sz)
    return {
        "size": sz,
        "bidir": bidir,
        "n": n,
        "native_ramp": 2 if bidir else 1,
        "ring_shape": {"cfg": list(cfg), "cfg_str": cfg_str(cfg)},
        "by_ramp": {r: [mks.get(m) for m in msg_sizes] for r, mks in by_ramp.items()},
        "eject_lb": {r: [((n - 1) * m + int(r) - 1) // int(r) for m in msg_sizes]
                     for r in by_ramp},
        "_quads": quads,
    }


def main():
    done = parse_log()
    msg_sizes = list(sw.MSG_SIZES)
    ramps = list(sw.SIZE_RAMPS)
    out = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "scheme": "border",
        "model": ("border short-arc, router_buf=0, wormhole m-flit messages, "
                  f"AFIFO cap={sw.SIZE_CAP}, cross_lat={sw.SIZE_CROSS_LAT}, "
                  f"flit={sw.FLIT_BYTES}B, bus={sw.BUS_WIDTH_BYTES}B"),
        "flit_bytes": sw.FLIT_BYTES,
        "bus_width_bytes": sw.BUS_WIDTH_BYTES,
        "cross_lat": sw.SIZE_CROSS_LAT,
        "msg_sizes": msg_sizes,
        "ramps": ramps,
        "cap": sw.SIZE_CAP,
        "configs": {},
    }
    t0 = time.time()
    for key in sorted(done):
        sz = int(key.split("x")[0])
        n = sz * sz
        cfg = build_config(key, done[key], n, msg_sizes)
        quads = cfg.pop("_quads")
        bidir = key.endswith("_bi")
        for rb in ramps:
            rs = str(rb)
            if rs not in cfg["by_ramp"]:
                cfg["by_ramp"][rs] = [None] * len(msg_sizes)
                cfg["eject_lb"][rs] = [
                    ((n - 1) * m + rb - 1) // rb for m in msg_sizes
                ]
            row = cfg["by_ramp"][rs]
            for i, m in enumerate(msg_sizes):
                if row[i] is not None:
                    continue
                t1 = time.time()
                mk = sw.best_makespan(sz, bidir, rb, quads, m)
                row[i] = mk
                print(f"  {key} ramp={rb} m={m}: mk={mk} ({time.time()-t1:.1f}s)",
                      flush=True)
        out["configs"][key] = cfg
        OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")

    # fill any missing keys (shouldn't happen)
    for sz in sw.SIZES:
        for bidir, tag in ((False, "uni"), (True, "bi")):
            key = f"{sz}x{sz}_{tag}"
            if key in out["configs"]:
                continue
            print(f"== full run {key} ==", flush=True)
            n = sz * sz
            cfg_shape = shape_cfg(sz, "border", tag)
            quads = make_quads(cfg_shape, sz)
            by_ramp, eject_lbs = {}, {}
            for rb in ramps:
                mks = []
                for m in msg_sizes:
                    t1 = time.time()
                    mk = sw.best_makespan(sz, bidir, rb, quads, m)
                    mks.append(mk)
                    print(f"  {key} ramp={rb} m={m}: mk={mk} ({time.time()-t1:.1f}s)",
                          flush=True)
                by_ramp[str(rb)] = mks
                eject_lbs[str(rb)] = [
                    ((n - 1) * m + rb - 1) // rb for m in msg_sizes
                ]
            out["configs"][key] = {
                "size": sz,
                "bidir": bidir,
                "n": n,
                "native_ramp": 2 if bidir else 1,
                "ring_shape": {"cfg": list(cfg_shape), "cfg_str": cfg_str(cfg_shape)},
                "by_ramp": by_ramp,
                "eject_lb": eject_lbs,
            }
            OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")

    out["elapsed_s"] = time.time() - t0
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {OUT} ({out['elapsed_s']:.0f}s)")


if __name__ == "__main__":
    main()
