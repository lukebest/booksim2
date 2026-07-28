#!/usr/bin/env python3
"""Decompose T_dyn into service-discipline components.

T = T_bound + T_dyn, and this script splits T_dyn into the terms that each
service discipline contributes, using the same network-calculus primitives as
`nc_mesh_analysis.py` and the same closed forms as `ppa_analytic_model.py`.

Two views are produced:

A. Background unicast (dynamic point-to-point), 12-hop worst case.
   Exact closed form: wire + ramp + router pipeline + per-hop arbitration wait
   (+ shared-pool turnover).  The only discipline-dependent term is the
   per-hop wait, which is `bg_window` under hard TDM and occupancy-derived
   under soft priority.

B. Collective traffic, per design (CAL_SOFT / CAL_SHAPED / TDM / PRIO_DYN /
   FIFO).  For latency-rate service beta = R[t-T]+ the per-hop delay bound is
   d = T + sup(alpha - R t)/R, so the path total splits into

       sum(T_e)                 -> discipline-inherent latency
                                   (arbitration pipeline + non-preemption)
       sum(queueing part)       -> interference / aggregate burst
       rate-deficit inflation   -> only getting R < C of the link
       frame wait              -> waiting for an own slot (TDM)

Outputs results/tdyn_decompose.json.
"""

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "utils"))

from nc_mesh_analysis import (  # noqa: E402
    LR,
    Mesh,
    bg_flows,
    delay_concave,
    eff_burst,
    leftover,
    link_loads,
    makespan_bound,
    rho_tot,
)

C_LINK = 1.0  # flit/cycle/direction


# --------------------------------------------------------------- A. BG view


def bg_decompose(dx=5, dy=7, *, h=7, v=9, ramp=1, t_router=3,
                 bg_window=16, max_cal_occupancy=49, horizon=952,
                 pool_size=28):
    """Exact term-by-term split of the 12-hop BG delay bound.

    Mirrors `ppa_analytic_model.hard_tdm_bg_bound` / `soft_prio_bg_bound`:
        bound = 2*ramp + dx*(wait + t_router + h) + dy*(wait + t_router + v)
    """
    hops = dx + dy
    wire = dx * h + dy * v
    pipeline = hops * t_router

    idle = max(horizon - max_cal_occupancy, 1)
    soft_wait = max(2, (horizon + idle - 1) // idle)

    def build(label, wait, pool=0):
        arb = hops * wait
        total = 2 * ramp + wire + pipeline + arb + pool
        t_bound = wire + 2 * ramp          # irreducible: wire + ramps
        return dict(
            policy=label,
            per_hop_wait=wait,
            hops=hops,
            terms=dict(
                wire=wire,
                ramp=2 * ramp,
                router_pipeline=pipeline,
                arbitration_wait=arb,
                pool_turnover=pool,
            ),
            t_bound=t_bound,
            t_dyn=total - t_bound,
            total=total,
        )

    return dict(
        model=dict(dx=dx, dy=dy, h=h, v=v, ramp=ramp, t_router=t_router,
                   bg_window=bg_window, max_cal_occupancy=max_cal_occupancy,
                   horizon=horizon, pool_size=pool_size,
                   soft_wait=soft_wait),
        policies=[
            build("hard TDM 1-in-%d" % bg_window, bg_window),
            build("soft-prio", soft_wait),
            build("soft-prio + shared pool", soft_wait, pool=pool_size),
        ],
    )


# ------------------------------------------------------- B. collective view


def sfa_split(mesh, flows, beta_of_link, n_pass=4):
    """Like nc_mesh_analysis.sfa_class but returns the (T, queueing) split.

    Returns (rows, pieces_of) where rows[i] = dict for flow i with
      lat_T   = sum of beta.T over the flow's links   (discipline-inherent)
      queue   = sum of (d_hop - beta.T) over links    (interference/burst)
      wire    = wire delay, ramp = 2*mesh.ramp
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
                (tuple(vv) for vv in groups.values())]
            for l, groups in acc.items()
        }
        d_hop = defaultdict(float, {
            l: delay_concave(ps, beta_of_link(l))
            for l, ps in pieces_of.items()
        })

    rows = []
    for f in flows:
        lat_T = 0.0
        queue = 0.0
        for l in f["links"]:
            b = beta_of_link(l)
            d = d_hop[l]
            if not math.isfinite(d):
                lat_T = queue = math.inf
                break
            lat_T += b.T
            queue += max(0.0, d - b.T)
        rows.append(dict(
            lat_T=lat_T,
            queue=queue,
            wire=float(f["wire"]),
            ramp=2.0 * mesh.ramp,
            total=(lat_T + queue + f["wire"] + 2.0 * mesh.ramp),
        ))
    return rows, pieces_of


def worst(rows):
    fin = [r for r in rows if math.isfinite(r["total"])]
    if not fin:
        return None
    return max(fin, key=lambda r: r["total"])


def coll_decompose(mesh, kind, root, m, f_c, lam, m_b, tdm_k=16):
    """Per-design decomposition of the collective's T_dyn."""
    mk, cload, cflows = makespan_bound(mesh, kind, root, m)
    Lmax = max(cload.values()) if cload else 0.0
    period = max(Lmax / f_c, mk)
    out = dict(collective=kind, m=m, f_c=round(Lmax / period, 4),
               period=round(period, 1), lam=lam, t_bound=mk,
               max_link_load=Lmax, designs=[])

    def add(design, total, terms, note=""):
        # T_bound is the max over the LB family (link / ramp / degree / latency
        # cuts); a single flow's own wire+ramp can be shorter than that, so the
        # offset is carried explicitly to keep the split exactly additive.
        out["designs"].append(dict(
            design=design,
            total=round(total, 1) if math.isfinite(total) else None,
            t_bound=mk,
            t_dyn=round(total - mk, 1) if math.isfinite(total) else None,
            terms={k: (round(v, 1) if math.isfinite(v) else None)
                   for k, v in terms.items()},
            note=note,
        ))

    # --- CAL_SOFT: alpha == beta on the schedule support -> nothing dynamic
    add("CAL_SOFT", mk,
        dict(arbitration=0.0, interference_queue=0.0, rate_deficit=0.0,
             frame_wait=0.0, shaping_dilation=0.0, bound_offset=0.0),
        "alpha=beta 逐槽对齐；coll_buf=0")

    # --- CAL_SHAPED: calendar dilated over the period (deliberate spreading)
    add("CAL_SHAPED", period,
        dict(arbitration=0.0, interference_queue=0.0, rate_deficit=0.0,
             frame_wait=0.0, shaping_dilation=period - mk, bound_offset=0.0),
        "刻意拉匀以压低对 BG 的 sigma_H")

    # --- TDM: closed form  mk/(nc/k) + ceil(k/nc)
    nc_slots = max(1, math.ceil((Lmax / period) * tdm_k))
    nb = tdm_k - nc_slots
    if nb > 0:
        share = nc_slots / tdm_k
        rate_term = mk * (1.0 / share - 1.0)
        frame_term = math.ceil(tdm_k / nc_slots)
        add("TDM", mk / share + frame_term,
            dict(arbitration=0.0, interference_queue=0.0,
                 rate_deficit=rate_term, frame_wait=float(frame_term),
                 shaping_dilation=0.0, bound_offset=0.0),
            f"预留 {nc_slots}/{tdm_k} 槽：速率只有 C·{nc_slots}/{tdm_k}")

    # --- PRIO_DYN: buffered HP class, beta = LR(C, 1 + m_b)
    cfl = [dict(links=list(f["links"]), m=f["m"], rho=f["m"] / period,
                wire=f["wire"], src=f.get("src")) for f in cflows]
    beta_hp = lambda l: LR(C_LINK, 1.0 + m_b)  # noqa: E731
    rows, _ = sfa_split(mesh, cfl, beta_hp)
    w = worst(rows)
    if w:
        hops = max(len(f["links"]) for f in cflows)
        add("PRIO_DYN", w["total"],
            dict(arbitration=w["lat_T"],
                 interference_queue=w["queue"],
                 rate_deficit=0.0, frame_wait=0.0, shaping_dilation=0.0,
                 bound_offset=w["wire"] + w["ramp"] - mk),
            f"每跳 T=1(仲裁)+{m_b}(低优先非抢占阻塞)；最长树 {hops} 跳")

    # --- FIFO: one shared class, beta = LR(C, 1), aggregate burst
    allf = list(cfl)
    allf += [dict(links=f["links"], m=f["m"], rho=f["rho"], wire=f["wire"])
             for f in bg_flows(mesh, lam, m_b)]
    beta_fifo = lambda l: LR(C_LINK, 1.0)  # noqa: E731
    rows_all, _ = sfa_split(mesh, allf, beta_fifo)
    w = worst(rows_all[:len(cfl)])
    if w:
        add("FIFO", w["total"],
            dict(arbitration=w["lat_T"],
                 interference_queue=w["queue"],
                 rate_deficit=0.0, frame_wait=0.0, shaping_dilation=0.0,
                 bound_offset=w["wire"] + w["ramp"] - mk),
            "集体与 BG 同类聚合：sigma 含全部干扰")
    else:
        add("FIFO", math.inf,
            dict(arbitration=math.inf, interference_queue=math.inf,
                 rate_deficit=0.0, frame_wait=0.0, shaping_dilation=0.0,
                 bound_offset=0.0),
            "不稳定（rho > C）")
    return out


# ------------------------------------------------- leftover-term illustration


def leftover_terms(C, T0, hp_sigma, hp_rho):
    """Show how strict priority splits into rate deficit + latency inflation."""
    b = leftover(C, T0, hp_sigma, hp_rho)
    return dict(
        C=C, T0=T0, sigma_H=hp_sigma, rho_H=hp_rho,
        R_leftover=b.R,
        rate_deficit=C - b.R,
        T_leftover=(b.T if math.isfinite(b.T) else None),
        latency_inflation=((b.T - T0) if math.isfinite(b.T) else None),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mx", type=int, default=6)
    ap.add_argument("--my", type=int, default=8)
    ap.add_argument("--h", type=int, default=7)
    ap.add_argument("--v", type=int, default=9)
    ap.add_argument("--m", type=int, default=4)
    ap.add_argument("--mb", type=int, default=4)
    ap.add_argument("--root", type=int, default=6)
    ap.add_argument("--lam", type=float, default=0.02)
    ap.add_argument("--tdm-k", type=int, default=16)
    ap.add_argument("--out",
                    default=str(ROOT / "results" / "tdyn_decompose.json"))
    args = ap.parse_args()

    mesh = Mesh(args.mx, args.my, args.h, args.v)

    bg = bg_decompose(h=args.h, v=args.v)

    coll = []
    for kind, f_c in (("broadcast", 0.8), ("allgather", 0.05),
                      ("allgather", 0.4), ("allgather", 0.8)):
        coll.append(coll_decompose(mesh, kind, args.root, args.m,
                                   f_c, args.lam, args.mb, args.tdm_k))

    # leftover illustration at the allgather hot link
    mk, cload, _ = makespan_bound(mesh, "allgather", args.root, args.m)
    Lmax = max(cload.values())
    lo = [leftover_terms(C_LINK, 1.0, Lmax, Lmax / p)
          for p in (Lmax / 0.05, Lmax / 0.4)]

    out = dict(
        model=dict(mesh=[args.mx, args.my], h=args.h, v=args.v,
                   m=args.m, mb=args.mb, root=args.root, lam=args.lam,
                   tdm_k=args.tdm_k, C=C_LINK),
        bg_view=bg,
        collective_view=coll,
        leftover_illustration=lo,
    )
    Path(args.out).write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(f"wrote {args.out}")

    # ---- console summary
    print("\n=== A. BG 12-hop 分解 (dx=5, dy=7) ===")
    hdr = f"{'policy':>26} {'wire':>6} {'ramp':>5} {'pipe':>6} {'arb':>6} {'pool':>6} {'total':>7} {'T_dyn':>7}"
    print(hdr)
    for p in bg["policies"]:
        t = p["terms"]
        print(f"{p['policy']:>26} {t['wire']:6d} {t['ramp']:5d} "
              f"{t['router_pipeline']:6d} {t['arbitration_wait']:6d} "
              f"{t['pool_turnover']:6d} {p['total']:7d} {p['t_dyn']:7d}")

    print("\n=== B. 集体 T_dyn 分解 ===")
    for blk in coll:
        print(f"\n-- {blk['collective']}  m={blk['m']}  f_c={blk['f_c']}  "
              f"period={blk['period']}  T_bound={blk['t_bound']:.0f}")
        print(f"{'design':>12} {'total':>10} {'T_dyn':>10} {'arb':>9} "
              f"{'queue':>11} {'rate_def':>10} {'frame':>7} {'shape':>9} "
              f"{'offset':>8}")
        for d in blk["designs"]:
            t = d["terms"]
            fmt = lambda x: ("inf" if x is None else f"{x:.1f}")  # noqa: E731
            print(f"{d['design']:>12} {fmt(d['total']):>10} "
                  f"{fmt(d['t_dyn']):>10} {fmt(t['arbitration']):>9} "
                  f"{fmt(t['interference_queue']):>11} "
                  f"{fmt(t['rate_deficit']):>10} {fmt(t['frame_wait']):>7} "
                  f"{fmt(t['shaping_dilation']):>9} "
                  f"{fmt(t['bound_offset']):>8}")


if __name__ == "__main__":
    main()
