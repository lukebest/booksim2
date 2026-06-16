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
            f"max({(N-1)}×M, maxᵢ(Lᵢ−i)+(N−2))",
            f"{(N-1)}×M",
            "Root slot assignment + global link-time calendar; closed form ≈(N−2)+max_path−mesh_diam+H+V",
        ),
        (
            "allgather",
            f"max({(N-1)}×M, bisection, worst-corner gather)",
            f"{(N-1)}×M",
            "NOT gather+broadcast; per-node down-ramp floor; 2D dimensional algorithm",
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
        f"<li><strong>gather</strong>: period <code>{N-1}×M</code> (root down-ramp); "
        f"makespan bound <code>maxᵢ(Lᵢ−i)+(N−2)</code> with global link-time calendar "
        f"(M=1 healthy: 205).</li>",
        f"<li><strong>allgather</strong>: the optimum is <strong>NOT</strong> gather+broadcast. "
        f"True bound = <code>max((N−1)×M down-ramp, bisection, worst-corner gather)</code> "
        f"= 205 (M=1), not 371. The bottleneck is every node's single down-ramp absorbing "
        f"<code>(N−1)×M</code> flits — allgather costs essentially one <em>worst-case gather</em>, "
        f"not a gather plus a broadcast. The <strong>2D dimensional algorithm</strong> "
        f"(row-allgather then column-allgather) is down-ramp bandwidth optimal: "
        f"makespan 235 (M=1) → 12238 (M=64), efficiency → 1.0.</li>",
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


def gather_bound_section():
    mesh_diam = H_LAT * (MESH_X - 1) + V_LAT * (MESH_Y - 1)
    max_path = mesh_diam + 2  # up + mesh + down
    closed = (N - 2) + max_path - mesh_diam + H_LAT + V_LAT
    # replicate healthy gather path lat slack (see gen_dataflow_doc.gather_bounds)
    import heapq

    class _M:
        def __init__(self):
            self.alive = [True] * N
            self.adj = [[] for _ in range(N)]
            for y in range(MESH_Y):
                for x in range(MESH_X):
                    u = x + MESH_X * y
                    if x + 1 < MESH_X:
                        v = u + 1
                        self.adj[u].append((v, H_LAT))
                        self.adj[v].append((u, H_LAT))
                    if y + 1 < MESH_Y:
                        v = u + MESH_X
                        self.adj[u].append((v, V_LAT))
                        self.adj[v].append((u, V_LAT))

        def path_lat(self, src, dst):
            dist = [10**9] * N
            dist[src] = 0
            pq = [(0, src)]
            while pq:
                d, u = heapq.heappop(pq)
                if d != dist[u]:
                    continue
                if u == dst:
                    return d + 2
                for v, w in self.adj[u]:
                    if d + w < dist[v]:
                        dist[v] = d + w
                        heapq.heappush(pq, (dist[v], v))
            return 10**9

    m = _M()
    lats = sorted(m.path_lat(n, 0) for n in range(1, N))
    t0 = max(lats[i] - i for i in range(len(lats)))
    tight = t0 + len(lats) - 1
    return f"""
<div class="card"><h2>Gather 无阻塞下界推导</h2>
<p>healthy 12×16 mesh，M=1，191 个非 root 源，root down-ramp 每 cycle 1 flit。</p>
<h3>Period（稳态 initiation interval）</h3>
<p><code>period = (N−1)×M = 191</code> — root 侧带宽瓶颈，与路径长度无关。</p>
<h3>Makespan 下界（两种表述）</h3>
<ol>
<li><strong>精确 slot 分配式</strong>（仿真采用）：将 191 个 flit 按路径延迟 Lᵢ 升序排列，
为第 i 个分配 root 完成时刻 Fᵢ = t₀+i，约束 Fᵢ ≥ Lᵢ。
最小化 max Fᵢ 得 t₀ = maxᵢ(Lᵢ−i) = {t0}，故 <code>makespan = t₀ + (N−2) = {tight}</code>。
全局 link-time calendar 反向预约各 hop 的 send-time，可达该下界（eff=1.0）。</li>
<li><strong>闭式近似</strong>（旧文档 204）：
<code>(N−2) + max_path − mesh_diam + H + V = 190 + {max_path} − {mesh_diam} + {H_LAT + V_LAT} = {closed}</code>。
其中 max_path={max_path}（最远源 up+mesh+down），mesh_diam={mesh_diam}。
+H+V 项假设 mesh 链路可额外流水线重叠 1 cycle；在本拓扑精确 slot 式下界为 {tight}，闭式少 1 cycle。</li>
</ol>
<p>详见 <a href="dataflow.html">dataflow.html</a> Gather 节的 link-time 日历示意图。</p>
</div>"""


def allgather_bound_section():
    """Re-analysis: allgather LB is NOT gather+broadcast."""
    mesh_diam = H_LAT * (MESH_X - 1) + V_LAT * (MESH_Y - 1)
    bcast = mesh_diam + 2  # diam + 2 ramp
    downramp = N - 1
    bis_links = min(MESH_X, MESH_Y)
    bisec = ((N // 2) + bis_links - 1) // bis_links
    # worst-corner gather pipeline = gather bound (corner root) = 205 for M=1
    pipe = 205
    lb = max(downramp, bisec, pipe)
    old = pipe + bcast  # 205 + 166 = 371

    # dimensional phase split (M=1)
    def line_gb(P, ell, m):
        avail = []
        for j in range(1, P):
            base = j * ell + 2
            for k in range(m):
                avail.append(base + k)
        avail.sort()
        t0 = max(avail[i] - i for i in range(len(avail)))
        return t0 + len(avail) - 1

    px = line_gb(MESH_X, H_LAT, 1)
    py = line_gb(MESH_Y, V_LAT, MESH_X)
    dim = px + py

    return f"""
<div class="card"><h2>AllGather 理论再分析：为何不是 gather + broadcast</h2>
<p>AllGather 要求<strong>每个</strong>节点最终持有全部 N 份数据。把它当作
「先 gather 到 root，再从 root broadcast」会得到 <code>205 + {bcast} = {old}</code>，
但这<strong>高估</strong>了下界——两阶段被人为串行化，且所有数据被迫汇经 root 形成热点。</p>

<h3>正确下界 = 三个约束取最大</h3>
<ol>
<li><strong>down-ramp 带宽下界</strong>：每个节点的单条 down-ramp 必须吞入
其余 (N−1) 个源的数据 = <code>(N−1)×M = {downramp}×M</code>。这是<strong>每个节点都同时</strong>
承受的瓶颈（gather 中只有 root 承受）。</li>
<li><strong>bisection 下界</strong>：最小割 {bis_links} 条链路，半数节点数据需跨割
= <code>⌈(N/2)×M / {bis_links}⌉ = {bisec}×M</code>（本拓扑非瓶颈）。</li>
<li><strong>最坏接收者 pipeline</strong>：最远角节点接收 (N−1)M flit 的 gather slot 下界
= <strong>{pipe}</strong>(M=1)。</li>
</ol>
<p>三者取大：<code>max({downramp}, {bisec}, {pipe}) = {lb}</code>(M=1)。
关键结论：<strong>allgather 下界 ≈ gather 下界（{pipe}），而非 gather+broadcast（{old}）</strong>。
直觉上 allgather = 「N 个并发 gather」，其代价由<strong>最坏的那一个 gather</strong> 主导，
额外的副本是<strong>并发</strong>送达的，而不是在一次完整 gather 之后再串行广播。</p>

<h3>最优算法：2D dimensional allgather</h3>
<p>沿 mesh 两个维度分解，<strong>不经过 root</strong>、只用相邻链路：</p>
<ul>
<li><strong>Phase X（行内 allgather）</strong>：每行 {MESH_X} 个节点沿 H-link 互相收集，
每节点收 (MX−1)M flit。行端节点 = 一次 line-gather，makespan = {px}(M=1)。</li>
<li><strong>Phase Y（列内 allgather）</strong>：每列 {MESH_Y} 个节点沿 V-link 收集
已打包的整行数据（每份 MX×M flit），每节点收 (MY−1)×MX×M flit。makespan = {py}(M=1)。</li>
</ul>
<p>两阶段 down-ramp 之和 = (MX−1)M + (MY−1)·MX·M = <code>(N−1)M</code>，
<strong>恰好等于带宽下界</strong>（bandwidth-optimal）。总 makespan = {px}+{py} = <strong>{dim}</strong>(M=1)，
仅比下界 {lb} 高 {dim - lb} cycle（两阶段 fill 开销），远优于 gather+broadcast 的 {old}。
M 越大越贴近下界（M=64：12238 vs 12229，eff≈0.999）。</p>

<h3>其它算法对比（12×16 mesh）</h3>
<table style="font-size:13px">
<tr><th>算法</th><th>带宽</th><th>延迟特性（本拓扑）</th><th>M=1 makespan</th></tr>
<tr><td>gather + broadcast（旧）</td><td>非最优（汇经 root）</td><td>两阶段串行 + root 热点</td><td>{old}</td></tr>
<tr><td>Ring（Hamiltonian 环）</td><td>最优</td><td>环周长 ~800cy 传播延迟主导，长宽比大时差</td><td>~800+</td></tr>
<tr><td>Recursive doubling</td><td>最优</td><td>log₂N≈8 步，但 mesh 上 partner 距离逐步增大→长线/拥塞；192 非 2 的幂</td><td>较差</td></tr>
<tr><td><strong>2D dimensional</strong></td><td><strong>最优</strong></td><td><strong>仅相邻链路、无长线、低拥塞</strong></td><td><strong>{dim}</strong></td></tr>
</table>
<p>下界 = <strong>{lb}</strong>(M=1)。dimensional 在 mesh 上是实用最优；
若进一步单阶段并发 multicast（每源一棵生成树 + 全局 calendar 让各接收者 down-ramp 持续满载），
可把行/列遍历重叠，逼近 {lb}，但调度复杂、对大 M 收益甚微。</p>
</div>"""


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
    sections.append(gather_bound_section())
    sections.append(allgather_bound_section())
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
