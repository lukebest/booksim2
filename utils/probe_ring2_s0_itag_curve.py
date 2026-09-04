#!/usr/bin/env python3
"""S0 I-tag (t_inj × itag_hold) sweep: bandwidth–CoV curves.

The two knobs already in S0 are the starvation threshold `t_inj` and the
self-clear `itag_hold` (0 = never expire). This walks their product on the
official write fabric and plots the (CoV, R) trajectories.

CoV is the official 100-cycle window mean: in each whole bin inside the
contention window, std/mean of the ten cores' WriteData counts, then the
average of those CoVs. Bandwidth is closed-batch total write throughput.

Official S0 is t_inj=4, itag_hold=0, core_outstanding=128.

Usage:
    PYTHONHASHSEED=0 python3 utils/probe_ring2_s0_itag_curve.py [K] [jobs]
    PYTHONHASHSEED=0 python3 utils/probe_ring2_s0_itag_curve.py --plot-only
"""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (BIN_W, FABRIC, W_FLITS, build_pattern, cov,
                                  fairness_stats, run_scheme)
from rg_ring2_topo import CHI_VCS_WRITE, Ring2Topology

ROOT = Path(__file__).resolve().parents[1]
OUT_JSON = ROOT / "results" / "probe_ring2_s0_itag_curve.json"
OUT_PNG = ROOT / "results" / "ring2_wfair_s0_itag_curve.png"

# Official report S0. FABRIC still defaults the deck/retry cap to 32.
OFFICIAL_OUTST = 128
T_INJ = (1, 2, 4, 8, 16, 32)
HOLD = (0, 1, 2, 4, 8, 16)
OFFICIAL = (4, 0)
R_STAR = 40.0 / 7.0

HOLD_COLOR = {
    0: "#22252b",
    1: "#c7000b",
    2: "#d97706",
    4: "#1f4e79",
    8: "#1a7f37",
    16: "#7c3aed",
}
TINJ_COLOR = {
    1: "#c7000b",
    2: "#d97706",
    4: "#1f4e79",
    8: "#1a7f37",
    16: "#7c3aed",
    32: "#22252b",
}
TINJ_MARK = {1: "o", 2: "s", 4: "D", 8: "^", 16: "v", 32: "P"}


def hold_lab(h: int) -> str:
    return "∞" if h == 0 else str(h)


def binned_cov(inject_times: dict[int, list[int]], bin_w: int,
               t_fair: int) -> dict[str, Any]:
    """Mean of per-`bin_w` CoV over bins wholly inside [0, t_fair]."""
    cs = sorted(inject_times)
    nbin = int(t_fair) // bin_w if bin_w > 0 else 0
    if not cs or nbin <= 0:
        return {"cov_mean": None, "n_bins": 0}
    n = len(cs)
    cnt = [[0] * nbin for _ in cs]
    for i, c in enumerate(cs):
        for t in inject_times[c]:
            b = int(t) // bin_w
            if 0 <= b < nbin:
                cnt[i][b] += 1
    vals = [cov([cnt[i][b] for i in range(n)]) for b in range(nbin)]
    return {
        "cov_mean": round(sum(vals) / len(vals), 5),
        "cov_min": round(min(vals), 5),
        "cov_max": round(max(vals), 5),
        "n_bins": nbin,
        "bin_w": bin_w,
    }


def _one(job: tuple) -> dict[str, Any]:
    t_inj, hold, k = job
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE, route="latency")
    tx = build_pattern("uniform", k=k, W=W_FLITS, seed=0)
    cfg = dict(FABRIC)
    cfg["core_outstanding"] = OFFICIAL_OUTST
    cfg["t_inj"] = t_inj
    cfg["itag_hold"] = hold
    r = run_scheme("S0", topo, tx, cfg=cfg, quiet=True)
    inj = {int(c): v for c, v in (r.get("wr_inject_by_core") or {}).items()}
    f = fairness_stats(inj, r["makespan"] or 1, k * W_FLITS)
    bc = binned_cov(inj, BIN_W, f.get("t_fair") or 0)
    return {
        "t_inj": t_inj, "itag_hold": hold,
        "thr": f["throughput"], "cov": bc["cov_mean"],
        "cov_min": bc.get("cov_min"), "cov_max": bc.get("cov_max"),
        "n_bins": bc.get("n_bins"),
        "max_min": f["max_min"], "makespan": r["makespan"],
        "t_fair": f.get("t_fair"),
        "n_itag_raised": r.get("n_itag_raised"),
        "n_itag_yield": r.get("n_itag_yield"),
        "defl": r.get("n_deflections"),
        "wall_secs": r.get("wall_secs"),
        "official": (t_inj, hold) == OFFICIAL,
    }


def _use_cjk_font() -> None:
    from matplotlib import font_manager as fm
    wanted = ("micro hei", "cjk", "noto sans sc", "source han sans")
    for f in fm.fontManager.ttflist:
        if any(w in f.name.lower() for w in wanted):
            plt.rcParams["font.sans-serif"] = [f.name, "DejaVu Sans"]
            plt.rcParams["axes.unicode_minus"] = False
            return


def _pareto(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Non-dominated set: no other point is both higher-R and lower-CoV."""
    live = [r for r in rows if r.get("cov") is not None]
    front = []
    for a in live:
        if any(b["thr"] >= a["thr"] and b["cov"] <= a["cov"]
               and (b["thr"] > a["thr"] or b["cov"] < a["cov"])
               for b in live):
            continue
        front.append(a)
    front.sort(key=lambda r: r["cov"])
    return front


def _style_ax(ax, rows: list[dict[str, Any]], *, legend_loc: str) -> None:
    xs = [r["cov"] for r in rows if r["cov"] is not None]
    ys = [r["thr"] for r in rows]
    ax.axhline(R_STAR, color="#c7000b", ls="--", lw=1.1, alpha=0.75,
               label=f"R* = {R_STAR:.4f}")
    front = _pareto(rows)
    if len(front) >= 2:
        ax.plot([r["cov"] for r in front], [r["thr"] for r in front],
                color="#94a3b8", lw=1.15, ls="-.", zorder=2,
                label="Pareto 前沿（更高带宽且更低 CoV）")
    off = next((r for r in rows if r.get("official")), None)
    if off and off["cov"] is not None:
        ax.axvline(off["cov"], color="#94a3b8", ls=":", lw=0.8)
        ax.axhline(off["thr"], color="#94a3b8", ls=":", lw=0.8)
        ax.scatter([off["cov"]], [off["thr"]], s=190, marker="*",
                   c="#c7000b", edgecolors="k", linewidths=0.55, zorder=8,
                   label=f"出厂 S0  t_inj={OFFICIAL[0]}  hold={hold_lab(OFFICIAL[1])}")
    pad_x = 0.035 * (max(xs) - min(xs) or 0.05)
    pad_y = 0.04 * (max(ys) - min(ys) or 0.3)
    ax.set_xlim(min(xs) - pad_x, max(xs) + pad_x)
    ax.set_ylim(min(ys) - pad_y, max(max(ys), R_STAR) + pad_y)
    ax.set_xlabel("100 拍窗 CoV 均值（越低越均衡）")
    ax.set_ylabel("总写带宽  flit/cycle")
    ax.grid(alpha=0.28)
    ax.legend(fontsize=7.8, loc=legend_loc, framealpha=0.94)


def _uniq(pts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one row per (round CoV, round R) so hold=1 does not stack."""
    seen: set[tuple[float, float]] = set()
    out = []
    for r in pts:
        key = (round(r["cov"], 5), round(r["thr"], 4))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def plot(data: dict[str, Any], path: Path = OUT_PNG) -> None:
    _use_cjk_font()
    rows = data["rows"]
    fig, axes = plt.subplots(1, 2, figsize=(13.2, 5.85), sharex=True, sharey=True)

    ax = axes[0]
    for hold in HOLD:
        pts = _uniq(sorted((r for r in rows if r["itag_hold"] == hold
                            and r["cov"] is not None),
                           key=lambda r: r["t_inj"]))
        if not pts:
            continue
        ax.plot([r["cov"] for r in pts], [r["thr"] for r in pts],
                color=HOLD_COLOR[hold], lw=1.7,
                marker="s" if hold == 1 else "o", ms=6.0 if hold != 1 else 8.0,
                label=f"hold = {hold_lab(hold)}"
                      + ("（1 拍到期，yield=0）" if hold == 1 else ""))
        if hold in (0, 2):
            for r in pts:
                if r.get("official"):
                    continue
                ax.annotate(f"t={r['t_inj']}", (r["cov"], r["thr"]),
                            textcoords="offset points", xytext=(4, 4),
                            fontsize=7.4, color=HOLD_COLOR[hold])
    best = min((r for r in rows if r["cov"] is not None), key=lambda r: r["cov"])
    ax.annotate(f"最低 CoV\nt_inj={best['t_inj']} hold={hold_lab(best['itag_hold'])}\n"
                f"R={best['thr']:.3f}  CoV={best['cov']:.3f}",
                (best["cov"], best["thr"]),
                textcoords="offset points", xytext=(8, -28),
                fontsize=7.6, color=HOLD_COLOR[best["itag_hold"]],
                arrowprops=dict(arrowstyle="->", color=HOLD_COLOR[best["itag_hold"]],
                                lw=0.8))
    _style_ax(ax, rows, legend_loc="lower right")
    ax.set_title("固定 hold，沿 t_inj 走（数字 = t_inj）",
                 fontsize=11.4, fontweight="bold")

    ax = axes[1]
    hold1 = next((r for r in rows if r["itag_hold"] == 1 and r["cov"] is not None),
                 None)
    if hold1:
        ax.scatter([hold1["cov"]], [hold1["thr"]], s=70, marker="s",
                   c="#c7000b", edgecolors="k", linewidths=0.4, zorder=6,
                   label="hold = 1（所有 t_inj 重合）")
    for ti in T_INJ:
        pts = sorted((r for r in rows if r["t_inj"] == ti
                      and r["cov"] is not None and r["itag_hold"] != 1),
                     key=lambda r: (r["itag_hold"] == 0, r["itag_hold"]))
        if hold1:
            pts = [hold1] + pts
        if not pts:
            continue
        ax.plot([r["cov"] for r in pts], [r["thr"] for r in pts],
                color=TINJ_COLOR[ti], lw=1.55, marker=TINJ_MARK[ti], ms=6.0,
                label=f"t_inj = {ti}")
        if ti in (1, 2):
            for r in pts:
                if r["itag_hold"] == 1:
                    continue
                ax.annotate(hold_lab(r["itag_hold"]), (r["cov"], r["thr"]),
                            textcoords="offset points", xytext=(4, 4),
                            fontsize=7.4, color=TINJ_COLOR[ti])
    _style_ax(ax, rows, legend_loc="upper left")
    ax.set_title("固定 t_inj，沿 hold 走（数字 = hold）",
                 fontsize=11.4, fontweight="bold")

    k = data.get("k")
    fig.suptitle("S0 · I-tag 门限 t_inj 与 hold 的带宽—CoV 曲线"
                 + (f"（K={k}，outstanding={data.get('core_outstanding')}）"
                    if k else ""),
                 fontsize=13.2, fontweight="bold", y=0.99)
    fig.tight_layout(rect=(0, 0.01, 1, 0.955))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=140)
    plt.close(fig)
    print(f"wrote {path}")


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--plot-only"]
    plot_only = "--plot-only" in sys.argv[1:]
    if plot_only:
        data = json.loads(OUT_JSON.read_text())
        plot(data)
        return
    k = int(args[0]) if args else 20_000
    jobs_n = (int(args[1]) if len(args) > 1
              else max(1, (os.cpu_count() or 2) - 1))
    jobs = [(ti, h, k) for ti in T_INJ for h in HOLD]
    print(f"K={k}  outstanding={OFFICIAL_OUTST}  "
          f"points={len(jobs)}  workers={jobs_n}  bin_w={BIN_W}",
          flush=True)
    with ProcessPoolExecutor(max_workers=jobs_n) as ex:
        rows = list(ex.map(_one, jobs, chunksize=1))
    rows.sort(key=lambda r: (r["t_inj"], r["itag_hold"] == 0, r["itag_hold"]))
    off = next(r for r in rows if r["official"])
    print(f"\n{'t_inj':>6} {'hold':>6} {'R':>8} {'CoV':>8} "
          f"{'vsS0%':>7} {'itag':>10} {'defl':>7}", flush=True)
    for r in rows:
        d = 100.0 * (r["thr"] - off["thr"]) / off["thr"] if off["thr"] else 0.0
        mark = "  <-- S0" if r["official"] else ""
        print(f"{r['t_inj']:>6} {hold_lab(r['itag_hold']):>6} "
              f"{r['thr']:>8.4f} {r['cov']:>8.5f} {d:>+6.2f}% "
              f"{r['n_itag_raised']:>10} {r['defl']:>7}{mark}", flush=True)
    data = {
        "k": k, "bin_w": BIN_W, "core_outstanding": OFFICIAL_OUTST,
        "r_star": R_STAR, "official": {"t_inj": OFFICIAL[0],
                                       "itag_hold": OFFICIAL[1]},
        "t_inj": list(T_INJ), "itag_hold": list(HOLD),
        "rows": rows,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    print(f"wrote {OUT_JSON}")
    plot(data)


if __name__ == "__main__":
    main()
