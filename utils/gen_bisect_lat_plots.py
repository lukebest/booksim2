#!/usr/bin/env python3
"""Three injection-rate curves for the two centralized arbiters.

Reads results/bisect_lat_8x6.json and writes

    results/bisect_util_vs_lam.png    bisection utilization
    results/mean_lat_vs_lam.png       mean packet latency
    results/p99_lat_vs_lam.png        99th percentile latency
    results/bisect_lat_all.png        the three panels side by side

Points past the stability boundary are drawn hollow with a dashed line: beyond
it the source queues grow without bound, so the latency there is a property of
the finite measurement window rather than of the fabric.

    python3 utils/gen_bisect_lat_plots.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "results" / "bisect_lat_8x6.json"

STYLE = {
    "mesh_islip2d": ("#2563eb", "o", "iSLIP-2D mesh (D-M, 12-link cut)"),
    "ring_islip2d": ("#dc2626", "s", "iSLIP-2D ring (D-R, 24-link cut)"),
}


def load() -> dict[str, Any]:
    return json.loads(SRC.read_text())


def series(d: dict, config: str) -> list[dict]:
    return sorted([r for r in d["rows"] if r["config"] == config],
                  key=lambda r: r["lam"])


def _draw(ax, d: dict, key: str) -> None:
    """One metric for both fabrics: solid+filled while stable, dashed+hollow after."""
    for cfg, (color, mark, label) in STYLE.items():
        rs = series(d, cfg)
        x = [r["lam"] for r in rs]
        y = [r[key] for r in rs]
        st = [bool(r["stable"]) for r in rs]
        ax.plot(x, y, "-", color=color, lw=1.6, alpha=0.9, label=label,
                zorder=3)
        ax.plot([a for a, s in zip(x, st) if s],
                [b for b, s in zip(y, st) if s],
                mark, color=color, ms=5, zorder=4)
        ax.plot([a for a, s in zip(x, st) if not s],
                [b for b, s in zip(y, st) if not s],
                mark, mfc="white", mec=color, ms=5, zorder=4)
        lam_star = d["summary"][cfg]["lam_star"]
        if lam_star is not None:
            ax.axvline(lam_star, color=color, ls=":", lw=1.1, alpha=0.55,
                       zorder=1)
    ax.set_xlabel("offered injection rate  λ  (packets / node / cycle)")
    ax.grid(alpha=0.25, lw=0.5)
    ax.set_xlim(0, 1.02)
    # Below the axes: the plot area is needed for annotations, and a log-scaled
    # latency panel has no reliably empty corner to put a legend in.
    ax.legend(fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.145),
              ncol=2, framealpha=0.95)


def _lamstar_note(d: dict) -> str:
    return "  ·  ".join(
        f"{STYLE[c][2].split(' (')[0]} λ*={d['summary'][c]['lam_star']}"
        for c in STYLE)


def fig_bisect(d: dict, ax=None) -> Any:
    own = ax is None
    if own:
        _, ax = plt.subplots(figsize=(7.4, 5.1))
    _draw(ax, d, "bisect_util")
    ax.axhline(1.0, color="#111827", ls="--", lw=1.0, alpha=0.6)
    ax.text(0.015, 1.012, "bisection saturated", fontsize=8, color="#111827")
    ax.set_ylabel("bisection utilization  (fraction of cut link-cycles busy)")
    ax.set_ylim(0, 1.12)
    m = d["summary"]["mesh_islip2d"]
    ax.annotate(f"cut is full ({m['peak_bisect_util']:.3f}) —\n"
                f"mesh λ* is bisection bound\n"
                f"({m['bisect_util_at_lam_star']:.3f} already at λ*)",
                xy=(0.52, 1.0), xytext=(0.055, 0.775),
                fontsize=8, color=STYLE["mesh_islip2d"][0],
                arrowprops=dict(arrowstyle="->", lw=0.9,
                                color=STYLE["mesh_islip2d"][0]))
    r = d["summary"]["ring_islip2d"]
    ax.annotate(f"ring never exceeds {r['peak_bisect_util']:.3f}:\n"
                f"only {r['bisect_util_at_lam_star']:.3f} at its own λ*,\n"
                f"so the ring saturates elsewhere",
                xy=(r["lam_star"], r["bisect_util_at_lam_star"]),
                xytext=(0.545, 0.115),
                fontsize=8, color=STYLE["ring_islip2d"][0],
                arrowprops=dict(arrowstyle="->", lw=0.9,
                                color=STYLE["ring_islip2d"][0]))
    ax.set_title("Bisection utilization vs injection rate\n"
                 f"8×6, all-to-all uniform, m=1, σ=1  ·  {_lamstar_note(d)}",
                 fontsize=9.5)
    return ax


def fig_mean(d: dict, ax=None) -> Any:
    own = ax is None
    if own:
        _, ax = plt.subplots(figsize=(7.4, 5.1))
    _draw(ax, d, "mean_lat")
    ax.set_ylabel("mean packet latency  (cycles)")
    ax.set_yscale("log")
    for cfg, dy in (("mesh_islip2d", 1.07), ("ring_islip2d", 0.86)):
        rs = [r for r in series(d, cfg) if r["stable"]]
        if rs:
            lo = min(r["mean_lat"] for r in rs)
            hi = max(r["mean_lat"] for r in rs)
            ax.axhline(lo, color=STYLE[cfg][0], ls="-.", lw=0.8, alpha=0.4)
            ax.text(0.02, lo * dy,
                    f"unloaded {lo:.0f} cy → {hi:.0f} cy at λ* "
                    f"(×{hi / lo:.2f})", fontsize=7.5, color=STYLE[cfg][0])
    ax.set_title("Mean latency vs injection rate  (log scale)\n"
                 "flat until λ*, then diverges — hollow markers are unstable",
                 fontsize=9.5)
    return ax


def fig_p99(d: dict, ax=None) -> Any:
    own = ax is None
    if own:
        _, ax = plt.subplots(figsize=(7.4, 5.1))
    _draw(ax, d, "p99")
    ax.set_ylabel("p99 packet latency  (cycles)")
    ax.set_yscale("log")
    for cfg, tx in (("mesh_islip2d", (0.055, 260)),
                    ("ring_islip2d", (0.45, 620))):
        rs = [r for r in series(d, cfg) if r["stable"]]
        if rs:
            hi = max(r["p99"] for r in rs)
            lo = min(r["p99"] for r in rs)
            ax.plot([rs[-1]["lam"]], [hi], "*", color=STYLE[cfg][0], ms=11,
                    zorder=5)
            ax.annotate(f"p99 at λ*: {hi:.0f} cy  (unloaded {lo:.0f}, "
                        f"×{hi / lo:.2f})",
                        xy=(rs[-1]["lam"], hi), xytext=tx, fontsize=8,
                        color=STYLE[cfg][0],
                        arrowprops=dict(arrowstyle="->", lw=0.9,
                                        color=STYLE[cfg][0]))
    ax.set_title("p99 latency vs injection rate  (log scale)\n"
                 "bufferless + rigid grants keep the tail near the mean "
                 "throughout the stable region", fontsize=9.5)
    return ax


def main() -> None:
    d = load()
    for fn, name in ((fig_bisect, "bisect_util_vs_lam"),
                     (fig_mean, "mean_lat_vs_lam"),
                     (fig_p99, "p99_lat_vs_lam")):
        fig, ax = plt.subplots(figsize=(7.4, 5.1))
        fn(d, ax)
        fig.tight_layout()
        p = ROOT / "results" / f"{name}.png"
        fig.savefig(p, dpi=170)
        plt.close(fig)
        print(f"wrote {p}")

    fig, axs = plt.subplots(1, 3, figsize=(19.5, 5.0))
    fig_bisect(d, axs[0])
    fig_mean(d, axs[1])
    fig_p99(d, axs[2])
    fig.tight_layout()
    p = ROOT / "results" / "bisect_lat_all.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    print(f"wrote {p}")


if __name__ == "__main__":
    main()
