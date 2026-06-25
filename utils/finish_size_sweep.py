#!/usr/bin/env python3
"""Finish 16x16_bi remaining msg-size points and write full JSON."""

import json
import re
import sys
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


def main():
    msg_sizes = list(sw.MSG_SIZES)
    ramps = list(sw.SIZE_RAMPS)
    done = parse_log()
    t0 = time.time()

    # only run missing 16x16_bi points
    key = "16x16_bi"
    sz, bidir, n = 16, True, 256
    cfg = shape_cfg(sz, "border", "bi")
    quads = make_quads(cfg, sz)
    br = done.setdefault(key, {})
    for rb in ramps:
        rs = str(rb)
        br.setdefault(rs, {})
        for m in msg_sizes:
            if br[rs].get(m) is not None:
                continue
            t1 = time.time()
            mk = sw.best_makespan(sz, bidir, rb, quads, m)
            br[rs][m] = mk
            line = f"  {key} ramp={rb} m={m}: mk={mk} ({time.time()-t1:.1f}s)"
            print(line, flush=True)
            with LOG.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

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
        "elapsed_s": 0,
    }

    for k in sorted(done):
        sz = int(k.split("x")[0])
        n = sz * sz
        bidir = k.endswith("_bi")
        tag = "bi" if bidir else "uni"
        rcfg = shape_cfg(sz, "border", tag)
        by_ramp, eject_lbs = {}, {}
        for rb in ramps:
            rs = str(rb)
            mks = done[k].get(rs, {})
            by_ramp[rs] = [mks.get(m) for m in msg_sizes]
            eject_lbs[rs] = [((n - 1) * m + rb - 1) // rb for m in msg_sizes]
        out["configs"][k] = {
            "size": sz,
            "bidir": bidir,
            "n": n,
            "native_ramp": 2 if bidir else 1,
            "ring_shape": {"cfg": list(rcfg), "cfg_str": cfg_str(rcfg)},
            "by_ramp": by_ramp,
            "eject_lb": eject_lbs,
        }

    out["elapsed_s"] = time.time() - t0
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {OUT}", flush=True)


if __name__ == "__main__":
    main()
