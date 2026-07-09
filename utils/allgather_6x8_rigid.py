#!/usr/bin/env python3
"""Rigid 0-buffer allgather schedules for 6×8 report (sched_zerobuf_compare).

Ring-family schemes (ring_uni / ring_bi / hybrid_v_bi_B2) and border Q=4
(border_uni_Q4 / border_bi_Q4, alias hybrid_*_Q4) pick the minimum makespan
over all successful rigid-pack strategies, including m×m=1 repeat.
"""

import allgather_lower_bounds as LB
import sched_zerobuf_compare as S

MX, MY = 6, 8
B_HYBRID_V = 2
SRAM_TURNAROUND = 10


def setup(h=7, v=9):
    S.cfg(MX, MY, h, v)
    S.init_ring()
    S.init_quadrants()


def theory_t(m, ramp_bw, h=7, v=9):
    return LB.bounds_for(MX, MY, h, v, m, ramp_bw)["T"]


def _verify_round(busy, ramp_bw, flits, n):
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


def _best_pack(foot, ramp_bw, flits, pack_flits=None):
    n = MX * MY
    pf = pack_flits if pack_flits is not None else flits
    best = None
    for _, gen in S.SRC_ORDERS.items():
        order = gen()
        mk, _, busy = S.pack(foot, ramp_bw, order, flits=pf)
        if _verify_round(busy, ramp_bw, flits, n) and (best is None or mk < best):
            best = mk
    return best


def _run_scheme(build_fp, ramp_bw, flits=1):
    mk, _, _, ok = S.run_scheme(build_fp, ramp_bw, flits=flits)
    return mk if ok else None


def _pick_min(candidates):
    """candidates: list of (label, mk|None) -> (mk, label) or (None, None)."""
    valid = [(lbl, mk) for lbl, mk in candidates if mk is not None]
    if not valid:
        return None, None
    lbl, mk = min(valid, key=lambda x: x[1])
    return mk, lbl


def batch_plans(m):
    """Candidate multi-round decompositions to try."""
    plans = [[m], [1] * m]
    if m == 2:
        plans.append([2])
    elif m == 3:
        plans.extend([[2, 1], [3]])
    elif m == 4:
        plans.extend([[2, 2], [4]])
    elif m == 5:
        plans.extend([[2, 2, 1], [5]])
    uniq = []
    seen = set()
    for p in plans:
        t = tuple(p)
        if t not in seen:
            seen.add(t)
            uniq.append(list(p))
    return uniq


def fp_ring_bi_flits(s, ramp_bw, flits):
    order, pos = S.RING_ORDER, S.RING_POS
    if flits == 1 or ramp_bw >= 2:
        return S.fp_ring(s, order, pos, True, ramp_bw)
    i = pos[s]
    n = len(order)
    a = n // 2
    b = (n - 1) - a
    fwd = [order[(i + k) % n] for k in range(a + 1)]
    bwd = [order[(i - k) % n] for k in range(b + 1)]
    d2, period = 1, 2
    slots = []
    for f in range(flits):
        base = f * period
        slots.append(("U", s, base))
        sf, _ = S._arc(fwd, S.RAMP + base)
        slots.append(("U", s, base + d2))
        sb, _ = S._arc(bwd, S.RAMP + base + d2)
        slots += sf + sb
    return slots


def fp_hybrid_v_flits(s, ramp_bw, flits):
    if flits == 1 or ramp_bw >= 2:
        return S.fp_hybrid_v(s, B_HYBRID_V, True, ramp_bw)
    C = S.MX // B_HYBRID_V
    sx, _ = S.coord(s)
    x0 = (sx // C) * C
    order = S.ham_cycle_vband(C, x0)
    pos = {nd: k for k, nd in enumerate(order)}
    d2, period = 1, 2
    slots = []
    for f in range(flits):
        base = f * period
        n = len(order)
        i = pos[s]
        a = n // 2
        b = (n - 1) - a
        fwd = [order[(i + k) % n] for k in range(a + 1)]
        bwd = [order[(i - k) % n] for k in range(b + 1)]
        arr = {s: S.RAMP + base}
        slots.append(("U", s, base))
        sf, _ = S._arc(fwd, S.RAMP + base)
        slots.append(("U", s, base + d2))
        sb, _ = S._arc(bwd, S.RAMP + base + d2)
        slots += sf + sb
        t = S.RAMP + base
        for k in range(len(fwd) - 1):
            t += S.edge_lat(fwd[k], fwd[k + 1])
            arr[fwd[k + 1]] = t
        t = S.RAMP + base + d2
        for k in range(len(bwd) - 1):
            t += S.edge_lat(bwd[k], bwd[k + 1])
            arr[bwd[k + 1]] = t
        for y in range(S.MY):
            t = arr[S.nid(x0, y)]
            prev = S.nid(x0, y)
            for xx in range(x0 - 1, -1, -1):
                cur = S.nid(xx, y)
                slots.append(("L", S.lk(prev, cur), t))
                t += S.H
                slots.append(("D", cur, t))
                prev = cur
            t = arr[S.nid(x0 + C - 1, y)]
            prev = S.nid(x0 + C - 1, y)
            for xx in range(x0 + C, S.MX):
                cur = S.nid(xx, y)
                slots.append(("L", S.lk(prev, cur), t))
                t += S.H
                slots.append(("D", cur, t))
                prev = cur
    return slots


def _ring_bi_round(ramp_bw, batch):
    n = MX * MY
    build = lambda s, rb=ramp_bw: S.fp_ring(
        s, S.RING_ORDER, S.RING_POS, True, rb)
    if batch == 1:
        return _run_scheme(build, ramp_bw, 1)
    if ramp_bw >= 2:
        return _run_scheme(build, ramp_bw, batch)
    foot = {s: fp_ring_bi_flits(s, ramp_bw, batch) for s in range(n)}
    mk_tdm = _best_pack(foot, ramp_bw, batch, pack_flits=1)
    mk_dir = _run_scheme(build, ramp_bw, batch)
    return _pick_min([("tdm", mk_tdm), ("direct", mk_dir)])[0]


def _hybrid_round(ramp_bw, batch):
    build = lambda s, rb=ramp_bw: S.fp_hybrid_v(s, B_HYBRID_V, True, rb)
    if batch == 1:
        return _run_scheme(build, ramp_bw, 1)
    if ramp_bw >= 2:
        return _run_scheme(build, ramp_bw, batch)
    n = MX * MY
    foot = {s: fp_hybrid_v_flits(s, ramp_bw, batch) for s in range(n)}
    mk_tdm = _best_pack(foot, ramp_bw, batch, pack_flits=1)
    mk_dir = _run_scheme(build, ramp_bw, batch)
    return _pick_min([("tdm", mk_tdm), ("direct", mk_dir)])[0]


def _compose_rounds(round_fn, batches):
    total = 0
    for b in batches:
        mk = round_fn(b)
        if mk is None:
            return None
        total += mk
    return total


def pack_ring_uni(ramp_bw, m):
    build = lambda s, rb=ramp_bw: S.fp_ring(
        s, S.RING_ORDER, S.RING_POS, False, rb)
    mk1 = _run_scheme(build, ramp_bw, 1)
    cands = []
    if mk1 is not None:
        cands.append((f"{m}×m=1", mk1 * m))
    mk_d = _run_scheme(build, ramp_bw, m)
    cands.append(("direct", mk_d))
    mk, lbl = _pick_min(cands)
    return mk, lbl


def pack_ring_bi(ramp_bw, m):
    mk1 = _ring_bi_round(ramp_bw, 1)
    cands = []
    if mk1 is not None:
        cands.append((f"{m}×m=1", mk1 * m))
    for batches in batch_plans(m):
        mk = _compose_rounds(lambda b: _ring_bi_round(ramp_bw, b), batches)
        cands.append((f"batch{batches}", mk))
    return _pick_min(cands)


def pack_hybrid_v_bi_B2(ramp_bw, m):
    mk1 = _hybrid_round(ramp_bw, 1)
    cands = []
    if mk1 is not None:
        cands.append((f"{m}×m=1", mk1 * m))
    for batches in batch_plans(m):
        mk = _compose_rounds(lambda b: _hybrid_round(ramp_bw, b), batches)
        cands.append((f"batch{batches}", mk))
    return _pick_min(cands)


def _border_round(bidir, ramp_bw, batch):
    build = lambda s, rb=ramp_bw: S.fp_border(s, bidir, rb)
    return _run_scheme(build, ramp_bw, batch)


def pack_border(bidir, ramp_bw, m):
    mk1 = _border_round(bidir, ramp_bw, 1)
    cands = []
    if mk1 is not None:
        cands.append((f"{m}×m=1", mk1 * m))
    for batches in batch_plans(m):
        mk = _compose_rounds(lambda b: _border_round(bidir, ramp_bw, b), batches)
        cands.append((f"batch{batches}", mk))
    return _pick_min(cands)


def pack_row_col(ramp_bw, m):
    S.cfg(MX, 1, S.H, S.V)
    S.init_ring()
    mk1, _, _, ok1 = S.run_scheme(S.fp_multitree, ramp_bw, flits=m)
    S.cfg(1, MY, S.H, S.V)
    S.init_ring()
    mk2, _, _, ok2 = S.run_scheme(S.fp_multitree, ramp_bw, flits=MX * m)
    if not (ok1 and ok2):
        return None
    total = mk1 + SRAM_TURNAROUND + mk2
    return {
        "T1": mk1,
        "T2": mk2,
        "turnaround": SRAM_TURNAROUND,
        "Ttotal": total,
        "sram": (MX - 1) * m,
    }


def scheme_makespan(name, ramp_bw, m):
    setup()
    if name == "multitree":
        mk = _run_scheme(S.fp_multitree, ramp_bw, m)
        return {"makespan": mk, "zbuf": mk is not None, "strategy": "direct"}
    if name == "ring_uni":
        mk, lbl = pack_ring_uni(ramp_bw, m)
        return {"makespan": mk, "zbuf": mk is not None, "strategy": lbl}
    if name == "ring_bi":
        mk, lbl = pack_ring_bi(ramp_bw, m)
        return {"makespan": mk, "zbuf": mk is not None, "strategy": lbl}
    if name == "hybrid_v_bi_B2":
        mk, lbl = pack_hybrid_v_bi_B2(ramp_bw, m)
        return {"makespan": mk, "zbuf": mk is not None, "strategy": lbl}
    if name == "border_uni_Q4":
        mk, lbl = pack_border(False, ramp_bw, m)
        return {"makespan": mk, "zbuf": mk is not None, "strategy": lbl}
    if name == "border_bi_Q4":
        mk, lbl = pack_border(True, ramp_bw, m)
        return {"makespan": mk, "zbuf": mk is not None, "strategy": lbl}
    if name == "row_col":
        rc = pack_row_col(ramp_bw, m)
        if not rc:
            return {"makespan": None, "zbuf": False, "strategy": None}
        return {**rc, "makespan": rc["Ttotal"], "zbuf": True, "strategy": "row+col"}
    raise KeyError(name)
