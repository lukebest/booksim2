#!/usr/bin/env python3
"""Generate per-cycle router/link trace CSV for dimensional multi-tree allgather.

Outputs (under results/ by default):
  allgather_trace_summary.csv   — one row per cycle (max inject/inFlight/eject, conflict flag)
  allgather_trace_links.csv     — one row per (cycle, directed link)
  allgather_trace_routers.csv   — one row per (cycle, router)

Use to verify every cycle is conflict-free (inject≤1 per link, eject≤1 per router) and
non-blocking (inFlight>1 on a link is pipelined wormhole, not a stall).
"""

import argparse
import csv
import heapq
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def nid(x, y, mx):
    return x + mx * y


def coord(n, mx):
    return n % mx, n // mx


def link_lat(u, v, mx, h, vlat):
    return h if coord(u, mx)[1] == coord(v, mx)[1] else vlat


def tree_edges(s, mx, my):
    sx, sy = coord(s, mx)
    edges = []
    for x in range(sx + 1, mx):
        edges.append((nid(x - 1, sy, mx), nid(x, sy, mx)))
    for x in range(sx - 1, -1, -1):
        edges.append((nid(x + 1, sy, mx), nid(x, sy, mx)))
    for x in range(mx):
        for y in range(sy + 1, my):
            edges.append((nid(x, y - 1, mx), nid(x, y, mx)))
        for y in range(sy - 1, -1, -1):
            edges.append((nid(x, y + 1, mx), nid(x, y, mx)))
    return edges


def allocate(nxt, e):
    t = e
    chain = []
    while t in nxt:
        chain.append(t)
        t += 1
    nxt[t] = t + 1
    for c in chain:
        nxt[c] = t + 1
    return t


def simulate(mx, my, h, vlat, ramp):
    n = mx * my
    trees = {}
    for s in range(n):
        adj = defaultdict(list)
        for p, c in tree_edges(s, mx, my):
            adj[p].append(c)
        trees[s] = adj

    link_nf = defaultdict(dict)
    down_nf = defaultdict(dict)
    edges = []
    ejects = []

    pq = []
    seq = 0
    avail = {}
    for s in range(n):
        avail[(s, s)] = ramp
        for c in trees[s][s]:
            heapq.heappush(pq, (ramp, seq, s, s, c))
            seq += 1

    makespan = 0
    while pq:
        ready, _, s, p, c = heapq.heappop(pq)
        send = allocate(link_nf[(p, c)], max(ready, avail[(s, p)]))
        arrive = send + link_lat(p, c, mx, h, vlat)
        avail[(s, c)] = arrive
        eject = allocate(down_nf[c], arrive)
        done = eject + ramp
        makespan = max(makespan, done)
        edges.append({"s": s, "p": p, "c": c, "send": send, "arrive": arrive})
        ejects.append({"node": c, "eject": eject, "s": s})
        for gc in trees[s][c]:
            heapq.heappush(pq, (arrive, seq, s, c, gc))
            seq += 1

    return n, mx, my, h, vlat, makespan, edges, ejects


def all_directed_links(mx, my):
    out = []
    for y in range(my):
        for x in range(mx - 1):
            u, v = nid(x, y, mx), nid(x + 1, y, mx)
            out.append((u, v))
            out.append((v, u))
    for x in range(mx):
        for y in range(my - 1):
            u, v = nid(x, y, mx), nid(x, y + 1, mx)
            out.append((u, v))
            out.append((v, u))
    return out


def build_traces_full(n, mx, my, h, vlat, makespan, edges, ejects):
    dir_links = all_directed_links(mx, my)
    summary = []
    link_rows = []
    router_rows = []

    for t in range(makespan + 1):
        max_inject = max_eject = max_inflight = 0
        for p, c in dir_links:
            injecting = [e for e in edges if e["p"] == p and e["c"] == c and e["send"] == t]
            inflight = [e for e in edges if e["p"] == p and e["c"] == c and e["send"] <= t < e["arrive"]]
            inj = len(injecting)
            inf = len(inflight)
            max_inject = max(max_inject, inj)
            max_inflight = max(max_inflight, inf)
            kind = "H" if link_lat(p, c, mx, h, vlat) == h else "V"
            px, py = coord(p, mx)
            cx, cy = coord(c, mx)
            link_rows.append({
                "cycle": t,
                "link": f"({px},{py})->({cx},{cy})",
                "kind": kind,
                "inject": inj,
                "inFlight": inf,
                "sources": ",".join(str(e["s"]) for e in injecting) if injecting else "—",
                "conflict_free": 1 if inj <= 1 else 0,
            })

        for node in range(n):
            ej = [e for e in ejects if e["node"] == node and e["eject"] == t]
            arr = [e for e in edges if e["c"] == node and e["arrive"] == t]
            fwd = [e for e in edges if e["p"] == node and e["send"] == t]
            ec = len(ej)
            max_eject = max(max_eject, ec)
            nx, ny = coord(node, mx)
            router_rows.append({
                "cycle": t,
                "router": f"({nx},{ny})",
                "eject": ec,
                "eject_sources": ",".join(str(e["s"]) for e in ej) if ej else "—",
                "arrive": len(arr),
                "forward": len(fwd),
                "conflict_free": 1 if ec <= 1 else 0,
            })

        summary.append({
            "cycle": t,
            "max_inject": max_inject,
            "max_inFlight": max_inflight,
            "max_eject": max_eject,
            "conflict_free": 1 if max_inject <= 1 and max_eject <= 1 else 0,
            "pipelined": 1 if max_inflight > 1 else 0,
        })

    return summary, link_rows, router_rows


def write_csv(path, fieldnames, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {path} ({len(rows)} rows)")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mx", type=int, default=6)
    ap.add_argument("--my", type=int, default=8)
    ap.add_argument("--h", type=int, default=4)
    ap.add_argument("--v", type=int, default=8, dest="vlat")
    ap.add_argument("--ramp", type=int, default=1)
    ap.add_argument("-o", "--outdir", type=Path, default=ROOT / "results")
    args = ap.parse_args()

    n, mx, my, h, vlat, makespan, edges, ejects = simulate(
        args.mx, args.my, args.h, args.vlat, args.ramp,
    )
    summary, link_rows, router_rows = build_traces_full(
        n, mx, my, h, vlat, makespan, edges, ejects,
    )

    tag = f"{mx}x{my}"
    out = args.outdir
    out.mkdir(parents=True, exist_ok=True)

    write_csv(out / f"allgather_trace_summary_{tag}.csv",
              ["cycle", "max_inject", "max_inFlight", "max_eject", "conflict_free", "pipelined"],
              summary)
    write_csv(out / f"allgather_trace_links_{tag}.csv",
              ["cycle", "link", "kind", "inject", "inFlight", "sources", "conflict_free"],
              link_rows)
    write_csv(out / f"allgather_trace_routers_{tag}.csv",
              ["cycle", "router", "eject", "eject_sources", "arrive", "forward", "conflict_free"],
              router_rows)

    bad = [r for r in summary if not r["conflict_free"]]
    print(f"mesh {tag}: makespan={makespan}, cycles={len(summary)}, "
          f"all conflict-free={'YES' if not bad else 'NO (' + str(len(bad)) + ' bad)'}")


if __name__ == "__main__":
    main()
