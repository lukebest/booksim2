#!/usr/bin/env python3
"""HTML tables for the uniform-λ all-to-all sweep (healthy XY / Super-turn)."""
from __future__ import annotations

import html
import json
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


def setup_html(data: dict) -> str:
    """Checkable simulation setup — first thing on the standalone report."""
    m = data.get("meta") or {}
    t0 = m.get("max_zero_latency", 98)
    wire = m.get("max_manhattan_wire", 94)
    hops = m.get("max_manhattan_hops", 12)
    lams = m.get("lams") or []
    lam_s = ", ".join(f"{x:.2f}" for x in lams)
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
<tr><td class="l">缓冲</td><td class="l">IQ，Q={m.get('Q', 19)} = 2·V+1，
credit 初值 = Q，每输出每周期 1 flit</td></tr>
<tr><td class="l">流量</td><td class="l">每个存活计算节点每周期以概率 λ 产生 1 个包；
目的在其余存活节点上均匀（各源 λ 相同）</td></tr>
<tr><td class="l">λ 网格</td><td class="l">0.10–0.35 步进 0.05，之后 0.36–0.50 步进 0.01：
<code>{lam_s}</code>（共 {len(lams)} 点）</td></tr>
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

    def rows_table(tag: str, title: str) -> str:
        sel = [r for r in rows if r["tag"] == tag]
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
                   "<th>accepted 中位<div class='sub'>flit/节点/cy</div></th>"
                   "<th>稳定<div class='sub'>场景数</div></th>")
        else:
            hdr = ("<th>λ</th><th>平均时延</th><th>最长时延</th>"
                   "<th>最长/T<sub>0</sub></th>"
                   "<th>有效带宽 (flit/cy)</th><th>accepted</th><th>稳定</th>")
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
λ ∈ {{{', '.join(f'{x:.2f}' for x in meta['lams'])}}}。
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
<p class="note">缓冲 = 5 端口 × <b>方案硅上 VC</b> × Q，单位 <b>flit/router</b>
（1 flit = 512 bit）。Super-turn 一律按 <b>2 VC</b> 计（M0s 硬顶），
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
