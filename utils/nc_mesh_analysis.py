#!/usr/bin/env python3
"""Network-calculus analysis of collective + dynamic traffic on a 2D-mesh NoC.

Formalization
-------------
* Workload -> arrival curves  alpha(t) = sigma + rho*t   (token bucket)
* Scheduling/routing/arbitration -> service curves beta(t) = R*[t-T]+ (latency-rate)
* Per-queue bounds:   delay h = T + sigma/R,   backlog v = sigma + rho*T
* End-to-end: hop-by-hop SFA with burst propagation (feedforward under XY-DOR),
  wire delays added as constants (H per X hop, V per Y hop, 1 per ramp).

Traffic classes
---------------
* COLL: one collective (broadcast/gather/reduce/allgather/allreduce/alltoall),
  message size m flits/node, invoked every P cycles (period). Per-flow curve at
  injection: (sigma=m, rho=m/P); multicast fork means one flit per tree link.
* BG: uniform-random unicast, per-node injection lambda flit/cycle, packet size
  m_b; decomposed into N*(N-1) flows with (sigma=m_b, rho=lambda/(N-1)).

Designs (service-curve assignment)
----------------------------------
* CAL_SOFT : offline zero-buffer calendar for COLL (buffer 0, delay = makespan
             bound); COLL appears to BG as strict-priority cross traffic with
             per-link (sigma_H = per-invocation link load, rho_H = load/P);
             BG service = leftover beta_L = (C-rho_H)[t - (sigma_H+C)/(C-rho_H)]+.
* TDM_k    : hard slot reservation, BG share 1/k: beta_BG=(C/k, k-1);
             COLL replayed at rate (1-1/k): makespan/(1-1/k)+k, buffer 0.
* PRIO_DYN : no calendar; COLL = high-priority buffered class over XY
             (T_hop = 1 + m_b blocking), BG = leftover of the *unshaped* COLL
             aggregate whose burst grows hop by hop.
* FIFO     : single shared FIFO class, beta=(C,1) per hop, everything aggregated.

Outputs results/nc_mesh_analysis.json + printed tables.
"""

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------- primitives

EPS = 1e-12


class TB:
    """Token-bucket arrival curve alpha(t) = sigma + rho t."""

    __slots__ = ("sigma", "rho")

    def __init__(self, sigma, rho):
        self.sigma, self.rho = float(sigma), float(rho)


class LR:
    """Latency-rate service curve beta(t) = R [t-T]+."""

    __slots__ = ("R", "T")

    def __init__(self, R, T):
        self.R, self.T = float(R), float(T)


def delay_bound(a: TB, b: LR):
    if a.rho > b.R + EPS:
        return math.inf
    return b.T + a.sigma / b.R


def backlog_bound(a: TB, b: LR):
    if a.rho > b.R + EPS:
        return math.inf
    return a.sigma + a.rho * b.T


# Concave piecewise arrival curves: a "piece" is one source-group's curve
# alpha_g(t) = min(sp + C*t, sigma + rho*t); the link aggregate is the sum of
# its pieces (concave, piecewise linear).

def _kinks(pieces):
    ks = {0.0}
    for sp, C, sg, rh in pieces:
        if C > rh + EPS and sg > sp:
            ks.add((sg - sp) / (C - rh))
    return sorted(ks)


def _alpha(pieces, t):
    return sum(min(sp + C * t, sg + rh * t) for (sp, C, sg, rh) in pieces)


def rho_tot(pieces):
    return sum(p[3] for p in pieces)


def delay_concave(pieces, b: LR):
    """Horizontal deviation h(alpha, beta) for concave alpha, beta=R[t-T]+."""
    if not pieces:
        return 0.0
    if rho_tot(pieces) > b.R + EPS or b.R <= EPS:
        return math.inf
    return b.T + max(_alpha(pieces, t) - b.R * t for t in _kinks(pieces)) / b.R


def backlog_concave(pieces, b: LR):
    """Vertical deviation v(alpha, beta); alpha - beta is concave."""
    if not pieces:
        return 0.0
    if rho_tot(pieces) > b.R + EPS or b.R <= EPS:
        return math.inf
    cand = set(_kinks(pieces))
    cand.add(b.T)
    return max(_alpha(pieces, t) - b.R * max(0.0, t - b.T) for t in cand)


def eff_burst(pieces):
    """sigma_eff = sup_t (alpha(t) - rho_tot * t): the burst of the aggregate
    relative to its own long-term rate (used for leftover service)."""
    if not pieces:
        return 0.0
    r = rho_tot(pieces)
    return max(_alpha(pieces, t) - r * t for t in _kinks(pieces))


def leftover(C, T0, hp_sigma, hp_rho):
    """Strict-priority residual service for the low class behind (hp_sigma,hp_rho).

    beta_L(t) = (C-rho_H)[t - (sigma_H + C*T0)/(C-rho_H)]+
    """
    R = C - hp_rho
    if R <= EPS:
        return LR(0.0, math.inf)
    return LR(R, (hp_sigma + C * T0) / R)


# ---------------------------------------------------------------- mesh model


class Mesh:
    def __init__(self, mx, my, h=7, v=9, ramp=1):
        self.mx, self.my, self.h, self.v, self.ramp = mx, my, h, v, ramp
        self.n = mx * my

    def coords(self, i):
        return i % self.mx, i // self.mx

    def nid(self, x, y):
        return y * self.mx + x

    def xy_path_links(self, s, d):
        """Directed mesh links (u,v) of the XY route s->d (X first, then Y)."""
        sx, sy = self.coords(s)
        dx, dy = self.coords(d)
        links, x, y = [], sx, sy
        while x != dx:
            nx = x + (1 if dx > x else -1)
            links.append((self.nid(x, y), self.nid(nx, y)))
            x = nx
        while y != dy:
            ny = y + (1 if dy > y else -1)
            links.append((self.nid(x, y), self.nid(x, ny)))
            y = ny
        return links

    def wire_delay(self, link):
        (u, v) = link
        return self.h if abs(u - v) == 1 else self.v

    def path_wire_delay(self, s, d):
        return sum(self.wire_delay(l) for l in self.xy_path_links(s, d))


# -------------------------------------------------- collective flow patterns
# A pattern is a list of flows; each flow = (src, dst, links, weight) where
# weight = flits carried per invocation on every link of `links` for this flow
# (multicast fork: tree links counted once via union).


def bcast_tree_links(mesh, root):
    links = set()
    for d in range(mesh.n):
        if d != root:
            links.update(mesh.xy_path_links(root, d))
    return links


def pattern_flows(mesh, kind, root, m):
    """Return (flows, inject, eject) for one invocation.

    flows: list of dicts {links: [...], m: flits, src, dst, wire: cycles}
           dst=None for multicast (delivery to farthest leaf).
    inject[node], eject[node]: flits crossing each node's ramp.
    """
    N = mesh.n
    inject = defaultdict(float)
    eject = defaultdict(float)
    flows = []

    def far_wire(src, links):
        # wire delay to the farthest leaf of a tree = max over dests
        return max(
            (mesh.path_wire_delay(src, d) for d in range(N) if d != src),
            default=0,
        )

    if kind == "broadcast":
        links = sorted(bcast_tree_links(mesh, root))
        flows.append(dict(links=links, m=m, src=root, dst=None,
                          wire=far_wire(root, links)))
        inject[root] += m
        for d in range(N):
            if d != root:
                eject[d] += m
    elif kind in ("gather", "reduce"):
        # Tier A: reduce is wire-identical to gather (combine at PEs).
        for s in range(N):
            if s == root:
                continue
            flows.append(dict(links=mesh.xy_path_links(s, root), m=m,
                              src=s, dst=root,
                              wire=mesh.path_wire_delay(s, root)))
            inject[s] += m
            eject[root] += m
    elif kind == "allgather":
        for s in range(N):
            links = sorted(bcast_tree_links(mesh, s))
            flows.append(dict(links=links, m=m, src=s, dst=None,
                              wire=far_wire(s, links)))
            inject[s] += m
            for d in range(N):
                if d != s:
                    eject[d] += m
    elif kind == "allreduce":
        # Tier A: gather phase + broadcast phase (sequential, same invocation).
        fg, ig, eg = pattern_flows(mesh, "gather", root, m)
        fb, ib, eb = pattern_flows(mesh, "broadcast", root, m)
        for k, v in ig.items():
            inject[k] += v
        for k, v in ib.items():
            inject[k] += v
        for k, v in eg.items():
            eject[k] += v
        for k, v in eb.items():
            eject[k] += v
        return fg + fb, inject, eject
    elif kind == "alltoall":
        for s in range(N):
            for d in range(N):
                if s != d:
                    flows.append(dict(links=mesh.xy_path_links(s, d), m=m,
                                      src=s, dst=d,
                                      wire=mesh.path_wire_delay(s, d)))
                    inject[s] += m
                    eject[d] += m
    else:
        raise ValueError(kind)
    return flows, inject, eject


def link_loads(flows):
    load = defaultdict(float)
    for f in flows:
        for l in f["links"]:
            load[l] += f["m"]
    return load


def makespan_bound(mesh, kind, root, m):
    """Lower bound on the conflict-free (calendar) completion time; the repo's
    schedule studies show these bound families are near-achievable, so we use
    max(bounds) as the calendar makespan proxy.
    """
    flows, inject, eject = pattern_flows(mesh, kind, root, m)
    load = link_loads(flows)
    link_lb = max(load.values()) if load else 0.0

    # node-cut bounds: ramp (bw=1 each way) and mesh ingress/egress degree
    ramp_lb = max(
        max(inject.values(), default=0), max(eject.values(), default=0)
    )
    deg_lb = 0.0
    for v in range(mesh.n):
        x, y = mesh.coords(v)
        deg = (x > 0) + (x < mesh.mx - 1) + (y > 0) + (y < mesh.my - 1)
        # everything ejected at v (never locally sourced: a node does not
        # receive its own data) must first cross v's incoming mesh links
        deg_lb = max(deg_lb, eject[v] / deg if deg else 0.0)

    lat_lb = max((f["wire"] + f["m"] - 1 for f in flows), default=0) + 2 * mesh.ramp
    return max(link_lb, ramp_lb, deg_lb, lat_lb), load, flows


# ------------------------------------------------------------ class analysis


def bg_flows(mesh, lam, m_b):
    """Uniform-random unicast: N*(N-1) flows, rho = lam/(N-1), sigma = m_b."""
    N = mesh.n
    rho = lam / (N - 1)
    out = []
    for s in range(N):
        for d in range(N):
            if s != d:
                out.append(dict(links=mesh.xy_path_links(s, d), m=m_b,
                                rho=rho, src=s, dst=d,
                                wire=mesh.path_wire_delay(s, d)))
    return out


def sfa_class(mesh, flows, beta_of_link, n_pass=4):
    """Hop-by-hop SFA for one class of token-bucket flows, with per-input
    peak-rate caps.

    flows: dicts with links, m (=injection sigma), rho, wire.
    beta_of_link: link -> LR (this class's service at that hop).

    Aggregate arrival at link l is grouped by the physical input that feeds l
    (previous link of the flow, or the source ramp for the first hop). Each
    group is physically serialized on a 1 flit/cycle wire, so its curve is
    alpha_g(t) = min(1 + 1*t, sigma_g + rho_g*t): the sum of these concave
    pieces is far tighter than the plain token-bucket sum when many flows
    share a link.

    Burst propagation: sigma_f grows by rho_f * d_hop at each hop, where d_hop
    is the *aggregate* delay bound at that hop (FIFO within the class).
    Feedforward under XY => a few passes reach a fixed point.

    Returns (per-flow e2e delay bounds incl. wire delays, per-link pieces,
    per-link backlog bound).
    """
    pieces_of = {}
    d_hop = defaultdict(float)
    for _ in range(n_pass):
        acc = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
        for f in flows:
            sig = float(f["m"])
            rho = float(f["rho"])
            prev = ("ramp", f.get("src"))
            for l in f["links"]:
                g = acc[l][prev]
                g[0] += sig
                g[1] += rho
                sig += rho * d_hop[l]
                prev = l
        pieces_of = {
            l: [(1.0, 1.0, sg, rh) for (sg, rh) in
                (tuple(v) for v in groups.values())]
            for l, groups in acc.items()
        }
        d_hop = defaultdict(float, {
            l: delay_concave(ps, beta_of_link(l))
            for l, ps in pieces_of.items()
        })

    e2e = []
    for f in flows:
        q = sum(d_hop[l] for l in f["links"])
        e2e.append(q + f["wire"] + 2 * mesh.ramp)
    backlog = {l: backlog_concave(ps, beta_of_link(l))
               for l, ps in pieces_of.items()}
    return e2e, pieces_of, backlog


# ------------------------------------------------------------------ designs


def analyze(mesh, kind, root, m, period, lam, m_b, design, tdm_k=16):
    """Return dict with COLL completion bound, BG max delay bound, max per-port
    buffer bound (flits), and feasibility."""
    C = 1.0
    mk, cload, cflows = makespan_bound(mesh, kind, root, m)
    if period < mk:
        return dict(feasible=False, reason="period < makespan")
    bflows = bg_flows(mesh, lam, m_b)

    res = dict(feasible=True, design=design, collective=kind, m=m,
               period=period, lam=lam, makespan_lb=mk)

    if design in ("CAL_SOFT", "CAL_SHAPED"):
        # COLL: zero-buffer calendar (buffer 0 in both variants).
        # CAL_SOFT  : compact schedule, completion = makespan; a bottleneck
        #             link may be solid-busy for its whole load -> BG sees a
        #             burst sigma_H = L per period.
        # CAL_SHAPED: calendar dilated over the whole period (slots evenly
        #             interleaved); link busy in any interval [s,s+t] is
        #             <= 2m + (L/P) t, so sigma_H = min(L, 2m) — but the
        #             collective itself now completes only at ~P.
        res["coll_delay"] = mk if design == "CAL_SOFT" else period
        res["coll_buf"] = 0.0
        def beta_bg(l):
            L = cload.get(l, 0.0)
            sig = L if design == "CAL_SOFT" else min(L, 2.0 * m)
            return leftover(C, 1.0, sig, L / period)
        e2e, _, bl = sfa_class(mesh, bflows, beta_bg)
        res["bg_delay"] = max(e2e, default=0.0)
        res["bg_buf"] = max(bl.values(), default=0.0)
        res["max_port_buf"] = res["bg_buf"]

    elif design == "TDM":
        # adaptive split: reserve just enough evenly-interleaved slots for the
        # calendar's bottleneck rate, the rest belongs to BG.
        k = tdm_k
        f_c = max(cload.values()) / period if cload else 0.0
        nc_slots = max(1, math.ceil(f_c * k))
        nb = k - nc_slots
        if nb <= 0:
            return dict(feasible=False, reason="no BG slots", design=design,
                        collective=kind, f_c=f_c)
        # evenly interleaved: max wait for an own-class slot ~ ceil(k/slots)
        res["coll_delay"] = mk / (nc_slots / k) + math.ceil(k / nc_slots)
        res["coll_buf"] = 0.0
        beta = LR(C * nb / k, math.ceil(k / nb))
        e2e, _, bl = sfa_class(mesh, bflows, lambda l: beta)
        res["bg_delay"] = max(e2e, default=0.0)
        res["bg_buf"] = max(bl.values(), default=0.0)
        res["max_port_buf"] = res["bg_buf"]
        res["tdm_coll_slots"] = nc_slots

    elif design == "PRIO_DYN":
        # COLL is a buffered HP class: T_hop = 1 (arb) + m_b (LP blocking)
        cfl = [dict(links=list(f["links"]), m=f["m"], rho=f["m"] / period,
                    wire=f["wire"], src=f.get("src")) for f in cflows]
        beta_hp = lambda l: LR(C, 1.0 + m_b)
        c_e2e, c_pieces, c_bl = sfa_class(mesh, cfl, beta_hp)
        res["coll_delay"] = max(c_e2e, default=0.0)
        res["coll_buf"] = max(c_bl.values(), default=0.0)

        def beta_bg(l):
            ps = c_pieces.get(l)
            if not ps:
                return LR(C, 1.0)
            return leftover(C, 1.0, eff_burst(ps), rho_tot(ps))
        e2e, _, bl = sfa_class(mesh, bflows, beta_bg)
        res["bg_delay"] = max(e2e, default=0.0)
        res["bg_buf"] = max(bl.values(), default=0.0)
        res["max_port_buf"] = max(
            max(bl.values(), default=0.0) + 0.0,
            res["coll_buf"],
        )
        # same physical port carries both classes -> port buffer = sum
        res["max_port_buf"] = max(
            (c_bl.get(l, 0.0) + bl.get(l, 0.0))
            for l in set(c_bl) | set(bl)
        ) if (c_bl or bl) else 0.0

    elif design == "FIFO":
        allf = [dict(links=list(f["links"]), m=f["m"], rho=f["m"] / period,
                     wire=f["wire"], src=f.get("src")) for f in cflows]
        allf += [dict(links=f["links"], m=f["m"], rho=f["rho"],
                      wire=f["wire"]) for f in bflows]
        beta = lambda l: LR(C, 1.0)
        e2e, _, bl = sfa_class(mesh, allf, beta)
        nc = len(cflows)
        res["coll_delay"] = max(e2e[:nc], default=0.0)
        res["bg_delay"] = max(e2e[nc:], default=0.0)
        res["coll_buf"] = res["bg_buf"] = None
        res["max_port_buf"] = max(bl.values(), default=0.0)
    else:
        raise ValueError(design)

    for k_ in ("coll_delay", "bg_delay", "max_port_buf"):
        if res.get(k_) is not None and not math.isfinite(res[k_]):
            res["feasible"] = False
            res["reason"] = f"unstable ({k_})"
    return res


# -------------------------------------------------------------------- sweep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mx", type=int, default=6)
    ap.add_argument("--my", type=int, default=8)
    ap.add_argument("--h", type=int, default=7)
    ap.add_argument("--v", type=int, default=9)
    ap.add_argument("--m", type=int, default=4, help="collective msg flits")
    ap.add_argument("--mb", type=int, default=4, help="BG packet flits")
    ap.add_argument("--root", type=int, default=6, help="root node id")
    ap.add_argument("--tdm-k", type=int, default=16)
    ap.add_argument("--out", default=str(ROOT / "results" / "nc_mesh_analysis.json"))
    args = ap.parse_args()

    mesh = Mesh(args.mx, args.my, args.h, args.v)
    kinds = ["broadcast", "gather", "reduce", "allgather", "allreduce", "alltoall"]
    designs = ["CAL_SOFT", "CAL_SHAPED", "TDM", "PRIO_DYN", "FIFO"]
    f_cs = [0.05, 0.1, 0.2, 0.4, 0.6, 0.8]     # collective bottleneck util
    lams = [0.02, 0.05, 0.1, 0.2, 0.3]         # BG per-node injection

    # pattern structure table
    patterns = {}
    for kind in kinds:
        mk, load, flows = makespan_bound(mesh, kind, args.root, args.m)
        hot = max(load.items(), key=lambda kv: kv[1]) if load else (None, 0)
        patterns[kind] = dict(
            makespan_lb=mk,
            max_link_load=hot[1],
            hot_link=hot[0],
            n_flows=len(flows),
            total_link_flits=sum(load.values()),
        )

    rows = []
    for kind in kinds:
        mk = patterns[kind]["makespan_lb"]
        Lmax = patterns[kind]["max_link_load"]
        for f_c in f_cs:
            period = max(Lmax / f_c, mk)
            f_c_eff = Lmax / period
            for lam in lams:
                for d in designs:
                    r = analyze(mesh, kind, args.root, args.m, period,
                                lam, args.mb, d, args.tdm_k)
                    r.update(f_c=round(f_c_eff, 4), f_c_req=f_c, lam=lam)
                    rows.append(r)

    out = dict(
        mesh=dict(mx=args.mx, my=args.my, h=args.h, v=args.v),
        m=args.m, mb=args.mb, root=args.root, tdm_k=args.tdm_k,
        patterns=patterns, rows=rows,
    )
    Path(args.out).write_text(json.dumps(out, indent=1))
    print(f"wrote {args.out}  ({len(rows)} rows)")

    # ------- console summary: pattern structure
    print("\n=== pattern structure (m=%d, root=%d) ===" % (args.m, args.root))
    print(f"{'kind':10s} {'makespan_lb':>12s} {'max_link_load':>14s} {'hot_link':>14s} {'flows':>6s}")
    for kind in kinds:
        p = patterns[kind]
        print(f"{kind:10s} {p['makespan_lb']:12.0f} {p['max_link_load']:14.0f} "
              f"{str(p['hot_link']):>14s} {p['n_flows']:6d}")


if __name__ == "__main__":
    main()
