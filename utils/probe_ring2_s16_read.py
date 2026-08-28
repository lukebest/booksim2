#!/usr/bin/env python3
"""S16's HA-local policy on reads: no DBID, no bus.

Write S16 withholds DBIDResp. ReadNoSnp has no such message -- the bulky
traffic is CompData the HA itself sends -- so the same policy (overcommit +
least-served) has to sit on the HA's CompData emit. That is a local completer
decision: zero new opcodes, zero flow-control bus.

This probe asks two things, on the same fabric the write study uses:

  * Does the read-side hook reproduce S0 when the budget is unbounded? If not,
    the hook perturbed the datapath and every number below is void.
  * On a fixed hot-read load (all CompData from HAs 11/13), does the policy
    beat S0 on bandwidth, fairness, or both -- and does putting the same
    decision on the 30-cycle bus (`grant_lat=30`) help or only tax?

Forecast, written before running:
  * Hot-read R* is 2.0 flit/cycle: two HA inject ports, DAT VC, 1 flit/cycle
    each. Equal-rate and max-total coincide, as they did for hot writes.
  * S0 should already sit close to that cap -- unlike hot writes, there is no
    E-tag circulation of *other people's* data through a full leave port; the
    HA inject is the scarce resource and S0 already owns it. So S16-read is
    unlikely to win much bandwidth. What it can win is *who* the port serves
    (least-served among cores whose REQs are queued).
  * `grant_lat=30` is a pure delay on a decision the HA already has the
    information for. It should lose bandwidth and not gain fairness. That is
    the measurement that answers "or use the out-of-band bus?"
  * Falsifier: S16-read beating S0 on bandwidth by more than a couple of
    percent would mean S0 was wasting the HA inject (queue HOL, E-tag at the
    cores) and the policy is load-bearing, not just a fairness reorder.

Usage:
    PYTHONHASHSEED=0 python3 probe_ring2_s16_read.py [K]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from collections import defaultdict

from dse_ring2_write_fair import (BIN_W, FABRIC, W_FLITS, binned_jain,
                                  fairness_stats, run_scheme)
from ideal_ring2_cc import coefficients, solve_max_total, solve_theta
import ideal_ring2_cc as _ideal
from rg_ring2_topo import (CHI_VCS_WRITE, Ring2Topology, build_hot_read,
                           build_uniform)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "probe_ring2_s16_read.json"
HOT_HAS = (11, 13)
# Match the write burst (128B / 64B) so a txn is the same DAT volume.
R_FLITS = W_FLITS
def read_ideal(topo: Ring2Topology, txns) -> dict:
    """Equal-rate / max-total bounds for this read mix (DAT is HA → core)."""
    cnt: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for t in txns:
        cnt[t.core][t.ha] += 1
    mix = {c: {h: v / sum(row.values()) for h, v in row.items()}
           for c, row in cnt.items()}
    old_m, old_r = dict(_ideal.MULT), set(_ideal.REVERSE)
    _ideal.MULT = {"req": 1, "dat": R_FLITS, "rsp": 0}
    _ideal.REVERSE = {"dat"}
    try:
        _, names, a = coefficients(topo, mix)
        lam_f = solve_theta(a, 1.0)
        lam_m = solve_max_total(a)
    finally:
        _ideal.MULT, _ideal.REVERSE = old_m, old_r
    load = a.T @ lam_f
    return {"r_fair": R_FLITS * float(lam_f.sum()),
            "r_max": R_FLITS * float(lam_m.sum()),
            "lam_star": float(lam_f.mean()),
            "binding": names[int(load.argmax())]}


def _times(r: dict) -> dict[int, list[int]]:
    return {int(c): v for c, v in (r.get("rd_inject_by_core") or {}).items()}


def one(name: str, scheme: str, topo, tx, cap: int, over: dict,
        r_star: float, s0: float | None) -> dict:
    cfg = dict(FABRIC)
    cfg.update(over)
    cfg["core_outstanding"] = cap
    r = run_scheme(scheme, topo, tx, cfg=cfg, quiet=True)
    inj = _times(r)
    fpc = max((len(v) for v in inj.values()), default=0)
    f = fairness_stats(inj, r["makespan"] or 1, fpc)
    jb = binned_jain(inj, BIN_W, f.get("t_fair") or 0)
    thr = f["throughput"]
    fc = r.get("fc") or {}
    row = {"name": name, "scheme": scheme, "cap": cap, "over": over,
           "thr": thr, "bw_vs_ideal": round(thr / r_star, 5) if r_star else None,
           "delta_vs_s0_pct": (None if s0 is None
                               else round(100 * (thr - s0) / s0, 2)),
           "jain_bin": jb.get("jain_bin_mean"),
           "jain_vs_ideal": jb.get("jain_vs_ideal"),
           "makespan": r["makespan"], "n_etag": r.get("n_etag_raised", 0),
           "n_grant_eager": fc.get("n_grant_eager"),
           "n_grant_paced": fc.get("n_grant_paced"),
           "n_grant_queued": fc.get("n_grant_queued"),
           "peak_grants": fc.get("peak_grants"),
           "bus_posts": fc.get("bus_posts", 0),
           "grant_delay_mean": fc.get("grant_delay_mean")}
    print(f"{name:<42}{thr:>9.4f}{thr / r_star:>8.4f}"
          f"{(0 if s0 is None else 100 * (thr - s0) / s0):>+8.2f}%"
          f"{(jb.get('jain_bin_mean') or 0):>9.5f}"
          f"{r.get('n_etag_raised', 0):>8}", flush=True)
    return row


def main() -> None:
    k = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE, route="latency")
    hot = build_hot_read(k=k, m_resp=R_FLITS, hot_has=HOT_HAS)
    uni = build_uniform(k=k, m_resp=R_FLITS, seed=0)

    idl_hot = read_ideal(topo, hot)
    idl_uni = read_ideal(topo, uni)
    print(f"K={k}  R={R_FLITS}  hot HAs={HOT_HAS}")
    print(f"  hot  R*={idl_hot['r_fair']:.4f}  Rmax={idl_hot['r_max']:.4f}  "
          f"bind={idl_hot['binding']}")
    print(f"  uni  R*={idl_uni['r_fair']:.4f}  Rmax={idl_uni['r_max']:.4f}  "
          f"bind={idl_uni['binding']}")
    print(f"{'scheme':<42}{'thr':>9}{'bw/R*':>8}{'vsS0':>9}{'Jbin':>9}{'etag':>8}")

    out = {"k": k, "r_flits": R_FLITS, "hot_has": list(HOT_HAS),
           "loads": {}}

    for load, tx, idl in (("hot", hot, idl_hot), ("uniform", uni, idl_uni)):
        rs = idl["r_fair"]
        print(f"\n=== {load}  R*={rs:.4f} ===")
        rows = []
        s0 = None
        for cap in (128, 32):
            tag = f" cap{cap}"
            r = one(f"S0{tag}", "S0", topo, tx, cap, {}, rs, s0)
            if s0 is None:
                s0 = r["thr"]
                r["delta_vs_s0_pct"] = 0.0
            rows.append(r)
            for oc in (8, 16, 32, 64):
                rows.append(one(f"S16 oc={oc}{tag}", "S16", topo, tx, cap,
                                {"overcommit": oc}, rs, s0))
            rows.append(one(f"S16 oc=inf{tag}", "S16", topo, tx, cap,
                            {"overcommit": 10 ** 9}, rs, s0))
            # The bus variant: same local policy, 30-cycle extra think time.
            rows.append(one(f"S16 oc=32 bus30{tag}", "S16", topo, tx, cap,
                            {"overcommit": 32, "grant_lat": 30}, rs, s0))
        out["loads"][load] = {"ideal": idl, "rows": rows}

    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
