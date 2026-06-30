#!/usr/bin/env python3
"""Hybrid TDM + packet-switched router microarchitecture, area/power, heterogeneous NoC.

Output: results/report_hybrid_router.html
"""

import html
import math
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TECH_PATH = ROOT / "src" / "power" / "techfile.txt"
OUT_PATH = ROOT / "results" / "report_hybrid_router.html"

# 16x16 mesh anchor (plan)
MESH_X, MESH_Y = 16, 16
N = MESH_X * MESH_Y
PORTS = 5  # N/S/E/W + local ramp
H_LAT, V_LAT, RAMP_LAT = 4, 6, 1
CHANNEL_WIDTH = 128

# TDM: skid only; packet: BookSim defaults
TDM_NUM_VC, TDM_DEPTH = 1, 2
PKT_NUM_VC, PKT_DEPTH = 16, 8

# Calendar slot-table depth (16x16 allgather M=1 period = N-1)
SCHEDULE_PERIOD = N - 1  # 255 cycles
SLOT_ENTRY_BITS = int(math.ceil(math.log2(PORTS))) + 1  # port-select + valid

# Representative dynamic activity (15% avg port utilization)
ACTIVITY = 0.15


def load_tech(path):
    vals = {}
    for line in path.read_text().splitlines():
        line = line.split("//")[0].strip()
        if not line or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().rstrip(";")
        try:
            vals[k] = float(v) if "." in v else int(v)
        except ValueError:
            pass
    return vals


TECH = load_tech(TECH_PATH)

# Derived constants (mirror Power_Module constructor)
Cw = 2.0 * TECH["Cw_cpl"] + 2.0 * TECH["Cw_gnd"]
Ci = (1.0 + 2.0) * TECH["Cg_pwr"]
Co = (1.0 + 2.0) * TECH["Cd_pwr"]
Ci_delay = (1.0 + 2.0) * (TECH["Cg"] + TECH["Cgdl"])
Co_delay = (1.0 + 2.0) * TECH["Cd"]
Vdd = TECH["Vdd"]
R = TECH["R"]
FO4 = R * (3.0 * TECH["Cd"] + 12.0 * TECH["Cg"] + 12.0 * TECH["Cgdl"])
tCLK = 20.0 * FO4
fCLK = 1.0 / tCLK
MetalPitch = TECH["MetalPitch"]
CrossbarPitch = 2.0 * MetalPitch
ChannelPitch = 2.0 * MetalPitch
wire_length = TECH["wire_length"]
W = CHANNEL_WIDTH

_wire_cache = {}


def wire_optimize(L):
    if L in _wire_cache:
        return _wire_cache[L]
    best = (1e18, 1.0, 1.0, 1.0)
    for K in [x * 0.1 for x in range(10, 100)]:
        for Nw in range(1, 40):
            for M in range(1, 40):
                seg = L / (Nw * M)
                k0 = R * (Co_delay + Ci_delay)
                k1 = R / K * Cw + K * TECH["Rw"] * Ci_delay
                k2 = 0.5 * TECH["Rw"] * Cw
                Tw = k0 + k1 * seg + k2 * seg * seg
                alpha = 0.2
                pw = alpha * 64 * _power_repeated_wire(L, K, M, Nw) + _power_wire_dff(M, 64, alpha)
                metric = M ** 4 * pw
                if Nw * Tw < 0.8 * tCLK and metric < best[0]:
                    best = (metric, K, M, Nw)
    _wire_cache[L] = (best[1], best[2], best[3])
    return _wire_cache[L]


def _power_repeated_wire(L, K, M, N):
    segments = M * N
    Ca = K * (Ci + Co) + Cw * (L / segments)
    return 0.5 * Ca * Vdd * Vdd * fCLK * M * N


def _power_wire_leak(K, M, N):
    return K * 0.5 * (TECH["IoffN"] + 2.0 * TECH["IoffP"]) * Vdd * M * N


def _power_wire_clk(M, width):
    columns = TECH["H_DFQD1"] * MetalPitch / ChannelPitch
    clock_len = width * ChannelPitch
    Cclk = (1 + 5.0 / 16.0 * (1 + Co_delay / Ci_delay)) * (
        clock_len * Cw * columns + width * Ci_delay
    )
    return M * Cclk * Vdd * Vdd * fCLK


def _power_wire_dff(M, width, alpha):
    Cdin = 2 * 0.8 * (Ci + Co) + 2 * (2.0 / 3.0 * 0.8 * Co)
    Cclk = 2 * 0.8 * (Ci + Co) + 2 * (2.0 / 3.0 * 0.8 * TECH["Cg_pwr"])
    return M * alpha * width * (Cdin + Cclk) * Vdd * Vdd * fCLK


def area_crossbar(inputs, outputs):
    return (inputs * W * CrossbarPitch) * (outputs * W * CrossbarPitch)


def area_input_module(words):
    return (W * TECH["H_SRAM"]) * (words * TECH["W_SRAM"]) * MetalPitch ** 2


def area_output_module(outputs):
    return W * outputs * TECH["W_DFQD1"] * TECH["H_DFQD1"] * MetalPitch ** 2


def area_channel(K, Nw, M):
    Adff = M * TECH["W_DFQD1"] * TECH["H_DFQD1"]
    Ainv = M * Nw * (TECH["W_INVD2"] + 3 * K) * TECH["H_INVD2"]
    return W * (Adff + Ainv) * MetalPitch ** 2


def power_word_line(mem_w, mem_d):
    Ccell = 2 * (4.0 * TECH["LAMBDA"]) * TECH["Cg_pwr"] + 6 * MetalPitch * Cw
    Cwl = mem_w * Ccell
    Warray = 8 * MetalPitch + mem_d
    x = 1.0 + (5.0 / 16.0) * (1 + Co / Ci)
    Cpredecode = x * (Cw * Warray * Ci)
    Cdecode = x * Cwl
    Harray = 6 * mem_w * MetalPitch
    y = (1 + 0.25) * (1 + Co / Ci)
    Cprecharge = y * (Cw * Harray + 3 * W * Ci)
    Cwren = y * (Cw * Harray + 2 * W * Ci)
    return (Cprecharge + Cwren + 2 * Cpredecode + Cdecode) * Vdd * Vdd * fCLK


def power_mem_read(mem_d):
    Ccell = 4.0 * TECH["LAMBDA"] * TECH["Cd_pwr"] + 8 * MetalPitch * Cw
    return mem_d * Ccell * Vdd * Vdd * fCLK


def power_mem_write(mem_d):
    Ccell = 4.0 * TECH["LAMBDA"] * TECH["Cd_pwr"] + 8 * MetalPitch * Cw
    Cbl = mem_d * Ccell
    Ccc = 2 * (Co + Ci)
    return 0.5 * Ccc * Vdd * Vdd + Cbl * Vdd * Vdd * fCLK


def power_mem_leak(mem_d):
    return mem_d * TECH["IoffSRAM"] * Vdd


def power_crossbar(inputs, outputs, to=0, fr=0):
    Wxbar = W * outputs * CrossbarPitch
    Hxbar = W * inputs * CrossbarPitch
    CwIn, CwOut = Wxbar * Cw, Hxbar * Cw
    Cxi = (1.0 / 16.0) * CwOut
    Cxo = 4.0 * Cxi * (Co_delay / Ci_delay)
    Cti = (1.0 / 16.0) * CwIn
    Cto = 4.0 * Cti * (Co_delay / Ci_delay)
    CinputDriver = 5.0 / 16.0 * (1 + Co_delay / Ci_delay) * (0.5 * Cw * Wxbar + Cti)
    Cin = CinputDriver + CwIn + Cti + outputs * Cxi
    if to < outputs / 2:
        Cin -= 0.5 * CwIn + outputs / 2 * Cxi
    Cout = CwOut + Cto + inputs * Cxo
    if fr < inputs / 2:
        Cout -= 0.5 * CwOut + inputs / 2 * Cxo
    return 0.5 * (Cin + Cout) * Vdd * Vdd * fCLK


def power_crossbar_ctrl(inputs, outputs):
    Wxbar = W * outputs * CrossbarPitch
    Hxbar = W * inputs * CrossbarPitch
    CwIn = Wxbar * Cw
    Cti = (5.0 / 16.0) * CwIn
    Cctrl = W * Cti + (Wxbar + Hxbar) * Cw
    Cdrive = (5.0 / 16.0) * (1 + Co_delay / Ci_delay) * Cctrl
    return (Cdrive + Cctrl) * Vdd * Vdd * fCLK


def power_crossbar_leak(inputs, outputs):
    Wxbar = W * outputs * CrossbarPitch
    Hxbar = W * inputs * CrossbarPitch
    CwIn, CwOut = Wxbar * Cw, Hxbar * Cw
    Cxi = (1.0 / 16.0) * CwOut
    Cti = (1.0 / 16.0) * CwIn
    return (
        0.5
        * (TECH["IoffN"] + 2 * TECH["IoffP"])
        * W
        * (inputs * outputs * Cxi + inputs * Cti + outputs * Cti)
        / Ci
    )


def power_output_ctrl():
    Woutmod = W * ChannelPitch
    Cenable = (1 + 5.0 / 16.0) * (1.0 + Co / Ci) * (Woutmod * Cw + W * Ci)
    return Cenable * Vdd * Vdd * fCLK


def unit_gate_area():
    return W * TECH["H_ND2D1"] * TECH["W_ND2D1"] * MetalPitch ** 2


@dataclass
class RouterBreakdown:
    name: str
    crossbar_a: float = 0.0
    buffer_a: float = 0.0
    output_a: float = 0.0
    slot_a: float = 0.0
    alloc_a: float = 0.0
    mux_a: float = 0.0
    crossbar_dyn: float = 0.0
    crossbar_leak: float = 0.0
    buffer_dyn: float = 0.0
    buffer_leak: float = 0.0
    output_dyn: float = 0.0
    slot_dyn: float = 0.0
    alloc_dyn: float = 0.0
    mux_dyn: float = 0.0

    @property
    def total_area(self):
        return (
            self.crossbar_a
            + self.buffer_a
            + self.output_a
            + self.slot_a
            + self.alloc_a
            + self.mux_a
        )

    @property
    def dynamic_power(self):
        return (
            self.crossbar_dyn
            + self.buffer_dyn
            + self.output_dyn
            + self.slot_dyn
            + self.alloc_dyn
            + self.mux_dyn
        )

    @property
    def leakage_power(self):
        return self.crossbar_leak + self.buffer_leak


def _crossbar_power_bundle(alpha):
    px = power_crossbar(PORTS, PORTS)
    pc = power_crossbar_ctrl(PORTS, PORTS)
    pl = power_crossbar_leak(PORTS, PORTS)
    return alpha * W * px + alpha * pc, pl


def _buffer_power_bundle(depth, alpha):
    Pwl = power_word_line(W, depth)
    Prd = power_mem_read(depth) * W
    Pwr = power_mem_write(depth) * W
    Pleak = power_mem_leak(depth) * W
    dyn = alpha * (Pwl + Prd + Pwl + Pwr)
    return dyn, Pleak


def compute_tdm_router():
    r = RouterBreakdown("TDM / calendar")
    r.crossbar_a = area_crossbar(PORTS, PORTS)
    r.output_a = area_output_module(PORTS)
    # skid register per input port
    r.buffer_a = PORTS * area_input_module(TDM_DEPTH)
    # per-output-port slot table
    slot_unit = area_input_module(SCHEDULE_PERIOD) * (SLOT_ENTRY_BITS / W)
    r.slot_a = PORTS * slot_unit

    cd, cl = _crossbar_power_bundle(ACTIVITY)
    r.crossbar_dyn, r.crossbar_leak = cd, cl
    bd, bl = _buffer_power_bundle(TDM_DEPTH, ACTIVITY)
    r.buffer_dyn = PORTS * bd
    r.buffer_leak = PORTS * bl
    r.output_dyn = (
        ACTIVITY * W * power_crossbar(PORTS, PORTS)
        + PORTS * (_power_wire_clk(1, W) + ACTIVITY * _power_wire_dff(1, W, 1.0))
        + ACTIVITY * PORTS * power_output_ctrl()
    )
    slot_w = max(SLOT_ENTRY_BITS, 8)
    r.slot_dyn = PORTS * ACTIVITY * (
        power_word_line(slot_w, SCHEDULE_PERIOD)
        + power_mem_read(SCHEDULE_PERIOD) * slot_w
    )
    return r


def compute_packet_router():
    r = RouterBreakdown("Packet-switched")
    words = PKT_NUM_VC * PKT_DEPTH
    r.crossbar_a = area_crossbar(PORTS, PORTS)
    r.output_a = area_output_module(PORTS)
    r.buffer_a = PORTS * area_input_module(words)
    ug = unit_gate_area()
    r.alloc_a = 2 * PORTS * PORTS * PKT_NUM_VC * ug * 0.35

    cd, cl = _crossbar_power_bundle(ACTIVITY)
    r.crossbar_dyn, r.crossbar_leak = cd, cl
    bd, bl = _buffer_power_bundle(words, ACTIVITY)
    r.buffer_dyn = PORTS * bd
    r.buffer_leak = PORTS * bl
    r.output_dyn = (
        PORTS * _power_wire_clk(1, W)
        + ACTIVITY * PORTS * (_power_wire_dff(1, W, 1.0) + power_output_ctrl())
    )
    r.alloc_dyn = (
        ACTIVITY
        * 2
        * PORTS
        * PORTS
        * PKT_NUM_VC
        * 0.5
        * (Ci + Co)
        * Vdd
        * Vdd
        * fCLK
        * 1e-4
    )
    return r


def compute_hybrid_router():
    tdm = compute_tdm_router()
    pkt = compute_packet_router()
    ug = unit_gate_area()
    mux_a = PORTS * ug * 2.5
    grant_a = PORTS * ug * 3.0
    r = RouterBreakdown("Hybrid (TDM + packet)")
    r.crossbar_a = pkt.crossbar_a
    r.output_a = pkt.output_a
    r.buffer_a = pkt.buffer_a
    r.slot_a = tdm.slot_a
    r.alloc_a = pkt.alloc_a
    r.mux_a = mux_a + grant_a
    r.crossbar_dyn = pkt.crossbar_dyn
    r.crossbar_leak = pkt.crossbar_leak
    r.buffer_dyn = pkt.buffer_dyn
    r.buffer_leak = pkt.buffer_leak
    r.output_dyn = pkt.output_dyn
    r.slot_dyn = tdm.slot_dyn
    r.alloc_dyn = pkt.alloc_dyn
    r.mux_dyn = (
        ACTIVITY
        * PORTS
        * 8
        * 0.5
        * (Ci + Co)
        * Vdd
        * Vdd
        * fCLK
        * 1e-4
    )
    return r


def mesh_link_counts():
    h_links = MESH_X * (MESH_Y - 1)
    v_links = MESH_Y * (MESH_X - 1)
    undirected = h_links + v_links
    directed = 2 * undirected
    ramps = 2 * N
    return directed, ramps


def noc_channel_totals():
    directed, ramps = mesh_link_counts()
    h_links = MESH_X * (MESH_Y - 1)
    v_links = MESH_Y * (MESH_X - 1)
    h_len = H_LAT * wire_length
    v_len = V_LAT * wire_length
    Kh, Mh, Nh = wire_optimize(h_len)
    Kv, Mv, Nv = wire_optimize(v_len)
    a_h = area_channel(Kh, Nh, Mh)
    a_v = area_channel(Kv, Mv, Nv)
    area = h_links * 2 * a_h + v_links * 2 * a_v + ramps * area_channel(Kh, Nh, Mh)

    def ch_pwr(K, M, Nw, lat):
        L = lat * wire_length
        pw = _power_repeated_wire(L, K, M, Nw)
        clk = _power_wire_clk(M, W)
        dff = _power_wire_dff(M, W, ACTIVITY)
        leak = _power_wire_leak(K, M, Nw)
        return ACTIVITY * W * pw + clk + dff, leak * W

    dh, lh = ch_pwr(Kh, Mh, Nh, H_LAT)
    dv, lv = ch_pwr(Kv, Mv, Nv, V_LAT)
    dyn = h_links * 2 * dh + v_links * 2 * dv + ramps * dh
    leak = h_links * 2 * lh + v_links * 2 * lv + ramps * lh
    return {"area": area, "dynamic": dyn, "leakage": leak}


def noc_totals(router: RouterBreakdown, ch):
    return {
        "router_area": router.total_area * N,
        "router_dyn": router.dynamic_power * N,
        "router_leak": router.leakage_power * N,
        "channel_area": ch["area"],
        "channel_dyn": ch["dynamic"],
        "channel_leak": ch["leakage"],
    }


def esc(s):
    return html.escape(str(s))


def fmt_area(x):
    if x >= 1:
        return f"{x:.3f}"
    return f"{x:.4f}"


def fmt_pwr(x):
    return f"{x:.2f}"


def router_diagram_svg(variant):
    """Microarchitecture block diagram."""
    w, h = 520, 200
    boxes = {
        "tdm": [
            (30, 70, 80, 50, "#dbeafe", "Input\nskid x5"),
            (140, 55, 100, 80, "#fef3c7", "Slot-table\nSRAM x5"),
            (280, 70, 90, 50, "#dcfce7", "5x5\nCrossbar"),
            (410, 70, 80, 50, "#f3e8ff", "Output\nregs x5"),
        ],
        "packet": [
            (20, 60, 90, 70, "#dbeafe", "VC buffers\n16x8 x5"),
            (130, 65, 80, 60, "#fecaca", "VC+SW\nalloc"),
            (240, 70, 90, 50, "#dcfce7", "5x5\nCrossbar"),
            (360, 70, 80, 50, "#f3e8ff", "Output\nregs x5"),
        ],
        "hybrid": [
            (10, 30, 70, 45, "#dbeafe", "Skid\nx5"),
            (10, 90, 70, 45, "#bfdbfe", "VC buf\n16x8"),
            (95, 55, 55, 40, "#fde68a", "Mode\nmux"),
            (165, 40, 75, 55, "#fef3c7", "Slot\nSRAM"),
            (165, 110, 75, 55, "#fecaca", "Grant\narb"),
            (260, 70, 85, 50, "#dcfce7", "Crossbar"),
            (380, 70, 75, 50, "#f3e8ff", "Output"),
        ],
    }
    titles = {
        "tdm": "A. TDM / calendar router (conflict-free, zero alloc)",
        "packet": "B. Packet-switched router (stat-mux, bufferable)",
        "hybrid": "C. Hybrid router (calendar priority + packet fallback)",
    }
    arrows = {
        "tdm": [(110, 95, 140, 95), (240, 95, 280, 95), (370, 95, 410, 95)],
        "packet": [(110, 95, 130, 95), (210, 95, 240, 95), (330, 95, 360, 95)],
        "hybrid": [
            (80, 52, 95, 70), (80, 112, 95, 85), (150, 75, 165, 67),
            (150, 100, 165, 130), (240, 95, 260, 95), (345, 95, 380, 95),
        ],
    }
    parts = [
        f'<svg width="{w}" height="{h}" xmlns="http://www.w3.org/2000/svg">',
        f'<text x="{w/2:.0f}" y="18" text-anchor="middle" font-size="11" '
        f'font-weight="bold" fill="#1e3a8a">{esc(titles[variant])}</text>',
    ]
    for x, y, bw, bh, col, lab in boxes[variant]:
        parts.append(
            f'<rect x="{x}" y="{y}" width="{bw}" height="{bh}" rx="6" '
            f'fill="{col}" stroke="#64748b" stroke-width="1"/>'
        )
        for i, line in enumerate(lab.split("\n")):
            parts.append(
                f'<text x="{x+bw/2:.0f}" y="{y+bh/2-4+12*i}" text-anchor="middle" '
                f'font-size="9" fill="#334155">{esc(line)}</text>'
            )
    for x1, y1, x2, y2 in arrows[variant]:
        parts.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2-6}" y2="{y2}" stroke="#475569" '
            f'stroke-width="1.2" marker-end="url(#ah)"/>'
        )
    parts.append(
        '<defs><marker id="ah" markerWidth="7" markerHeight="7" refX="5" refY="3.5" '
        'orient="auto"><polygon points="0,0 7,3.5 0,7" fill="#475569"/></marker></defs>'
    )
    parts.append("</svg>")
    return "\n".join(parts)


def floorplan_svg():
    """16x16 heterogeneous placement: interior TDM, border packet."""
    cs = 22
    pad = 28
    w = pad * 2 + MESH_X * cs
    h = pad * 2 + MESH_Y * cs + 24
    parts = [
        f'<svg width="{w}" height="{h}" xmlns="http://www.w3.org/2000/svg">',
        f'<text x="{w/2:.0f}" y="16" text-anchor="middle" font-size="11" '
        f'font-weight="bold" fill="#334155">16x16 heterogeneous floorplan '
        f'(interior TDM backbone, border packet routers)</text>',
    ]
    tdm_n = 0
    for y in range(MESH_Y):
        for x in range(MESH_X):
            interior = 4 <= x <= 11 and 4 <= y <= 11
            col = "#2563eb" if interior else "#ea580c"
            px = pad + x * cs
            py = pad + 8 + (MESH_Y - 1 - y) * cs
            parts.append(
                f'<rect x="{px+1}" y="{py+1}" width="{cs-2}" height="{cs-2}" '
                f'fill="{col}" opacity="0.75" rx="2"/>'
            )
            if interior:
                tdm_n += 1
    # bisection guides
    bx = pad + 7.5 * cs
    by = pad + 8 + 7.5 * cs
    parts.append(
        f'<line x1="{bx:.0f}" y1="{pad+8}" x2="{bx:.0f}" y2="{h-pad}" '
        f'stroke="#9333ea" stroke-width="1" stroke-dasharray="4 3"/>'
    )
    parts.append(
        f'<line x1="{pad}" y1="{by:.0f}" x2="{w-pad}" y2="{by:.0f}" '
        f'stroke="#9333ea" stroke-width="1" stroke-dasharray="4 3"/>'
    )
    parts.append(
        f'<rect x="{pad+4*cs:.0f}" y="{pad+8+4*cs:.0f}" width="{8*cs:.0f}" '
        f'height="{8*cs:.0f}" fill="none" stroke="#1e40af" stroke-width="1.5" '
        f'stroke-dasharray="5 3"/>'
    )
    parts.append(
        f'<text x="{w-pad-4}" y="{h-6}" text-anchor="end" font-size="9" fill="#64748b">'
        f"TDM {tdm_n} nodes ({100*tdm_n/N:.0f}%) | packet {N-tdm_n} nodes</text>"
    )
    parts.append(
        f'<rect x="{pad}" y="{h-22}" width="10" height="10" fill="#2563eb"/>'
        f'<text x="{pad+14}" y="{h-13}" font-size="9">TDM-only (collective backbone)</text>'
        f'<rect x="{pad+160}" y="{h-22}" width="10" height="10" fill="#ea580c"/>'
        f'<text x="{pad+174}" y="{h-13}" font-size="9">Packet-only (any-to-any edges)</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts), tdm_n


def stacked_bar_svg(routers, ch, title):
    w = 560
    h = 300
    pad = {"l": 70, "r": 20, "t": 40, "b": 50}
    plot_w = w - pad["l"] - pad["r"]
    plot_h = h - pad["t"] - pad["b"]
    labels = [r.name.split("(")[0].strip() for r in routers]
    keys = [
        ("router_area", "#2563eb", "Router area"),
        ("channel_area", "#059669", "Channel area"),
    ]
    totals = []
    segs = {k: [] for k, _, _ in keys}
    for r in routers:
        t = noc_totals(r, ch)
        totals.append(t["router_area"] + t["channel_area"])
        segs["router_area"].append(t["router_area"])
        segs["channel_area"].append(t["channel_area"])
    ymax = max(totals) * 1.12

    def yv(v):
        return pad["t"] + plot_h * (1 - v / ymax)

    parts = [
        f'<svg width="{w}" height="{h}" xmlns="http://www.w3.org/2000/svg">',
        f'<text x="{w/2:.0f}" y="22" text-anchor="middle" font-size="12" '
        f'font-weight="bold" fill="#334155">{esc(title)}</text>',
        f'<line x1="{pad["l"]}" y1="{pad["t"]+plot_h}" x2="{w-pad["r"]}" '
        f'y2="{pad["t"]+plot_h}" stroke="#94a3b8"/>',
        f'<line x1="{pad["l"]}" y1="{pad["t"]}" x2="{pad["l"]}" '
        f'y2="{pad["t"]+plot_h}" stroke="#94a3b8"/>',
    ]
    for j, (_, col, lab) in enumerate(keys):
        lx = pad["l"] + j * 130
        parts.append(
            f'<rect x="{lx:.0f}" y="{h-16}" width="10" height="10" fill="{col}"/>'
            f'<text x="{lx+14:.0f}" y="{h-7}" font-size="9" fill="#475569">{esc(lab)}</text>'
        )
    bw = plot_w / len(routers) * 0.55
    for i, tot in enumerate(totals):
        gx = pad["l"] + (i + 0.5) * plot_w / len(routers)
        base = pad["t"] + plot_h
        stack = 0
        for key, col, _ in keys:
            val = segs[key][i]
            bh = plot_h * val / ymax
            y0 = base - stack - bh
            parts.append(
                f'<rect x="{gx-bw/2:.0f}" y="{y0:.1f}" width="{bw:.0f}" '
                f'height="{bh:.1f}" fill="{col}"/>'
            )
            stack += bh
        parts.append(
            f'<text x="{gx:.0f}" y="{yv(tot)-6:.0f}" text-anchor="middle" '
            f'font-size="9" font-weight="bold">{fmt_area(tot)}</text>'
        )
        parts.append(
            f'<text x="{gx:.0f}" y="{pad["t"]+plot_h+16}" text-anchor="middle" '
            f'font-size="9">{esc(labels[i][:12])}</text>'
        )
    parts.append(
        f'<text x="12" y="{pad["t"]+plot_h/2:.0f}" font-size="9" fill="#64748b" '
        f'transform="rotate(-90 12 {pad["t"]+plot_h/2:.0f})">mm²</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts)


def per_router_table(routers):
    rows = []
    for r in routers:
        rows.append(
            f"<tr><td>{esc(r.name)}</td>"
            f"<td>{fmt_area(r.crossbar_a)}</td>"
            f"<td>{fmt_area(r.buffer_a)}</td>"
            f"<td>{fmt_area(r.slot_a)}</td>"
            f"<td>{fmt_area(r.alloc_a)}</td>"
            f"<td>{fmt_area(r.mux_a)}</td>"
            f"<td>{fmt_area(r.output_a)}</td>"
            f"<td><strong>{fmt_area(r.total_area)}</strong></td>"
            f"<td>{fmt_pwr(r.dynamic_power)}</td>"
            f"<td>{fmt_pwr(r.leakage_power)}</td></tr>"
        )
    return "\n".join(rows)


def noc_summary_table(routers, ch):
    rows = []
    for r in routers:
        t = noc_totals(r, ch)
        ta = t["router_area"] + t["channel_area"]
        tp = t["router_dyn"] + t["router_leak"] + t["channel_dyn"] + t["channel_leak"]
        rows.append(
            f"<tr><td>{esc(r.name)}</td>"
            f"<td>{fmt_area(t['router_area'])}</td>"
            f"<td>{fmt_area(t['channel_area'])}</td>"
            f"<td><strong>{fmt_area(ta)}</strong></td>"
            f"<td>{fmt_pwr(t['router_dyn']+t['channel_dyn'])}</td>"
            f"<td>{fmt_pwr(t['router_leak']+t['channel_leak'])}</td>"
            f"<td><strong>{fmt_pwr(tp)}</strong></td></tr>"
        )
    return "\n".join(rows)


def hetero_sweep(tdm_r, pkt_r, hybrid_r, ch):
    fracs = [0.0, 0.25, 0.5, 0.64, 0.75, 1.0]
    rows = []
    sweep_vals = []
    for f in fracs:
        n_tdm = int(round(f * N))
        n_pkt = N - n_tdm
        ra = n_tdm * tdm_r.total_area + n_pkt * pkt_r.total_area
        ca = ch["area"]
        rd = n_tdm * tdm_r.dynamic_power + n_pkt * pkt_r.dynamic_power
        rl = n_tdm * tdm_r.leakage_power + n_pkt * pkt_r.leakage_power
        ta = ra + ca
        tp = rd + rl + ch["dynamic"] + ch["leakage"]
        sweep_vals.append((f, ta, tp))
        label = f"{f*100:.0f}% TDM"
        rows.append(
            f"<tr><td>{label}</td><td>{n_tdm}</td><td>{n_pkt}</td>"
            f"<td>{fmt_area(ra)}</td><td>{fmt_area(ta)}</td>"
            f"<td>{fmt_pwr(tp)}</td></tr>"
        )
    # add all-hybrid row
    ht = noc_totals(hybrid_r, ch)
    rows.append(
        f'<tr class="highlight"><td>100% Hybrid</td><td colspan="2">256 unified</td>'
        f'<td>{fmt_area(ht["router_area"])}</td>'
        f'<td><strong>{fmt_area(ht["router_area"]+ht["channel_area"])}</strong></td>'
        f'<td><strong>{fmt_pwr(ht["router_dyn"]+ht["router_leak"]+ht["channel_dyn"]+ht["channel_leak"])}</strong></td></tr>'
    )
    return "\n".join(rows), sweep_vals


def hetero_chart_svg(sweep_vals, all_tdm_a, all_pkt_a):
    w, h = 520, 240
    pad = {"l": 52, "r": 16, "t": 28, "b": 36}
    plot_w = w - pad["l"] - pad["r"]
    plot_h = h - pad["t"] - pad["b"]
    areas = [v[1] for v in sweep_vals]
    ymax = max(areas + [all_pkt_a]) * 1.08

    def ya(v):
        return pad["t"] + plot_h * (1 - v / ymax)

    pts = " ".join(
        f"{pad['l'] + i * plot_w / (len(sweep_vals)-1):.1f},{ya(v[1]):.1f}"
        for i, v in enumerate(sweep_vals)
    )
    hy = ya(all_tdm_a + (all_pkt_a - all_tdm_a) * 0.64)  # reference
    return f"""<svg width="{w}" height="{h}" xmlns="http://www.w3.org/2000/svg">
<text x="{w/2:.0f}" y="16" text-anchor="middle" font-size="11" fill="#334155">
Heterogeneous NoC total area vs TDM fraction f</text>
<line x1="{pad['l']}" y1="{pad['t']+plot_h}" x2="{w-pad['r']}" y2="{pad['t']+plot_h}" stroke="#94a3b8"/>
<line x1="{pad['l']}" y1="{pad['t']}" x2="{pad['l']}" y2="{pad['t']+plot_h}" stroke="#94a3b8"/>
<line x1="{pad['l']}" y1="{ya(all_tdm_a):.1f}" x2="{w-pad['r']}" y2="{ya(all_tdm_a):.1f}"
 stroke="#2563eb" stroke-dasharray="4 3"/>
<text x="{w-pad['r']-2}" y="{ya(all_tdm_a)-4:.1f}" font-size="8" fill="#2563eb" text-anchor="end">all-TDM</text>
<line x1="{pad['l']}" y1="{ya(all_pkt_a):.1f}" x2="{w-pad['r']}" y2="{ya(all_pkt_a):.1f}"
 stroke="#ea580c" stroke-dasharray="4 3"/>
<text x="{w-pad['r']-2}" y="{ya(all_pkt_a)-4:.1f}" font-size="8" fill="#ea580c" text-anchor="end">all-packet</text>
<polyline fill="none" stroke="#059669" stroke-width="2.5" points="{pts}"/>
<text x="{w/2:.0f}" y="{h-8}" text-anchor="middle" font-size="9" fill="#64748b">TDM router fraction f →</text>
</svg>"""


def build():
    tdm = compute_tdm_router()
    pkt = compute_packet_router()
    hybrid = compute_hybrid_router()
    routers = [tdm, pkt, hybrid]
    ch = noc_channel_totals()
    floorplan, tdm_placement_n = floorplan_svg()
    hetero_rows, sweep_vals = hetero_sweep(tdm, pkt, hybrid, ch)

    all_tdm = noc_totals(tdm, ch)
    all_pkt = noc_totals(pkt, ch)
    all_hybrid = noc_totals(hybrid, ch)
    tdm_total_a = all_tdm["router_area"] + all_tdm["channel_area"]
    pkt_total_a = all_pkt["router_area"] + all_pkt["channel_area"]
    hybrid_total_a = all_hybrid["router_area"] + all_hybrid["channel_area"]
    placement_a = (
        tdm_placement_n * tdm.total_area
        + (N - tdm_placement_n) * pkt.total_area
        + ch["area"]
    )
    overhead_hybrid = (hybrid.total_area / pkt.total_area - 1) * 100
    savings_tdm = (1 - tdm.total_area / pkt.total_area) * 100

    page = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Hybrid TDM + Packet Router — Microarchitecture &amp; Area/Power</title>
<script>
MathJax = {{ tex: {{ inlineMath: [['\\\\(','\\\\)']], displayMath: [['\\\\[','\\\\]']] }} }};
</script>
<script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js" async></script>
<style>
:root {{ --bg:#f7f8fb; --card:#fff; --text:#0f172a; --muted:#64748b; --accent:#1e3a8a; }}
* {{ box-sizing:border-box; }}
body {{ font-family:system-ui,-apple-system,"Segoe UI",sans-serif; margin:0; padding:28px 36px 64px;
        background:var(--bg); color:var(--text); line-height:1.62; max-width:1060px; }}
h1 {{ font-size:1.65rem; margin:0 0 6px; }}
h2 {{ font-size:1.18rem; margin:24px 0 10px; color:var(--accent);
      border-bottom:2px solid #e2e8f0; padding-bottom:5px; }}
h3 {{ font-size:1.02rem; margin:16px 0 8px; color:#334155; }}
.card {{ background:var(--card); border:1px solid #e2e8f0; border-radius:12px;
         padding:20px 24px; margin:16px 0; box-shadow:0 1px 2px rgba(0,0,0,.03); }}
.meta {{ color:var(--muted); font-size:.9rem; }}
table {{ border-collapse:collapse; width:100%; font-size:.88rem; margin:12px 0; }}
th, td {{ border:1px solid #e2e8f0; padding:7px 9px; text-align:center; }}
th {{ background:#f1f5f9; }}
td:first-child {{ text-align:left; }}
tr.highlight td {{ background:#ecfdf5; font-weight:600; }}
.note {{ color:var(--muted); font-size:.86rem; }}
code {{ font-family:"SF Mono",Menlo,Consolas,monospace; font-size:.86em;
        background:#eef2f7; padding:1px 5px; border-radius:4px; }}
.tag {{ display:inline-block; background:#dbeafe; color:#1e40af;
         padding:2px 8px; border-radius:4px; font-size:.82rem; margin-right:6px; }}
.diagram-grid {{ display:grid; grid-template-columns:1fr; gap:20px; }}
ul {{ margin:8px 0; padding-left:22px; }}
li {{ margin:5px 0; }}
.formula {{ background:#fbfdff; border:1px solid #e2e8f0; border-radius:8px;
            padding:12px 16px; margin:10px 0; overflow-x:auto; }}
</style></head><body>

<h1>Hybrid TDM + Packet-Switched Router</h1>
<p class="meta">16×16 mesh（N={N}），32nm ITRS（Orion 模型），W={W} bit/flit。
生成脚本 <code>utils/gen_hybrid_router_report.py</code>。</p>

<div class="card">
<h2>1. 设计目标</h2>
<p>NoC 需同时支持两类流量模式：</p>
<ul>
<li><strong>时隙表 TDM</strong>：离线 calendar 预配置，无冲突、无阻塞、无 router buffer（broadcast / allgather / reduce 等集合通信）。</li>
<li><strong>分组交换 packet</strong>：statistical multiplexing + VC buffer，支持 all-to-all / any-to-any 等未知目的地流量。</li>
</ul>
<p>本节给出三种 router 微架构（TDM-only / packet-only / hybrid），评估面积与功耗，并分析异构 NoC 可行性。</p>
</div>

<div class="card">
<h2>2. 模型参数</h2>
<p><span class="tag">拓扑</span>{MESH_X}×{MESH_Y} mesh，{PORTS}-port router（4 neighbor + local ramp）。</p>
<p><span class="tag">TDM</span>num_vc={TDM_NUM_VC}，skid depth={TDM_DEPTH}；slot-table depth={SCHEDULE_PERIOD} cy（allgather M=1 period）。</p>
<p><span class="tag">Packet</span>num_vc={PKT_NUM_VC}，depth={PKT_DEPTH}；iSLIP VC+SW allocator（面积估算）。</p>
<p><span class="tag">Tech</span>32nm，Vdd={Vdd}V，fCLK={fCLK/1e9:.2f}GHz；Orion formulas from
<code>src/power/power_module.cpp</code> + <code>src/power/techfile.txt</code>。</p>
<p><span class="tag">Activity</span>dynamic power at α={ACTIVITY} average port utilization.</p>
</div>

<div class="card">
<h2>3. 微架构设计</h2>
<div class="diagram-grid">
{figure_block("tdm")}
{figure_block("packet")}
{figure_block("hybrid")}
</div>

<h3>3.1 TDM / calendar router</h3>
<ul>
<li>每 output port 独立 <strong>slot-table SRAM</strong>（depth={SCHEDULE_PERIOD}）：每 cycle 读出 {{input-select, valid}}，直接配置 crossbar。</li>
<li>Input 仅 skid register（depth {TDM_DEPTH}），<strong>无 VC allocator / SW allocator</strong>。</li>
<li>离线 schedule 保证每 link ≤1 flit/cycle → 严格无冲突、无阻塞。</li>
</ul>

<h3>3.2 Packet-switched router</h3>
<ul>
<li>经典 input-queued VC router：{PKT_NUM_VC} VC × {PKT_DEPTH} deep SRAM / port。</li>
<li>VC allocator + SW allocator（iSLIP）+ credit-based flow control。</li>
<li>Buffer 吸收 contention → 支持任意目的地 all-to-all / any-to-any。</li>
</ul>

<h3>3.3 Hybrid router</h3>
<ul>
<li>共享 crossbar + output stage；并行 slot-table 与 VC buffer 路径。</li>
<li><strong>Mode mux</strong>：calendar window → bypass latch；packet window → buffered+allocated path。</li>
<li><strong>Grant arbiter</strong>：reserved slot 时 calendar grant 严格优先；unreserved slot 由 SW allocator 填充。</li>
<li>面积 ≈ packet + slot-table + mux/arb（相对 packet +{overhead_hybrid:.1f}%）。</li>
</ul>
</div>

<div class="card">
<h2>4. 单 Router 面积 / 功耗分解</h2>
<table>
<tr><th>Variant</th><th>Crossbar</th><th>Buffer</th><th>Slot-table</th>
<th>Allocator</th><th>Mux/Grant</th><th>Output</th><th>Total area</th>
<th>Dyn (W)</th><th>Leak (W)</th></tr>
{per_router_table(routers)}
</table>
<p class="note">TDM buffer 面积极小（skid only）；packet 面积 dominated by VC SRAM + allocators。
Hybrid = packet + slot-table + {fmt_area(hybrid.mux_a)} mm² mux overhead。</p>
</div>

<div class="card">
<h2>5. 全网（256 nodes + channels）面积 / 功耗</h2>
{stacked_bar_svg(routers, ch, "Full NoC area breakdown (256 routers + mesh channels)")}
<table>
<tr><th>Variant</th><th>Router area</th><th>Channel area</th><th>Total area</th>
<th>Dynamic (W)</th><th>Leakage (W)</th><th>Total power (W)</th></tr>
{noc_summary_table(routers, ch)}
</table>
<p class="note">Ordering: area(TDM) &lt; area(hybrid) &lt; area(packet)。
TDM saves <strong>{savings_tdm:.0f}%</strong> router area vs packet。
Channel area = {fmt_area(ch["area"])} mm²（{mesh_link_counts()[0]} directed mesh links + {mesh_link_counts()[1]} ramp links）。</p>
</div>

<div class="card">
<h2>6. 异构 NoC 分析</h2>
<h3>6.1 放置策略</h3>
<p>Interior 8×8（x,y ∈ [4,11]）部署 <strong>TDM-only</strong> router 作为 collective bisection backbone；
外围 border 部署 <strong>packet-only</strong> router 服务 PE 注入与非规则 any-to-any。</p>
<div style="overflow-x:auto">{floorplan}</div>

<h3>6.2 TDM 比例 sweep</h3>
{hetero_chart_svg(sweep_vals, tdm_total_a, pkt_total_a)}
<table>
<tr><th>Config</th><th>#TDM</th><th>#Packet</th><th>Router area</th><th>Total area</th><th>Total power</th></tr>
{hetero_rows}
<tr class="highlight"><td>Representative mixed (8×8 interior)</td><td>{tdm_placement_n}</td>
<td>{N-tdm_placement_n}</td><td colspan="3">Total area <strong>{fmt_area(placement_a)}</strong> mm²
（vs all-packet {fmt_area(pkt_total_a)} mm²，节省 {(1-placement_a/pkt_total_a)*100:.0f}%）</td></tr>
</table>

<h3>6.3 可行性与权衡</h3>
<ul>
<li><strong>Packet → TDM 边界</strong>：packet 流量进入 TDM 区域需 admission control（AFIFO 或 reserved transit slot），否则破坏无冲突保证。</li>
<li><strong>Any-to-any 穿越 TDM 岛</strong>：TDM-only router 无法服务未调度流 → 需空间分区或绕路（escape VC）。</li>
<li><strong>Collective-dominated</strong>：异构 NoC 显著节省面积/功耗（本例 interior 方案 −{(1-placement_a/pkt_total_a)*100:.0f}%），TDM backbone 精确命中 bisection 下界。</li>
<li><strong>Global unpredictable any-to-any</strong>：需 all-packet 或 all-hybrid；异构仅在有明确 traffic 分区时最优。</li>
<li><strong>Crossover</strong>：当 any-to-any 流量 &gt; ~30% 总带宽时，all-hybrid 或 all-packet 更优；collective-only 场景 all-TDM 最优。</li>
</ul>
</div>

<div class="card">
<h2>7. 设计建议</h2>
<table>
<tr><th>Workload</th><th>推荐 router</th><th>Total area</th><th>Notes</th></tr>
<tr><td>纯集合通信（calendar）</td><td>All-TDM</td><td>{fmt_area(tdm_total_a)} mm²</td>
<td>最低面积/功耗；不可服务 unscheduled traffic</td></tr>
<tr><td>纯 all-to-all / any-to-any</td><td>All-packet</td><td>{fmt_area(pkt_total_a)} mm²</td>
<td>最高灵活性；makespan 命中 bisection 下界（bufferable）</td></tr>
<tr><td>混合 workload</td><td>All-hybrid</td><td>{fmt_area(hybrid_total_a)} mm²</td>
<td>单芯片统一；+{overhead_hybrid:.0f}% vs packet</td></tr>
<tr class="highlight"><td>Collective + edge any-to-any</td><td>Heterogeneous</td>
<td>{fmt_area(placement_a)} mm²</td><td>Interior TDM + border packet；最优 PPA 折中</td></tr>
</table>
</div>

<div class="card">
<h2>8. 与现有 CollectivePowerModule 对照</h2>
<p>本报告 analytical model 基于同一 Orion 32nm 公式。
现有 C++ 仿真（<code>src/collective_power.cpp</code>）对 12×16 allgather M=16 给出
≈66 mm² / 2935 W（含 calendar table + fork/reduce）。
本报告 16×16 all-TDM router-only {fmt_area(all_tdm["router_area"])} mm² 量级一致
（channel 与 fork 单元另计）。</p>
</div>

</body></html>"""

    # fix figure_block placeholder - need to inject SVG before write
    page = page.replace("{figure_block(\"tdm\")}", router_diagram_svg("tdm"))
    page = page.replace("{figure_block(\"packet\")}", router_diagram_svg("packet"))
    page = page.replace("{figure_block(\"hybrid\")}", router_diagram_svg("hybrid"))

    OUT_PATH.write_text(page, encoding="utf-8")
    print(f"Wrote {OUT_PATH}")
    print(f"  TDM router:    {fmt_area(tdm.total_area)} mm2, {fmt_pwr(tdm.dynamic_power)} W dyn")
    print(f"  Packet router: {fmt_area(pkt.total_area)} mm2, {fmt_pwr(pkt.dynamic_power)} W dyn")
    print(f"  Hybrid router: {fmt_area(hybrid.total_area)} mm2, {fmt_pwr(hybrid.dynamic_power)} W dyn")
    assert tdm.total_area < hybrid.total_area <= pkt.total_area * 1.15
    assert hybrid.total_area >= pkt.total_area


def figure_block(variant):
    return router_diagram_svg(variant)


if __name__ == "__main__":
    build()
