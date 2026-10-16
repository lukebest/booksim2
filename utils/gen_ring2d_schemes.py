#!/usr/bin/env python3
"""Concise 2D-ring report: baseline, iSLIP-2D, CA-credit, CA-rate, CA-ring.

One all-to-all of 2,256 single-flit flows on the dimension-sliced ring.
The baseline is the reactive ring (I-tag / E-tag), with no central arbiter.
iSLIP-2D makespan is the interval schedule already in
results/islip2d_8x6.json. CA-credit and CA-rate drain the same flows on the
ring data plane. One Pareto uses arbiter state; the other uses system bits
(arbiter plus the ring-station storage iSLIP deletes).
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "utils"))

from rg_ca_admit import CreditSim, FabricMap, RateSim
from rg_ca_ring import RingGrainSim
from rg_islip_ring import RingSlipSim
from rg_ring_sched import schedule_ring
from rg_ring_topo import RingTopology
from rg_sched_cost import W_FLIT, distributed_cost, pareto_front, sched_cost
from rg_steady_des import RingBaseAdapter, SteadyParams

BATCH = ROOT / "results" / "islip2d_8x6.json"
OUT = ROOT / "results" / "report_ring2d_schemes.html"
N_FLOWS = 48 * 47


def _islip() -> dict:
    with open(BATCH, encoding="utf-8") as f:
        doc = json.load(f)
    for r in doc["rows"]:
        if (r.get("fabric") == "ring" and r.get("pattern") == "alltoall"
                and r.get("m") == 1 and r.get("sigma") == 1
                and r.get("grants_per_src") == 2
                and r.get("conflict_domain") == "interval"
                and (r.get("spatial_reuse") or "arc") == "arc"
                and r.get("board_ports") in (None, 1)
                and r.get("leave_ports") in (None, 1)
                and (r.get("path_mode") or "fixed") == "fixed"
                and r.get("fill") in (None, "arc_desc")):
            return r
    raise RuntimeError("ring interval all-to-all row not found")


def _drain_base() -> dict:
    p = SteadyParams(buf_depth=20, t_rtt=16)
    sim = RingBaseAdapter(p)
    for s in range(48):
        for d in range(48):
            if s != d:
                sim.offer(s, d)
    got = 0
    last = 0
    while got < N_FLOWS and sim.sim.t < 200_000:
        sim.step()
        while sim.pkt_done:
            _pid, _s, _d, _tg, td = sim.pkt_done.pop()
            got += 1
            last = max(last, td)
    if got != N_FLOWS:
        raise RuntimeError(f"baseline delivered {got}/{N_FLOWS}")
    bits = distributed_cost("ring_base", fifo_depth=p.fifo_depth)["bits"]
    return {
        "makespan": last,
        "bits": 0,
        "system": bits,
        "defl": sim.sim.st["n_deflections"],
    }


def _drain(kind: str, **kw) -> dict:
    if kind == "slip":
        p = SteadyParams(buf_depth=20, t_rtt=16, **kw)
        sim = RingSlipSim(p)
        for s in range(48):
            for d in range(48):
                if s != d:
                    sim.offer(s, d)
        got = last = 0
        while got < N_FLOWS and sim.t < 100_000:
            sim.step()
            while sim.pkt_done:
                got += 1
                last = max(last, sim.pkt_done.pop()[-1])
        if got != N_FLOWS:
            raise RuntimeError(f"slip delivered {got}/{N_FLOWS}")
        e = sim.export()
        return {
            "makespan": last,
            "bits": e["arbiter_bits"],
            "system": e["system_bits"],
            "breakdown": e["arbiter_breakdown"],
            "rounds": e["rounds"],
            "defl": e["n_deflections"],
            "ctrl_per_cy": e["ctrl_msgs"] / max(1, sim.t),
        }
    if kind == "ring":
        scale = kw.pop("scale")
        p = SteadyParams(buf_depth=20, t_rtt=16, **kw)
        sim = RingGrainSim(p, ring_credit_scale=scale,
                           bridge_credit_scale=scale)
    else:
        p = SteadyParams(buf_depth=20, t_rtt=16, **kw)
        sim = CreditSim("ring", p) if kind == "credit" else RateSim("ring", p)
    for s in range(48):
        for d in range(48):
            if s != d:
                sim.offer(s, d)
    got = 0
    last = 0
    while got < N_FLOWS and sim.t < 100_000:
        sim.step()
        while sim.pkt_done:
            _pid, _s, _d, _tg, td = sim.pkt_done.pop()
            got += 1
            last = max(last, td)
    if got != N_FLOWS:
        raise RuntimeError(f"{kind} delivered {got}/{N_FLOWS}")
    if kind == "ring":
        e = sim.export()
        return {
            "makespan": last,
            "bits": e["arbiter_bits"],
            "system": e["system_bits"],
            "ring_credit": e["ring_credit"],
            "ring_credit_min": e["ring_credit_min"],
            "bridge_credit": e["bridge_credit"],
            "grants": e["n_grants"],
            "defl": e["n_deflections"],
            "ctrl_per_cy": e["ctrl_msgs"] / max(1, sim.t),
        }
    return {
        "makespan": last,
        "bits": sim.fm.arbiter_bits,
        "system": sim.fm.system_bits(),
        "ha": sim.fm.ha,
        "nol": sim.fm.nol,
        "link_credit": sim.fm.link_credit,
    }


def _svg(pts: list[dict]) -> str:
    W, H = 880, 420
    x0, y0, pw, ph = 72, 28, 760, 310
    xs = [p["bits"] for p in pts]
    ys = [p["makespan"] for p in pts]
    xmin, xmax = min(xs) / 1.5, max(xs) * 1.4
    ymin, ymax = min(ys) / 1.25, max(ys) * 1.35

    def X(v: float) -> float:
        return x0 + (math.log10(v) - math.log10(xmin)) / (
            math.log10(xmax) - math.log10(xmin)) * pw

    def Y(v: float) -> float:
        # Larger makespan toward the top. The good corner is bottom-left.
        return y0 + (math.log10(ymax) - math.log10(v)) / (
            math.log10(ymax) - math.log10(ymin)) * ph

    parts = [f'<svg viewBox="0 0 {W} {H}" width="100%" '
             f'preserveAspectRatio="xMidYMid meet" role="img">',
             f'<rect class="plot" x="{x0}" y="{y0}" width="{pw}" height="{ph}"/>']
    for g in (100, 150, 200, 300):
        if ymin <= g <= ymax:
            yy = Y(g)
            parts.append(f'<line class="gl" x1="{x0}" y1="{yy:.1f}" '
                         f'x2="{x0+pw}" y2="{yy:.1f}"/>')
            parts.append(f'<text class="tick" x="{x0-8}" y="{yy+4:.1f}" '
                         f'text-anchor="end">{g}</text>')
    p0 = math.ceil(math.log10(xmin))
    p1 = math.floor(math.log10(xmax))
    for e in range(int(p0), int(p1) + 1):
        xx = X(10 ** e)
        lab = f"{10**e/1000:.0f}k" if 10 ** e >= 1000 else str(10 ** e)
        parts.append(f'<text class="tick" x="{xx:.1f}" y="{y0+ph+18}" '
                     f'text-anchor="middle">{lab}</text>')
    front = [p for p in pts if p["front"]]
    front.sort(key=lambda p: p["bits"])
    if len(front) >= 2:
        d = " ".join(f"{X(p['bits']):.1f},{Y(p['makespan']):.1f}" for p in front)
        parts.append(f'<polyline class="cv cB" points="{d}" fill="none"/>')
    groups: list[dict] = []
    for p in pts:
        for g in groups:
            if g["bits"] == p["bits"] and g["makespan"] == p["makespan"]:
                g["names"].append(p["name"])
                g["front"] = g["front"] or p["front"]
                break
        else:
            groups.append({**p, "names": [p["name"]]})
    # Offsets keep the credit cluster readable: those four markers sit
    # inside ~30 px. (dx, dy, anchor) is relative to the marker center.
    label_at = {
        "iSLIP-2D": (-14, -14, "end", "iSLIP-2D"),
        "credit ×1": (22, 4, "start", "credit ×1"),
        "CA-ring ×1": (16, -12, "start", "CA-ring ×1"),
        "CA-ring ×2": (16, 16, "start", "CA-ring ×2"),
        "iSLIP-ring": (-14, -12, "end", "iSLIP-ring"),
        "credit ×2": (22, 18, "start", "credit ×2"),
        "credit HA=16": (18, -12, "start", "credit HA=16"),
        "rate T=16": (16, -8, "start", "rate"),
    }
    for p in groups:
        cx, cy = X(p["bits"]), Y(p["makespan"])
        op = "1" if p["front"] else "0.45"
        col = p["color"]
        if p["shape"] == "o":
            parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="6" '
                         f'fill="{col}" opacity="{op}"/>')
        elif p["shape"] == "s":
            parts.append(f'<rect x="{cx-5:.1f}" y="{cy-5:.1f}" width="10" '
                         f'height="10" fill="{col}" opacity="{op}"/>')
        elif p["shape"] == "h":
            hx = " ".join(f"{cx + 7*math.cos(math.pi/3*i):.1f},"
                          f"{cy + 7*math.sin(math.pi/3*i):.1f}"
                          for i in range(6))
            parts.append(f'<polygon points="{hx}" fill="{col}" '
                         f'opacity="{op}"/>')
        elif p["shape"] == "p":
            pg = " ".join(f"{cx + 7.5*math.cos(-math.pi/2 + 2*math.pi/5*i):.1f},"
                          f"{cy + 7.5*math.sin(-math.pi/2 + 2*math.pi/5*i):.1f}"
                          for i in range(5))
            parts.append(f'<polygon points="{pg}" fill="{col}" '
                         f'opacity="{op}"/>')
        elif p["shape"] == "t":
            parts.append(
                f'<polygon points="{cx:.1f},{cy-7:.1f} {cx+6.5:.1f},{cy+5:.1f} '
                f'{cx-6.5:.1f},{cy+5:.1f}" fill="{col}" opacity="{op}"/>')
        else:
            parts.append(
                f'<polygon points="{cx:.1f},{cy-7:.1f} {cx+7:.1f},{cy:.1f} '
                f'{cx:.1f},{cy+7:.1f} {cx-7:.1f},{cy:.1f}" fill="{col}" '
                f'opacity="{op}"/>')
        dx, dy, anchor, short = label_at[p["names"][0]]
        parts.append(f'<text class="tick" x="{cx+dx:.1f}" y="{cy+dy:.1f}" '
                     f'text-anchor="{anchor}" fill="#e8ecf4">{short}  '
                     f'{p["makespan"]}</text>')
    parts.append(f'<text class="axl" x="{x0+pw/2}" y="{y0+ph+40}" '
                 f'text-anchor="middle">仲裁器状态（bit，对数）</text>')
    parts.append(f'<text class="axl" x="16" y="{y0+ph/2}" text-anchor="middle" '
                 f'transform="rotate(-90 16 {y0+ph/2})">makespan（拍，对数）</text>')
    parts.append("</svg>")
    return "".join(parts)


def _svg_system(pts: list[dict]) -> str:
    """System bits on a linear axis. The stations dominate, so a log axis
    would stack every point into one column."""
    W, H = 880, 420
    x0, y0, pw, ph = 78, 28, 750, 310
    xs = [p["system"] for p in pts]
    ys = [p["makespan"] for p in pts]
    xmin, xmax = min(xs) - 6_000, max(xs) + 6_000
    ymin, ymax = min(ys) - 20, max(ys) + 24

    def X(v: float) -> float:
        return x0 + (v - xmin) / (xmax - xmin) * pw

    def Y(v: float) -> float:
        return y0 + (ymax - v) / (ymax - ymin) * ph

    parts = [f'<svg viewBox="0 0 {W} {H}" width="100%" '
             f'preserveAspectRatio="xMidYMid meet" role="img">',
             f'<rect class="plot" x="{x0}" y="{y0}" width="{pw}" height="{ph}"/>']
    for g in (120, 160, 200, 240):
        if ymin <= g <= ymax:
            yy = Y(g)
            parts.append(f'<line class="gl" x1="{x0}" y1="{yy:.1f}" '
                         f'x2="{x0+pw}" y2="{yy:.1f}"/>')
            parts.append(f'<text class="tick" x="{x0-8}" y="{yy+4:.1f}" '
                         f'text-anchor="end">{g}</text>')
    for g in (190_000, 200_000, 210_000, 220_000, 230_000, 240_000):
        if xmin <= g <= xmax:
            xx = X(g)
            parts.append(f'<text class="tick" x="{xx:.1f}" y="{y0+ph+18}" '
                         f'text-anchor="middle">{g/1000:.0f}k</text>')
    front = [p for p in pts if p["sfront"]]
    front.sort(key=lambda p: p["system"])
    if len(front) >= 2:
        d = " ".join(f"{X(p['system']):.1f},{Y(p['makespan']):.1f}"
                     for p in front)
        parts.append(f'<polyline class="cv cB" points="{d}" fill="none"/>')
    groups: list[dict] = []
    for p in pts:
        for g in groups:
            if (abs(g["system"] - p["system"]) < 100
                    and g["makespan"] == p["makespan"]):
                g["names"].append(p["name"])
                g["sfront"] = g["sfront"] or p["sfront"]
                break
        else:
            groups.append({**p, "names": [p["name"]]})
    label_at = {
        "基线": (14, 18, "start", "基线"),
        "iSLIP-2D": (-12, -16, "end", "iSLIP-2D"),
        "credit ×1": (28, -4, "start", "credit ×1"),
        "CA-ring ×1": (-10, -14, "end", "CA-ring ×1 / ×2"),
        "iSLIP-ring": (-12, -12, "end", "iSLIP-ring"),
        "credit ×2": (28, 16, "start", "credit ×2"),
        "credit HA=16": (14, -12, "start", "HA=16"),
        "rate T=16": (12, -10, "start", "rate"),
    }
    for p in groups:
        cx, cy = X(p["system"]), Y(p["makespan"])
        op = "1" if (p["sfront"] or p["names"][0] == "基线") else "0.55"
        col = p["color"]
        if p["shape"] == "o":
            parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="6" '
                         f'fill="{col}" opacity="{op}"/>')
        elif p["shape"] == "s":
            parts.append(f'<rect x="{cx-5:.1f}" y="{cy-5:.1f}" width="10" '
                         f'height="10" fill="{col}" opacity="{op}"/>')
        elif p["shape"] == "h":
            hx = " ".join(f"{cx + 7*math.cos(math.pi/3*i):.1f},"
                          f"{cy + 7*math.sin(math.pi/3*i):.1f}"
                          for i in range(6))
            parts.append(f'<polygon points="{hx}" fill="{col}" '
                         f'opacity="{op}"/>')
        elif p["shape"] == "p":
            pg = " ".join(f"{cx + 7.5*math.cos(-math.pi/2 + 2*math.pi/5*i):.1f},"
                          f"{cy + 7.5*math.sin(-math.pi/2 + 2*math.pi/5*i):.1f}"
                          for i in range(5))
            parts.append(f'<polygon points="{pg}" fill="{col}" '
                         f'opacity="{op}"/>')
        elif p["shape"] == "t":
            parts.append(
                f'<polygon points="{cx:.1f},{cy-7:.1f} {cx+6.5:.1f},{cy+5:.1f} '
                f'{cx-6.5:.1f},{cy+5:.1f}" fill="{col}" opacity="{op}"/>')
        else:
            parts.append(
                f'<polygon points="{cx:.1f},{cy-7:.1f} {cx+7:.1f},{cy:.1f} '
                f'{cx:.1f},{cy+7:.1f} {cx-7:.1f},{cy:.1f}" fill="{col}" '
                f'opacity="{op}"/>')
        dx, dy, anchor, short = label_at[p["names"][0]]
        parts.append(f'<text class="tick" x="{cx+dx:.1f}" y="{cy+dy:.1f}" '
                     f'text-anchor="{anchor}" fill="#e8ecf4">{short}  '
                     f'{p["makespan"]}</text>')
    parts.append(f'<text class="axl" x="{x0+pw/2}" y="{y0+ph+40}" '
                 f'text-anchor="middle">系统状态（bit）</text>')
    parts.append(f'<text class="axl" x="16" y="{y0+ph/2}" text-anchor="middle" '
                 f'transform="rotate(-90 16 {y0+ph/2})">makespan（拍）</text>')
    parts.append("</svg>")
    return "".join(parts)


def _clk(sim) -> int:
    return sim.t if hasattr(sim, "t") else sim.sim.t


def _finish(sim, *, limit: int = 300_000, stall: int = 25_000,
            stagger: bool = False) -> dict:
    """Offer the whole all-to-all at t=0. Default order is destination index
    0..47 for every source, so all sources aim at the same destination at the
    same time. stagger=True sends source s to s+1, s+2, ... instead."""
    for s in range(48):
        dsts = ([(s + k) % 48 for k in range(1, 48)] if stagger
                else range(48))
        for d in dsts:
            if d != s:
                sim.offer(s, d)
    got = last = 0
    seen = -1
    seen_at = 0
    while got < N_FLOWS and _clk(sim) < limit:
        sim.step()
        while sim.pkt_done:
            td = sim.pkt_done.pop()[-1]
            got += 1
            last = max(last, td)
        c = _clk(sim)
        if got != seen:
            seen, seen_at = got, c
        elif c - seen_at > stall:
            return {"makespan": None, "got": got, "stall": True}
    if got != N_FLOWS:
        return {"makespan": None, "got": got, "stall": True}
    out = {"makespan": last, "got": got, "stall": False}
    if hasattr(sim, "export"):
        out["ctrl_per_cy"] = sim.export()["ctrl_msgs"] / max(1, sim.t)
    if hasattr(sim, "sim"):
        out["defl"] = sim.sim.st["n_deflections"]
        out["reasm_ovf"] = sim.sim.st["n_reasm_overflow"]
    return out


def _measure_m(m: int) -> dict:
    """Same 2,256-pair all-to-all, m flits per pair."""
    a2a = [(s, d) for s in range(48) for d in range(48) if s != d]
    isl = schedule_ring(RingTopology(), a2a, m=m, ring_path_mode="fixed",
                        grants_per_src=2, conflict_domain="interval",
                        pipeline_depth=1 << 20, t_rtt=16)
    hot = isl["plan"]["max_link_load"]
    out: dict = {
        "m": m,
        "islip": isl["makespan_sched"],
        "hot": hot,
        "link_lb": hot * m,
    }
    base = _finish(RingBaseAdapter(SteadyParams(m=m, buf_depth=20, t_rtt=16)))
    out["base"] = base["makespan"]
    out["defl"] = base.get("defl")
    out["reasm_ovf"] = base.get("reasm_ovf")
    for scale in (1.0, 2.0):
        p = SteadyParams(m=m, buf_depth=20, t_rtt=16, ca_credit_scale=scale)
        credit = FabricMap("ring", p).link_credit
        key = f"c{scale:g}"
        out[f"{key}_credit"] = credit
        if credit < m:
            out[key] = None
            continue
        out[key] = _finish(CreditSim("ring", p))["makespan"]
    out["rate"] = _finish(RateSim(
        "ring", SteadyParams(m=m, buf_depth=20, t_rtt=16, ca_period=64)
    ))["makespan"]
    for scale in (1.0, 2.0):
        r = _finish(RingGrainSim(SteadyParams(m=m, buf_depth=20, t_rtt=16),
                                 ring_credit_scale=scale,
                                 bridge_credit_scale=scale))
        out[f"ca{scale:g}"] = r["makespan"]
        if scale == 1.0:
            out["ca_defl"] = r.get("defl")
            out["ca_ctrl"] = r.get("ctrl_per_cy")
    out["ca_inflight"] = _finish(RingGrainSim(
        SteadyParams(m=m, buf_depth=20, t_rtt=16),
        ring_basis="inflight"))["makespan"]
    out.update(_slip_extra(m))
    return out


def _slip_extra(m: int) -> dict:
    """iSLIP-ring at m flits per pair, plus the two checks the text cites."""
    p = lambda: SteadyParams(m=m, buf_depth=20, t_rtt=16)  # noqa: E731
    sim = RingSlipSim(p())
    r = _finish(sim)
    e = sim.export()
    a2a = [(s, d) for s in range(48) for d in range(48) if s != d]
    excl = schedule_ring(RingTopology(spatial_reuse="whole_ring"), a2a, m=m,
                         ring_path_mode="fixed", grants_per_src=2,
                         conflict_domain="interval", pipeline_depth=1 << 20,
                         t_rtt=16)
    return {
        "sr": r["makespan"],
        "sr_defl": e["n_deflections"],
        "sr_fifo": e["n_fifo_full_deflect"],
        "sr_ctrl": e["ctrl_msgs"],
        "sr_ctrl_cy": e["ctrl_msgs"] / max(1, sim.t),
        "sr_start": e["t_last_start"],
        "sr_rounds": e["rounds"],
        "sr_rl1": _finish(RingSlipSim(p(), round_lat=1))["makespan"],
        "sr_excl": excl["makespan_sched"],
        "sr_full": _finish(RingSlipSim(p(), book_ej=True,
                                       book_dst=True))["makespan"],
    }


def _measure_stagger(m: int) -> dict:
    p = lambda **k: SteadyParams(m=m, buf_depth=20, t_rtt=16, **k)  # noqa: E731
    return {
        "base": _finish(RingBaseAdapter(p()), stagger=True)["makespan"],
        "c1": _finish(CreditSim("ring", p(ca_credit_scale=1.0)),
                      stagger=True)["makespan"],
        "rate": _finish(RateSim("ring", p(ca_period=64)),
                        stagger=True)["makespan"],
        "ca1": _finish(RingGrainSim(p()), stagger=True)["makespan"],
        "sr": _finish(RingSlipSim(p()), stagger=True)["makespan"],
    }


def _svg_len(rows: list[dict]) -> str:
    """makespan against flits per src-dst. Log-log. Smaller makespan is down."""
    W, H = 880, 400
    x0, y0, pw, ph = 72, 36, 640, 300
    series = [
        ("链路下限", "#8b95ab", "link_lb", True),
        ("iSLIP-2D", "#6ee7a8", "islip", False),
        ("基线", "#7eb6ff", "base", False),
        ("credit ×1", "#e06c6c", "c1", False),
        ("rate", "#e6a23c", "rate", False),
        ("CA-ring ×1", "#4dd0e1", "ca1", False),
        ("iSLIP-ring", "#ff8fd0", "sr", False),
    ]
    xs = [r["m"] for r in rows]
    ys = [r[k] for r in rows for _, _, k, _ in series if r.get(k)]
    xmin, xmax = min(xs) / 1.6, max(xs) * 1.45
    ymin, ymax = min(ys) / 1.4, max(ys) * 1.35

    def X(v: float) -> float:
        return x0 + (math.log10(v) - math.log10(xmin)) / (
            math.log10(xmax) - math.log10(xmin)) * pw

    def Y(v: float) -> float:
        return y0 + (math.log10(ymax) - math.log10(v)) / (
            math.log10(ymax) - math.log10(ymin)) * ph

    parts = [f'<svg viewBox="0 0 {W} {H}" width="100%" '
             f'preserveAspectRatio="xMidYMid meet" role="img">',
             f'<rect class="plot" x="{x0}" y="{y0}" width="{pw}" height="{ph}"/>']
    for g in (100, 1_000, 10_000):
        if ymin <= g <= ymax:
            yy = Y(g)
            parts.append(f'<line class="gl" x1="{x0}" y1="{yy:.1f}" '
                         f'x2="{x0+pw}" y2="{yy:.1f}"/>')
            lab = f"{g/1000:.0f}k" if g >= 1000 else str(g)
            parts.append(f'<text class="tick" x="{x0-8}" y="{yy+4:.1f}" '
                         f'text-anchor="end">{lab}</text>')
    for m in xs:
        xx = X(m)
        parts.append(f'<text class="tick" x="{xx:.1f}" y="{y0+ph+18}" '
                     f'text-anchor="middle">{m}</text>')
    for _name, col, key, dash in series:
        pts = [(r["m"], r[key]) for r in rows if r.get(key)]
        d = " ".join(f"{X(m):.1f},{Y(v):.1f}" for m, v in pts)
        extra = ' stroke-dasharray="5 4"' if dash else ""
        parts.append(f'<polyline points="{d}" fill="none" stroke="{col}" '
                     f'stroke-width="2.2"{extra}/>')
        for m, v in pts:
            parts.append(f'<circle cx="{X(m):.1f}" cy="{Y(v):.1f}" r="3.5" '
                         f'fill="{col}"/>')
    lx = x0 + pw + 16
    for i, (name, col, _key, dash) in enumerate(series):
        ly = y0 + 18 + i * 22
        parts.append(f'<line x1="{lx}" y1="{ly}" x2="{lx+22}" y2="{ly}" '
                     f'stroke="{col}" stroke-width="2.2"'
                     f'{" stroke-dasharray=\"4 3\"" if dash else ""}/>')
        parts.append(f'<text class="tick" x="{lx+28}" y="{ly+4}" '
                     f'fill="#e8ecf4">{name}</text>')
    parts.append(f'<text class="axl" x="{x0+pw/2}" y="{y0+ph+40}" '
                 f'text-anchor="middle">每个 src-dst 的 flit 数（对数）</text>')
    parts.append(f'<text class="axl" x="16" y="{y0+ph/2}" text-anchor="middle" '
                 f'transform="rotate(-90 16 {y0+ph/2})">makespan（拍，对数）</text>')
    parts.append("</svg>")
    return "".join(parts)


def _cell(v) -> str:
    return "放不出" if v is None else f"{v:,}"


def _vs(v, base: int) -> str:
    if not v:
        return "—"
    if v == base:
        return "1"
    return f"{v / base:.2f}×"


def main() -> None:
    isl = _islip()
    topo = RingTopology()
    ic = sched_cost("islip2d_ring", topo, N_FLOWS, iters=1,
                    n_rounds=isl["n_rounds"], conflict_domain="interval")
    base = _drain_base()
    station = base["system"]
    pts = [{
        "name": "基线",
        "scheme": "base",
        "makespan": base["makespan"],
        "bits": 0,
        "system": station,
        "color": "#7eb6ff",
        "shape": "t",
        "note": "I-tag / E-tag · 无集中仲裁",
        "defl": base["defl"],
    }, {
        "name": "iSLIP-2D",
        "scheme": "islip",
        "makespan": isl["makespan"],
        "bits": ic["bits"],
        "system": ic["bits"],
        "color": "#6ee7a8",
        "shape": "d",
        "note": "interval · fixed · g=2",
    }]
    for scale in (1.0, 2.0):
        r = _drain("credit", ca_credit_scale=scale, ca_period=1, ha_trackers=0)
        pts.append({
            "name": f"credit ×{scale:g}",
            "scheme": "credit",
            "makespan": r["makespan"],
            "bits": r["bits"],
            "system": r["system"],
            "color": "#e06c6c" if scale == 1 else "#f0a0a0",
            "shape": "o",
            "ha": r["ha"],
            "link_credit": r["link_credit"],
            "note": f"HA={r['ha']} · 链路信用 {r['link_credit']}",
            "headline": scale == 1.0,
        })
    ha = _drain("credit", ca_credit_scale=1.0, ca_period=1, ha_trackers=16)
    pts.append({
        "name": "credit HA=16",
        "scheme": "credit",
        "makespan": ha["makespan"],
        "bits": ha["bits"],
        "system": ha["system"],
        "color": "#c9a6ff",
        "shape": "o",
        "note": "tracker=16",
    })
    for period in (16, 64, 256):
        r = _drain("rate", ca_period=period, ha_trackers=0)
        pts.append({
            "name": f"rate T={period}",
            "scheme": "rate",
            "makespan": r["makespan"],
            "bits": r["bits"],
            "system": r["system"],
            "color": "#e6a23c" if period == 64 else "#f5c16c",
            "shape": "s",
            "note": f"周期 {period}",
            "headline": period == 64,
        })
    for scale in (1.0, 2.0):
        r = _drain("ring", scale=scale, ca_period=1)
        pts.append({
            "name": f"CA-ring ×{scale:g}",
            "scheme": "ring",
            "makespan": r["makespan"],
            "bits": r["bits"],
            "system": r["system"],
            "color": "#4dd0e1" if scale == 1 else "#95e6f0",
            "shape": "h",
            "note": f"环计数 {r['ring_credit_min']}–{r['ring_credit']} · "
                    f"桥计数 {r['bridge_credit']}",
            "ring_credit": r["ring_credit"],
            "ring_credit_min": r["ring_credit_min"],
            "bridge_credit": r["bridge_credit"],
            "defl": r["defl"],
            "ctrl_per_cy": r["ctrl_per_cy"],
        })
    r = _drain("slip", ca_period=1)
    pts.append({
        "name": "iSLIP-ring",
        "scheme": "slip",
        "makespan": r["makespan"],
        "bits": r["bits"],
        "system": r["system"],
        "color": "#ff8fd0",
        "shape": "p",
        "note": "环 + 桥 + 注入口 · g=2",
        "defl": r["defl"],
        "rounds": r["rounds"],
        "breakdown": r["breakdown"],
    })
    arb = [p for p in pts if p["bits"] > 0]
    front = pareto_front([(p["bits"], p["makespan"], p["name"]) for p in arb])
    front_names = {name for _, _, name in front}
    sfront = pareto_front([(p["system"], p["makespan"], p["name"]) for p in pts])
    sfront_names = {name for _, _, name in sfront}
    for p in pts:
        p["front"] = p["name"] in front_names
        p["sfront"] = p["name"] in sfront_names

    base_mk = isl["makespan"]
    raw = next(p for p in pts if p["name"] == "基线")
    cred = next(p for p in pts if p["name"] == "credit ×1")
    rate = next(p for p in pts if p["name"] == "rate T=64")
    ca1 = next(p for p in pts if p["name"] == "CA-ring ×1")
    ca2 = next(p for p in pts if p["name"] == "CA-ring ×2")
    sr = next(p for p in pts if p["name"] == "iSLIP-ring")
    sr_tbl = sum(v for k, v in sr["breakdown"].items()
                 if k.startswith("interval"))

    def row(p: dict, hl: bool = False) -> str:
        cls = ' class="hl"' if hl else ""
        ratio = p["makespan"] / base_mk
        rel = "—" if p["name"] == "iSLIP-2D" else f"{ratio:.2f}×"
        return (f"<tr{cls}><td>{p['name']}<br><span class='muted'>{p['note']}</span></td>"
                f"<td><b>{p['makespan']:,}</b></td><td>{rel}</td>"
                f"<td>{p['bits']:,}</td><td>{p['system']:,}</td></tr>")

    order = ["基线", "iSLIP-2D", "credit ×1", "credit ×2",
             "credit HA=16", "rate T=16", "rate T=64", "rate T=256",
             "CA-ring ×1", "CA-ring ×2", "iSLIP-ring"]
    by = {p["name"]: p for p in pts}
    body = "".join(row(by[n], n in ("基线", "iSLIP-2D", "credit ×1",
                                    "rate T=64", "CA-ring ×1",
                                    "iSLIP-ring"))
                   for n in order)
    names = "、".join(p["name"] for p in sorted(
        (p for p in arb if p["front"]), key=lambda p: p["bits"]))
    snames = "、".join(p["name"] for p in sorted(
        (p for p in pts if p["sfront"]), key=lambda p: p["system"]))
    c2 = next(p for p in pts if p["name"] == "credit ×2")
    long = {1: {
        "m": 1,
        "islip": base_mk,
        "base": raw["makespan"],
        "c1": cred["makespan"],
        "c2": c2["makespan"],
        "c1_credit": cred["link_credit"],
        "c2_credit": c2["link_credit"],
        "rate": rate["makespan"],
        "ca1": ca1["makespan"],
        "ca2": ca2["makespan"],
        "hot": 60,
        "link_lb": 60,
        "defl": raw["defl"],
        "reasm_ovf": None,
    }}
    long[1].update(_slip_extra(1))
    long[1]["ca_inflight"] = _finish(RingGrainSim(
        SteadyParams(m=1, buf_depth=20, t_rtt=16),
        ring_basis="inflight"))["makespan"]
    for m in (16, 64):
        long[m] = _measure_m(m)
    stag = {m: _measure_stagger(m) for m in (1, 16, 64)}
    wide = long[64]
    flit_b = W_FLIT // 8
    kib = 64 * flit_b // 1024

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>2D 环 · 基线 / iSLIP-2D / CA-credit / CA-rate / CA-ring / iSLIP-ring</title>
<style>
body {{ font-family: "Segoe UI", "Noto Sans SC", system-ui, sans-serif;
       margin: 0; background: #0b1020; color: #e8ecf4; line-height: 1.62; }}
.wrap {{ max-width: 920px; margin: 0 auto; padding: 28px 34px 72px; }}
h1 {{ font-size: 1.45rem; color: #f0f4ff; border-bottom: 1px solid #2a3555;
     padding-bottom: .45rem; }}
h2 {{ margin-top: 1.7rem; font-size: 1.12rem; color: #f0f4ff;
     border-left: 4px solid #7eb6ff; padding-left: .55rem; }}
p {{ margin: .5rem 0; }}
.muted {{ color: #9aa3b5; font-size: .85rem; }}
.pill {{ display: inline-block; background: #1a2340; border: 1px solid #2a3555;
        border-radius: 999px; padding: 2px 11px; font-size: .78rem;
        margin: 0 6px 6px 0; }}
table {{ border-collapse: collapse; width: 100%; font-size: .88rem;
        margin: .7rem 0 1rem; }}
th, td {{ border: 1px solid #2a3555; padding: 6px 9px; text-align: left;
         vertical-align: top; }}
th {{ background: #1a2340; }}
tr:nth-child(even) {{ background: #12192c; }}
tr.hl td {{ background: #17243d; }}
.win {{ color: #6ee7a8; font-weight: 600; }}
.fig {{ background: #0e1425; border: 1px solid #2a3555; border-radius: 10px;
       padding: 12px 12px 8px; margin: .9rem 0; }}
.fig .cap {{ color: #9aa3b5; font-size: .82rem; margin-top: .4rem; }}
.note {{ background: #141b2f; border-left: 3px solid #d9a03c;
        border-radius: 0 8px 8px 0; padding: 10px 15px; margin: .8rem 0;
        font-size: .9rem; }}
ol {{ margin: .2rem 0 .9rem; padding-left: 1.35rem; }}
li {{ margin: .28rem 0; }}
svg text {{ font-family: "Segoe UI","Noto Sans SC",system-ui,sans-serif; }}
.plot {{ fill: #0b142a; stroke: #2a3555; }}
.gl {{ stroke: #1f2a47; }}
.tick {{ fill: #8b95ab; font-size: 11px; }}
.axl {{ fill: #9aa3b5; font-size: 12px; }}
.cv {{ fill: none; stroke: #6ee7a8; stroke-width: 2.4; }}
code {{ background: #1a2340; padding: 1px 5px; border-radius: 4px; }}
</style>
</head>
<body><div class="wrap">
<h1>2D 环：基线与五种集中仲裁的 makespan</h1>
<p class="muted">只看维度切片 2D 环。同一批 all-to-all：48 节点、每对节点一条流，共 {N_FLOWS:,} 条。
第 1–3 节每条流是 1 个 flit（{flit_b} byte）。第 4 节把包长加到 64 flit。
基线、CA-credit、CA-rate、CA-ring、iSLIP-ring 都在环数据面上排完；
iSLIP-2D 在 1 flit 时的完成时间来自 <code>results/islip2d_8x6.json</code>。</p>
<p>
<span class="pill">6 行环 × 8 + 8 列环 × 6</span>
<span class="pill">192 条有向链路</span>
<span class="pill">H=7 / V=9</span>
<span class="pill">t<sub>rtt</sub>=16</span>
</p>

<h2>1 · 基线与五种方案</h2>
<p><b>基线</b>。没有集中仲裁。每个环站自己决定注入、转弯和弹出，
I-tag 保住饿着的注入，E-tag 保住转不了弯的 flit。
面积全在环站：转发 FIFO、弹出队列、重组缓冲，共 {station:,} bit。仲裁器状态是 0。</p>
<p><b>iSLIP-2D</b>（CA-GiSLIP）。中央仲裁器给每个包指定它占用每一段的哪几拍。
一条流要它经过的每一段同时同意，interval 表把这些拍订死，包发出去就不会再在环上排队。
这页用报告里的环配置：fixed 路径、每源每轮最多 2 个授权、interval。
面积几乎全是这张表。</p>
<p><b>CA-credit</b>。中央仲裁器只回答「现在能不能注入」。
它给包要经过的每一段留一个计数：行环或列环上的链路、上车口、下车口，以及目的 HA 还能挂多少个未完成事务。
计数够就把包放进基线那套环，之后的冲突由环站偏转解决。</p>
<ol>
<li>每一段一个计数。一条链路的初值，是它在一个空载往返里能运走的 flit 数，再乘 scale。
scale=1 时这个数是 {cred['link_credit']}：链路每拍 1 个 flit，空载往返 {cred['link_credit']} 拍。
HA 的初值是 tracker 个数，默认 {cred['ha']}，也就是同时最多挂 {cred['ha']} 个未完成事务。</li>
<li>每拍按源轮询，每个源最多放行 1 个最早的包。
沿途每一段的计数都至少还有 1，才把这些计数减掉。包在 16 拍之后进入环。</li>
<li>包到达目的后，DONE 再过 16 拍回到仲裁器，沿途的计数加回来。
一个包从获准到归还，占的是这一整段往返。仲裁器不指定它占用哪一拍。</li>
</ol>
<p><b>CA-rate</b>。仲裁器不一个包一个包地点头。
每隔 T 拍看各核还积压哪些（源，目的），给每条流一个速率，核按这个速率自己注入。</p>
<ol>
<li>上报的是积压，不是逐个包的请求。分配按最大最小公平：
挤在同一条链路或同一个 HA 上的流平分这一段的容量；
已经分够的流退出，剩下的容量再分给还要的流。</li>
<li>算完的速率再过 32 拍才在源上生效，16 拍用来上报，16 拍用来下发。
每个（源，目的）一个令牌桶，桶里攒满 1 个令牌才注入 1 个包。没有 DONE。</li>
<li>控制消息只有这一来一回。每 T 拍每个核上报一次、仲裁器回一次，共 96 条，和包的多少无关。
这页的代表点是 T=64。T=16 和 T=256 的仲裁器都是同一张速率表，{rate['bits']:,} bit。</li>
</ol>
<p><b>CA-ring</b>（环级信用）。把每条环当成一个整体，仲裁器不看链路，只看环和桥。
一个包从源环出发，经环桥转上目的环，只要转上了目的环就交给环站：
剩下的路由 I-tag / E-tag 带到目的节点，不再经过仲裁器。
源和目的在同一行或同一列的包，一开始就在目的环上，直接走数据面。</p>
<ol>
<li>两种计数。源环每个方向一个：初值是这个方向的带宽时延积，即环段数除以平均第一段跳数，再乘空载往返 {ca1['bridge_credit']} 拍，
两个方向分别是 {ca1['ring_credit_min']} 和 {ca1['ring_credit']} 个 flit。
每座环桥一个：初值就是空载往返 {ca1['bridge_credit']} 拍，桥每拍最多转 1 个 flit。</li>
<li>每拍一轮 iSLIP。每个源对每座桥有 1 位请求，共 336 位。
每座桥在向它请求的 7 个源里轮询选 1 个，条件是桥和这个源的环方向都还有计数。
每个源再在收到的授予里轮询选 1 个。</li>
<li>一次只授予 1 个 flit，16 拍后注入。flit 在桥上转进目的环那一拍，桥发 DONE，再过 16 拍把两个计数加回来。
按 flit 授予，所以 64 flit 的包也不会被小于包长的计数卡住。</li>
</ol>
<p><b>iSLIP-ring</b>（环级 iSLIP）。视角和 CA-ring 一样：只看环和桥，转上目的环就算到。
区别在仲裁方式。CA-ring 是信用计数，只说「现在可以注入」；
iSLIP-ring 沿用 iSLIP-2D，给整个包订一个起始拍，只是订的资源从「每一段」换成「每条环」。</p>
<ol>
<li>一个包订三样，都从起始拍 t0 算：
源注入口，t0 起连续 m 拍，容量 1；
源环的这个方向，同样这 m 拍，容量是这条环的段数 k（行环 8、列环 6），这个包占 h 个，h 是它在这条环上走的跳数；
环桥，从第一个 flit 飞到桥的那拍起连续 m 拍，容量 1。
环是一个整体，不分哪一段，只按「占了几段」记账：同一拍占用的段数加起来不超过 k，就认为放得下。
这只是不冲突的必要条件，不是充分条件。</li>
<li>每轮一次 iSLIP。每个源对每座桥 1 位请求，共 336 位；每座桥轮询选 1 个源授予；
每个源最多接受 2 个授予；再按最长弧优先补满。同一轮每座桥最多 1 个包，每个环方向占用不超过 k。
共 {sr['rounds']} 轮。和 iSLIP-2D 一样，makespan 不计这些轮本身的耗时，只计 16 拍的请求授权往返。</li>
<li>通过的包按最长弧优先，各自订到最早的可行起拍，不早于第 16 拍。
一个包的 m 个 flit 连续注入，之后不再回仲裁器，没有 DONE。
目的环那一段和目的端的弹出口没有人订，仍由 I-tag / E-tag 兜底。</li>
</ol>
<p class="muted">另一种做法是整环独占：一条流把它走过的两条环整条占住。不会冲突，但环段延迟是 {topo.H}–{topo.V} 拍，
一条环被占住很久。同样的 iSLIP，1、16、64 flit 的 makespan 是
{long[1]['sr_excl']:,}、{long[16]['sr_excl']:,}、{long[64]['sr_excl']:,}，比基线慢得多。
所以这里改按段数记账，把冲突交给数据面。</p>

<h2>2 · 同一 all-to-all 的 makespan</h2>
<p>makespan 是最后一条流到达目的端的拍。基线是 <b>{raw['makespan']}</b> 拍。
iSLIP-2D 是 <b class="win">{base_mk}</b> 拍
（其中数据跨度 {isl['data_span']} 拍，前面 16 拍是第一次请求授权），
比基线短 {raw['makespan']-base_mk} 拍。
CA-credit ×1 是 {cred['makespan']} 拍，CA-rate 是 {rate['makespan']} 拍，
CA-ring ×1 是 {ca1['makespan']} 拍，都长于基线
（{cred['makespan']-raw['makespan']} 拍、{rate['makespan']-raw['makespan']} 拍、{ca1['makespan']-raw['makespan']} 拍）。
这批流在第 0 拍就全部在场，基线当场注入。
credit 要先减到计数，并且每个源每拍只放 1 个包。
rate 要等第一份速率走完 32 拍才开始注入；三个周期的完成时间相同，
因为这批流一次就摆齐，第一次分配已经把最热的链路分满。
CA-ring 要等 16 拍的授予才注入，并且每座桥每拍只授予 1 个 flit；
×2 的计数更大，但这批流只有 1 flit，计数不是瓶颈，还是 {ca2['makespan']} 拍。</p>
<p>iSLIP-ring 是 {sr['makespan']} 拍，比基线短 {raw['makespan']-sr['makespan']} 拍，比 CA-ring ×1 短 {ca1['makespan']-sr['makespan']} 拍，
比 iSLIP-2D 长 {sr['makespan']-base_mk} 拍。表里最后一个包订在第 {long[1]['sr_start']} 拍开始注入，
环上另有 {long[1]['sr_defl']} 次偏转，是桥上转弯冲突。
若按一轮占 1 拍计这 {sr['rounds']} 轮，makespan 是 {long[1]['sr_rl1']}，比不计时长 {long[1]['sr_rl1']-sr['makespan']} 拍，已经慢于基线。</p>
<p class="muted">基线这 {N_FLOWS:,} 个包里偏转 {raw['defl']} 次。
时钟差 2 拍：iSLIP 的 makespan 含弹出斜坡 RAMP=2；
基线、credit、rate、CA-ring 记的是 flit 进入目的弹出队列的拍。这 2 拍不改变排序。</p>
<table>
<thead><tr><th>方案</th><th>makespan</th><th>相对 iSLIP</th>
<th>仲裁器 bit</th><th>系统 bit</th></tr></thead>
<tbody>
{body}
</tbody></table>
<p class="muted">高亮行是正文比较的点。系统 bit = 仲裁器 + 环站存储。
基线和 iSLIP 的两列含义不同：基线没有仲裁器，iSLIP 删掉了环站队列。
credit / rate / CA-ring 在基线的 {station:,} bit 上再加自己的仲裁器。
CA-ring ×1 的仲裁器只有 {ca1['bits']:,} bit，是 credit ×1 的 {100*ca1['bits']/cred['bits']:.0f}%，是 iSLIP 的 {100*ca1['bits']/ic['bits']:.1f}%。
iSLIP-ring 是 {sr['bits']:,} bit，其中区间表 {sr_tbl:,} bit
（{sr['breakdown']['interval_ring_dir']:,} 环方向、{sr['breakdown']['interval_bridge']:,} 桥、{sr['breakdown']['interval_inject']:,} 注入口，每张表 16 段区间），
是 iSLIP-2D 的 {100*sr['bits']/ic['bits']:.0f}%，是 CA-ring ×1 的 {sr['bits']/ca1['bits']:.0f} 倍。
区间表用 12 位时间戳，和 iSLIP-2D 同一口径，长包下没有重新加宽。
把 HA tracker 从 {cred['ha']} 收到 16，同时能挂起的事务变少，
credit 的 makespan 从 {cred['makespan']} 变成 {ha['makespan']}。</p>

<h2>3 · 面积 / makespan Pareto</h2>
<p>两轴都是越小越好，好的角在左下。第一张图只比较有集中状态的方案，横轴是仲裁器 bit。
基线的仲裁器是 0，不在对数轴上。第二张图把环站存储算进去。</p>
<div class="fig">{_svg(arb)}
<div class="cap">仲裁器前沿：{names}。
CA-ring ×1 的仲裁器只有 {ca1['bits']:,} bit，是 credit ×1（{cred['bits']:,}）的 {100*ca1['bits']/cred['bits']:.0f}%，
makespan 多 {ca1['makespan']-cred['makespan']} 拍。
iSLIP-ring 比 credit ×1 快 {cred['makespan']-sr['makespan']} 拍，状态多 {sr['bits']/cred['bits']:.1f} 倍；
比 iSLIP-2D 慢 {sr['makespan']-base_mk} 拍，状态只有它的 {100*sr['bits']/ic['bits']:.0f}%。
iSLIP 比 credit ×1 再快 {cred['makespan']-base_mk} 拍，状态是 {ic['bits']:,} bit。
CA-ring ×2 与 ×1 的 makespan 相同、状态更多；rate 比 credit 更大也更慢。</div></div>
<div class="fig">{_svg_system(pts)}
<div class="cap">系统前沿：{snames}。
基线 {station:,} bit、{raw['makespan']} 拍。iSLIP 把环站换成区间表之后，
面积少 {station-ic['bits']:,} bit，makespan 也更短，所以基线落在前沿内侧。
credit、rate、CA-ring 和 iSLIP-ring 既背着环站、又多一段准入延迟。
iSLIP-ring 还多一张 {sr_tbl:,} bit 的区间表，系统 bit {sr['system']:,}，makespan 只比基线短 {raw['makespan']-sr['makespan']} 拍；
其余几个 1 flit 时两轴都差于基线。
包长到 64 flit 时 CA-ring 比基线快，见第 4 节。</div></div>

<h2>4 · 64 flit 的包</h2>
<p>64 flit 是<b>每个 src-dst 的包长</b>，节点数和流数不变，还是 2,256 条。
面积模型里一个 flit 是 {W_FLIT} bit，也就是 {flit_b} byte。
所以 1 flit = {flit_b} byte，16 flit = {16 * flit_b} byte，
64 flit = {64 * flit_b:,} byte = {kib} KiB。
每个节点向另外 47 个节点各发 {kib} KiB，自己发出 47 KiB，也收到 47 KiB。
全网 {N_FLOWS:,} KiB。</p>
<p>最热链路在 1 flit 时过 {wide['hot']} 个包，64 flit 时就要串行
{wide['link_lb']:,} 个 flit。iSLIP 把这一整包订成连续的 64 拍，
makespan <b class="win">{wide['islip']:,}</b>，是基线的 {_vs(wide['islip'], wide['base'])}，
也是这条下限的 {wide['islip']/wide['link_lb']:.2f} 倍。
基线是 {wide['base']:,}。credit ×1 的链路计数只有 {wide['c1_credit']}，
一个包就要 64，同一条链路上放不下第二个包，要等 DONE 回来，
所以是 {wide['c1']:,}，慢于基线。计数加倍到 {wide['c2_credit']} 可以叠两个包，
makespan {wide['c2']:,}，是基线的 {_vs(wide['c2'], wide['base'])}。
rate 是 {wide['rate']:,}，{_vs(wide['rate'], wide['base'])}，三个周期相同。
credit ×1 是 {_vs(wide['c1'], wide['base'])}。</p>
<p>CA-ring ×1 是 <b class="win">{wide['ca1']:,}</b>，{_vs(wide['ca1'], wide['base'])}，
×2 是 {wide['ca2']:,}，{_vs(wide['ca2'], wide['base'])}。
它比基线快，环上偏转只有 {wide['ca_defl']:,} 次，基线是 {wide['defl']:,} 次。
原因有两个：每座桥每拍只授予 1 个 flit，源环的流量被限在带宽时延积以内；
CA-ring 的源队列按桥分开，一个源同时往 7 个方向发，而基线的源队列按目的号排队，
48 个源在同一时刻打向同一个目的。第 5 节把这一项去掉。
但它没有预订时隙，所以仍是 iSLIP 的 {wide['ca1']/wide['islip']:.2f} 倍。
16 flit 时 CA-ring ×1 是 {long[16]['ca1']:,}，是基线的 {_vs(long[16]['ca1'], long[16]['base'])}。</p>
<p>iSLIP-ring 是 <b class="win">{wide['sr']:,}</b>，{_vs(wide['sr'], wide['base'])}，是 iSLIP-2D 的 {wide['sr']/wide['islip']:.2f} 倍，
也比 CA-ring ×1 慢 {wide['sr']-wide['ca1']:,} 拍。16 flit 时是 {long[16]['sr']:,}，比 CA-ring ×1 慢 {long[16]['sr']-long[16]['ca1']:,} 拍。
所以包一长，它在两个指标上都被 CA-ring 压住：状态多 {sr['bits']/ca1['bits']:.0f} 倍，makespan 还更长。
表里最后一个包在第 {wide['sr_start']:,} 拍开始注入，数据面却花了 {wide['sr']:,} 拍才送完，
中间的差距是 {wide['sr_defl']:,} 次偏转，其中 {wide['sr_fifo']:,} 次是 flit 到了桥，而桥的转发 FIFO 已满。
环级记账不能保证两个包在桥上同一拍转弯，冲突就落在这一点上。
再把弹出口和目的环也订上，1、16、64 flit 是 {long[1]['sr_full']:,}、{long[16]['sr_full']:,}、{long[64]['sr_full']:,}，
1 flit 反而变慢，64 flit 只好 {100*(1-long[64]['sr_full']/wide['sr']):.0f}%，表也更大，所以正文用不订这两项的版本。</p>
<table>
<thead><tr><th>方案</th><th>1 flit<br><span class="muted">{flit_b} B</span></th>
<th>16 flit<br><span class="muted">{16 * flit_b} B</span></th>
<th>64 flit<br><span class="muted">{kib} KiB</span></th>
<th>64 flit / 基线</th></tr></thead>
<tbody>
<tr><td>链路下限</td><td>{long[1]['link_lb']:,}</td><td>{long[16]['link_lb']:,}</td><td>{long[64]['link_lb']:,}</td><td>{_vs(long[64]['link_lb'], wide['base'])}</td></tr>
<tr class="hl"><td>iSLIP-2D</td><td>{long[1]['islip']:,}</td><td>{long[16]['islip']:,}</td><td><b>{long[64]['islip']:,}</b></td><td><b>{_vs(long[64]['islip'], wide['base'])}</b></td></tr>
<tr class="hl"><td>基线</td><td>{long[1]['base']:,}</td><td>{long[16]['base']:,}</td><td><b>{long[64]['base']:,}</b></td><td>1</td></tr>
<tr><td>credit ×2</td><td>{_cell(long[1]['c2'])}</td><td>{_cell(long[16]['c2'])}</td><td>{_cell(long[64]['c2'])}</td><td>{_vs(long[64]['c2'], wide['base'])}</td></tr>
<tr class="hl"><td>credit ×1</td><td>{_cell(long[1]['c1'])}</td><td>{_cell(long[16]['c1'])}</td><td><b>{_cell(long[64]['c1'])}</b></td><td>{_vs(long[64]['c1'], wide['base'])}</td></tr>
<tr class="hl"><td>rate</td><td>{long[1]['rate']:,}</td><td>{long[16]['rate']:,}</td><td><b>{long[64]['rate']:,}</b></td><td>{_vs(long[64]['rate'], wide['base'])}</td></tr>
<tr><td>CA-ring ×2</td><td>{long[1]['ca2']:,}</td><td>{long[16]['ca2']:,}</td><td>{long[64]['ca2']:,}</td><td>{_vs(long[64]['ca2'], wide['base'])}</td></tr>
<tr class="hl"><td>CA-ring ×1</td><td>{long[1]['ca1']:,}</td><td>{long[16]['ca1']:,}</td><td><b>{long[64]['ca1']:,}</b></td><td><b>{_vs(long[64]['ca1'], wide['base'])}</b></td></tr>
<tr class="hl"><td>iSLIP-ring</td><td>{long[1]['sr']:,}</td><td>{long[16]['sr']:,}</td><td><b>{long[64]['sr']:,}</b></td><td><b>{_vs(long[64]['sr'], wide['base'])}</b></td></tr>
</tbody></table>
<div class="fig">{_svg_len([long[1], long[16], long[64]])}
<div class="cap">纵轴是 makespan，横轴是每个 src-dst 的 flit 数，都是对数。
虚线是最热链路要串行的 flit 数，makespan 不能低于它。</div></div>
<p class="muted">64 flit 时基线偏转 {wide['defl']:,} 次。
目的端重组缓冲是 64 flit，刚好一个包，多出来的 flit 把这个计数打满了
{wide['reasm_ovf']:,} 次；包没有被丢掉，只是在环上多转。
rate 的 T=16、64、256 在 16 flit 和 64 flit 都是同一个 makespan。</p>
<p class="muted">CA-ring 的环计数取带宽时延积。若只取环上同时在飞的 flit 数（行环 8 段 × 7 拍 = 56），
计数太小，会把环限死：1、16、64 flit 分别是 {long[1]['ca_inflight']:,}、{long[16]['ca_inflight']:,}、{long[64]['ca_inflight']:,}，
64 flit 比基线慢 {_vs(long[64]['ca_inflight'], wide['base'])}。
CA-ring 把每个 flit 当独立子包送入数据面，目的端重组缓冲在这里不限流，和基线一样只做统计。
64 flit 时 CA-ring ×1 每拍约 {wide['ca_ctrl']:.0f} 条控制消息（请求、授予、DONE），仲裁器状态小，控制流量大。
iSLIP-ring 的控制消息只有请求、授予、接受，共 {wide['sr_ctrl']:,} 条，和包长无关，64 flit 时平均每拍 {wide['sr_ctrl_cy']:.2f} 条。</p>
<h2>5 · 激励顺序的影响</h2>
<p>前面所有行用同一个顺序：每个源按目的号 0 到 47 依次排队，所以第一批包全部冲向节点 0，再冲向节点 1。
这对基线和 credit、rate、CA-ring、iSLIP-ring 的数据面都是同步的 incast。
改成错开：源 s 按 s+1、s+2、… 的顺序发，各源同一时刻打向不同的目的。
iSLIP 的调度只看整批流，不看到达顺序，数字不变。</p>
<table>
<thead><tr><th>方案</th><th>1 flit</th><th>16 flit</th><th>64 flit</th><th>64 flit / 基线</th></tr></thead>
<tbody>
<tr class="hl"><td>iSLIP-2D</td><td>{long[1]['islip']:,}</td><td>{long[16]['islip']:,}</td><td><b>{long[64]['islip']:,}</b></td><td><b>{_vs(long[64]['islip'], stag[64]['base'])}</b></td></tr>
<tr class="hl"><td>基线（错开）</td><td>{stag[1]['base']:,}</td><td>{stag[16]['base']:,}</td><td><b>{stag[64]['base']:,}</b></td><td>1</td></tr>
<tr><td>credit ×1（错开）</td><td>{stag[1]['c1']:,}</td><td>{stag[16]['c1']:,}</td><td>{stag[64]['c1']:,}</td><td>{_vs(stag[64]['c1'], stag[64]['base'])}</td></tr>
<tr><td>rate（错开）</td><td>{stag[1]['rate']:,}</td><td>{stag[16]['rate']:,}</td><td>{stag[64]['rate']:,}</td><td>{_vs(stag[64]['rate'], stag[64]['base'])}</td></tr>
<tr class="hl"><td>CA-ring ×1（错开）</td><td>{stag[1]['ca1']:,}</td><td>{stag[16]['ca1']:,}</td><td><b>{stag[64]['ca1']:,}</b></td><td><b>{_vs(stag[64]['ca1'], stag[64]['base'])}</b></td></tr>
<tr class="hl"><td>iSLIP-ring（错开）</td><td>{stag[1]['sr']:,}</td><td>{stag[16]['sr']:,}</td><td><b>{stag[64]['sr']:,}</b></td><td><b>{_vs(stag[64]['sr'], stag[64]['base'])}</b></td></tr>
</tbody></table>
<p>错开之后，基线 1 flit 是 {stag[1]['base']}，已经短于 iSLIP 的 {long[1]['islip']}。
64 flit 时基线从 {wide['base']:,} 降到 {stag[64]['base']:,}，CA-ring ×1 从 {wide['ca1']:,} 降到 {stag[64]['ca1']:,}，
相对基线是 {_vs(stag[64]['ca1'], stag[64]['base'])}，比第 4 节的 {_vs(wide['ca1'], wide['base'])} 窄。
iSLIP 在 64 flit 仍是基线的 {_vs(long[64]['islip'], stag[64]['base'])}。
iSLIP-ring 错开后是 {stag[1]['sr']}、{stag[16]['sr']:,}、{stag[64]['sr']:,}，
三档都比错开的基线慢（{stag[1]['base']}、{stag[16]['base']:,}、{stag[64]['base']:,}），64 flit 时是基线的 {_vs(stag[64]['sr'], stag[64]['base'])}。
第 4 节里 iSLIP-ring 比基线快，主要是基线那个同步 incast 的顺序太差；换成错开的顺序，它就没有优势了。
所以 CA-ring 比基线快的幅度，有一部分来自排队方式，另一部分才是仲裁本身；
iSLIP 在长包上的优势不受这一项影响。</p>
<p class="muted">重新生成：<code>python3 utils/gen_ring2d_schemes.py</code></p>
</div></body></html>
"""
    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
    for p in pts:
        mark = "*" if p["front"] else " "
        print(f"  {mark} {p['name']:16} mk={p['makespan']:4} "
              f"bits={p['bits']:7} sys={p['system']:7}")


if __name__ == "__main__":
    main()
