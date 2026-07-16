#!/usr/bin/env python3
"""Exact feasibility search for col_comb3 cyclic-replay II.

Proves the minimal II under link all-different + up-ramp capacity +
down-ramp queue-depth=0 (drain = RAMP_BW) constraints by:
  1) analytic lower bound (max link multiplicity),
  2) descending exhaustive bitset backtrack from the known greedy II,
  3) recording the first infeasible II as a hard lower bound.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict

import sched_zerobuf_compare as S
from dse_m1_tree_uarch import (
    MX, MY, H, V, N, RAMP, RAMP_BW, OUT, col_comb_tree, _queue_peak,
)
from dse_tree_allgather_6x8 import footprint, validate_tree


def build_fps():
    fps = {}
    for s in range(N):
        edges = col_comb_tree(s)
        chk = validate_tree(s, edges)
        assert chk["ok"], chk
        fps[s] = footprint(s, edges, chk)
    return fps


def prep(fps, ii: int, buffer_depth: int = 0):
    """Precompute per-source constraint masks for a fixed II."""
    # links: list of (key, [(src, rel), ...])
    link_users = defaultdict(list)
    up_users = defaultdict(list)
    down_users = defaultdict(list)
    for s, slots in fps.items():
        for kind, key, rel in slots:
            if kind == "L":
                link_users[key].append((s, rel % ii))
            elif kind == "U":
                up_users[key].append((s, rel % ii))
            else:
                down_users[key].append((s, rel % ii))

    # Per source: list of (resource_id, residue_offset) for LINKS only;
    # resource shares a global residue-occupancy bitset of size II.
    link_ids = {k: i for i, k in enumerate(link_users)}
    n_links = len(link_ids)

    # Each source's link placements: list of (link_id, rel_mod)
    src_links = [[] for _ in range(N)]
    for key, users in link_users.items():
        lid = link_ids[key]
        for s, r in users:
            src_links[s].append((lid, r))

    # Up: per-node fold; capacity RAMP_BW. Represent occupancy as count array.
    up_ids = {k: i for i, k in enumerate(up_users)}
    src_ups = [[] for _ in range(N)]
    for key, users in up_users.items():
        uid = up_ids[key]
        for s, r in users:
            src_ups[s].append((uid, r))

    down_ids = {k: i for i, k in enumerate(down_users)}
    src_downs = [[] for _ in range(N)]
    for key, users in down_users.items():
        did = down_ids[key]
        for s, r in users:
            src_downs[s].append((did, r))

    mult_lb = max((len(u) for u in link_users.values()), default=0)
    return {
        "ii": ii,
        "n_links": n_links,
        "src_links": src_links,
        "n_up": len(up_ids),
        "src_ups": src_ups,
        "n_down": len(down_ids),
        "src_downs": src_downs,
        "buffer_depth": buffer_depth,
        "mult_lb": mult_lb,
        # MRV order: densest sources first
        "order": sorted(range(N),
                        key=lambda s: -len(src_links[s])),
    }


def feasible(P, time_limit_s: float = 30.0) -> tuple[bool, str]:
    """Bitset backtrack. Returns (ok, reason)."""
    ii = P["ii"]
    if P["mult_lb"] > ii:
        return False, f"multiplicity {P['mult_lb']} > II"
    # Link occupancy: list of bitsets (as int, II<=128 fits in int)
    assert ii <= 128
    link_occ = [0] * P["n_links"]
    up_cnt = [[0] * ii for _ in range(P["n_up"])]
    down_fold = [[0] * ii for _ in range(P["n_down"])]
    buf = P["buffer_depth"]
    order = P["order"]
    src_links = P["src_links"]
    src_ups = P["src_ups"]
    src_downs = P["src_downs"]
    t0 = time.time()
    nodes = 0
    offs = [-1] * N

    def down_ok(did: int) -> bool:
        return _queue_peak(down_fold[did]) <= buf

    def place(k: int) -> bool:
        nonlocal nodes
        if time.time() - t0 > time_limit_s:
            raise TimeoutError(f"nodes={nodes}")
        if k == N:
            return True
        s = order[k]
        links = src_links[s]
        ups = src_ups[s]
        downs = src_downs[s]
        # candidate mask: intersection of free residues across this source's links
        # For offset o, residue used on link lid is (o + rel) % ii, must be free.
        # Start with all-ones mask of width ii.
        cand = (1 << ii) - 1
        for lid, rel in links:
            occ = link_occ[lid]
            # o is forbidden if bit (o+rel)%ii is set in occ
            # free_o_bit[o] = not occ[(o+rel)%ii]
            # rotate occ right by rel: free = ~rot_right(occ, rel) & mask
            # rot_right: bits move to lower indices
            rot = ((occ >> rel) | (occ << (ii - rel))) & ((1 << ii) - 1)
            cand &= ~rot
            if cand == 0:
                return False
        # iterate set bits in cand
        c = cand
        while c:
            lsb = c & -c
            o = lsb.bit_length() - 1
            c ^= lsb
            nodes += 1
            # try up
            up_ok = True
            for uid, rel in ups:
                r = (o + rel) % ii
                if up_cnt[uid][r] >= RAMP_BW:
                    up_ok = False
                    break
            if not up_ok:
                continue
            # try down (apply then check peak)
            down_applied = []
            d_ok = True
            for did, rel in downs:
                r = (o + rel) % ii
                down_fold[did][r] += 1
                down_applied.append((did, r))
                if not down_ok(did):
                    d_ok = False
                    break
            if not d_ok:
                for did, r in down_applied:
                    down_fold[did][r] -= 1
                continue
            # commit links + up
            for lid, rel in links:
                link_occ[lid] |= 1 << ((o + rel) % ii)
            for uid, rel in ups:
                up_cnt[uid][(o + rel) % ii] += 1
            offs[s] = o
            if place(k + 1):
                return True
            offs[s] = -1
            for uid, rel in ups:
                up_cnt[uid][(o + rel) % ii] -= 1
            for lid, rel in links:
                link_occ[lid] &= ~(1 << ((o + rel) % ii))
            for did, r in down_applied:
                down_fold[did][r] -= 1
        return False

    try:
        ok = place(0)
        return ok, f"nodes={nodes} elapsed={time.time()-t0:.2f}s"
    except TimeoutError as e:
        return False, f"TIMEOUT {e} elapsed={time.time()-t0:.2f}s"


def main() -> None:
    S.cfg(MX, MY, H, V)
    S.init_ring()
    S.init_quadrants()
    fps = build_fps()
    # known greedy II = 45; multiplicity LB = 27
    results = {}
    # Ascend from mult_lb to find first feasible with short timeout, then
    # refine. Also descend from 45 to prove infeasibility of 44 etc.
    print("proving infeasibility downward from 45 ...", flush=True)
    min_feasible = None
    max_infeasible = None
    for ii in range(45, 26, -1):
        P = prep(fps, ii, buffer_depth=0)
        # longer budget near the frontier
        budget = 120.0 if ii >= 40 else 20.0
        ok, reason = feasible(P, time_limit_s=budget)
        status = "FEASIBLE" if ok else (
            "INFEASIBLE" if not reason.startswith("TIMEOUT") else "UNKNOWN")
        print(f"II={ii:3d} {status:10s} {reason}", flush=True)
        results[str(ii)] = {"status": status, "detail": reason,
                            "mult_lb": P["mult_lb"]}
        if ok:
            min_feasible = ii
        elif status == "INFEASIBLE":
            max_infeasible = ii
            # once we have a hard infeasible and a feasible above, we can stop
            # descending further only if we want the exact min; keep going a
            # few more to tighten the LB, then jump.
            if min_feasible is not None and min_feasible == max_infeasible + 1:
                break
        elif status == "UNKNOWN":
            # can't prove; stop descending this way
            break

    # If 45 was never re-checked as feasible via this solver, verify it.
    if min_feasible is None:
        P = prep(fps, 45, 0)
        ok, reason = feasible(P, time_limit_s=60.0)
        print(f"verify II=45: {ok} {reason}", flush=True)
        results["45"] = {"status": "FEASIBLE" if ok else "UNKNOWN",
                         "detail": reason, "mult_lb": P["mult_lb"]}
        if ok:
            min_feasible = 45

    summary = {
        "scheme": "col_comb3",
        "buffer_depth": 0,
        "link_multiplicity_lb": 27,
        "eject_lb": (N - 1 + RAMP_BW - 1) // RAMP_BW,
        "proven_min_feasible_ii": min_feasible,
        "proven_max_infeasible_ii": max_infeasible,
        "per_ii": results,
    }
    print("SUMMARY", json.dumps({k: v for k, v in summary.items()
                                 if k != "per_ii"}), flush=True)

    data = json.loads(OUT.read_text(encoding="utf-8"))
    data["schemes"]["col_comb3"]["cyclic_ii_exact"] = summary
    OUT.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
