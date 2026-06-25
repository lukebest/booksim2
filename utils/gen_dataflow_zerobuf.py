#!/usr/bin/env python3
"""Cycle-by-cycle HTML demos of the ZERO router-buffer allgather schedules.

Every scheme below is strictly ring_buf=0 and eject_buf=0 (no router buffer; all
waiting happens in the border AFIFOs), eject bandwidth 2 flit/cy, H=4, V=6.
Schedules come from utils/sched_ring_zerobuf.py.

  border_d5      : atomic pacing, single AFIFO <= 5 -> makespan 387cy (4096-sweep bi cfg).
  grid_8x2_r2/r4 : 16x16 mesh, 8x2 grid border, atomic AFIFO<=5, ramp 2 or 4 flit/cy.
  ringfollow     : shape-opt ringfollow -> 416cy.
  global1        : 4 rings spliced into ONE global ring -> 754cy.

Output: results/dataflow_zerobuf.html (self-contained canvas viewer).
"""

import json
from pathlib import Path

import sim_fused_rings as fr
import sched_ring_zerobuf as S
from sweep_buffer_pareto import build_grid_border
from sweep_quad_ring_shapes import cfg_str, make_quads

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "dataflow_zerobuf.html"
MX, MY = 16, 16
H, V = 4, 6
AFIFO_CAP = 5
# 4096-sweep minimum-makespan bi border shape (mk=240 @ spread=0); atomic d5 uses same rings.
BORDER_BI_CFG = (("vflip", 1), ("rect", 1), ("rect", 3), ("vflip", 3))
GRID_8X2 = (8, 2)

def quad_rings():
    hw, hh = MX // 2, MY // 2
    return [fr.ham_cycle_rect(qx * hw, qy * hh, hw, hh)
            for qy in range(2) for qx in range(2)]


def global_ring():
    return [fr.ham_cycle_rect(0, 0, MX, MY)]


def rings_from_quads(quads):
    return [q["order"] for q in quads]


def schedule_with_quads(cfg, deliv_fn, spread=0, lb_cross=False):
    quads = make_quads(cfg)
    deliv = lambda s, b, q=quads: deliv_fn(s, b, q)
    r = S.schedule(MX, True, 2, deliv, spread=spread, quads=quads,
                   record_events=True, lb_cross=lb_cross)
    return r, rings_from_quads(quads)


def compute_conflicts(events_dict, ramp_bw, makespan):
    """Detect router output-port conflicts in a fixed schedule.

    At router R, cycle T, if >= 2 flits emit on the *same* output port -> conflict.
    Mesh link (R->C): cap 1 flit/cy.  Down ramp at R: cap ramp_bw flit/cy.
    """
    ev = events_dict
    n_ev = ev["n_ev"]
    p_arr, c_arr, t_arr, arr_arr = ev["p"], ev["c"], ev["t"], ev["arr"]

    link_buckets = {}
    for i in range(n_ev):
        key = (p_arr[i], c_arr[i], t_arr[i])
        link_buckets.setdefault(key, []).append(i)
    link_list = []
    for (p, c, t), idxs in link_buckets.items():
        if len(idxs) >= 2:
            link_list.append({
                "cy": t, "p": p, "c": c, "n": len(idxs), "ev": idxs[:24], "kind": "link",
            })

    order = sorted(range(n_ev), key=lambda i: (arr_arr[i], t_arr[i], i))
    eject_buckets = {}
    eject_at = [0] * n_ev
    busy = {}

    def reserve_down(node, earliest):
        d = busy.setdefault(node, {})
        t = earliest
        while d.get(t, 0) >= ramp_bw:
            t += 1
        d[t] = d.get(t, 0) + 1
        return t

    for i in order:
        nd = c_arr[i]
        e = reserve_down(nd, arr_arr[i])
        eject_at[i] = e
        key = (nd, e)
        eject_buckets.setdefault(key, []).append(i)

    eject_list = []
    for (nd, t), idxs in eject_buckets.items():
        if len(idxs) > ramp_bw:
            eject_list.append({
                "cy": t, "p": nd, "c": -1, "n": len(idxs), "ev": idxs[:24], "kind": "eject",
            })

    mk = makespan + 1
    at = [{"link": [], "eject": []} for _ in range(mk + 1)]
    for item in link_list:
        cy = item["cy"]
        if cy <= mk:
            at[cy]["link"].append(item)
    for item in eject_list:
        cy = item["cy"]
        if cy <= mk:
            at[cy]["eject"].append(item)

    cycles_with = sorted({x["cy"] for x in link_list + eject_list})
    return {
        "link": link_list,
        "eject": eject_list,
        "at": at,
        "eject_at": eject_at,
        "n_link_conflicts": len(link_list),
        "n_eject_conflicts": len(eject_list),
        "n_flits_in_link_conflict": sum(x["n"] for x in link_list),
        "n_flits_in_eject_conflict": sum(x["n"] for x in eject_list),
        "n_cycles_with_conflict": len(cycles_with),
        "clean": len(link_list) == 0 and len(eject_list) == 0,
        "ramp_bw": ramp_bw,
    }


def pack(name, label, note, result, ring_paths, *, grid=None, cellmap=None, ramp_bw=None):
    ev = result["events"]
    mk = result["makespan"]
    s_, p_, c_, t_, lat_, arr_, k_ = [], [], [], [], [], [], []
    for (s, p, c, t, lat, arr, k) in ev:
        s_.append(s); p_.append(p); c_.append(c)
        t_.append(t); lat_.append(lat); arr_.append(arr); k_.append(k)
    start_at = [[] for _ in range(mk + 2)]
    end_at = [[] for _ in range(mk + 2)]
    for i, (t, a) in enumerate(zip(t_, arr_)):
        if t <= mk:
            start_at[t].append(i)
        if a <= mk:
            end_at[a].append(i)
    out = {
        "name": name,
        "label": label,
        "note": note,
        "makespan": mk,
        "afifo_depth": result["afifo_depth"],
        "afifo_bal_peak": (result.get("afifo_balanced") or {}).get("peak", 0),
        "max_inject_off": result["max_inject_off"],
        "events": {"s": s_, "p": p_, "c": c_, "t": t_, "lat": lat_,
                   "arr": arr_, "kind": k_, "n_ev": len(s_)},
        "start_at": start_at,
        "end_at": end_at,
        "ring_paths": ring_paths,
        "afifo": result.get("afifo_profile") or {
            "global": [0] * (mk + 1), "peak": 0, "peak_cy": 0, "worst": None, "top": [],
        },
        "afifo_balanced": result.get("afifo_balanced") or {
            "global": [0] * (mk + 1), "peak": 0, "peak_cy": 0,
        },
    }
    if grid is not None:
        out["grid"] = {"Qx": grid[0], "Qy": grid[1]}
    if cellmap is not None:
        out["cellmap"] = cellmap
    if ramp_bw is not None:
        out["ramp_bw"] = ramp_bw
    else:
        ramp_bw = 2
    evd = out["events"]
    out["conflicts"] = compute_conflicts(evd, ramp_bw, mk)
    return out


def grid_ring_paths(qx, qy):
    wx, wy = MX // qx, MY // qy
    return [fr.ham_cycle_rect(rx * wx, ry * wy, wx, wy)
            for ry in range(qy) for rx in range(qx)]


def grid_cell_map(qx, qy):
    wx, wy = MX // qx, MY // qy
    out = []
    for s in range(MX * MY):
        x, y = s % MX, s // MX
        out.append((x // wx) + (y // wy) * qx)
    return out


def best_grid_atomic(qx, qy, ramp_bw):
    deliv = lambda s, b: build_grid_border(s, qx, qy, b)
    best_r, best_order = None, None
    for order in ("interleave", "natural", "quad"):
        r = S.schedule_atomic(MX, True, ramp_bw, deliv, afifo_cap=AFIFO_CAP,
                              order=order, record_events=True)
        if not r.get("ok") or r["afifo_depth"] > AFIFO_CAP:
            continue
        if best_r is None or r["makespan"] < best_r["makespan"]:
            best_r, best_order = r, order
    if best_r is None:
        raise RuntimeError(f"grid {qx}x{qy} atomic AFIFO<={AFIFO_CAP} infeasible @ ramp={ramp_bw}")
    return best_r, best_order


def build():
    fr.cfg(MX, MY, H, V)
    schemes = {}
    qx, qy = GRID_8X2
    gpaths = grid_ring_paths(qx, qy)
    gcells = grid_cell_map(qx, qy)

    for ramp_bw, key, tag in ((2, "grid_8x2_r2", "2 flit/cy"), (4, "grid_8x2_r4", "4 flit/cy")):
        r_g, order = best_grid_atomic(qx, qy, ramp_bw)
        schemes[key] = pack(
            f"grid {qx}x{qy} ramp={ramp_bw}", f"grid {qx}×{qy} · AFIFO≤5 · 下 ramp={tag}",
            f"16×16 划分为 {qx}×{qy} 个 {MX // qx}×{MY // qy} 小环 + 格间短弧；"
            f"<code>schedule_atomic</code> 源序 <b>{order}</b>；"
            f"makespan=<b>{r_g['makespan']}</b>，单链路 AFIFO {r_g['afifo_depth']}，"
            f"均衡 {r_g['afifo_balanced']['peak']}。router 零 buffer。",
            r_g, gpaths, grid=GRID_8X2, cellmap=gcells, ramp_bw=ramp_bw)

    quads_d5 = make_quads(BORDER_BI_CFG)
    deliv_d5 = lambda s, b, q=quads_d5: S.deliv_border_quads(s, b, q)
    r_d5 = S.schedule_atomic(MX, True, 2, deliv_d5, afifo_cap=5, order="natural",
                             record_events=True, quads=quads_d5)
    schemes["border_d5"] = pack(
        "border depth≤5", "border 短弧 · 单链路 AFIFO≤5（相位错开）",
        "4096 组扫描最优环形状 + <code>schedule_atomic</code> natural 源序；"
        f"makespan=<b>{r_d5['makespan']}</b>，单链路 AFIFO {r_d5['afifo_depth']}，"
        f"均衡 {r_d5['afifo_balanced']['peak']}。"
        + cfg_str(BORDER_BI_CFG),
        r_d5, [q["order"] for q in quads_d5])

    # ringfollow with optimized shape
    from optimize_quad_shapes import quads_for, load_optimal
    load_optimal()
    try:
        quads_rf = quads_for(MX, "ringfollow", "bi")
    except Exception:
        quads_rf = quads_d5
    deliv_rf = lambda s, b, q=quads_rf: S.deliv_ringfollow_quads(s, b, q)
    r_rf = S.schedule(MX, True, 2, deliv_rf, spread=0, quads=quads_rf, record_events=True)
    schemes["ringfollow_opt"] = pack(
        "ring-following", "ring-following · 环形状优化",
        f"环形状优化 ringfollow；mk={r_rf['makespan']}，均衡 AFIFO {r_rf['afifo_balanced']['peak']}。",
        r_rf, [q["order"] for q in quads_rf])

    schemes["ringfollow"] = pack(
        "ring-following d5", "ring-following · AFIFO≤5（相位错开）",
        "外象限切入目的环后绕整圈；AFIFO≤5。",
        S.schedule_atomic(MX, True, 2, deliv_rf, afifo_cap=5,
                          order="quad", record_events=True),
        [q["order"] for q in quads_rf])

    schemes["global1"] = pack(
        "global 单环", "4 环穿成 1 个全局环（无并行）",
        "四象限 Hamilton 环经边界 AFIFO 拼成一条全局环。",
        S.schedule(MX, True, 2, S.deliv_global, record_events=True), global_ring())

    cell, pad = 34, 44
    cfg = {
        "mx": MX, "my": MY, "cell": cell, "pad": pad,
        "W": pad * 2 + (MX - 1) * cell, "H": pad * 2 + (MY - 1) * cell,
        "n": MX * MY,
        "pos": [[pad + (i % MX) * cell, pad + (i // MX) * cell] for i in range(MX * MY)],
        "qmap": [fr.quad_of(i) for i in range(MX * MY)],
        "order": ["grid_8x2_r2", "grid_8x2_r4", "border_d5",
                  "ringfollow_opt", "ringfollow", "global1"],
        "default": "grid_8x2_r2",
        "schemes": schemes,
    }
    return cfg


HTML = r"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>16×16 零 router-buffer AllGather · cycle-by-cycle 演示</title>
<style>
*{box-sizing:border-box;}
body{font-family:'Segoe UI',system-ui,sans-serif;margin:0;background:#f1f5f9;color:#0f172a;}
header{background:linear-gradient(135deg,#0f766e,#0e7490);color:#fff;padding:16px 24px;}
header h1{margin:0 0 6px;font-size:20px;}
header p{margin:0;font-size:13px;opacity:.92;line-height:1.5;}
.layout{display:grid;grid-template-columns:1fr 300px;gap:16px;padding:16px;max-width:1400px;margin:0 auto;}
@media(max-width:980px){.layout{grid-template-columns:1fr;}}
.panel{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:14px;box-shadow:0 1px 3px rgba(0,0,0,.06);}
.panel h2{margin:0 0 10px;font-size:15px;color:#0f766e;}
#cvwrap{position:relative;overflow:auto;border-radius:8px;background:#f8fafc;}
canvas{display:block;}
.ctl{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:8px 0;font-size:13px;}
button{background:#0d9488;color:#fff;border:0;border-radius:6px;padding:7px 14px;cursor:pointer;font-size:13px;}
button:hover{background:#0f766e;}
button.sec{background:#64748b;}
input[type=range]{flex:1;min-width:120px;}
select{font-size:13px;padding:4px 6px;border-radius:4px;border:1px solid #cbd5e1;}
#cyc{font-weight:700;color:#0f766e;font-variant-numeric:tabular-nums;min-width:3em;display:inline-block;}
.statgrid{display:grid;grid-template-columns:repeat(6,1fr);gap:8px;font-size:12px;}
.statgrid div{background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:8px;}
.statgrid b{display:block;font-size:18px;color:#0f766e;}
.phase{height:8px;border-radius:4px;background:#e2e8f0;margin:8px 0;overflow:hidden;}
.phase>div{height:100%;background:linear-gradient(90deg,#14b8a6,#0ea5e9,#22c55e);width:0%;}
.note{font-size:12px;color:#475569;line-height:1.55;margin-top:8px;}
.tag{display:inline-block;padding:1px 6px;border-radius:4px;font-size:11px;color:#fff;background:#0d9488;margin-right:4px;}
table.cmp{border-collapse:collapse;width:100%;font-size:12px;margin-top:6px;}
table.cmp td,table.cmp th{border:1px solid #e2e8f0;padding:4px 6px;text-align:center;}
table.cmp th{background:#ccfbf1;}
table.cmp tr.on td{background:#fef9c3;font-weight:700;}
.conf-ok{color:#059669;font-weight:700;}
.conf-bad{color:#dc2626;font-weight:700;}
#conflictPanel{font-size:12px;max-height:180px;overflow-y:auto;}
#conflictPanel li{margin:4px 0;}
</style></head><body>
<header>
<h1>16×16 零 router-buffer AllGather · cycle-by-cycle</h1>
<p>grid 8×2 / border 四象限环 + 跨界 AFIFO · H=4, V=6 · 双向 ·
所有方案 <b>router 内零 buffer</b>（ring_buf=0, eject_buf=0），等待只发生在 AFIFO。
<p>当前方案下 ramp=<b id="hramp"></b> flit/cy · makespan=<b id="hmk"></b> cy ·
单链路 AFIFO=<b id="haf"></b> · 均衡深度=<b id="hafb"></b> ·
冲突检测=<b id="hconf"></b></p>
</header>
<div class="layout">
<div>
<div class="panel">
<div class="ctl">
<label>方案 <select id="scheme"></select></label>
<label>模式 <select id="mode">
<option value="single">单源</option>
<option value="quad">单象限 64 源</option>
<option value="all">全源（慢）</option>
</select></label>
<label>源 <select id="src"></select></label>
<label>象限 <select id="quad">
<option value="-1">全部</option><option value="0">Q0 左下</option>
<option value="1">Q1 右下</option><option value="2">Q2 左上</option>
<option value="3">Q3 右上</option></select></label>
</div>
<div id="cvwrap"><canvas id="cv"></canvas></div>
<div class="phase"><div id="pbar"></div></div>
<div class="ctl">
<button id="play">▶ 播放</button>
<button id="step" class="sec">单步 ▶|</button>
<button id="reset" class="sec">⟲ 复位</button>
<span>cycle <span id="cyc">0</span> / <span id="mk"></span></span>
<label>速度 <input type="range" id="speed" min="20" max="500" value="160" step="10"></label>
</div>
<div class="ctl"><input type="range" id="slider" min="0" value="0" step="1"></div>
<div class="statgrid">
<div>飞行中 flit<b id="fly">0</b></div>
<div>本 cycle eject<b id="ej">0</b></div>
<div>makespan<b id="mk2">0</b></div>
<div>单链路 AFIFO<b id="af">0</b></div>
<div>均衡 AFIFO<b id="afb">0</b></div>
<div>本 cycle 排队<b id="afnow">0</b></div>
<div>链路冲突<b id="clink">0</b></div>
<div>下ramp冲突<b id="ceject">0</b></div>
</div>
<p class="note" id="note"></p>
<p class="note" id="confdef"><b>冲突定义</b>：同一 router 在同一 cycle 向<b>同一出端口</b>发出 ≥2 个 flit。
Mesh 链路口容量 1 flit/cy；下 ramp 容量 = ramp_bw flit/cy。
链路冲突在<b>发送 cycle</b>（t）标红；下 ramp 冲突在<b>eject cycle</b>标红。</p>
</div>
</div>
<div>
<div class="panel"><h2>冲突检测</h2>
<p id="confSummary"></p>
<ul id="conflictPanel"></ul>
<button id="jumpconf" class="sec" style="margin-top:6px;font-size:12px;padding:5px 10px">跳到首个冲突 cycle</button>
</div>
<div class="panel"><h2>方案对比（均 router 零 buffer）</h2>
<table class="cmp" id="cmp"><thead><tr><th>方案</th><th>makespan</th><th>单链路 AFIFO</th><th>均衡 AFIFO</th><th>冲突</th></tr></thead>
<tbody id="cmpbody"></tbody></table>
<p class="note"><b>grid 8×2</b>：16×16 划为 8×2 个 2×8 Hamilton 小环 + 格间 AFIFO，atomic 单链路 AFIFO≤5。
<b>border 387cy</b>：4096 组扫描四象限环 + natural 源序。240cy 需 spread=0 且无 AFIFO 上限。</p>
</div>
<div class="panel"><h2>AFIFO 深度曲线</h2>
<canvas id="afc" width="280" height="150" style="width:100%;max-width:300px;display:block;background:#f8fafc;border-radius:6px;border:1px solid #e2e8f0"></canvas>
<p class="note" id="aflbl">橙=单链路全网峰值；紫虚线=8链路理想均摊（均衡深度）；绿虚线=预算 5。</p>
<button id="jumppeak" class="sec" style="margin-top:6px;font-size:12px;padding:5px 10px">跳到 AFIFO 峰值 cycle</button>
<button id="jumpbal" class="sec" style="margin-top:6px;font-size:12px;padding:5px 10px">跳到理想均摊峰值</button>
</div>
<div class="panel"><h2>Hamilton 环路径</h2>
<svg id="ringsvg" style="width:100%;max-width:280px;display:block;margin:0 auto"></svg>
<p class="note">彩色=各小环走向；红虚线=格间/象限边界（AFIFO）。圆点颜色=源节点，粗描边=本 cycle 正在 eject。</p>
</div>
</div>
</div>
<script>
const D = __DATA__;
const QCOL=['#0ea5e9','#22c55e','#f97316','#a855f7'];
const QBG =['#ecfeff','#f0fdf4','#fff7ed','#faf5ff'];
let key=D.default, S=D.schemes[key];
const cv=document.getElementById('cv'), ctx=cv.getContext('2d');
cv.width=D.W; cv.height=D.H;
function pos(i){return D.pos[i];}
function coord(i){return [i%D.mx,(i/D.mx)|0];}
function hue(s){return 'hsl('+Math.round(s*360/D.n)+',72%,48%)';}
function isCross(p,c){
  if(S.cellmap)return S.cellmap[p]!==S.cellmap[c];
  return D.qmap[p]!==D.qmap[c];}
function drawGridLines(ctx, sc, pad, stroke, lw, dash){
  if(!S.grid)return false;
  const wx=D.mx/S.grid.Qx, wy=D.my/S.grid.Qy;
  ctx.save();ctx.strokeStyle=stroke;ctx.lineWidth=lw;
  if(dash)ctx.setLineDash(dash);else ctx.setLineDash([]);
  for(let i=1;i<S.grid.Qx;i++){const x=pad+i*wx*sc;
    ctx.beginPath();ctx.moveTo(x,pad);ctx.lineTo(x,pad+(D.my-1)*sc);ctx.stroke();}
  for(let j=1;j<S.grid.Qy;j++){const y=pad+j*wy*sc;
    ctx.beginPath();ctx.moveTo(pad,y);ctx.lineTo(pad+(D.mx-1)*sc,y);ctx.stroke();}
  ctx.restore();return true;}

// scheme dropdown + comparison table
const schemeSel=document.getElementById('scheme');
D.order.forEach(k=>{const o=document.createElement('option');o.value=k;
  const sc=D.schemes[k];
  o.textContent=sc.label+'（'+sc.makespan+'cy, 均衡AFIFO '+sc.afifo_bal_peak+'）';
  schemeSel.appendChild(o);});
schemeSel.value=key;
function buildCmp(){const tb=document.getElementById('cmpbody');tb.innerHTML='';
  D.order.forEach(k=>{const sc=D.schemes[k];const tr=document.createElement('tr');
    if(k===key)tr.className='on';
    const balCls=sc.afifo_bal_peak<=5?' style="background:#dcfce7;font-weight:bold"':'';
    const cf=sc.conflicts||{};
    const cTxt=cf.clean?'<span class="conf-ok">无</span>':
      '<span class="conf-bad">链路'+cf.n_link_conflicts+' 下ramp'+cf.n_eject_conflicts+'</span>';
    tr.innerHTML='<td>'+sc.label+'</td><td>'+sc.makespan+'</td><td>'+sc.afifo_depth+'</td>'
      +'<td'+balCls+'>'+sc.afifo_bal_peak+'</td><td>'+cTxt+'</td>';
    tb.appendChild(tr);});}

function fmtCoord(n){const x=n%D.mx,y=(n/D.mx)|0;return '('+x+','+y+')';}
function updateConflictPanel(k){
  const C=S.conflicts||{at:[],clean:true,link:[],eject:[]};
  const at=(C.at&&C.at[k])||{link:[],eject:[]};
  let nLink=0,nEj=0;
  at.link.forEach(x=>{nLink+=x.n;});
  at.eject.forEach(x=>{nEj+=x.n;});
  document.getElementById('clink').textContent=nLink;
  document.getElementById('ceject').textContent=nEj;
  const ul=document.getElementById('conflictPanel');
  ul.innerHTML='';
  if(C.clean){
    document.getElementById('confSummary').innerHTML=
      '<span class="conf-ok">✓ 全 schedule 无端口冲突</span>（共 '+S.events.n_ev+' 次链路发送均已错开）。';
    return;
  }
  document.getElementById('confSummary').innerHTML=
    '<span class="conf-bad">发现冲突</span>：链路端口 '+C.n_link_conflicts+
    ' 处 / 下 ramp '+C.n_eject_conflicts+' 处；涉及 '+C.n_cycles_with_conflict+' 个 cycle。';
  const show=at.link.concat(at.eject);
  if(!show.length){
    const li=document.createElement('li');li.textContent='当前 cycle '+k+' 无冲突';
    ul.appendChild(li);return;
  }
  show.forEach(x=>{
    const li=document.createElement('li');
    if(x.kind==='link'||x.c>=0){
      li.innerHTML='<b>链路</b> cy<b>'+x.cy+'</b> router '+fmtCoord(x.p)+
        ' → '+fmtCoord(x.c)+'：<b>'+x.n+'</b> flit 同端口同发';
    }else{
      li.innerHTML='<b>下ramp</b> cy<b>'+x.cy+'</b> 节点 '+fmtCoord(x.p)+
        '：<b>'+x.n+'</b> flit（cap '+((C.ramp_bw)||2)+'）';
    }
    ul.appendChild(li);
  });
}
function drawConflicts(k){
  const C=S.conflicts;if(!C||!C.at)return;
  const at=C.at[k]||{link:[],eject:[]};
  at.link.forEach(x=>{
    const a=pos(x.p),b=pos(x.c);
    ctx.strokeStyle='#dc2626';ctx.lineWidth=5;ctx.globalAlpha=0.9;
    ctx.beginPath();ctx.moveTo(a[0],a[1]);ctx.lineTo(b[0],b[1]);ctx.stroke();
    ctx.globalAlpha=1;
    ctx.fillStyle='#dc2626';ctx.font='bold 11px sans-serif';
    ctx.fillText('×'+x.n,a[0]+(b[0]-a[0])*0.4,a[1]+(b[1]-a[1])*0.4-8);
    [x.p,x.c].forEach(nd=>{const p=pos(nd);
      ctx.beginPath();ctx.arc(p[0],p[1],11,0,Math.PI*2);
      ctx.strokeStyle='#dc2626';ctx.lineWidth=3;ctx.stroke();});
  });
  at.eject.forEach(x=>{
    const p=pos(x.p);
    ctx.beginPath();ctx.arc(p[0],p[1],13,0,Math.PI*2);
    ctx.strokeStyle='#b91c1c';ctx.lineWidth=4;ctx.stroke();
    ctx.fillStyle='#b91c1c';ctx.font='bold 11px sans-serif';
    ctx.fillText('eject×'+x.n,p[0]+8,p[1]-10);
  });
}
function firstConflictCy(){
  const C=S.conflicts;if(!C)return 0;
  for(const x of (C.link||[]))return x.cy;
  for(const x of (C.eject||[]))return x.cy;
  return 0;
}

const srcSel=document.getElementById('src');
for(let s=0;s<D.n;s++){const [x,y]=coord(s);const o=document.createElement('option');
  o.value=s;o.textContent='('+x+','+y+') id='+s+' Q'+D.qmap[s];srcSel.appendChild(o);}
srcSel.value='0';

function drawRingSvg(){
  const NS='http://www.w3.org/2000/svg',svg=document.getElementById('ringsvg');
  while(svg.firstChild)svg.removeChild(svg.firstChild);
  const sc=16,pad=14,W=pad*2+(D.mx-1)*sc,Ht=pad*2+(D.my-1)*sc;
  svg.setAttribute('viewBox','0 0 '+W+' '+Ht);
  for(let y=0;y<D.my;y++)for(let x=0;x<D.mx;x++){const i=x+D.mx*y;
    const r=document.createElementNS(NS,'circle');
    r.setAttribute('cx',pad+x*sc);r.setAttribute('cy',pad+y*sc);r.setAttribute('r',2.5);
    r.setAttribute('fill',QBG[D.qmap[i]]);r.setAttribute('stroke','#cbd5e1');svg.appendChild(r);}
  S.ring_paths.forEach((path,qi)=>{let d='';
    path.forEach((nd,k)=>{const p=pad+(nd%D.mx)*sc,q=pad+((nd/D.mx)|0)*sc;d+=(k?'L':'M')+p+','+q;});
    d+='Z';const el=document.createElementNS(NS,'path');el.setAttribute('d',d);
    el.setAttribute('fill','none');el.setAttribute('stroke',QCOL[qi%4]);
    el.setAttribute('stroke-width','1.6');el.setAttribute('opacity','0.85');svg.appendChild(el);});
  if(S.grid){
    const wx=D.mx/S.grid.Qx, wy=D.my/S.grid.Qy;
    for(let i=1;i<S.grid.Qx;i++){const x=pad+i*wx*sc;
      const l=document.createElementNS(NS,'line');l.setAttribute('x1',x);l.setAttribute('y1',pad);
      l.setAttribute('x2',x);l.setAttribute('y2',pad+(D.my-1)*sc);l.setAttribute('stroke','#dc2626');
      l.setAttribute('stroke-dasharray','4 3');l.setAttribute('stroke-width','1.4');svg.appendChild(l);}
    for(let j=1;j<S.grid.Qy;j++){const y=pad+j*wy*sc;
      const l=document.createElementNS(NS,'line');l.setAttribute('x1',pad);l.setAttribute('y1',y);
      l.setAttribute('x2',pad+(D.mx-1)*sc);l.setAttribute('y2',y);l.setAttribute('stroke','#dc2626');
      l.setAttribute('stroke-dasharray','4 3');l.setAttribute('stroke-width','1.4');svg.appendChild(l);}
  }else{
    const hw=D.mx/2,hh=D.my/2;
    [['M',pad,pad+hh*sc,'L',pad+(D.mx-1)*sc,pad+hh*sc],
     ['M',pad+hw*sc,pad,'L',pad+hw*sc,pad+(D.my-1)*sc]].forEach(a=>{
      const l=document.createElementNS(NS,'line');l.setAttribute('x1',a[1]);l.setAttribute('y1',a[2]);
      l.setAttribute('x2',a[3]);l.setAttribute('y2',a[4]);l.setAttribute('stroke','#dc2626');
      l.setAttribute('stroke-dasharray','4 3');l.setAttribute('stroke-width','1.4');svg.appendChild(l);});
  }
}

let staticCv=null;
const afc=document.getElementById('afc'), actx=afc.getContext('2d');
const AFCAP=5;

function drawAfifoChart(k){
  const A=S.afifo;if(!A||!A.global)return;
  const B=S.afifo_balanced||{global:[],peak:0,peak_cy:0};
  const W=afc.width,H=afc.height,pad={l:34,r:8,t:10,b:22};
  actx.clearRect(0,0,W,H);
  const mk=S.makespan, ymax=Math.max(A.peak,B.peak||0,AFCAP,1);
  function x(t){return pad.l+(W-pad.l-pad.r)*t/mk;}
  function y(v){return pad.t+(H-pad.t-pad.b)*(1-v/ymax);}
  actx.strokeStyle='#e2e8f0';actx.lineWidth=1;
  for(let g=0;g<=4;g++){const v=ymax*g/4;actx.beginPath();
    actx.moveTo(pad.l,y(v));actx.lineTo(W-pad.r,y(v));actx.stroke();}
  actx.setLineDash([4,3]);actx.strokeStyle='#22c55e';actx.lineWidth=1.5;
  actx.beginPath();actx.moveTo(pad.l,y(AFCAP));actx.lineTo(W-pad.r,y(AFCAP));actx.stroke();
  actx.setLineDash([]);
  actx.fillStyle='#64748b';actx.font='10px sans-serif';
  actx.fillText('5',4,y(AFCAP)+3);actx.fillText(String(ymax|0),4,pad.t+4);
  actx.fillText('0',8,H-pad.b+4);actx.fillText('0',pad.l,H-4);actx.fillText(String(mk),W-pad.r-12,H-4);
  function plotCurve(curve,col,lw,dash){
    if(!curve||!curve.length)return;
    actx.setLineDash(dash||[]);actx.strokeStyle=col;actx.lineWidth=lw;actx.beginPath();
    curve.forEach((v,t)=>{const px=x(t),py=y(v);if(t)actx.lineTo(px,py);else actx.moveTo(px,py);});
    actx.stroke();actx.setLineDash([]);
  }
  (A.top||[]).slice().reverse().forEach(lk=>plotCurve(lk.curve,'#94a3b8',1));
  if(A.worst)plotCurve(A.worst.curve,'#dc2626',1.5);
  if(B.global&&B.global.length)plotCurve(B.global,'#7c3aed',2,[5,4]);
  plotCurve(A.global,'#f97316',2.2);
  actx.fillStyle='#f97316';actx.beginPath();
  actx.arc(x(A.peak_cy),y(A.peak),3.5,0,Math.PI*2);actx.fill();
  if(B.peak>0){actx.fillStyle='#7c3aed';actx.beginPath();
    actx.arc(x(B.peak_cy),y(B.peak),3,0,Math.PI*2);actx.fill();}
  actx.strokeStyle='#0f766e';actx.lineWidth=1.5;
  actx.beginPath();actx.moveTo(x(k),pad.t);actx.lineTo(x(k),H-pad.b);actx.stroke();
  const now=A.global[k]||0;
  document.getElementById('afnow').textContent=now;
  let lbl='固定路由峰值 <b>'+A.peak+'</b>@cy<b>'+A.peak_cy+'</b>';
  if(B.peak)lbl+=' · 8链路理想均摊 <b>'+B.peak+'</b>@cy<b>'+B.peak_cy+'</b>';
  if(A.worst)lbl+=' · 最深链路 '+A.worst.label;
  lbl+=' · 当前 cy'+k+' 排队 <b>'+now+'</b>';
  document.getElementById('aflbl').innerHTML=lbl;
}

function highlightAfifoLink(k){
  const A=S.afifo;if(!A||!A.worst)return;
  const w=A.worst, now=w.curve[k]||0;
  if(now<=0)return;
  const a=pos(w.p),b=pos(w.c);
  ctx.strokeStyle='#dc2626';ctx.lineWidth=4;ctx.globalAlpha=0.85;
  ctx.beginPath();ctx.moveTo(a[0],a[1]);ctx.lineTo(b[0],b[1]);ctx.stroke();
  ctx.globalAlpha=1;
  ctx.fillStyle='#dc2626';ctx.font='11px sans-serif';
  ctx.fillText('AFIFO '+now,w.p===w.c?a[0]:(a[0]+b[0])/2+4,(a[1]+b[1])/2-6);
}

function ensureStatic(){
  if(staticCv)return;
  staticCv=document.createElement('canvas');staticCv.width=D.W;staticCv.height=D.H;
  const c=staticCv.getContext('2d');
  const hw=D.mx/2,hh=D.my/2;
  [[0,0,0],[hw,0,1],[0,hh,2],[hw,hh,3]].forEach(([x0,y0,qi])=>{c.fillStyle=QBG[qi];
    c.fillRect(D.pad+x0*D.cell-14,D.pad+y0*D.cell-14,(hw-1)*D.cell+28,(hh-1)*D.cell+28);});
  c.strokeStyle='#e2e8f0';c.lineWidth=2;
  for(let y=0;y<D.my;y++)for(let x=0;x<D.mx;x++){const i=x+D.mx*y,[px,py]=pos(i);
    if(x+1<D.mx){const[qx,qy]=pos(i+1);c.beginPath();c.moveTo(px,py);c.lineTo(qx,qy);c.stroke();}
    if(y+1<D.my){const[qx,qy]=pos(i+D.mx);c.beginPath();c.moveTo(px,py);c.lineTo(qx,qy);c.stroke();}}
  c.setLineDash([6,4]);c.strokeStyle='#dc2626';c.lineWidth=2;
  if(!drawGridLines(c,D.cell,D.pad,'#dc2626',2,[6,4])){
    const mx=D.pad+(hw-0.5)*D.cell,my=D.pad+(hh-0.5)*D.cell;
    c.beginPath();c.moveTo(D.pad-10,my);c.lineTo(D.W-D.pad+10,my);c.stroke();
    c.beginPath();c.moveTo(mx,D.pad-10);c.lineTo(mx,D.H-D.pad+10);c.stroke();
  }
  c.setLineDash([]);c.lineWidth=1.2;
  S.ring_paths.forEach((path,qi)=>{c.strokeStyle=QCOL[qi%4];c.globalAlpha=0.22;c.beginPath();
    path.forEach((nd,k)=>{const p=pos(nd);if(k)c.lineTo(p[0],p[1]);else c.moveTo(p[0],p[1]);});
    c.closePath();c.stroke();});
  c.globalAlpha=1;
  for(let i=0;i<D.n;i++){const[px,py]=pos(i);c.beginPath();c.arc(px,py,5,0,Math.PI*2);
    c.fillStyle='#fff';c.fill();c.strokeStyle='#94a3b8';c.lineWidth=1.4;c.stroke();}
}

let cur=0,active=new Set(),timer=null,mode='single',fq=-1;
function visible(s){
  if(mode==='single')return s===parseInt(srcSel.value,10);
  if(mode==='quad')return D.qmap[s]===fq;
  if(fq>=0)return D.qmap[s]===fq;
  return true;
}
function rebuild(k){active.clear();const e=S.events;
  for(let i=0;i<e.n_ev;i++){if(k>=e.t[i]&&k<e.arr[i]&&visible(e.s[i]))active.add(i);}}
function stepActive(to){
  (S.start_at[to]||[]).forEach(i=>{if(visible(S.events.s[i]))active.add(i);});
  (S.end_at[to]||[]).forEach(i=>active.delete(i));}

function draw(k){
  cur=k;ensureStatic();
  document.getElementById('cyc').textContent=k;
  document.getElementById('slider').value=k;
  document.getElementById('pbar').style.width=(100*k/S.makespan)+'%';
  ctx.clearRect(0,0,D.W,D.H);ctx.drawImage(staticCv,0,0);
  const e=S.events;let fly=0,ej=0;const ejn={};
  const rdot=mode==='all'?3:5;
  for(const i of active){const s=e.s[i],p=e.p[i],c=e.c[i],t=e.t[i],lat=e.lat[i];
    const f=lat>0?(k-t)/lat:1;const a=pos(p),b=pos(c);
    const x=a[0]+(b[0]-a[0])*f,y=a[1]+(b[1]-a[1])*f;fly++;
    ctx.beginPath();ctx.arc(x,y,rdot,0,Math.PI*2);ctx.fillStyle=hue(s);
    if(isCross(p,c)){ctx.strokeStyle='#dc2626';ctx.lineWidth=2;ctx.stroke();}
    ctx.fill();}
  for(let i=0;i<e.n_ev;i++){if(e.arr[i]!==k)continue;if(!visible(e.s[i]))continue;ej++;ejn[e.c[i]]=hue(e.s[i]);}
  for(const nd in ejn){const p=pos(+nd);ctx.beginPath();ctx.arc(p[0],p[1],9,0,Math.PI*2);
    ctx.strokeStyle=ejn[nd];ctx.lineWidth=3;ctx.stroke();}
  document.getElementById('fly').textContent=fly;
  document.getElementById('ej').textContent=ej;
  drawAfifoChart(k);
  highlightAfifoLink(k);
  drawConflicts(k);
  updateConflictPanel(k);
}

function syncScheme(){
  S=D.schemes[key];staticCv=null;active.clear();cur=0;
  document.getElementById('mk').textContent=S.makespan;
  document.getElementById('mk2').textContent=S.makespan;
  document.getElementById('af').textContent=S.afifo_depth;
  document.getElementById('afb').textContent=S.afifo_bal_peak;
  document.getElementById('hmk').textContent=S.makespan;
  document.getElementById('haf').textContent=S.afifo_depth;
  document.getElementById('hafb').textContent=S.afifo_bal_peak;
  document.getElementById('hramp').textContent=S.ramp_bw||2;
  const C=S.conflicts||{clean:true};
  document.getElementById('hconf').innerHTML=C.clean?
    '<span class="conf-ok">无冲突</span>':
    '<span class="conf-bad">链路'+C.n_link_conflicts+'/下ramp'+C.n_eject_conflicts+'</span>';
  document.getElementById('slider').max=S.makespan;
  document.getElementById('note').innerHTML='<span class="tag">'+S.name+'</span>'+S.note;
  document.getElementById('jumppeak').onclick=()=>{
    const cy=(S.afifo&&S.afifo.peak_cy)||0;stop();
    if(Math.abs(cy-cur)>1)rebuild(cy);else stepActive(cy);draw(cy);
  };
  document.getElementById('jumpbal').onclick=()=>{
    const cy=(S.afifo_balanced&&S.afifo_balanced.peak_cy)||0;stop();
    if(Math.abs(cy-cur)>1)rebuild(cy);else stepActive(cy);draw(cy);
  };
  document.getElementById('jumpconf').onclick=()=>{
    const cy=firstConflictCy();if(!cy)return;stop();
    if(Math.abs(cy-cur)>1)rebuild(cy);else stepActive(cy);draw(cy);
  };
  buildCmp();drawRingSvg();rebuild(0);draw(0);
}
function stop(){if(timer){clearInterval(timer);timer=null;document.getElementById('play').textContent='▶ 播放';}}

schemeSel.onchange=()=>{key=schemeSel.value;stop();syncScheme();};
document.getElementById('mode').onchange=()=>{mode=document.getElementById('mode').value;
  fq=parseInt(document.getElementById('quad').value,10);
  if(mode==='quad'&&fq<0){document.getElementById('quad').value='0';fq=0;}rebuild(cur);draw(cur);};
document.getElementById('quad').onchange=()=>{fq=parseInt(document.getElementById('quad').value,10);
  if(mode==='quad'&&fq<0){document.getElementById('quad').value='0';fq=0;}rebuild(cur);draw(cur);};
srcSel.onchange=()=>{rebuild(cur);draw(cur);};
document.getElementById('slider').oninput=()=>{const nk=parseInt(document.getElementById('slider').value,10);
  if(Math.abs(nk-cur)>1)rebuild(nk);else stepActive(nk);draw(nk);};
document.getElementById('step').onclick=()=>{const nk=Math.min(cur+1,S.makespan);stepActive(nk);draw(nk);};
document.getElementById('reset').onclick=()=>{stop();rebuild(0);draw(0);};
document.getElementById('play').onclick=()=>{if(timer){stop();return;}
  document.getElementById('play').textContent='⏸ 暂停';
  timer=setInterval(()=>{let nk=cur+1;if(nk>S.makespan){rebuild(0);nk=0;}else stepActive(nk);draw(nk);},
    520-(+document.getElementById('speed').value));};
document.getElementById('speed').oninput=()=>{if(timer){stop();document.getElementById('play').click();}};

syncScheme();
</script>
</body></html>"""


def render():
    cfg = build()
    data = json.dumps(cfg, separators=(",", ":"))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(HTML.replace("__DATA__", data), encoding="utf-8")
    sizes = {k: (v["makespan"], v["afifo_depth"], v["afifo_bal_peak"],
                 v["conflicts"]["clean"], v["conflicts"]["n_link_conflicts"])
             for k, v in cfg["schemes"].items()}
    print(f"Wrote {OUT}")
    for k in cfg["order"]:
        mk, af, bal, clean, ncf = sizes[k]
        flag = "CLEAN" if clean else f"CONFLICT link={ncf}"
        print(f"  {k:16s} makespan={mk:4d}  AFIFO_link={af:2d}  AFIFO_bal={bal}  {flag}")


if __name__ == "__main__":
    render()
