#!/usr/bin/env python3
"""Export allgather schedules to BookSim2 trace files (Route A hop + Route B tree/fork).

Uses sched_zerobuf_compare rigid packer for scheme footprints and offsets.
Writes under results/traces/6x8/<scheme>/bw<rb>_m<m>.{hop,tree,fork,meta.json}
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path

import sched_zerobuf_compare as S

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "results" / "traces" / "6x8"

MX, MY, H, V = 6, 8, 4, 6
N = MX * MY
FLITS = [1, 2, 3, 4, 5]
RAMP_BWS = [1, 2]


def lk_decode(key):
    return key // 100000, key % 100000


def scheme_builders():
    """Return list of (name, build_fp_fn)."""
    S.cfg(MX, MY, H, V)
    cands = [
        ("multitree", lambda s, rb: S.fp_multitree(s)),
        ("ring_uni", lambda s, rb: S.fp_ring(s, S.RING_ORDER, S.RING_POS, False, rb)),
        ("ring_bi", lambda s, rb: S.fp_ring(s, S.RING_ORDER, S.RING_POS, True, rb)),
    ]
    for B in S.divisors_bands():
        if MY // B < 1:
            continue
        if MY // B >= 2:
            cands.append((f"hybrid_uni_B{B}",
                            lambda s, rb, B=B: S.fp_hybrid(s, B, False, rb)))
        cands.append((f"hybrid_bi_B{B}",
                        lambda s, rb, B=B: S.fp_hybrid(s, B, True, rb)))
    for B in S.divisors_bands():
        if MX // B < 1:
            continue
        if MX // B >= 2:
            cands.append((f"hybrid_v_uni_B{B}",
                            lambda s, rb, B=B: S.fp_hybrid_v(s, B, False, rb)))
            cands.append((f"hybrid_v_bi_B{B}",
                            lambda s, rb, B=B: S.fp_hybrid_v(s, B, True, rb)))
    return cands


def pack_scheme(build_fp, ramp_bw, flits, mx=None, my=None):
    if mx is None:
        mx, my = MX, MY
    S.cfg(mx, my, H, V)
    S.init_ring()
    n = mx * my
    foot = {s: build_fp(s, ramp_bw) for s in range(n)}
    best = None
    best_order = None
    for name, gen in S.SRC_ORDERS.items():
        order = gen()
        mk, mo, busy = S.pack(foot, ramp_bw, order, flits=flits)
        ok = S.verify(busy, ramp_bw, flits=flits)
        if not ok:
            continue
        if best is None or mk < best:
            best, best_order = mk, order
    if best is None:
        return None
    _, _, _, inj, _ = S.export_events(foot, ramp_bw, best_order, flits=flits)
    _, _, busy = S.apply_offsets(foot, inj, best_order, ramp_bw, flits=flits)
    ok = S.verify(busy, ramp_bw, flits=flits)
    if not ok:
        return None
    return {"makespan": best, "order": best_order, "foot": foot, "inj": inj, "n": n}


def footprint_to_hops(foot, inj, src_order, flits, flit_count_fn=None,
                      flit_idx_offset=0, per_source_flits=None):
    """Convert rigid footprints to per-hop injection events."""
    if flit_count_fn is None:
        flit_count_fn = lambda s: flits
    events = []
    for s in src_order:
        off = inj[s]
        nf = per_source_flits[s] if per_source_flits else flit_count_fn(s)
        slots = foot[s]
        d_rel = {}
        for kind, key, rel in slots:
            if kind == "D":
                d_rel[key] = rel
        for kind, key, rel in slots:
            if kind != "L":
                continue
            p, c = lk_decode(key)
            lat = S.edge_lat(p, c)
            arr = rel + lat
            base = off + rel
            pack_flits = 1 if per_source_flits else nf
            for i in range(pack_flits):
                fi = flit_idx_offset + i
                is_final = d_rel.get(c, -1) == arr + i
                events.append({
                    "inject": p,
                    "dest": c,
                    "cycle": base + i,
                    "gather_src": s,
                    "flit_idx": fi,
                    "final": int(is_final),
                })
    events.sort(key=lambda e: (e["cycle"], e["inject"], e["dest"], e["gather_src"]))
    return events


def footprint_to_fork(foot, inj, src_order, flits, flit_count_fn=None):
    """Per-source tree inject + fork table for Route B."""
    if flit_count_fn is None:
        flit_count_fn = lambda s: flits
    tree_injects = []
    fork_rows = []
    for s in src_order:
        off = inj[s]
        nf = flit_count_fn(s)
        slots = foot[s]
        min_u = min((rel for k, _, rel in [(x[0], x[1], x[2]) for x in slots if x[0] == "U"]), default=0)
        tree_injects.append({
            "gather_src": s,
            "inject_cycle": off + min_u,
            "num_flits": nf,
        })
        by_node = defaultdict(lambda: {"eject": False, "forwards": set()})
        for kind, key, rel in slots:
            if kind == "L":
                p, c = lk_decode(key)
                by_node[p]["forwards"].add(c)
            elif kind == "D":
                by_node[key]["eject"] = True
        for node, act in sorted(by_node.items()):
            if not act["eject"] and not act["forwards"]:
                continue
            fork_rows.append({
                "gather_src": s,
                "node": node,
                "eject": int(act["eject"]),
                "forwards": sorted(act["forwards"]),
            })
    return tree_injects, fork_rows


def verify_hop_trace(events):
    link_busy = defaultdict(lambda: defaultdict(int))
    max_cycle = 0
    for e in events:
        k = (e["inject"], e["dest"])
        c = e["cycle"]
        link_busy[k][c] += 1
        if link_busy[k][c] > 1:
            return False, f"link conflict {k} @ {c}"
        max_cycle = max(max_cycle, c)
    return True, max_cycle


def row_col_pack(ramp_bw, flits):
    p1 = pack_scheme(lambda s, rb: S.fp_multitree(s), ramp_bw, flits, mx=MX, my=1)
    if not p1:
        return None
    p2 = pack_scheme(lambda s, rb: S.fp_multitree(s), ramp_bw, MX * flits, mx=1, my=MY)
    if not p2:
        return None
    t1 = p1["makespan"]
    shift = t1
    foot = {}
    inj = {}
    order = p1["order"]
    for s in order:
        foot[s] = list(p1["foot"][s])
        inj[s] = p1["inj"][s]
    row_nodes = MX
    for s in p2["order"]:
        gy = s
        for x in range(MX):
            nid = x + MX * gy
            foot[nid] = list(p2["foot"][s])
            inj[nid] = p2["inj"][s] + shift
    mk = max(inj[s] + max(rel for _, _, rel in foot[s]) for s in foot)
    for s in foot:
        for kind, key, rel in foot[s]:
            if kind == "D":
                mk = max(mk, inj[s] + rel + flits - 1 + S.RAMP)
    busy_link = defaultdict(lambda: defaultdict(int))
    for s in foot:
        for kind, key, rel in foot[s]:
            if kind == "L":
                p, c = lk_decode(key)
                for i in range(flits if s < row_nodes else MX * flits):
                    busy_link[(p, c)][inj[s] + rel + i] += 1
    for k, d in busy_link.items():
        for c, ct in d.items():
            if ct > 1:
                return None
    return {
        "makespan": mk,
        "order": order,
        "foot": foot,
        "inj": inj,
        "T1": t1,
        "T2": p2["makespan"],
        "sram_per_node": (MX - 1) * flits,
    }


def rb1_tdm_batch_plan(m, ramp_bw, m2_tdm_ok):
    """Batch waves @ ramp_bw=1 when per-flit up-ramp TDM works for m=2."""
    if ramp_bw != 1:
        return [m]
    if not m2_tdm_ok:
        return [1] * m
    if m == 1:
        return [1]
    if m == 2:
        return [2]
    if m == 3:
        return [2, 1]
    if m == 4:
        return [2, 2]
    if m == 5:
        return [2, 2, 1]
    raise ValueError(f"unsupported m={m}")


def ring_bi_batch_plan(m, ramp_bw):
    """Split ring_bi @ ramp_bw=1 into TDM rounds (fwd/bwd stagger + batch waves)."""
    if ramp_bw != 1:
        return [m]
    return rb1_tdm_batch_plan(m, ramp_bw, m2_tdm_ok=True)


def fp_ring_bi_flits(s, ramp_bw, flits, mx=None, my=None):
    """Bidirectional ring footprint; per-flit up-ramp TDM when ramp_bw=1 and flits>1."""
    if mx is not None or my is not None:
        S.cfg(mx or MX, my or MY, H, V)
        S.init_ring()
    order, pos = S.RING_ORDER, S.RING_POS
    if flits == 1 or ramp_bw >= 2:
        return S.fp_ring(s, order, pos, True, ramp_bw)
    i = pos[s]
    n = len(order)
    a = n // 2
    b = (n - 1) - a
    fwd = [order[(i + k) % n] for k in range(a + 1)]
    bwd = [order[(i - k) % n] for k in range(b + 1)]
    d2 = 1
    period = d2 + 1
    slots = []
    for f in range(flits):
        base = f * period
        slots.append(("U", s, base))
        sf, _ = S._arc(fwd, S.RAMP + base)
        slots.append(("U", s, base + d2))
        sb, _ = S._arc(bwd, S.RAMP + base + d2)
        slots += sf + sb
    return slots


def _verify_scheme_round(busy, ramp_bw, flits, n):
    link_busy, up_busy, down_busy = busy
    if not all(ct <= 1 for d in link_busy.values() for ct in d.values()):
        return False
    if not all(ct <= ramp_bw for d in up_busy.values() for ct in d.values()):
        return False
    if not all(ct <= ramp_bw for d in down_busy.values() for ct in d.values()):
        return False
    need = (n - 1) * flits
    ejects = {node: sum(d.values()) for node, d in down_busy.items()}
    return all(ejects.get(node, 0) == need for node in range(n))


def _verify_ring_round(busy, ramp_bw, flits, n):
    return _verify_scheme_round(busy, ramp_bw, flits, n)


def hybrid_v_bi_batch_plan(m, ramp_bw, B=2):
    """hybrid_v_bi_B2 @ ramp_bw=1: m 轮 m=1 串行（优于单轮 m=2 TDM）。"""
    if ramp_bw != 1:
        return [m]
    return [1] * m


def pack_hybrid_v_bi_round(ramp_bw, batch_flits, B=2, mx=None, my=None):
    mx = mx or MX
    my = my or MY
    S.cfg(mx, my, H, V)
    S.init_ring()
    n = mx * my
    assert batch_flits == 1
    foot = {s: S.fp_hybrid_v(s, B, True, ramp_bw) for s in range(n)}
    best = None
    best_order = None
    for _, gen in S.SRC_ORDERS.items():
        order = gen()
        mk, _, busy = S.pack(foot, ramp_bw, order, flits=1)
        if _verify_scheme_round(busy, ramp_bw, 1, n) and (best is None or mk < best):
            best, best_order = mk, order
    if best is None:
        return None
    _, _, _, inj, _ = S.export_events(foot, ramp_bw, best_order, flits=1)
    return {
        "makespan": best,
        "order": best_order,
        "foot": foot,
        "inj": inj,
        "batch": 1,
        "n": n,
    }


def pack_hybrid_v_bi_B2(ramp_bw, flits, mx=None, my=None, B=2):
    """Rigid 0-buffer hybrid_v_bi_B2 @ rb=1: m 轮 m=1 串行。"""
    batches = hybrid_v_bi_batch_plan(flits, ramp_bw, B)
    if len(batches) == 1:
        return pack_scheme(
            lambda s, rb, B=B: S.fp_hybrid_v(s, B, True, rb),
            ramp_bw,
            flits,
            mx=mx,
            my=my,
        )
    rounds = []
    t0 = 0
    flit_base = 0
    for b in batches:
        pr = pack_hybrid_v_bi_round(ramp_bw, b, B=B, mx=mx, my=my)
        if not pr:
            return None
        pr["t0"] = t0
        pr["flit_base"] = flit_base
        rounds.append(pr)
        t0 += pr["makespan"]
        flit_base += b
    return {
        "makespan": t0,
        "rounds": rounds,
        "batches": batches,
        "m": flits,
        "order": rounds[0]["order"],
        "source": "hybrid_v_m1_repeat",
    }


def pack_ring_bi_round(ramp_bw, batch_flits, mx=None, my=None):
    """One rigid 0-buffer round for ring_bi (batch_flits flits per source)."""
    mx = mx or MX
    my = my or MY
    S.cfg(mx, my, H, V)
    S.init_ring()
    n = mx * my
    foot = {
        s: fp_ring_bi_flits(s, ramp_bw, batch_flits, mx=mx, my=my)
        for s in range(n)
    }
    pack_flits = 1 if ramp_bw == 1 and batch_flits > 1 else batch_flits
    best = None
    best_order = None
    for _, gen in S.SRC_ORDERS.items():
        order = gen()
        mk, _, busy = S.pack(foot, ramp_bw, order, flits=pack_flits)
        ok = (
            _verify_ring_round(busy, ramp_bw, batch_flits, n)
            if pack_flits == 1
            else S.verify(busy, ramp_bw, flits=batch_flits)
        )
        if ok and (best is None or mk < best):
            best, best_order = mk, order
    if best is None:
        return None
    _, _, _, inj, _ = S.export_events(foot, ramp_bw, best_order, flits=pack_flits)
    return {
        "makespan": best,
        "order": best_order,
        "foot": foot,
        "inj": inj,
        "batch": batch_flits,
        "n": n,
    }


def pack_ring_bi(ramp_bw, flits, mx=None, my=None):
    """Rigid 0-buffer ring_bi: TDM fwd/bwd @ rb=1; batched waves for m>=3."""
    batches = ring_bi_batch_plan(flits, ramp_bw)
    if len(batches) == 1 and not (ramp_bw == 1 and flits > 1):
        return pack_scheme(
            lambda s, rb: S.fp_ring(s, S.RING_ORDER, S.RING_POS, True, rb),
            ramp_bw,
            flits,
            mx=mx,
            my=my,
        )
    rounds = []
    t0 = 0
    flit_base = 0
    for b in batches:
        pr = pack_ring_bi_round(ramp_bw, b, mx=mx, my=my)
        if not pr:
            return None
        pr["t0"] = t0
        pr["flit_base"] = flit_base
        rounds.append(pr)
        t0 += pr["makespan"]
        flit_base += b
    return {
        "makespan": t0,
        "rounds": rounds,
        "batches": batches,
        "m": flits,
        "order": rounds[0]["order"],
        "source": "ring_bi_tdm_batches",
    }


def hops_from_batched_rounds(rounds):
    hops = []
    for r in rounds:
        inj = {s: r["t0"] + r["inj"][s] for s in r["inj"]}
        psf = {s: r["batch"] for s in range(r["n"])}
        hops.extend(
            footprint_to_hops(
                r["foot"],
                inj,
                r["order"],
                r["batch"],
                flit_idx_offset=r["flit_base"],
                per_source_flits=psf,
            )
        )
    hops.sort(key=lambda e: (e["cycle"], e["inject"], e["dest"], e["gather_src"]))
    return hops


def tree_fork_from_batched_rounds(rounds):
    trees = []
    forks = None
    for r in rounds:
        inj = {s: r["t0"] + r["inj"][s] for s in r["inj"]}
        t, f = footprint_to_fork(r["foot"], inj, r["order"], r["batch"])
        for tr in t:
            trees.append({
                "gather_src": tr["gather_src"],
                "inject_cycle": tr["inject_cycle"],
                "num_flits": tr["num_flits"],
            })
        if forks is None:
            forks = f
    trees.sort(key=lambda t: (t["inject_cycle"], t["gather_src"]))
    return trees, forks


def write_trace_bundle(out_stem, scheme, ramp_bw, flits, packed, extra=None, flit_count_fn=None):
    if packed.get("rounds"):
        hops = hops_from_batched_rounds(packed["rounds"])
        tree, fork = tree_fork_from_batched_rounds(packed["rounds"])
        expected_mk = packed["makespan"]
    else:
        foot, inj, order = packed["foot"], packed["inj"], packed["order"]
        hops = footprint_to_hops(foot, inj, order, flits, flit_count_fn)
        tree, fork = footprint_to_fork(foot, inj, order, flits, flit_count_fn)
        expected_mk = packed["makespan"]
    ok, info = verify_hop_trace(hops)
    if not ok:
        raise RuntimeError(f"hop verify failed {scheme} bw={ramp_bw} m={flits}: {info}")
    max_cycle = info

    meta = {
        "mx": MX, "my": MY, "n": N, "h": H, "v": V,
        "scheme": scheme, "ramp_bw": ramp_bw, "m": flits,
        "expected_makespan": expected_mk,
        "num_hop_events": len(hops),
        "max_hop_cycle": max_cycle,
        "num_tree_sources": len(tree),
        "num_fork_rows": len(fork),
    }
    if extra:
        meta.update(extra)
    if packed.get("batches"):
        meta["batches"] = packed["batches"]
        meta["source"] = packed.get("source", "ring_bi_tdm_batches")

    out_stem.parent.mkdir(parents=True, exist_ok=True)
    with open(out_stem.with_suffix(".hop"), "w", encoding="utf-8") as f:
        f.write(f"# scheme={scheme} ramp_bw={ramp_bw} m={flits} expected_mk={expected_mk}\n")
        for e in hops:
            f.write(f"HOP {e['inject']} {e['dest']} {e['cycle']} {e['gather_src']} {e['flit_idx']} {e['final']}\n")

    with open(out_stem.with_suffix(".tree"), "w", encoding="utf-8") as f:
        f.write(f"# scheme={scheme} ramp_bw={ramp_bw} m={flits}\n")
        for t in tree:
            f.write(f"TREE {t['gather_src']} {t['inject_cycle']} {t['num_flits']}\n")

    with open(out_stem.with_suffix(".fork"), "w", encoding="utf-8") as f:
        f.write(f"# scheme={scheme} ramp_bw={ramp_bw} m={flits}\n")
        for r in fork:
            fwd = " ".join(str(x) for x in r["forwards"])
            f.write(f"FORK {r['gather_src']} {r['node']} {r['eject']} {len(r['forwards'])} {fwd}\n")

    out_stem.with_suffix(".meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def export_all(out_dir=OUT_DIR, schemes_filter=None):
    results = []
    builders = scheme_builders()
    if schemes_filter:
        builders = [(n, b) for n, b in builders if n in schemes_filter]

    for ramp_bw in RAMP_BWS:
        for flits in FLITS:
            for name, build_fp in builders:
                packed = pack_scheme(lambda s, rb, bf=build_fp: bf(s, rb), ramp_bw, flits)
                if not packed:
                    results.append({"scheme": name, "ramp_bw": ramp_bw, "m": flits, "ok": False})
                    continue
                stem = out_dir / name / f"bw{ramp_bw}_m{flits}"
                meta = write_trace_bundle(stem, name, ramp_bw, flits, packed)
                meta["ok"] = True
                results.append(meta)
                print(f"OK {name} bw={ramp_bw} m={flits} mk={meta['expected_makespan']} hops={meta['num_hop_events']}")

            # row_col
            rc = row_col_pack(ramp_bw, flits)
            if rc:
                stem = out_dir / "row_col" / f"bw{ramp_bw}_m{flits}"
                meta = write_trace_bundle(stem, "row_col", ramp_bw, flits, rc,
                                          extra={"T1": rc["T1"], "T2": rc["T2"], "sram": rc["sram_per_node"]},
                                          flit_count_fn=lambda s, m=flits: m if s < MX else MX * m)
                meta["ok"] = True
                results.append(meta)
                print(f"OK row_col bw={ramp_bw} m={flits} mk={meta['expected_makespan']}")

    summary_path = out_dir / "export_summary.json"
    summary_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Wrote {summary_path} ({len(results)} entries)")
    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    ap.add_argument("--scheme", action="append", default=None)
    args = ap.parse_args()
    export_all(Path(args.out_dir), args.scheme)


if __name__ == "__main__":
    main()
