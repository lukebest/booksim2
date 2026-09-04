#!/usr/bin/env python3
"""S0 E-tag threshold sweep: t_xfer vs bandwidth–CoV, with fail counts.

Fixes the official I-tag point (t_inj=16, itag_hold=8, outstanding=128) and
walks `t_xfer`, the number of failed leaves before a flit raises E-tag.
E-tag then wins the leave port and the reserved eject slots, so a larger
threshold delays that priority and lets a flit keep circulating.

CoV is the official 100-cycle window mean. `t_xfer=0` in the grid means
never tag (implemented as a threshold the run cannot reach).

Usage:
    PYTHONHASHSEED=0 python3 utils/probe_ring2_s0_txfer_curve.py [K] [jobs]
    PYTHONHASHSEED=0 python3 utils/probe_ring2_s0_txfer_curve.py --no-leave-fifo [K] [jobs]
    PYTHONHASHSEED=0 python3 utils/probe_ring2_s0_txfer_curve.py --plot-only [--no-leave-fifo]
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
OUT_STOCK = ROOT / "results" / "probe_ring2_s0_txfer_curve.json"
PNG_STOCK = ROOT / "results" / "ring2_wfair_s0_txfer_curve.png"
OUT_NOFIFO = ROOT / "results" / "probe_ring2_s0_txfer_noleave.json"
PNG_NOFIFO = ROOT / "results" / "ring2_wfair_s0_txfer_noleave.png"

OFFICIAL_OUTST = 128
T_INJ = 16
ITAG_HOLD = 8
# 0 = never raise E-tag
T_XFER = (1, 2, 4, 8, 16, 32, 64, 128, 256, 0)
OFFICIAL = 1
NEVER = 10 ** 9
R_STAR = 40.0 / 7.0


def txfer_lab(v: int) -> str:
    return "∞" if v == 0 else str(v)


def binned_cov(inject_times: dict[int, list[int]], bin_w: int,
               t_fair: int) -> dict[str, Any]:
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
    t_xfer, k, no_leave_fifo = job
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE, route="latency")
    tx = build_pattern("uniform", k=k, W=W_FLITS, seed=0)
    cfg = dict(FABRIC)
    cfg["core_outstanding"] = OFFICIAL_OUTST
    cfg["t_inj"] = T_INJ
    cfg["itag_hold"] = ITAG_HOLD
    cfg["t_xfer"] = NEVER if t_xfer == 0 else t_xfer
    if no_leave_fifo:
        # Drop the two-write-one-read leave FIFO: one write port, one slot
        # so the PE can still drain in the same cycle.
        cfg["two_write_leave"] = False
        cfg["eject_depth"] = 1
    r = run_scheme("S0", topo, tx, cfg=cfg, quiet=True)
    inj = {int(c): v for c, v in (r.get("wr_inject_by_core") or {}).items()}
    f = fairness_stats(inj, r["makespan"] or 1, k * W_FLITS)
    bc = binned_cov(inj, BIN_W, f.get("t_fair") or 0)
    board = int(r.get("n_board_fail") or 0)
    defl = int(r.get("n_deflections") or 0)
    return {
        "t_xfer": t_xfer,
        "t_xfer_cfg": cfg["t_xfer"],
        "thr": f["throughput"], "cov": bc["cov_mean"],
        "cov_min": bc.get("cov_min"), "cov_max": bc.get("cov_max"),
        "n_bins": bc.get("n_bins"),
        "max_min": f["max_min"], "makespan": r["makespan"],
        "t_fair": f.get("t_fair"),
        "n_etag_raised": r.get("n_etag_raised"),
        "n_itag_raised": r.get("n_itag_raised"),
        "n_board_fail": board,
        "n_deflections": defl,
        "max_deflections": r.get("max_deflections"),
        "max_ejectq": r.get("max_ejectq"),
        "fail_ratio": (defl / board) if board else None,
        "completed": r.get("completed"),
        "stall_detected": r.get("stall_detected"),
        "wall_secs": r.get("wall_secs"),
        "official": t_xfer == OFFICIAL,
        "no_leave_fifo": no_leave_fifo,
    }


def _use_cjk_font() -> None:
    from matplotlib import font_manager as fm
    wanted = ("micro hei", "cjk", "noto sans sc", "source han sans")
    for font in fm.fontManager.ttflist:
        if any(w in font.name.lower() for w in wanted):
            plt.rcParams["font.sans-serif"] = [font.name, "DejaVu Sans"]
            plt.rcParams["axes.unicode_minus"] = False
            return


def plot(data: dict[str, Any], path: Path | None = None) -> None:
    no_fifo = bool(data.get("no_leave_fifo"))
    if path is None:
        path = PNG_NOFIFO if no_fifo else PNG_STOCK
    _use_cjk_font()
    rows = sorted(data["rows"], key=lambda r: (r["t_xfer"] == 0, r["t_xfer"]))
    live = [r for r in rows if r.get("cov") is not None]
    fig, (ax, bx) = plt.subplots(1, 2, figsize=(13.2, 5.55))

    xs = [r["cov"] for r in live]
    ys = [r["thr"] for r in live]
    ax.plot(xs, ys, color="#1f4e79", lw=1.8, marker="o", ms=6.5, zorder=3,
            label=f"t_inj={T_INJ}, hold={ITAG_HOLD}")
    ax.axhline(R_STAR, color="#c7000b", ls="--", lw=1.1, alpha=0.75,
               label=f"R* = {R_STAR:.4f}")
    off = next((r for r in live if r["official"]), None)
    if off:
        ax.scatter([off["cov"]], [off["thr"]], s=200, marker="*",
                   c="#c7000b", edgecolors="k", linewidths=0.55, zorder=8,
                   label=f"出厂 t_xfer={OFFICIAL}")
        ax.axvline(off["cov"], color="#94a3b8", ls=":", lw=0.8)
        ax.axhline(off["thr"], color="#94a3b8", ls=":", lw=0.8)
    for r in live:
        if r["official"]:
            continue
        ax.annotate(txfer_lab(r["t_xfer"]), (r["cov"], r["thr"]),
                    textcoords="offset points", xytext=(5, 4),
                    fontsize=8.0, color="#1f4e79")
    pad_x = 0.04 * (max(xs) - min(xs) or 0.05)
    pad_y = 0.05 * (max(ys) - min(ys) or 0.3)
    ax.set_xlim(min(xs) - pad_x, max(xs) + pad_x)
    ax.set_ylim(min(ys) - pad_y, max(max(ys), R_STAR) + pad_y)
    ax.set_xlabel("100 拍窗 CoV 均值（越低越均衡）")
    ax.set_ylabel("总写带宽  flit/cycle")
    ax.set_title("固定 I-tag，沿 t_xfer 走（数字 = t_xfer）",
                 fontsize=11.4, fontweight="bold")
    ax.grid(alpha=0.28)
    ax.legend(fontsize=8.2, loc="best", framealpha=0.94)

    order = [r for r in live]
    labs = [txfer_lab(r["t_xfer"]) for r in order]
    bx.plot(range(len(order)), [r["n_board_fail"] for r in order],
            color="#1f4e79", lw=1.8, marker="o", ms=6.5, label="上环失败 n_board_fail")
    bx.plot(range(len(order)), [r["n_deflections"] for r in order],
            color="#c7000b", lw=1.8, marker="s", ms=6.0, label="下环失败 n_deflections")
    bx.plot(range(len(order)), [r["n_etag_raised"] for r in order],
            color="#2e7d32", lw=1.6, marker="^", ms=6.0, label="E-tag 次数 n_etag_raised")
    bx.set_xticks(range(len(order)), labs)
    bx.set_yscale("log")
    bx.set_xlabel("t_xfer（∞ = 永不打 E-tag）")
    bx.set_ylabel("全程次数（对数轴）")
    bx.set_title("上环 / 下环失败与 E-tag 会不会随 t_xfer 动",
                 fontsize=11.4, fontweight="bold")
    bx.grid(alpha=0.28, which="both")
    bx.legend(fontsize=8.2, loc="best", framealpha=0.94)
    if off:
        i = next(j for j, r in enumerate(order) if r["official"])
        bx.axvline(i, color="#94a3b8", ls=":", lw=0.8)

    k = data.get("k")
    leave = "无两写一读 leave FIFO" if no_fifo else "出厂两写一读 leave FIFO"
    fig.suptitle("S0 · t_xfer · " + leave
                 + (f"（K={k}，outstanding={data.get('core_outstanding')}，"
                    f"t_inj={T_INJ}，hold={ITAG_HOLD}）" if k else ""),
                 fontsize=13.0, fontweight="bold", y=0.99)
    fig.tight_layout(rect=(0, 0.01, 1, 0.95))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=140)
    plt.close(fig)
    print(f"wrote {path}")


def main() -> None:
    raw = sys.argv[1:]
    no_fifo = "--no-leave-fifo" in raw
    plot_only = "--plot-only" in raw
    args = [a for a in raw if a not in ("--plot-only", "--no-leave-fifo")
            and not a.startswith("-")]
    out_json = OUT_NOFIFO if no_fifo else OUT_STOCK
    if plot_only:
        plot(json.loads(out_json.read_text()),
             PNG_NOFIFO if no_fifo else PNG_STOCK)
        return
    k = int(args[0]) if args else 20_000
    jobs_n = (int(args[1]) if len(args) > 1
              else min(5, max(1, (os.cpu_count() or 2) - 1)))
    jobs = [(v, k, no_fifo) for v in T_XFER]
    print(f"K={k}  outstanding={OFFICIAL_OUTST}  t_inj={T_INJ}  "
          f"hold={ITAG_HOLD}  no_leave_fifo={no_fifo}  "
          f"points={len(jobs)}  workers={jobs_n}",
          flush=True)
    with ProcessPoolExecutor(max_workers=jobs_n) as ex:
        rows = list(ex.map(_one, jobs, chunksize=1))
    rows.sort(key=lambda r: (r["t_xfer"] == 0, r["t_xfer"]))
    off = next(r for r in rows if r["official"])
    print(f"\n{'t_xfer':>7} {'R':>8} {'CoV':>8} {'vsS0%':>7} "
          f"{'board':>10} {'defl':>8} {'maxd':>5} {'etag':>8}",
          flush=True)
    for r in rows:
        d = 100.0 * (r["thr"] - off["thr"]) / off["thr"] if off["thr"] else 0.0
        mark = "  <-- S0" if r["official"] else ""
        print(f"{txfer_lab(r['t_xfer']):>7} {r['thr']:>8.4f} {r['cov']:>8.5f} "
              f"{d:>+6.2f}% {r['n_board_fail']:>10} {r['n_deflections']:>8} "
              f"{int(r.get('max_deflections') or 0):>5} "
              f"{r['n_etag_raised']:>8}{mark}", flush=True)
    data = {
        "k": k, "bin_w": BIN_W, "core_outstanding": OFFICIAL_OUTST,
        "r_star": R_STAR, "t_inj": T_INJ, "itag_hold": ITAG_HOLD,
        "official_t_xfer": OFFICIAL, "t_xfer": list(T_XFER),
        "no_leave_fifo": no_fifo,
        "two_write_leave": not no_fifo,
        "eject_depth": 1 if no_fifo else 12,
        "rows": rows,
    }
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    print(f"wrote {out_json}")
    plot(data, PNG_NOFIFO if no_fifo else PNG_STOCK)


if __name__ == "__main__":
    main()
