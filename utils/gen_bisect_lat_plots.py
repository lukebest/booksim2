#!/usr/bin/env python3
"""Three injection-rate curves for all four configurations.

Reads results/bisect_lat_8x6.json and writes

    results/bisect_util_vs_lam.png    bisection utilization
    results/mean_lat_vs_lam.png       mean packet latency
    results/p99_lat_vs_lam.png        99th percentile latency
    results/bisect_lat_all.png        the three panels side by side

One hue per fabric, light for the distributed baseline and dark for the
centralized arbiter, so the two comparisons a reader wants -- baseline vs
centralized, and mesh vs ring -- are both visible without a legend lookup.

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
    "mesh_base": ("#93c5fd", "^", "mesh_base (buffered + credits)"),
    "mesh_islip2d": ("#1d4ed8", "o", "mesh_islip2d (central, 12-link cut)"),
    "ring_base": ("#fca5a5", "v", "ring_base (E-tag/I-tag + deflection)"),
    "ring_islip2d": ("#b91c1c", "s", "ring_islip2d (central, 24-link cut)"),
}
CEN = ("mesh_islip2d", "ring_islip2d")


def load() -> dict[str, Any]:
    return json.loads(SRC.read_text())


def series(d: dict, config: str) -> list[dict]:
    return sorted([r for r in d["rows"] if r["config"] == config],
                  key=lambda r: r["lam"])


def stable(d: dict, config: str) -> list[dict]:
    return [r for r in series(d, config) if r["stable"]]


def _draw(ax, d: dict, key: str) -> None:
    """One metric for all four configs: filled while stable, hollow after."""
    for cfg, (color, mark, label) in STYLE.items():
        rs = series(d, cfg)
        x = [r["lam"] for r in rs]
        y = [r[key] for r in rs]
        st = [bool(r["stable"]) for r in rs]
        cen = cfg in CEN
        ax.plot(x, y, "-", color=color, lw=1.8 if cen else 2.6,
                alpha=0.95 if cen else 0.55, label=label, zorder=3 if cen else 2)
        ax.plot([a for a, s in zip(x, st) if s],
                [b for b, s in zip(y, st) if s],
                mark, color=color, ms=4.5, zorder=4)
        ax.plot([a for a, s in zip(x, st) if not s],
                [b for b, s in zip(y, st) if not s],
                mark, mfc="white", mec=color, ms=4.5, zorder=4)
        lam_star = d["summary"][cfg]["lam_star"]
        if lam_star is not None:
            ax.axvline(lam_star, color=color, ls=":", lw=1.0, alpha=0.5,
                       zorder=1)
    ax.set_xlabel("offered injection rate  λ  (packets / node / cycle)")
    ax.grid(alpha=0.25, lw=0.5)
    ax.set_xlim(0, 1.02)
    # Below the axes: with four curves no corner stays empty across the sweep,
    # and a log-scaled latency panel has nowhere safe to put a legend.
    ax.legend(fontsize=7.5, loc="upper center", bbox_to_anchor=(0.5, -0.145),
              ncol=2, framealpha=0.95)


def _lamstar_note(d: dict) -> str:
    """Compact enough to fit one title line at this figure width."""
    s = d["summary"]
    return (f"λ* mesh {s['mesh_base']['lam_star']}→"
            f"{s['mesh_islip2d']['lam_star']}, "
            f"ring {s['ring_base']['lam_star']}→"
            f"{s['ring_islip2d']['lam_star']}")


def fig_bisect(d: dict, ax=None) -> Any:
    if ax is None:
        _, ax = plt.subplots(figsize=(7.4, 5.1))
    _draw(ax, d, "bisect_util")
    ax.axhline(1.0, color="#111827", ls="--", lw=1.0, alpha=0.6)
    ax.text(0.015, 1.012, "bisection saturated", fontsize=8, color="#111827")
    ax.set_ylabel("bisection utilization  (fraction of cut link-cycles busy)")
    ax.set_ylim(0, 1.12)
    mi, mb = d["summary"]["mesh_islip2d"], d["summary"]["mesh_base"]
    rb = d["summary"]["ring_base"]
    ax.annotate(f"only mesh_islip2d fills the cut ({mi['peak_bisect_util']:.3f});"
                f"\n{mi['bisect_util_at_lam_star']:.3f} already at its λ*, so"
                f"\nits λ* IS the bisection bound",
                xy=(mi["peak_bisect_util_at_lam"], 1.0), xytext=(0.05, 0.80),
                fontsize=8, color=STYLE["mesh_islip2d"][0],
                arrowprops=dict(arrowstyle="->", lw=0.9,
                                color=STYLE["mesh_islip2d"][0]))
    ax.annotate(f"baselines stop short: mesh_base peaks at\n"
                f"{mb['peak_bisect_util']:.3f}, ring_base at "
                f"{rb['peak_bisect_util']:.3f} — they are\n"
                f"limited by credits / deflection, not by metal",
                xy=(rb["peak_bisect_util_at_lam"], rb["peak_bisect_util"]),
                xytext=(0.30, 0.115), fontsize=8, color="#7f1d1d",
                arrowprops=dict(arrowstyle="->", lw=0.9, color="#7f1d1d"))
    ax.set_title("Bisection utilization vs injection rate\n"
                 f"8×6, all-to-all uniform, m=1, σ=1  ·  {_lamstar_note(d)}",
                 fontsize=8.5)
    return ax


def fig_mean(d: dict, ax=None) -> Any:
    if ax is None:
        _, ax = plt.subplots(figsize=(7.4, 5.1))
    _draw(ax, d, "mean_lat")
    ax.set_ylabel("mean packet latency  (cycles)")
    ax.set_yscale("log")
    # All four unloaded means sit within a factor of two of each other, so
    # per-curve labels would pile up on a log axis: one block instead.
    lines = []
    for cfg in STYLE:
        rs = stable(d, cfg)
        lo, hi = rs[0]["mean_lat"], rs[-1]["mean_lat"]
        lines.append(f"{cfg}: {lo:.0f} cy unloaded → {hi:.0f} cy at λ* "
                     f"(×{hi / lo:.2f})")
    ax.text(0.025, 150, "\n".join(lines), fontsize=7.5, color="#111827",
            bbox=dict(fc="white", ec="#9ca3af", lw=0.6, alpha=0.92, pad=3))
    ax.set_title("Mean latency vs injection rate  (log scale)\n"
                 "the centralized pair pays the grant round trip unloaded, "
                 "then stays flat to a higher λ*", fontsize=8.5)
    return ax


def fig_p99(d: dict, ax=None) -> Any:
    if ax is None:
        _, ax = plt.subplots(figsize=(7.4, 5.1))
    _draw(ax, d, "p99")
    ax.set_ylabel("p99 packet latency  (cycles)")
    ax.set_yscale("log")
    lines = [f"{c}: worst p99/mean = "
             f"{d['summary'][c]['worst_p99_over_mean_stable']:.2f}×"
             for c in STYLE]
    ax.text(0.02, 1.6e3, "\n".join(lines), fontsize=7.5, color="#111827",
            bbox=dict(fc="white", ec="#9ca3af", lw=0.6, alpha=0.9, pad=3))
    mbl = d["summary"]["mesh_base"]["lam_star"]
    pb = next(r["p99"] for r in series(d, "mesh_base") if r["lam"] == mbl)
    pc = next(r["p99"] for r in series(d, "mesh_islip2d") if r["lam"] == mbl)
    ax.annotate(f"at mesh_base's own λ*={mbl}:\n{pb:.0f} cy vs {pc:.0f} cy "
                f"= {pb / pc:.1f}× tail gap",
                xy=(mbl, pb), xytext=(0.47, 130), fontsize=8, color="#111827",
                arrowprops=dict(arrowstyle="->", lw=0.9, color="#111827"))
    ax.set_title("p99 latency vs injection rate  (log scale)\n"
                 "rigid grants keep the tail near the mean; the baselines' "
                 "tails run 3-5× their mean", fontsize=8.5)
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
