#!/usr/bin/env python3
"""Concise 2D-ring report: baseline, iSLIP-2D, CA-credit, CA-rate.

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
        "credit ×0.5": (18, -18, "start", "credit ×0.5"),
        "credit ×1": (22, 4, "start", "credit ×1"),
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
    for g in (190_000, 200_000, 210_000, 220_000):
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
            if g["system"] == p["system"] and g["makespan"] == p["makespan"]:
                g["names"].append(p["name"])
                g["sfront"] = g["sfront"] or p["sfront"]
                break
        else:
            groups.append({**p, "names": [p["name"]]})
    label_at = {
        "基线": (14, 18, "start", "基线"),
        "iSLIP-2D": (-12, -16, "end", "iSLIP-2D"),
        "credit ×0.5": (28, -18, "start", "credit ×0.5"),
        "credit ×1": (28, -4, "start", "credit ×1"),
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


def _finish(sim, *, limit: int = 300_000, stall: int = 25_000) -> dict:
    for s in range(48):
        for d in range(48):
            if s != d:
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
    for scale in (0.5, 1.0, 2.0):
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
    return out


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
    for scale in (0.5, 1.0, 2.0):
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

    def row(p: dict, hl: bool = False) -> str:
        cls = ' class="hl"' if hl else ""
        ratio = p["makespan"] / base_mk
        rel = "—" if p["name"] == "iSLIP-2D" else f"{ratio:.2f}×"
        return (f"<tr{cls}><td>{p['name']}<br><span class='muted'>{p['note']}</span></td>"
                f"<td><b>{p['makespan']:,}</b></td><td>{rel}</td>"
                f"<td>{p['bits']:,}</td><td>{p['system']:,}</td></tr>")

    order = ["基线", "iSLIP-2D", "credit ×0.5", "credit ×1", "credit ×2",
             "credit HA=16", "rate T=16", "rate T=64", "rate T=256"]
    by = {p["name"]: p for p in pts}
    body = "".join(row(by[n], n in ("基线", "iSLIP-2D", "credit ×1", "rate T=64"))
                   for n in order)
    names = "、".join(p["name"] for p in sorted(
        (p for p in arb if p["front"]), key=lambda p: p["bits"]))
    snames = "、".join(p["name"] for p in sorted(
        (p for p in pts if p["sfront"]), key=lambda p: p["system"]))
    c2 = next(p for p in pts if p["name"] == "credit ×2")
    c05 = next(p for p in pts if p["name"] == "credit ×0.5")
    long = {1: {
        "m": 1,
        "islip": base_mk,
        "base": raw["makespan"],
        "c0.5": c05["makespan"],
        "c1": cred["makespan"],
        "c2": c2["makespan"],
        "c0.5_credit": c05["link_credit"],
        "c1_credit": cred["link_credit"],
        "c2_credit": c2["link_credit"],
        "rate": rate["makespan"],
        "hot": 60,
        "link_lb": 60,
        "defl": raw["defl"],
        "reasm_ovf": None,
    }}
    for m in (16, 64):
        long[m] = _measure_m(m)
    wide = long[64]
    flit_b = W_FLIT // 8
    kib = 64 * flit_b // 1024

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>2D 环 · 基线 / iSLIP-2D / CA-credit / CA-rate</title>
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
<h1>2D 环：基线与三种准入的 makespan</h1>
<p class="muted">只看维度切片 2D 环。同一批 all-to-all：48 节点、每对节点一条流，共 {N_FLOWS:,} 条。
第 1–3 节每条流是 1 个 flit（{flit_b} byte）。第 4 节把包长加到 64 flit。
基线、CA-credit、CA-rate 都在环数据面上排完；
iSLIP-2D 在 1 flit 时的完成时间来自 <code>results/islip2d_8x6.json</code>。</p>
<p>
<span class="pill">6 行环 × 8 + 8 列环 × 6</span>
<span class="pill">192 条有向链路</span>
<span class="pill">H=7 / V=9</span>
<span class="pill">t<sub>rtt</sub>=16</span>
</p>

<h2>1 · 基线与三种方案</h2>
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

<h2>2 · 同一 all-to-all 的 makespan</h2>
<p>makespan 是最后一条流到达目的端的拍。基线是 <b>{raw['makespan']}</b> 拍。
iSLIP-2D 是 <b class="win">{base_mk}</b> 拍
（其中数据跨度 {isl['data_span']} 拍，前面 16 拍是第一次请求授权），
比基线短 {raw['makespan']-base_mk} 拍。
CA-credit ×1 是 {cred['makespan']} 拍，CA-rate 是 {rate['makespan']} 拍，
都长于基线（{cred['makespan']-raw['makespan']} 拍、{rate['makespan']-raw['makespan']} 拍）。
这批流在第 0 拍就全部在场，基线当场注入。
credit 要先减到计数，并且每个源每拍只放 1 个包。
rate 要等第一份速率走完 32 拍才开始注入；三个周期的完成时间相同，
因为这批流一次就摆齐，第一次分配已经把最热的链路分满。</p>
<p class="muted">基线这 {N_FLOWS:,} 个包里偏转 {raw['defl']} 次。
时钟差 2 拍：iSLIP 的 makespan 含弹出斜坡 RAMP=2；
基线、credit、rate 记的是 flit 进入目的弹出队列的拍。这 2 拍不改变排序。</p>
<table>
<thead><tr><th>方案</th><th>makespan</th><th>相对 iSLIP</th>
<th>仲裁器 bit</th><th>系统 bit</th></tr></thead>
<tbody>
{body}
</tbody></table>
<p class="muted">高亮行是正文比较的点。系统 bit = 仲裁器 + 环站存储。
基线和 iSLIP 的两列含义不同：基线没有仲裁器，iSLIP 删掉了环站队列。
credit / rate 在基线的 {station:,} bit 上再加自己的仲裁器。
把 HA tracker 从 {cred['ha']} 收到 16，同时能挂起的事务变少，
credit 的 makespan 从 {cred['makespan']} 变成 {ha['makespan']}。</p>

<h2>3 · 面积 / makespan Pareto</h2>
<p>两轴都是越小越好，好的角在左下。第一张图只比较有集中状态的方案，横轴是仲裁器 bit。
基线的仲裁器是 0，不在对数轴上。第二张图把环站存储算进去。</p>
<div class="fig">{_svg(arb)}
<div class="cap">仲裁器前沿：{names}。
credit ×1 的仲裁器状态约为 iSLIP 的 {100*cred['bits']/ic['bits']:.0f}%，
makespan 多 {cred['makespan']-base_mk} 拍，并且长于基线的 {raw['makespan']} 拍。
rate 比 credit 更大也更慢。credit ×2 与 ×1 的 makespan 相同、状态更多。</div></div>
<div class="fig">{_svg_system(pts)}
<div class="cap">系统前沿：{snames}。
基线 {station:,} bit、{raw['makespan']} 拍。iSLIP 把环站换成区间表之后，
面积少 {station-ic['bits']:,} bit，makespan 也更短，所以基线落在前沿内侧。
credit 和 rate 既背着环站、又多一段准入延迟，两轴都差于基线。</div></div>

<h2>4 · 64 flit 的包</h2>
<p>64 flit 是<b>每个 src-dst 的包长</b>，节点数和流数不变，还是 2,256 条。
面积模型里一个 flit 是 {W_FLIT} bit，也就是 {flit_b} byte。
所以 1 flit = {flit_b} byte，16 flit = {16 * flit_b} byte，
64 flit = {64 * flit_b:,} byte = {kib} KiB。
每个节点向另外 47 个节点各发 {kib} KiB，自己发出 47 KiB，也收到 47 KiB。
全网 {N_FLOWS:,} KiB。</p>
<p>最热链路在 1 flit 时过 {wide['hot']} 个包，64 flit 时就要串行
{wide['link_lb']:,} 个 flit。iSLIP 把这一整包订成连续的 64 拍，
makespan <b class="win">{wide['islip']:,}</b>，是这条下限的
{wide['islip']/wide['link_lb']:.2f} 倍。
基线是 {wide['base']:,}。credit ×1 的链路计数只有 {wide['c1_credit']}，
一个包就要 64，同一条链路上放不下第二个包，要等 DONE 回来，
所以是 {wide['c1']:,}，慢于基线。计数加倍到 {wide['c2_credit']} 可以叠两个包，
makespan {wide['c2']:,}，短于基线。×0.5 的计数是 {wide['c0.5_credit']}，
小于 64，一个包都放不出去。rate 是 {wide['rate']:,}，三个周期相同。</p>
<table>
<thead><tr><th>方案</th><th>1 flit<br><span class="muted">{flit_b} B</span></th>
<th>16 flit<br><span class="muted">{16 * flit_b} B</span></th>
<th>64 flit<br><span class="muted">{kib} KiB</span></th></tr></thead>
<tbody>
<tr><td>链路下限</td><td>{long[1]['link_lb']:,}</td><td>{long[16]['link_lb']:,}</td><td>{long[64]['link_lb']:,}</td></tr>
<tr class="hl"><td>iSLIP-2D</td><td>{long[1]['islip']:,}</td><td>{long[16]['islip']:,}</td><td><b>{long[64]['islip']:,}</b></td></tr>
<tr class="hl"><td>基线</td><td>{long[1]['base']:,}</td><td>{long[16]['base']:,}</td><td><b>{long[64]['base']:,}</b></td></tr>
<tr><td>credit ×2</td><td>{_cell(long[1]['c2'])}</td><td>{_cell(long[16]['c2'])}</td><td>{_cell(long[64]['c2'])}</td></tr>
<tr class="hl"><td>credit ×1</td><td>{_cell(long[1]['c1'])}</td><td>{_cell(long[16]['c1'])}</td><td><b>{_cell(long[64]['c1'])}</b></td></tr>
<tr><td>credit ×0.5</td><td>{_cell(long[1]['c0.5'])}</td><td>{_cell(long[16]['c0.5'])}</td><td>{_cell(long[64]['c0.5'])}</td></tr>
<tr class="hl"><td>rate</td><td>{long[1]['rate']:,}</td><td>{long[16]['rate']:,}</td><td><b>{long[64]['rate']:,}</b></td></tr>
</tbody></table>
<div class="fig">{_svg_len([long[1], long[16], long[64]])}
<div class="cap">纵轴是 makespan，横轴是每个 src-dst 的 flit 数，都是对数。
虚线是最热链路要串行的 flit 数，makespan 不能低于它。
credit ×0.5 在 64 flit 放不出包，没有点。</div></div>
<p class="muted">64 flit 时基线偏转 {wide['defl']:,} 次。
目的端重组缓冲是 64 flit，刚好一个包，多出来的 flit 把这个计数打满了
{wide['reasm_ovf']:,} 次；包没有被丢掉，只是在环上多转。
rate 的 T=16、64、256 在 16 flit 和 64 flit 都是同一个 makespan。</p>
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
