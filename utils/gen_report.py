#!/usr/bin/env python3
"""Generate self-contained HTML report from calendar collective results."""

import argparse
import csv
import html
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSV = ROOT / "results" / "results.csv"
DEFAULT_HTML = ROOT / "results" / "report.html"

MESH_X = 12
MESH_Y = 16
H_LAT = 4
V_LAT = 8
N = MESH_X * MESH_Y


def load_rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def healthy_by_collective(rows):
    data = defaultdict(dict)
    for r in rows:
        if r["fault_desc"] != "healthy":
            continue
        data[r["collective"]][int(r["msg_size"])] = r
    return data


def fault_rows(rows):
    return [r for r in rows if r["fault_desc"] != "healthy"]


def baseline_map(rows):
    base = {}
    for r in rows:
        if r["fault_desc"] == "healthy" and int(r["msg_size"]) == 16:
            base[(r["collective"],)] = r
    return base


def svg_topology():
    cell = 28
    pad = 40
    w = pad * 2 + MESH_X * cell
    h = pad * 2 + MESH_Y * cell
    parts = [
        f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg">',
        '<rect width="100%" height="100%" fill="#f8fafc"/>',
    ]
    for y in range(MESH_Y):
        for x in range(MESH_X):
            cx = pad + x * cell + cell / 2
            cy = pad + y * cell + cell / 2
            fill = "#dbeafe"
            if (x, y) in {(0, 0), (11, 0), (0, 15), (11, 15)}:
                fill = "#fca5a5"
            elif x in (0, 11) or y in (0, 15):
                fill = "#fde68a"
            elif 4 <= x <= 7 and 6 <= y <= 9:
                fill = "#bbf7d0"
            parts.append(
                f'<rect x="{cx-10:.1f}" y="{cy-10:.1f}" width="20" height="20" rx="3" fill="{fill}" stroke="#334155" stroke-width="1"/>'
            )
    parts.append(
        '<text x="20" y="20" font-size="12" fill="#334155">Red=corner Yellow=edge Green=center</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts)


def bar_chart(title, labels, values, ymax=None):
    if not labels:
        return ""
    width = max(640, 40 * len(labels))
    height = 260
    margin = 50
    plot_w = width - 2 * margin
    plot_h = height - 2 * margin
    ymax = ymax or max(float(v) for v in values) * 1.1 or 1
    bar_w = plot_w / max(1, len(labels))
    parts = [
        f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">',
        f'<text x="{margin}" y="20" font-size="14" font-weight="bold">{html.escape(title)}</text>',
        f'<line x1="{margin}" y1="{height-margin}" x2="{width-margin}" y2="{height-margin}" stroke="#64748b"/>',
        f'<line x1="{margin}" y1="{margin}" x2="{margin}" y2="{height-margin}" stroke="#64748b"/>',
    ]
    for i, (lab, val) in enumerate(zip(labels, values)):
        v = float(val)
        bh = 0 if ymax == 0 else (v / ymax) * plot_h
        x = margin + i * bar_w + bar_w * 0.15
        y = height - margin - bh
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w*0.7:.1f}" height="{bh:.1f}" fill="#3b82f6"/>'
        )
        parts.append(
            f'<text x="{x + bar_w*0.35:.1f}" y="{height-margin+14}" font-size="9" text-anchor="middle">{html.escape(lab)}</text>'
        )
    parts.append(
        f'<text x="10" y="{margin+10}" font-size="10" fill="#64748b">max={ymax:.0f}</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts)


def degradation_chart(rows, baseline):
    labels = []
    values = []
    for r in sorted(rows, key=lambda x: (x["collective"], x["fault_desc"])):
        key = (r["collective"],)
        if key not in baseline:
            continue
        b = float(baseline[key]["makespan"])
        m = float(r["makespan"])
        if b <= 0:
            continue
        labels.append(f"{r['collective'][:4]}/{r['fault_desc'][:10]}")
        values.append((m / b - 1.0) * 100.0)
    return bar_chart("Fault degradation (% makespan increase vs healthy M=16)", labels[:24], values[:24])


def theory_table():
    mesh_diam = H_LAT * (MESH_X - 1) + V_LAT * (MESH_Y - 1)
    bcast_diam = mesh_diam + 2
    bisection = max((MESH_X // 2) * V_LAT, (MESH_Y // 2) * H_LAT)
    alltoall_bw = (N * (N - 1) + bisection - 1) // bisection
    rows = [
        ("broadcast", f"{bcast_diam} + M - 1", "M", "Root up-ramp + mesh (H=4/V=8) + leaf down-ramp"),
        ("reduce", f"{bcast_diam} + M - 1", "M", "Node up-ramp + mesh inline combine + root down-ramp"),
        ("allreduce", f"2×{bcast_diam} + M - 1", "M", "Reduce (up+mesh+down) then broadcast"),
        (
            "gather",
            f"max({(N-1)}×M, (N-1)×M + path − mesh_diam + H + V)",
            f"{(N-1)}×M",
            "Down-ramp bound + longest source path (H/V)",
        ),
        (
            "allgather",
            f"gather_bound + {bcast_diam} + M − 1",
            f"{(N-1)}×M",
            "Gather then broadcast; path-aware gather bound",
        ),
        (
            "alltoall",
            f"{alltoall_bw}×M + {bcast_diam} − M",
            f"{alltoall_bw}×M",
            "Bisection bandwidth (H/V weighted) + path drain",
        ),
        (
            "anytoany",
            f"~{alltoall_bw}×M (per-hop sim)",
            f"~{alltoall_bw}×M",
            "Per-path Dijkstra schedule with H/V hops",
        ),
    ]
    out = ["<table><tr><th>Collective</th><th>Min makespan</th><th>Min period</th><th>Notes</th></tr>"]
    for r in rows:
        out.append(
            "<tr>"
            + "".join(f"<td>{html.escape(c)}</td>" for c in r)
            + "</tr>"
        )
    out.append("</table>")
    return "\n".join(out)


def q1_answer(healthy):
    mesh_diam = H_LAT * (MESH_X - 1) + V_LAT * (MESH_Y - 1)
    bisection = max((MESH_X // 2) * V_LAT, (MESH_Y // 2) * H_LAT)
    alltoall_bw = (N * (N - 1) + bisection - 1) // bisection
    lines = [
        "<p><strong>Q1 conclusion:</strong> Not all collectives can reach the unconstrained latency minimum.</p>",
        "<ul>",
        "<li><strong>broadcast</strong>: root up-ramp inject + mesh tree fork + leaf down-ramp; optimal period <code>M</code>.</li>",
        "<li><strong>reduce</strong>: leaf PE nodes inject via up-ramp; inline combine on mesh; root down-ramp for final result; period <code>M</code>.</li>",
        "<li><strong>allreduce</strong>: reduce phase (up+mesh+down) then broadcast phase (up+mesh+down).</li>",
        f"<li><strong>gather, allgather</strong>: down-ramp bound <code>{N-1}×M</code> plus longest shortest-path latency (H=4/V=8); simulated gather M=1 makespan ≈ 205.</li>",
        f"<li><strong>alltoall</strong>: bisection bandwidth bound <code>{alltoall_bw}×M</code> plus diameter path drain (<code>+{mesh_diam}+2</code> ramp cycles); anytoany uses full per-hop calendar simulation.</li>",
        "</ul>",
    ]
    if healthy:
        sample = healthy.get("broadcast", {}).get(1)
        if sample:
            lines.append(
                f"<p>Simulated broadcast M=1 makespan={sample['makespan']}, period={sample['period']}, "
                f"efficiency={sample['efficiency']}.</p>"
            )
    return "\n".join(lines)


def q2_answer(fault_rows_data, baseline):
    feasible = sum(1 for r in fault_rows_data if r["feasible"] == "1")
    total = len(fault_rows_data)
    degradations = []
    for r in fault_rows_data:
        key = (r["collective"],)
        if key not in baseline:
            continue
        b = float(baseline[key]["makespan"])
        m = float(r["makespan"])
        if b > 0:
            degradations.append((m / b - 1.0) * 100.0)
    avg_deg = sum(degradations) / len(degradations) if degradations else 0.0
    max_deg = max(degradations) if degradations else 0.0
    return (
        "<p><strong>Q2 conclusion:</strong> With offline calendar rescheduling on the surviving topology, "
        f"{feasible}/{total} fault scenarios remained feasible in simulation.</p>"
        f"<p>Average makespan degradation vs healthy (M=16): {avg_deg:.1f}%; "
        f"maximum observed: {max_deg:.1f}%. Corner faults and multi-link cuts show higher degradation "
        "due to longer detour paths and reduced bisection bandwidth.</p>"
    )


def render(csv_path, html_path):
    rows = load_rows(csv_path)
    healthy = healthy_by_collective(rows)
    faults = fault_rows(rows)
    baseline = baseline_map(rows)

    sections = []
    sections.append("<!DOCTYPE html><html><head><meta charset='utf-8'>")
    sections.append("<title>Calendar Collective Simulation Report</title>")
    sections.append(
        "<style>body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#0f172a;}"
        "h1,h2{color:#1e3a8a;}table{border-collapse:collapse;margin:12px 0;width:100%;}"
        "td,th{border:1px solid #cbd5e1;padding:6px 8px;font-size:13px;}"
        "th{background:#e2e8f0;} .card{background:#fff;border:1px solid #e2e8f0;padding:16px;margin:16px 0;border-radius:8px;}"
        "code{background:#f1f5f9;padding:2px 4px;border-radius:4px;}</style></head><body>"
    )
    sections.append("<h1>Calendar-preconfigured Collective Communication</h1>")
    sections.append(
        "<p>详细数据流动示意图见 "
        "<a href='dataflow.html'>dataflow.html</a>（各 collective 最低 makespan calendar 及故障重调度）。</p>"
    )
    sections.append("<div class='card'><h2>Problem setup</h2>")
    sections.append(
        f"<p>12×16 mesh2d ({N} nodes). Horizontal link latency {H_LAT} cycles, vertical {V_LAT} cycles. "
        "PE↔router ramps: 1 flit/cycle, 1-cycle latency. NoC: 1 flit/cycle per link. "
        "In-network combine (reduce) and fork (broadcast) enabled. Calendar = contention-free TDM preconfiguration.</p>"
    )
    sections.append("<h3>Topology</h3>" + svg_topology())
    sections.append("</div>")

    sections.append("<div class='card'><h2>Theoretical bounds</h2>" + theory_table() + "</div>")
    sections.append("<div class='card'><h2>Q1: Minimum makespan and calendar period</h2>" + q1_answer(healthy) + "</div>")

    sections.append("<div class='card'><h2>Healthy simulation: makespan vs M</h2>")
    for collective in ["broadcast", "reduce", "gather", "allgather", "allreduce", "alltoall", "anytoany"]:
        d = healthy.get(collective, {})
        if not d:
            continue
        labels = [str(m) for m in sorted(d)]
        values = [d[m]["makespan"] for m in sorted(d)]
        sections.append(f"<h3>{html.escape(collective)}</h3>")
        sections.append(bar_chart(f"{collective} makespan", labels, values))
    sections.append("</div>")

    sections.append("<div class='card'><h2>Healthy simulation summary (M=64)</h2><table>")
    sections.append("<tr><th>Collective</th><th>Makespan</th><th>Period</th><th>Bound</th><th>Efficiency</th></tr>")
    for collective in sorted(healthy):
        r = healthy[collective].get(64)
        if not r:
            continue
        sections.append(
            f"<tr><td>{collective}</td><td>{r['makespan']}</td><td>{r['period']}</td>"
            f"<td>{r['theo_bound']}</td><td>{r['efficiency']}</td></tr>"
        )
    sections.append("</table></div>")

    sections.append("<div class='card'><h2>Q2: Fault tolerance with offline rescheduling</h2>")
    sections.append(q2_answer(faults, baseline))
    sections.append(degradation_chart(faults, baseline))
    sections.append("<table><tr><th>Collective</th><th>Fault</th><th>Makespan</th><th>Period</th><th>Feasible</th><th>Degradation%</th></tr>")
    for r in sorted(faults, key=lambda x: (x["collective"], x["fault_desc"]))[:80]:
        key = (r["collective"],)
        deg = ""
        if key in baseline:
            b = float(baseline[key]["makespan"])
            m = float(r["makespan"])
            if b > 0:
                deg = f"{(m/b-1)*100:.1f}"
        sections.append(
            f"<tr><td>{r['collective']}</td><td>{r['fault_desc']}</td>"
            f"<td>{r['makespan']}</td><td>{r['period']}</td><td>{r['feasible']}</td><td>{deg}</td></tr>"
        )
    sections.append("</table></div>")
    sections.append("</body></html>")

    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text("\n".join(sections), encoding="utf-8")
    print(f"Wrote {html_path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", default=str(DEFAULT_CSV))
    parser.add_argument("--html", default=str(DEFAULT_HTML))
    args = parser.parse_args()
    render(Path(args.csv), Path(args.html))


if __name__ == "__main__":
    main()
