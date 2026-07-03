#!/usr/bin/env python3
"""Sweep driver for the SystemC cycle-level cross-reticle AFIFO CDC study.

Builds sc/mesh_tb (if needed), (re)generates the 4x4 per-link trace files, then
runs the testbench across a matrix of:

  scheme  : ring, hybrid           (which allgather plan)
  policy  : greedy, gated          (AFIFO read policy under study)
  sigma   : per-cycle jitter std-dev, in UI            {0, 0.05, 0.1, 0.2}
  sync    : Gray-pointer synchronizer depth, in cycles {2, 3}
  depth   : AFIFO capacity in flits                    {1..8}
  seed    : Monte-Carlo repeats (random per-domain phase + jitter stream)

For each (scheme, policy, sigma, sync) combination it also runs a
"required-depth" probe at very large depth (no writer stall possible) across
many seeds, giving the physical peak-occupancy distribution -- i.e. how deep
the AFIFO must actually be provisioned, and hence the buffer cost.

Output: results/sc_afifo_sweep.json
"""

import json
import os
import re
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
SC_DIR = os.path.join(ROOT, "sc")
RESULTS = os.path.join(ROOT, "results")
MESH_TB = os.path.join(SC_DIR, "mesh_tb")

SCHEMES = ("ring", "hybrid")
POLICIES = ("greedy", "gated")
SIGMAS = (0.0, 0.05, 0.1, 0.2)
SYNCS = (2, 3)
DEPTHS = tuple(range(1, 9))
MC_SEEDS = 20
LARGE_DEPTH = 64
DEPTH_PROBE_SEEDS = 30

SUMMARY_RE = re.compile(
    r"SUMMARY scheme=(?P<scheme>\S+) policy=(?P<policy>\S+) "
    r"sigma=(?P<sigma>[\d.]+) sync=(?P<sync>\d+) depth=(?P<depth>\d+) "
    r"seed=(?P<seed>\d+) ncross=(?P<ncross>\d+) total_writes=(?P<tw>\d+) "
    r"total_reads=(?P<tr>\d+) total_wstall=(?P<wst>\d+) "
    r"total_collisions=(?P<coll>\d+) peak_occ_max=(?P<pom>\d+) "
    r"peak_phys_occ_max=(?P<ppom>\d+) total_buf_flits=(?P<tbf>\d+) "
    r"last_delivered_max=(?P<ldm>-?\d+) mass_ok=(?P<mok>\d+) "
    r"makespan_trace=(?P<mk>\d+)")


def ensure_built():
    subprocess.run(["python3", os.path.join(HERE, "export_sc_trace.py"),
                    "--mx", "4", "--my", "4"], check=True, cwd=HERE)
    subprocess.run(["make", "-C", SC_DIR], check=True)


def trace_path(scheme):
    name = "sc_trace_ring_4x4.trace" if scheme == "ring" else "sc_trace_hybrid_4x4.trace"
    return os.path.join(RESULTS, name)


def run_once(scheme, policy, sigma, sync, depth, seed, extra=None):
    args = [MESH_TB, "--trace", trace_path(scheme), "--scheme", scheme,
            "--policy", policy, "--sigma", str(sigma), "--sync", str(sync),
            "--depth", str(depth), "--seed", str(seed)]
    if extra:
        args += extra
    out = subprocess.run(args, capture_output=True, text=True, check=True).stdout
    m = SUMMARY_RE.search(out)
    if not m:
        raise RuntimeError(f"could not parse mesh_tb output:\n{out}")
    d = m.groupdict()
    return {
        "ncross": int(d["ncross"]), "total_writes": int(d["tw"]),
        "total_reads": int(d["tr"]), "total_wstall": int(d["wst"]),
        "total_collisions": int(d["coll"]), "peak_occ_max": int(d["pom"]),
        "peak_phys_occ_max": int(d["ppom"]), "total_buf_flits": int(d["tbf"]),
        "last_delivered_max": int(d["ldm"]), "mass_ok": int(d["mok"]) == 1,
        "makespan_trace": int(d["mk"]),
    }


def depth_sweep(scheme, policy, sigma, sync, seeds=MC_SEEDS):
    rows = []
    for depth in DEPTHS:
        wstalls, collisions, mass_oks = [], [], []
        for seed in range(1, seeds + 1):
            r = run_once(scheme, policy, sigma, sync, depth, seed)
            wstalls.append(r["total_wstall"])
            collisions.append(r["total_collisions"])
            mass_oks.append(r["mass_ok"])
        rows.append({
            "depth": depth, "max_wstall": max(wstalls),
            "mean_wstall": sum(wstalls) / len(wstalls),
            "max_collisions": max(collisions),
            "mean_collisions": sum(collisions) / len(collisions),
            "all_mass_ok": all(mass_oks),
        })
    return rows


def required_depth_probe(scheme, policy, sigma, sync, seeds=DEPTH_PROBE_SEEDS):
    peaks = []
    buf_flits = []
    ncross = None
    for seed in range(101, 101 + seeds):
        r = run_once(scheme, policy, sigma, sync, LARGE_DEPTH, seed)
        peaks.append(r["peak_phys_occ_max"])
        buf_flits.append(r["total_buf_flits"])
        ncross = r["ncross"]
        assert r["total_wstall"] == 0, "large depth probe must never stall"
        assert r["mass_ok"], "mass conservation must hold"
    peaks_sorted = sorted(peaks)
    p95 = peaks_sorted[int(round(0.95 * (len(peaks_sorted) - 1)))]
    return {
        "sigma": sigma, "sync": sync, "seeds": seeds, "ncross": ncross,
        "max_depth": max(peaks), "p95_depth": p95,
        "mean_depth": sum(peaks) / len(peaks),
        "max_total_buf_flits": max(buf_flits),
        "mean_total_buf_flits": sum(buf_flits) / len(buf_flits),
    }


def main():
    ensure_built()
    payload = {"mx": 4, "my": 4, "schemes": {}}
    for scheme in SCHEMES:
        base = run_once(scheme, "greedy", 0.0, 2, LARGE_DEPTH, 1)
        sch = {"baseline_makespan": base["makespan_trace"],
               "n_cross_links": base["ncross"], "policies": {}}
        for policy in POLICIES:
            print(f"== {scheme} / {policy} ==")
            probes = {}
            sweeps = {}
            for sync in SYNCS:
                for sigma in SIGMAS:
                    key = f"S{sync}_sigma{sigma}"
                    print(f"   required-depth probe {key} ...")
                    probes[key] = required_depth_probe(scheme, policy, sigma, sync)
                # depth-vs-stall sweep at a representative jitter level
                sk = f"S{sync}_sigma0.1"
                print(f"   depth sweep {sk} ...")
                sweeps[sk] = depth_sweep(scheme, policy, 0.1, sync)
            sch["policies"][policy] = {
                "required_depth_probe": probes,
                "depth_sweep": sweeps,
            }
            p = probes["S2_sigma0.1"]
            print(f"   -> S2/sigma0.1: p95_depth={p['p95_depth']} "
                  f"max_depth={p['max_depth']} "
                  f"buf_flits(mean)={p['mean_total_buf_flits']:.1f}")
        payload["schemes"][scheme] = sch

    out_path = os.path.join(RESULTS, "sc_afifo_sweep.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
