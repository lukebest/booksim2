#!/usr/bin/env python3
"""Generate mesh2d spatial data-flow diagrams for calendar collectives."""

import csv
import heapq
import html
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "results" / "results.csv"
OUT = ROOT / "results" / "dataflow.html"

MX, MY = 12, 16
H_LAT, V_LAT = 4, 8
N = MX * MY
ROOT_NODE = 0
PAD = 36
CS = 26  # cell size px


def nid(x, y):
    return x + MX * y


def coord(n):
    return n % MX, n // MX


def pos(n):
    x, y = coord(n)
    return PAD + x * CS + CS / 2, PAD + y * CS + CS / 2


class Mesh:
    def __init__(self, dead_nodes=None, dead_edges=None):
        dead_nodes = set(dead_nodes or [])
        dead_edges = set(dead_edges or [])  # (a,b) undirected
        self.alive = [i not in dead_nodes for i in range(N)]
        self.edges = []  # (a,b,lat,kind)
        for y in range(MY):
            for x in range(MX):
                u = nid(x, y)
                if not self.alive[u]:
                    continue
                if x + 1 < MX:
                    v = nid(x + 1, y)
                    if self.alive[v] and (u, v) not in dead_edges and (v, u) not in dead_edges:
                        self.edges.append((u, v, H_LAT, "h"))
                        self.edges.append((v, u, H_LAT, "h"))
                if y + 1 < MY:
                    v = nid(x, y + 1)
                    if self.alive[v] and (u, v) not in dead_edges and (v, u) not in dead_edges:
                        self.edges.append((u, v, V_LAT, "v"))
                        self.edges.append((v, u, V_LAT, "v"))
        self.adj = [[] for _ in range(N)]
        for u, v, lat, k in self.edges:
            self.adj[u].append((v, lat))

    def dijkstra_tree(self, root):
        parent = [-1] * N
        dist = [10**9] * N
        if not self.alive[root]:
            return parent
        dist[root] = 0
        pq = [(0, root)]
        while pq:
            d, u = heapq.heappop(pq)
            if d != dist[u]:
                continue
            for v, w in self.adj[u]:
                if d + w < dist[v]:
                    dist[v] = d + w
                    parent[v] = u
                    heapq.heappush(pq, (dist[v], v))
        return parent

    def shortest_path(self, src, dst):
        if not self.alive[src] or not self.alive[dst]:
            return []
        prev = [-1] * N
        dist = [10**9] * N
        dist[src] = 0
        pq = [(0, src)]
        while pq:
            d, u = heapq.heappop(pq)
            if d != dist[u]:
                continue
            if u == dst:
                break
            for v, w in self.adj[u]:
                if d + w < dist[v]:
                    dist[v] = d + w
                    prev[v] = u
                    heapq.heappush(pq, (dist[v], v))
        if src == dst:
            return [src]
        if prev[dst] == -1:
            return []
        path = []
        cur = dst
        while cur != -1:
            path.append(cur)
            cur = prev[cur]
        path.reverse()
        return path

    def tree_edges(self, root):
        parent = self.dijkstra_tree(root)
        edges = []
        for v in range(N):
            if parent[v] >= 0:
                edges.append((parent[v], v))
        return edges


def svg_size(extra_h=0):
    w = PAD * 2 + MX * CS
    h = PAD * 2 + MY * CS + extra_h
    return w, h


def svg_defs(uid):
    return f"""
<defs>
  <marker id="arr_{uid}" markerWidth="7" markerHeight="7" refX="5.5" refY="3.5" orient="auto">
    <polygon points="0,0 7,3.5 0,7" fill="currentColor"/>
  </marker>
</defs>"""


def draw_grid(uid, mesh, root=ROOT_NODE, dead_mark=None, title=""):
    w, h = svg_size(56 if title else 0)
    parts = [
        f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg">',
        svg_defs(uid),
    ]
    if title:
        parts.append(f'<text x="{PAD}" y="22" font-size="12" fill="#334155">{html.escape(title)}</text>')

    # faint mesh links
    drawn = set()
    for u, v, lat, kind in mesh.edges:
        key = (min(u, v), max(u, v))
        if key in drawn:
            continue
        drawn.add(key)
        x1, y1 = pos(u)
        x2, y2 = pos(v)
        stroke = "#cbd5e1" if kind == "h" else "#e2e8f0"
        parts.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{stroke}" stroke-width="1"/>'
        )

    # nodes
    for n in range(N):
        if not mesh.alive[n]:
            continue
        x, y = pos(n)
        fill = "#dbeafe"
        stroke = "#64748b"
        r = 4.5
        if n == root:
            fill = "#fef3c7"
            stroke = "#b45309"
            r = 6
        if dead_mark and n in dead_mark:
            fill = "#fecaca"
            stroke = "#dc2626"
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{fill}" stroke="{stroke}" stroke-width="1.2"/>'
        )
    if root is not None and mesh.alive[root]:
        rx, ry = pos(root)
        parts.append(
            f'<text x="{rx:.1f}" y="{ry - 9:.1f}" text-anchor="middle" font-size="8" fill="#b45309">R</text>'
        )
    return parts, w, h


def arrow_line(x1, y1, x2, y2, color, uid, width=1.4, shrink=5):
    dx, dy = x2 - x1, y2 - y1
    ln = math.hypot(dx, dy) or 1
    ux, uy = dx / ln, dy / ln
    x1s, y1s = x1 + ux * shrink, y1 + uy * shrink
    x2s, y2s = x2 - ux * (shrink + 2), y2 - uy * (shrink + 2)
    return (
        f'<line x1="{x1s:.1f}" y1="{y1s:.1f}" x2="{x2s:.1f}" y2="{y2s:.1f}" '
        f'stroke="{color}" stroke-width="{width}" color="{color}" '
        f'marker-end="url(#arr_{uid})" opacity="0.85"/>'
    )


def draw_flow_edges(parts, edges, uid, color, reverse=False):
    for a, b in edges:
        x1, y1 = pos(a)
        x2, y2 = pos(b)
        if reverse:
            x1, y1, x2, y2 = x2, y2, x1, y1
        parts.append(arrow_line(x1, y1, x2, y2, color, uid))


def mesh_coords_only():
    mesh = Mesh()
    uid = "coord"
    parts, w, h = draw_grid(
        uid, mesh, title="12×16 mesh：x→右, y→下；节点 id=x+12y；R=root@0"
    )
    # axis labels
    for x in range(MX):
        px, _ = pos(nid(x, 0))
        parts.append(f'<text x="{px:.0f}" y="{PAD - 8}" text-anchor="middle" font-size="7" fill="#64748b">{x}</text>')
    for y in range(MY):
        _, py = pos(nid(0, y))
        parts.append(f'<text x="{PAD - 10}" y="{py + 3:.0f}" text-anchor="end" font-size="7" fill="#64748b">{y}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def _mesh_coords_only():
    return mesh_coords_only()


def mesh_broadcast(mesh=None):
    mesh = mesh or Mesh()
    uid = "bc"
    parts, w, h = draw_grid(
        uid, mesh, title="Broadcast：root(0) 沿 latency 最小树向外 fork（蓝箭头 = flit 扩散方向）"
    )
    edges = mesh.tree_edges(ROOT_NODE)
    draw_flow_edges(parts, edges, uid, "#2563eb", reverse=False)
    parts.append(
        f'<text x="{PAD}" y="{h - 12}" font-size="10" fill="#64748b">'
        f"最远角 (11,15) 路径延迟 164cy + ramp → makespan=166 (M=1)；period=M</text></svg>"
    )
    return "\n".join(parts)


def mesh_reduce(mesh=None):
    mesh = mesh or Mesh()
    uid = "rd"
    parts, w, h = draw_grid(
        uid, mesh, title="Reduce：各节点 flit 沿同一棵树的反向汇聚至 root（红箭头 = 归约方向，router combine）"
    )
    edges = mesh.tree_edges(ROOT_NODE)
    draw_flow_edges(parts, edges, uid, "#dc2626", reverse=True)
    parts.append(
        f'<text x="{PAD}" y="{h - 12}" font-size="10" fill="#64748b">'
        f"叶节点先经 up-ramp 注入，router inline combine；root down-ramp 输出 → makespan=166 (M=1)</text></svg>"
    )
    return "\n".join(parts)


def path_latency(mesh, src, dst):
    path = mesh.shortest_path(src, dst)
    if len(path) < 2:
        return 10**9
    lat = 1  # up-ramp
    for j in range(len(path) - 1):
        u, v = path[j], path[j + 1]
        for a, b, w, _ in mesh.edges:
            if a == u and b == v:
                lat += w
                break
    lat += 1  # down-ramp
    return lat


def gather_bounds():
    """Return closed-form 204, tight slot bound, period, slack for healthy gather M=1."""
    mesh = Mesh()
    lats = []
    for n in range(N):
        if not mesh.alive[n] or n == ROOT_NODE:
            continue
        lats.append(path_latency(mesh, n, ROOT_NODE))
    lats.sort()
    slack = max(lats[i] - i for i in range(len(lats)))
    slack_i = max(range(len(lats)), key=lambda i: lats[i] - i)
    tight = slack + len(lats) - 1
    mesh_diam = H_LAT * (MX - 1) + V_LAT * (MY - 1)
    max_path = max(lats)
    closed = (len(lats) - 1) + max_path - mesh_diam + H_LAT + V_LAT
    period = len(lats)
    # node id for slack argmax (sorted by path latency, then node id)
    sources = []
    for n in range(N):
        if not mesh.alive[n] or n == ROOT_NODE:
            continue
        sources.append((path_latency(mesh, n, ROOT_NODE), n))
    sources.sort()
    slack_node = sources[slack_i][1]
    sx, sy = coord(slack_node)
    return {
        "lats": lats,
        "slack": slack,
        "slack_i": slack_i,
        "slack_L": lats[slack_i],
        "slack_node": slack_node,
        "slack_coord": (sx, sy),
        "tight": tight,
        "closed": closed,
        "period": period,
        "max_path": max_path,
        "min_path": lats[0] if lats else 0,
        "mesh_diam": mesh_diam,
    }


def gather_link_calendar(mesh, max_cycles=48):
    """Simulate global link-time calendar (backward placement) for hot links near root."""
    items = []
    for n in range(N):
        if not mesh.alive[n] or n == ROOT_NODE:
            continue
        L = path_latency(mesh, n, ROOT_NODE)
        items.append({"src": n, "path_lat": L})
    items.sort(key=lambda x: (x["path_lat"], x["src"]))

    target = []
    for i, it in enumerate(items):
        if i == 0:
            target.append(it["path_lat"])
        else:
            target.append(max(it["path_lat"], target[i - 1] + 1))

    occupancy = {}

    def link_free(key, t):
        return t not in occupancy.get(key, set())

    def occupy(key, t):
        occupancy.setdefault(key, set()).add(t)

    def edge_lat(u, v):
        for a, b, w, _ in mesh.edges:
            if a == u and b == v:
                return w
        return H_LAT

    for ord_i, it in enumerate(items):
        path = mesh.shortest_path(it["src"], ROOT_NODE)
        while True:
            hops = []
            t = target[ord_i]
            ok = True

            if True:  # down-ramp
                key = ("down", ROOT_NODE)
                lat = 1
                send = t - lat
                if send < 0 or not link_free(key, send):
                    ok = False
                else:
                    hops.insert(0, (key, send, t))
                    t = send

            if ok:
                for j in range(len(path) - 2, -1, -1):
                    key = (path[j], path[j + 1])
                    lat = edge_lat(path[j], path[j + 1])
                    send = t - lat
                    if send < 0 or not link_free(key, send):
                        ok = False
                        break
                    hops.insert(0, (key, send, t))
                    t = send

            if ok:
                key = ("up", it["src"])
                lat = 1
                send = t - lat
                if send < 0 or not link_free(key, send):
                    ok = False
                else:
                    hops.insert(0, (key, send, t))

            if ok:
                for key, s, _ in hops:
                    occupy(key, s)
                break

            target[ord_i] += 1
            for j in range(ord_i + 1, len(target)):
                target[j] = max(target[j], target[j - 1] + 1)

    hot = (12, 0)
    hot_times = sorted(occupancy.get(hot, []))
    return {
        "target": target,
        "hot": hot,
        "hot_times": hot_times,
        "makespan": max(target) if target else 0,
    }


def mesh_gather_calendar():
    """SVG: root down-ramp timeline + link-time calendar on hot ingress link."""
    info = gather_bounds()
    mesh = Mesh()
    cal = gather_link_calendar(mesh)
    t0 = info["slack"]
    t1 = cal["makespan"]
    w, h = 820, 320
    parts = [
        f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg">',
        '<text x="16" y="22" font-size="12" fill="#334155" font-weight="bold">'
        "全局 Link-Time Calendar（Gather M=1，root-slot 反向定标 + 链路时隙预约）</text>",
        f'<text x="16" y="40" font-size="10" fill="#64748b">'
        f"t₀=maxᵢ(Lᵢ−i)={info['slack']}；makespan=t₀+190={info['tight']}；"
        f"period=191；闭式近似 {info['closed']}（H+V 流水线假设，少 1cy）</text>",
        # Root down-ramp timeline
        '<text x="16" y="68" font-size="11" fill="#b45309">① Root down-ramp：每 cycle 完成 1 flit（191 连续 slot）</text>',
    ]
    ox, oy, cw = 16, 78, 760
    parts.append(f'<line x1="{ox}" y1="{oy+28}" x2="{ox+cw}" y2="{oy+28}" stroke="#cbd5e1" stroke-width="1"/>')
    # show window [t0 .. t1] scaled
    span = max(t1 - t0 + 1, 40)
    scale = cw / span
    for k in range(0, min(span, 80)):
        cyc = t0 + k
        x = ox + k * scale
        fill = "#fed7aa" if k < info["period"] else "#f1f5f9"
        parts.append(
            f'<rect x="{x:.1f}" y="{oy}" width="{max(scale-0.5,2):.1f}" height="24" fill="{fill}" stroke="#fdba74" stroke-width="0.5"/>'
        )
        if k % 5 == 0:
            parts.append(
                f'<text x="{x:.1f}" y="{oy+44}" font-size="7" fill="#64748b">{cyc}</text>'
            )
    parts.append(
        f'<text x="{ox}" y="{oy+58}" font-size="9" fill="#64748b">'
        f"橙块=root 每 cycle 接收 1 flit（slot {t0}…{t1}）；首 slot 由最远源路径决定</text>"
    )
    # Link-time calendar heatmap
    ly = 150
    parts.append(
        f'<text x="16" y="{ly}" font-size="11" fill="#0369a1">'
        f"② 链路 (12→0) send-time 日历：每 (link,cycle) 至多 1 flit（无冲突 TDM）</text>"
    )
    hot = cal["hot"]
    hx, hy = 16, ly + 12
    nshow = 48
    for k in range(nshow):
        cyc = t0 + k
        x = hx + k * (cw / nshow)
        occupied = cyc in cal["hot_times"]
        fill = "#2563eb" if occupied else "#f8fafc"
        parts.append(
            f'<rect x="{x:.1f}" y="{hy}" width="{cw/nshow - 1:.1f}" height="22" '
            f'fill="{fill}" stroke="#94a3b8" stroke-width="0.4"/>'
        )
    parts.append(
        f'<text x="{hx}" y="{hy+38}" font-size="9" fill="#64748b">'
        f"蓝=该 cycle 在节点 12→0 竖链路发送；反向定标保证与 root slot 对齐</text>"
    )
    # Backward placement schematic
    by = 230
    parts.append(f'<text x="16" y="{by}" font-size="11" fill="#0f766e">③ 单 flit 反向预约（最远源示例）</text>')
    parts.append(
        f'<text x="16" y="{by+16}" font-size="9" fill="#64748b">'
        "F=目标 root 完成时刻 → down-ramp send=F−1 → 逐 hop 向前减 latency → up-ramp；"
        "若 (link,t) 冲突则 F++ 并更新后续 slot</text>"
    )
    parts.append(
        f'<text x="16" y="{by+32}" font-size="9" fill="#64748b">'
        f"公式：makespan = maxᵢ(Lᵢ−i) + (N−2) = {info['slack']}+{info['period']-1} = {info['tight']}"
        f"（闭式 (N−2)+max_path−mesh_diam+H+V = {info['closed']} 为乐观近似）</text>"
    )
    parts.append("</svg>")
    return "\n".join(parts)


def mesh_gather(mesh=None):
    mesh = mesh or Mesh()
    uid = "ga"
    parts, w, h = draw_grid(
        uid, mesh, title="Gather：每个非 root 沿最短路径向 root(0) 收敛（橙箭头）"
    )
    # node heat by distance to root
    parent = mesh.dijkstra_tree(ROOT_NODE)
    depth = [0] * N
    for v in range(N):
        if not mesh.alive[v] or v == ROOT_NODE:
            continue
        d, cur = 0, v
        while parent[cur] >= 0 and d < 40:
            d += 1
            cur = parent[cur]
        depth[v] = d
    for n in range(N):
        if not mesh.alive[n] or n == ROOT_NODE:
            continue
        x, y = pos(n)
        t = min(depth[n] / 20.0, 1.0)
        r = int(254 - 80 * (1 - t))
        g = int(215 - 100 * t)
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="rgb({r},{g},180)" opacity="0.7"/>')

    # representative full paths (corners, edges, center)
    reps = [nid(11, 15), nid(0, 15), nid(11, 0), nid(6, 0), nid(0, 8), nid(11, 8), nid(6, 15), nid(5, 7)]
    for src in reps:
        if not mesh.alive[src] or src == ROOT_NODE:
            continue
        path = mesh.shortest_path(src, ROOT_NODE)
        for j in range(len(path) - 1):
            a, b = path[j], path[j + 1]
            parts.append(arrow_line(*pos(a), *pos(b), "#ea580c", uid, width=1.4))
    parts.append(
        f'<text x="{PAD}" y="{h - 12}" font-size="10" fill="#64748b">'
        f"节点着色=到 root 跳数；示 8 条完整路径；全局 calendar 填满 root down-ramp → makespan={gather_bounds()['tight']}</text></svg>"
    )
    return "\n".join(parts)


def mesh_gather_section():
    """Spatial paths + link-time calendar diagram."""
    return mesh_gather() + "\n" + mesh_gather_calendar()


def gather_description():
    """HTML prose for Gather card: bounds, L_i, slack=15 explanation."""
    b = gather_bounds()
    t0, tight, closed = b["slack"], b["tight"], b["closed"]
    si, sL = b["slack_i"], b["slack_L"]
    sn, (sx, sy) = b["slack_node"], b["slack_coord"]
    mp, md = b["max_path"], b["mesh_diam"]
    ramp2 = mp - md
    return f"""<p>每个非 root 节点沿 <strong>Dijkstra 最短路径</strong>（H=4, V=8）向 root 发送 flit。
<strong>全局 link-time calendar</strong>：先为每个 flit 分配 root down-ramp 完成时刻
Fᵢ=t₀+i（相邻 slot 间隔 1 cycle），再<strong>反向</strong>逐 hop 在 (link, cycle) 网格上预约 send-time；
冲突则 Fᵢ 递增并重算后续 slot。下图为空间路径 + 时隙日历示意。</p>

<h3>Lᵢ 的含义</h3>
<p><strong>Lᵢ</strong> 是将 191 个非 root 源按<strong>路径延迟升序</strong>编号后，第 i 个源的端到端延迟
（up-ramp 1cy + mesh 最短路径 + root down-ramp 1cy，与 <code>PathLatency</code> 一致）。
M=1 healthy 时 L₀={b['min_path']}（最近邻），L₁₉₀={mp}（角点 (11,15)）。</p>

<h3>精确 makespan 下界：maxᵢ(Lᵢ−i)+(N−2)={tight}</h3>
<p>root down-ramp 每 cycle 只能完成 1 个 flit，191 个 flit 占用连续 slot t₀, t₀+1, …, t₀+190。
第 i 个 flit 最早在 cycle Lᵢ 完成，故约束 <code>t₀+i ≥ Lᵢ</code>，即 <code>t₀ ≥ Lᵢ−i</code>。
取 <code>t₀=maxᵢ(Lᵢ−i)={t0}</code>，则 makespan = t₀+190 = t₀+(N−2) = <strong>{tight}</strong>。</p>

<h3>为何 maxᵢ(Lᵢ−i)={t0}？</h3>
<p>瓶颈<strong>不是</strong>最远角点 (11,15)（L=166 排在 i=190，166−190=−24，约束很松），
而是「路径中等偏长、但排序靠前」的源：例如节点 <strong>{sn}</strong>（坐标 ({sx},{sy})）在排序中 i={si}，
Lᵢ={sL}，故 Lᵢ−i={sL}−{si}=<strong>{t0}</strong>。
含义：已有 {si} 个更短路径的源占用了 slot t₀…t₀+{si - 1}，该源若要在 slot t₀+{si} 完成，
必须整体把 pipeline 起点后移 t₀={t0} cycle，使 t₀+{si}≥{sL}。
同 slack={t0} 的还有 i=15（节点 7）、i=19（节点 8）等 L=30/34 的 x 轴同行源。</p>

<h3>闭式近似：(N−2)+max_path−mesh_diam+H+V={closed}</h3>
<table style="border-collapse:collapse;font-size:13px;margin:8px 0">
<tr><th style="border:1px solid #cbd5e1;padding:4px 8px">项</th>
<th style="border:1px solid #cbd5e1;padding:4px 8px">值</th>
<th style="border:1px solid #cbd5e1;padding:4px 8px">含义</th></tr>
<tr><td style="border:1px solid #cbd5e1;padding:4px 8px">N−2</td>
<td style="border:1px solid #cbd5e1;padding:4px 8px">190</td>
<td style="border:1px solid #cbd5e1;padding:4px 8px">191 个 root slot 的首尾索引差</td></tr>
<tr><td style="border:1px solid #cbd5e1;padding:4px 8px">max_path</td>
<td style="border:1px solid #cbd5e1;padding:4px 8px">{mp}</td>
<td style="border:1px solid #cbd5e1;padding:4px 8px">最远源 up+mesh+down 全路径延迟</td></tr>
<tr><td style="border:1px solid #cbd5e1;padding:4px 8px">mesh_diam</td>
<td style="border:1px solid #cbd5e1;padding:4px 8px">{md}</td>
<td style="border:1px solid #cbd5e1;padding:4px 8px">纯 mesh 直径 11×H+15×V</td></tr>
<tr><td style="border:1px solid #cbd5e1;padding:4px 8px">max_path−mesh_diam</td>
<td style="border:1px solid #cbd5e1;padding:4px 8px">{ramp2}</td>
<td style="border:1px solid #cbd5e1;padding:4px 8px">两端 ramp 各 1cy</td></tr>
<tr><td style="border:1px solid #cbd5e1;padding:4px 8px">H+V</td>
<td style="border:1px solid #cbd5e1;padding:4px 8px">{H_LAT + V_LAT}</td>
<td style="border:1px solid #cbd5e1;padding:4px 8px">mesh 流水线重叠的乐观修正（本拓扑少 1cy）</td></tr>
</table>
<p>代入 190+{ramp2}+{H_LAT + V_LAT}={closed}，比精确式少 1 cycle（闭式用 max_path 代替全部 Lᵢ 分布）。
<strong>period=(N−1)×M=191</strong> 是 root 带宽下界（稳态重复间隔），不是 makespan。</p>"""


def mesh_allgather():
    """Dimensional multi-tree: left = one source's X-then-Y fork tree; right = corner funnel."""
    w1, _ = svg_size(48)
    w = w1 * 2 + 24
    h = PAD * 2 + MY * CS + 72
    parts = [
        f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg">',
        svg_defs("ag0"),
        svg_defs("ag1"),
        '<text x="16" y="18" font-size="12" fill="#334155">'
        '双向维序多树 AllGather：左 = 单源 X-先-Y fork 树（router 复制）  右 = 叠加后最坏角节点漏斗</text>',
    ]
    sub = Mesh()

    # ---- Panel 0: one source's X-then-Y in-network multicast tree ----
    sdx, sdy = 6, 8
    s_demo = nid(sdx, sdy)
    ox = 8
    parts.append(f'<g transform="translate({ox},26)">')
    parts.append(f'<text x="{PAD}" y="0" font-size="10" font-weight="bold">'
                 f"源 ({sdx},{sdy})：行脊 →← (H) + 每列分叉 ↑↓ (V)，节点 fork 同时下 ramp+转发</text>")
    gparts, _, _ = draw_grid("ag0", sub, root=s_demo)
    for line in gparts[2:]:
        if "svg" not in line:
            parts.append(line)
    # row spine (bidirectional along row sdy)
    for x in range(sdx, MX - 1):
        parts.append(arrow_line(*pos(nid(x, sdy)), *pos(nid(x + 1, sdy)), "#0d9488", "ag0", 1.2))
    for x in range(sdx, 0, -1):
        parts.append(arrow_line(*pos(nid(x, sdy)), *pos(nid(x - 1, sdy)), "#0d9488", "ag0", 1.2))
    # column branches from every spine node, both directions
    for x in range(MX):
        for y in range(sdy, MY - 1):
            parts.append(arrow_line(*pos(nid(x, y)), *pos(nid(x, y + 1)), "#7c3aed", "ag0", 0.7))
        for y in range(sdy, 0, -1):
            parts.append(arrow_line(*pos(nid(x, y)), *pos(nid(x, y - 1)), "#7c3aed", "ag0", 0.7))
    parts.append("</g>")

    # ---- Panel 1: superposed funnel into the worst corner (0,0) ----
    ox = w1 + 16
    parts.append(f'<g transform="translate({ox},26)">')
    parts.append(f'<text x="{PAD}" y="0" font-size="10" font-weight="bold">'
                 "角节点 (0,0)：列0 漏斗 180 flit (V) + 行0 11 flit (H) → down-ramp 191</text>")
    gparts, _, _ = draw_grid("ag1", sub, root=nid(0, 0))
    for line in gparts[2:]:
        if "svg" not in line:
            parts.append(line)
    # column-0 vertical funnel toward (0,0)
    for y in range(MY - 1, 0, -1):
        parts.append(arrow_line(*pos(nid(0, y)), *pos(nid(0, y - 1)), "#dc2626", "ag1", 1.3))
    # row-0 horizontal toward (0,0)
    for x in range(MX - 1, 0, -1):
        parts.append(arrow_line(*pos(nid(x, 0)), *pos(nid(x - 1, 0)), "#0d9488", "ag1", 1.0))
    parts.append("</g>")

    parts.append(
        f'<text x="16" y="{h - 8}" font-size="10" fill="#64748b">'
        "全局 link-time calendar 贪心装填（每 link/down-ramp 每周期≤1 flit）→ 无冲突命中下界："
        "makespan = 205(M=1) / 769(M=4) / 12229(M=64)，eff=1.0</text></svg>"
    )
    return "\n".join(parts)


def allgather_description():
    return """<p><strong>最优方案不是 gather+broadcast。</strong>把数据汇经 root 再广播会串行化两阶段并制造 root 热点，
        下界被高估为 205+166=371。真实瓶颈是<strong>每个节点</strong>的单条 down-ramp 必须吞入 (N−1)M flit，
        故下界 = <code>max((N−1)M, bisection, 最坏角节点 gather)</code> = <strong>205</strong>(M=1)，
        约等于<strong>一次最坏 gather</strong>，而非 gather+broadcast。</p>
        <p>实现的最优算法是 <strong>双向维序多树</strong>（bidirectional dimensional multi-tree，<strong>无字面意义的 Hamiltonian 环</strong>，
        每维的「双向 line」即 mesh 上环的等价物）：每个源沿 <strong>X-先-Y</strong> 维序展开一棵组播树
        —— 行脊（H-link 双向）+ 每列分叉（V-link 双向）。转发是 <strong>router 内 fork</strong>：节点把到达的 flit
        复制一份下 down-ramp（eject 到 PE），另一份继续转发，<strong>中间节点从不 eject 后再 reinject</strong>，
        因此不付 10cy 的 PE/SRAM bounce。N 棵树叠加后由<strong>全局 link-time calendar</strong> 贪心装填，
        每条有向 link、每个 down-ramp 每 cycle≤1 flit，<strong>构造即无冲突</strong>。
        最坏角节点 (0,0) 的列0 漏斗承载 180M flit、down-ramp 吞 191M flit，恰是瓶颈。
        仿真（C++ 调度器 + <code>utils/sim_dim_multitree.py</code>）确认 makespan <strong>精确命中下界</strong>：
        205(M=1) / 769(M=4) / 3061(M=16) / 12229(M=64)，<strong>eff=1.0</strong>，优于 2D dimensional(235) 与 gather+broadcast(371)。
        <strong>但这组 eff=1.0 数依赖充足缓冲</strong>（见下）。详见 <a href="report.html">report.html</a> 的「AllGather 理论再分析」。</p>
        <h3 style="font-size:15px;margin:16px 0 8px">真实物理约束：零 eject 缓冲 + 有界网内等待</h3>
        <p><strong>无冲突</strong>指同一 cycle 同一资源最多 1 flit（<code>inject≤1</code> / <code>eject≤1</code>），不等于不排队。
        硬件上有两条硬约束：<strong>(1) down-ramp/eject 缓冲必须为 0</strong>——到达当 cycle 即被 PE 取走；
        <strong>(2) 每跳网内等待必须有界 W</strong>——无界滞留会与后续 flit <strong>踩踏</strong>（卡住的 flit 占住端口，后面流水 flit 撞上）。
        之前 makespan=205/eff=1.0 依赖角节点 <strong>50–87 flit 的 eject 队列</strong> + 端口无界滞留，正是被禁止的场景。</p>
        <p><strong>关键修正</strong>：零 eject 缓冲<strong>不抬高下界</strong>。down-ramp 的 release-packing 下界
        <strong>LB*=78(6×8)/205(12×16)</strong>，与带缓冲日历实测值<strong>完全相等</strong> → 205 即最优，
        且 E=0 的最优<strong>仍是 205</strong>（原理上去掉 eject 缓冲一个 cycle 都不用多花）。带宽项 192 因到达量化不可达。</p>
        <p>但<strong>目前贪心调度器还达不到 205</strong>（<code>utils/sched_no_eject_buffer.py</code>，E=0、有界 W）：</p>
        <table style="font-size:12px;border-collapse:collapse;width:100%;margin:8px 0">
        <tr style="background:#f1f5f9"><th>每跳网内等待上限 W</th><th>6×8 (LB*78)</th><th>12×16 (LB*205)</th></tr>
        <tr><td>0（完全刚性）</td><td>139 (1.78×)</td><td>579 (2.82×)</td></tr>
        <tr><td>8（现实工作点）</td><td>103 (1.32×)</td><td>330 (1.61×)</td></tr>
        <tr style="background:#ecfdf5"><td>∞（仅参考，违反有界等待）</td><td><strong>82 (1.05×)</strong></td><td><strong>274 (1.34×)</strong></td></tr>
        </table>
        <p style="font-size:12px;color:#475569">差距是<strong>调度器次优性</strong>（fork 树共享前缀耦合：一次网内等待同时推迟下游所有目的地），
        不是物理代价。完整分析见 <a href="report.html">report.html</a>「AllGather：无冲突、缓冲与 makespan 权衡」。</p>"""


def buffer_tradeoff_svg():
    """makespan vs bounded in-network wait W under zero eject buffer, 12×16."""
    return """
<svg width="480" height="210" viewBox="0 0 480 210" xmlns="http://www.w3.org/2000/svg">
  <text x="240" y="14" text-anchor="middle" font-size="11" fill="#334155">零 eject 缓冲：makespan vs 网内等待上限 W（12×16, M=1）</text>
  <line x1="50" y1="170" x2="455" y2="170" stroke="#94a3b8"/>
  <line x1="50" y1="28" x2="50" y2="170" stroke="#94a3b8"/>
  <!-- y: ms 600->170, 192->34 ; y=170-(ms-192)*136/408 -->
  <line x1="50" y1="166" x2="455" y2="166" stroke="#94a3b8" stroke-dasharray="2,3"/>
  <text x="457" y="170" font-size="8" fill="#94a3b8">192不可达</text>
  <line x1="50" y1="162" x2="455" y2="162" stroke="#059669" stroke-dasharray="4,3"/>
  <text x="457" y="156" font-size="8" fill="#059669">最优LB*205</text>
  <polyline fill="none" stroke="#2563eb" stroke-width="2"
    points="80,41 145,76 210,93 275,109 340,124"/>
  <circle cx="80" cy="41" r="3.5" fill="#2563eb"/><text x="80" y="34" text-anchor="middle" font-size="8">W0:579</text>
  <circle cx="145" cy="76" r="3.5" fill="#2563eb"/><text x="145" y="69" text-anchor="middle" font-size="8">1:475</text>
  <circle cx="210" cy="93" r="3.5" fill="#2563eb"/><text x="210" y="86" text-anchor="middle" font-size="8">2:424</text>
  <circle cx="275" cy="109" r="3.5" fill="#2563eb"/><text x="275" y="102" text-anchor="middle" font-size="8">4:376</text>
  <circle cx="340" cy="124" r="3.5" fill="#2563eb"/><text x="340" y="117" text-anchor="middle" font-size="8">8:330</text>
  <circle cx="410" cy="142" r="3.5" fill="#94a3b8"/><text x="410" y="135" text-anchor="middle" font-size="8" fill="#64748b">∞:274</text>
  <text x="250" y="200" text-anchor="middle" font-size="9" fill="#64748b">每跳网内等待上限 W</text>
</svg>"""


def mesh_allreduce():
    w1, _ = svg_size(48)
    w = w1 * 2 + 30
    h = PAD * 2 + MY * CS + 80
    parts = [
        f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg">',
        svg_defs("ar0"),
        svg_defs("ar1"),
        '<text x="20" y="18" font-size="12" fill="#334155">AllReduce：左 Reduce 汇聚 → 右 Broadcast 扩散</text>',
    ]
    sub = Mesh()
    for idx, (label, color, rev) in enumerate([
        ("Phase1 Reduce", "#dc2626", True),
        ("Phase2 Broadcast", "#2563eb", False),
    ]):
        ox = idx * (w1 + 10) + 10
        parts.append(f'<g transform="translate({ox},28)">')
        parts.append(f'<text x="{PAD}" y="0" font-size="11" font-weight="bold">{label}</text>')
        gparts, _, _ = draw_grid(f"ar{idx}", sub, root=ROOT_NODE)
        for line in gparts[2:]:
            if "svg" not in line:
                parts.append(line)
        edges = sub.tree_edges(ROOT_NODE)
        for a, b in edges:
            if rev:
                parts.append(arrow_line(*pos(b), *pos(a), color, f"ar{idx}", width=1.1))
            else:
                parts.append(arrow_line(*pos(a), *pos(b), color, f"ar{idx}", width=1.1))
        parts.append("</g>")
    parts.append(
        f'<text x="20" y="{h - 10}" font-size="10" fill="#64748b">'
        f"两阶段 calendar 首尾拼接；period=M</text></svg>"
    )
    return "\n".join(parts)


def mesh_alltoall():
    mesh = Mesh()
    uid = "a2a"
    parts, w, h = draw_grid(
        uid,
        mesh,
        root=None,
        title="AllToAll：左半→右半经竖切链路交换（紫=跨切 H-step），行内横向 + 列间纵向两阶段",
    )
    # bisection cut between x=5 and x=6
    cx = PAD + 6 * CS - CS / 2
    parts.append(
        f'<line x1="{cx:.1f}" y1="{PAD}" x2="{cx:.1f}" y2="{PAD + MY * CS}" '
        f'stroke="#9333ea" stroke-width="2" stroke-dasharray="6,4"/>'
    )
    parts.append(
        f'<text x="{cx + 4:.1f}" y="{PAD + 12}" font-size="9" fill="#9333ea">二分切面</text>'
    )
    # cross-cut arrows (representative rows)
    for y in range(0, MY, 2):
        u = nid(5, y)
        v = nid(6, y)
        x1, y1 = pos(u)
        x2, y2 = pos(v)
        parts.append(arrow_line(x1, y1, x2, y2, "#9333ea", uid, width=1.2))
        parts.append(arrow_line(x2, y2, x1, y1, "#c026d3", uid, width=0.9))
    # horizontal flow within rows (thin, sample rows)
    for y in range(0, MY, 4):
        for x in range(MX - 1):
            u, v = nid(x, y), nid(x + 1, y)
            x1, y1 = pos(u)
            x2, y2 = pos(v)
            parts.append(
                f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                f'stroke="#7c3aed" stroke-width="0.7" opacity="0.35" marker-end="url(#arr_{uid})" color="#7c3aed"/>'
            )
    parts.append(
        f'<text x="{PAD}" y="{h - 12}" font-size="10" fill="#64748b">'
        f"96×96 对 flit / 12 切链路 = 768×M；M=16 makespan=12224</text></svg>"
    )
    return "\n".join(parts)


def mesh_anytoany():
    mesh = Mesh()
    uid = "a2p"
    parts, w, h = draw_grid(
        uid,
        mesh,
        root=None,
        title="AnyToAny：预定义置换 P(i)（示例箭头）；每对 (i→P(i)) 走 Dijkstra 最短路径",
    )
    # fixed permutation sample (seed 42 style): show ~24 long arrows
    perm = list(range(N))
    import random

    rng = random.Random(42)
    for attempt in range(64):
        rng.shuffle(perm)
        if all(perm[i] != i for i in range(N)):
            break
    shown = 0
    for i in range(N):
        if shown >= 28:
            break
        j = perm[i]
        if coord(i) == coord(j):
            continue
        path = mesh.shortest_path(i, j)
        if len(path) < 2:
            continue
        # draw polyline
        pts = [pos(p) for p in path]
        d = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        hue = (i * 37) % 360
        parts.append(
            f'<path d="{d}" fill="none" stroke="hsl({hue},65%,45%)" stroke-width="0.9" '
            f'opacity="0.65" marker-end="url(#arr_{uid})" color="hsl({hue},65%,45%)"/>'
        )
        shown += 1
    parts.append(
        f'<text x="{PAD}" y="{h - 12}" font-size="10" fill="#64748b">'
        f"展示 28 条置换路径；全网 192 条同时 calendar 调度，bound≈768×M</text></svg>"
    )
    return "\n".join(parts)


def mesh_fault_node():
    dead = {0}
    mesh_h = Mesh()
    mesh_f = Mesh(dead_nodes=dead)
    new_root = 1
    w1, _ = svg_size(40)
    w = w1 * 2 + 24
    h = PAD * 2 + MY * CS + 72
    parts = [
        f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg">',
        svg_defs("fn0"),
        svg_defs("fn1"),
        '<text x="16" y="18" font-size="12" fill="#334155">节点故障 node_corner_1：节点0失效，root→1，broadcast 树在子图上重建</text>',
    ]
    for idx, (mesh, label, root, dead_m) in enumerate([
        (mesh_h, "healthy", 0, None),
        (mesh_f, "node0 故障", new_root, dead),
    ]):
        ox = idx * (w1 + 8) + 8
        parts.append(f'<g transform="translate({ox},26)">')
        parts.append(f'<text x="{PAD}" y="0" font-size="10" font-weight="bold">{label}</text>')
        gparts, _, _ = draw_grid(f"fn{idx}", mesh, root=root, dead_mark=dead_m)
        for line in gparts[2:]:
            if "svg" not in line:
                parts.append(line)
        edges = mesh.tree_edges(root)
        for a, b in edges:
            parts.append(arrow_line(*pos(a), *pos(b), "#2563eb", f"fn{idx}", width=1.0))
        if dead_m:
            x, y = pos(0)
            parts.append(
                f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="middle" font-size="14" fill="#dc2626">✕</text>'
            )
        parts.append("</g>")
    parts.append(
        f'<text x="16" y="{h - 8}" font-size="10" fill="#64748b">'
        f"离线重调度：剔除故障节点 → 重选 root → 在存活 mesh 上重建 latency-tree 与 calendar</text></svg>"
    )
    return "\n".join(parts)


def mesh_fault_link():
    dead_edge = {(0, 1)}
    mesh_h = Mesh()
    mesh_f = Mesh(dead_edges=dead_edge)
    w1, _ = svg_size(40)
    w = w1 * 2 + 24
    h = PAD * 2 + MY * CS + 88
    parts = [
        f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg">',
        svg_defs("fl0"),
        svg_defs("fl1"),
        '<text x="16" y="18" font-size="12" fill="#334155">链路故障 linkH_corner_1：边 0—1 不可用，0→1 改绕 0→12→1</text>',
    ]
    for idx, (mesh, label, show_detour) in enumerate([
        (mesh_h, "healthy: 0→1 直连 H", False),
        (mesh_f, "故障: 绕路经 V-link", True),
    ]):
        ox = idx * (w1 + 8) + 8
        parts.append(f'<g transform="translate({ox},26)">')
        parts.append(f'<text x="{PAD}" y="0" font-size="10" font-weight="bold">{label}</text>')
        gparts, _, _ = draw_grid(f"fl{idx}", mesh, root=0)
        for line in gparts[2:]:
            if "svg" not in line:
                parts.append(line)
        if idx == 0:
            parts.append(arrow_line(*pos(0), *pos(1), "#2563eb", f"fl{idx}", width=1.8))
        else:
            x1, y1 = pos(0)
            x2, y2 = pos(1)
            parts.append(
                f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                f'stroke="#dc2626" stroke-width="2.5" stroke-dasharray="3,2"/>'
            )
            parts.append(
                f'<text x="{(x1+x2)/2:.1f}" y="{(y1+y2)/2 - 4:.1f}" font-size="8" fill="#dc2626">✕</text>'
            )
            detour = mesh.shortest_path(0, 1)
            for j in range(len(detour) - 1):
                a, b = detour[j], detour[j + 1]
                parts.append(arrow_line(*pos(a), *pos(b), "#ea580c", f"fl{idx}", width=1.5))
        parts.append("</g>")
    parts.append(
        f'<text x="16" y="{h - 20}" font-size="10" fill="#64748b">'
        f"绕路 lat: 4 → 8+8=16；calendar 注入 slot 后移 → makespan 上升 (~+2% broadcast)</text></svg>"
    )
    return "\n".join(parts)


def load_metrics():
    rows = list(csv.DictReader(open(CSV, encoding="utf-8")))
    m = {}
    for r in rows:
        if r["fault_desc"] != "healthy":
            continue
        m[(r["collective"], int(r["msg_size"]))] = r
    return m


COLLECTIVES = [
    (
        "broadcast",
        "Broadcast 广播",
        mesh_broadcast,
        """<p>在 12×16 mesh 上，root 位于节点 0（左上角）。flit 从 root 的 up-ramp 注入后，沿<strong>_latency 最小生成树_</strong>的每条树边向子节点传播；router 在分叉处 <strong>fork</strong> 复制 flit。箭头方向即单 flit 在 mesh 链路（H=4cy / V=8cy）上的流动方向。最远节点 (11,15) 累计链路延迟 164cy，加 ramp 得 makespan=166。</p>""",
    ),
    (
        "reduce",
        "Reduce 归约",
        mesh_reduce,
        """<p>使用与 broadcast 相同的 latency 树，但箭头<strong>指向 root</strong>。参与归约的叶节点 PE 经 <strong>up-ramp</strong> 注入本地 flit，router 内 <strong>inline combine</strong>，沿 mesh 链路（H=4cy / V=8cy）逐级汇聚；最终结果经 root <strong>down-ramp</strong> 输出至 PE。M=1 makespan=166（1cy up + 164cy mesh + 1cy down，与 broadcast 对称）。</p>""",
    ),
    (
        "gather",
        "Gather 收集",
        mesh_gather_section,
        None,  # description from gather_description()
    ),
    (
        "allgather",
        "AllGather 全收集",
        mesh_allgather,
        None,  # description from allgather_description()
    ),
    (
        "allreduce",
        "AllReduce 全归约",
        mesh_allreduce,
        """<p>左：Reduce 阶段，红箭头由叶向 root 汇聚并在 router combine。右：Broadcast 阶段，蓝箭头由 root 向全网 fork。calendar 在 mesh 链路上为两阶段分别预分配时隙后拼接。</p>""",
    ),
    (
        "alltoall",
        "AllToAll 全交换",
        mesh_alltoall,
        """<p>虚线为<strong>水平二分切面</strong>（x=5|6）。紫色双向箭头表示跨切面的 flit 交换（瓶颈链路）；淡紫水平线为行内 X 维交换。全网每对节点互发 M flit，calendar 分 X/Y 两阶段填满切面带宽，makespan 下界 768×M。</p>""",
    ),
    (
        "anytoany",
        "AnyToAny 任意置换",
        mesh_anytoany,
        """<p>每条彩色折线为一条置换路径 i→P(i)（Dijkstra 最短路径，沿 mesh 水平/竖直链路转弯）。模式事先已知，calendar 为每条路径的每一跳预分配 send-time，全网并行、链路无冲突。</p>""",
    ),
]


def render():
    metrics = load_metrics()
    parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        "<title>Mesh2d 集合通信空间数据流</title>",
        "<style>",
        "body{font-family:'Segoe UI',Arial,sans-serif;margin:20px;color:#0f172a;line-height:1.65;max-width:980px;}",
        "h1,h2{color:#1e3a8a;} .card{background:#fff;border:1px solid #e2e8f0;padding:18px;margin:18px 0;border-radius:8px;}",
        ".mesh-wrap{overflow-x:auto;} svg{display:block;margin:8px auto;}",
        ".legend{font-size:12px;color:#475569;margin:8px 0;}",
        ".metric{color:#0369a1;font-weight:600;}",
        "a{color:#2563eb;}",
        "</style></head><body>",
        "<h1>12×16 Mesh2d 上的 Calendar 集合通信数据流</h1>",
        "<p>节点编号 <code>id = x + 12·y</code>，x∈[0,11]，y∈[0,15]。"
        "H-link 时延 4cy（水平），V-link 8cy（竖直）。"
        "R = root（节点 0）。"
        "<a href='report.html'>仿真报告</a></p>",
        '<div class="legend">'
        "图例：■ 浅蓝=普通节点 ■ 黄=root ■ 红叉=故障节点/链路 "
        "| 蓝箭头=broadcast | 红箭头=reduce | 橙箭头=gather</div>",
        "<div class='card'><h2>Mesh 坐标系</h2>",
        '<div class="mesh-wrap">',
        _mesh_coords_only(),
        "</div></div>",
    ]

    for name, title, fn, desc in COLLECTIVES:
        m1 = metrics.get((name, 1), {})
        note = ""
        if m1:
            note = f'<p class="metric">healthy M=1: makespan={m1["makespan"]}, period={m1["period"]}</p>'
        if desc is None and name == "gather":
            desc = gather_description()
        if desc is None and name == "allgather":
            desc = allgather_description()
        parts.extend([
            f"<div class='card'><h2>{html.escape(title)}</h2>",
            desc,
            '<div class="mesh-wrap">',
            fn(),
            "</div>",
        ])
        if name == "allgather":
            parts.append('<div class="mesh-wrap">')
            parts.append(buffer_tradeoff_svg())
            parts.append("</div>")
        parts.extend([
            note,
            "</div>",
        ])

    parts.extend([
        "<div class='card'><h2>故障下离线重调度 — Mesh 上的数据流变化</h2>",
        "<p>故障后在<strong>同一 mesh 坐标系</strong>上更新拓扑（删节点/删边），"
        "Dijkstra 重算路径，calendar 全链路时隙表 offline 重生成。</p>",
        '<div class="mesh-wrap">',
        mesh_fault_node(),
        "</div>",
        "<p><strong>节点故障</strong>：故障节点从 mesh 消失（✕）；若其为 root 则迁移至节点 1；"
        "broadcast 树在剩余 191 节点上重建，箭头拓扑整体改变。</p>",
        '<div class="mesh-wrap">',
        mesh_fault_link(),
        "</div>",
        "<p><strong>链路故障</strong>：左图 0→1 走单跳 H-link（lat=4）；"
        "右图该边断开（红虚线✕），0→1 改走 0→12→1 两跳 V-link（橙箭头，lat=16），"
        "calendar 将对应路径上的 send-time 整体后移。</p>",
        "</div></body></html>",
    ])

    OUT.write_text("\n".join(parts), encoding="utf-8")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    render()
