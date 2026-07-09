#!/usr/bin/env python3
"""Rigid 0-buffer allgather schedules for 6×8 report (sched_zerobuf_compare).

Ring-family schemes (ring_uni / ring_bi / hybrid_v_bi_B2) and border Q=4
(border_uni_Q4 / border_bi_Q4, alias hybrid_*_Q4) pick the minimum makespan
over all successful rigid-pack strategies, including m×m=1 repeat.

Also exposes axis_ccw (cross-axis + CCW-90° fanout) and per-scheme ramp
bandwidth stats (average / peak burst).
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


def _verify_round(busy, ramp_bw, flits, n, up_cap=None, down_cap=None):
    uc = ramp_bw if up_cap is None else up_cap
    dc = ramp_bw if down_cap is None else down_cap
    link_busy, up_busy, down_busy = busy
    if not all(ct <= 1 for d in link_busy.values() for ct in d.values()):
        return False
    if not all(ct <= uc for d in up_busy.values() for ct in d.values()):
        return False
    if not all(ct <= dc for d in down_busy.values() for ct in d.values()):
        return False
    need = (n - 1) * flits
    ejects = {node: sum(d.values()) for node, d in down_busy.items()}
    return all(ejects.get(node, 0) == need for node in range(n))


def ramp_stats(busy, makespan, n=None):
    """Average / peak up & down ramp bandwidth from a packed busy table.

    avg = total flit-cycles / (N * makespan)  — mean per-node bandwidth over
    the schedule window. peak = max concurrent flits on any node in any cycle.
    """
    n = MX * MY if n is None else n
    if busy is None or makespan is None or makespan <= 0:
        return {
            "up_avg": None, "down_avg": None,
            "up_peak": None, "down_peak": None,
        }
    _, up_busy, down_busy = busy

    def peak(table):
        return max((ct for d in table.values() for ct in d.values()), default=0)

    def total(table):
        return sum(sum(d.values()) for d in table.values())

    span = makespan
    return {
        "up_avg": round(total(up_busy) / (n * span), 4),
        "down_avg": round(total(down_busy) / (n * span), 4),
        "up_peak": peak(up_busy),
        "down_peak": peak(down_busy),
    }


def _merge_ramp_stats(parts, total_mk, n=None):
    """Merge per-round ramp stats for multi-round schedules."""
    n = MX * MY if n is None else n
    if not parts or total_mk is None or total_mk <= 0:
        return ramp_stats(None, None, n)
    up_peak = max(p["up_peak"] for p in parts if p["up_peak"] is not None)
    down_peak = max(p["down_peak"] for p in parts if p["down_peak"] is not None)
    # Reconstruct totals from avg * n * round_mk
    up_tot = sum(p["up_avg"] * n * p["_mk"] for p in parts)
    down_tot = sum(p["down_avg"] * n * p["_mk"] for p in parts)
    return {
        "up_avg": round(up_tot / (n * total_mk), 4),
        "down_avg": round(down_tot / (n * total_mk), 4),
        "up_peak": up_peak,
        "down_peak": down_peak,
    }


def _best_pack_detail(foot, ramp_bw, flits, pack_flits=None, down_cap=None):
    n = MX * MY
    pf = pack_flits if pack_flits is not None else flits
    best = None  # (mk, busy)
    for _, gen in S.SRC_ORDERS.items():
        order = gen()
        mk, _, busy = S.pack(foot, ramp_bw, order, flits=pf, down_cap=down_cap)
        if _verify_round(busy, ramp_bw, flits, n, down_cap=down_cap) and (
                best is None or mk < best[0]):
            best = (mk, busy)
    return best


def _best_pack(foot, ramp_bw, flits, pack_flits=None, down_cap=None):
    d = _best_pack_detail(foot, ramp_bw, flits, pack_flits, down_cap=down_cap)
    return d[0] if d else None


def _run_scheme_detail(build_fp, ramp_bw, flits=1, down_cap=None):
    """Return (makespan, busy, ok) for the best SRC_ORDER pack.

    Uses current ``S.N`` so row_col sub-meshes (6×1 / 1×8) verify correctly.
    ``down_cap``: if set, down-ramp may carry that many flits/cy (models a
    small eject buffer absorbing bursts); links and up-ramp still use
    ramp_bw (up) / 1 (link).
    """
    n = S.N
    foot = {s: build_fp(s) for s in range(n)}
    foot_lo = None
    if ramp_bw > 1:
        foot_lo = {s: build_fp(s) for s in range(n)}
    best = None  # (mk, busy, ok)
    for _, gen in S.SRC_ORDERS.items():
        try:
            order = gen()
        except TypeError:
            continue
        if len(order) != n:
            order = list(range(n))
        mk, _, busy = S.pack(foot, ramp_bw, order, flits=flits, down_cap=down_cap)
        ok = _verify_round(busy, ramp_bw, flits, n, down_cap=down_cap)
        if best is None or mk < best[0]:
            best = (mk, busy, ok)
        if foot_lo is not None:
            _, _, _, inj, _ = S.export_events(
                foot_lo, 1, order, flits=flits, down_cap=down_cap)
            mk_lo, _, busy_lo = S.apply_offsets(
                foot_lo, inj, order, ramp_bw, flits=flits, down_cap=down_cap)
            ok_lo = _verify_round(busy_lo, ramp_bw, flits, n, down_cap=down_cap)
            if mk_lo < best[0]:
                best = (mk_lo, busy_lo, ok_lo)
    if best is None:
        return None, None, False
    return best


def _run_scheme(build_fp, ramp_bw, flits=1, down_cap=None):
    mk, _, ok = _run_scheme_detail(build_fp, ramp_bw, flits, down_cap=down_cap)
    return mk if ok else None


def _pick_min(candidates):
    """candidates: list of (label, mk|None, extra|None) or (label, mk|None)."""
    valid = []
    for c in candidates:
        if len(c) == 2:
            lbl, mk = c
            extra = None
        else:
            lbl, mk, extra = c
        if mk is not None:
            valid.append((lbl, mk, extra))
    if not valid:
        return None, None, None
    lbl, mk, extra = min(valid, key=lambda x: x[1])
    return mk, lbl, extra


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


def _ring_bi_round_detail(ramp_bw, batch, down_cap=None):
    n = MX * MY
    build = lambda s, rb=ramp_bw: S.fp_ring(
        s, S.RING_ORDER, S.RING_POS, True, rb)
    if batch == 1:
        mk, busy, ok = _run_scheme_detail(build, ramp_bw, 1, down_cap=down_cap)
        return (mk, busy) if ok else (None, None)
    if ramp_bw >= 2:
        mk, busy, ok = _run_scheme_detail(build, ramp_bw, batch, down_cap=down_cap)
        return (mk, busy) if ok else (None, None)
    foot = {s: fp_ring_bi_flits(s, ramp_bw, batch) for s in range(n)}
    tdm = _best_pack_detail(foot, ramp_bw, batch, pack_flits=1, down_cap=down_cap)
    mk_dir, busy_dir, ok_dir = _run_scheme_detail(
        build, ramp_bw, batch, down_cap=down_cap)
    cands = []
    if tdm:
        cands.append(("tdm", tdm[0], tdm[1]))
    if ok_dir:
        cands.append(("direct", mk_dir, busy_dir))
    mk, _, busy = _pick_min(cands)
    return mk, busy


def _ring_bi_round(ramp_bw, batch, down_cap=None):
    mk, _ = _ring_bi_round_detail(ramp_bw, batch, down_cap=down_cap)
    return mk


def _hybrid_round_detail(ramp_bw, batch, down_cap=None):
    build = lambda s, rb=ramp_bw: S.fp_hybrid_v(s, B_HYBRID_V, True, rb)
    if batch == 1:
        mk, busy, ok = _run_scheme_detail(build, ramp_bw, 1, down_cap=down_cap)
        return (mk, busy) if ok else (None, None)
    if ramp_bw >= 2:
        mk, busy, ok = _run_scheme_detail(build, ramp_bw, batch, down_cap=down_cap)
        return (mk, busy) if ok else (None, None)
    n = MX * MY
    foot = {s: fp_hybrid_v_flits(s, ramp_bw, batch) for s in range(n)}
    tdm = _best_pack_detail(foot, ramp_bw, batch, pack_flits=1, down_cap=down_cap)
    mk_dir, busy_dir, ok_dir = _run_scheme_detail(
        build, ramp_bw, batch, down_cap=down_cap)
    cands = []
    if tdm:
        cands.append(("tdm", tdm[0], tdm[1]))
    if ok_dir:
        cands.append(("direct", mk_dir, busy_dir))
    mk, _, busy = _pick_min(cands)
    return mk, busy


def _hybrid_round(ramp_bw, batch, down_cap=None):
    mk, _ = _hybrid_round_detail(ramp_bw, batch, down_cap=down_cap)
    return mk


def _compose_rounds_detail(round_detail_fn, batches):
    total = 0
    parts = []
    for b in batches:
        mk, busy = round_detail_fn(b)
        if mk is None:
            return None, None
        st = ramp_stats(busy, mk)
        st["_mk"] = mk
        parts.append(st)
        total += mk
    return total, _merge_ramp_stats(parts, total)


def _compose_rounds(round_fn, batches):
    total = 0
    for b in batches:
        mk = round_fn(b)
        if mk is None:
            return None
        total += mk
    return total


def pack_ring_uni(ramp_bw, m, down_cap=None):
    build = lambda s, rb=ramp_bw: S.fp_ring(
        s, S.RING_ORDER, S.RING_POS, False, rb)
    mk1, busy1, ok1 = _run_scheme_detail(build, ramp_bw, 1, down_cap=down_cap)
    cands = []
    if ok1:
        st = ramp_stats(busy1, mk1)
        st["_mk"] = mk1
        parts = [dict(st) for _ in range(m)]
        for p in parts:
            p["_mk"] = mk1
        cands.append((f"{m}×m=1", mk1 * m, _merge_ramp_stats(parts, mk1 * m)))
    mk_d, busy_d, ok_d = _run_scheme_detail(build, ramp_bw, m, down_cap=down_cap)
    if ok_d:
        cands.append(("direct", mk_d, ramp_stats(busy_d, mk_d)))
    mk, lbl, st = _pick_min(cands)
    return mk, lbl, st


def pack_ring_bi(ramp_bw, m, down_cap=None):
    mk1, busy1 = _ring_bi_round_detail(ramp_bw, 1, down_cap=down_cap)
    cands = []
    if mk1 is not None:
        st = ramp_stats(busy1, mk1)
        parts = []
        for _ in range(m):
            p = dict(st)
            p["_mk"] = mk1
            parts.append(p)
        cands.append((f"{m}×m=1", mk1 * m, _merge_ramp_stats(parts, mk1 * m)))
    for batches in batch_plans(m):
        mk, st = _compose_rounds_detail(
            lambda b: _ring_bi_round_detail(ramp_bw, b, down_cap=down_cap),
            batches)
        cands.append((f"batch{batches}", mk, st))
    return _pick_min(cands)


def pack_hybrid_v_bi_B2(ramp_bw, m, down_cap=None):
    mk1, busy1 = _hybrid_round_detail(ramp_bw, 1, down_cap=down_cap)
    cands = []
    if mk1 is not None:
        st = ramp_stats(busy1, mk1)
        parts = []
        for _ in range(m):
            p = dict(st)
            p["_mk"] = mk1
            parts.append(p)
        cands.append((f"{m}×m=1", mk1 * m, _merge_ramp_stats(parts, mk1 * m)))
    for batches in batch_plans(m):
        mk, st = _compose_rounds_detail(
            lambda b: _hybrid_round_detail(ramp_bw, b, down_cap=down_cap),
            batches)
        cands.append((f"batch{batches}", mk, st))
    return _pick_min(cands)


def _border_round_detail(bidir, ramp_bw, batch, down_cap=None):
    build = lambda s, rb=ramp_bw: S.fp_border(s, bidir, rb)
    mk, busy, ok = _run_scheme_detail(build, ramp_bw, batch, down_cap=down_cap)
    return (mk, busy) if ok else (None, None)


def pack_border(bidir, ramp_bw, m, down_cap=None):
    mk1, busy1 = _border_round_detail(bidir, ramp_bw, 1, down_cap=down_cap)
    cands = []
    if mk1 is not None:
        st = ramp_stats(busy1, mk1)
        parts = []
        for _ in range(m):
            p = dict(st)
            p["_mk"] = mk1
            parts.append(p)
        cands.append((f"{m}×m=1", mk1 * m, _merge_ramp_stats(parts, mk1 * m)))
    for batches in batch_plans(m):
        mk, st = _compose_rounds_detail(
            lambda b: _border_round_detail(bidir, ramp_bw, b, down_cap=down_cap),
            batches)
        cands.append((f"batch{batches}", mk, st))
    return _pick_min(cands)


def pack_axis_ccw(ramp_bw, m, down_cap=None):
    mk, busy, ok = _run_scheme_detail(
        S.fp_axis_ccw, ramp_bw, m, down_cap=down_cap)
    if not ok:
        return None, None, None
    return mk, "direct", ramp_stats(busy, mk)


def pack_row_col(ramp_bw, m, down_cap=None):
    S.cfg(MX, 1, S.H, S.V)
    S.init_ring()
    mk1, busy1, ok1 = _run_scheme_detail(
        S.fp_multitree, ramp_bw, m, down_cap=down_cap)
    n1 = MX * 1
    st1 = ramp_stats(busy1, mk1, n=n1) if ok1 else None
    if st1:
        st1["_mk"] = mk1

    S.cfg(1, MY, S.H, S.V)
    S.init_ring()
    mk2, busy2, ok2 = _run_scheme_detail(
        S.fp_multitree, ramp_bw, MX * m, down_cap=down_cap)
    n2 = 1 * MY
    st2 = ramp_stats(busy2, mk2, n=n2) if ok2 else None
    if st2:
        st2["_mk"] = mk2

    if not (ok1 and ok2):
        return None
    total = mk1 + SRAM_TURNAROUND + mk2
    n = MX * MY
    up_tot = st1["up_avg"] * n1 * mk1 + st2["up_avg"] * n2 * mk2
    down_tot = st1["down_avg"] * n1 * mk1 + st2["down_avg"] * n2 * mk2
    st = {
        "up_avg": round(up_tot / (n * total), 4),
        "down_avg": round(down_tot / (n * total), 4),
        "up_peak": max(st1["up_peak"], st2["up_peak"]),
        "down_peak": max(st1["down_peak"], st2["down_peak"]),
    }
    return {
        "T1": mk1,
        "T2": mk2,
        "turnaround": SRAM_TURNAROUND,
        "Ttotal": total,
        "sram": (MX - 1) * m,
        "ramp": st,
    }


def scheme_makespan(name, ramp_bw, m, down_cap=None):
    """Compute makespan (+ ramp stats). If ``down_cap`` is set, also try the
    strict (down_cap=None) schedule and keep the better — any strict schedule
    remains feasible under a larger down-ramp burst budget.
    """
    setup()
    rec = _scheme_makespan_once(name, ramp_bw, m, down_cap=down_cap)
    if down_cap is None:
        return rec
    strict = _scheme_makespan_once(name, ramp_bw, m, down_cap=None)
    if strict["makespan"] is None:
        return rec
    if rec["makespan"] is None or strict["makespan"] <= rec["makespan"]:
        return strict
    return rec


def _scheme_makespan_once(name, ramp_bw, m, down_cap=None):
    if name == "multitree":
        mk, busy, ok = _run_scheme_detail(
            S.fp_multitree, ramp_bw, m, down_cap=down_cap)
        st = ramp_stats(busy, mk) if ok else ramp_stats(None, None)
        return {"makespan": mk if ok else None, "zbuf": ok,
                "strategy": "direct", "ramp": st}
    if name == "axis_ccw":
        mk, lbl, st = pack_axis_ccw(ramp_bw, m, down_cap=down_cap)
        return {"makespan": mk, "zbuf": mk is not None,
                "strategy": lbl, "ramp": st or ramp_stats(None, None)}
    if name == "ring_uni":
        mk, lbl, st = pack_ring_uni(ramp_bw, m, down_cap=down_cap)
        return {"makespan": mk, "zbuf": mk is not None,
                "strategy": lbl, "ramp": st or ramp_stats(None, None)}
    if name == "ring_bi":
        mk, lbl, st = pack_ring_bi(ramp_bw, m, down_cap=down_cap)
        return {"makespan": mk, "zbuf": mk is not None,
                "strategy": lbl, "ramp": st or ramp_stats(None, None)}
    if name == "hybrid_v_bi_B2":
        mk, lbl, st = pack_hybrid_v_bi_B2(ramp_bw, m, down_cap=down_cap)
        return {"makespan": mk, "zbuf": mk is not None,
                "strategy": lbl, "ramp": st or ramp_stats(None, None)}
    if name == "border_uni_Q4":
        mk, lbl, st = pack_border(False, ramp_bw, m, down_cap=down_cap)
        return {"makespan": mk, "zbuf": mk is not None,
                "strategy": lbl, "ramp": st or ramp_stats(None, None)}
    if name == "border_bi_Q4":
        mk, lbl, st = pack_border(True, ramp_bw, m, down_cap=down_cap)
        return {"makespan": mk, "zbuf": mk is not None,
                "strategy": lbl, "ramp": st or ramp_stats(None, None)}
    if name == "row_col":
        rc = pack_row_col(ramp_bw, m, down_cap=down_cap)
        if not rc:
            return {"makespan": None, "zbuf": False, "strategy": None,
                    "ramp": ramp_stats(None, None)}
        return {**rc, "makespan": rc["Ttotal"], "zbuf": True,
                "strategy": "row+col"}
    raise KeyError(name)
