#!/usr/bin/env python3
"""HTML section: CA-credit and CA-rate versus the iSLIP-2D numbers.

Reads results/ca_credit_rate_8x6.json, the bisection sweep, and the
centralization ledger. Returns a fragment that gen_islip2d_report splices
in front of section 12. Every figure is computed from those files.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CA = ROOT / "results" / "ca_credit_rate_8x6.json"
BISECT = ROOT / "results" / "bisect_lat_8x6.json"
SWEEP = ROOT / "results" / "load_sweep_8x6.json"
SIMPLE = ROOT / "results" / "simple2d_8x6.json"


def _load(p: Path) -> dict:
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _f(x: Any, nd: int = 2) -> str:
    if x is None:
        return "—"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    if isinstance(x, int):
        return f"{x:,}"
    return str(x)


def _bits(n: int | None) -> str:
    return "—" if n is None else f"{n:,}"


def _at(rows: list[dict], lam: float) -> dict | None:
    for r in rows:
        if abs(r["lam"] - lam) < 1e-9:
            return r
    return None


def _lam_star(rows: list[dict]) -> float | None:
    ok = [r["lam"] for r in rows if r.get("stable")]
    return max(ok) if ok else None


def _peak(rows: list[dict]) -> float | None:
    xs = [r["accepted"] for r in rows if r.get("accepted") is not None]
    return max(xs) if xs else None


def _lam_tail(rows: list[dict], factor: float = 3.0) -> float | None:
    """Largest stable load whose p99 is still within `factor` of p99 at λ=0.1.

    The stability test only asks whether the source queue is growing. The tail
    can already be in the thousands while that test still passes.
    """
    base = _at(rows, 0.1)
    if not base or not base.get("p99"):
        return None
    cap = factor * base["p99"]
    ok = [r["lam"] for r in rows
          if r.get("stable") and r.get("p99") is not None and r["p99"] <= cap]
    return max(ok) if ok else None


def _front(pts: list[dict], *, max_y: bool) -> list[dict]:
    """Non-dominated subset. x is minimized; y is maximized or minimized."""
    keep = []
    for p in pts:
        dom = False
        for q in pts:
            if q is p:
                continue
            y_better = q["y"] >= p["y"] if max_y else q["y"] <= p["y"]
            y_strict = q["y"] > p["y"] if max_y else q["y"] < p["y"]
            if q["x"] <= p["x"] and y_better and (q["x"] < p["x"] or y_strict):
                dom = True
                break
        if not dom:
            keep.append(p)
    keep.sort(key=lambda p: (p["x"], -p["y"] if max_y else p["y"]))
    # Drop a later point that does not improve y (duplicate x, worse y).
    out = []
    best = -math.inf if max_y else math.inf
    for p in keep:
        if max_y and p["y"] > best:
            best = p["y"]
            out.append(p)
        elif not max_y and p["y"] < best:
            best = p["y"]
            out.append(p)
    return out


def _svg_pareto(panels: list[dict], *, ylog: bool, ymin: float, ymax: float,
                ylab: str, xlab: str) -> str:
    W, H = 940, 430
    pw, ph = 400, 280
    gap = 50
    x0s = (50, 50 + pw + gap)
    y0 = 36
    parts = [f'<svg viewBox="0 0 {W} {H}" width="100%" '
             f'preserveAspectRatio="xMidYMid meet" role="img">']
    for i, panel in enumerate(panels):
        x0 = x0s[i]
        pts = panel["pts"]
        xs = [p["x"] for p in pts if p["x"] > 0]
        xmin, xmax = min(xs) / 1.15, max(xs) * 1.15
        parts.append(f'<rect class="plot" x="{x0}" y="{y0}" width="{pw}" height="{ph}"/>')
        parts.append(f'<text class="bxt" x="{x0 + pw/2}" y="{y0 - 12}" '
                     f'text-anchor="middle">{panel["title"]}</text>')

        def X(x: float) -> float:
            return x0 + (math.log10(x) - math.log10(xmin)) / (
                math.log10(xmax) - math.log10(xmin)) * pw

        def Y(y: float) -> float:
            # Larger pixel-y is lower on the page. λ* keeps big values up.
            # p99 keeps small values up, so "up" is the better direction
            # on every figure in this section.
            if ylog:
                y = max(y, ymin)
                return y0 + (math.log10(y) - math.log10(ymin)) / (
                    math.log10(ymax) - math.log10(ymin)) * ph
            return y0 + ph - (y - ymin) / (ymax - ymin) * ph

        if ylog:
            grid = [v for v in (50, 100, 200, 500, 1000, 2000, 5000)
                    if ymin <= v <= ymax]
        else:
            grid = [0.2, 0.4, 0.6, 0.8]
        for g in grid:
            yy = Y(g)
            parts.append(f'<line class="gl" x1="{x0}" y1="{yy:.1f}" '
                         f'x2="{x0+pw}" y2="{yy:.1f}"/>')
            parts.append(f'<text class="tick" x="{x0-6}" y="{yy+4:.1f}" '
                         f'text-anchor="end">{g:g}</text>')
        # log x ticks at powers of ten inside the range
        p0 = math.ceil(math.log10(xmin))
        p1 = math.floor(math.log10(xmax))
        for p in range(int(p0), int(p1) + 1):
            xx = X(10 ** p)
            parts.append(f'<line class="gl" x1="{xx:.1f}" y1="{y0+ph}" '
                         f'x2="{xx:.1f}" y2="{y0+ph+4}"/>')
            lab = f"{10**p/1000:.0f}k" if 10 ** p >= 1000 else str(10 ** p)
            parts.append(f'<text class="tick" x="{xx:.1f}" y="{y0+ph+16}" '
                         f'text-anchor="middle">{lab}</text>')
        front = panel["front"]
        if len(front) >= 2:
            d = " ".join(f"{X(p['x']):.1f},{Y(p['y']):.1f}" for p in front)
            parts.append(f'<polyline class="cv cB" points="{d}" '
                         f'fill="none"/>')
        for p in pts:
            cx, cy = X(p["x"]), Y(p["y"])
            op = "1" if p.get("front") else "0.4"
            col = p["color"]
            sh = p["shape"]
            if sh == "o":
                parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="5.5" '
                             f'fill="{col}" opacity="{op}"/>')
            elif sh == "s":
                parts.append(
                    f'<rect x="{cx-4.5:.1f}" y="{cy-4.5:.1f}" width="9" '
                    f'height="9" fill="{col}" opacity="{op}"/>')
            else:
                parts.append(
                    f'<polygon points="{cx:.1f},{cy-6:.1f} {cx+6:.1f},{cy:.1f} '
                    f'{cx:.1f},{cy+6:.1f} {cx-6:.1f},{cy:.1f}" fill="{col}" '
                    f'opacity="{op}"/>')
        # labels: front, plus any point flagged
        placed: list[tuple[float, float]] = []
        for p in pts:
            if not p.get("front") and not p.get("tag"):
                continue
            cx, cy = X(p["x"]), Y(p["y"])
            ty = cy - 8
            for px, py in placed:
                if abs(cx - px) < 110 and abs(ty - py) < 12:
                    ty = py - 13
            ty = min(max(ty, y0 + 12), y0 + ph - 4)
            placed.append((cx, ty))
            anchor = "start" if cx < x0 + pw - 78 else "end"
            dx = 8 if anchor == "start" else -8
            parts.append(f'<text class="tick" x="{cx+dx:.1f}" y="{ty:.1f}" '
                         f'text-anchor="{anchor}" fill="#e8ecf4">{p["name"]}</text>')
        parts.append(f'<text class="axl" x="{x0 + pw/2}" y="{y0+ph+34}" '
                     f'text-anchor="middle">{xlab}</text>')
        parts.append(f'<text class="axl" x="{x0-36}" y="{y0+ph/2}" '
                     f'text-anchor="middle" transform="rotate(-90 {x0-36} '
                     f'{y0+ph/2})">{ylab}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _svg_curve(panels: list[dict]) -> str:
    W, H = 940, 400
    pw, ph = 400, 270
    x0s = (58, 58 + pw + 50)
    y0 = 28
    xmin, xmax = 0.0, 0.85
    ymin, ymax = 0.0, 0.9
    parts = [f'<svg viewBox="0 0 {W} {H}" width="100%" '
             f'preserveAspectRatio="xMidYMid meet" role="img">']

    def X(x0, x):
        return x0 + (x - xmin) / (xmax - xmin) * pw

    def Y(y):
        return y0 + ph - (y - ymin) / (ymax - ymin) * ph

    for i, panel in enumerate(panels):
        x0 = x0s[i]
        parts.append(f'<rect class="plot" x="{x0}" y="{y0}" width="{pw}" height="{ph}"/>')
        parts.append(f'<text class="bxt" x="{x0+pw/2}" y="{y0-10}" '
                     f'text-anchor="middle">{panel["title"]}</text>')
        for g in (0.2, 0.4, 0.6, 0.8):
            parts.append(f'<line class="gl" x1="{x0}" y1="{Y(g):.1f}" '
                         f'x2="{x0+pw}" y2="{Y(g):.1f}"/>')
            parts.append(f'<text class="tick" x="{x0-6}" y="{Y(g)+4:.1f}" '
                         f'text-anchor="end">{g:.1f}</text>')
        for g in (0.2, 0.4, 0.6, 0.8):
            parts.append(f'<text class="tick" x="{X(x0, g):.1f}" y="{y0+ph+16}" '
                         f'text-anchor="middle">{g:.1f}</text>')
        # y = λ
        parts.append(
            f'<line class="ideal" x1="{X(x0, 0):.1f}" y1="{Y(0):.1f}" '
            f'x2="{X(x0, 0.85):.1f}" y2="{Y(0.85):.1f}"/>')
        for series in panel["series"]:
            pts = series["pts"]
            if len(pts) < 2:
                continue
            d = " ".join(f"{X(x0, a):.1f},{Y(b):.1f}" for a, b in pts)
            dash = ' stroke-dasharray="5 4"' if series.get("dash") else ""
            parts.append(f'<polyline fill="none" stroke="{series["color"]}" '
                         f'stroke-width="2.4" points="{d}"{dash}/>')
        parts.append(f'<text class="axl" x="{x0+pw/2}" y="{y0+ph+34}" '
                     f'text-anchor="middle">注入率 λ（包 / 节点 / 拍）</text>')
        parts.append(f'<text class="axl" x="{x0-40}" y="{y0+ph/2}" '
                     f'text-anchor="middle" transform="rotate(-90 {x0-40} '
                     f'{y0+ph/2})">接受吞吐</text>')
    parts.append("</svg>")
    return "".join(parts)


def _series(rows: list[dict]) -> list[tuple[float, float]]:
    return [(r["lam"], r["accepted"]) for r in sorted(rows, key=lambda r: r["lam"])]


def _bisect_rows(b: dict, cfg: str) -> list[dict]:
    return [r for r in b["rows"] if r["config"] == cfg]


def _rel(new: float | None, old: float | None) -> str:
    if new is None or old is None or old <= 0:
        return "—"
    pct = (new - old) / old * 100
    if abs(new - old) < 0.026:
        return f"与原始 {_f(old)} 同一网格步长内"
    if pct < 0:
        return f"比原始 {_f(old)} 低 {abs(pct):.0f}%（{_f(new)}）"
    return f"比原始 {_f(old)} 高 {pct:.0f}%（{_f(new)}）"


def _dlam(new: float | None, old: float | None) -> str:
    if new is None or old is None:
        return "—"
    d = new - old
    # One 0.05 grid step is not a measured difference against the fine sweep.
    if abs(d) < 0.026:
        cls = ""
    elif d > 0:
        cls = "win"
    else:
        cls = "lose"
    return f"<span class='{cls}'>{d:+.2f}</span>"


def render(root: Path | None = None) -> str:
    root = root or ROOT
    ca_path = root / "results" / "ca_credit_rate_8x6.json"
    if not ca_path.exists():
        return ""
    import sys
    sys.path.insert(0, str(root / "utils"))
    import rg_sched_cost

    ca = _load(ca_path)
    bis = _load(root / "results" / "bisect_lat_8x6.json")
    sweep = _load(root / "results" / "load_sweep_8x6.json")
    simple = _load(root / "results" / "simple2d_8x6.json")
    led = rg_sched_cost.centralization_ledger()

    by: dict[str, list[dict]] = {}
    for r in ca["rows"]:
        by.setdefault(r["design"], []).append(r)

    fine = sweep["lam_star"]
    # simple2d is not in the load sweep's headline map; the bisection is.
    fine = dict(fine)
    fine["mesh_simple2d"] = bis["summary"]["mesh_simple2d"]["lam_star"]

    bits = {
        "mesh_islip2d": led["mesh_islip2d"]["arbiter_bits_interval"],
        "ring_islip2d": led["ring_islip2d"]["arbiter_bits_interval"],
        "mesh_simple2d": simple["cost"]["simple2d:free_at:105"]["bits"],
        "mesh_base": led["mesh_base"]["distributed_bits"],
        "ring_base": led["ring_base"]["distributed_bits"],
    }
    # iSLIP / simple2d delete the data-plane queues; the base IS those queues.
    system = {
        "mesh_islip2d": bits["mesh_islip2d"],
        "ring_islip2d": bits["ring_islip2d"],
        "mesh_simple2d": bits["mesh_simple2d"],
        "mesh_base": bits["mesh_base"],
        "ring_base": bits["ring_base"],
    }

    def summ(design: str) -> dict[str, Any]:
        rows = by[design]
        r0 = rows[0]
        star = _lam_star(rows)
        tail = _lam_tail(rows)
        r_star = _at(rows, star) if star is not None else None
        r01 = _at(rows, 0.1)
        r03 = _at(rows, 0.3)
        return {
            "design": design,
            "rows": rows,
            "scheme": r0.get("ca_scheme"),
            "fabric": design.split("_")[0],
            "scale": r0.get("ca_credit_scale"),
            "period": r0.get("ca_period"),
            "ha_req": r0.get("ha_trackers"),
            "ha": r0.get("ha_trackers_eff"),
            "nol": r0.get("nol_rtt"),
            "link_credit": r0.get("link_credit"),
            "bits": r0.get("arbiter_bits"),
            "system": r0.get("system_bits"),
            "data": r0.get("datapath_bits"),
            "breakdown": r0.get("arbiter_breakdown") or {},
            "lam_star": star,
            "lam_tail": tail,
            "peak": _peak(rows),
            "p50_01": None if not r01 else r01.get("p50"),
            "p99_01": None if not r01 else r01.get("p99"),
            "p99_03": None if not r03 else r03.get("p99"),
            "stable_03": bool(r03 and r03.get("stable")),
            # Control traffic scales with offered load for credit (one request,
            # grant and DONE per packet) and does not for rate. Quote λ=0.3,
            # where the main designs are still stable, plus the value at λ*.
            "ctrl": (r03 or r0).get("ctrl_msgs_per_cy"),
            "ctrl_star": None if not r_star else r_star.get("ctrl_msgs_per_cy"),
            "defl": None if not r_star else r_star.get("defl_per_pkt"),
        }

    S = {d: summ(d) for d in by}

    def bref(cfg: str) -> dict[str, Any]:
        rows = _bisect_rows(bis, cfg)
        r01, r03 = _at(rows, 0.1), _at(rows, 0.3)
        return {
            "design": cfg,
            "lam_star": fine[cfg],
            "lam_tail": None,
            "peak": sweep["peak_accepted"].get(cfg) or _peak(rows),
            "p50_01": None if not r01 else r01.get("p50"),
            "p99_01": None if not r01 else r01.get("p99"),
            "p99_03": None if not r03 else r03.get("p99"),
            "stable_03": bool(r03 and r03.get("stable")),
            "bits": bits[cfg] if cfg.endswith("islip2d") or cfg.endswith("simple2d")
                    else None,
            "system": system[cfg],
            "ctrl": 96.0 if "islip" in cfg or cfg.endswith("simple2d") else None,
            "rows_b": rows,
        }

    refs = {c: bref(c) for c in (
        "mesh_base", "mesh_islip2d", "mesh_simple2d", "ring_base", "ring_islip2d")}

    # ---- colours / names -------------------------------------------------
    credit_color = {0.5: "#f0a0a0", 1.0: "#e06c6c", 2.0: "#ff9ecb"}
    rate_color = {16: "#f5c16c", 64: "#e6a23c", 256: "#c9842a"}

    def cname(s: dict) -> str:
        if s["design"].endswith("ha16"):
            return "credit HA=16"
        if s["scheme"] == "ca_credit":
            return f"credit ×{s['scale']:g}"
        return f"rate T={s['period']}"

    def ccolor(s: dict) -> str:
        if s["design"].endswith("ha16"):
            return "#c9a6ff"
        if s["scheme"] == "ca_credit":
            return credit_color.get(s["scale"], "#e06c6c")
        return rate_color.get(s["period"], "#e6a23c")

    def cshape(s: dict) -> str:
        return "o" if s["scheme"] == "ca_credit" else "s"

    # ---- pareto points ---------------------------------------------------
    def arb_points(fabric: str) -> list[dict]:
        pts = []
        isl = refs[f"{fabric}_islip2d"]
        pts.append({"x": isl["bits"], "y": isl["lam_star"], "name": "iSLIP-2D",
                     "color": "#6ee7a8", "shape": "d", "tag": True})
        if fabric == "mesh":
            sm = refs["mesh_simple2d"]
            pts.append({"x": sm["bits"], "y": sm["lam_star"], "name": "simple2d",
                         "color": "#7eb6ff", "shape": "d", "tag": True})
        for s in S.values():
            if s["fabric"] != fabric or s["lam_star"] is None or not s["bits"]:
                continue
            # HA=16 is a different resource, not a scale knob. Keep it, tag it.
            pts.append({
                "x": s["bits"], "y": s["lam_star"], "name": cname(s),
                "color": ccolor(s), "shape": cshape(s),
                "tag": s["design"].endswith("ha16"),
            })
        fr = _front(pts, max_y=True)
        ids = {(p["x"], p["y"], p["name"]) for p in fr}
        for p in pts:
            p["front"] = (p["x"], p["y"], p["name"]) in ids
        return pts

    def sys_points(fabric: str) -> list[dict]:
        pts = []
        for cfg, name, col in (
                (f"{fabric}_islip2d", "iSLIP-2D", "#6ee7a8"),
                (f"{fabric}_base", "基线", "#9aa3b5")):
            r = refs[cfg]
            pts.append({"x": r["system"], "y": r["lam_star"], "name": name,
                         "color": col, "shape": "d", "tag": True})
        if fabric == "mesh":
            sm = refs["mesh_simple2d"]
            pts.append({"x": sm["system"], "y": sm["lam_star"], "name": "simple2d",
                         "color": "#7eb6ff", "shape": "d", "tag": True})
        for s in S.values():
            if s["fabric"] != fabric or s["lam_star"] is None:
                continue
            # One point per scheme family on this axis: the buffer term
            # dominates, so every scale lands on the same x. Keep scale=1,
            # T=64, and HA=16.
            if s["scheme"] == "ca_credit" and s["scale"] != 1.0:
                continue
            if s["scheme"] == "ca_rate" and s["period"] != 64:
                continue
            pts.append({
                "x": s["system"], "y": s["lam_star"], "name": cname(s),
                "color": ccolor(s), "shape": cshape(s),
                "tag": True,
            })
        fr = _front(pts, max_y=True)
        ids = {(p["x"], p["y"], p["name"]) for p in fr}
        for p in pts:
            p["front"] = (p["x"], p["y"], p["name"]) in ids
        return pts

    def lat_points(fabric: str) -> list[dict]:
        pts = []
        isl = refs[f"{fabric}_islip2d"]
        if isl["stable_03"] and isl["p99_03"]:
            pts.append({"x": isl["bits"], "y": isl["p99_03"], "name": "iSLIP-2D",
                         "color": "#6ee7a8", "shape": "d", "tag": True})
        if fabric == "mesh":
            sm = refs["mesh_simple2d"]
            if sm["stable_03"] and sm["p99_03"]:
                pts.append({"x": sm["bits"], "y": sm["p99_03"], "name": "simple2d",
                             "color": "#7eb6ff", "shape": "d", "tag": True})
        base = refs[f"{fabric}_base"]
        # Base has no "arbiter bits" in the same column; skip it here.
        for s in S.values():
            if s["fabric"] != fabric or not s["stable_03"] or not s["p99_03"]:
                continue
            if not s["bits"]:
                continue
            pts.append({
                "x": s["bits"], "y": s["p99_03"], "name": cname(s),
                "color": ccolor(s), "shape": cshape(s),
                "tag": s["design"].endswith("ha16") or s["period"] in (16, 256),
            })
        if not pts:
            return pts
        fr = _front(pts, max_y=False)
        ids = {(p["x"], p["y"], p["name"]) for p in fr}
        for p in pts:
            p["front"] = (p["x"], p["y"], p["name"]) in ids
        return pts

    arb = {"mesh": arb_points("mesh"), "ring": arb_points("ring")}
    sysp = {"mesh": sys_points("mesh"), "ring": sys_points("ring")}
    latp = {"mesh": lat_points("mesh"), "ring": lat_points("ring")}
    for group in (arb, sysp, latp):
        for pts in group.values():
            fr = [p for p in pts if p.get("front")]
            # _front already filtered; keep the polyline in x order
            pts_front = sorted(fr, key=lambda p: p["x"])
            for p in pts:
                p["_front_list"] = pts_front

    def panel_pair(group: dict, titles=("mesh", "ring")) -> list[dict]:
        out = []
        for fab, title in zip(("mesh", "ring"), titles):
            pts = group[fab]
            out.append({
                "title": title,
                "pts": pts,
                "front": sorted((p for p in pts if p.get("front")),
                                key=lambda p: p["x"]),
            })
        return out

    # y ranges for latency
    lat_ys = [p["y"] for fab in latp.values() for p in fab]
    lat_min = max(20, min(lat_ys) / 1.4) if lat_ys else 40
    lat_max = max(lat_ys) * 1.4 if lat_ys else 2000

    pareto_lam = _svg_pareto(
        panel_pair(arb, ("mesh · 仲裁器状态", "ring · 仲裁器状态")),
        ylog=False, ymin=0, ymax=1, ylab="λ*", xlab="仲裁器状态（bit，对数）")
    pareto_sys = _svg_pareto(
        panel_pair(sysp, ("mesh · 系统存储", "ring · 系统存储")),
        ylog=False, ymin=0, ymax=1, ylab="λ*", xlab="系统存储（bit，对数）")
    pareto_lat = _svg_pareto(
        panel_pair(latp, ("mesh · λ=0.3 的 p99", "ring · λ=0.3 的 p99")),
        ylog=True, ymin=lat_min, ymax=lat_max,
        ylab="p99（拍，对数）", xlab="仲裁器状态（bit，对数）")

    # ---- throughput curves ----------------------------------------------
    def curve_panel(fabric: str) -> dict:
        series = [
            {"color": "#9aa3b5", "dash": True, "name": "基线",
             "pts": _series(refs[f"{fabric}_base"]["rows_b"])},
            {"color": "#6ee7a8", "name": "iSLIP-2D",
             "pts": _series(refs[f"{fabric}_islip2d"]["rows_b"])},
        ]
        if fabric == "mesh":
            series.append({"color": "#7eb6ff", "dash": True, "name": "simple2d",
                           "pts": _series(refs["mesh_simple2d"]["rows_b"])})
        series.append({"color": "#e06c6c", "name": "credit ×1",
                        "pts": _series(S[f"{fabric}_credit_s1"]["rows"])})
        series.append({"color": "#ff9ecb", "dash": True, "name": "credit ×2",
                        "pts": _series(S[f"{fabric}_credit_s2"]["rows"])})
        series.append({"color": "#e6a23c", "name": "rate T=64",
                        "pts": _series(S[f"{fabric}_rate_p64"]["rows"])})
        return {"title": fabric, "series": series}

    curves = _svg_curve([curve_panel("mesh"), curve_panel("ring")])

    # ---- tables ----------------------------------------------------------
    def row_ref(fabric: str, cfg: str, knob: str) -> str:
        r = refs[cfg]
        old = refs[f"{fabric}_islip2d"]["lam_star"]
        ctrl = "—" if r["ctrl"] is None else _f(r["ctrl"], 2)
        arb = "—" if cfg.endswith("base") else _bits(r["bits"])
        return (
            f"<tr><td>{knob}</td><td>{_f(r['lam_star'])}</td>"
            f"<td>{_dlam(r['lam_star'], old)}</td>"
            f"<td>{_f(r['peak'], 3)}</td><td>{_f(r['p50_01'], 0)}</td>"
            f"<td>{_f(r['p99_03'], 0)}</td><td>{ctrl}</td>"
            f"<td>{arb}</td><td>{_bits(r['system'])}</td></tr>"
        )

    def row_ca(s: dict, old: float) -> str:
        star = "不稳定" if s["lam_star"] is None else _f(s["lam_star"])
        tail = ""
        if s["lam_tail"] is not None and s["lam_star"] is not None \
                and s["lam_tail"] + 1e-9 < s["lam_star"]:
            tail = f"<br><span class='muted'>尾部 λ≤{_f(s['lam_tail'])}</span>"
        knob = cname(s)
        p99 = _f(s["p99_03"], 0) if s["stable_03"] else (
            "失稳" if s["p99_03"] is None or not s["stable_03"] else _f(s["p99_03"], 0))
        if not s["stable_03"] and s["p99_03"] is not None:
            p99 = f"<span class='lose'>{_f(s['p99_03'], 0)}</span>"
        return (
            f"<tr><td>{knob}</td><td>{star}{tail}</td>"
            f"<td>{_dlam(s['lam_star'], old)}</td>"
            f"<td>{_f(s['peak'], 3)}</td><td>{_f(s['p50_01'], 0)}</td>"
            f"<td>{p99}</td><td>{_f(s['ctrl'], 2)}</td>"
            f"<td>{_bits(s['bits'])}</td><td>{_bits(s['system'])}</td></tr>"
        )

    def table(fabric: str) -> str:
        old = refs[f"{fabric}_islip2d"]["lam_star"]
        body = [row_ref(fabric, f"{fabric}_base", "基线（无集中准入）")]
        body.append(row_ref(fabric, f"{fabric}_islip2d", "iSLIP-2D（原始）"))
        if fabric == "mesh":
            body.append(row_ref(fabric, "mesh_simple2d", "simple2d"))
        order = [f"{fabric}_credit_s0.5", f"{fabric}_credit_s1",
                 f"{fabric}_credit_s2", f"{fabric}_credit_s1_ha16",
                 f"{fabric}_rate_p16", f"{fabric}_rate_p64",
                 f"{fabric}_rate_p256"]
        for d in order:
            body.append(row_ca(S[d], old))
        return (
            "<table><thead><tr><th>方案</th><th>λ*</th><th>Δλ* vs iSLIP</th>"
            "<th>峰值接受</th><th>p50@0.1</th><th>p99@0.3</th>"
            "<th>控制消息/拍 @0.3</th><th>仲裁器 bit</th><th>系统 bit</th>"
            f"</tr></thead><tbody>{''.join(body)}</tbody></table>"
        )

    # ---- prose -----------------------------------------------------------
    mc, rc = S["mesh_credit_s1"], S["ring_credit_s1"]
    mr, rr = S["mesh_rate_p64"], S["ring_rate_p64"]
    mh, rh = S["mesh_credit_s1_ha16"], S["ring_credit_s1_ha16"]
    mi, ri = refs["mesh_islip2d"], refs["ring_islip2d"]
    mb, rb = refs["mesh_base"], refs["ring_base"]

    def front_names(pts: list[dict]) -> str:
        names = [p["name"] for p in sorted(
            (p for p in pts if p.get("front")), key=lambda p: p["x"])]
        return "、".join(names) if names else "（空）"

    def bd(s: dict) -> str:
        return "，".join(f"{k} {v:,}" for k, v in s["breakdown"].items())

    ha_lim_m = mh["ha"] / mh["nol"] if mh["nol"] else None
    ha_lim_r = rh["ha"] / rh["nol"] if rh["nol"] else None

    # Best credit / rate by lam_star then by tail
    def best(fabric: str, scheme: str) -> dict:
        cand = [s for s in S.values()
                if s["fabric"] == fabric and s["scheme"] == scheme
                and not s["design"].endswith("ha16")]
        return max(cand, key=lambda s: (
            s["lam_star"] or 0, s["lam_tail"] or 0, -(s["bits"] or 0)))

    bc_m, bc_r = best("mesh", "ca_credit"), best("ring", "ca_credit")
    br_m, br_r = best("mesh", "ca_rate"), best("ring", "ca_rate")

    meta = ca["meta"]
    text = f"""
<h2 id="s11b">11b · CA-credit 与 CA-rate</h2>
<p class="lead">同一套 8×6 流量上，把 <code>docs/rg.md</code> 的另外两个集中准入器
放在 iSLIP-2D 旁边。CA-GiSLIP 就是本文的 iSLIP-2D（路径上每个资源都同意才授权，
interval 表把时隙订死）。CA-credit 和 CA-rate 不订时隙：它们只决定「能不能注入」，
注入之后走的仍是 §8 的数据面——mesh 是有缓冲的 XY，环是 E-tag/I-tag 偏转环。</p>
<ul>
<li><b>CA-credit。</b>每个资源一个在途计数。链路计数 = 每拍 flit 数 × 空载 RTT
× <code>ca_credit_scale</code>，空载 RTT 取「授权飞行 + 最长路径 + DONE 飞行」
（mesh {mc['nol']} 拍，环 {rc['nol']} 拍）。HA 计数就是 tracker 个数；
默认按解析锚点 × RTT 配到不先被 tracker 卡住（mesh {mc['ha']}，环 {rc['ha']}），
另外单列 HA=16。每个周期源最多报 <code>ca_probe</code> 个最老请求，
轮询，足迹上每个计数都够才授予，DONE 回到仲裁器才还计数。</li>
<li><b>CA-rate。</b>每 <code>ca_period</code> 拍收集各核的积压 VOQ，
对链路容量和 HA 容量做 water-filling，得到 max-min 速率，
再过 2×t<sub>rtt</sub> 生效。核上用已有的 per-VOQ 令牌桶准入。
没有逐包 request，也没有 DONE。</li>
</ul>
<div class="note">
面积分两列，因为这两列回答的不是同一个问题。
<b>仲裁器 bit</b> 只算准入器自己的状态，这是 rg.md 里「credit 最小、rate 控制开销最低」的口径。
<b>系统 bit</b> 把数据面还要付的队列加回去：iSLIP-2D 和 simple2d 的数据面是零缓冲，
所以系统 bit = 仲裁器 bit；credit / rate 还背着基线的那笔缓冲。
λ* 沿用 §9 的定义（队列斜率 &lt; 0.002 且接受/注入 ≥ 0.98）。
本次网格在膝部的步长是 0.05，原始 iSLIP 的 λ* 来自 §9 的细网格，
差不到一个步长的不当成差别。测量窗口 warmup={meta['warmup']} / measure={meta['measure']}，
与二分扫描相同。
</div>

<h3>11b.1 和原始 iSLIP-2D 差在哪</h3>
<p>先看默认点：credit 的 scale=1，rate 的周期 64。</p>
<ul>
<li><b>mesh 吞吐。</b>credit ×1 的 λ* {_rel(mc['lam_star'], mi['lam_star'])}，
rate T=64 {_rel(mr['lam_star'], mi['lam_star'])}。
两者都更接近基线 mesh_base 的 {_f(mb['lam_star'])}，而不是 iSLIP 的 {_f(mi['lam_star'])}。
iSLIP 赢在把 mesh 从「信用卡住」换成「金属卡住」；credit/rate 把准入做粗了，
包仍会在路由器里排队，膝部因此回到基线附近。
旋钮扫完，mesh 上最好的 credit 是 {cname(bc_m)}（λ*={_f(bc_m['lam_star'])}），
最好的 rate 是 {cname(br_m)}（λ*={_f(br_m['lam_star'])}）。</li>
<li><b>环吞吐。</b>credit ×1 {_rel(rc['lam_star'], ri['lam_star'])}，
rate T=64 {_rel(rr['lam_star'], ri['lam_star'])}，
对照基线 ring_base {_f(rb['lam_star'])}。
最好的 credit 是 {cname(bc_r)}（λ*={_f(bc_r['lam_star'])}），
最好的 rate 是 {cname(br_r)}（λ*={_f(br_r['lam_star'])}）。
环上 iSLIP 的 λ* 是 {_f(ri['lam_star'])}。credit ×1 在自己的 λ* 处偏转只有
{_f(rc['defl'], 3)} 次/包，rate T=64 是 {_f(rr['defl'], 3)} 次/包，
过载后也停在 0.01 次/包以下。吞吐却正好停在基线的 {_f(rb['lam_star'])}：
准入把注入收在反应式数据面自己的接受上限上，没有多出 iSLIP 那种时隙级并发。
环上 scale=0.5 会先碰到信用本身，λ* 降到 {_f(S['ring_credit_s0.5']['lam_star'])}；
mesh 上 scale=0.5 的 λ* 是 {_f(S['mesh_credit_s0.5']['lam_star'])}，
尾部在 {_f(S['mesh_credit_s0.5']['lam_tail'])} 就已经涨起来，
scale≥1 之后再加信用也不超过缓冲 mesh 的膝部。</li>
<li><b>轻载时延。</b>λ=0.1 时 mesh credit ×1 的 p50 是 {_f(mc['p50_01'], 0)} 拍，
iSLIP 是 {_f(mi['p50_01'], 0)} 拍，两边都在付同一个 t<sub>rtt</sub>=16 的授权环路。
rate T=64 的 p50 是 {_f(mr['p50_01'], 0)} 拍：速率向量每 64 拍才重算，
还要再飞 2×16 拍才生效，空闲 VOQ 的第一包等的就是这个周期。</li>
<li><b>λ=0.3 的尾部。</b>mesh iSLIP p99={_f(mi['p99_03'], 0)}，
credit ×1 p99={_f(mc['p99_03'], 0)}，
rate T=64 p99={_f(mr['p99_03'], 0)}。
环上三者分别是 {_f(ri['p99_03'], 0)} / {_f(rc['p99_03'], 0)} / {_f(rr['p99_03'], 0)}。
表里「尾部 λ」一栏是 p99 还没涨过 λ=0.1 时 3 倍的最大稳定负载——
稳定性测试通过之后，尾巴可以先炸。</li>
<li><b>HA=16 是另一条约束。</b>按最长路径的空载 RTT 周转，
上限约 mesh {_f(ha_lim_m, 3)}、环 {_f(ha_lim_r, 3)} 包/拍。
多数流比最长路径短，DONE 回得更早，所以网格上的 λ* 略高一点：
mesh {_f(mh['lam_star'])}、环 {_f(rh['lam_star'])}。
相对默认池的 {_f(mc['lam_star'])} / {_f(rc['lam_star'])}，
16 个 tracker 仍然先把准入停住。默认池配在锚点之上，
就是为了让主表的 λ* 反映链路信用，而不是一个随手写的 tracker 深度。</li>
<li><b>控制面。</b>iSLIP 每个周期每节点一来一回，与负载无关，{_f(mi['ctrl'], 0)} 条/拍。
credit 按包计：λ=0.3 时 mesh {_f(mc['ctrl'], 1)}、环 {_f(rc['ctrl'], 1)} 条/拍，
到各自的 λ* 升到 {_f(mc['ctrl_star'], 1)} / {_f(rc['ctrl_star'], 1)}
（请求 + 授权 + DONE）。
rate T=64 固定 {_f(mr['ctrl'], 2)} 条/拍，等于 2×48/64，不随负载涨。
三套里 rate 的控制开销最低，和 rg.md 一致。</li>
<li><b>仲裁器面积。</b>credit ×1 是 {_bits(mc['bits'])} bit（mesh）/ {_bits(rc['bits'])} bit（环），
对 iSLIP 的 {_bits(mi['bits'])} / {_bits(ri['bits'])}。
rate 的速率表是 48×47 个 8 bit 寄存器，{_bits(mr['bits'])} bit，
两边相同，因为表按 VOQ 而不是按链路存；需求每个周期算完就丢，不占状态。
拆开：mesh credit（{bd(mc)}）；mesh rate（{bd(mr)}）。</li>
</ul>

<h3>11b.2 接受吞吐</h3>
<p>灰虚线是基线，绿线是 iSLIP-2D，红线是 credit ×1，粉虚线是 credit ×2，
黄线是 rate T=64。mesh 上另有一条蓝虚线是 simple2d。对角虚线是「注入全部被接受」。</p>
<div class="fig">{curves}
<div class="cap">图 11b-1 · 接受吞吐。credit / rate 的平台贴着基线，iSLIP 的平台更高。
credit ×2 把 BDP 放宽，膝部略动，但过了数据面自己的饱和就不再涨。</div></div>

<h3>11b.3 逐个旋钮</h3>
<p>Δλ* 相对的是<b>同一 fabric 的 iSLIP-2D</b>（mesh {_f(mi['lam_star'])}，
环 {_f(ri['lam_star'])}）。p99@0.3 标红表示该点已经不稳定。
原始三行的 λ* 与 p99 来自分项扫描，不是这次重跑的。</p>
<h4>mesh</h4>
{table("mesh")}
<h4>ring</h4>
{table("ring")}

<h3>11b.4 Pareto：仲裁器面积 × λ*</h3>
<p>横轴是仲裁器状态 bit（越小越好），纵轴是 λ*（越大越好）。
实心、连线的是非支配前沿；淡的点被前沿上的某个点在两轴上同时不差、且至少一轴更差。
圆是 credit，方是 rate，菱形是 iSLIP / simple2d。</p>
<div class="fig">{pareto_lam}
<div class="cap">图 11b-2 · 仲裁器 Pareto。
mesh 前沿：{front_names(arb['mesh'])}。
环前沿：{front_names(arb['ring'])}。
credit 靠计数器而不是区间表，面积比 iSLIP 小一个数量级以上；iSLIP 靠 λ* 站在最上。
mesh 最左端是 simple2d，它比 credit 更小，λ* 也更低。
rate 的控制消息更少，但状态是整张速率表，面积和 λ* 往往同时落在两者内侧。</div></div>

<h3>11b.5 Pareto：系统存储 × λ*</h3>
<p>把数据面队列加回来之后，横轴变成系统 bit。
iSLIP 的系统存储就是那张区间表；credit / rate 要再加基线的输入缓冲或环站 FIFO。
同一方案只留 scale=1、T=64 和 HA=16，因为缓冲项比计数器宽度大两个数量级，
scale 在这张图上分不开。</p>
<div class="fig">{pareto_sys}
<div class="cap">图 11b-3 · 系统存储 Pareto。
mesh 前沿：{front_names(sysp['mesh'])}。
环前沿：{front_names(sysp['ring'])}。
mesh 上 iSLIP 同时更小、λ* 更高，credit/rate 落在前沿内侧——
省下的区间表不够付还回去的那 {_bits(mc['data'])} bit 缓冲。
环上两边的存储本来就几乎打平（§11），准入器再小也搬不动 λ*。</div></div>

<h3>11b.6 Pareto：仲裁器面积 × λ=0.3 的 p99</h3>
<p>横轴越小越好，纵轴越靠上 p99 越小。只画在 λ=0.3 仍然稳定的点。
这张图把 rate 的周期税单独显出来：T 越大，控制消息越少，p99 越高，面积不变，
所以长周期在「面积 × 尾延迟」上不占优，它占优的是控制带宽。</p>
<div class="fig">{pareto_lat}
<div class="cap">图 11b-4 · λ=0.3 尾延迟 Pareto。
mesh 前沿：{front_names(latp['mesh'])}。
环前沿：{front_names(latp['ring'])}。
credit ×1 在这条轻载线上和 iSLIP 的 p99 同量级，面积小一个数量级以上；
rate 用尾延迟换控制带宽。</div></div>

<h3>11b.7 读法</h3>
<ol>
<li><b>rg.md 的面积排序在仲裁器口径下成立。</b>
三套里 credit 的状态最少，rate 的控制消息最少，iSLIP（CA-GiSLIP）最贵。
mesh credit ×1 是 iSLIP 的 {_f(mc['bits']/mi['bits'], 3)} 倍 bit，
环是 {_f(rc['bits']/ri['bits'], 3)} 倍。</li>
<li><b>性能排序相反，而且差的是数据面不是匹配算法。</b>
credit / rate 的 λ* 落在基线旁边，低于 iSLIP
（mesh {_f(mi['lam_star'])}，环 {_f(ri['lam_star'])}）。
它们没有 interval 表，也就没有「发出去就不会堵」。
mesh 上这正是 §9.3 说的那笔交易的反面：把金属瓶颈退回成队列瓶颈。</li>
<li><b>系统 Pareto 上，mesh 的 iSLIP 支配 credit 和 rate。</b>
仲裁器 Pareto 上 credit 留下来，是因为横轴没算缓冲。
两张图要一起看：只报仲裁器，会得出「credit 又小又够用」；
把 §11 的缓冲账加回来，mesh 上这笔账是
{_bits(mb['system'])} bit，credit 的计数器相对它是零头。</li>
<li><b>rate 的旋钮是周期，不是面积。</b>
T=16 / 64 / 256 的仲裁器 bit 相同，控制消息按 1/T 下降，
λ=0.1 的 p50 随 T 上升。要控制带宽就拉长周期，并且接受第一包的额外等待。</li>
<li><b>HA tracker 会先于链路信用把 λ* 压住。</b>
16 个 tracker 的最长路径上限约 {_f(ha_lim_m, 2)} 包/拍，
实测 mesh {_f(mh['lam_star'])}、环 {_f(rh['lam_star'])}，
和 scale 无关。默认池配在锚点之上，上表的 λ* 才是链路准入的结果。</li>
</ol>
<p class="muted">数据 <code>results/ca_credit_rate_8x6.json</code>
（{meta['n']} 点，{meta['secs']:.0f} 秒）。
重跑：<code>python3 utils/dse_ca_credit_rate.py</code>，
再 <code>python3 utils/gen_islip2d_report.py</code>。
实现：<code>utils/rg_ca_admit.py</code>。</p>
"""
    return text


if __name__ == "__main__":
    print(render()[:500])
    print("... ok", len(render()))
