#!/usr/bin/env python3
"""Metrics helpers for AllGather slide deck: utilization, slot-table depth, AFIFO."""

from collections import defaultdict


def utilization_from_busy(busy, n, ramp_bw, makespan):
    """From sched_zerobuf_compare busy=(link_busy, up_busy, down_busy).

    Returns dict with eject_series, link_series, avg_eject_util, avg_link_util.
    """
    link_busy, up_busy, down_busy = busy
    mk = makespan + 1
    eject = [0] * mk
    for nd, cyc in down_busy.items():
        for c, cnt in cyc.items():
            if 0 <= c < mk:
                eject[c] += cnt
    cap_eject = n * ramp_bw
    eject_series = [v / cap_eject for v in eject]

    link_cnt = [0] * mk
    n_links = len(link_busy) or 1
    for lk, cyc in link_busy.items():
        for c, cnt in cyc.items():
            if 0 <= c < mk and cnt > 0:
                link_cnt[c] += 1
    link_series = [v / n_links for v in link_cnt]

    active_e = [v for v in eject_series if v > 0]
    active_l = [v for v in link_series if v > 0]
    return {
        "eject_series": eject_series,
        "link_series": link_series,
        "avg_eject_util": sum(active_e) / len(active_e) if active_e else 0.0,
        "avg_link_util": sum(active_l) / len(active_l) if active_l else 0.0,
    }


def utilization_from_events(events, n, ramp_bw, makespan, mx):
    """From sched_ring_zerobuf events (s,p,c,t,lat,arr,kind)."""
    mk = makespan + 1
    eject = [0] * mk
    link_busy = [0] * mk
    links_used = set()
    for ev in events:
        s, p, c, t, lat, arr, kind = ev
        if 0 <= t < mk:
            link_busy[t] += 1
            links_used.add((p, c))
    # eject: arrival + ramp (approximate from last hop)
    for ev in events:
        s, p, c, t, lat, arr, kind = ev
        ej = arr + 1  # RAMP=1
        if 0 <= ej < mk:
            eject[ej] += 1
    n_links = len(links_used) or 1
    cap_eject = n * ramp_bw
    eject_series = [v / cap_eject for v in eject]
    link_series = [v / n_links for v in link_busy]
    active_e = [v for v in eject_series if v > 0]
    active_l = [v for v in link_series if v > 0]
    return {
        "eject_series": eject_series,
        "link_series": link_series,
        "avg_eject_util": sum(active_e) / len(active_e) if active_e else 0.0,
        "avg_link_util": sum(active_l) / len(active_l) if active_l else 0.0,
    }


def dir_of(p, c, mx):
    px, py = p % mx, p // mx
    cx, cy = c % mx, c // mx
    if cx == px + 1:
        return "E"
    if cx == px - 1:
        return "W"
    if cy == py + 1:
        return "S"
    if cy == py - 1:
        return "N"
    return "L"


MESH_DIRS = frozenset("EWNS")


def topo_crossbar(cset):
    """Source-agnostic mesh crossbar: drop Local inject/eject ports."""
    return frozenset((i, o) for i, o in cset if i in MESH_DIRS and o in MESH_DIRS)


def min_period_nonempty_topo(seq):
    """Min period P over nonempty topo configs only (skip idle cycles).

    Returns P = min(temporal repeat period, |distinct configs|) so that a
    bi-directional ring with two transit configs reports P=2 even when the
    packed schedule tail breaks strict temporal period-2.
    """
    win = [x for x in seq if x]
    if not win:
        return 0, 0, 0
    ndist = len(set(win))
    L = len(win)
    temporal = L
    for P in range(1, L + 1):
        if all(win[i] == win[i + P] for i in range(L - P)):
            temporal = P
            break
    P = min(temporal, ndist)
    return P, L, ndist


def slot_table_depth(events, mx, my, makespan):
    """Per-router min period P of mesh (in_dir→out_dir) crossbar pattern.

    Only nonempty cycles; Local inject/eject stripped (topology-only).
    """
    arrive = {}
    for (s, p, c, t, lat, arr, kind) in events:
        arrive[(s, c)] = arr

    conn = defaultdict(set)
    for (s, p, c, t, lat, arr, kind) in events:
        out_d = dir_of(p, c, mx)
        a_in = arrive.get((s, p))
        if a_in is None:
            in_d = "L"
        else:
            in_d = None
            for (s2, p2, c2, t2, lat2, arr2, k2) in events:
                if s2 == s and c2 == p and arr2 == a_in:
                    in_d = {"E": "W", "W": "E", "S": "N", "N": "S"}.get(
                        dir_of(p2, p, mx), "?")
                    break
            if in_d is None:
                in_d = "L"
        conn[(p, t)].add((in_d, out_d))

    n = mx * my
    series = {p: [frozenset() for _ in range(makespan + 1)] for p in range(n)}
    for (p, t), cset in conn.items():
        if 0 <= t <= makespan:
            series[p][t] = topo_crossbar(cset)

    per_router = {}
    for p in range(n):
        P, span, ndist = min_period_nonempty_topo(series[p])
        per_router[p] = {"period": P, "span": span, "distinct": ndist}
    depths = [r["period"] for r in per_router.values() if r["span"] > 0]
    return {
        "per_router": per_router,
        "max_period": max(depths) if depths else 0,
        "min_period": min(depths) if depths else 0,
        "mean_period": sum(depths) / len(depths) if depths else 0.0,
        "series": series,
    }


def afifo_occupancy_series(afifo_profile, makespan):
    """Extract global AFIFO occupancy time-series from schedule result."""
    if not afifo_profile:
        return []
    g = afifo_profile.get("global", [])
    if len(g) <= makespan:
        return list(g) + [0] * (makespan + 1 - len(g))
    return g[: makespan + 1]


def odd_cycle_points(series):
    """Return [(cycle, util), ...] for odd cycles only."""
    return [(c, v) for c, v in enumerate(series) if c % 2 == 1]


def svg_line_chart(series_list, labels, width=720, height=220, ymax=None,
                   colors=("#2563eb", "#16a34a", "#ea580c")):
    """Simple inline SVG multi-line chart. series_list: list of [(x,y)...] or [y,...]."""
    if not series_list:
        return '<svg width="720" height="220"></svg>'
    # normalize to list of [(x, y), ...] point lists
    pts_list = []
    for s in series_list:
        if s and isinstance(s[0], (list, tuple)):
            pts_list.append([(float(x), float(v)) for x, v in s])
        else:
            pts_list.append([(float(i), float(v)) for i, v in enumerate(s)])
    all_y = [v for pts in pts_list for _, v in pts]
    mx_y = ymax or max(all_y) or 1.0
    mx_y = max(mx_y, 0.01)
    x_min = min(x for pts in pts_list for x, _ in pts)
    x_max = max(x for pts in pts_list for x, _ in pts)
    pad_l, pad_r, pad_t, pad_b = 48, 16, 16, 32
    iw = width - pad_l - pad_r
    ih = height - pad_t - pad_b
    lines = [
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'xmlns="http://www.w3.org/2000/svg">',
        f'<rect width="100%" height="100%" fill="#fff"/>',
        f'<line x1="{pad_l}" y1="{pad_t+ih}" x2="{pad_l+iw}" y2="{pad_t+ih}" '
        f'stroke="#cbd5e1"/>',
        f'<line x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{pad_t+ih}" stroke="#cbd5e1"/>',
        f'<text x="8" y="{pad_t+ih//2}" font-size="10" fill="#64748b" '
        f'transform="rotate(-90 8,{pad_t+ih//2})">利用率</text>',
        f'<text x="{pad_l+iw//2}" y="{height-4}" font-size="10" fill="#64748b" '
        f'text-anchor="middle">cycle</text>',
    ]
    x_span = max(x_max - x_min, 1.0)
    for yi, pts in enumerate(pts_list):
        col = colors[yi % len(colors)]
        svg_pts = []
        for x, v in pts:
            px = pad_l + ((x - x_min) / x_span) * iw
            py = pad_t + ih - (v / mx_y) * ih
            svg_pts.append(f"{px:.1f},{py:.1f}")
        lines.append(f'<polyline points="{" ".join(svg_pts)}" fill="none" '
                     f'stroke="{col}" stroke-width="1.5"/>')
    for i, lab in enumerate(labels):
        col = colors[i % len(colors)]
        lines.append(f'<rect x="{pad_l+i*120}" y="4" width="10" height="10" fill="{col}"/>')
        lines.append(f'<text x="{pad_l+i*120+14}" y="13" font-size="10">{lab}</text>')
    lines.append("</svg>")
    return "\n".join(lines)


def svg_bar_chart(categories, series, labels, width=720, height=260,
                  colors=("#2563eb", "#16a34a", "#ea580c", "#9333ea")):
    """Grouped bar chart. categories=list of str, series=list of value lists."""
    n_cat = len(categories)
    n_ser = len(series)
    if not n_cat:
        return ""
    vmax = max(v for s in series for v in s if v is not None) or 1
    pad_l, pad_r, pad_t, pad_b = 48, 16, 24, 48
    iw = width - pad_l - pad_r
    ih = height - pad_t - pad_b
    bw = iw / (n_cat * (n_ser + 1))
    lines = [
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'xmlns="http://www.w3.org/2000/svg">',
        f'<rect width="100%" height="100%" fill="#fff"/>',
    ]
    for ci, cat in enumerate(categories):
        for si, vals in enumerate(series):
            v = vals[ci]
            if v is None:
                continue
            x = pad_l + ci * (n_ser + 1) * bw + si * bw + bw * 0.2
            h = (v / vmax) * ih
            y = pad_t + ih - h
            col = colors[si % len(colors)]
            lines.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw*0.7:.1f}" '
                         f'height="{h:.1f}" fill="{col}"/>')
            lines.append(f'<text x="{x+bw*0.35:.1f}" y="{pad_t+ih+14}" '
                         f'font-size="9" text-anchor="middle">{cat}</text>')
    for si, lab in enumerate(labels):
        col = colors[si % len(colors)]
        lines.append(f'<rect x="{pad_l+si*100}" y="4" width="10" height="10" fill="{col}"/>')
        lines.append(f'<text x="{pad_l+si*100+14}" y="13" font-size="10">{lab}</text>')
    lines.append("</svg>")
    return "\n".join(lines)


def svg_depth_heatmap(per_router, mx, my, key="period", cell=14):
    """16x16 grid colored by per-router depth metric."""
    vals = [per_router[p].get(key, 0) for p in range(mx * my)]
    active = [v for v in vals if v > 0]
    vmax = max(active) if active else 1

    def color(v):
        if v <= 0:
            return "#f1f5f9"
        t = v / vmax
        r = int(219 + t * (37 - 219))
        g = int(234 + t * (99 - 234))
        b = int(254 + t * (235 - 254))
        return f"rgb({r},{g},{b})"

    w = mx * cell + 40
    h = my * cell + 40
    lines = [
        f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        f'xmlns="http://www.w3.org/2000/svg">',
        f'<rect width="100%" height="100%" fill="#fff"/>',
    ]
    for y in range(my):
        for x in range(mx):
            p = x + mx * y
            v = per_router[p].get(key, 0)
            lines.append(
                f'<rect x="{20+x*cell}" y="{20+y*cell}" width="{cell-1}" '
                f'height="{cell-1}" fill="{color(v)}" stroke="#e2e8f0"/>')
    lines.append("</svg>")
    return "\n".join(lines)
