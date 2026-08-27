#!/usr/bin/env python3
"""Do I-tag and E-tag, implemented as specified, change what S0 can reach?

The shipped fabric runs with both mechanisms dormant: the longest run of
consecutive failed boards is 41 cycles against `t_inj = 64`, and there are
almost no eject failures at all, so `n_itag_raised = n_etag_raised = 0`. That
makes S0's distance from R* impossible to blame on them -- but it also means
they have never been exercised, so their two behaviours are worth separating:

  * `itag_mode`: "broadcast" holds off every other injector on the tag's
    (plane, dir, VC) for as long as the starvation lasts. "reserve" is the
    specified behaviour -- the tag walks upstream to the node whose flit would
    have taken the slot, that node yields exactly one, the bubble is reserved
    for the requester, and the tag clears when the requester boards. One tag
    costs one slot instead of a whole starvation period, so the two should
    price very differently.
  * `t_xfer`: the specification E-tags a flit on its *first* failed eject, not
    after four, and gives it the leave port ahead of any normal flit.

Usage:
    PYTHONHASHSEED=0 python3 probe_ring2_tags.py [K] [group ...]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (BIN_W, FABRIC, M_REQ, M_RSP, W_FLITS,
                                  binned_jain, build_pattern, fairness_stats,
                                  run_scheme)
from rg_ring2_topo import (CHI_VCS_WRITE, Ring2Topology, write_bounds,
                           write_paths_for_txns)

OUT = Path(__file__).resolve().parents[1] / "results" / "probe_ring2_tags.json"

# Written before the runs. Do not edit after seeing results.
FORECAST = {
    "hypothesis": (
        "S0 距 R* 的 9.6% 缺口已被测定为「绑定链路上的空槽」，且其中在 rsp 热 hop 上"
        "88%、dat 热 hop 上 56% 是「本节点该向有 flit、但每 VC 单端口那一拍被另一个"
        "方向占用」——port_none = 0 说明没有一个空槽是仲裁/打标签失误造成的。"
        "I-tag 治的是「有 flit 但 hop 被 transit 占住」，不是「hop 空着但端口被占用」，"
        "所以按规定语义补全 I-tag 不应该显著抬高总带宽。"
        "但 reserve 模式一个 tag 只花一个 slot，而 broadcast 模式要封停整段直到饥饿"
        "解除，所以同一个 t_inj 下 reserve 的带宽损失应远小于 broadcast，"
        "公平性收益应当相当。"
    ),
    "predicted": {
        # thr delta vs the dormant baseline, at a t_inj low enough to fire
        "reserve_thr_delta_pct_at_t8": [-2.0, 0.5],
        "broadcast_thr_delta_pct_at_t8": [-25.0, -8.0],
        "reserve_beats_broadcast_on_thr": True,
        # Neither mode should push throughput above the dormant baseline by >1%.
        "either_mode_raises_thr_over_1pct": False,
        # E-tag at spec (t_xfer=1) is inert on this workload: ~0 eject failures.
        "t_xfer1_thr_delta_pct": [-0.3, 0.3],
    },
    "confidence": 0.7,
    "falsify": (
        "reserve 模式把总带宽抬过基线 1% 以上（那说明缺口里确实有 I-tag 能回收的部分，"
        "前面的归因错了），或 reserve 与 broadcast 的带宽代价没有区别"
        "（那说明「一个 tag 一个 slot」在这个 fabric 上不成立）"
    ),
}


def _groups() -> dict[str, list[dict[str, Any]]]:
    lo = (4, 8, 16, 32)
    return {
        # Baseline: both mechanisms dormant, as shipped.
        "base": [{}],
        # E-tag exactly as specified: tag on the first failed eject.
        "etag": [{"t_xfer": 1}, {"t_xfer": 1, "resv_ej": 0},
                 {"t_xfer": 1, "resv_ej": 4}],
        # I-tag: same thresholds, the two blocking semantics side by side.
        "itag_broadcast": [{"t_inj": v, "itag_mode": "broadcast"} for v in lo],
        "itag_segment": [{"t_inj": v, "itag_mode": "broadcast",
                          "itag_scope": "segment"} for v in lo],
        "itag_reserve": [{"t_inj": v, "itag_mode": "reserve"} for v in lo],
        # Both mechanisms at spec together, which is the configuration the
        # re-derived ceiling has to be judged on.
        "spec": [{"t_xfer": 1, "t_inj": v, "itag_mode": "reserve"}
                 for v in (2, 4, 8, 12, 16, 24, 32)],
    }


def run_one(topo, txns, over, *, k: int, r_star: float,
            seed: int = 0) -> dict[str, Any]:
    r = run_scheme("S0", topo, txns, seed=seed, cfg={**FABRIC, **over},
                   quiet=True)
    inj = r.get("wr_inject_by_core") or {}
    f = fairness_stats(inj, r["makespan"], k * W_FLITS)
    jb = binned_jain(inj, BIN_W, f["t_fair"]) if f else {}
    hu = r.get("hop_use") or {}
    dat = [v for kk, v in hu.items() if kk.endswith(":dat")]
    hot = max(dat, key=lambda v: v["n"]) if dat else {}
    return {
        "over": over, "thr": f.get("throughput"),
        "pct_r_star": round(100.0 * (f.get("throughput") or 0) / r_star, 2),
        "jain_bin": jb.get("jain_bin_mean"),
        "jain_bin_ratio": jb.get("jain_bin_ratio"),
        "max_min": f.get("max_min"),
        "hot_dat_util": hot.get("util"), "hot_dat_defl": hot.get("defl"),
        "n_itag": r.get("n_itag_raised"), "n_itag_yield": r.get("n_itag_yield"),
        "n_etag": r.get("n_etag_raised"), "n_defl": r.get("n_deflections"),
        "max_defl": r.get("max_deflections"),
        "starve_max": max((r.get("starve") or {}).values(), default=None),
        "wall": r.get("wall_secs"),
    }


def main() -> None:
    k = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    want = sys.argv[2:]
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE)
    txns = build_pattern("uniform", k=k, W=W_FLITS, seed=0)
    vp = write_paths_for_txns(topo, txns, strategy="least_occupied")
    b = write_bounds(topo, vp, m_req=M_REQ, m_rsp=M_RSP, m_wdata=W_FLITS,
                     merge_port_vcs=False)
    r_star = len(topo.cores) * k * W_FLITS / max(1, b["bound"])
    print(f"K={k} link_lb={b['link_lb']} port_lb={b['port_lb']} "
          f"R*={r_star:.4f}", flush=True)

    t0 = time.perf_counter()
    out: dict[str, Any] = {"k": k, "r_star": round(r_star, 4),
                           "link_lb": b["link_lb"], "port_lb": b["port_lb"],
                           "forecast": FORECAST, "groups": {}}
    base = None
    for name, cases in _groups().items():
        if want and name not in want and name != "base":
            continue
        print(f"\n[{name}]", flush=True)
        rows = []
        for over in cases:
            row = run_one(topo, txns, over, k=k, r_star=r_star)
            if base is None:
                base = row["thr"]
            row["thr_delta_pct"] = round(
                100.0 * (row["thr"] - base) / base, 2) if base else None
            rows.append(row)
            print(f"  {str(over) or '(dormant, as shipped)':<56} "
                  f"thr={row['thr']:<8} {row['pct_r_star']:>6}% R* "
                  f"({row['thr_delta_pct']:+.2f}%)  Jbin={row['jain_bin']}  "
                  f"itag={row['n_itag']}/{row['n_itag_yield']} "
                  f"etag={row['n_etag']} defl={row['n_defl']}", flush=True)
        out["groups"][name] = rows
    out["base_thr"] = base
    out["wall_secs"] = round(time.perf_counter() - t0, 1)
    OUT.write_text(json.dumps(out, indent=1))
    print(f"\nwrote {OUT}  {out['wall_secs']}s")


if __name__ == "__main__":
    main()
