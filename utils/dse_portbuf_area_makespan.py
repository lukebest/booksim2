#!/usr/bin/env python3
"""Port-buffered router: dynamic-arbitration allgather DES + area Pareto.

The rigid 0-buffer schedule requires exact per-source inject offsets and a
per-router slot table (Pmax entries).  If each router input port instead has a
small FIFO of depth Q, the network can run WITHOUT a cycle-exact calendar:

  * write control  : a link delivers <=1 flit/cy into its dedicated in-port
    FIFO (single write port, no arbitration on write); overflow is impossible
    because the upstream sender spends a credit (init = Q) per send and gets
    it back when the flit vacates the FIFO (credit return latency = link
    latency).
  * read/scheduling: the head-of-line flit of each in-port FIFO looks up its
    source id in a route LUT (N entries x 5-bit out-mask), bids for the output
    ports in the mask; each output port has an oldest-first arbiter; a flit
    may win several ports in one cycle (CalFork-style fork) and dequeues only
    when its whole mask is served.

This script simulates that router cycle-accurately (store-and-forward /
cut-through when uncontended, credit RTT throttling, HOL blocking, finite
eject path W/E/B identical to the rigid model) for all 7 tree schemes and
Q in {1,2,4,8}, prices the port FIFOs into the area model, and merges the
resulting points with the rigid-calendar points of multi_area_makespan.json
into one Pareto front.

Key physics: a link with latency L needs Q >= 2L+1 credits for full rate;
with Q=1 a heavily reused link runs at ~1/(2L) flits/cy, so small-Q dynamic
routing pays a large makespan penalty on 8x6 with H=7/V=9.
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import ppa_analytic_model as PPA
import sched_zerobuf_compare as S
from dse_axis_area_makespan import A_FLIT, area_parts, pareto
from dse_multi_area_makespan import SCHEMES
from dse_tree_allgather_6x8 import MX, MY, H, V, N, RAMP, coord, nid


def setup() -> None:
    S.cfg(MX, MY, H, V)
    S.init_ring()
    S.init_quadrants()

ROOT = Path(__file__).resolve().parents[1]
OUT_JSON = ROOT / "results" / "portbuf_area_makespan.json"
OUT_PNG = ROOT / "results" / "portbuf_area_makespan.png"
RIGID_JSON = ROOT / "results" / "multi_area_makespan.json"

Q_RANGE = [1, 2, 4, 8, 16]
W_RANGE = [1, 2, 3, 4]
E_RANGE = [1, 2]
B_RANGE = [0, 2, 8]
T_MAX = 6000
STALL_LIMIT = 200

# 4 mesh directions; opposite(d) = d ^ 1.
DIRS = [(1, 0), (-1, 0), (0, 1), (0, -1)]
INJ = 4  # injection pseudo-port index


def dir_index(p: int, c: int) -> int:
    px, py = coord(p)
    cx, cy = coord(c)
    return DIRS.index((cx - px, cy - py))


def link_lat(p: int, c: int) -> int:
    return H if coord(p)[1] == coord(c)[1] else V


def build_children(builder):
    """children[s][node] -> list of child nodes in source-s tree."""
    ch = []
    for s in range(N):
        m = defaultdict(list)
        for p, c in builder(s):
            m[p].append(c)
        ch.append(m)
    return ch


def fanout_stats(children) -> tuple[int, float]:
    mx, tot, cnt = 0, 0, 0
    for s in range(N):
        for node, kids in children[s].items():
            mx = max(mx, len(kids))
            tot += len(kids)
            cnt += 1
    return mx, round(tot / cnt, 2)


class Flit:
    __slots__ = ("src", "arrival", "mesh_out", "need_eject", "served")

    def __init__(self, src, arrival, mesh_out, need_eject):
        self.src = src
        self.arrival = arrival
        self.mesh_out = mesh_out          # tuple of out-dir indices
        self.need_eject = need_eject
        self.served = set()               # served out-dirs; "E" for eject

    def done(self) -> bool:
        return len(self.served) == len(self.mesh_out) + (1 if self.need_eject
                                                         else 0)


def simulate(children, Q: int, W: int, E: int, B: int):
    """Cycle-accurate DES of the dynamically arbitrated buffered router.
    Returns dict with makespan or None on deadlock/timeout."""
    fifos = [[deque() for _ in range(5)] for _ in range(N)]
    credits = [[Q] * 4 for _ in range(N)]     # per out-dir, owned by sender
    arrive = defaultdict(list)                # t -> [(node, port, Flit)]
    cred_ret = defaultdict(list)              # t -> [(node, dir)]
    eject_occ = [0] * N
    drained = [0] * N
    last_drain = [0] * N

    def neighbor(node: int, d: int):
        x, y = coord(node)
        nx, ny = x + DIRS[d][0], y + DIRS[d][1]
        if 0 <= nx < MX and 0 <= ny < MY:
            return nid(nx, ny)
        return None

    def make_flit(src: int, node: int, t: int) -> Flit:
        outs = tuple(dir_index(node, c) for c in children[src].get(node, ()))
        return Flit(src, t, outs, node != src)

    for s in range(N):
        arrive[RAMP].append((s, INJ, make_flit(s, s, RAMP)))

    live = N * N                                  # flit copies still in flight
    # every tree has exactly N-1 edges -> N-1 mesh copies + 1 injected = N
    last_activity = 0
    t = 0
    while t <= T_MAX:
        activity = False
        for node, d in cred_ret.pop(t, ()):
            credits[node][d] += 1
            activity = True
        for node, port, fl in arrive.pop(t, ()):
            fifos[node][port].append(fl)
            activity = True

        eject_grants = [0] * N
        for node in range(N):
            # mesh output arbitration: oldest HOL bidder per out-dir
            for d in range(4):
                if credits[node][d] == 0:
                    continue
                nb = neighbor(node, d)
                if nb is None:
                    continue
                best = None
                for port in range(5):
                    q = fifos[node][port]
                    if not q:
                        continue
                    fl = q[0]
                    if d in fl.mesh_out and d not in fl.served:
                        if best is None or fl.arrival < best.arrival or (
                                fl.arrival == best.arrival
                                and fl.src < best.src):
                            best = fl
                if best is None:
                    continue
                best.served.add(d)
                credits[node][d] -= 1
                lat = link_lat(node, nb)
                arrive[t + lat].append((nb, d ^ 1, make_flit(best.src, nb,
                                                             t + lat)))
                activity = True
            # eject arbitration: up to W grants, FIFO room B with drain E
            cap = B + E - eject_occ[node]
            grants = min(W, cap)
            if grants > 0:
                bidders = []
                for port in range(5):
                    q = fifos[node][port]
                    if q and q[0].need_eject and "E" not in q[0].served:
                        bidders.append(q[0])
                bidders.sort(key=lambda f: (f.arrival, f.src))
                for fl in bidders[:grants]:
                    fl.served.add("E")
                    eject_grants[node] += 1
                    activity = True

        # eject FIFO update: arrivals then drain (matches rigid fifo_ok)
        for node in range(N):
            occ = eject_occ[node] + eject_grants[node]
            out = min(E, occ)
            if out > 0:
                drained[node] += out
                last_drain[node] = t
                activity = True
            eject_occ[node] = occ - out

        # dequeue completed HOL flits, return credits upstream
        for node in range(N):
            for port in range(5):
                q = fifos[node][port]
                while q and q[0].done():
                    q.popleft()
                    live -= 1
                    activity = True
                    if port != INJ:
                        up = neighbor(node, port)
                        cred_ret[t + link_lat(node, up)].append(
                            (up, port ^ 1))

        if activity:
            last_activity = t
        if live == 0 and all(o == 0 for o in eject_occ):
            break
        if t - last_activity > STALL_LIMIT:
            return None                       # deadlock / livelock
        t += 1
    else:
        return None                           # timeout

    if any(drained[n] != N - 1 for n in range(N)):
        return None
    return {"makespan": max(last_drain) + RAMP}


# ---- area model for the dynamic buffered router --------------------------
ROUTE_LUT_BITS = N * 5                        # per-source 5-bit out-mask
CTRL_ARB_DELTA = 0.005                        # arbiters + credit counters
MC = PPA.CALFORK_MC_DELTA


def area_buffered(Q: int, W: int, E: int, B: int) -> float:
    xbar, buf, sram = area_parts(W, E, B)
    port_fifo = 4 * Q * A_FLIT                # 4 mesh in-port FIFOs
    lut = ROUTE_LUT_BITS * PPA.K_CTRL
    return round(1.0 + lut + CTRL_ARB_DELTA + MC + port_fifo
                 + xbar + buf + sram, 5)


def coherent():
    for E in E_RANGE:
        for W in W_RANGE:
            if W < E:
                continue
            for B in B_RANGE:
                if B == 0 and W != E:
                    continue
                yield W, E, B


def main() -> None:
    setup()
    rigid = json.loads(RIGID_JSON.read_text(encoding="utf-8"))
    rigid_pts = [
        {**p, "mode": "rigid", "Q": 0,
         "label": f"{p['label']} rigid W{p['W']}/E{p['E']}/B{p['B']}"}
        for p in rigid["points"] if p["makespan"] is not None
    ]

    buf_pts = []
    scheme_meta = {}
    for key, (label, builder) in SCHEMES.items():
        children = build_children(builder)
        fmax, favg = fanout_stats(children)
        scheme_meta[key] = {"label": label, "fanout_max": fmax,
                            "fanout_avg": favg}
        best = None
        for Q in Q_RANGE:
            for W, E, B in coherent():
                res = simulate(children, Q, W, E, B)
                mk = res["makespan"] if res else None
                pt = {"scheme": key, "mode": "buffered", "Q": Q,
                      "W": W, "E": E, "B": B, "makespan": mk,
                      "area_total": area_buffered(Q, W, E, B),
                      "label": f"{label} buf Q{Q}/W{W}/E{E}/B{B}"}
                buf_pts.append(pt)
                if mk and (best is None or mk < best[0]):
                    best = (mk, Q, W, E, B)
        print(f"{label:16s} fanout<= {fmax} best buffered {best}")

    allpts = rigid_pts + buf_pts
    front = pareto(allpts, "area_total", "makespan")
    front.sort(key=lambda p: p["area_total"])
    front_buf = pareto(buf_pts, "area_total", "makespan")
    front_buf.sort(key=lambda p: p["area_total"])

    # plot
    fig, ax = plt.subplots(figsize=(10.4, 6.4))
    cmap = plt.get_cmap("tab10")
    kidx = {k: i for i, k in enumerate(SCHEMES)}
    for k, (label, _) in SCHEMES.items():
        rp = [p for p in rigid_pts if p["scheme"] == k]
        bp = [p for p in buf_pts if p["scheme"] == k and p["makespan"]]
        ax.scatter([p["area_total"] for p in rp],
                   [p["makespan"] for p in rp], s=22, marker="o",
                   color=cmap(kidx[k]), alpha=0.35, edgecolor="none")
        ax.scatter([p["area_total"] for p in bp],
                   [p["makespan"] for p in bp], s=30, marker="^",
                   color=cmap(kidx[k]), alpha=0.75, edgecolor="none",
                   label=f"{label}")
    ax.plot([p["area_total"] for p in front],
            [p["makespan"] for p in front], "-o", color="#111827",
            lw=2.3, ms=5, zorder=6, label="global Pareto (rigid+buffered)")
    for p in front:
        tag = (f"Q{p['Q']}" if p["mode"] == "buffered" else "cal") + \
            f" W{p['W']}/E{p['E']}/B{p['B']}"
        ax.annotate(f"{SCHEMES[p['scheme']][0]} {tag}",
                    (p["area_total"], p["makespan"]),
                    textcoords="offset points", xytext=(6, 4),
                    fontsize=6, color="#111827")
    ax.set_xlabel("chip implementation area incl. port buffers (IQ-XY=1.0)")
    ax.set_ylabel("makespan (cycles)")
    ax.set_title("rigid calendar (o) vs port-buffered dynamic router (^): "
                 "makespan vs area")
    ax.set_yscale("log")
    ax.grid(True, ls=":", alpha=0.5, which="both")
    ax.legend(loc="upper right", fontsize=7, ncol=2)
    fig.tight_layout()
    fig.savefig(OUT_PNG, dpi=130)
    plt.close(fig)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": {
            "mesh": [MX, MY], "H": H, "V": V,
            "Q_range": Q_RANGE, "W_range": W_RANGE, "E_range": E_RANGE,
            "B_range": B_RANGE,
            "buffered_router": "per-in-port FIFO depth Q, credit flow "
                               "control, oldest-first per-output arbiters, "
                               "route LUT (48x5b), CalFork fork; no slot "
                               "table",
            "area_buffered": "1.0 + LUT + arb(0.005) + MC + 4*Q*A_flit "
                             "+ eject(W,E,B)",
            "credit_rtt_note": "full link rate needs Q >= 2*lat+1 "
                               "(H: 15, V: 19)",
        },
        "scheme_meta": scheme_meta,
        "buffered_points": buf_pts,
        "pareto_global": front,
        "pareto_buffered": front_buf,
        "plot": str(OUT_PNG.relative_to(ROOT)),
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nglobal front: {[(p['label'], p['makespan']) for p in front]}")
    print(f"buffered-only front size {len(front_buf)}; "
          f"best buffered mk {min((p['makespan'] for p in buf_pts if p['makespan']), default=None)}")
    print(f"Wrote {OUT_JSON}\nWrote {OUT_PNG}")


if __name__ == "__main__":
    main()
