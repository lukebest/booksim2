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
            "NOT gather+broadcast; per-node down-ramp floor; dimensional multi-tree hits LB exactly",
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
        f"not a gather plus a broadcast. The implemented optimum is the "
        f"<strong>bidirectional dimensional multi-tree</strong> "
        f"(X-then-Y in-network fork trees superposed under one global link-time calendar; "
        f"no literal ring): it <strong>hits the lower bound exactly</strong> — "
        f"makespan 205 (M=1) → 12229 (M=64), efficiency = 1.0, conflict-free.</li>",
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

<h3>实现的最优算法：双向维序多树（bidirectional dimensional multi-tree，命中下界）</h3>
<p>把行/列遍历<strong>重叠到单阶段</strong>：每个源沿 <strong>X-先-Y</strong> 维序展开一棵组播树
——行脊（H-link 双向）+ 每列分叉（V-link 双向）。<strong>注意没有字面意义的 Hamiltonian 环</strong>，
每个维度上的「双向 line」就是 mesh 上环的等价物。转发为 <strong>router 内 fork</strong>：
节点把到达 flit 复制一份下 down-ramp（eject 到 PE），另一份继续转发，
<strong>中间节点从不 eject 后再 reinject</strong>，因此不付 10cy 的 PE/SRAM bounce。</p>
<p>N 棵树叠加后用<strong>全局 link-time calendar</strong> 贪心装填：每条有向 link、每个 down-ramp
每周期 ≤1 flit，<strong>构造即无冲突</strong>。最坏角节点 (0,0) 的列0 漏斗承载 180M flit、
down-ramp 吞 191M flit，正是瓶颈。仿真（C++ <code>ScheduleAllGatherDimMultiTree</code> +
<code>utils/sim_dim_multitree.py</code>）确认 makespan <strong>精确命中下界</strong>：
<code>205 / 769 / 3061 / 12229</code>（M=1/4/16/64），<strong>eff=1.0</strong>。</p>

<h3>对比：2D dimensional allgather（次优，两阶段）</h3>
<p>另一可行算法是先沿行（Phase X，H-link）做行内 allgather（makespan {px}），
再沿列（Phase Y，V-link）做列内 allgather（makespan {py}），down-ramp 之和同为 (N−1)M。
但两阶段被串行化，总 makespan = {px}+{py} = <strong>{dim}</strong>(M=1)，比下界高 {dim - lb} cycle
（Phase X 的 fill 未被隐藏）。多树方案把这段 fill 重叠掉，故能从 {dim} 收紧到 {lb}。</p>

<h3>算法对比（12×16 mesh，M=1）</h3>
<table style="font-size:13px">
<tr><th>算法</th><th>带宽</th><th>延迟特性（本拓扑）</th><th>M=1 makespan</th></tr>
<tr><td>gather + broadcast（旧）</td><td>非最优（汇经 root）</td><td>两阶段串行 + root 热点</td><td>{old}</td></tr>
<tr><td>Ring（Hamiltonian 环）</td><td>最优</td><td>环周长 ~800cy 传播延迟主导，长宽比大时差</td><td>~800+</td></tr>
<tr><td>Recursive doubling</td><td>最优</td><td>log₂N≈8 步，但 mesh 上 partner 距离逐步增大→长线/拥塞；192 非 2 的幂</td><td>较差</td></tr>
<tr><td>2D dimensional</td><td>最优</td><td>仅相邻链路，但两阶段 fill 不重叠</td><td>{dim}</td></tr>
<tr><td><strong>双向维序多树</strong></td><td><strong>最优</strong></td><td><strong>单阶段重叠 + in-network fork + 全局 calendar</strong></td><td><strong>{lb}</strong></td></tr>
</table>
<p>下界 = <strong>{lb}</strong>(M=1)，双向维序多树 <strong>无冲突地精确命中</strong>（eff=1.0），
为本拓扑上 allgather 的实用最优实现。</p>
</div>"""


def allgather_buffer_section():
    """Buffer vs makespan under realistic constraints (sched_no_eject_buffer.py)."""
    return """
<div class="card"><h2>AllGather：无冲突、缓冲与 makespan 权衡（真实物理约束）</h2>

<h3>「无冲突」≠「无缓冲」</h3>
<p>Calendar 保证的是<strong>同一 cycle、同一资源</strong>上最多 1 个 flit：
每条<strong>有向 link</strong> 每 cycle 最多 1 次注入（<code>inject≤1</code>），
每个 router 的 <strong>down-ramp</strong> 每 cycle 最多 1 次 eject（<code>eject≤1</code>）。
这与「flit 从不排队」是两回事——若不加约束，争用会被<strong>暂存进缓冲</strong>（输出端口 FIFO 或 down-ramp 入口队列）后再错开放出。</p>

<h3>两条硬性物理约束</h3>
<p>真实硬件上这两类缓冲<strong>都不能随意存在</strong>：</p>
<ol>
<li><strong>down-ramp / eject 缓冲必须为 0</strong>：flit 到达目的地当 cycle 必须被 PE 取走，
不允许在 down-ramp 入口排队。形式上 <code>eject_cycle == arrive_cycle</code>，down-ramp 仅在该 cycle 占用。</li>
<li><strong>每跳网内等待必须有界（W 有限）</strong>：flit 在输出端口最多滞留 W cycle。
<strong>无上限滞留会与后续 flit 踩踏</strong>——被卡住的 flit 占住端口/链路，后面 wormhole 流水的 flit 会撞上它。</li>
</ol>
<p>注意：<strong>205 本身仍是最优 makespan</strong>（见下「真实下界」），它没有问题；有问题的是<strong>实现它的那个贪心日历</strong>
——它靠角节点 <strong>50–87 flit 的 eject 队列</strong> + 输出端口<strong>无界滞留</strong>来达到 205，而这正是上面禁止的踩踏场景。
所以问题不是「205 不可达」，而是「在 E=0 且 W 有界下，我们目前的调度器还达不到 205」。下面给出满足约束后的真实结果。</p>

<h3>三类「同时到达」场景（区分冲突与合法并发）</h3>
<ol>
<li><strong>左右邻居同时到达 router</strong>（不同输入端口）——合法，<code>arrive</code> 可 &gt;1，不是冲突。</li>
<li><strong>两者都要 fork 到同一输出端口</strong>——真争用；靠<strong>错开 send 周期</strong>化解（该 lane 全程 <code>inject≤1</code>），
而非靠无界排队。</li>
<li><strong>同一 link 上多个 flit 同时在飞</strong>（<code>inFlight&gt;1</code>）——wormhole 流水线，非阻塞；
各 flit 在不同 cycle 注入、沿 link 错开飞行。</li>
</ol>

<h3>真实下界：release-time packing（关键修正）</h3>
<p>每个节点单条 down-ramp 必须吞 (N−1)M flit、每 cycle 1 个，给出<strong>带宽项</strong>
<code>(N−1)M+ramp</code> = 48(6×8)/192(12×16)。但这<strong>不可达</strong>：down-ramp 不能在 flit 物理到达前 eject，
而到达时刻被 mesh 距离<strong>量化</strong>——源 s 到节点 d 最早到达 <code>ramp + dist(s,d)</code>（injection 只能推迟、不能提前）。
于是每个节点的 down-ramp 是一个<strong>带 release-time 的单机排程</strong>（N−1 个单位作业按 release 升序贪心装填），
其 makespan 取所有节点的最大 = <strong>release-packing 下界 LB*</strong>。</p>
<p>实测（<code>utils/sched_zero_eject_v2.py</code> 的 <code>packing_lb</code>）：
<strong>LB* = 78（6×8）/ 205（12×16）</strong>，瓶颈都在最远角节点 (0,0)。
<strong>这恰好等于带缓冲日历的 makespan（78/205）</strong>——两者相等说明 <strong>205 就是真正的最优 makespan</strong>
（下界 = 可达值），且<strong>零 eject 缓冲并不抬高这个下界</strong>：原理上去掉 eject 缓冲<strong>不需要多花一个 cycle</strong>。
零 eject 缓冲只要求这 (N−1) 次 eject 落在互异 cycle 且恰在到达当 cycle，靠<strong>有界网内等待</strong>把到达抹平到 ≤1/cycle 即可。</p>

<h3>满足约束的排程（<code>utils/sched_no_eject_buffer.py</code>）</h3>
<p>调度器强制 <strong>eject 缓冲 E=0</strong>（到达即取走），并用<strong>每跳 ≤W 的网内等待</strong>来错开最后一跳的到达 cycle，
使下行端口无需排队；若 W 内找不到可行时隙，则把整条源树的 <strong>injection 整体后移</strong>
（争用退回<strong>源 PE 发射队列</strong>，这不属于网内/eject 缓冲）。全程 <code>inject≤1</code>、<code>eject≤1</code>、E=0、每跳等待 ≤W。</p>

<p><strong>6×8（最优 LB*=78，带宽项 48 不可达）</strong></p>
<table style="font-size:13px">
<tr><th>每跳网内等待上限 W</th><th>makespan</th><th>vs LB* 78</th></tr>
<tr><td>0（完全刚性）</td><td>139</td><td>1.78×</td></tr>
<tr><td>1</td><td>123</td><td>1.58×</td></tr>
<tr><td>2</td><td>110</td><td>1.41×</td></tr>
<tr><td>4</td><td>108</td><td>1.38×</td></tr>
<tr><td>8</td><td>103</td><td>1.32×</td></tr>
<tr><td>∞（仅参考，违反有界等待）</td><td>82</td><td>1.05×</td></tr>
</table>

<p><strong>12×16（最优 LB*=205，带宽项 192 不可达）</strong></p>
<table style="font-size:13px">
<tr><th>每跳网内等待上限 W</th><th>makespan</th><th>vs LB* 205</th></tr>
<tr><td>0（完全刚性）</td><td>579</td><td>2.82×</td></tr>
<tr><td>1</td><td>475</td><td>2.32×</td></tr>
<tr><td>2</td><td>424</td><td>2.07×</td></tr>
<tr><td>4</td><td>376</td><td>1.83×</td></tr>
<tr><td>8</td><td>330</td><td>1.61×</td></tr>
<tr><td>∞（仅参考，违反有界等待）</td><td>274</td><td>1.34×</td></tr>
</table>

<p><strong>权衡曲线（12×16 makespan vs 每跳网内等待上限 W，E=0）</strong></p>
<svg width="520" height="230" viewBox="0 0 520 230" xmlns="http://www.w3.org/2000/svg" style="max-width:100%">
  <text x="260" y="16" text-anchor="middle" font-size="12" fill="#334155">零 eject 缓冲下 makespan vs 网内等待上限 W（12×16, M=1）</text>
  <line x1="60" y1="180" x2="490" y2="180" stroke="#94a3b8" stroke-width="1"/>
  <line x1="60" y1="30" x2="60" y2="180" stroke="#94a3b8" stroke-width="1"/>
  <!-- y scale: ms 600->180, 192->40 ; y = 180 - (ms-192)*140/408 -->
  <line x1="60" y1="176" x2="490" y2="176" stroke="#94a3b8" stroke-width="1" stroke-dasharray="2,3"/>
  <text x="492" y="180" font-size="9" fill="#94a3b8">带宽项 192(不可达)</text>
  <line x1="60" y1="172" x2="490" y2="172" stroke="#059669" stroke-width="1.3" stroke-dasharray="4,3"/>
  <text x="492" y="166" font-size="9" fill="#059669">最优 LB* 205</text>
  <polyline fill="none" stroke="#2563eb" stroke-width="2.5"
    points="90,47 160,80 230,96 300,111 370,126"/>
  <circle cx="90" cy="47" r="4" fill="#2563eb"/><text x="90" y="40" text-anchor="middle" font-size="9">W=0:579</text>
  <circle cx="160" cy="80" r="4" fill="#2563eb"/><text x="160" y="73" text-anchor="middle" font-size="9">1:475</text>
  <circle cx="230" cy="96" r="4" fill="#2563eb"/><text x="230" y="89" text-anchor="middle" font-size="9">2:424</text>
  <circle cx="300" cy="111" r="4" fill="#2563eb"/><text x="300" y="104" text-anchor="middle" font-size="9">4:376</text>
  <circle cx="370" cy="126" r="4" fill="#2563eb"/><text x="370" y="119" text-anchor="middle" font-size="9">8:330</text>
  <circle cx="445" cy="144" r="4" fill="#94a3b8"/><text x="445" y="137" text-anchor="middle" font-size="9" fill="#64748b">∞:274</text>
  <text x="275" y="212" text-anchor="middle" font-size="10" fill="#64748b">每跳网内等待上限 W（→ 越大网内缓冲越多） →</text>
  <text x="18" y="105" text-anchor="middle" font-size="10" fill="#64748b" transform="rotate(-90 18 105)">makespan</text>
</svg>
<p style="font-size:12px;color:#64748b">注：曲线由贪心启发式产生（<code>sched_no_eject_buffer.py</code>），是<strong>可达上界</strong>而非最优；
最优 LB*=205（绿线）才是 E=0 下真正的下界，曲线与它的差距是<strong>调度器次优性</strong>（见结论），不是物理代价。
带宽项 192 因到达量化不可达，仅作参照。</p>

<h3>结论（含关键修正）</h3>
<ul>
<li><strong>205 是真正的最优 makespan</strong>：release-packing 下界 LB* = 205（12×16）/ 78（6×8），
与带缓冲日历的实测值<strong>完全相等</strong> → 下界 = 可达值 = 最优。带宽项 192 因到达量化<strong>不可达</strong>。</li>
<li><strong>零 eject 缓冲不抬高下界</strong>（重要修正）：LB* 的推导本身就假设到达即 eject、无队列，
所以 E=0 的最优<strong>仍是 205</strong>——原理上<strong>去掉 eject 缓冲一个 cycle 都不用多花</strong>。
之前「零 eject 缓冲根本上更贵」的说法是错的。</li>
<li><strong>eject 缓冲可以严格为 0</strong>且调度仍<strong>构造即无冲突</strong>（所有 W 下均可行）。</li>
<li>但<strong>目前的贪心调度器还达不到 205</strong>：E=0、W=∞ 时最好只到 <strong>274（1.34×）</strong>，W=8 为 330。
差距是<strong>调度器次优性</strong>，根因是 <strong>fork 树的共享前缀耦合</strong>——共享链路上的一次网内等待会
<strong>同时</strong>推迟其下游所有目的地的到达，无法独立地把每个节点的 down-ramp packing 到 LB*。
试过的「瓶颈对齐」「按到角距离升序」等排序反而更差（382/343），<strong>far-from-center 优先</strong>仍是最好的贪心序。</li>
<li><strong>205 那组（eff=1.0）的旧日历</strong>用 50–87 flit 的 eject 队列 + 端口无界滞留（踩踏）来达到 205；
这是<strong>实现方式</strong>的问题，不是 205 本身——存在合法的 E=0 调度同样以 205 为最优，只是需要近最优 packing（开放问题，疑似需 ILP/flow）。</li>
<li>相关脚本：<code>utils/sched_zero_eject_v2.py</code>（release-packing 下界 + 瓶颈对齐尝试）、
<code>utils/sched_no_eject_buffer.py</code>（E=0 + 有界 W 主结果 274/330）、
<code>utils/sched_zero_buffer.py</code>（W=0 完全刚性 139/579）、
<code>utils/sched_buffer_sweep.py</code>（允许 eject 排队的对照）。</li>
</ul>
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
    sections.append(allgather_buffer_section())
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
