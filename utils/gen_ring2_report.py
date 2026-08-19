#!/usr/bin/env python3
"""HTML report for the 20-node dual-plane ring study."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_UTILS = Path(__file__).resolve().parent
if str(_UTILS) not in sys.path:
    sys.path.insert(0, str(_UTILS))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from rg_ring2_aimd import run_batch as run_aimd
from rg_ring2_base import Ring2BaseParams, run_batch as run_base
from rg_ring2_rg import RGConfig, run_batch as run_rg
from rg_ring2_topo import Ring2Topology, build_allpairs, build_uniform, cores

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "report_ring2_20node.html"
BIN_W = 4                                  # cycles per bandwidth sample


def _load(name: str) -> dict:
    p = ROOT / "results" / name
    if not p.exists():
        return {}
    return json.loads(p.read_text())


def _table(headers: list[str], rows: list[list]) -> str:
    th = "".join(f"<th>{h}</th>" for h in headers)
    body = []
    for r in rows:
        body.append("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>")
    return f"<table><thead><tr>{th}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def _bin_rate(times: list[int], t_max: int, bin_w: int = BIN_W
              ) -> tuple[list[int], list[float]]:
    """Receive rate in flits/cycle, one sample per `bin_w` cycles."""
    nbin = max(1, (t_max + bin_w) // bin_w)
    rate = [0.0] * nbin
    for t in times:
        rate[min(max(t, 0) // bin_w, nbin - 1)] += 1.0 / bin_w
    return [i * bin_w for i in range(nbin)], rate


def _collect_traces(topo: Ring2Topology, txns, *, seed: int = 0
                    ) -> dict[str, dict]:
    p = Ring2BaseParams(plane_sel="least_occupied")
    s0 = run_base(topo, txns, params=p, seed=seed)
    s1 = run_aimd(topo, txns, params=p, seed=seed)
    s2 = run_rg(topo, txns, cfg=RGConfig(algo="islip", iters=2,
                                        plane_sel="least_occupied",
                                        seed=seed))
    out = {}
    for name, r in (("S0", s0), ("S1", s1), ("S2", s2)):
        recv = {int(k): v for k, v in (r.get("recv_by_core") or {}).items()}
        out[name] = {
            "makespan": r.get("makespan"),
            "completed": r.get("completed"),
            "recv_by_core": recv,
        }
    return out


def plot_core_recv_bw(traces: dict[str, dict], path: Path, *,
                      title: str, bin_w: int = BIN_W) -> None:
    cs = cores()
    cmap = plt.get_cmap("tab10")
    fig, axes = plt.subplots(3, 1, figsize=(9.2, 8.4), sharex=False)
    t_max_all = max(
        (max((max(ts) for ts in tr["recv_by_core"].values()), default=0)
         for tr in traces.values()),
        default=1)
    for ax, scheme in zip(axes, ("S0", "S1", "S2")):
        tr = traces[scheme]
        t_max = max(
            (max(ts) for ts in tr["recv_by_core"].values()), default=1)
        mean = None
        for i, c in enumerate(cs):
            xs, ys = _bin_rate(tr["recv_by_core"].get(c, []), t_max, bin_w)
            ax.plot(xs, ys, color=cmap(i % 10), lw=1.1, alpha=0.85,
                    label=f"core {c}")
            if mean is None:
                mean = [0.0] * len(ys)
            for j, y in enumerate(ys):
                mean[j] += y / len(cs)
        if mean:
            ax.plot(xs, mean, color="#111827", lw=1.6, ls="--",
                    label="mean", zorder=5)
        ax.set_ylabel("recv flit / cycle")
        ax.set_title(f"{scheme}  makespan={tr['makespan']}", loc="left",
                     fontsize=10)
        ax.set_xlim(0, t_max_all)
        ax.set_ylim(bottom=0)
        ax.grid(True, ls=":", alpha=0.45)
        if scheme == "S0":
            ax.legend(ncol=6, fontsize=7, frameon=False, loc="upper right")
    axes[-1].set_xlabel("cycle")
    fig.suptitle(title, fontsize=12)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130)
    plt.close(fig)


def main() -> None:
    cmp_ = _load("ring2_20node.json")
    pareto = _load("ring2_rg_pareto.json")
    verify = _load("verify_ring2_20.json")

    sum_rows = []
    for s in cmp_.get("summary") or []:
        mk = s.get("m") if s["pattern"] == "allpairs" else s.get("K")
        sum_rows.append([
            s["scheme"], s["pattern"], s.get("R"), mk,
            s.get("makespan_mean"), s.get("makespan_min"),
            s.get("makespan_max"), s.get("bound"),
            "yes" if s.get("all_completed") else "NO",
        ])

    front_rows = [[p["tag"], p["area_norm"], p["makespan"]]
                  for p in (pareto.get("pareto") or [])]
    ver_rows = [[r["name"], "ok" if r["ok"] else "FAIL"]
                for r in (verify.get("rows") or [])]

    big = _load("ring2_core10k.json")
    board_html = ""
    if big.get("schemes"):
        meta = big.get("meta") or {}
        board_rows = [["makespan"] + [big["schemes"][s].get("makespan")
                                      for s in ("S0", "S1", "S2")]]
        for label, field in (("deflections", "n_deflections"),
                             ("peak boarding queue", "max_srcq"),
                             ("peak eject queue", "max_ejectq"),
                             ("resp latency p50", "lat_p50"),
                             ("resp latency p99", "lat_p99")):
            board_rows.append(
                [label] + [big["schemes"][s].get(field, "—") if
                           big["schemes"][s].get(field) is not None else "—"
                           for s in ("S0", "S1", "S2")])
        cores_s = sorted({int(c) for s in big["schemes"].values()
                          for c in (s.get("board_by_core") or {})},
                         key=int)
        for c in cores_s:
            rec = [f"core {c}"]
            for sch in ("S0", "S1", "S2"):
                b = (big["schemes"][sch].get("board_by_core") or {}).get(
                    str(c), {})
                rec.append(
                    f"上环 {b.get('board', 0)} "
                    f"(CW {b.get('board_cw', 0)} / CCW {b.get('board_ccw', 0)})"
                    f"<br>失败 {b.get('board_fail', 0)} "
                    f"(CW {b.get('board_fail_cw', 0)} / "
                    f"CCW {b.get('board_fail_ccw', 0)})")
            board_rows.append(rec)
        tot = ["total"]
        for sch in ("S0", "S1", "S2"):
            bb = (big["schemes"][sch].get("board_by_core") or {}).values()
            board = sum(v.get("board", 0) for v in bb)
            cw = sum(v.get("board_cw", 0) for v in bb)
            ccw = sum(v.get("board_ccw", 0) for v in bb)
            fail = sum(v.get("board_fail", 0) for v in bb)
            fcw = sum(v.get("board_fail_cw", 0) for v in bb)
            fccw = sum(v.get("board_fail_ccw", 0) for v in bb)
            tot.append(
                f"上环 {board} (CW {cw} / CCW {ccw})"
                f"<br>失败 {fail} (CW {fcw} / CCW {fccw})")
        board_rows.append(tot)
        board_html = f"""
<h2>3. Same-pattern comparison · {meta.get('flits_per_core', 10000)} flits/core</h2>
<p class="note">uniform K={meta.get('K')} R={meta.get('R')} seed={meta.get('seed')},
<code>plane_sel=least_occupied</code>, hop latency {meta.get('hop_lat')} cy,
boarding queue {meta.get('inj_depth')} deep, eject queue
{meta.get('eject_depth')}. Every core receives the same number of response
flits. Overlay shares one time axis so S0 / S1 / S2 can be compared directly.
Board counts are for <em>response data</em> destined to that core:
上环 = successful injects, CW = dir +1, CCW = dir −1, 失败 = inject attempts
that found the slot busy or I-tag blocked (AIMD token denials are not
counted). S2 still has I-tag / E-tag; its 失败 is 0 because a grant is
placed only when the hop is already free, so the reactive tags are not
exercised on this closed batch.</p>
<p><img src="ring2_core_recv_bw_10k_overlay.png" alt="overlay mean recv bw"></p>
<p><img src="ring2_core_recv_bw_10k.png" alt="per-core recv bw 10k"></p>
{_table(["", "S0 RR", "S1 AIMD", "S2 request-grant"], board_rows)}
<p class="note">Wall {big.get('wall_secs', '?')}s.</p>
"""
    else:
        board_html = "<h2>3. Same-pattern 10000 flits/core</h2><p class='note'>Run <code>python3 utils/dse_ring2_core10k.py</code> to fill this section.</p>"

    png = "ring2_rg_pareto.png"
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>2-full-ring 20-node — three schemes + RG Pareto</title>
<style>
body {{ font-family: ui-sans-serif, system-ui, sans-serif; margin: 2rem auto;
       max-width: 980px; color: #111; }}
h1,h2,h3 {{ font-weight: 650; }}
table {{ border-collapse: collapse; width: 100%; font-size: 0.92rem; }}
th,td {{ border: 1px solid #e5e7eb; padding: 0.35rem 0.5rem; text-align: right; }}
th:first-child, td:first-child {{ text-align: left; }}
th {{ background: #f8fafc; }}
code {{ background: #f1f5f9; padding: 0.1rem 0.3rem; }}
img {{ max-width: 100%; border: 1px solid #e5e7eb; }}
.note {{ color: #475569; font-size: 0.9rem; }}
</style></head><body>
<h1>2-full-ring, 20 nodes: makespan of three schemes + RG Pareto</h1>
<p class="note">Even indices are AI cores, odd indices are memory Home Agents.
Two independent bidirectional ring planes; each node has one port per plane
and the two directions of a plane share that port's buffer. Traffic is
read-return (1-flit request, R-flit response). Makespan is the cycle the
last response flit is drained at the requesting core.</p>

<h2>0. Common datapath (all three schemes)</h2>
<p class="note">S0 / S1 / S2 are <em>not</em> three different fabrics.
They share one point-to-point credit-based datapath, the same 8-deep
boarding queue, and the same I-tag / E-tag guarantees. The sweep only
changes how a source is allowed to spend credit (RR, AIMD rate, or a
request-grant match).</p>
{_table(["layer", "S0 RR", "S1 AIMD", "S2 request-grant"], [
    ["hop latency between neighbours", "2 cy", "2 cy", "2 cy"],
    ["boarding queue per (node, plane)", "8 flits", "8 flits", "8 flits"],
    ["eject queue per (node, plane)", "4 + 1 E-tag", "4 + 1 E-tag",
     "4 + 1 E-tag"],
    ["inject / eject ports", "1 per (node, plane)", "1 per (node, plane)",
     "1 per (node, plane)"],
    ["point-to-point credit FC", "yes", "yes", "yes"],
    ["I-tag (inject starvation bound)", "yes", "yes", "yes"],
    ["E-tag (leave / reserved eject)", "yes", "yes", "yes"],
    ["RR inject when credit is available", "yes", "yes", "—"],
    ["AIMD source rate (piggybacked fails)", "—", "yes", "—"],
    ["request-grant match before inject", "—", "—", "yes"],
])}
<ul>
<li><b>Credit:</b> each directed hop is a credit pair. The upstream node
decrements credit before it launches a flit; the downstream node returns
credit when the slot frees. A flit is never sent onto a hop with no
credit. 80 directed segments, two planes × two directions.</li>
<li><b>Boarding queue:</b> 8 flits per (node, plane). A PE hands flits to
an off-fabric backlog and they are admitted only when the queue has room,
so the injection point exerts real backpressure. Both directions of a
plane draw from the same queue.</li>
<li><b>I-tag:</b> a source starved for <code>t_inj</code> cycles on a
(plane, dir) raises I-tag and inhibits other injects on that ring until
it boards. Bounds inject starvation.</li>
<li><b>E-tag:</b> a flit that fails to leave (shared per-plane eject
queue full, or the single leave port already taken this cycle)
<code>t_xfer</code> times raises E-tag and may use <code>resv_ej</code>
reserved eject slots; otherwise it deflects and rides another lap.
Rebound onto reserved <em>eject</em> entries — an adaptation, not HiRD's
transfer-FIFO E-tag.</li>
</ul>

<h2>1. Verification</h2>
<p>{verify.get("n_ok", 0)}/{verify.get("n_total", 0)} checks passed.</p>
{_table(["check", "result"], ver_rows)}

<h2>2. Three-scheme makespan (default plane_sel=least_occupied, eject_depth=4)</h2>
<p class="note">Same credit + I-tag / E-tag datapath on every row.
S0 = RR inject, no source rate control.
S1 = S0 + piggybacked failure counts + AIMD token bucket.
S2 = request-grant iSLIP (I=2, interval, arc) on the same hops.
Bound is the analytic floor (link / port / cut / single-txn).</p>
{_table(["scheme", "pattern", "R", "m or K", "mean", "min", "max", "bound", "ok"],
        sum_rows)}
<p class="note">Wall {cmp_.get("wall_secs", "?")}s, {len(cmp_.get("rows") or [])} rows.
Quick={ (cmp_.get("meta") or {}).get("quick") }.</p>

{board_html}

<h2>4. Request-grant area / makespan Pareto</h2>
<p class="note">y = makespan_des + t_sched_cycles (scheduler delay charged
back). x = area_norm (IQ-XY router = 1.0, per node). S0 and S1 sit on the
same plot as reference points. Area counts the <em>shared</em> credit +
8-deep boarding queue + I-tag / E-tag datapath on all three schemes; S2
adds the arbiter and a small control-plane tax on top of that datapath —
it does not delete station storage. Bit-equivalent model calibrated so mesh
<code>greedy_ff = 0.05</code>; not mm².</p>
<p><img src="{png}" alt="Pareto front"></p>
{_table(["tag", "area_norm", "makespan"], front_rows)}
<p class="note">{pareto.get("n_front", 0)} non-dominated points,
{len(pareto.get("rows") or [])} evaluated, wall {pareto.get("wall_secs", "?")}s.</p>

<h2>5. How to read the comparison</h2>
<ul>
<li>Read the three schemes as policies on one fabric. Credit + I-tag +
E-tag are always there. In-ring traffic still never stalls (lookahead
shorter than hop delay); I-tag bounds inject starvation; E-tag bounds
leave livelock.</li>
<li>S0 is the reactive baseline: spend credit with RR when the hop is
free. No source rate control beyond I-tag. It is work-conserving, which
costs ~2 board retries per success — retries burn no slots, so the cost
lands on latency, not makespan.</li>
<li>S1 feeds board/leave failure counts back to the source and AIMDs the
token-bucket rate. On a closed burst this often <em>hurts</em> makespan
because every source sees board NACK in the first epoch and the rate
collapses; that is a result, not a bug.</li>
<li>S1 is not simply broken: it trades throughput for latency. Its
response latency p50 is ~400x lower than S0's while its makespan is
~2.8x worse, because throttling the sources keeps the boarding queues
from filling.</li>
<li>S2 keeps the same credit + boarding queue + I-tag / E-tag datapath
and adds a request-grant match so a flit only injects when the hop is
already reserved. It pays an arbiter plus a small control-plane tax on
top of the shared datapath. With ports priced per (node, plane) — the
same as the S0 DES — S2 wins the data plane and one of its
configurations stays on the Pareto front even after
<code>t_sched_cycles</code> is charged back. It buys makespan with
roughly 4.5x the area.</li>
<li>The 4-deep eject queue barely matters at these loads: peak
occupancy is 1. Deflections come from the single leave port per (node,
plane), which both directions contend for. The limiter is port count,
not queue depth.</li>
</ul>
<p class="note">Write-up: <code>docs/phase-7-exploration/ring2-20node-core-ha.md</code></p>
</body></html>
"""
    OUT.write_text(html)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
