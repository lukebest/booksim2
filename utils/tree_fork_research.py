#!/usr/bin/env python3
"""Tree-shaped allgather: multicast fork position vs makespan (0-buffer + AFIFO).

Model (ignore down-ramp multicast bandwidth here — only link TDM + eject cap):
  * Rigid 0-buffer: sched_zerobuf_compare.pack — no router buffer, no blocking.
  * Border short-arc + AFIFO≤5: sched_ring_zerobuf (shape-opt quads).

Tree families (fork topology):
  ring_bi_2fork   — global bi Hamilton ring: 1st-level 2-way fork at source, no later fork
  dim_xy / dim_yx — dimensional multi-tree (X-then-Y / Y-then-X spine multicast)
  dim_xy_late_y   — X spine only at source row, Y forks delayed to row band B
  row_spine       — multicast along source row only, then column forks at row ends
  col_spine       — multicast along source column, then row forks at column ends
  border_3level   — L1 local-quad tree, L2 adjacent-quad border fork, L3 diagonal fork
  quad_4ring      — local quad ring circulation + central 4-cycle exchange (tree view)
  hybrid_h_B*     — horizontal band ring + per-column vertical tree fork
  hybrid_v_B*     — vertical band ring + per-row horizontal tree fork

Output: results/tree_fork_research.json
"""

import argparse
import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import sched_ring_zerobuf as S
import sched_zerobuf_compare as Z
from optimize_quad_shapes import load_optimal
from sweep_quad_ring_shapes import cfg_str, make_quads

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "tree_fork_research.json"
SIZES = (16,)
AFIFO_CAP = 5


# ---------------------------------------------------------------------------
# Parameterized multicast trees -> edge list (parent, child)
# ---------------------------------------------------------------------------
def _nid(x, y, mx):
    return x + mx * y


def _coord(n, mx):
    return n % mx, n // mx


def _manh(s, d, mx, h, v):
    sx, sy = _coord(s, mx)
    dx, dy = _coord(d, mx)
    return abs(sx - dx) * h + abs(sy - dy) * v


def tree_dim(s, mx, my, order="xy"):
    """Full dimensional multi-tree: spine along dim1, fork along dim2 at every spine node."""
    sx, sy = _coord(s, mx)
    edges = []
    if order in ("xy", "yx"):
        horiz_first = order == "xy"
    else:
        horiz_first = True
    if horiz_first:
        for x in range(sx + 1, mx):
            edges.append((_nid(x - 1, sy, mx), _nid(x, sy, mx)))
        for x in range(sx - 1, -1, -1):
            edges.append((_nid(x + 1, sy, mx), _nid(x, sy, mx)))
        for x in range(mx):
            for y in range(sy + 1, my):
                edges.append((_nid(x, y - 1, mx), _nid(x, y, mx)))
            for y in range(sy - 1, -1, -1):
                edges.append((_nid(x, y + 1, mx), _nid(x, y, mx)))
    else:
        for y in range(sy + 1, my):
            edges.append((_nid(sx, y - 1, mx), _nid(sx, y, mx)))
        for y in range(sy - 1, -1, -1):
            edges.append((_nid(sx, y + 1, mx), _nid(sx, y, mx)))
        for y in range(my):
            for x in range(sx + 1, mx):
                edges.append((_nid(x - 1, y, mx), _nid(x, y, mx)))
            for x in range(sx - 1, -1, -1):
                edges.append((_nid(x + 1, y, mx), _nid(x, y, mx)))
    return edges


def tree_dim_late_y(s, mx, my, fork_row_band):
    """X spine on all rows, but Y forks only on rows in band [y0, y0+fork_row_band)."""
    sx, sy = _coord(s, mx)
    edges = []
    for x in range(sx + 1, mx):
        edges.append((_nid(x - 1, sy, mx), _nid(x, sy, mx)))
    for x in range(sx - 1, -1, -1):
        edges.append((_nid(x + 1, sy, mx), _nid(x, sy, mx)))
    y0 = max(0, sy - fork_row_band // 2)
    y1 = min(my, y0 + fork_row_band)
    for x in range(mx):
        for y in range(y0, y1):
            if y > sy:
                edges.append((_nid(x, y - 1, mx), _nid(x, y, mx)))
            elif y < sy:
                edges.append((_nid(x, y + 1, mx), _nid(x, y, mx)))
    return edges


def tree_dim_late_x(s, mx, my, fork_col_band):
    """Y spine on source column; X forks only within column band around source."""
    sx, sy = _coord(s, mx)
    edges = []
    for y in range(sy + 1, my):
        edges.append((_nid(sx, y - 1, mx), _nid(sx, y, mx)))
    for y in range(sy - 1, -1, -1):
        edges.append((_nid(sx, y + 1, mx), _nid(sx, y, mx)))
    x0 = max(0, sx - fork_col_band // 2)
    x1 = min(mx, x0 + fork_col_band)
    for y in range(my):
        for x in range(x0, x1):
            if x > sx:
                edges.append((_nid(x - 1, y, mx), _nid(x, y, mx)))
            elif x < sx:
                edges.append((_nid(x + 1, y, mx), _nid(x, y, mx)))
    return edges


def tree_serpentine(s, mx, my):
    """Row spine at source, then per-column serpentine vertical fill."""
    sx, sy = _coord(s, mx)
    edges = []
    for x in range(sx + 1, mx):
        edges.append((_nid(x - 1, sy, mx), _nid(x, sy, mx)))
    for x in range(sx - 1, -1, -1):
        edges.append((_nid(x + 1, sy, mx), _nid(x, sy, mx)))
    for x in range(mx):
        up = list(range(sy + 1, my))
        down = list(range(sy - 1, -1, -1))
        order_y = (up + down) if x % 2 == 0 else (down + up)
        prev = sy
        for y in order_y:
            edges.append((_nid(x, prev, mx), _nid(x, y, mx)))
            prev = y
    return edges


def tree_center_first(s, mx, my, order="xy"):
    """Route to mesh center first, then dimensional multicast from center."""
    sx, sy = _coord(s, mx)
    cx, cy = (mx - 1) // 2, (my - 1) // 2
    edges = []
    x, y = sx, sy
    while x < cx:
        edges.append((_nid(x, y, mx), _nid(x + 1, y, mx)))
        x += 1
    while x > cx:
        edges.append((_nid(x, y, mx), _nid(x - 1, y, mx)))
        x -= 1
    while y < cy:
        edges.append((_nid(x, y, mx), _nid(x, y + 1, mx)))
        y += 1
    while y > cy:
        edges.append((_nid(x, y, mx), _nid(x, y - 1, mx)))
        y -= 1
    center = _nid(cx, cy, mx)
    edges += tree_dim(center, mx, my, order)
    return edges


def tree_quad_late_y(s, mx, my, band):
    """L1: dim_xy_late_y within home quadrant; L2/L3: border forks."""
    hw, hh = mx // 2, my // 2
    sx, sy = _coord(s, mx)
    qx0 = 0 if sx < hw else hw
    qy0 = 0 if sy < hh else hh
    edges = []
    y0 = max(qy0, sy - band // 2)
    y1 = min(qy0 + hh, y0 + band)
    for x in range(max(qx0, sx) + 1, qx0 + hw):
        edges.append((_nid(x - 1, sy, mx), _nid(x, sy, mx)))
    for x in range(min(qx0 + hw - 1, sx) - 1, qx0 - 1, -1):
        edges.append((_nid(x + 1, sy, mx), _nid(x, sy, mx)))
    for x in range(qx0, qx0 + hw):
        for y in range(y0, y1):
            if y > sy:
                edges.append((_nid(x, y - 1, mx), _nid(x, y, mx)))
            elif y < sy:
                edges.append((_nid(x, y + 1, mx), _nid(x, y, mx)))
    if qx0 == 0:
        bx, arc_xs = hw - 1, list(range(hw, mx))
    else:
        bx, arc_xs = hw, list(range(hw - 1, -1, -1))
    for y in range(qy0, qy0 + hh):
        edges.append((_nid(bx, y, mx), _nid(arc_xs[0], y, mx)))
        for k in range(len(arc_xs) - 1):
            edges.append((_nid(arc_xs[k], y, mx), _nid(arc_xs[k + 1], y, mx)))
    if qy0 == 0:
        by, arc_ys = hh - 1, list(range(hh, my))
    else:
        by, arc_ys = hh, list(range(hh - 1, -1, -1))
    for x in range(qx0, qx0 + hw):
        edges.append((_nid(x, by, mx), _nid(x, arc_ys[0], mx)))
        for k in range(len(arc_ys) - 1):
            edges.append((_nid(x, arc_ys[k], mx), _nid(x, arc_ys[k + 1], mx)))
    for x in arc_xs:
        edges.append((_nid(x, by, mx), _nid(x, arc_ys[0], mx)))
        for k in range(len(arc_ys) - 1):
            edges.append((_nid(x, arc_ys[k], mx), _nid(x, arc_ys[k + 1], mx)))
    return edges


def tree_row_spine(s, mx, my):
    """Fork along source row to both ends, then column multicast from endpoints."""
    sx, sy = _coord(s, mx)
    edges = []
    for x in range(sx + 1, mx):
        edges.append((_nid(x - 1, sy, mx), _nid(x, sy, mx)))
    for x in range(sx - 1, -1, -1):
        edges.append((_nid(x + 1, sy, mx), _nid(x, sy, mx)))
    for x in (0, mx - 1):
        for y in range(sy + 1, my):
            edges.append((_nid(x, y - 1, mx), _nid(x, y, mx)))
        for y in range(sy - 1, -1, -1):
            edges.append((_nid(x, y + 1, mx), _nid(x, y, mx)))
    return edges


def tree_col_spine(s, mx, my):
    """Fork along source column, then row multicast from endpoints."""
    sx, sy = _coord(s, mx)
    edges = []
    for y in range(sy + 1, my):
        edges.append((_nid(sx, y - 1, mx), _nid(sx, y, mx)))
    for y in range(sy - 1, -1, -1):
        edges.append((_nid(sx, y + 1, mx), _nid(sx, y, mx)))
    for y in (0, my - 1):
        for x in range(sx + 1, mx):
            edges.append((_nid(x - 1, y, mx), _nid(x, y, mx)))
        for x in range(sx - 1, -1, -1):
            edges.append((_nid(x + 1, y, mx), _nid(x, y, mx)))
    return edges


def tree_border_3level(s, mx, my):
    """Explicit 3-level multicast: L1 intra-quad tree, L2 border to adjacent quad, L3 diagonal."""
    hw, hh = mx // 2, my // 2
    sx, sy = _coord(s, mx)
    qx0 = 0 if sx < hw else hw
    qy0 = 0 if sy < hh else hh
    edges = []

    # L1: X-then-Y tree within home quadrant
    for x in range(max(qx0, sx) + 1, qx0 + hw):
        edges.append((_nid(x - 1, sy, mx), _nid(x, sy, mx)))
    for x in range(min(qx0 + hw - 1, sx) - 1, qx0 - 1, -1):
        edges.append((_nid(x + 1, sy, mx), _nid(x, sy, mx)))
    for x in range(qx0, qx0 + hw):
        for y in range(max(qy0, sy) + 1, qy0 + hh):
            edges.append((_nid(x, y - 1, mx), _nid(x, y, mx)))
        for y in range(min(qy0 + hh - 1, sy) - 1, qy0 - 1, -1):
            edges.append((_nid(x, y + 1, mx), _nid(x, y, mx)))

    # L2: from horizontal border of home quad -> adjacent horizontal quad (same y)
    if qx0 == 0:
        bx, arc_xs = hw - 1, list(range(hw, mx))
    else:
        bx, arc_xs = hw, list(range(hw - 1, -1, -1))
    for y in range(qy0, qy0 + hh):
        edges.append((_nid(bx, y, mx), _nid(arc_xs[0], y, mx)))
        for k in range(len(arc_xs) - 1):
            edges.append((_nid(arc_xs[k], y, mx), _nid(arc_xs[k + 1], y, mx)))

    # L2 vertical: border column -> adjacent vertical quad
    if qy0 == 0:
        by, arc_ys = hh - 1, list(range(hh, my))
    else:
        by, arc_ys = hh, list(range(hh - 1, -1, -1))
    for x in range(qx0, qx0 + hw):
        edges.append((_nid(x, by, mx), _nid(x, arc_ys[0], mx)))
        for k in range(len(arc_ys) - 1):
            edges.append((_nid(x, arc_ys[k], mx), _nid(x, arc_ys[k + 1], mx)))

    # L3: diagonal via horizontal-border arrival points
    for x in arc_xs:
        edges.append((_nid(x, by, mx), _nid(x, arc_ys[0], mx)))
        for k in range(len(arc_ys) - 1):
            edges.append((_nid(x, arc_ys[k], mx), _nid(x, arc_ys[k + 1], mx)))
    return edges


def fp_from_edges(s, edges, mx, h, v, ramp_bw):
    """Rigid 0-buffer footprint from directed tree edges (parent->child)."""
    slots = [("U", s, 0)]
    for p, c in edges:
        slots.append(("L", Z.lk(p, c), ramp_bw + _manh(s, p, mx, h, v)))
    n = mx * mx
    for d in range(n):
        if d != s:
            slots.append(("D", d, ramp_bw + _manh(s, d, mx, h, v)))
    return slots


def fork_metadata(strategy):
    """Human-readable fork-level description."""
    meta = {
        "ring_bi_2fork": {"levels": 1, "desc": "源点双向2分叉，环上无再分叉"},
        "ring_uni": {"levels": 0, "desc": "单向环链，无网内分叉"},
        "dim_xy": {"levels": 2, "desc": "X脊多播 + 各列Y分叉（维序多树）"},
        "dim_yx": {"levels": 2, "desc": "Y脊多播 + 各行X分叉"},
        "dim_xy_late_y": {"levels": 2, "desc": "X脊全局，Y分叉延迟到指定行带"},
        "dim_xy_late_x": {"levels": 2, "desc": "Y脊全局，X分叉延迟到指定列带"},
        "serpentine": {"levels": 2, "desc": "行脊 + 各列蛇形垂直填充"},
        "center_first_xy": {"levels": 2, "desc": "先到网格中心，再X-then-Y多播"},
        "center_first_yx": {"levels": 2, "desc": "先到网格中心，再Y-then-X多播"},
        "quad_late_y": {"levels": 3, "desc": "象限内延迟Y分叉 + 边界L2/L3"},
        "row_spine": {"levels": 2, "desc": "源行左右分叉，行端列多播"},
        "col_spine": {"levels": 2, "desc": "源列上下分叉，列端行多播"},
        "border_3level": {"levels": 3, "desc": "L1象限内树 L2邻象限边界 L3对角"},
        "quad_4ring": {"levels": 2, "desc": "象限环 circulate + 中心4环交换"},
        "border_short_arc": {"levels": 3, "desc": "Hamilton环+短弧（环上无分叉，边界AFIFO）"},
    }
    return meta.get(strategy, {"levels": "?", "desc": strategy})


def shape_cfg(sz, tag):
    data = load_optimal()
    block = data["sizes"].get(f"{sz}x{sz}", {}).get("border", {}).get(tag, {})
    rec = block.get("best_balanced") or block.get("chosen") or block.get("best_any")
    if not rec:
        return (("rect", 0),) * 4
    return tuple(tuple(x) for x in rec["cfg"])


def eval_zerobuf_tree(sz, strategy, ramp_bw, extra=None):
    Z.cfg(sz, sz, 4, 6)
    Z.init_ring()
    Z.init_quadrants()
    mx, my, h, v = sz, sz, 4, 6
    extra = extra or {}

    def build(s):
        if strategy == "ring_bi_2fork":
            return Z.fp_ring(s, Z.RING_ORDER, Z.RING_POS, True, ramp_bw)
        if strategy == "ring_uni":
            return Z.fp_ring(s, Z.RING_ORDER, Z.RING_POS, False, ramp_bw)
        if strategy == "dim_xy":
            return fp_from_edges(s, tree_dim(s, mx, my, "xy"), mx, h, v, ramp_bw)
        if strategy == "dim_yx":
            return fp_from_edges(s, tree_dim(s, mx, my, "yx"), mx, h, v, ramp_bw)
        if strategy == "dim_xy_late_y":
            band = extra.get("fork_row_band", mx)
            return fp_from_edges(s, tree_dim_late_y(s, mx, my, band), mx, h, v, ramp_bw)
        if strategy == "dim_xy_late_x":
            band = extra.get("fork_col_band", mx)
            return fp_from_edges(s, tree_dim_late_x(s, mx, my, band), mx, h, v, ramp_bw)
        if strategy == "serpentine":
            return fp_from_edges(s, tree_serpentine(s, mx, my), mx, h, v, ramp_bw)
        if strategy == "center_first_xy":
            return fp_from_edges(s, tree_center_first(s, mx, my, "xy"), mx, h, v, ramp_bw)
        if strategy == "center_first_yx":
            return fp_from_edges(s, tree_center_first(s, mx, my, "yx"), mx, h, v, ramp_bw)
        if strategy == "quad_late_y":
            band = extra.get("fork_row_band", hh := mx // 2)
            return fp_from_edges(s, tree_quad_late_y(s, mx, my, band), mx, h, v, ramp_bw)
        if strategy == "row_spine":
            return fp_from_edges(s, tree_row_spine(s, mx, my), mx, h, v, ramp_bw)
        if strategy == "col_spine":
            return fp_from_edges(s, tree_col_spine(s, mx, my), mx, h, v, ramp_bw)
        if strategy == "border_3level":
            return fp_from_edges(s, tree_border_3level(s, mx, my), mx, h, v, ramp_bw)
        if strategy == "quad_4ring":
            return Z.fp_quadrant(s, ramp_bw >= 2, ramp_bw)
        if strategy == "border_rigid":
            return Z.fp_border(s, ramp_bw >= 2, ramp_bw)
        if strategy == "hybrid_h":
            B = extra["B"]
            return Z.fp_hybrid(s, B, ramp_bw >= 2, ramp_bw)
        if strategy == "hybrid_v":
            B = extra["B"]
            return Z.fp_hybrid_v(s, B, ramp_bw >= 2, ramp_bw)
        raise ValueError(strategy)

    mk, mo, order, ok = Z.run_scheme(build, ramp_bw)
    lb = (mx * my - 1 + ramp_bw - 1) // ramp_bw
    return dict(makespan=mk, max_offset=mo, src_order=order, ok=ok,
                eject_lb=lb, scheduler="zerobuf_rigid")


def eval_border_afifo(sz, bidir, ramp_bw, cap=AFIFO_CAP):
    tag = "bi" if bidir else "uni"
    cfg = shape_cfg(sz, tag)
    quads = make_quads(cfg, sz)
    deliv = lambda s, b, q=quads: S.deliv_border_quads(s, b, q)
    best = None
    for sp in range(30):
        for lb in (False, True):
            r = S.schedule(sz, bidir, ramp_bw, deliv, spread=sp, lb_cross=lb, quads=quads)
            if not r.get("ok") or r["afifo_depth"] > cap:
                continue
            if r["afifo_balanced"]["peak"] > cap:
                continue
            rec = dict(makespan=r["makespan"], afifo_depth=r["afifo_depth"],
                       afifo_balanced=r["afifo_balanced"]["peak"], spread=sp,
                       scheduler="ring_zerobuf")
            if best is None or rec["makespan"] < best["makespan"]:
                best = rec
    for order in ("interleave", "natural", "quad"):
        r = S.schedule_atomic(sz, bidir, ramp_bw, deliv, afifo_cap=cap,
                              order=order, quads=quads)
        if not r.get("ok"):
            continue
        if r["afifo_balanced"]["peak"] > cap:
            continue
        rec = dict(makespan=r["makespan"], afifo_depth=r["afifo_depth"],
                   afifo_balanced=r["afifo_balanced"]["peak"], order=order,
                   scheduler="atomic")
        if best is None or rec["makespan"] < best["makespan"]:
            best = rec
    n = sz * sz
    return best or dict(makespan=None, feasible=False)


def strategy_list(sz, deep=False):
    strats = [
        ("ring_uni", "ring_uni", {}),
        ("ring_bi_2fork", "ring_bi_2fork", {}),
        ("dim_xy", "dim_xy", {}),
        ("dim_yx", "dim_yx", {}),
        ("row_spine", "row_spine", {}),
        ("col_spine", "col_spine", {}),
        ("border_3level", "border_3level", {}),
        ("quad_4ring", "quad_4ring", {}),
        ("border_rigid", "border_rigid", {}),
    ]
    if deep:
        strats += [
            ("serpentine", "serpentine", {}),
            ("center_first_xy", "center_first_xy", {}),
            ("center_first_yx", "center_first_yx", {}),
        ]
    bands = sorted(set([2, 4, max(2, sz // 2), sz]))
    if deep:
        bands = sorted(set(bands + list(range(1, min(sz, 9)))))
    for band in bands:
        if band <= sz:
            strats.append((f"dim_xy_late_y_B{band}", "dim_xy_late_y", {"fork_row_band": band}))
            if deep:
                strats.append((f"dim_xy_late_x_B{band}", "dim_xy_late_x", {"fork_col_band": band}))
                if sz >= 8:
                    strats.append((f"quad_late_y_B{band}", "quad_late_y", {"fork_row_band": band}))
    Z.cfg(sz, sz, 4, 6)
    for B in Z.divisors_bands():
        if sz // B >= 2:
            strats.append((f"hybrid_h_B{B}", "hybrid_h", {"B": B}))
            strats.append((f"hybrid_v_B{B}", "hybrid_v", {"B": B}))
    return strats


def run(sizes=SIZES, deep=False):
    out = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "model": "tree fork position vs makespan; router 0-buffer rigid pack + border AFIFO≤5",
        "afifo_cap": AFIFO_CAP,
        "deep": deep,
        "sizes": {},
    }
    t0 = time.time()
    for sz in sizes:
        print(f"== {sz}x{sz} ==", flush=True)
        block = {"strategies": {}}
        for ramp_bw, tag in ((1, "uni"), (2, "bi")):
            for name, strat, extra in strategy_list(sz, deep=deep):
                key = f"{name}_{tag}"
                try:
                    rec = eval_zerobuf_tree(sz, strat, ramp_bw, extra)
                    rec["fork"] = fork_metadata(
                        "border_short_arc" if strat == "border_rigid" else strat)
                    rec["params"] = extra
                    block["strategies"][key] = rec
                    print(f"  {key:28s} mk={rec['makespan']:5d} ok={rec['ok']}", flush=True)
                except Exception as e:
                    block["strategies"][key] = dict(error=str(e))
                    print(f"  {key:28s} ERROR {e}", flush=True)

            # border short-arc with AFIFO≤5 (not pure rigid tree — uses ring TDM + AFIFO)
            bkey = f"border_short_arc_{tag}"
            brec = eval_border_afifo(sz, ramp_bw >= 2, ramp_bw)
            brec["fork"] = fork_metadata("border_short_arc")
            brec["ring_shape"] = cfg_str(shape_cfg(sz, tag))
            block["strategies"][bkey] = brec
            mk = brec.get("makespan", "—")
            print(f"  {bkey:28s} mk={mk} afifo≤{AFIFO_CAP}", flush=True)

        block["border_compare_bi"] = {
            "border_3level": block["strategies"].get("border_3level_bi", {}).get("makespan"),
            "border_short_arc": block["strategies"].get("border_short_arc_bi", {}).get("makespan"),
            "border_rigid": block["strategies"].get("border_rigid_bi", {}).get("makespan"),
            "note": "3level=显式多播树0-buffer; short_arc=Hamilton环+AFIFO≤5; rigid=fp_border刚性",
        }
        cands = [(v["makespan"], k) for k, v in block["strategies"].items()
                 if k.endswith("_bi") and v.get("makespan") and v.get("ok")
                 and not k.startswith("border_short")]
        block["best_zerobuf_bi"] = min(cands) if cands else None
        out["sizes"][f"{sz}x{sz}"] = block
    out["elapsed_s"] = time.time() - t0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {OUT} ({out['elapsed_s']:.0f}s)")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", type=int, nargs="+", default=list(SIZES))
    ap.add_argument("--deep", action="store_true")
    args = ap.parse_args()
    run(tuple(args.sizes), deep=args.deep)


if __name__ == "__main__":
    main()
