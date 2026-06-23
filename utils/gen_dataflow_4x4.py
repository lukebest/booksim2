#!/usr/bin/env python3
"""Generate a per-cycle dataflow animation (self-contained HTML) for the
dimensional multi-tree allgather on a small mesh (default 4x4).

Each flit is drawn as a colored dot (color = source) moving along its mesh link,
positioned by interpolating the link latency. A flit sent on link p->c at cycle
t with latency L occupies the link during [t, t+L) and ejects at c at t+L.
The schedule is the greedy E=0 (zero eject buffer) calendar with bounded per-hop
wait W and down-ramp bandwidth B; W=0 means no in-network buffering (flits flow
at constant speed -> cleanest animation).

Output: results/dataflow_4x4.html  (open in a browser; play / slider / source filter).
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path

from sched_no_eject_buffer import coord, nid, link_lat, tree_children

ROOT = Path(__file__).resolve().parents[1]


def schedule_events(mx, my, h, vv, ramp, wcap, bw):
    """Greedy E=0 schedule; return (makespan, flits, injects).

    flits: list of dicts {s, p, c, t, lat}  (t = send cycle on link p->c)
    injects: {s: inject_offset}
    """
    n = mx * my
    trees = {s: tree_children(s, mx, my) for s in range(n)}
    link_busy = defaultdict(set)
    down_cnt = defaultdict(lambda: defaultdict(int))
    cx0, cy0 = (mx - 1) / 2, (my - 1) / 2
    srcs = sorted(range(n),
                  key=lambda s: -(abs(coord(s, mx)[0] - cx0) + abs(coord(s, mx)[1] - cy0)))
    flits = []
    injects = {}
    makespan = 0
    for s in srcs:
        off = 0
        while True:
            tent_l, tent_d, tl = [], [], set()
            tent_dcnt = defaultdict(lambda: defaultdict(int))
            tent_flits = []
            avail = {s: off + ramp}
            order = [s]; qi = 0; ok = True
            while qi < len(order) and ok:
                p = order[qi]; qi += 1
                for c in trees[s][p]:
                    ready = avail[p]; lk = p * 100000 + c
                    lat = link_lat(p, c, mx, h, vv)
                    t = ready; found = False
                    while wcap is None or t - ready <= wcap:
                        if t not in link_busy[lk] and (lk, t) not in tl:
                            arrive = t + lat
                            if down_cnt[c][arrive] + tent_dcnt[c][arrive] < bw:
                                found = True; break
                        t += 1
                    if not found:
                        ok = False; break
                    tent_l.append((lk, t)); tl.add((lk, t))
                    tent_d.append((c, arrive)); tent_dcnt[c][arrive] += 1
                    tent_flits.append({"s": s, "p": p, "c": c, "t": t, "lat": lat})
                    avail[c] = arrive; order.append(c)
            if ok:
                for (lk, t) in tent_l:
                    link_busy[lk].add(t)
                for (d, ej) in tent_d:
                    down_cnt[d][ej] += 1
                    makespan = max(makespan, ej + ramp)
                flits.extend(tent_flits)
                injects[s] = off
                break
            off += 1
    return makespan, flits, injects


def schedule_events_cpsat(mx, my, h, vv, ramp, wcap, bw):
    """CP-SAT schedule honoring serialize-fork (router fan-out <=1/cycle).

    Reconstruct per-link flit send events from optimal arrival cycles:
    send(s, p->c) = a[s,c] - lat(p,c); ejects at c at a[s,c].
    """
    from sched_ilp import solve
    n = mx * my
    res = solve(mx, my, h, vv, ramp, wcap, horizon=12 * (mx + my) * bw,
                time_limit=120, workers=8, bw=bw, serfork=True)
    if res.get("arrivals") is None:
        raise SystemExit(f"CP-SAT serialize-fork infeasible/timeout at W={wcap} "
                         f"(try larger W; W>=3 needed for 4x4).")
    arr = res["arrivals"]
    trees = res["trees"]
    flits = []
    for s in range(n):
        for p, kids in trees[s].items():
            for c in kids:
                lat = link_lat(p, c, mx, h, vv)
                flits.append({"s": s, "p": p, "c": c,
                              "t": arr[(s, c)] - lat, "lat": lat})
    injects = {s: arr[(s, s)] - ramp for s in range(n)}
    return res["makespan"], flits, injects


def render(mx, my, h, vv, ramp, wcap, bw, out_path, serfork=False):
    if serfork:
        makespan, flits, injects = schedule_events_cpsat(mx, my, h, vv, ramp, wcap, bw)
    else:
        makespan, flits, injects = schedule_events(mx, my, h, vv, ramp, wcap, bw)
    n = mx * my
    # node positions for SVG
    cell = 120
    pad = 70
    W = pad * 2 + (mx - 1) * cell
    H = pad * 2 + (my - 1) * cell
    pos = {}
    for i in range(n):
        x, y = coord(i, mx)
        pos[i] = [pad + x * cell, pad + y * cell]

    cfg = {
        "mx": mx, "my": my, "h": h, "v": vv, "ramp": ramp,
        "bw": bw, "w": wcap, "makespan": makespan, "n": n,
        "pos": pos, "flits": flits, "injects": injects,
        "cell": cell, "pad": pad, "W": W, "H": H,
        "serfork": 1 if serfork else 0,
    }
    data = json.dumps(cfg)

    html = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>4x4 多树 AllGather 每-cycle 数据流</title>
<style>
 body{font-family:'Segoe UI',Arial,sans-serif;margin:18px;color:#0f172a;background:#f8fafc;}
 h1{color:#1e3a8a;font-size:20px;} .sub{color:#475569;font-size:13px;line-height:1.6;}
 #wrap{display:flex;gap:20px;flex-wrap:wrap;align-items:flex-start;}
 #panel{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:12px;}
 .ctl{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:10px 0;}
 button{background:#2563eb;color:#fff;border:0;border-radius:6px;padding:6px 14px;cursor:pointer;font-size:14px;}
 button:hover{background:#1d4ed8;} button.sec{background:#64748b;}
 input[type=range]{width:360px;} select,label{font-size:13px;}
 #cyc{font-weight:700;color:#1e3a8a;font-variant-numeric:tabular-nums;}
 #legend{display:flex;flex-wrap:wrap;gap:6px;max-width:380px;}
 .lg{font-size:11px;padding:2px 6px;border-radius:4px;color:#fff;cursor:pointer;opacity:.4;}
 .lg.on{opacity:1;}
 table{border-collapse:collapse;font-size:12px;} td,th{border:1px solid #cbd5e1;padding:3px 7px;}
</style></head><body>
<h1>4×4 双向维序多树 AllGather —— 每 cycle 数据流动</h1>
<p class="sub" id="meta"></p>
<div id="wrap">
 <div id="panel"><svg id="svg"></svg></div>
 <div id="panel" style="min-width:300px">
  <div class="ctl">
    <button id="play">▶ 播放</button>
    <button id="step" class="sec">单步 ▶|</button>
    <button id="reset" class="sec">⟲ 复位</button>
  </div>
  <div class="ctl">cycle <span id="cyc">0</span> / <span id="mk"></span></div>
  <div class="ctl"><input type="range" id="slider" min="0" value="0" step="1"></div>
  <div class="ctl">速度 <input type="range" id="speed" min="60" max="900" value="350" step="20"></div>
  <div class="ctl">源筛选
    <select id="src"><option value="-1">全部源</option></select>
  </div>
  <div class="ctl" style="font-size:12px;color:#475569">点击图例可切换显示对应源；圆点=飞行中的 flit，到达即 eject。</div>
  <div id="legend"></div>
  <h3 style="font-size:14px;margin:14px 0 4px;color:#1e3a8a">本 cycle 统计</h3>
  <table><tr><th>飞行中 flit</th><th>本 cycle eject 数</th></tr>
   <tr><td id="inflight">0</td><td id="ejcnt">0</td></tr></table>
 </div>
</div>
<script>
const D = __DATA__;
const NS="http://www.w3.org/2000/svg";
const svg=document.getElementById("svg");
svg.setAttribute("width",D.W); svg.setAttribute("height",D.H);
svg.setAttribute("viewBox",`0 0 ${D.W} ${D.H}`);
function hue(s){return `hsl(${Math.round(s*360/D.n)},70%,45%)`;}
function P(i){return D.pos[i];}

// static layer: faint physical links + nodes
for(let y=0;y<D.my;y++)for(let x=0;x<D.mx;x++){
  const i=x+D.mx*y;
  [[1,0],[0,1]].forEach(([dx,dy])=>{
    const nx=x+dx,ny=y+dy;
    if(nx<D.mx&&ny<D.my){const j=nx+D.mx*ny;
      const a=P(i),b=P(j);
      const l=document.createElementNS(NS,"line");
      l.setAttribute("x1",a[0]);l.setAttribute("y1",a[1]);
      l.setAttribute("x2",b[0]);l.setAttribute("y2",b[1]);
      l.setAttribute("stroke","#e2e8f0");l.setAttribute("stroke-width",6);
      svg.appendChild(l);
    }});
}
const nodeEls={};
for(let i=0;i<D.n;i++){const p=P(i);
  const ring=document.createElementNS(NS,"circle");
  ring.setAttribute("cx",p[0]);ring.setAttribute("cy",p[1]);ring.setAttribute("r",22);
  ring.setAttribute("fill","#fff");ring.setAttribute("stroke","#94a3b8");ring.setAttribute("stroke-width",3);
  svg.appendChild(ring);
  const t=document.createElementNS(NS,"text");
  t.setAttribute("x",p[0]);t.setAttribute("y",p[1]+4);t.setAttribute("text-anchor","middle");
  t.setAttribute("font-size",12);t.setAttribute("fill","#334155");
  const [cx,cy]=[i%D.mx,Math.floor(i/D.mx)];
  t.textContent=`${cx},${cy}`;
  svg.appendChild(t);
  nodeEls[i]=ring;
}
// dynamic layer
const dyn=document.createElementNS(NS,"g");svg.appendChild(dyn);

// controls
const slider=document.getElementById("slider");
slider.max=D.makespan;
document.getElementById("mk").textContent=D.makespan;
document.getElementById("meta").textContent=
  `配置: ${D.mx}×${D.my} mesh, H-link delay=${D.h}, V-link delay=${D.v}, down-ramp B=${D.bw} flit/cy, E=0, 每跳网内等待 W=${D.w == null ? "inf" : D.w}`
  + (D.serfork? ", serialize-fork: 每路由器每 cycle 至多转发 1 个 flit(fan-out≤1 端口)":"")
  + `; makespan=${D.makespan}`;
const srcSel=document.getElementById("src");
const legend=document.getElementById("legend");
const srcOn=new Array(D.n).fill(true);
for(let s=0;s<D.n;s++){
  const o=document.createElement("option");o.value=s;
  const [cx,cy]=[s%D.mx,Math.floor(s/D.mx)];o.textContent=`源 (${cx},${cy}) = ${s}`;srcSel.appendChild(o);
  const b=document.createElement("span");b.className="lg on";b.style.background=hue(s);
  b.textContent=`${cx},${cy}`;b.onclick=()=>{srcOn[s]=!srcOn[s];b.classList.toggle("on");draw(cur);};
  legend.appendChild(b);
}
let filter=-1;
srcSel.onchange=()=>{filter=parseInt(srcSel.value);draw(cur);};

function visible(s){ if(filter>=0) return s===filter; return srcOn[s]; }

function draw(k){
  cur=k;
  document.getElementById("cyc").textContent=k;
  slider.value=k;
  while(dyn.firstChild) dyn.removeChild(dyn.firstChild);
  let inflight=0, ej=0;
  const ejNodes={};
  for(const f of D.flits){
    if(!visible(f.s)) continue;
    if(k<f.t || k>f.t+f.lat) continue;
    const a=P(f.p), b=P(f.c);
    const frac=(k-f.t)/f.lat;
    if(k===f.t+f.lat){ // ejecting at c
      ej++; ejNodes[f.c]=hue(f.s);
    } else {
      inflight++;
    }
    const x=a[0]+(b[0]-a[0])*frac, y=a[1]+(b[1]-a[1])*frac;
    const d=document.createElementNS(NS,"circle");
    d.setAttribute("cx",x);d.setAttribute("cy",y);d.setAttribute("r",8);
    d.setAttribute("fill",hue(f.s));
    d.setAttribute("stroke","#fff");d.setAttribute("stroke-width",1.5);
    if(k===f.t+f.lat){d.setAttribute("r",6);d.setAttribute("opacity",.9);}
    dyn.appendChild(d);
  }
  // highlight ejecting nodes
  for(let i=0;i<D.n;i++){
    nodeEls[i].setAttribute("stroke", ejNodes[i]||"#94a3b8");
    nodeEls[i].setAttribute("stroke-width", ejNodes[i]?6:3);
  }
  document.getElementById("inflight").textContent=inflight;
  document.getElementById("ejcnt").textContent=ej;
}

let cur=0, timer=null;
slider.oninput=()=>draw(parseInt(slider.value));
document.getElementById("step").onclick=()=>draw(Math.min(cur+1,D.makespan));
document.getElementById("reset").onclick=()=>{stop();draw(0);};
function stop(){if(timer){clearInterval(timer);timer=null;document.getElementById("play").textContent="▶ 播放";}}
document.getElementById("play").onclick=()=>{
  if(timer){stop();return;}
  document.getElementById("play").textContent="⏸ 暂停";
  timer=setInterval(()=>{
    let nk=cur+1; if(nk>D.makespan){nk=0;}
    draw(nk);
  }, 1000 - (document.getElementById("speed").value));
};
document.getElementById("speed").oninput=()=>{ if(timer){stop();document.getElementById("play").click();} };
draw(0);
</script>
</body></html>"""
    html = html.replace("__DATA__", data)
    out_path.write_text(html, encoding="utf-8")
    print(f"Wrote {out_path}  (makespan={makespan}, flits={len(flits)})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mx", type=int, default=4)
    ap.add_argument("--my", type=int, default=4)
    ap.add_argument("--h", type=int, default=4)
    ap.add_argument("--v", type=int, default=6)
    ap.add_argument("--ramp", type=int, default=1)
    ap.add_argument("--bw", type=int, default=2)
    ap.add_argument("--w", type=int, default=0, help="per-hop wait cap (0=no in-network buffer)")
    ap.add_argument("--serfork", action="store_true",
                    help="serialize router fan-out (CP-SAT optimal; needs W>=3 on 4x4)")
    ap.add_argument("--out", default=str(ROOT / "results" / "dataflow_4x4.html"))
    args = ap.parse_args()
    render(args.mx, args.my, args.h, args.v, args.ramp, args.w, args.bw,
           Path(args.out), serfork=args.serfork)


if __name__ == "__main__":
    main()
