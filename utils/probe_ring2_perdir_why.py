#!/usr/bin/env python3
"""Why per-direction ports *lose* throughput, against the obvious prediction.

Splitting the up-ring port by direction should only add injection capacity, so
throughput should not fall. At the study's K it falls hard: 91.3% -> 76.1% of
R*. Two measurements in the probe say the cause is not the ring geometry:

  * the busiest RSP hop carries 90543 flits where the routing table assigns it
    70000, and only 275 of the excess are extra laps;
  * the busiest REQ hop carries 45134 against an assigned 35000.

`choose_dir` is deterministic (latency, then hops, then CW), so extra crossings
cannot come from different routing. They have to be *extra transactions*, and
the only source of those in this model is the completer's request tracker: when
it is full the HA answers RetryAck and the core re-sends, which puts another REQ
and another RSP on the ring.

Hypothesis: per-direction ports inject faster, the 256-entry tracker overruns,
and the resulting retry traffic loads the RSP link -- so the binding resource
stops being the ring and becomes the completer.

This tests it the direct way: sweep `ha_track` on both port structures. If the
hypothesis holds, a large enough tracker makes per-direction ports match or beat
the shared port, and the excess over the assigned load goes to zero.

Forecast, written before this ran: at ha_track >= 1024 the per-direction fabric
recovers to >= 91% of R* and its RSP hop load falls to ~70000; on the shared
port the tracker barely matters, because injection is slow enough that the
tracker never fills (retries/txn already ~0 there). If instead per-direction
ports stay below the shared port even with an unbounded tracker, the loss is
structural and the retry story is wrong.

Result note, added after running -- the forecast holds, but the first run of
this probe did not test it. It was run at K=3000, which is *below the
saturation onset*: there `max_ha_used` stays under 256, no RetryAck is ever
sent, and sweeping the tracker changes nothing, which reads as a falsification
but is really an underpowered test. At K=6000 the same sweep shows the effect
plainly -- track=256 gives n_retry=2781 with `max_ha_used` pinned at 256 and
8343 extra flits delivered (2781 extra transactions x 1 REQ + 2 RSP), while
track=4096 gives n_retry=0 and a shorter makespan. The default K here is
therefore the study's own K, and the small-K rows are kept so the onset is
visible rather than implied.

Usage:
    PYTHONHASHSEED=0 python3 probe_ring2_perdir_why.py [K]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (BIN_W, FABRIC, K_PER_CORE, M_REQ, M_RSP,
                                  W_FLITS, binned_jain, build_pattern,
                                  fairness_stats, run_scheme)
from rg_ring2_topo import (CHI_VCS_WRITE, Ring2Topology, write_bounds,
                           write_paths_for_txns)

OUT = (Path(__file__).resolve().parents[1] / "results"
       / "probe_ring2_perdir_why.json")

TRACKS = (256, 512, 1024, 4096)


def _hot(r: dict[str, Any], vc: str) -> dict[str, Any]:
    best = None
    for key, v in (r.get("hop_use") or {}).items():
        if key.rsplit(":", 1)[-1] != vc:
            continue
        if best is None or v["n"] > best[1]["n"]:
            best = (key, v)
    return {"hop": best[0], "n": best[1]["n"], "util": best[1]["util"],
            "defl": best[1]["defl"]} if best else {}


def main() -> None:
    k = int(sys.argv[1]) if len(sys.argv) > 1 else K_PER_CORE
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE, route="latency")
    tx = build_pattern("uniform", k=k, W=W_FLITS, seed=0)
    vp = write_paths_for_txns(topo, tx, strategy="least_occupied")
    b = write_bounds(topo, vp, m_req=M_REQ, m_rsp=M_RSP, m_wdata=W_FLITS,
                     merge_port_vcs=False)
    r_star = len(topo.cores) * k * W_FLITS / max(1, b["bound"])
    assigned = b["link_by_vc"]
    print(f"K={k}  R*={r_star:.4f}  assigned rsp={assigned['rsp']} "
          f"req={assigned['req']} dat={assigned['dat']}\n", flush=True)

    rows = []
    t0 = time.time()
    for per_dir in (False, True):
        for track in TRACKS:
            cfg = {**FABRIC, "per_dir_ports": per_dir, "ha_track": track}
            r = run_scheme("S0", topo, tx, seed=0, cfg=cfg, quiet=True)
            inj = r["wr_inject_by_core"]
            f = fairness_stats(inj, r["makespan"], k * W_FLITS)
            jb = binned_jain(inj, BIN_W, f["t_fair"])
            thr = len(tx) * W_FLITS / max(1, r["makespan"])
            hot_rsp, hot_req = _hot(r, "rsp"), _hot(r, "req")
            retry = r.get("retry") or {}
            n_retry = retry.get("n_retry", 0)
            row = {
                "per_dir_ports": per_dir, "ha_track": track,
                "thr": round(thr, 4), "pct_r_star": round(100 * thr / r_star, 2),
                "jain_bin": round(jb["jain_bin_mean"], 5),
                "max_min": round(f["max_min"], 4),
                "makespan": r["makespan"], "n_retry": n_retry,
                "retry_per_txn": retry.get("retry_per_txn"),
                # `max_ha_used` pinned at `ha_track` is the saturation witness,
                # and `completed` guards against reading a truncated run.
                "max_ha_used": retry.get("max_ha_used"),
                "tracker_saturated": retry.get("max_ha_used") == track,
                "completed": bool(r.get("completed")),
                "n_deflections": r.get("n_deflections"),
                "n_delivered_flits": r.get("n_delivered_flits"),
                "flits_expected": len(tx) * (M_REQ + M_RSP + W_FLITS),
                "hot_rsp": hot_rsp, "hot_req": hot_req,
                "rsp_excess": hot_rsp.get("n", 0) - assigned["rsp"],
                "req_excess": hot_req.get("n", 0) - assigned["req"],
                "n_board_fail": r.get("n_board_fail"),
            }
            rows.append(row)
            print(f"  per_dir={int(per_dir)} track={track:<5} "
                  f"thr={thr:.4f} ({row['pct_r_star']:.2f}% R*) "
                  f"Jbin={row['jain_bin']:.5f} mm={row['max_min']:.4f}  "
                  f"retry={n_retry} ha_used={row['max_ha_used']}"
                  f"{'(SAT)' if row['tracker_saturated'] else ''} "
                  f"defl={row['n_deflections']}  "
                  f"flits={row['n_delivered_flits']}/{row['flits_expected']}  "
                  f"rsp_util={hot_rsp.get('util')}"
                  f"{'' if row['completed'] else '  !! INCOMPLETE'}",
                  flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(
        {"k": k, "r_star": round(r_star, 4), "assigned": assigned,
         "tracks": list(TRACKS), "rows": rows,
         "wall_secs": round(time.time() - t0, 1)}, indent=2))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
