#!/usr/bin/env python3
"""HTML tables for the uniform-λ all-to-all sweep (healthy XY / Super-turn)."""
from __future__ import annotations

import html
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JSON_PATH = ROOT / "results" / "pg_a2a_lambda.json"
HTML_PATH = ROOT / "results" / "report_pg_a2a_lambda.html"
MAIN_HTML = ROOT / "results" / "report_pg_alltoall_8x6.html"


def _esc(s) -> str:
    return html.escape(str(s))


def _fmt(x, nd=2):
    if x is None:
        return "—"
    if isinstance(x, bool):
        return "yes" if x else "no"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def _table_cell(h: dict) -> str:
    dest = h.get("table_bits_per_router")
    if h.get("scheme") == "xy" or h.get("table_combo_bits") == 0 and h.get("n_live") == 48 and h.get("num_vc") == 1:
        # healthy XY: combinational, 0 SRAM bits
        if dest is not None and h.get("table_combo_bits") == 0 and h.get("n_live") == 48:
            dest_s = "0（组合 XY）"
        elif dest is None:
            dest_s = "不可行"
        else:
            dest_s = str(dest)
    elif dest is None:
        dest_s = "不可行"
    else:
        dest_s = str(dest)
    src_a = h.get("table_src_aware_bits_max")
    src_s = f"{src_a}" if src_a is not None else "—"
    return dest_s, src_s, str(h.get("sr_header_bits", "—"))


def buffer_derivation_html(data: dict) -> str:
    """10.1.1 — per-port buffer depth, with the DES evidence behind it."""
    bm = data.get("buffer_model") or {}
    ev = data.get("buf_depth_evidence") or {}
    hw_xy = data.get("hw_all_good") or {}
    hw_st = data.get("hw_super_turn_healthy") or {}
    m = data.get("meta") or {}
    hlat, vlat = m.get("H", 7), m.get("V", 9)
    ramp_bw = m.get("ramp_bw", 2)
    q_mesh = bm.get("q_mesh", 19)
    q_local = bm.get("q_local", 3)
    q_h = bm.get("q_h_min", 15)
    q_v = bm.get("q_v_min", 19)
    per_vc = bm.get("flits_per_vc", 79)
    per_vc_old = bm.get("flits_per_vc_5port", 95)

    occ_rows = []
    for r in ev.get("rows") or []:
        o = r.get("hw_occ_uniform") or {}
        occ_rows.append(
            f"<tr><td>{r['lam']:.2f}</td>"
            f"<td>{o.get('local', '—')}</td><td>{o.get('h', '—')}</td>"
            f"<td>{o.get('v', '—')}</td>"
            f"<td>{'相同' if r.get('B_identical_to_A') else '不同'}</td>"
            f"<td>{'相同' if r.get('C_identical_to_A') else '不同'}</td></tr>")
    occ_tbl = ("".join(occ_rows) or
               "<tr><td colspan='6'>无证据行（--quick）</td></tr>")

    ev_rows = []
    for r in ev.get("rows") or []:
        a = r.get("A_5port_uniform") or {}
        c = r.get("C_per_link_mesh") or {}
        ev_rows.append(
            f"<tr><td>{r['lam']:.2f}</td>"
            f"<td>{_fmt(a.get('mean_lat'))}</td><td>{a.get('max_lat', '—')}</td>"
            f"<td>{_fmt(a.get('bw_eff_flits_per_cy'), 4)}</td>"
            f"<td>{_fmt(c.get('mean_lat'))}</td><td>{c.get('max_lat', '—')}</td>"
            f"<td>{_fmt(c.get('bw_eff_flits_per_cy'), 4)}</td></tr>")
    ev_tbl = ("".join(ev_rows) or
              "<tr><td colspan='7'>无证据行（--quick）</td></tr>")

    return f"""
<h3>10.1.1 缓冲深度：4 个 mesh 端口 + 1 个 ramp 端口（不是 5 个一样深）</h3>
<p class="note">旧模型写成「5 端口 × VC × Q」，把<b>下 ramp 的本地端口</b>也按
Q={q_mesh} 计，这是错的。输入缓冲的深度只由<b>喂给它的那条通道的 credit 回环</b>
决定：上游 t 拍发出 → t+L 落地 → 最快 t+L 走 → credit 在 t+2L 回到上游，
所以要让这条链路每拍都能发，深度需要 <b>2L+1</b>。四个 mesh 端口各自挂在一条
router→router 链路上，本地端口挂的却是同一个 tile 内的 NI ramp，根本不在
credit 回环里——放不下的 flit 只是继续待在 NI 自己的队列里，不会卡住任何链路。</p>
<table>
<thead><tr><th class="l">输入端口</th><th class="l">被谁喂</th><th>L (cy)</th>
<th>2L+1 下界</th><th>本报告计入</th><th class="l">理由</th></tr></thead>
<tbody>
<tr><td class="l">N / S（2 个）</td><td class="l">纵向链路</td><td>{vlat}</td>
<td>{q_v}</td><td><b>{q_mesh}</b></td>
<td class="l">credit 回环 2V+1，最慢链路，决定统一深度</td></tr>
<tr><td class="l">E / W（2 个）</td><td class="l">横向链路</td><td>{hlat}</td>
<td>{q_h}</td><td><b>{q_mesh}</b></td>
<td class="l">吞吐只要 2H+1={q_h}，但实测尾时延变差，故与 N/S 取齐</td></tr>
<tr><td class="l">本地（1 个，上 ramp）</td><td class="l">同 tile 的 NI ramp</td>
<td>—</td><td>—</td><td><b>{q_local}</b></td>
<td class="l">不在 credit 回环里：RAMP_BW={ramp_bw} 一拍的突发 + 1 个流水槽</td></tr>
<tr><td class="l">下 ramp（弹出）</td><td class="l">—</td><td>—</td><td>—</td>
<td><b>0</b></td>
<td class="l">弹出的 flit 直接从 mesh 输入端口以 {ramp_bw} flit/cy 出网，
不再单独排一级缓冲</td></tr>
</tbody></table>
<p class="note">于是<b>每 VC 每 router = 4×{q_mesh} + {q_local}
= {per_vc} flit</b>（旧模型 5×{q_mesh} = {per_vc_old} flit，多算
{per_vc_old - per_vc} flit）。
健康 XY（1 VC）= <b>{hw_xy.get('buffer_slots_per_router')} flit</b>，
Super-turn（硅上 2 VC）= <b>{hw_st.get('buffer_slots_per_router')} flit</b>
（旧模型分别是 {hw_xy.get('buffer_slots_per_router_5port')} /
{hw_st.get('buffer_slots_per_router_5port')}）。1 flit = {m.get('flit_bits', 512)} bit。</p>

<h4>实测占用高水位（统一 Q={q_mesh} 时各类端口真正用到多深）</h4>
<p class="note">配置 A = 5 端口统一 {q_mesh}（旧模型）；
B = 4 mesh × {q_mesh} + 本地 × {q_local}（本报告计入）；
C = N/S × {q_v} + E/W × {q_h} + 本地 × {q_local}（再按链路收紧）。
「与 A 相同」= 平均/最长/p99 时延、有效带宽、交付包数<b>逐项完全相等</b>。</p>
<table>
<thead><tr><th>λ</th><th>本地高水位</th><th>E/W 高水位</th><th>N/S 高水位</th>
<th>B 与 A</th><th>C 与 A</th></tr></thead>
<tbody>{occ_tbl}</tbody></table>
<p class="note">两个结论：<br/>
① <b>本地端口白占</b>。它在高载下确实会涨到 {q_mesh}，但那段积压和留在 NI 队列里
完全等价——B 与 A 每一项指标都<b>一模一样</b>，所以第 5 个深端口从来没干活。
（唯一会翻的是 <code>stable</code> 标志，因为它盯的是 NI 队列增长，
积压换了个地方显形；在膝点 λ=0.38 会由 True 翻 False，时延/带宽不变。）<br/>
② <b>E/W 不能收到 {q_h}</b>。实测 E/W 高水位顶到 {q_mesh}，比 N/S 还满：
8 列比 6 行长，XY 先走 X，横向链路载更重。收紧到 2H+1={q_h} 吞吐不掉，
但尾时延变差，所以四个 mesh 端口统一按 {q_mesh} 计。</p>
<table>
<thead><tr><th>λ</th><th>A 平均</th><th>A 最长</th><th>A 带宽</th>
<th>C 平均</th><th>C 最长</th><th>C 带宽</th></tr></thead>
<tbody>{ev_tbl}</tbody></table>
<p class="note">证据脚本：<code>dse_pg_a2a_lambda.buf_depth_evidence()</code>
（健康 XY，warmup={ev.get('warmup', '—')}，measure={ev.get('measure', '—')}），
原始数据在 JSON 的 <code>buf_depth_evidence</code>。</p>
"""


def _sr_field_rows(br: dict) -> str:
    """Render the SR header fields; the JSON keeps English, the page is 中文."""
    hmax = br.get("hmax", 12)
    zh = {
        "hop_count": (f"要能表示「还剩 0…{hmax} 跳」共 {hmax + 1} 个取值，"
                      f"⌈log₂{hmax + 1}⌉ = {br.get('len_bits')} bit"),
        "dir": (f"每跳 1 个 2 bit 方向码（E/W/N/S），按最坏路径 "
                f"H<sub>max</sub> = {hmax} 跳开定长字段：2×{hmax}"),
        "vc_sel": ("包锁定在哪一层 Glass–Ni 转向模型上（硅上 2 层，1 bit）"
                   if br.get("vc_bit") else "只有 1 条 VC，无需选择位"),
    }
    out = []
    for f in br.get("fields") or []:
        name = f["field"]
        key = "dir" if name.startswith("dir") else name
        out.append(f"<tr><td class='l'><code>{_esc(name)}</code></td>"
                   f"<td>{f['bits']}</td>"
                   f"<td class='l'>{zh.get(key, _esc(f['why']))}</td></tr>")
    return "".join(out)


def sr_header_derivation_html(data: dict) -> str:
    """10.1.2 — where 28 bit / 29 bit comes from, field by field."""
    br = data.get("sr_header_breakdown") or {}
    xy = br.get("xy") or {}
    st = br.get("super_turn") or {}
    if not xy:
        return ""
    hmax = xy.get("hmax", 12)
    fl = xy.get("flit_bits", 512)
    return f"""
<h3>10.1.2 源路由头 {xy.get('total_bits')} bit / {st.get('total_bits', '—')} bit 是怎么来的</h3>
<p class="note">源路由 = 不查表：源节点把<b>整条路径</b>压进包头，
每个 router 只做「弹出头部 2 bit → 照它转发 → 跳数减一」。
所以头的宽度由<b>最长路径</b>决定，而不是由节点数决定。
公式 <code>{_esc(xy.get('formula', ''))}</code>。</p>
<table>
<thead><tr><th class="l">字段</th><th>bit</th><th class="l">为什么是这么多</th></tr></thead>
<tbody>
{_sr_field_rows(xy)}
<tr><td class="l"><b>健康 XY 合计</b></td><td><b>{xy.get('total_bits')}</b></td>
<td class="l">1 VC，无 VC 选择位</td></tr>
<tr><td class="l"><b>Super-turn 合计</b></td><td><b>{st.get('total_bits', '—')}</b></td>
<td class="l">同样的路径、同样的 H<sub>max</sub>，只多 1 bit
<code>vc_sel</code>（硅上 2 层 Glass–Ni，包在源端锁定一层）</td></tr>
</tbody></table>
<p class="note">三个关键点：<br/>
① <b>每跳 2 bit</b>：一个 router 的可选输出只有 E/W/N/S 四个方向，
2 bit 正好编码；「弹出」不用编码，跳数减到 0 就是到站。<br/>
② <b>为什么是 H<sub>max</sub>={hmax} 而不是平均跳数</b>：头是定长字段，
必须按<b>最坏路径</b>开宽度。健康 8×6 的曼哈顿直径 = (8−1)+(6−1) = {hmax}，
所以 2×{hmax} = {xy.get('dir_bits')} bit 的方向串。<br/>
③ <b><code>hop_count</code> {xy.get('len_bits')} bit</b>：要能表示 0…{hmax}
共 {hmax + 1} 个取值，⌈log₂{hmax + 1}⌉ = {xy.get('len_bits')} bit。
没有它，router 无法区分「方向串还剩几个有效」和「后面是填充位」。</p>
<p class="note">开销占比：{xy.get('total_bits')} / {fl} bit flit =
<b>{100 * (xy.get('frac_of_flit') or 0):.2f}%</b>
（Super-turn {st.get('total_bits', '—')} / {fl} =
{100 * (st.get('frac_of_flit') or 0):.2f}%）。两者都<b>塞得进同一个 flit 的头部</b>，
不额外产生一个 flit，所以源路由在本设计里不增加线上流量——
它换掉的是每个 router 里的路由表。</p>
"""


def dest_table_derivation_html(data: dict) -> str:
    """10.1.3 — where the 94-bit destination table comes from."""
    br = (data.get("dest_table_breakdown") or {}).get("xy") or {}
    st = (data.get("dest_table_breakdown") or {}).get("super_turn") or {}
    hw_sum = data.get("hw_partial_summary") or {}
    hw_pg = data.get("hw_partial_per_scenario") or []
    if not br:
        return ""
    n_live = br.get("n_live", 48)
    n_ent = br.get("n_entries", 47)
    eb = br.get("entry_bits", 2)
    tot = br.get("total_bits", 94)
    return f"""
<h3>10.1.3 目的路由表 {tot} bit 是怎么来的</h3>
<p class="note">这是<b>每个 router 一份</b>的表，回答的问题只有一个：
「包要去 d，我该往哪个方向送？」公式
<code>{_esc(br.get('formula', ''))}</code> = {n_ent}×{eb} = <b>{tot} bit</b>。</p>
<table>
<thead><tr><th class="l">项</th><th>值</th><th class="l">为什么</th></tr></thead>
<tbody>
<tr><td class="l">表项数</td><td>{n_ent}</td>
<td class="l">每个<b>其它</b>存活节点一行：A−1 = {n_live}−1 = {n_ent}。
「目的就是我」那一行不用存，它是弹出，由 <code>dest == my_id</code> 直接判出</td></tr>
<tr><td class="l">每项宽度</td><td>{eb} bit</td>
<td class="l">一行只需要说出<b>一个输出方向</b>：mesh 只有 E/W/N/S 四个，
{eb} bit 正好编完</td></tr>
<tr><td class="l">目的 id 本身</td><td>0 bit</td>
<td class="l">表是<b>用目的 id 直接索引</b>的（第 d 行就是去 d），
不是 CAM，所以行里不存 key</td></tr>
<tr><td class="l">VC 字段</td><td>0 bit</td>
<td class="l">Super-turn 虽然按 2 VC 计缓冲，但那一层是<b>源端选定、随包携带</b>的，
路由器不需要为它再存一位，所以 2 VC 下每行仍是 {eb} bit</td></tr>
<tr><td class="l"><b>合计</b></td><td><b>{tot} bit</b></td>
<td class="l">≈ {tot / 8:.1f} B，比一个 flit（{data.get('meta', {}).get('flit_bits', 512)} bit）还小</td></tr>
</tbody></table>
<p class="note">和源路由的分工：目的表把 {tot} bit 压在<b>每个 router</b> 里
（全网 {n_live} 个 router 共 {n_live * tot} bit ≈ {n_live * tot / 8 / 1024:.1f} KiB），
换来包头 0 bit；源路由反过来，router 里 0 bit，包头
{(data.get('sr_header_breakdown') or {}).get('xy', {}).get('total_bits', '—')} bit。</p>
<p class="note"><b>什么时候目的表不够用。</b>只按目的查表的前提是
「同一个 router 上，去同一个 d 的所有包下一跳唯一」。健康 XY 满足
（冲突数 {br.get('conflicts')}）。故障残图上 Super-turn 常常不满足：
同一个 (router, d) 会因为来源不同而必须走不同方向，才能既绕开故障
又不违反转向模型。本目录 {len(hw_pg)} 个故障场景里只有
{hw_sum.get('n_dest_only_ok')} 个仍然 dest-only 可行；其余要么升级成
<b>源感知表</b>（键变成 (src,dst)，见 10.1 表里的「源感知表 bit（最大）」），
要么退回<b>源路由</b>（每 router 0 bit，代价是包头那
{(data.get('sr_header_breakdown') or {}).get('super_turn', {}).get('total_bits', '—')} bit）。</p>
"""


def setup_html(data: dict) -> str:
    """Checkable simulation setup — first thing on the standalone report."""
    m = data.get("meta") or {}
    t0 = m.get("max_zero_latency", 98)
    wire = m.get("max_manhattan_wire", 94)
    hops = m.get("max_manhattan_hops", 12)
    lams = m.get("lams") or []
    lams_ag = m.get("lams_all_good") or lams
    lams_pg = m.get("lams_partial") or lams
    lam_ag_s = ", ".join(f"{x:.2f}" for x in lams_ag)
    lam_pg_s = ", ".join(f"{x:.2f}" for x in lams_pg)
    return f"""
<h2>仿真 Setup（请先核对这些假设）</h2>
<table>
<thead><tr><th class="l">项</th><th class="l">取值</th></tr></thead>
<tbody>
<tr><td class="l">几何</td><td class="l">{m.get('mx', 8)}×{m.get('my', 6)} mesh，
N=48，曼哈顿最大 hops = (MX−1)+(MY−1) = {hops}</td></tr>
<tr><td class="l">链路时延</td><td class="l">同行 H={m.get('H', 7)} cy，
同列 V={m.get('V', 9)} cy（<code>link_lat</code>）</td></tr>
<tr><td class="l">NI</td><td class="l">RAMP={m.get('ramp', 2)} cy（注入 / 弹出各一次），
RAMP_BW={m.get('ramp_bw', 2)} flit/cy</td></tr>
<tr><td class="l">包长</td><td class="l">m=1 flit，flit={m.get('flit_bits', 512)} bit</td></tr>
<tr><td class="l">缓冲</td><td class="l">IQ，mesh 输入端口 Q={m.get('Q', 19)} = 2·V+1
（credit 回环），credit 初值 = Q，每输出每周期 1 flit。
本地 ramp 输入端口不在 credit 回环里，计
{(data.get('buffer_model') or {}).get('q_local', 3)} flit，
成本口径见 10.1.1（DES 仍按统一 Q={m.get('Q', 19)} 跑，已验证与之等价）</td></tr>
<tr><td class="l">流量</td><td class="l">每个存活计算节点每周期以概率 λ 产生 1 个包；
目的在其余存活节点上均匀（各源 λ 相同）</td></tr>
<tr><td class="l">λ 网格</td><td class="l">all-good XY：0.10–0.35 步进 0.05，
之后 0.36–0.50 步进 0.01：<code>{lam_ag_s}</code>
（共 {len(lams_ag)} 点）。<br/>
partial Super-turn：0.10–0.25 步进 0.05，
膝点 0.30–0.40 步进 0.01：<code>{lam_pg_s}</code>
（共 {len(lams_pg)} 点）。</td></tr>
<tr><td class="l">时间窗</td><td class="l">warmup={m.get('warmup')} cy，
measure={m.get('measure')} cy，测量结束后排空（不再注入），
只统计 warmup ≤ t_gen &lt; warmup+measure 的包</td></tr>
<tr><td class="l">时延</td><td class="l"><code>t_eject − t_gen</code>；
弹出时刻 = 到达目的 switch + RAMP</td></tr>
<tr><td class="l">T<sub>0</sub> = max_zero_latency</td>
<td class="l">全图最长曼哈顿（对角）的零负载时延：
<code>(MX−1)·H + (MY−1)·V + 2·RAMP + (m−1)</code>
= 7·{m.get('H', 7)} + 5·{m.get('V', 9)} + 2·{m.get('ramp', 2)}
= <b>{wire} + {2 * int(m.get('ramp', 2))} = {t0} cy</b>。
与主报告 §8 <code>lat_lb</code> 同口径。孤立 DES 对角包实测 97 cy（少 1 拍注入对齐）。
表中「最长/T<sub>0</sub>」= 该 λ 的 max packet latency / {t0}。
全体场景共用这一几何 T<sub>0</sub>（不随故障缩小）。</td></tr>
<tr><td class="l">有效带宽</td><td class="l"><b>全网</b>交付 flit 数 / measure 周期，
单位 <b>flit/cy</b>（整个 8×6，不是每节点）。
旁列 bit/cy = ×512。accepted 才是每节点 flit/cy（m=1）。</td></tr>
<tr><td class="l">热点 / 对分</td><td class="l">理论对分 = 健康 8×6 中线 X 割
（x=3|4）单向 <b>6 flit/cy</b>。
稳态对分 = 测量窗内实际穿过该割的单向吞吐（取 L→R / R→L 较忙一侧）。
<b>热点带宽利用率 = 稳态对分 / 理论对分 × 100%</b>，上限 100%。</td></tr>
<tr><td class="l">稳定</td><td class="l">该 (场景, λ) 是否仍处于开环稳态：
accepted/λ ≥ 0.95（几乎吃下全部注入）且源队列斜率 &lt; 0.002
且未 backlog 熔断。表里 <b>k/14</b> = 14 个故障场景中有 k 个仍稳定，
不是 2 条 VC。例如 λ=0.30 的 2/14 表示只有 2 个故障图还没饱和。</td></tr>
<tr><td class="l">H<sub>max</sub></td><td class="l">该路由表里所有 (src,dst) 路径的
<b>最大跳数</b>（边数）。源路由头 = ⌈log<sub>2</sub>(H<sub>max</sub>+1)⌉
+ 2·H<sub>max</sub> + [VC 选择 1 bit]。健康 8×6 曼哈顿直径 = 12。</td></tr>
<tr><td class="l">all-good</td><td class="l">健康 mesh，路由 XY，1 VC，seed=0</td></tr>
<tr><td class="l">partial-good</td><td class="l">M0s Super-turn（Glass–Ni turn-model，硬顶 2 VC）；
故障 ≤2 死 router + ≤4 无向链路，router–链路不重叠；
每个 (n<sub>R</sub>, n<sub>L</sub>) 格子 1 样本（seed=0），
共 {m.get('n_partial_scenarios')} 场景</td></tr>
<tr><td class="l">DES</td><td class="l"><code>utils/dse_pg_a2a_lambda.py</code>
PathMeshSteady：残图 credit IQ、包锁定 VC</td></tr>
</tbody></table>
<p class="note">原始 JSON：<code>results/pg_a2a_lambda.json</code>。</p>
"""


def lambda_section_html(data: dict | None = None) -> str:
    if data is None:
        if not JSON_PATH.exists():
            return ("<h2>10. 均匀注入率 all-to-all（健康 / ≤2R+≤4L Super-turn）</h2>"
                    "<p class='note'>尚无 <code>results/pg_a2a_lambda.json</code>。"
                    "运行 <code>python3 utils/dse_pg_a2a_lambda.py</code>。</p>")
        data = json.loads(JSON_PATH.read_text())
    meta = data["meta"]
    hw_xy = data["hw_all_good"]
    hw_st_h = data.get("hw_super_turn_healthy") or {}
    hw_pg = data.get("hw_partial_per_scenario") or []
    hw_sum = data.get("hw_partial_summary") or {}
    ag = data.get("summary_all_good") or []
    pg = data.get("summary_partial") or []
    rows = data.get("rows") or []
    bm = data.get("buffer_model") or {}
    lams_ag = set(round(x, 2) for x in (meta.get("lams_all_good") or meta.get("lams") or []))
    lams_pg = set(round(x, 2) for x in (meta.get("lams_partial") or meta.get("lams") or []))

    def rows_table(tag: str, title: str) -> str:
        allow = lams_ag if tag == "all_good" else lams_pg
        sel = [r for r in rows if r["tag"] == tag
               and (not allow or round(r["lam"], 2) in allow)]
        if not sel:
            return f"<p class='note'>{_esc(title)}：无行</p>"
        # group by scenario
        scenes = []
        seen = set()
        for r in sel:
            if r["scenario"] not in seen:
                seen.add(r["scenario"])
                scenes.append(r["scenario"])
        # wide table: one block per scenario
        chunks = []
        for sc in scenes:
            rs = [r for r in sel if r["scenario"] == sc]
            rs.sort(key=lambda x: x["lam"])
            r0 = rs[0]
            t0 = r0.get("max_zero_latency") or meta.get("max_zero_latency")
            head = (
                f"<h4>{_esc(sc)} · {_esc(r0.get('scheme'))} · "
                f"A={r0.get('A')} · VC={r0.get('num_vc')} · "
                f"mode={_esc(r0.get('turn_mode') or '—')} · "
                f"sac={r0.get('n_sacrificed', 0)}</h4>"
                "<table><thead><tr>"
                "<th>λ</th><th>平均时延 (cy)</th><th>最长时延 (cy)</th>"
                f"<th>最长 / T<sub>0</sub>"
                f"{f' (T<sub>0</sub>={t0})' if t0 else ''}</th>"
                "<th>有效带宽<div class='sub'>全网 flit/cy</div></th>"
                "<th>有效带宽<div class='sub'>全网 bit/cy</div></th>"
                "<th>热点利用率<div class='sub'>稳态对分/理论 %</div></th>"
                "<th>accepted<div class='sub'>flit/节点/cy</div></th>"
                "<th>稳定</th><th>样本包数</th>"
                "</tr></thead><tbody>"
            )
            body = []
            for r in rs:
                stab = "yes" if r.get("stable") else "no"
                cls = "" if r.get("stable") else " class='bad'"
                body.append(
                    f"<tr{cls}><td>{r['lam']:.2f}</td>"
                    f"<td>{_fmt(r.get('mean_lat'))}</td>"
                    f"<td>{_fmt(r.get('max_lat'), 0)}</td>"
                    f"<td>{_fmt(r.get('max_lat_over_t0'), 3)}</td>"
                    f"<td>{_fmt(r.get('bw_eff_flits_per_cy'), 4)}</td>"
                    f"<td>{_fmt(r.get('bw_eff_bits_per_cy'), 1)}</td>"
                    f"<td>{_fmt(r.get('hotspot_util'), 1)}%</td>"
                    f"<td>{_fmt(r.get('accepted_per_node'), 5)}</td>"
                    f"<td>{stab}</td>"
                    f"<td>{r.get('n_samples')}</td></tr>"
                )
            chunks.append(head + "".join(body) + "</tbody></table>")
        return f"<h3>{_esc(title)}</h3>" + "".join(chunks)

    def summary_table(rows_s, caption) -> str:
        if not rows_s:
            return ""
        has_worst = "mean_lat_worst" in rows_s[0]
        if has_worst:
            hdr = ("<th>λ</th><th>n</th><th>平均时延 中位</th><th>平均时延 最差</th>"
                   "<th>最长时延 中位</th><th>最长时延 最差</th>"
                   "<th>最长/T<sub>0</sub> 中位</th><th>最长/T<sub>0</sub> 最差</th>"
                   "<th>有效带宽 中位<div class='sub'>全网 flit/cy</div></th>"
                   "<th>有效带宽 最差<div class='sub'>全网 flit/cy</div></th>"
                   "<th>热点利用率 中位<div class='sub'>稳态对分/理论 %</div></th>"
                   "<th>热点利用率 最差<div class='sub'>14 场景最小 %</div></th>"
                   "<th>accepted 中位<div class='sub'>flit/节点/cy</div></th>"
                   "<th>稳定<div class='sub'>场景数</div></th>")
        else:
            hdr = ("<th>λ</th><th>平均时延</th><th>最长时延</th>"
                   "<th>最长/T<sub>0</sub></th>"
                   "<th>有效带宽 (flit/cy)</th>"
                   "<th>热点利用率 %</th><th>accepted</th><th>稳定</th>")
        body = []
        for s in rows_s:
            if has_worst:
                body.append(
                    f"<tr><td>{s['lam']:.2f}</td><td>{s['n']}</td>"
                    f"<td>{_fmt(s.get('mean_lat_med'))}</td>"
                    f"<td>{_fmt(s.get('mean_lat_worst'))}</td>"
                    f"<td>{_fmt(s.get('max_lat_med'), 0)}</td>"
                    f"<td>{_fmt(s.get('max_lat_worst'), 0)}</td>"
                    f"<td>{_fmt(s.get('max_over_t0_med'), 3)}</td>"
                    f"<td>{_fmt(s.get('max_over_t0_worst'), 3)}</td>"
                    f"<td>{_fmt(s.get('bw_eff_med'), 4)}</td>"
                    f"<td>{_fmt(s.get('bw_eff_worst'), 4)}</td>"
                    f"<td>{_fmt(s.get('hotspot_util_med'), 1)}%</td>"
                    f"<td>{_fmt(s.get('hotspot_util_worst'), 1)}%</td>"
                    f"<td>{_fmt(s.get('accepted_med'), 5)}</td>"
                    f"<td>{s.get('n_stable')}/{s.get('n')}</td></tr>"
                )
            else:
                body.append(
                    f"<tr><td>{s['lam']:.2f}</td>"
                    f"<td>{_fmt(s.get('mean_lat_med'))}</td>"
                    f"<td>{_fmt(s.get('max_lat_med'), 0)}</td>"
                    f"<td>{_fmt(s.get('max_over_t0_med'), 3)}</td>"
                    f"<td>{_fmt(s.get('bw_eff_med'), 4)}</td>"
                    f"<td>{_fmt(s.get('hotspot_util_med'), 1)}%</td>"
                    f"<td>{_fmt(s.get('accepted_med'), 5)}</td>"
                    f"<td>{s.get('n_stable')}</td></tr>"
                )
        return (f"<h3>{_esc(caption)}</h3>"
                f"<table><thead><tr>{hdr}</tr></thead>"
                f"<tbody>{''.join(body)}</tbody></table>")

    def buf_flits(h: dict | None):
        if not h:
            return None
        if h.get("buffer_slots_per_router") is not None:
            return h["buffer_slots_per_router"]
        bits = h.get("buffer_bits_per_router")
        return bits // 512 if bits else None

    dest_xy = "0（组合逻辑，无 SRAM 表）"
    sr_xy = hw_xy.get("sr_header_bits")
    buf_xy = buf_flits(hw_xy)
    buf_st = buf_flits(hw_st_h)
    buf_pg = [buf_flits(h) for h in hw_pg if buf_flits(h) is not None]
    buf_pg_med = sorted(buf_pg)[len(buf_pg) // 2] if buf_pg else None
    buf_pg_max = max(buf_pg) if buf_pg else None
    dest_st = hw_st_h.get("table_bits_per_router")
    dest_st_s = "不可行" if dest_st is None else str(dest_st)
    st_billed = hw_st_h.get("num_vc_billed", hw_st_h.get("num_vc", "—"))
    st_used = hw_st_h.get("num_vc_used", hw_st_h.get("num_vc"))
    st_vc_s = (f"{st_billed}" if st_used in (None, st_billed)
               else f"{st_billed}（用{st_used}）")
    xy_src = hw_xy.get("table_src_aware_bits_max") or 0
    st_src = hw_st_h.get("table_src_aware_bits_max") or 0
    xy_ent = xy_src // 2 if xy_src else "—"
    st_ent = st_src // 3 if st_src else "—"
    n_pairs = (hw_xy.get("n_live") or 48) * ((hw_xy.get("n_live") or 48) - 1)
    hmax_xy = hw_xy.get("sr_hmax")
    if isinstance(hmax_xy, int) and hmax_xy > 0:
        len_bits = max(1, math.ceil(math.log2(hmax_xy + 1)))
    else:
        len_bits = 4

    def vc_cell(h):
        b = h.get("num_vc_billed", h.get("num_vc"))
        u = h.get("num_vc_used", h.get("num_vc"))
        return f"{b}" if u in (None, b) else f"{b}（用{u}）"

    hw_pg_rows = []
    for h in hw_pg:
        dest = h.get("table_bits_per_router")
        dest_s = "不可行" if dest is None else str(dest)
        hw_pg_rows.append(
            f"<tr><td class='l'>{_esc(h['scenario'])}</td>"
            f"<td>{h.get('n_routers')}</td><td>{h.get('n_links')}</td>"
            f"<td>{h.get('n_live')}</td>"
            f"<td>{vc_cell(h)}</td>"
            f"<td>{buf_flits(h)}</td>"
            f"<td>{dest_s}</td>"
            f"<td>{h.get('table_src_aware_bits_max', '—')}</td>"
            f"<td>{h.get('sr_header_bits')}</td>"
            f"<td>{_fmt(h.get('sr_frac_of_flit'), 4)}</td>"
            f"<td>{h.get('sr_hmax')}</td></tr>"
        )

    return f"""
<h2>10. 均匀注入率 all-to-all（健康 XY / ≤2R+≤4L Super-turn）</h2>
<p class="note">每个存活计算节点以相同伯努利注入率 λ 发单 flit 包，
目的均匀落在其余存活节点（与 <code>rg_steady_des.Injector</code> 同口径）。
all-good λ ∈ {{{', '.join(f'{x:.2f}' for x in (meta.get('lams_all_good') or meta.get('lams') or []))}}}；
partial λ ∈ {{{', '.join(f'{x:.2f}' for x in (meta.get('lams_partial') or meta.get('lams') or []))}}}。
warmup={meta['warmup']} cy，measure={meta['measure']} cy，Q={meta['Q']}，
flit={meta['flit_bits']} bit。
时延 = <code>t_eject − t_gen</code>（仅统计 measure 窗内产生的包，测量结束后排空）。
有效带宽 = 交付 flit 数 / measure 周期（全网；即数据量 / makespan，
open-loop 下 makespan = 测量窗）。
accepted = 交付包数 / (A · measure)。
all-good 路由 = 健康 mesh XY（1 VC，组合逻辑）。
partial-good 路由 = M0s Super-turn（turn-model，硬顶 2 VC）。
故障目录 = ≤2 死 router + ≤4 无向链路、router–链路不重叠，
每个 (n<sub>R</sub>, n<sub>L</sub>) 格子 1 个样本（seed=0），共
{meta.get('n_partial_scenarios')} 个场景
{('；跳过 ' + ', '.join(meta['skipped'])) if meta.get('skipped') else ''}。
原始 JSON：<code>results/pg_a2a_lambda.json</code>。</p>

<h3>10.1 开销（与 λ 无关）</h3>
<p class="note">缓冲 = <b>方案硅上 VC</b> ×（4 个 mesh 端口 × {bm.get('q_mesh', 19)}
+ 1 个本地 ramp 端口 × {bm.get('q_local', 3)}）
= VC × {bm.get('flits_per_vc', 79)} flit/router（推导与实测见 10.1.1；
下 ramp 端口<b>不</b>按 Q 计）。1 flit = {meta.get('flit_bits', 512)} bit。
Super-turn 一律按 <b>2 VC</b> 计（M0s 硬顶），
即使某张残图 1 个转向模型就够用（「VC」列括号里是本场景实际用到的层数）。
目的表 = 目的唯一下一跳时 (A−1)×2 bit；冲突则 dest-only 不可行。
源感知表 = 过路 (src,dst) 条目 × (2+VC bit)，取路由器最大值。
源路由头 = ⌈log<sub>2</sub>(H<sub>max</sub>+1)⌉ + 2·H<sub>max</sub> + [VC 1 bit]；
H<sub>max</sub> = 路径最大跳数。512-bit flit 上均不额外占 flit。</p>
<table>
<thead><tr>
<th class="l">场景</th><th>缓冲 flit/router</th>
<th>目的路由表 bit</th><th>源感知表 bit（最大）</th>
<th>源路由头 bit</th><th>H<sub>max</sub></th><th>VC</th>
</tr></thead>
<tbody>
<tr><td class="l">all-good 健康 XY</td>
<td>{buf_xy}</td><td>{dest_xy}</td>
<td>{hw_xy.get('table_src_aware_bits_max', '—')}</td>
<td>{sr_xy}</td><td>{hw_xy.get('sr_hmax')}</td><td>1</td></tr>
<tr><td class="l">健康 Super-turn（对照）</td>
<td>{buf_st if buf_st is not None else '—'}</td>
<td>{dest_st_s}</td>
<td>{hw_st_h.get('table_src_aware_bits_max', '—')}</td>
<td>{hw_st_h.get('sr_header_bits', '—')}</td>
<td>{hw_st_h.get('sr_hmax', '—')}</td>
<td>{st_vc_s}</td></tr>
</tbody></table>
<p class="note">健康 Super-turn（对照）相对健康 XY：源感知表
{xy_src} → {st_src} bit，
源路由头 {hw_xy.get('sr_header_bits')} → {hw_st_h.get('sr_header_bits')} bit。
<strong>不是绕路。</strong>健康 8×6 上两套路径完全相同
（H<sub>max</sub> 都是 {hmax_xy}，{n_pairs} 对都是曼哈顿最短路，
最忙路由器过路 (src,dst) 条数相同）。
差出来的 bit 只来自 Super-turn 的硅上 VC 预算：M0s 硬顶 2 VC
（本图实际只用 1 层 <code>{hw_st_h.get('turn_mode') or 'east_first'}</code>）。
源感知表每条 = 2 bit 下一跳 + [VC 选择 1 bit]，
所以 {xy_src} = {xy_ent}×2，
{st_src} = {st_ent}×3。
源路由头 = ⌈log<sub>2</sub>(H<sub>max</sub>+1)⌉ + 2·H<sub>max</sub> + [VC 1 bit]
= {len_bits}+{2 * (hmax_xy or 0)}+0 = {hw_xy.get('sr_header_bits')}
对 {len_bits}+{2 * (hmax_xy or 0)}+1 = {hw_st_h.get('sr_header_bits')}。</p>
<p class="note">partial Super-turn 汇总：缓冲中位
{buf_pg_med} / 最大 {buf_pg_max} flit/router；
目的表中位 {hw_sum.get('table_bits_med')} / 最大
{hw_sum.get('table_bits_max')} bit
（dest-only 可行 {hw_sum.get('n_dest_only_ok')}/{len(hw_pg)}）；
源路由头中位 {hw_sum.get('sr_header_bits_med')} / 最大
{hw_sum.get('sr_header_bits_max')} bit；VC 最大
{hw_sum.get('num_vc_max')}。</p>
<table>
<thead><tr>
<th class="l">场景</th><th>R</th><th>L</th><th>A</th><th>VC</th>
<th>缓冲 flit</th><th>目的表 bit</th><th>源感知表 max</th>
<th>源路由头 bit</th><th>头/flit</th><th>H<sub>max</sub></th>
</tr></thead>
<tbody>
{''.join(hw_pg_rows)}
</tbody></table>

{buffer_derivation_html(data)}
{sr_header_derivation_html(data)}
{dest_table_derivation_html(data)}

{summary_table(ag, "10.2 all-good（健康 XY）按 λ")}
{summary_table(pg, "10.3 partial-good Super-turn 按 λ（14 场景中位 / 最差）")}
{rows_table("all_good", "10.4 all-good 逐 λ 明细")}
{rows_table("partial_good", "10.5 partial-good 逐场景 × λ 明细")}
"""


def build_standalone(data: dict) -> str:
    css = """
body { font-family: "IBM Plex Sans", "Noto Sans SC", sans-serif;
       margin: 2rem; color: #1a1a1a; background: #fafafa; line-height: 1.45; }
h1,h2,h3,h4 { font-family: "IBM Plex Serif", "Noto Serif SC", serif; }
table { border-collapse: collapse; margin: 1rem 0; font-size: 0.85rem;
        background: #fff; }
th,td { border: 1px solid #ddd; padding: 0.35rem 0.5rem; text-align: right; }
th { background: #eef2f5; }
td.l, th.l { text-align: left; }
td.bad { color: #c0392b; font-weight: 600; }
.note { color: #555; max-width: 56rem; }
.sub { font-size: 0.7rem; color: #666; }
code { background: #eee; padding: 0.1rem 0.3rem; }
"""
    return (
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'/>"
        "<title>均匀注入率 all-to-all（8×6）</title>"
        f"<style>{css}</style></head><body>"
        "<h1>8×6 均匀注入率 all-to-all：健康 XY 与 ≤2R+≤4L Super-turn</h1>"
        + setup_html(data)
        + lambda_section_html(data) +
        "</body></html>"
    )


def main() -> None:
    data = json.loads(JSON_PATH.read_text())
    HTML_PATH.write_text(build_standalone(data), encoding="utf-8")
    print(f"Wrote {HTML_PATH}")


if __name__ == "__main__":
    main()
