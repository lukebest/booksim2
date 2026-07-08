#!/usr/bin/env python3
"""Export BookSim Route B traces (tree + fork) for 6x8 zero-buffer report cells.

Includes every (scheme, ramp_bw, m) where sched_zerobuf_compare rigid packer
succeeds (TRUE 0-buffer by construction), plus row_col (always rigid).

Output: results/traces/6x8_zbuf/<scheme>/bw<rb>_m<m>.{hop,tree,fork,meta.json}
"""

import json
from pathlib import Path

import export_booksim_trace as ET
import sched_zerobuf_compare as S

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "results" / "traces" / "6x8_zbuf"

MX, MY = 6, 8
REPORT_SCHEMES = ["multitree", "ring_uni", "ring_bi", "hybrid_v_bi_B2", "row_col"]
FLITS = [1, 2, 3, 4, 5]
RAMP_BWS = [1, 2]

BUILDERS = {n: b for n, b in ET.scheme_builders()}


def zbuf_cells_from_rigid():
    """All report scheme cells with a verified rigid 0-buffer pack."""
    out = set()
    for rb in RAMP_BWS:
        for m in FLITS:
            out.add(("row_col", rb, m))
            for name in REPORT_SCHEMES[:-1]:
                if resolve_packed(name, rb, m):
                    out.add((name, rb, m))
    return sorted(out)


def _remap_slots(slots, nid_fn):
    out = []
    for kind, key, rel in slots:
        if kind == "L":
            p, c = ET.lk_decode(key)
            out.append(("L", nid_fn(p) * 100000 + nid_fn(c), rel))
        elif kind == "D":
            out.append(("D", nid_fn(key), rel))
        elif kind == "U":
            out.append(("U", nid_fn(key), rel))
    return out


def _remap_fork_rows(fork_rows, src_fn, node_fn, fwd_fn):
    out = []
    for r in fork_rows:
        out.append({
            "gather_src": src_fn(r["gather_src"]),
            "node": node_fn(r["node"]),
            "eject": r["eject"],
            "forwards": [fwd_fn(x) for x in r["forwards"]],
        })
    return out


def row_col_packed(ramp_bw, flits):
    """Merge row + column rigid packs onto 6x8 grid for Route B bundle export."""
    p1 = ET.pack_scheme(lambda s, rb: S.fp_multitree(s), ramp_bw, flits, mx=MX, my=1)
    p2 = ET.pack_scheme(
        lambda s, rb: S.fp_multitree(s), ramp_bw, MX * flits, mx=1, my=MY)
    if not p1 or not p2:
        return None
    t1 = p1["makespan"]
    foot, inj, order = {}, {}, []
    for y in range(MY):
        for x in range(MX):
            nid = x + MX * y
            foot[nid] = _remap_slots(p1["foot"][x], lambda s, y=y: s + MX * y)
            inj[nid] = p1["inj"][x]
            order.append(nid)
    inj2 = {s: p2["inj"][s] + t1 for s in p2["inj"]}
    for x in range(MX):
        for y in range(MY):
            nid = x + MX * y
            foot[nid].extend(_remap_slots(p2["foot"][y], lambda s, x=x: x + MX * s))
            inj[nid] = min(inj[nid], inj2[y])
    mk = t1 + p2["makespan"]
    return {
        "makespan": mk,
        "order": order,
        "foot": foot,
        "inj": inj,
        "T1": t1,
        "T2": p2["makespan"],
        "sram_per_node": (MX - 1) * flits,
    }


def row_col_tree_fork(ramp_bw, flits):
    """Two-phase tree/fork: row allgather then column allgather."""
    p1 = ET.pack_scheme(lambda s, rb: S.fp_multitree(s), ramp_bw, flits, mx=MX, my=1)
    p2 = ET.pack_scheme(
        lambda s, rb: S.fp_multitree(s), ramp_bw, MX * flits, mx=1, my=MY)
    if not p1 or not p2:
        return None
    t1 = p1["makespan"]
    trees, forks = [], []
    for y in range(MY):
        src_fn = lambda s, y=y: s + MX * y
        node_fn = src_fn
        fwd_fn = src_fn
        t, f = ET.footprint_to_fork(p1["foot"], p1["inj"], p1["order"], flits)
        for tr in t:
            trees.append({
                "gather_src": src_fn(tr["gather_src"]),
                "inject_cycle": tr["inject_cycle"],
                "num_flits": tr["num_flits"],
            })
        forks.extend(_remap_fork_rows(f, src_fn, node_fn, fwd_fn))
    inj2 = {s: p2["inj"][s] + t1 for s in p2["inj"]}
    for x in range(MX):
        src_fn = lambda s, x=x: x + MX * s
        node_fn = src_fn
        fwd_fn = src_fn
        t, f = ET.footprint_to_fork(p2["foot"], inj2, p2["order"], MX * flits)
        for tr in t:
            trees.append({
                "gather_src": src_fn(tr["gather_src"]),
                "inject_cycle": tr["inject_cycle"],
                "num_flits": tr["num_flits"],
            })
        forks.extend(_remap_fork_rows(f, src_fn, node_fn, fwd_fn))
    return trees, forks, t1 + p2["makespan"]


def write_row_col_bundle(stem, ramp_bw, flits, packed):
    """Write hop + tree + fork for row_col (dual-phase tree injects)."""
    flit_count_fn = lambda s, m=flits: m
    hops = ET.footprint_to_hops(
        packed["foot"], packed["inj"], packed["order"], flits, flit_count_fn)
    ok, info = ET.verify_hop_trace(hops)
    if not ok:
        raise RuntimeError(f"row_col hop verify: {info}")
    trees, forks, mk = row_col_tree_fork(ramp_bw, flits)
    if not trees:
        raise RuntimeError("row_col tree/fork export failed")

    stem.parent.mkdir(parents=True, exist_ok=True)
    with open(stem.with_suffix(".hop"), "w", encoding="utf-8") as f:
        f.write(f"# scheme=row_col ramp_bw={ramp_bw} m={flits} expected_mk={mk}\n")
        for e in hops:
            f.write(
                f"HOP {e['inject']} {e['dest']} {e['cycle']} "
                f"{e['gather_src']} {e['flit_idx']} {e['final']}\n"
            )
    with open(stem.with_suffix(".tree"), "w", encoding="utf-8") as f:
        f.write(f"# scheme=row_col ramp_bw={ramp_bw} m={flits}\n")
        for t in sorted(trees, key=lambda t: (t["inject_cycle"], t["gather_src"])):
            f.write(f"TREE {t['gather_src']} {t['inject_cycle']} {t['num_flits']}\n")
    with open(stem.with_suffix(".fork"), "w", encoding="utf-8") as f:
        f.write(f"# scheme=row_col ramp_bw={ramp_bw} m={flits}\n")
        for r in forks:
            fwd = " ".join(str(x) for x in r["forwards"])
            f.write(f"FORK {r['gather_src']} {r['node']} {r['eject']} {len(r['forwards'])} {fwd}\n")

    return {
        "scheme": "row_col",
        "ramp_bw": ramp_bw,
        "m": flits,
        "expected_makespan": mk,
        "num_hop_events": len(hops),
        "max_hop_cycle": info,
        "num_tree_sources": len(trees),
        "num_fork_rows": len(forks),
        "T1": packed["T1"],
        "T2": packed["T2"],
        "sram": packed["sram_per_node"],
        "source": "rigid_row_col",
        "ok": True,
    }


def resolve_packed(scheme, ramp_bw, flits):
    if scheme == "row_col":
        return row_col_packed(ramp_bw, flits)
    if scheme == "ring_bi":
        return ET.pack_ring_bi(ramp_bw, flits)
    if scheme == "hybrid_v_bi_B2":
        return ET.pack_hybrid_v_bi_B2(ramp_bw, flits)
    build = BUILDERS.get(scheme)
    if not build:
        return None
    return ET.pack_scheme(lambda s, rb, bf=build: bf(s, rb), ramp_bw, flits)


def export_all(out_dir=OUT_DIR):
    cells = zbuf_cells_from_rigid()
    results = []
    for scheme, ramp_bw, flits in cells:
        stem = out_dir / scheme / f"bw{ramp_bw}_m{flits}"
        meta = {"scheme": scheme, "ramp_bw": ramp_bw, "m": flits, "ok": False}
        try:
            packed = resolve_packed(scheme, ramp_bw, flits)
            if not packed:
                results.append(meta)
                print(f"FAIL {scheme} bw={ramp_bw} m={flits} (pack)")
                continue
            if scheme == "row_col":
                meta = write_row_col_bundle(stem, ramp_bw, flits, packed)
            else:
                meta = ET.write_trace_bundle(stem, scheme, ramp_bw, flits, packed)
                meta["source"] = "rigid_pack"
                meta["ok"] = True
            results.append(meta)
            print(
                f"OK {scheme} bw={ramp_bw} m={flits} mk={meta['expected_makespan']} "
                f"tree={meta.get('num_tree_sources')} fork={meta.get('num_fork_rows')}"
            )
        except Exception as exc:
            meta["error"] = str(exc)
            results.append(meta)
            print(f"ERR {scheme} bw={ramp_bw} m={flits}: {exc}")

    summary = out_dir / "export_summary.json"
    summary.write_text(json.dumps(results, indent=2), encoding="utf-8")
    ok_n = sum(1 for r in results if r.get("ok"))
    print(f"Wrote {summary} ({ok_n}/{len(results)} ok)")
    return results


def main():
    export_all()


if __name__ == "__main__":
    main()
