#!/usr/bin/env python3
"""Self-contained Chinese HTML report for 8x6 allreduce DSE."""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JSON_PATH = ROOT / "results" / "allreduce_8x6_dse.json"
HTML_PATH = ROOT / "results" / "report_allreduce_8x6.html"

SITE_LABEL = {
    "l1": "S1 L1/PE",
    "nic": "S2 NIC ALU",
    "router": "S3 router ALU",
    "none": "S4 无网内归约",
}


def esc(x) -> str:
    return html.escape(str(x))


def fmt_ratio(r) -> str:
    if r is None:
        return "—"
    return f"{r:.3f}"


def algo_and_uarch_html() -> str:
    """Algorithm schematics + router inline-ALU microarchitecture (inline SVG)."""
    return r"""
<style>
  .fig { background:#fafbfc; border:1px solid #e2e8f0; border-radius:8px;
         padding:16px; margin:12px 0 20px; overflow-x:auto; }
  .fig svg text { font-family: sans-serif; }
  .fig-cap { color:#555; font-size:12px; margin-top:8px; }
  .two-col { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
  @media (max-width:900px){ .two-col{ grid-template-columns:1fr; } }
  .step { margin:6px 0 6px 1.2em; }
</style>

<h2>§2.1 算法示意图与说明</h2>

<h3>A · 树 Reduce + 树 Broadcast</h3>
<p>两阶段：自底向上归约到 root，再自 root 多播结果。中间节点等齐子树 + 本地贡献后做一次 merge。</p>
<div class="fig">
<svg xmlns="http://www.w3.org/2000/svg" width="720" height="220" viewBox="0 0 720 220">
  <text x="120" y="18" font-size="13" font-weight="700">Phase 1 · Tree Reduce</text>
  <text x="480" y="18" font-size="13" font-weight="700">Phase 2 · Tree Broadcast</text>
  <!-- leaves -->
  <circle cx="40" cy="60" r="14" fill="#dbeafe" stroke="#3b82f6"/><text x="40" y="64" text-anchor="middle" font-size="10">L</text>
  <circle cx="100" cy="60" r="14" fill="#dbeafe" stroke="#3b82f6"/><text x="100" y="64" text-anchor="middle" font-size="10">L</text>
  <circle cx="180" cy="60" r="14" fill="#dbeafe" stroke="#3b82f6"/><text x="180" y="64" text-anchor="middle" font-size="10">L</text>
  <circle cx="240" cy="60" r="14" fill="#dbeafe" stroke="#3b82f6"/><text x="240" y="64" text-anchor="middle" font-size="10">L</text>
  <circle cx="70" cy="110" r="16" fill="#fef3c7" stroke="#d97706"/><text x="70" y="114" text-anchor="middle" font-size="10">⊕</text>
  <circle cx="210" cy="110" r="16" fill="#fef3c7" stroke="#d97706"/><text x="210" y="114" text-anchor="middle" font-size="10">⊕</text>
  <circle cx="140" cy="170" r="20" fill="#bbf7d0" stroke="#16a34a"/><text x="140" y="174" text-anchor="middle" font-size="11">ROOT</text>
  <line x1="40" y1="74" x2="60" y2="96" stroke="#64748b"/>
  <line x1="100" y1="74" x2="80" y2="96" stroke="#64748b"/>
  <line x1="180" y1="74" x2="200" y2="96" stroke="#64748b"/>
  <line x1="240" y1="74" x2="220" y2="96" stroke="#64748b"/>
  <line x1="70" y1="126" x2="125" y2="152" stroke="#64748b"/>
  <line x1="210" y1="126" x2="155" y2="152" stroke="#64748b"/>
  <text x="40" y="200" font-size="10" fill="#666">local → root</text>
  <!-- bcast -->
  <circle cx="500" cy="50" r="20" fill="#bbf7d0" stroke="#16a34a"/><text x="500" y="54" text-anchor="middle" font-size="11">ROOT</text>
  <circle cx="440" cy="110" r="14" fill="#e9d5ff" stroke="#7c3aed"/><text x="440" y="114" text-anchor="middle" font-size="9">fork</text>
  <circle cx="560" cy="110" r="14" fill="#e9d5ff" stroke="#7c3aed"/><text x="560" y="114" text-anchor="middle" font-size="9">fork</text>
  <circle cx="400" cy="170" r="12" fill="#dbeafe" stroke="#3b82f6"/><text x="400" y="174" text-anchor="middle" font-size="9">D</text>
  <circle cx="460" cy="170" r="12" fill="#dbeafe" stroke="#3b82f6"/><text x="460" y="174" text-anchor="middle" font-size="9">D</text>
  <circle cx="540" cy="170" r="12" fill="#dbeafe" stroke="#3b82f6"/><text x="540" y="174" text-anchor="middle" font-size="9">D</text>
  <circle cx="600" cy="170" r="12" fill="#dbeafe" stroke="#3b82f6"/><text x="600" y="174" text-anchor="middle" font-size="9">D</text>
  <line x1="490" y1="70" x2="450" y2="96" stroke="#64748b"/>
  <line x1="510" y1="70" x2="550" y2="96" stroke="#64748b"/>
  <line x1="430" y1="124" x2="405" y2="158" stroke="#64748b"/>
  <line x1="450" y1="124" x2="455" y2="158" stroke="#64748b"/>
  <line x1="550" y1="124" x2="545" y2="158" stroke="#64748b"/>
  <line x1="570" y1="124" x2="595" y2="158" stroke="#64748b"/>
  <path d="M280 170 C320 170 340 170 360 110" stroke="#94a3b8" stroke-dasharray="4 3" fill="none"/>
  <text x="300" y="130" font-size="10" fill="#64748b">result</text>
</svg>
<p class="fig-cap">黄 ⊕ = merge（站点代价 S3=5 / S1·S2=15）；绿 ROOT；蓝 = 叶/目的。维序树 bcast + CalFork 扇出。</p>
</div>
<ol class="step">
<li>叶经上 ramp 注入；中间节点凑齐扇入后 merge，再发往 parent。</li>
<li>Root 就绪后切 bcast 日历；每点下 ramp 收 m 个结果 flit。</li>
<li>优势：延迟路径≈树高×(边+merge)；劣势：root 扇入串行 + 两 phase 间隙。</li>
</ol>

<h3>B · 双树拆分 m</h3>
<p>将 m 拆成两半，各走一棵树，理想边不相交并行以逼近 ⌈m/2⌉ 串行化项。</p>
<div class="fig">
<svg width="640" height="140" viewBox="0 0 640 140" xmlns="http://www.w3.org/2000/svg">
  <rect x="20" y="20" width="280" height="100" rx="8" fill="#eff6ff" stroke="#3b82f6"/>
  <text x="160" y="42" text-anchor="middle" font-size="12" font-weight="700">Tree A · flits 0..⌊m/2⌋−1</text>
  <circle cx="80" cy="80" r="12" fill="#dbeafe" stroke="#3b82f6"/>
  <circle cx="140" cy="80" r="12" fill="#dbeafe" stroke="#3b82f6"/>
  <circle cx="200" cy="80" r="14" fill="#bbf7d0" stroke="#16a34a"/>
  <text x="200" y="84" text-anchor="middle" font-size="9">rA</text>
  <line x1="92" y1="80" x2="186" y2="80" stroke="#64748b"/>
  <line x1="152" y1="80" x2="186" y2="80" stroke="#64748b"/>
  <rect x="340" y="20" width="280" height="100" rx="8" fill="#f5f3ff" stroke="#7c3aed"/>
  <text x="480" y="42" text-anchor="middle" font-size="12" font-weight="700">Tree B · flits ⌊m/2⌋..m−1</text>
  <circle cx="400" cy="80" r="12" fill="#e9d5ff" stroke="#7c3aed"/>
  <circle cx="460" cy="80" r="12" fill="#e9d5ff" stroke="#7c3aed"/>
  <circle cx="520" cy="80" r="14" fill="#bbf7d0" stroke="#16a34a"/>
  <text x="520" y="84" text-anchor="middle" font-size="9">rB</text>
  <line x1="412" y1="80" x2="506" y2="80" stroke="#64748b"/>
  <line x1="472" y1="80" x2="506" y2="80" stroke="#64748b"/>
  <text x="320" y="75" text-anchor="middle" font-size="11" fill="#64748b">∥</text>
</svg>
<p class="fig-cap">本次 DSE 以顺序拼接为安全上界；真正边不相交并行是缩小 vs T_LB gap 的主杠杆。</p>
</div>

<h3>C · 维度化 RS + AG（需 m%8==0）</h3>
<p>行内 reduce-scatter（chunk=m/8）→ 列向 RS/树 → 反向 allgather。</p>
<div class="fig">
<svg width="700" height="160" viewBox="0 0 700 160" xmlns="http://www.w3.org/2000/svg">
  <text x="10" y="24" font-size="12" font-weight="700">① Row RS-X</text>
  <g font-size="10">
    <rect x="10" y="40" width="36" height="28" rx="4" fill="#dbeafe" stroke="#3b82f6"/><text x="28" y="58" text-anchor="middle">n0</text>
    <rect x="60" y="40" width="36" height="28" rx="4" fill="#dbeafe" stroke="#3b82f6"/><text x="78" y="58" text-anchor="middle">n1</text>
    <rect x="110" y="40" width="36" height="28" rx="4" fill="#dbeafe" stroke="#3b82f6"/><text x="128" y="58" text-anchor="middle">n2</text>
    <text x="160" y="58">…</text>
    <rect x="180" y="40" width="36" height="28" rx="4" fill="#dbeafe" stroke="#3b82f6"/><text x="198" y="58" text-anchor="middle">n7</text>
  </g>
  <path d="M46 54 H58" stroke="#0f172a" marker-end="url(#arrow)"/>
  <path d="M96 54 H108" stroke="#0f172a"/>
  <path d="M146 54 H178" stroke="#0f172a"/>
  <text x="100" y="88" font-size="10" fill="#666">每步搬运 chunk 并 ⊕</text>
  <text x="280" y="24" font-size="12" font-weight="700">② Col Y</text>
  <rect x="280" y="40" width="100" height="70" rx="6" fill="#fef3c7" stroke="#d97706"/>
  <text x="330" y="70" text-anchor="middle" font-size="11">RS-Y 或</text>
  <text x="330" y="88" text-anchor="middle" font-size="11">tree reduce</text>
  <text x="430" y="24" font-size="12" font-weight="700">③④ AG</text>
  <rect x="430" y="40" width="240" height="70" rx="6" fill="#bbf7d0" stroke="#16a34a"/>
  <text x="550" y="70" text-anchor="middle" font-size="11">Col AG → Row AG</text>
  <text x="550" y="90" text-anchor="middle" font-size="11">每节点持有完整结果</text>
  <path d="M230 54 H275" stroke="#94a3b8" stroke-dasharray="4 2"/>
  <path d="M385 75 H425" stroke="#94a3b8" stroke-dasharray="4 2"/>
</svg>
<p class="fig-cap">m=32/200 适用；m=1/13 标记 applicable=false。大 m 时击败单 root 树。</p>
</div>

<h3>D · Hamilton Ring RS + AG</h3>
<p>沿蛇形哈密顿环做 RS 再 AG。步数≈N−1，延迟主导区仅作证否基线。</p>
<div class="fig">
<svg width="640" height="100" viewBox="0 0 640 100" xmlns="http://www.w3.org/2000/svg">
  <path d="M40 50 H560" stroke="#64748b" fill="none" stroke-width="2"/>
  <path d="M560 50 Q600 50 600 30 Q600 10 560 10 H40 Q20 10 20 30 Q20 50 40 50"
        stroke="#64748b" fill="none" stroke-width="2" stroke-dasharray="0"/>
  <circle cx="40" cy="50" r="10" fill="#bbf7d0" stroke="#16a34a"/><text x="40" y="54" text-anchor="middle" font-size="9">0</text>
  <circle cx="120" cy="50" r="10" fill="#dbeafe" stroke="#3b82f6"/><text x="120" y="54" text-anchor="middle" font-size="9">1</text>
  <circle cx="200" cy="50" r="10" fill="#dbeafe" stroke="#3b82f6"/><text x="200" y="54" text-anchor="middle" font-size="9">2</text>
  <text x="300" y="54" font-size="12" fill="#64748b">…</text>
  <circle cx="400" cy="50" r="10" fill="#dbeafe" stroke="#3b82f6"/><text x="400" y="54" text-anchor="middle" font-size="9">i</text>
  <circle cx="480" cy="50" r="10" fill="#fef3c7" stroke="#d97706"/><text x="480" y="54" text-anchor="middle" font-size="9">⊕</text>
  <circle cx="560" cy="50" r="10" fill="#dbeafe" stroke="#3b82f6"/><text x="560" y="54" text-anchor="middle" font-size="9">47</text>
  <text x="320" y="90" text-anchor="middle" font-size="11" fill="#555">每步：边延迟 + merge；双向可折半环长</text>
</svg>
</div>

<h3>E · Allgather 型（S4，无网内归约）</h3>
<p>每源多播自己的 m flit；PE 本地做 N 路归约。网络 = allgather，弹出界 ⌈47m/2⌉。</p>
<div class="fig">
<svg width="640" height="130" viewBox="0 0 640 130" xmlns="http://www.w3.org/2000/svg">
  <rect x="20" y="20" width="300" height="90" rx="8" fill="#eff6ff" stroke="#3b82f6"/>
  <text x="170" y="42" text-anchor="middle" font-size="12" font-weight="700">Allgather trees</text>
  <circle cx="70" cy="75" r="12" fill="#dbeafe" stroke="#3b82f6"/><text x="70" y="79" text-anchor="middle" font-size="9">s0</text>
  <circle cx="130" cy="75" r="12" fill="#dbeafe" stroke="#3b82f6"/><text x="130" y="79" text-anchor="middle" font-size="9">s1</text>
  <circle cx="190" cy="75" r="12" fill="#dbeafe" stroke="#3b82f6"/><text x="190" y="79" text-anchor="middle" font-size="9">…</text>
  <circle cx="250" cy="75" r="12" fill="#dbeafe" stroke="#3b82f6"/><text x="250" y="79" text-anchor="middle" font-size="9">s47</text>
  <path d="M330 65 H380" stroke="#94a3b8" stroke-dasharray="4 2"/>
  <rect x="380" y="20" width="240" height="90" rx="8" fill="#fef3c7" stroke="#d97706"/>
  <text x="500" y="50" text-anchor="middle" font-size="12" font-weight="700">PE local</text>
  <text x="500" y="75" text-anchor="middle" font-size="11">recv (N−1)·m → ⊕ → m</text>
  <text x="500" y="95" text-anchor="middle" font-size="10" fill="#666">COMPUTE=5</text>
</svg>
<p class="fig-cap">m=1 有竞争力（本研究 139 cy 最优）；大 m 被 L5 打爆。</p>
</div>

<h2>§2.2 Router 内联 ALU（S3）微架构</h2>
<p>在 Arch-A5（SparseCal + CalFork + SharedPool）日历路径上增加 inline reduce 的草图。
ALU 挂在日历命中路径、CalFork 之前；结果不进 L1、不进 SharedPool。</p>

<div class="fig">
<svg width="760" height="280" viewBox="0 0 760 280" xmlns="http://www.w3.org/2000/svg">
  <text x="380" y="18" text-anchor="middle" font-size="13" font-weight="700">Router datapath with Inline Reduce</text>
  <rect x="20" y="40" width="90" height="200" rx="8" fill="#dbeafe" stroke="#3b82f6"/>
  <text x="65" y="140" text-anchor="middle" font-size="11">5× Ingress</text>
  <text x="65" y="158" text-anchor="middle" font-size="10">N E S W L</text>

  <rect x="140" y="50" width="130" height="50" rx="6" fill="#e2e8f0" stroke="#475569"/>
  <text x="205" y="80" text-anchor="middle" font-size="11">SparseCal match</text>

  <path d="M205 100 V120" stroke="#64748b"/>
  <rect x="150" y="120" width="110" height="36" rx="6" fill="#fef3c7" stroke="#d97706"/>
  <text x="205" y="143" text-anchor="middle" font-size="11">opcode decode</text>

  <!-- FORWARD -->
  <path d="M260 138 H320" stroke="#16a34a" stroke-width="1.5"/>
  <text x="280" y="130" font-size="9" fill="#16a34a">FORWARD</text>

  <!-- REDUCE -->
  <path d="M205 156 V180" stroke="#d97706"/>
  <rect x="140" y="180" width="130" height="50" rx="6" fill="#ffedd5" stroke="#ea580c"/>
  <text x="205" y="200" text-anchor="middle" font-size="11">Acc SRAM</text>
  <text x="205" y="216" text-anchor="middle" font-size="9">(reduce_id, flit_idx)</text>

  <path d="M270 205 H320" stroke="#ea580c"/>
  <rect x="320" y="175" width="120" height="60" rx="6" fill="#fecaca" stroke="#dc2626"/>
  <text x="380" y="200" text-anchor="middle" font-size="11">Reduce ALU</text>
  <text x="380" y="218" text-anchor="middle" font-size="9">depth=COMPUTE=5</text>

  <path d="M440 205 H500" stroke="#dc2626"/>
  <path d="M440 205 V100 H500" stroke="#dc2626" fill="none"/>

  <rect x="500" y="70" width="110" height="50" rx="6" fill="#e9d5ff" stroke="#7c3aed"/>
  <text x="555" y="100" text-anchor="middle" font-size="11">CalFork mask</text>

  <rect x="500" y="160" width="110" height="50" rx="6" fill="#f1f5f9" stroke="#64748b"/>
  <text x="555" y="182" text-anchor="middle" font-size="10">SharedPool BG</text>
  <text x="555" y="198" text-anchor="middle" font-size="9">(miss / demote)</text>
  <path d="M205 50 H205 40 H555 40 V70" stroke="#94a3b8" stroke-dasharray="3 2" fill="none"/>
  <text x="360" y="36" font-size="9" fill="#64748b">miss</text>

  <rect x="640" y="100" width="100" height="80" rx="8" fill="#bbf7d0" stroke="#16a34a"/>
  <text x="690" y="140" text-anchor="middle" font-size="11">5×5 Xbar</text>
  <text x="690" y="158" text-anchor="middle" font-size="10">→ Egress</text>
  <path d="M610 95 H640" stroke="#7c3aed"/>
  <path d="M610 185 H640 185 V160" stroke="#64748b"/>
</svg>
<p class="fig-cap">橙路径 = inline reduce；紫 = CalFork 多播；灰 = BG。与 S1 比：零次 ramp 往返。</p>
</div>

<div class="two-col">
<div>
<h3>日历 opcode（概念）</h3>
<table>
<thead><tr><th>opcode</th><th>行为</th></tr></thead>
<tbody>
<tr><td><code>FORWARD</code></td><td>今日 CalFork 路径，不碰 ALU</td></tr>
<tr><td><code>REDUCE_ACC</code></td><td>累加进 Acc，不转发</td></tr>
<tr><td><code>REDUCE_FWD</code></td><td>凑齐 expect_n 后 ALU→按 mask 转发</td></tr>
<tr><td><code>BCAST</code></td><td>root 结果多播扇出</td></tr>
</tbody>
</table>
</div>
<div>
<h3>流水代价</h3>
<ul>
<li>非流水：单级 <code>COMPUTE · m</code></li>
<li>流水：单级 <code>COMPUTE + m − 1</code></li>
<li>Acc 按 <code>(reduce_id, flit_idx)</code> 索引</li>
<li>日历保证各子树 flit_k 到达窗可重叠</li>
</ul>
</div>
</div>

<h3>Inline Reduce 时序（中间节点扇入=2）</h3>
<div class="fig">
<svg width="700" height="150" viewBox="0 0 700 150" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M0 0 L10 5 L0 10 z" fill="#334155"/>
    </marker>
  </defs>
  <text x="20" y="24" font-size="11" fill="#64748b">cycle →</text>
  <line x1="80" y1="40" x2="680" y2="40" stroke="#cbd5e1"/>
  <!-- child0 -->
  <rect x="100" y="55" width="70" height="22" rx="3" fill="#dbeafe" stroke="#3b82f6"/>
  <text x="135" y="70" text-anchor="middle" font-size="10">child0 flit_k</text>
  <!-- child1 -->
  <rect x="160" y="85" width="70" height="22" rx="3" fill="#dbeafe" stroke="#3b82f6"/>
  <text x="195" y="100" text-anchor="middle" font-size="10">child1 flit_k</text>
  <!-- local -->
  <rect x="190" y="115" width="70" height="22" rx="3" fill="#e0e7ff" stroke="#4f46e5"/>
  <text x="225" y="130" text-anchor="middle" font-size="10">local flit_k</text>
  <!-- ALU -->
  <rect x="280" y="75" width="120" height="40" rx="4" fill="#fecaca" stroke="#dc2626"/>
  <text x="340" y="92" text-anchor="middle" font-size="11">ALU fill 5 cy</text>
  <text x="340" y="108" text-anchor="middle" font-size="9">pipelined after</text>
  <path d="M230 66 H278" stroke="#334155" marker-end="url(#arrow)"/>
  <path d="M230 96 H278" stroke="#334155" marker-end="url(#arrow)"/>
  <path d="M260 126 H300 126 V115" stroke="#334155" marker-end="url(#arrow)"/>
  <!-- out -->
  <rect x="440" y="80" width="120" height="30" rx="4" fill="#bbf7d0" stroke="#16a34a"/>
  <text x="500" y="100" text-anchor="middle" font-size="11">FORWARD to parent</text>
  <path d="M400 95 H438" stroke="#334155" marker-end="url(#arrow)"/>
  <text x="580" y="100" font-size="10" fill="#666">不进 PE</text>
</svg>
<p class="fig-cap">零缓冲：到达时刻由日历排定；Acc 凑齐 expect_n 后启动 ALU。</p>
</div>

<h3>端到端树上的 inline reduce 流程</h3>
<ol>
<li><b>Leaf：</b>仅 <code>FORWARD</code>，ALU 不激活。</li>
<li><b>Intermediate：</b>日历下发 <code>REDUCE_*</code>；Acc 凑齐扇入 → ALU → partial 立刻发往 parent（<b>数据不进 PE</b>）。</li>
<li><b>Root：</b>最终 reduce 后切 bcast 日历；CalFork 扇出；各节点下 ramp 交付 m flit。</li>
<li><b>迟到/错口：</b>仍走 watchdog → SharedPool escape；reduce 上下文超时则软件回退。</li>
</ol>

<table>
<thead><tr><th>站点</th><th>单级关键路径</th><th>占 ramp BW</th><th>DSE m=13 最优</th></tr></thead>
<tbody>
<tr><td>S3 router inline</td><td>wire → Acc → ALU(5) → wire</td><td>否</td><td><b>257</b></td></tr>
<tr><td>S2 NIC</td><td>wire → ramp 域 → ALU → 回网络</td><td>否（不占 L1）</td><td>327</td></tr>
<tr><td>S1 L1</td><td>wire → 下 ramp → PE → 上 ramp</td><td>是</td><td>344</td></tr>
<tr><td>S4 none</td><td>全量 allgather</td><td>弹出 (N−1)m</td><td>887</td></tr>
</tbody>
</table>
"""


def bar_svg(rows: list[tuple[str, float, str]], width=420, height=None) -> str:
    """Horizontal bar chart; rows = (label, value, color)."""
    if not rows:
        return ""
    h = height or (28 * len(rows) + 20)
    max_v = max(v for _, v, _ in rows) or 1
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{h}" '
        f'viewBox="0 0 {width} {h}">'
    ]
    for i, (lab, val, color) in enumerate(rows):
        y = 10 + i * 28
        bw = int((width - 160) * val / max_v)
        parts.append(
            f'<text x="4" y="{y + 14}" font-size="12" font-family="sans-serif">'
            f"{esc(lab)}</text>"
        )
        parts.append(
            f'<rect x="150" y="{y}" width="{bw}" height="18" fill="{color}" rx="2"/>'
        )
        parts.append(
            f'<text x="{152 + bw}" y="{y + 14}" font-size="11" '
            f'font-family="sans-serif">{val:.0f}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts)


def main():
    data = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    p = data["params"]
    gen = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Bounds table
    bound_rows = []
    for b in data["bounds_table"]:
        bound_rows.append(
            "<tr>"
            f"<td>{b['m']}</td>"
            f"<td>{b['L1_causal']}</td>"
            f"<td>{b['L2_inject']}</td>"
            f"<td>{b['L3_eject_final']}</td>"
            f"<td>{b['L4_corner_cut']}</td>"
            f"<td>{b['L5_ag_eject']}</td>"
            f"<td><b>{b['T_LB']}</b></td>"
            f"<td>{b['site_LB']['l1']}</td>"
            f"<td>{b['site_LB']['router']}</td>"
            f"<td>{b['site_LB']['none']}</td>"
            "</tr>"
        )

    # Best per m
    best_rows = []
    best_bars = []
    colors = {"none": "#5b8ff9", "router": "#61ddaa", "l1": "#f6bd16", "nic": "#7262fd"}
    for m, rec in data["best_per_m"].items():
        if not rec:
            continue
        best_rows.append(
            "<tr>"
            f"<td>{esc(m)}</td>"
            f"<td>{esc(SITE_LABEL.get(rec['site'], rec['site']))}</td>"
            f"<td>{esc(rec['scheme'])}</td>"
            f"<td>{rec['makespan']}</td>"
            f"<td>{rec['T_LB']}</td>"
            f"<td>{fmt_ratio(rec['ratio_vs_ideal'])}</td>"
            f"<td>{'是' if rec['pipelined'] else '否'}</td>"
            "</tr>"
        )
        best_bars.append(
            (f"m={m} {rec['site']}", rec["makespan"], colors.get(rec["site"], "#999"))
        )

    # Site rank tables
    site_sections = []
    for m, rows in data["site_rank_per_m"].items():
        trs = []
        bars = []
        for i, r in enumerate(rows):
            trs.append(
                "<tr>"
                f"<td>{i + 1}</td>"
                f"<td>{esc(SITE_LABEL.get(r['site'], r['site']))}</td>"
                f"<td>{esc(r['scheme'])}</td>"
                f"<td>{r['makespan']}</td>"
                f"<td>{fmt_ratio(r['ratio_vs_ideal'])}</td>"
                f"<td>{'流水' if r['pipelined'] else '非流水'}</td>"
                "</tr>"
            )
            bars.append(
                (SITE_LABEL.get(r["site"], r["site"]), r["makespan"],
                 colors.get(r["site"], "#999"))
            )
        site_sections.append(
            f"<h3>m = {esc(m)}</h3>"
            f"{bar_svg(bars)}"
            "<table><thead><tr><th>名次</th><th>站点</th><th>方案</th>"
            "<th>makespan</th><th>相对理想 LB</th><th>ALU</th></tr></thead>"
            f"<tbody>{''.join(trs)}</tbody></table>"
        )

    # Pipelined vs not for router site
    pipe_rows = []
    last_cells = data["ticks"][-1]["cells"]
    for m in p["message_flits"]:
        pipe_mk = non_mk = None
        for c in last_cells:
            if c["m"] != m or c["site"] != "router" or not c.get("best"):
                continue
            if c["pipelined"]:
                pipe_mk = c["best"]["makespan"]
            else:
                non_mk = c["best"]["makespan"]
        if pipe_mk is not None:
            if non_mk is not None:
                pipe_rows.append(
                    f"<tr><td>{m}</td><td>{pipe_mk}</td>"
                    f"<td>{non_mk}</td><td>{(non_mk / pipe_mk):.2f}×</td></tr>"
                )
            else:
                pipe_rows.append(
                    f"<tr><td>{m}</td><td>{pipe_mk}</td><td>—</td><td>—</td></tr>"
                )

    # Scheme matrix (tick last, pipelined)
    matrix_sections = []
    for m in p["message_flits"]:
        trs = []
        for c in last_cells:
            if c["m"] != m:
                continue
            if c["site"] != "none" and not c["pipelined"]:
                continue
            for s in c["schemes"]:
                if s.get("name") == "tree_best":
                    continue
                if s.get("name", "").startswith("dual_tree") and s.get("makespan") is None:
                    if s.get("reason") == "m<2 cannot split":
                        continue
                mk = s.get("makespan")
                trs.append(
                    "<tr>"
                    f"<td>{esc(SITE_LABEL.get(c['site'], c['site']))}</td>"
                    f"<td>{esc(s.get('algo', ''))}</td>"
                    f"<td>{esc(s.get('name', ''))}</td>"
                    f"<td>{mk if mk is not None else '—'}</td>"
                    f"<td>{'✓' if s.get('ok') else '✗'}</td>"
                    f"<td>{esc(s.get('method', s.get('reason', '')))}</td>"
                    "</tr>"
                )
        matrix_sections.append(
            f"<h3>m = {m}（pipelined；S4 仅一次）</h3>"
            "<table><thead><tr><th>站点</th><th>算法</th><th>方案</th>"
            "<th>makespan</th><th>ok</th><th>备注</th></tr></thead>"
            f"<tbody>{''.join(trs)}</tbody></table>"
        )

    notes = "".join(f"<li>{esc(n)}</li>" for n in data.get("notes", []))

    body = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<title>8×6 Allreduce DSE 报告</title>
<style>
  body {{ font-family: "Source Han Sans", "Noto Sans CJK SC", sans-serif;
         margin: 24px 40px; color: #222; line-height: 1.55; max-width: 1100px; }}
  h1,h2,h3 {{ color: #111; }}
  h1 {{ border-bottom: 3px solid #111; padding-bottom: 8px; }}
  h2 {{ margin-top: 2em; border-left: 4px solid #5b8ff9; padding-left: 10px; }}
  table {{ border-collapse: collapse; margin: 12px 0 24px; font-size: 13px; }}
  th, td {{ border: 1px solid #ccc; padding: 6px 10px; text-align: right; }}
  th {{ background: #f4f6f8; text-align: center; }}
  td:nth-child(1), td:nth-child(2), td:nth-child(3) {{ text-align: left; }}
  code {{ background: #f0f0f0; padding: 1px 4px; border-radius: 3px; }}
  .meta {{ color: #666; font-size: 13px; }}
  .callout {{ background: #f7fafc; border: 1px solid #d0e3f0; padding: 12px 16px;
              border-radius: 6px; margin: 16px 0; }}
  .warn {{ background: #fff8e6; border-color: #f0d78c; }}
  footer {{ margin-top: 48px; color: #888; font-size: 12px; border-top: 1px solid #ddd;
            padding-top: 12px; }}
</style>
</head>
<body>
<h1>8×6 Mesh Allreduce 最优方案探索</h1>
<p class="meta">
几何：{p['mx']}×{p['my']}，H={p['h']}，V={p['v']}，直径={p['diameter']}；
RAMP={p['ramp']}，RAMP_BW={p['ramp_bw']} flits/cycle；COMPUTE={p['compute']}；
m ∈ {p['message_flits']}<br/>
loop_status=<b>{esc(data['loop_status'])}</b>，ticks={data['n_ticks']}；
生成 {esc(gen)}
</p>

<div class="callout">
<b>核心结论</b>
<ul>
<li>理想下界 <code>T_LB = 108 + ⌈m/2⌉</code>：m=1→109，13→115，32→124，200→208。
到 m=200 仍是<strong>延迟主导</strong>（固定 108 占 52%）。</li>
<li>m=1 最优：<strong>S4 无网内归约 + allgather</strong>（139 cy，1.275×LB）——单 phase、无 root 串行。</li>
<li>m∈{{13,32,200}} 最优：<strong>S3 router 内联 ALU + 流水</strong>；
小消息用中心树 reduce+bcast，大消息且 m%8==0 用维度 RS+AG。</li>
<li>S1 L1 归约在小消息上因每级 15 cy 往返显著劣于 S3；S4 在大消息被
<code>⌈47m/2⌉</code> 弹出界打爆。</li>
</ul>
</div>

<h2>§1 硬约束与模型</h2>
<ul>
<li>有向 mesh 链路 ≤1 flit/cycle；上下 ramp 各 ≤{p['ramp_bw']} flits/cycle，独立。</li>
<li>零缓冲刚性调度（沿用 <code>sched_zerobuf_compare</code>）；makespan 为可行上界，非最优性证明。</li>
<li>下界为证明性：因果直径 + 注入/弹出 + 割 +（S4）全量 allgather 弹出。</li>
</ul>

<h2>§2 Reduce 硬件位置</h2>
<table>
<thead><tr><th>站点</th><th>ramp_crossings</th><th>uses_ramp_bw</th><th>单级代价 (m=1)</th><th>说明</th></tr></thead>
<tbody>
<tr><td>S1 L1/PE</td><td>2</td><td>是</td><td>15</td><td>下 ramp→计算→上 ramp</td></tr>
<tr><td>S2 NIC ALU</td><td>2</td><td>否</td><td>15</td><td>跨 ramp 边界但不占 L1 端口</td></tr>
<tr><td>S3 router ALU</td><td>0</td><td>否</td><td>5</td><td>数据通路内联</td></tr>
<tr><td>S4 无网内归约</td><td>—</td><td>—</td><td>—</td><td>allgather + 本地 COMPUTE</td></tr>
</tbody>
</table>
<p>ALU 流水：单级 m flit 代价 <code>COMPUTE+m−1</code>；非流水 <code>COMPUTE·m</code>。
对 m=200 是量级杠杆（204 vs 1000）。</p>

@@ALGO_UARCH@@

<h2>§3 时延下界</h2>
<p>理想界 <code>T_LB = max(L1..L4) = 108 + ⌈m/2⌉</code>（L5 仅约束 S4）。</p>
<table>
<thead><tr>
<th>m</th><th>L1 因果</th><th>L2 注入</th><th>L3 弹出</th><th>L4 角割</th>
<th>L5 AG弹出</th><th>T_LB</th><th>S1/S2 LB</th><th>S3 LB</th><th>S4 LB</th>
</tr></thead>
<tbody>
{''.join(bound_rows)}
</tbody>
</table>
<div class="callout warn">
观察：允许网内归约时 allreduce <b>带宽廉价</b>（任一割每方向只需 m 个 flit），
L4 从不主导。探索应缩短关键路径 / 拆分 m，而非堆带宽最优 ring。
</div>

<h2>§4 整体最优（每 m）</h2>
{bar_svg(best_bars)}
<table>
<thead><tr><th>m</th><th>最优站点</th><th>方案</th><th>makespan</th>
<th>T_LB</th><th>ratio</th><th>流水</th></tr></thead>
<tbody>
{''.join(best_rows)}
</tbody>
</table>

<h2>§5 站点排名（何处做 reduce 最好）</h2>
{''.join(site_sections)}

<h2>§6 ALU 流水 vs 非流水（S3 router）</h2>
<table>
<thead><tr><th>m</th><th>流水 makespan</th><th>非流水</th><th>非流水/流水</th></tr></thead>
<tbody>
{''.join(pipe_rows)}
</tbody>
</table>

<h2>§7 方案明细</h2>
<p>A 树 reduce+bcast · B 双树拆分 m · C 维度 RS+AG（需 m%8==0）·
D Hamilton ring RS+AG · E allgather 型（仅 S4）</p>
{''.join(matrix_sections)}

<h2>§8 文件索引</h2>
<ul>
<li>扫掠：<code>utils/dse_allreduce_8x6.py</code> → <code>results/allreduce_8x6_dse.json</code></li>
<li>报告：<code>utils/gen_allreduce_8x6_report.py</code> → <code>results/report_allreduce_8x6.html</code></li>
<li>文档：<code>docs/phase-7-exploration/allreduce-8x6.md</code></li>
</ul>

<h2>§9 备注</h2>
<ul>{notes}</ul>

<footer>
生成脚本：utils/gen_allreduce_8x6_report.py · 数据：results/allreduce_8x6_dse.json · {esc(gen)}
</footer>
</body>
</html>
"""
    body = body.replace("@@ALGO_UARCH@@", algo_and_uarch_html())
    HTML_PATH.write_text(body, encoding="utf-8")
    print(f"Wrote {HTML_PATH}")


if __name__ == "__main__":
    main()
