#!/usr/bin/env python3
"""Cycle-by-cycle HTML demos of the ZERO router-buffer allgather schedules.

Every scheme below is strictly ring_buf=0 and eject_buf=0 (no router buffer; all
waiting happens in the border AFIFOs), eject bandwidth 2 flit/cy, H=4, V=6.
Schedules come from utils/sched_ring_zerobuf.py.

  border-fast : 4 parallel quadrant rings, short-arc spread, home rings injected
                together -> makespan 266 but bursty (AFIFO depth 11).
  border-opt  : ring shapes tuned by sweep -> makespan 240 (AFIFO depth ~45).
  border-d5   : same, paced so every AFIFO <= 5 -> makespan ~454.
  ringfollow  : cross then ride the WHOLE destination ring, AFIFO <= 5 -> ~508.
  ringfollow-opt : shape-tuned ring-following -> makespan 416.
  global1     : the 4 rings spliced into ONE global ring -> 754, AFIFO depth ~2.

Output: results/dataflow_zerobuf.html (self-contained canvas viewer).
"""

import json
from pathlib import Path

import sim_fused_rings as fr
import sched_ring_zerobuf as S
from sweep_quad_ring_shapes import cfg_str, make_quads

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "dataflow_zerobuf.html"
MX, MY = 16, 16

# 4096-config sweep minima (0 router-buffer, spread=0)
OPTIMAL_BORDER = (("vflip", 1), ("rect", 1), ("rect", 3), ("vflip", 3))
OPTIMAL_RINGFOLLOW = (("vflip", 3), ("rect", 3), ("rect", 1), ("rect", 3))


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


def pack(name, label, note, result, ring_paths):
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
    return {
        "name": name,
        "label": label,
        "note": note,
        "makespan": mk,
        "afifo_depth": result["afifo_depth"],
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


def build():
    fr.cfg(MX, MY, 4, 6)
    schemes = {}
    r, rings = schedule_with_quads(OPTIMAL_BORDER, S.deliv_border_quads)
    schemes["border_opt"] = pack(
        "border 240cy", "border 短弧 · 环形状优化（240cy）",
        "4096 组环形状扫描最优：左右列旋转相反，makespan=240（比默认 266 快 26cy）；"
        "需 AFIFO 深度 ~45。" + cfg_str(OPTIMAL_BORDER),
        r, rings)
    r_lb, rings_lb = schedule_with_quads(OPTIMAL_BORDER, S.deliv_border_quads, lb_cross=True)
    schemes["border_opt_lb"] = pack(
        "border 240cy+LB", "border 240cy · 8链路最短队列 LB",
        "跨界时在 8 条并行 AFIFO 中选排队最短的链路（在线贪心）。"
        f"在线 peak={r_lb['afifo_depth']}；"
        f"8链路组内理想均摊下界 peak={r_lb['afifo_balanced']['peak']}。",
        r_lb, rings_lb)
    schemes["border_fast"] = pack(
        "border 全速", "border 短弧 · 默认环（266cy）",
        "4 个 8×8 环并行，本环 conveyor 同时注入；默认 ham_cycle_rect，AFIFO 深度 11。",
        S.schedule(MX, True, 2, S.deliv_border, record_events=True), quad_rings())
    r266_lb = S.schedule(MX, True, 2, S.deliv_border, record_events=True, lb_cross=True)
    schemes["border_fast_lb"] = pack(
        "border 266cy+LB", "border 266cy · 8链路最短队列 LB",
        "默认环 + 边界最短队列选路。"
        f"在线 peak={r266_lb['afifo_depth']}；"
        f"理想均摊 peak={r266_lb['afifo_balanced']['peak']}（≤5 预算内）。",
        r266_lb, quad_rings())
    schemes["border_d5"] = pack(
        "border depth≤5", "border 短弧 · AFIFO≤5（相位错开）",
        "同 4 环并行，但注入相位错开把每个 AFIFO 压到 ≤5；router 仍零 buffer。",
        S.schedule_atomic(MX, True, 2, S.deliv_border, afifo_cap=5,
                          order="natural", record_events=True), quad_rings())
    r, rings = schedule_with_quads(OPTIMAL_RINGFOLLOW, S.deliv_ringfollow_quads)
    schemes["ringfollow_opt"] = pack(
        "ring-following 416cy", "ring-following · 环形状优化（416cy）",
        "4096 组扫描最优：makespan=416（比默认 508 快 92cy）；AFIFO 深度 8。"
        + cfg_str(OPTIMAL_RINGFOLLOW),
        r, rings)
    schemes["ringfollow"] = pack(
        "ring-following", "ring-following · AFIFO≤5（相位错开）",
        "外象限数据切入目的 8×8 环后绕整圈双向流转；AFIFO≤5，router 零 buffer。",
        S.schedule_atomic(MX, True, 2, S.deliv_ringfollow, afifo_cap=5,
                          order="quadblock", record_events=True), quad_rings())
    schemes["global1"] = pack(
        "global 单环", "4 环穿成 1 个全局环（无并行）",
        "四象限 Hamilton 环经边界 AFIFO 拼成一条全局环；天然零 buffer、AFIFO 深度仅 2，但放弃并行故最慢。",
        S.schedule(MX, True, 2, S.deliv_global, record_events=True), global_ring())

    cell, pad = 34, 44
    cfg = {
        "mx": MX, "my": MY, "cell": cell, "pad": pad,
        "W": pad * 2 + (MX - 1) * cell, "H": pad * 2 + (MY - 1) * cell,
        "n": MX * MY,
        "pos": [[pad + (i % MX) * cell, pad + (i // MX) * cell] for i in range(MX * MY)],
        "qmap": [fr.quad_of(i) for i in range(MX * MY)],
        "order": ["border_opt", "border_opt_lb", "border_fast", "border_fast_lb",
                  "border_d5", "ringfollow_opt", "ringfollow", "global1"],
        "default": "border_opt_lb",
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
.statgrid{display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:8px;font-size:12px;}
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
</style></head><body>
<header>
<h1>16×16 零 router-buffer AllGather · cycle-by-cycle</h1>
<p>4×(8×8 Hamilton 环)+ 跨界 AFIFO · H=4, V=6 · 双向, 下 ramp=2 ·
所有方案 <b>router 内零 buffer</b>（ring_buf=0, eject_buf=0），等待只发生在 AFIFO。
当前 makespan=<b id="hmk"></b> cy · 所需 AFIFO 深度=<b id="haf"></b></p>
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
<div>AFIFO 深度<b id="af">0</b></div>
<div>本 cycle 排队<b id="afnow">0</b></div>
</div>
<p class="note" id="note"></p>
</div>
</div>
<div>
<div class="panel"><h2>方案对比（均 router 零 buffer）</h2>
<table class="cmp" id="cmp"><thead><tr><th>方案</th><th>makespan</th><th>AFIFO 深度</th></tr></thead>
<tbody id="cmpbody"></tbody></table>
<p class="note">权衡：AFIFO 越深 → makespan 越小。240cy 固定路由 peak 45，8链路理想均摊下界 40；
266cy 固定 peak 11，理想均摊 5（≤5 预算）；相位错开 454cy。</p>
</div>
<div class="panel"><h2>AFIFO 深度曲线</h2>
<canvas id="afc" width="280" height="150" style="width:100%;max-width:300px;display:block;background:#f8fafc;border-radius:6px;border:1px solid #e2e8f0"></canvas>
<p class="note" id="aflbl">橙=固定路由全网峰值；紫虚线=8链路组内理想均摊下界；绿虚线=预算5。</p>
<button id="jumppeak" class="sec" style="margin-top:6px;font-size:12px;padding:5px 10px">跳到 AFIFO 峰值 cycle</button>
<button id="jumpbal" class="sec" style="margin-top:6px;font-size:12px;padding:5px 10px">跳到理想均摊峰值</button>
</div>
<div class="panel"><h2>Hamilton 环路径</h2>
<svg id="ringsvg" style="width:100%;max-width:280px;display:block;margin:0 auto"></svg>
<p class="note">彩色=各环走向；红虚线=象限边界（AFIFO）。圆点颜色=源节点，粗描边=本 cycle 正在 eject。</p>
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
function isCross(p,c){return D.qmap[p]!==D.qmap[c];}

// scheme dropdown + comparison table
const schemeSel=document.getElementById('scheme');
D.order.forEach(k=>{const o=document.createElement('option');o.value=k;
  o.textContent=D.schemes[k].label+'（'+D.schemes[k].makespan+'cy, AFIFO '+D.schemes[k].afifo_depth+'）';
  schemeSel.appendChild(o);});
schemeSel.value=key;
function buildCmp(){const tb=document.getElementById('cmpbody');tb.innerHTML='';
  D.order.forEach(k=>{const sc=D.schemes[k];const tr=document.createElement('tr');
    if(k===key)tr.className='on';
    tr.innerHTML='<td>'+sc.label+'</td><td>'+sc.makespan+'</td><td>'+sc.afifo_depth+'</td>';
    tb.appendChild(tr);});}

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
  const hw=D.mx/2,hh=D.my/2;
  [['M',pad,pad+hh*sc,'L',pad+(D.mx-1)*sc,pad+hh*sc],
   ['M',pad+hw*sc,pad,'L',pad+hw*sc,pad+(D.my-1)*sc]].forEach(a=>{
    const l=document.createElementNS(NS,'line');l.setAttribute('x1',a[1]);l.setAttribute('y1',a[2]);
    l.setAttribute('x2',a[3]);l.setAttribute('y2',a[4]);l.setAttribute('stroke','#dc2626');
    l.setAttribute('stroke-dasharray','4 3');l.setAttribute('stroke-width','1.4');svg.appendChild(l);});
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
  const mx=D.pad+(hw-0.5)*D.cell,my=D.pad+(hh-0.5)*D.cell;
  c.beginPath();c.moveTo(D.pad-10,my);c.lineTo(D.W-D.pad+10,my);c.stroke();
  c.beginPath();c.moveTo(mx,D.pad-10);c.lineTo(mx,D.H-D.pad+10);c.stroke();
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
}

function syncScheme(){
  S=D.schemes[key];staticCv=null;active.clear();cur=0;
  document.getElementById('mk').textContent=S.makespan;
  document.getElementById('mk2').textContent=S.makespan;
  document.getElementById('af').textContent=S.afifo_depth;
  document.getElementById('hmk').textContent=S.makespan;
  document.getElementById('haf').textContent=S.afifo_depth;
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
    sizes = {k: (v["makespan"], v["afifo_depth"]) for k, v in cfg["schemes"].items()}
    print(f"Wrote {OUT}")
    for k in cfg["order"]:
        mk, af = sizes[k]
        print(f"  {k:12s} makespan={mk:4d}  AFIFO_depth={af}")


if __name__ == "__main__":
    render()
