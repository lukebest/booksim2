#!/usr/bin/env python3
"""Zero router-buffer ring scheduler that EXPLOITS link time-division.

Model (H=4, V=6): a directed link of latency L is pipelined -- it can launch a
new flit every cycle (cap 1/cycle on the SEND slot) while up to L flits are in
flight.  So a region-internal ring link has free send-slots on most cycles, and
a flit arriving from another quadrant via the AFIFO can be slotted into one of
those free send-cycles instead of needing a router buffer.

Decision structure
------------------
Every source's delivery tree splits into RIGID sub-trees (maximal pieces that use
only intra-region links) joined by CROSS edges (the AFIFO border links):

  * intra link (p->c)  : RIGID, send(p,c) = arrive(p)            -> 0 router buffer
  * cross link (p->c)  : FLEXIBLE, send >= arrive(p); the wait happens IN THE
                         AFIFO (allowed), then the sub-tree rooted at c starts.

We place sub-trees on a global (link, cycle) calendar (cap 1/send-cycle) and a
down-ramp calendar (cap = ramp_bw arrivals / node / cycle, i.e. eject buffer 0).
A sub-tree is committed only at an anchor time where its ENTIRE rigid footprint
is conflict-free -> ring_buf == 0 and eject_buf == 0 by construction.

Two passes so the four home rings stay clean conveyors:
  Pass 1: place every source's HOME sub-tree (the 4 rings are conflict-free at
          offset 0 -- the proven 0-buffer primitive).
  Pass 2: place every FOREIGN sub-tree, searching the cross-send (AFIFO wait)
          for the earliest free slot + conflict-free downstream footprint.

Reports makespan, max AFIFO queue depth (peak flits waiting on one border link),
max injection offset, and feasibility (ring_buf=0, eject_buf=0 always hold by
construction; we assert every node ejects N-1).
"""

import argparse
from collections import defaultdict, deque

import sim_fused_rings as fr


def classify_subtrees(ch, s):
    """Return list of sub-trees. Each: dict(root, is_source, links[(lk,rel)],
    ejects[(node,rel)], arrive_rel{node:rel}, crosses[(p,c,lat)]).
    Sub-tree roots are s and every cross-edge destination."""
    cross_dsts = []
    for p, kids in ch.items():
        for c in kids:
            if fr.quad_of(p) != fr.quad_of(c):
                cross_dsts.append((p, c))
    roots = [(s, True)] + [(c, False) for (_, c) in cross_dsts]

    subtrees = {}
    for root, is_src in roots:
        arrive_rel = {root: 0}
        links, ejects, crosses = [], [], []
        if not is_src:
            ejects.append((root, 0))  # cross destination ejects on arrival
        dq = deque([root])
        while dq:
            p = dq.popleft()
            for c in ch.get(p, []):
                lat = fr.edge_lat(p, c)
                if fr.quad_of(p) != fr.quad_of(c):
                    crosses.append((p, c, lat))      # boundary: separate sub-tree
                    continue
                send_rel = arrive_rel[p]
                a_rel = send_rel + lat
                arrive_rel[c] = a_rel
                links.append((p * 100000 + c, send_rel))
                ejects.append((c, a_rel))
                dq.append(c)
        subtrees[root] = dict(root=root, is_source=is_src, links=links,
                              ejects=ejects, arrive_rel=arrive_rel, crosses=crosses)
    return subtrees, cross_dsts


class Calendar:
    def __init__(self, ramp_bw):
        self.link = defaultdict(set)        # lk -> set(send cycles)
        self.eject = defaultdict(lambda: defaultdict(int))  # node -> cycle -> count
        self.ramp_bw = ramp_bw

    def fits(self, links, ejects, T):
        for lk, rel in links:
            if (T + rel) in self.link[lk]:
                return False
        for nd, rel in ejects:
            if self.eject[nd][T + rel] >= self.ramp_bw:
                return False
        return True

    def commit(self, links, ejects, T):
        for lk, rel in links:
            self.link[lk].add(T + rel)
        last = 0
        for nd, rel in ejects:
            self.eject[nd][T + rel] += 1
            last = max(last, T + rel)
        return last

    def cross_send_free(self, lk, t):
        while t in self.link[lk]:
            t += 1
        return t


def cross_lb_groups(sz):
    """Eight parallel AFIFO links per adjacent-quad direction (LB pool)."""
    hw = sz // 2
    groups = {}
    pair_to_group = {}
    specs = [
        ("Hdn02", [(fr.nid(x, hw), fr.nid(x, hw - 1)) for x in range(hw)]),
        ("Hup20", [(fr.nid(x, hw - 1), fr.nid(x, hw)) for x in range(hw)]),
        ("Hdn13", [(fr.nid(x, hw), fr.nid(x, hw - 1)) for x in range(hw, sz)]),
        ("Hup31", [(fr.nid(x, hw - 1), fr.nid(x, hw)) for x in range(hw, sz)]),
        ("Vrt01", [(fr.nid(hw - 1, y), fr.nid(hw, y)) for y in range(hw)]),
        ("Vlf10", [(fr.nid(hw, y), fr.nid(hw - 1, y)) for y in range(hw)]),
        ("Vrt23", [(fr.nid(hw - 1, y), fr.nid(hw, y)) for y in range(hw, sz)]),
        ("Vlf32", [(fr.nid(hw, y), fr.nid(hw - 1, y)) for y in range(hw, sz)]),
    ]
    for name, links in specs:
        groups[name] = links
        for lk in links:
            pair_to_group[lk] = name
    return groups, pair_to_group


def afifo_profile(afifo_intervals, mx, makespan, top_n=3):
    """Per-cycle AFIFO queue depth: global max + top-N busiest border links."""
    mk = makespan + 1
    global_d = [0] * mk
    ranked = []
    for lk, ivs in afifo_intervals.items():
        diff = [0] * (mk + 1)
        for ap, cs in ivs:
            if cs > ap:
                diff[ap] += 1
                if cs < mk:
                    diff[cs] -= 1
                else:
                    diff[mk] -= 1
        d = 0
        curve = [0] * mk
        peak = 0
        peak_cy = 0
        for t in range(mk):
            d += diff[t]
            curve[t] = d
            if d > peak:
                peak = d
                peak_cy = t
            global_d[t] = max(global_d[t], d)
        p, c = divmod(lk, 100000)
        ranked.append((peak, lk, curve, peak_cy, p, c))
    ranked.sort(reverse=True)
    g_peak = max(global_d) if global_d else 0
    g_peak_cy = global_d.index(g_peak) if g_peak else 0

    def link_entry(peak, lk, curve, peak_cy, p, c):
        return {
            "label": f"({p % mx},{p // mx})→({c % mx},{c // mx})",
            "p": p, "c": c,
            "peak": peak, "peak_cy": peak_cy,
            "curve": curve,
        }

    worst = link_entry(*ranked[0]) if ranked else None
    top = [link_entry(*r) for r in ranked[1:1 + top_n]]
    return {
        "global": global_d,
        "peak": g_peak,
        "peak_cy": g_peak_cy,
        "worst": worst,
        "top": top,
    }


def afifo_profile_balanced(afifo_intervals, sz, makespan):
    """Theoretical LB: per-cycle water-fill across 8 parallel links per direction."""
    mk = makespan + 1
    _, pair_to_group = cross_lb_groups(sz)
    by_group = defaultdict(list)
    for lk, ivs in afifo_intervals.items():
        p, c = divmod(lk, 100000)
        g = pair_to_group.get((p, c))
        if g:
            by_group[g].append((lk, ivs))
    global_d = [0] * mk
    worst_peak = 0
    worst_lk = None
    for g, items in by_group.items():
        n = len(items)
        if not n:
            continue
        curves = []
        for lk, ivs in items:
            diff = [0] * (mk + 1)
            for ap, cs in ivs:
                if cs > ap:
                    diff[ap] += 1
                    if cs < mk:
                        diff[cs] -= 1
                    else:
                        diff[mk] -= 1
            d = 0
            cur = []
            for t in range(mk):
                d += diff[t]
                cur.append(d)
            curves.append(cur)
        for t in range(mk):
            s = sum(curves[i][t] for i in range(n))
            bal = -(s // -n)
            global_d[t] = max(global_d[t], bal)
            if bal > worst_peak:
                worst_peak = bal
    g_peak = max(global_d) if global_d else 0
    g_peak_cy = global_d.index(g_peak) if g_peak else 0
    return {"global": global_d, "peak": g_peak, "peak_cy": g_peak_cy}


def schedule(sz, bidir, ramp_bw, deliv_fn, off_limit=20000, spread=0,
             record_events=False, quads=None, lb_cross=False):
    fr.cfg(sz, sz, 4, 6)
    n = sz * sz
    deliveries = {s: deliv_fn(s, bidir) for s in range(n)}
    sub = {s: classify_subtrees(deliveries[s], s)[0] for s in range(n)}
    if quads is None:
        quads, _ = fr.quad_setup()
    home_idx = {}
    for s in range(n):
        order = quads[fr.quad_of(s)]["order"]
        home_idx[s] = order.index(s)

    lb_groups, lb_pair = (cross_lb_groups(sz) if lb_cross else (None, None))

    cal = Calendar(ramp_bw)
    makespan = 0
    max_off = 0
    afifo_intervals = defaultdict(list)   # lk -> [(start_wait, end_wait)]
    arrive_abs = {}                        # (s, root, node) -> absolute arrive cycle
    events = []

    def afifo_q(lk, t):
        return sum(1 for ap, cs in afifo_intervals[lk] if ap <= t < cs)

    def pick_cross(s, owner_root, p, c, lat, placed_roots):
        """Pick among 8 parallel AFIFOs (unused foreign sub-tree root, min queue)."""
        if not lb_cross:
            return p, c
        gname = lb_pair.get((p, c))
        if not gname:
            return p, c
        best = None
        for pp, cc in lb_groups[gname]:
            if cc in placed_roots or cc not in sub[s]:
                continue
            ap2 = arrive_abs.get((s, owner_root, pp))
            if ap2 is None:
                continue
            lk = pp * 100000 + cc
            cs = cal.cross_send_free(lk, ap2)
            q = afifo_q(lk, cs)
            score = (q, cs)
            if best is None or score < best[0]:
                best = (score, pp, cc)
        if best:
            return best[1], best[2]
        return p, c

    def place(s, root, anchor):
        st = sub[s][root]
        last = cal.commit(st["links"], st["ejects"], anchor)
        for nd, rel in st["arrive_rel"].items():
            arrive_abs[(s, root, nd)] = anchor + rel
        if record_events:
            for lk, rel in st["links"]:
                p, c = divmod(lk, 100000)
                snd = anchor + rel
                lt = fr.edge_lat(p, c)
                events.append((s, p, c, snd, lt, snd + lt, hop_kind(s, p, c)))
        return last, st

    # ---- Pass 1: home sub-trees (rooted at s) ----
    # `spread` biases each source's start time so crossings de-burst -> the
    # AFIFO needs less depth (at the cost of a longer makespan).
    for s in range(n):
        st = sub[s][s]
        off = spread * home_idx[s]
        while off < off_limit and not cal.fits(st["links"], st["ejects"], fr.RAMP + off):
            off += 1
        anchor = fr.RAMP + off
        max_off = max(max_off, off)
        last, _ = place(s, s, anchor)
        makespan = max(makespan, last + fr.RAMP)

    # ---- Pass 2: foreign sub-trees, BFS over cross edges per source ----
    for s in range(n):
        # cross edges become available once their parent sub-tree is placed.
        pending = deque()
        # seed from the home sub-tree's crosses
        for (p, c, lat) in sub[s][s]["crosses"]:
            pending.append((s, p, c, lat))
        placed_roots = {s}
        batch = list(pending)
        pending.clear()
        batch.sort(key=lambda x: arrive_abs.get((s, x[0], x[1]), 0), reverse=lb_cross)
        pending.extend(batch)
        while pending:
            owner_root, p, c, lat = pending.popleft()
            ap = arrive_abs[(s, owner_root, p)]
            p, c = pick_cross(s, owner_root, p, c, lat, placed_roots)
            ap = arrive_abs[(s, owner_root, p)]
            st = sub[s][c]
            llat = fr.edge_lat(p, c)
            cs = ap
            while True:
                cs = cal.cross_send_free(p * 100000 + c, cs)   # free AFIFO send slot
                anchor = cs + llat
                if cal.fits(st["links"], st["ejects"], anchor):
                    break
                cs += 1
                if cs - ap > off_limit:
                    return dict(ok=False)
            cal.link[p * 100000 + c].add(cs)                   # reserve cross link
            afifo_intervals[p * 100000 + c].append((ap, cs))   # waited in AFIFO
            if record_events:
                events.append((s, p, c, cs, llat, cs + llat, 2))
            last, _ = place(s, c, anchor)
            makespan = max(makespan, last + fr.RAMP)
            placed_roots.add(c)
            for (pp, cc, llat) in st["crosses"]:
                if cc not in placed_roots:
                    pending.append((c, pp, cc, llat))

    # AFIFO queue depth = peak overlap of wait intervals on any one border link
    def peak(ivs):
        ev = []
        for a, b in ivs:
            if b > a:
                ev += [(a, 1), (b, -1)]
        ev.sort()
        cur = m = 0
        for _, d in ev:
            cur += d
            m = max(m, cur)
        return m
    afifo_depth = max((peak(v) for v in afifo_intervals.values()), default=0)

    # verify every node ejects exactly n-1
    ej_total = defaultdict(int)
    for nd, cyc in cal.eject.items():
        ej_total[nd] = sum(cyc.values())
    ok = all(ej_total[nd] == n - 1 for nd in range(n))
    out = dict(ok=ok, makespan=makespan, afifo_depth=afifo_depth,
               max_inject_off=max_off, ramp_bw=ramp_bw,
               afifo_profile=afifo_profile(afifo_intervals, sz, makespan),
               afifo_balanced=afifo_profile_balanced(afifo_intervals, sz, makespan))
    if record_events:
        out["events"] = events
    return out


# ---- delivery builders -----------------------------------------------------
def deliv_border(s, bidir):
    return fr.build_border_delivery(s, bidir)


def deliv_border_quads(s, bidir, quads):
    """Border short-arc; local lap uses the given per-quadrant Hamilton orders."""
    qi = fr.quad_of(s)
    ch = defaultdict(list)
    fr.add_ring_chain(ch, quads[qi]["order"], s, bidir)
    hw, hh = fr._MX // 2, fr._MY // 2
    sx, sy = fr.coord(s)
    qx = qi % 2
    qy = qi // 2
    qx0, qy0 = qx * hw, qy * hh
    if qx == 0:
        bxQ, arc_xs = hw - 1, list(range(hw, 2 * hw))
    else:
        bxQ, arc_xs = hw, list(range(hw - 1, -1, -1))
    for y in range(qy0, qy0 + hh):
        ch[fr.nid(bxQ, y)].append(fr.nid(arc_xs[0], y))
        for k in range(len(arc_xs) - 1):
            ch[fr.nid(arc_xs[k], y)].append(fr.nid(arc_xs[k + 1], y))
    if qy == 0:
        byQ, arc_ys = hh - 1, list(range(hh, 2 * hh))
    else:
        byQ, arc_ys = hh, list(range(hh - 1, -1, -1))
    for x in range(qx0, qx0 + hw):
        ch[fr.nid(x, byQ)].append(fr.nid(x, arc_ys[0]))
        for k in range(len(arc_ys) - 1):
            ch[fr.nid(x, arc_ys[k])].append(fr.nid(x, arc_ys[k + 1]))
    for x in arc_xs:
        ch[fr.nid(x, byQ)].append(fr.nid(x, arc_ys[0]))
        for k in range(len(arc_ys) - 1):
            ch[fr.nid(x, arc_ys[k])].append(fr.nid(x, arc_ys[k + 1]))
    return ch


def deliv_ringfollow(s, bidir):
    quads, _ = fr.quad_setup()
    return deliv_ringfollow_quads(s, bidir, quads)


def deliv_ringfollow_quads(s, bidir, quads):
    sz = fr._MX
    hw, hh = sz // 2, sz // 2
    qi = fr.quad_of(s)
    qx0, qy0 = (qi % 2) * hw, (qi // 2) * hh
    sx, sy = fr.coord(s)
    ch = defaultdict(list)
    fr.add_ring_chain(ch, quads[qi]["order"], s, bidir)
    home_bx, far_bx = (hw - 1, hw) if qx0 == 0 else (hw, hw - 1)
    home_by, far_by = (hh - 1, hh) if qy0 == 0 else (hh, hh - 1)
    ch[fr.nid(home_bx, sy)].append(fr.nid(far_bx, sy))
    fr.add_ring_chain(ch, quads[qi ^ 1]["order"], fr.nid(far_bx, sy), bidir)
    ch[fr.nid(sx, home_by)].append(fr.nid(sx, far_by))
    fr.add_ring_chain(ch, quads[qi ^ 2]["order"], fr.nid(sx, far_by), bidir)
    xcross = (qx0 + hw) % sz + (sx - qx0)
    ch[fr.nid(xcross, home_by)].append(fr.nid(xcross, far_by))
    fr.add_ring_chain(ch, quads[qi ^ 3]["order"], fr.nid(xcross, far_by), bidir)
    return ch


def hop_kind(s, p, c):
    if fr.quad_of(p) != fr.quad_of(c):
        return 2  # cross / AFIFO
    return 0 if fr.quad_of(p) == fr.quad_of(s) else 1  # home ring vs foreign


def schedule_atomic(sz, bidir, ramp_bw, deliv_fn, afifo_cap=None,
                    order="interleave", off_limit=20000, record_events=False):
    """Per-source atomic placement with a global AFIFO-occupancy calendar.

    Each source is placed (home rigid sub-tree + all foreign sub-trees) at the
    smallest injection offset for which the WHOLE source is conflict-free
    (ring_buf=0), eject<=ramp_bw (eject_buf=0) AND every border AFIFO stays
    <= afifo_cap.  If a crossing would need to wait deeper than afifo_cap, the
    source's injection is bumped (pacing the home conveyor so flits reach the
    border no faster than the destination ring can drain them)."""
    fr.cfg(sz, sz, 4, 6)
    n = sz * sz
    deliveries = {s: deliv_fn(s, bidir) for s in range(n)}
    sub = {s: classify_subtrees(deliveries[s], s)[0] for s in range(n)}
    quads, _ = fr.quad_setup()
    home_idx = {s: quads[fr.quad_of(s)]["order"].index(s) for s in range(n)}

    if order == "interleave":
        srcs = sorted(range(n), key=lambda s: (home_idx[s], fr.quad_of(s)))
    elif order == "natural":
        srcs = list(range(n))
    else:
        srcs = sorted(range(n), key=lambda s: (fr.quad_of(s), home_idx[s]))

    link_busy = defaultdict(set)
    eject_busy = defaultdict(lambda: defaultdict(int))
    afifo_occ = defaultdict(lambda: defaultdict(int))
    afifo_intervals = defaultdict(list)
    events = []
    makespan = 0
    max_off = 0

    def fits(st, anchor, tlink, teject):
        for lk, rel in st["links"]:
            t = anchor + rel
            if t in link_busy[lk] or t in tlink.get(lk, ()):
                return False
        for nd, rel in st["ejects"]:
            t = anchor + rel
            if eject_busy[nd][t] + teject.get((nd, t), 0) >= ramp_bw:
                return False
        return True

    def try_source(s, off):
        anchor0 = fr.RAMP + off
        tlink = defaultdict(set)
        teject = defaultdict(int)
        tafifo = defaultdict(int)        # (lk,cyc) -> waiting flits added by this source
        af_ivs = []                      # (lk, ap, cs)
        arrive = {}
        evs = []
        home = sub[s][s]
        if not fits(home, anchor0, tlink, teject):
            return None
        # tentatively record home
        for lk, rel in home["links"]:
            tlink[lk].add(anchor0 + rel)
        for nd, rel in home["ejects"]:
            teject[(nd, anchor0 + rel)] += 1
        for nd, rel in home["arrive_rel"].items():
            arrive[(s, nd)] = anchor0 + rel
        if record_events:
            for lk, rel in home["links"]:
                p, c = divmod(lk, 100000)
                snd = anchor0 + rel
                lt = fr.edge_lat(p, c)
                evs.append((s, p, c, snd, lt, snd + lt, hop_kind(s, p, c)))
        pending = deque((s, p, c, lat) for (p, c, lat) in home["crosses"])
        placed = {s}
        while pending:
            owner, p, c, lat = pending.popleft()
            ap = arrive[(owner, p)]
            lk = p * 100000 + c
            st = sub[s][c]
            cs = ap
            while True:
                while cs in link_busy[lk] or cs in tlink.get(lk, ()):
                    cs += 1
                anchor_c = cs + lat
                if fits(st, anchor_c, tlink, teject):
                    # AFIFO occupancy on [ap, cs)
                    if afifo_cap is not None:
                        bad = any(afifo_occ[lk][t] + tafifo[(lk, t)] + 1 > afifo_cap
                                  for t in range(ap, cs))
                        if bad:
                            return None  # waiting too deep -> bump injection
                    break
                cs += 1
                if cs - ap > off_limit:
                    return None
            # accept child sub-tree
            tlink[lk].add(cs)
            for t in range(ap, cs):
                tafifo[(lk, t)] += 1
            af_ivs.append((lk, ap, cs))
            for lk2, rel in st["links"]:
                tlink[lk2].add(anchor_c + rel)
            for nd, rel in st["ejects"]:
                teject[(nd, anchor_c + rel)] += 1
            for nd, rel in st["arrive_rel"].items():
                arrive[(c, nd)] = anchor_c + rel
            if record_events:
                evs.append((s, p, c, cs, lat, cs + lat, 2))
                for lk2, rel in st["links"]:
                    p2, c2 = divmod(lk2, 100000)
                    snd = anchor_c + rel
                    lt = fr.edge_lat(p2, c2)
                    evs.append((s, p2, c2, snd, lt, snd + lt, hop_kind(s, p2, c2)))
            placed.add(c)
            for (pp, cc, llat) in st["crosses"]:
                if cc not in placed:
                    pending.append((c, pp, cc, llat))
        return dict(tlink=tlink, teject=teject, tafifo=tafifo, af_ivs=af_ivs,
                    arrive=arrive, evs=evs)

    for s in srcs:
        off = 0
        res = None
        while off < off_limit:
            res = try_source(s, off)
            if res is not None:
                break
            off += 1
        if res is None:
            return dict(ok=False)
        max_off = max(max_off, off)
        for lk, cycset in res["tlink"].items():
            link_busy[lk] |= cycset
        for (nd, t), cnt in res["teject"].items():
            eject_busy[nd][t] += cnt
            makespan = max(makespan, t + fr.RAMP)
        for (lk, t), cnt in res["tafifo"].items():
            afifo_occ[lk][t] += cnt
        for (lk, ap, cs) in res["af_ivs"]:
            afifo_intervals[lk].append((ap, cs))
        if record_events:
            events.extend(res["evs"])

    def peak(ivs):
        ev = []
        for a, b in ivs:
            if b > a:
                ev += [(a, 1), (b, -1)]
        ev.sort()
        cur = m = 0
        for _, d in ev:
            cur += d
            m = max(m, cur)
        return m
    afifo_depth = max((peak(v) for v in afifo_intervals.values()), default=0)
    ej_total = defaultdict(int)
    for nd, cyc in eject_busy.items():
        ej_total[nd] = sum(cyc.values())
    ok = all(ej_total[nd] == n - 1 for nd in range(n))
    out = dict(ok=ok, makespan=makespan, afifo_depth=afifo_depth,
               max_inject_off=max_off, ramp_bw=ramp_bw,
               afifo_profile=afifo_profile(afifo_intervals, sz, makespan),
               afifo_balanced=afifo_profile_balanced(afifo_intervals, sz, makespan))
    if record_events:
        out["events"] = events
    return out


def sweep(sz, deliv_fn, name):
    print(f"---- {name} {sz}x{sz} bidir, ramp=2: makespan vs AFIFO depth (inject spread) ----")
    print(f"{'spread':>6s} {'makespan':>8s} {'afifo_depth':>11s} {'max_inj_off':>11s}")
    for sp in (0, 1, 2, 3, 4, 6, 8, 12):
        r = schedule(sz, True, 2, deliv_fn, spread=sp)
        if r["ok"]:
            print(f"{sp:>6d} {r['makespan']:8d} {r['afifo_depth']:11d} {r['max_inject_off']:11d}")


def deliv_global(s, bidir):
    """One Hamilton ring over the whole mesh (crosses quad borders many times)."""
    order = fr.ham_cycle_rect(0, 0, fr._MX, fr._MY)
    ch = defaultdict(list)
    fr.add_ring_chain(ch, order, s, bidir)
    return ch


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", type=int, nargs="+", default=[8, 16])
    ap.add_argument("--sweep", action="store_true")
    args = ap.parse_args()
    if args.sweep:
        for sz in args.sizes:
            sweep(sz, deliv_border, "border")
            print()
            sweep(sz, deliv_ringfollow, "ringfollow")
            print()
        return
    builders = {"border": deliv_border, "ringfollow": deliv_ringfollow}
    print("0-buffer (ring_buf=0, eject_buf=0) schedules with AFIFO time-division insertion")
    print("eject bandwidth = 2 flit/cy (bidirectional)\n")
    for sz in args.sizes:
        n = sz * sz
        lb = (n - 1 + 1) // 2
        print(f"==== {sz}x{sz} (N={n}, eject LB @bw2 = {lb}) ====")
        print(f"{'scheme':11s} {'dir':3s} {'ramp':>4s} {'makespan':>8s} "
              f"{'afifo_depth':>11s} {'max_inj_off':>11s} {'feasible':>8s}")
        for name, fn in builders.items():
            for bidir, rb in ((False, 1), (True, 2)):
                r = schedule(sz, bidir, rb, fn)
                tag = "bi " if bidir else "uni"
                if r["ok"]:
                    print(f"{name:11s} {tag:3s} {rb:>4d} {r['makespan']:8d} "
                          f"{r['afifo_depth']:11d} {r['max_inject_off']:11d} {'YES':>8s}")
                else:
                    print(f"{name:11s} {tag:3s} {rb:>4d} {'-':>8s} {'-':>11s} {'-':>11s} {'NO':>8s}")
        print()


if __name__ == "__main__":
    main()
