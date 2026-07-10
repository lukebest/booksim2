#!/usr/bin/env python3
"""Generate HTML report: 6×8 allgather ⊕ broadcast/gather zero-buffer superposition.

Output: results/report_superpose_6x8.html
"""

from __future__ import annotations

import html
import json
from pathlib import Path

import superpose_bcast_gather_6x8 as SP

ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "results" / "report_superpose_6x8.html"
JSON_PATH = ROOT / "results" / "superpose_6x8.json"

CSS = """
:root { --bg:#f8fafc; --card:#fff; --text:#0f172a; --muted:#64748b; --line:#e2e8f0; }
body { font-family: system-ui, -apple-system, sans-serif; margin:0; padding:28px 32px 64px;
       background:var(--bg); color:var(--text); line-height:1.65; max-width:1080px; }
h1 { font-size:1.55rem; margin:0 0 4px; }
h2 { font-size:1.15rem; margin:0 0 12px; color:#1e3a8a; }
h3 { font-size:1.0rem; margin:2px 0 8px; color:#334155; }
.sub { color:var(--muted); font-size:.92rem; margin:0 0 18px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:12px;
        padding:20px 24px; margin:16px 0; }
.card.hero { border-color:#c7d2fe; background:linear-gradient(180deg,#fbfcff,#fff); }
.lead { font-size:1.02rem; margin:0 0 14px; }
.note { color:var(--muted); font-size:.86rem; margin:8px 0 0; }
code { background:#f1f5f9; padding:1px 5px; border-radius:4px; font-size:.85em; }
table.data { border-collapse:collapse; font-size:.82rem; margin:6px 0; width:100%; }
table.data th, table.data td { border:1px solid var(--line); padding:6px 8px; text-align:center; }
table.data th { background:#f1f5f9; font-weight:600; }
table.data td.name { text-align:left; font-weight:600; }
table.data tr.best td { background:#ecfdf5; }
td.win { background:#dcfce7 !important; font-weight:700; }
td.warn { background:#fef3c7 !important; }
.dash { color:#cbd5e1; }
.tag { display:inline-block; font-size:.72rem; padding:1px 7px; border-radius:20px; }
.tag-ok { background:#dcfce7; color:#166534; }
.tag-no { background:#fee2e2; color:#991b1b; }
ul.compact { margin:6px 0; padding-left:20px; }
ul.compact li { margin:5px 0; }
.formula { font-family: ui-monospace, monospace; background:#f8fafc; border:1px solid var(--line);
           border-radius:6px; padding:9px 13px; margin:10px 0; font-size:.85rem; }
.clist { list-style:none; padding:0; margin:0; display:grid; gap:10px; }
.crow { display:grid; grid-template-columns:160px 1fr; gap:14px; align-items:start;
        padding:12px 14px; border:1px solid var(--line); border-radius:9px; background:#fff; }
.crow .h { font-weight:700; color:#1e3a8a; }
.crow .b { font-size:.9rem; color:#334155; }
.table-wrap { overflow-x:auto; }
@media (max-width:640px){ .crow{ grid-template-columns:1fr; } }
"""


def esc(s):
    return html.escape(str(s))


def fmt(v):
    return str(v) if v is not None else '<span class="dash">—</span>'


def pct(a, b):
    if a is None or b is None or b == 0:
        return "—"
    return f"{100.0 * a / b:.1f}%"


def build_html(data):
    mx, my = data["mx"], data["my"]
    h, v = data["h"], data["v"]
    root = data["root"]
    rb = data["ramp_bw"]
    flits = sorted(int(k) for k in data["ag_bcast"].keys())

    # --- solo table ---
    solo_rows = []
    for m in flits:
        s = data["solo"][m]
        best_ag = min((v for v in s["ag"].values() if v is not None), default=None)
        best_bc = min((v for v in s["bcast"].values() if v is not None), default=None)
        best_g = min((v for v in s["gather"].values() if v is not None), default=None)
        solo_rows.append(
            f"<tr><td>{m}</td>"
            f"<td>{fmt(s['ag_lb'])}</td><td>{fmt(best_ag)}</td>"
            f"<td>{fmt(s['bcast_lb'])}</td><td>{fmt(best_bc)}</td>"
            f"<td>{fmt(s['gather_lb'])}</td><td>{fmt(best_g)}</td>"
            f"<td>{fmt(s['ag_bcast_lb'])}</td><td>{fmt(s['ag_gather_lb'])}</td></tr>"
        )

    def combo_table(key, title):
        rows = []
        for m in flits:
            r = data[key][m]
            solo = data["solo"][m]
            best_ag = min((v for v in solo["ag"].values() if v is not None), default=None)
            extra_key = "bcast" if key == "ag_bcast" else "gather"
            best_ex = min((v for v in solo[extra_key].values() if v is not None), default=None)
            sum_solo = (best_ag + best_ex) if (best_ag and best_ex) else None
            mk = r.get("makespan")
            lb = r.get("lb")
            perfect = r.get("perfect")
            cls = ' class="best"' if perfect else ""
            scheme = f"{r.get('ag_scheme','—')}/{r.get('tree','—')}"
            mode = r.get("mode", "—")
            order = r.get("order", "—")
            gap = r.get("gap_to_lb")
            save = None
            if mk is not None and sum_solo is not None:
                save = sum_solo - mk
            tag = (
                '<span class="tag tag-ok">perfect overlap</span>'
                if perfect else
                '<span class="tag tag-no">gap to LB</span>'
            )
            rows.append(
                f"<tr{cls}><td>{m}</td>"
                f"<td>{fmt(lb)}</td>"
                f"<td class='win'>{fmt(mk)}</td>"
                f"<td>{fmt(gap)}</td>"
                f"<td>{fmt(sum_solo)}</td>"
                f"<td>{fmt(save)}</td>"
                f"<td>{pct(mk, sum_solo)}</td>"
                f"<td class='name'>{esc(scheme)}<br>"
                f"<span class='note'>{esc(mode)} · {esc(order)}</span></td>"
                f"<td>{tag}</td></tr>"
            )
        return f"""
        <div class="card">
          <h2>{esc(title)}</h2>
          <div class="table-wrap">
          <table class="data">
            <tr>
              <th>m</th><th>联合 LB</th><th>最优 makespan</th><th>gap</th>
              <th>单独之和</th><th>叠加节省</th><th>相对单独和</th>
              <th>最优方案</th><th>判定</th>
            </tr>
            {''.join(rows)}
          </table>
          </div>
        </div>"""

    # conclusions
    concl = []
    for m in flits:
        b = data["ag_bcast"][m]
        g = data["ag_gather"][m]
        solo = data["solo"][m]
        best_ag = min((v for v in solo["ag"].values() if v is not None), default=None)
        if b.get("makespan") is not None:
            vs_ag = b["makespan"] - best_ag if best_ag is not None else None
            save = (b.get("sum_lb") - b["makespan"]) if b.get("sum_lb") else None
            if vs_ag is not None and vs_ag <= 0:
                concl.append(
                    f"<li><b>m={m} AG⊕Bcast</b>: makespan=<code>{b['makespan']}</code> "
                    f"≤ 单独 AG(<code>{best_ag}</code>) — broadcast 完全藏进 AG 窗口"
                    f"（相对单独之和节省 <code>{save}</code>）。"
                    f"方案 <code>{b.get('ag_scheme')}/{b.get('tree')}</code> "
                    f"({b.get('mode')} · {esc(b.get('order'))}).</li>"
                )
            else:
                concl.append(
                    f"<li><b>m={m} AG⊕Bcast</b>: makespan=<code>{b['makespan']}</code>, "
                    f"LB=<code>{b['lb']}</code>, 相对 AG +<code>{vs_ag}</code>, "
                    f"相对单独之和节省 <code>{save}</code>; "
                    f"方案 <code>{b.get('ag_scheme')}/{b.get('tree')}</code> "
                    f"({b.get('mode')}).</li>"
                )
        if g.get("makespan") is not None:
            vs_ag = g["makespan"] - best_ag if best_ag is not None else None
            save = (g.get("sum_lb") - g["makespan"]) if g.get("sum_lb") else None
            root_eject_lb = 2 * (mx * my - 1) * m
            if vs_ag is not None and vs_ag <= 0:
                concl.append(
                    f"<li><b>m={m} AG⊕Gather</b>: makespan=<code>{g['makespan']}</code> "
                    f"= 单独 AG — gather 完全藏进 AG 窗口"
                    f"（root down 硬下界 <code>{root_eject_lb}</code> 仍有松弛）。"
                    f"方案 <code>{g.get('ag_scheme')}/{g.get('tree')}</code>.</li>"
                )
            else:
                concl.append(
                    f"<li><b>m={m} AG⊕Gather</b>: makespan=<code>{g['makespan']}</code>, "
                    f"LB=<code>{g['lb']}</code> (含 root down ≥<code>{root_eject_lb}</code>), "
                    f"相对 AG +<code>{vs_ag}</code>, "
                    f"相对单独之和节省 <code>{save}</code>; "
                    f"方案 <code>{g.get('ag_scheme')}/{g.get('tree')}</code> "
                    f"({g.get('mode')}).</li>"
                )

    body = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>6×8 AG ⊕ Bcast/Gather 零缓冲叠加</title>
<style>{CSS}</style></head><body>
<h1>6×8 Mesh：Allgather ⊕ Broadcast / Gather 零缓冲叠加分析</h1>
<p class="sub">Mesh {mx}×{my} · H={h} · V={v} · ramp_bw={rb} · root={root}
· 刚性足迹打包（无阻塞 / 无 router buffer / 无链路冲突）</p>

<div class="card hero">
  <p class="lead">在第一列节点 <code>({root[0]},{root[1]})</code> 上叠加
  <b>broadcast</b> 或 <b>gather</b> 与全网 <b>allgather</b>。
  使用与 <code>sched_zerobuf_compare</code> 相同的刚性 0-buffer 模型：
  每条有向链路 ≤1 flit/周期，ramp ≤ ramp_bw；唯一自由度是注入偏移。</p>
  <ul class="compact">
    <li><b>AG⊕Bcast</b>：allgather 的 N 个源足迹 + root 的一棵广播树足迹，联合打包。</li>
    <li><b>AG⊕Gather</b>：allgather 足迹 + (N−1) 条向 root 的单播 gather 足迹，联合打包。</li>
    <li>搜索空间：AG 方案 {{{', '.join(SP.AG_SCHEMES)}}} × 树形态
      {{{', '.join(SP.TREE_KINDS)}}} × 源顺序 × 交织/串行。</li>
  </ul>
</div>

<div class="card">
  <h2>理论下界</h2>
  <div class="formula">AG LB = max(eject, corner, latency, bisect)</div>
  <div class="formula">Bcast LB ≈ RAMP + max_manh(root,·) + (m−1) + RAMP</div>
  <div class="formula">Gather LB = max(⌈(N−1)m / ramp⌉, latency_to_root)</div>
  <div class="formula">AG⊕Bcast LB ≥ max(AG_LB, Bcast_LB, ⌈N·m / ramp⌉<sub>non-root down</sub>,
    ⌈2m / ramp⌉<sub>root up</sub>)</div>
  <div class="formula">AG⊕Gather LB ≥ max(AG_LB, Gather_LB, ⌈2(N−1)m / ramp⌉<sub>root down</sub>)
    &nbsp;— root 同时要收 AG 的 (N−1)m 与 gather 的 (N−1)m</div>
  <p class="note">若最优 makespan = 联合 LB → 完美叠加（free overlap）；
  否则 gap 量化叠加代价。串行单独之和是平凡上界。</p>
</div>

<div class="card">
  <h2>单独执行基线（m = 1..5）</h2>
  <div class="table-wrap">
  <table class="data">
    <tr>
      <th>m</th><th>AG LB</th><th>AG best</th>
      <th>Bcast LB</th><th>Bcast best</th>
      <th>Gather LB</th><th>Gather best</th>
      <th>AG⊕Bcast LB</th><th>AG⊕Gather LB</th>
    </tr>
    {''.join(solo_rows)}
  </table>
  </div>
</div>

{combo_table('ag_bcast', 'Allgather ⊕ Broadcast(root) — 最优叠加')}
{combo_table('ag_gather', 'Allgather ⊕ Gather(root) — 最优叠加')}

<div class="card">
  <h2>结论</h2>
  <ul class="clist">
    <li class="crow"><div class="h">可叠加性</div>
      <div class="b">两组组合均存在满足零缓冲 / 无冲突 / 无阻塞的刚性方案
      （打包器构造保证）。问题不是“能否叠加”，而是叠加后相对联合下界与
      单独之和的代价。</div></li>
    <li class="crow"><div class="h">AG ⊕ Broadcast</div>
      <div class="b">m=1 时 broadcast 可完全藏进 multitree AG 窗口（makespan 甚至略优于
      单独 AG，因 greedy 注入顺序被 bcast 扰动后更优）。m≥3 时最优常退化为
      串行（ring_bi AG + bcast），交织无法再压缩。</div></li>
    <li class="crow"><div class="h">AG ⊕ Gather</div>
      <div class="b">m=1 时 gather 亦可完全藏进 AG（makespan=单独 AG=170）。
      根节点 down-ramp 硬下界为 <b>2(N−1)m = 94m</b>；m=1 时 AG 窗口（170）
      仍有松弛故可 free-overlap。m≥3 时最优多为串行，因 ring_bi AG 已较紧，
      gather 的额外 (N−1)m root eject 无法再藏。</div></li>
  </ul>
  <ul class="compact" style="margin-top:14px">{''.join(concl)}</ul>
</div>

<div class="card">
  <h2>验证</h2>
  <ul class="compact">
    <li>链路占用 ≤1 / 周期；up/down ramp ≤ ramp_bw。</li>
    <li>AG⊕Bcast：每个非 root 节点 eject = (N−1)m + m；root eject = (N−1)m。</li>
    <li>AG⊕Gather：root eject = 2(N−1)m；其他节点 eject = (N−1)m。</li>
  </ul>
  <p class="note">数据源：<code>{esc(JSON_PATH.relative_to(ROOT))}</code> ·
  生成脚本 <code>utils/superpose_bcast_gather_6x8.py</code> /
  <code>utils/gen_superpose_6x8_report.py</code></p>
</div>
</body></html>"""
    return body


def main():
    SP.setup()
    if JSON_PATH.exists():
        print(f"Loading {JSON_PATH} ...", flush=True)
        with open(JSON_PATH, encoding="utf-8") as f:
            # JSON keys for m are strings
            raw = json.load(f)
        data = raw
        for section in ("ag_bcast", "ag_gather", "solo"):
            data[section] = {int(k): v for k, v in data[section].items()}
    else:
        print("Computing superposition schedules ...", flush=True)
        data = SP.compute()
        JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(SP._strip_busy(data), f, indent=2)
        print(f"Wrote {JSON_PATH}", flush=True)

    html_out = build_html(data)
    HTML_PATH.parent.mkdir(parents=True, exist_ok=True)
    HTML_PATH.write_text(html_out, encoding="utf-8")
    print(f"Wrote {HTML_PATH}")


if __name__ == "__main__":
    main()
