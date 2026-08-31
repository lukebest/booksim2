#!/usr/bin/env python3
"""Price each cause of S0's remaining 4.31% gap to R*, one intervention each.

`probe_ring2_ceiling_gap.py` split the gap exactly:

    makespan 73152 = 70000 assigned floor
                   +  1056 extra crossings of the binding RSP hop (extra laps)
                   +  2096 idle cycles on that hop

and attributed the idle in full (`other` = 0): 66.5% `dry` (the HA had nothing
queued for that direction), 29.4% `itag` (an I-tag reservation refused the
board), 2.6% `raced`, 1.5% `hol`.

Three candidate causes fall out, and each has an intervention that isolates it:

  * **Down-ring drain.** Up-ring got two ports per VC on this fabric, down-ring
    still retires 1 flit/cycle/node, so RSP flits fail to eject, take an E-tag
    and ride a full extra lap -- which is exactly the surcharge, since the
    measured extra crossings are an exact multiple of 20. `eject_depth` 64 and
    the rule-breaking `eject_bw` 2 bracket what removing it is worth.
  * **I-tag on the binding link.** A yielded slot on the bottleneck hop is a
    slot the bottleneck never gets back. `t_inj` = 10**9 makes I-tag dormant.
  * **Supply.** The write handshake is a serial four-phase chain, so an HA can
    only have a DBIDResp/Comp ready once a REQ has landed. If that is what
    `dry` means, raising `core_outstanding` should fill those slots.

Forecast before running: the eject interventions pay the most (the surcharge is
measured, not inferred); I-tag dormancy pays about 0.8%; `core_outstanding` pays
close to nothing, because 128 outstanding per core against a 20-node ring is
already far more than the pipeline needs -- if it *does* pay, `dry` is a
provisioning artifact rather than a dependency limit.

Re-measured at `core_outstanding` 32, the numbers above no longer describe the
run: the surcharge collapses from 1056 to 5 and deflections from 2306 to 16, so
the down-ring drain is no longer a cause at all and the eject interventions buy
nothing. The gap is 3.03%, entirely idle, split `itag` 49.1% / `dry` 42.0% /
`raced` 8.6% / `hol` 0.2%. I-tag dormancy, which used to lose on both axes, is
now a plain bandwidth-for-fairness trade.

Usage:
    PYTHONHASHSEED=0 python3 probe_ring2_ceiling_fix.py [K]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (BIN_W, FABRIC, K_PER_CORE, M_REQ, M_RSP,
                                  W_FLITS, binned_jain, build_pattern,
                                  fairness_stats, make_sim)
from rg_ring2_topo import (CHI_VCS_WRITE, Ring2Topology, write_bounds,
                           write_paths_for_txns)

OUT = (Path(__file__).resolve().parents[1] / "results"
       / "probe_ring2_ceiling_fix.json")

CASES = [
    ("现状（shipped）", {}),
    ("I-tag 休眠 t_inj=1e9", {"t_inj": 10 ** 9}),
    ("I-tag 更弱 t_inj=2", {"t_inj": 2}),
    ("I-tag 更强 t_inj=8", {"t_inj": 8}),
    ("下环 eject 32", {"eject_depth": 32}),
    ("下环 eject 64", {"eject_depth": 64}),
    ("下环 eject 64 + I-tag 休眠", {"eject_depth": 64, "t_inj": 10 ** 9}),
    ("core_outstanding 48", {"core_outstanding": 48}),
    ("core_outstanding 64", {"core_outstanding": 64}),
    ("core_outstanding 256", {"core_outstanding": 256}),
    ("eject_bw 2（破规则，上界）", {"eject_bw": 2}),
    ("eject_bw 2 + I-tag 休眠（破规则，上界）",
     {"eject_bw": 2, "t_inj": 10 ** 9}),
]


def main() -> None:
    k = int(sys.argv[1]) if len(sys.argv) > 1 else K_PER_CORE
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE, route="latency")
    tx = build_pattern("uniform", k=k, W=W_FLITS, seed=0)
    total = len(topo.cores) * k * W_FLITS
    vp = write_paths_for_txns(topo, tx, strategy="least_occupied")
    b = write_bounds(topo, vp, m_req=M_REQ, m_rsp=M_RSP, m_wdata=W_FLITS,
                     merge_port_vcs=False)
    floor = b["bound"]
    r_star = total / floor
    print(f"K={k}  R*={r_star:.4f}  assigned floor={floor}\n", flush=True)

    rows = []
    for name, over in CASES:
        cfg = dict(FABRIC)
        cfg.update(over)
        sim = make_sim("S0", topo, seed=0, cfg=cfg)
        sim.offer_batch(tx)
        while sim.t < 5_000_000 and not sim.done():
            sim.step()
        s = sim.summary()
        mk = s["makespan"]
        thr = total / mk
        inj = {int(c): v for c, v in (s.get("wr_inject_by_core") or {}).items()}
        fs = fairness_stats(inj, mk or 1, k * W_FLITS)
        jb = binned_jain(inj, BIN_W, fs.get("t_fair") or 0)
        hot = {}
        for (_pl, _d, _n, vc), n in sim.hop_use.items():
            if n > hot.get(vc, (0, ""))[0]:
                hot[vc] = (n, f"{_n}:{_d}")
        rows.append({
            "case": name, "over": over, "makespan": mk, "thr": round(thr, 4),
            "pct_rstar": round(100 * thr / r_star, 2),
            "jbin": jb, "mm": fs.get("maxmin"),
            "defl": s["n_deflections"], "etag": s.get("n_etag"),
            "hot_rsp": hot.get("rsp", (0, ""))[0],
            "hot_dat": hot.get("dat", (0, ""))[0],
            "rsp_surcharge": hot.get("rsp", (0, ""))[0] - b["link_by_vc"]["rsp"],
        })
        r = rows[-1]
        print(f"  {name:<38} thr={thr:.4f} ({r['pct_rstar']:.2f}% R*) "
              f"Jbin={jb['jain_bin_mean']} defl={r['defl']} "
              f"hot_rsp={r['hot_rsp']} surcharge={r['rsp_surcharge']:+.0f} "
              f"idle={mk - r['hot_rsp']}",
              flush=True)

    OUT.write_text(json.dumps({"k": k, "r_star": round(r_star, 4),
                               "rows": rows}, indent=2))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
