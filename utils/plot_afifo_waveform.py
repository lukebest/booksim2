#!/usr/bin/env python3
"""Parse a mesh_tb-generated VCD and render a gtkwave-style waveform PNG for
the traced AFIFOs (wr/rd occupancy + wr_en/rd_en/wr_stall/slot_free pulses).

Usage:
  python3 utils/plot_afifo_waveform.py <in.vcd> <out.png> [--afifo N] [--window START END]
"""
import argparse
import re
import sys
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

VAR_RE = re.compile(r"\$var wire\s+\d+\s+(\S+)\s+(\S+)\s*(?:\[\d+:\d+\])?\s*\$end")


def parse_vcd(path):
    sym_name = {}
    changes = defaultdict(list)  # symbol -> list[(time_ps, value)]
    t = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith("$var"):
                m = VAR_RE.match(line)
                if m:
                    sym, name = m.groups()
                    sym_name[sym] = name
                continue
            if line.startswith("#"):
                t = int(line[1:])
                continue
            if line.startswith("b"):
                parts = line.split()
                if len(parts) == 2:
                    val_s, sym = parts
                    val = int(val_s[1:], 2) if val_s[1:] else 0
                    changes[sym].append((t, val))
    sig = {}
    for sym, name in sym_name.items():
        sig[name] = changes.get(sym, [(0, 0)])
    return sig


def to_steps(points, t_end):
    """points: list[(t,v)] sorted -> arrays for a post-step plot up to t_end."""
    xs, ys = [], []
    for t, v in points:
        xs.append(t)
        ys.append(v)
    if not xs or xs[0] != 0:
        xs = [0] + xs
        ys = [0] + ys
    xs.append(t_end)
    ys.append(ys[-1])
    return xs, ys


def find_afifo_groups(sig):
    groups = {}
    for name in sig:
        m = re.match(r"(afifo\d+_p\d+_c\d+)_(\w+)", name)
        if m:
            groups.setdefault(m.group(1), {})[m.group(2)] = name
    return dict(sorted(groups.items(), key=lambda kv: int(kv[0][5:kv[0].index("_p")])))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("vcd")
    ap.add_argument("png")
    ap.add_argument("--afifos", type=int, default=4, help="max number of AFIFOs to plot")
    ap.add_argument("--window", type=int, nargs=2, default=None,
                     help="time window in ps [start end], default = full trace")
    args = ap.parse_args()

    sig = parse_vcd(args.vcd)
    groups = find_afifo_groups(sig)
    if not groups:
        print("No afifo* signals found in VCD", file=sys.stderr)
        sys.exit(1)

    t_end = max((pt[-1][0] for pt in sig.values() if pt), default=1000) + 1000
    if args.window:
        t0, t1 = args.window
    else:
        t0, t1 = 0, t_end

    names = list(groups.keys())[: args.afifos]
    occ_sigs = ["wr_occ_phys", "rd_occ_phys"]
    pulse_sigs = ["wr_en", "wr_stall", "rd_ok", "slot_free"]
    rows_per_afifo = len(occ_sigs) + len(pulse_sigs)
    fig, axes = plt.subplots(len(names) * rows_per_afifo, 1,
                              figsize=(13, 1.1 * len(names) * rows_per_afifo),
                              sharex=True)
    if len(names) * rows_per_afifo == 1:
        axes = [axes]

    colors = {"wr_occ_phys": "#2563eb", "rd_occ_phys": "#dc2626",
              "wr_en": "#16a34a", "wr_stall": "#f59e0b",
              "rd_ok": "#0891b2", "slot_free": "#7c3aed"}

    row = 0
    for gname in names:
        g = groups[gname]
        m = re.match(r"afifo(\d+)_p(\d+)_c(\d+)", gname)
        idx, p, c = m.groups()
        for sname in occ_sigs:
            ax = axes[row]; row += 1
            xs, ys = to_steps(sig[g[sname]], t1)
            ax.step(xs, ys, where="post", color=colors[sname], linewidth=1.4)
            ax.fill_between(xs, ys, step="post", color=colors[sname], alpha=0.15)
            ax.set_xlim(t0, t1)
            ax.set_ylim(bottom=0)
            ax.set_ylabel(f"afifo{idx}\np{p}\u2192c{c}\n{sname}" if sname == occ_sigs[0]
                           else sname, fontsize=8, rotation=0, ha="right", va="center")
            ax.tick_params(labelsize=7)
            ax.grid(True, alpha=0.25, linewidth=0.5)
        for sname in pulse_sigs:
            ax = axes[row]; row += 1
            xs, ys = to_steps(sig[g[sname]], t1)
            ax.step(xs, ys, where="post", color=colors[sname], linewidth=1.2)
            ax.fill_between(xs, ys, step="post", color=colors[sname], alpha=0.35)
            ax.set_xlim(t0, t1)
            ax.set_ylim(-0.1, 1.3)
            ax.set_yticks([])
            ax.set_ylabel(sname, fontsize=7, rotation=0, ha="right", va="center")
            ax.grid(True, alpha=0.2, linewidth=0.5)
        axes[row - 1].axhline(0, color="#94a3b8", linewidth=0.5)

    axes[-1].set_xlabel("time (ps, 1 domain cycle \u2248 1000 ps nominal)", fontsize=9)
    fig.suptitle(f"AFIFO waveform: {args.vcd.split('/')[-1]}", fontsize=11)
    fig.tight_layout(rect=[0, 0, 1, 0.98])
    fig.savefig(args.png, dpi=140)
    print(f"Wrote {args.png}")


if __name__ == "__main__":
    main()
