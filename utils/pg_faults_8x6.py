#!/usr/bin/env python3
"""8x6 partial-good fault catalogue (ring_report model, no Hamilton logic).

Fault scenario enumerators mirror utils/hamilton_ring.py:
  link_fault_scenarios / node_fault_scenarios / quadrant_fault_scenarios

PG semantics:
  dead    — PE + router + incident links unavailable (strict ring_report)
  transit — PE does not participate in alltoall; router/links still forward
"""

from __future__ import annotations

from typing import Any

MX, MY = 8, 6
N = MX * MY


def nid(x: int, y: int, mx: int = MX) -> int:
    return x + mx * y


def coord(n: int, mx: int = MX) -> tuple[int, int]:
    return n % mx, n // mx


def grid_neighbors(n: int, mx: int = MX, my: int = MY) -> list[int]:
    x, y = coord(n, mx)
    res = []
    if x + 1 < mx:
        res.append(nid(x + 1, y, mx))
    if x - 1 >= 0:
        res.append(nid(x - 1, y, mx))
    if y + 1 < my:
        res.append(nid(x, y + 1, mx))
    if y - 1 >= 0:
        res.append(nid(x, y - 1, mx))
    return res


def build_adj(mx: int, my: int, dead_nodes=(), dead_links=()) -> dict[int, list[int]]:
    dead_nodes = set(dead_nodes)
    dead_links = {frozenset(l) for l in dead_links}
    adj = {}
    for n in range(mx * my):
        if n in dead_nodes:
            continue
        nb = []
        for m in grid_neighbors(n, mx, my):
            if m in dead_nodes:
                continue
            if frozenset((n, m)) in dead_links:
                continue
            nb.append(m)
        adj[n] = sorted(nb)
    return adj


def _link_region_sets(mx: int, my: int):
    """Link-fault anchors by region.

    Unlike hamilton_ring (which offsets corner links so no node drops below
    degree 2, needed for Hamilton cycles), corner faults here sit on the
    actual corner node (0,0): its two incident edges, then one neighbouring
    edge for count=3.
    """
    cx, cy = mx // 2, my // 2
    return {
        # (0,0)-(1,0), (0,0)-(0,1), then (1,0)-(1,1)
        "corner": [((0, 0), (1, 0)), ((0, 0), (0, 1)), ((1, 0), (1, 1))],
        "edge": [((cx, 0), (cx, 1)), ((cx + 1, 0), (cx + 1, 1)),
                 ((cx, 1), (cx + 1, 1))],
        "center": [((cx, cy), (cx, cy + 1)), ((cx + 1, cy), (cx + 1, cy + 1)),
                   ((cx, cy), (cx + 1, cy))],
    }


def link_fault_scenarios(mx: int = MX, my: int = MY) -> list[dict[str, Any]]:
    regions = _link_region_sets(mx, my)
    out = []
    for region, links in regions.items():
        for cnt in (1, 2, 3):
            sel = links[:cnt]
            dl = [(nid(ax, ay, mx), nid(bx, by, mx))
                  for (ax, ay), (bx, by) in sel]
            human = ", ".join(f"({ax},{ay})-({bx},{by})"
                              for (ax, ay), (bx, by) in sel)
            out.append({
                "name": f"link_{region}_{cnt}",
                "fault_class": "link",
                "region": region,
                "detail": str(cnt),
                "dead_nodes": [],
                "dead_links": dl,
                "desc": f"{cnt} link fault(s) @ {region}: {human}",
            })
    return out


def _block(x0: int, y0: int, s: int, mx: int) -> list[int]:
    return [nid(x, y, mx) for x in range(x0, x0 + s) for y in range(y0, y0 + s)]


def node_fault_scenarios(mx: int = MX, my: int = MY) -> list[dict[str, Any]]:
    cx, cy = mx // 2, my // 2
    out = []
    for s in (1, 2, 3):
        anchors = {
            "corner": (0, 0),
            "edge": (cx - (s - 1) // 2, 0),
            "center": (cx - (s - 1) // 2, cy - (s - 1) // 2),
        }
        for region, (x0, y0) in anchors.items():
            dn = _block(x0, y0, s, mx)
            out.append({
                "name": f"node_{region}_{s}x{s}",
                "fault_class": "node",
                "region": region,
                "detail": f"{s}x{s}",
                "dead_nodes": dn,
                "dead_links": [],
                "desc": f"{s}x{s} node hole @ {region} anchor ({x0},{y0})",
            })
    return out


def quadrant_fault_scenarios(mx: int = MX, my: int = MY) -> list[dict[str, Any]]:
    hw, hh = mx // 2, my // 2
    anchors = {
        "Q0": (0, 0),
        "Q1": (hw, 0),
        "Q2": (0, hh),
        "Q3": (hw, hh),
    }
    regions = {"Q0": "corner", "Q1": "corner", "Q2": "edge", "Q3": "corner"}
    out = []
    for q, (x0, y0) in anchors.items():
        dn = [nid(x, y, mx)
              for x in range(x0, x0 + hw) for y in range(y0, y0 + hh)]
        out.append({
            "name": f"quadrant_{q}",
            "fault_class": "quadrant",
            "region": regions[q],
            "detail": q,
            "dead_nodes": dn,
            "dead_links": [],
            "desc": (f"1/4 quadrant {q} fault "
                     f"({hw}x{hh} @ ({x0},{y0}), {len(dn)} nodes)"),
        })
    return out


def all_scenarios(mx: int = MX, my: int = MY) -> list[dict[str, Any]]:
    # Quadrant faults omitted from the PG alltoall study (too coarse / too many
    # nodes removed for the 8x6 mesh). Keep quadrant_fault_scenarios() available
    # for other callers.
    return (link_fault_scenarios(mx, my)
            + node_fault_scenarios(mx, my))


def expand_pg(scenario: dict[str, Any], semantics: str = "dead",
              mx: int = MX, my: int = MY) -> dict[str, Any]:
    """Expand a fault scenario into compute_nodes + route_graph under PG semantics.

    semantics:
      dead    — dead_nodes removed from both compute and route graph
      transit — dead_nodes removed from compute only; routers stay for forwarding
                (link faults always remove those links from the route graph)
    """
    if semantics not in ("dead", "transit"):
        raise ValueError(f"unknown PG semantics: {semantics}")
    dead_nodes = list(scenario.get("dead_nodes", []))
    dead_links = [tuple(l) for l in scenario.get("dead_links", [])]
    all_nodes = set(range(mx * my))
    fault_set = set(dead_nodes)

    if semantics == "dead":
        route_dead = set(dead_nodes)
        compute = sorted(all_nodes - fault_set)
    else:
        # transit: node holes keep routers; link faults still cut links
        route_dead = set()
        compute = sorted(all_nodes - fault_set)

    adj = build_adj(mx, my, route_dead, dead_links)
    return {
        **scenario,
        "semantics": semantics,
        "compute_nodes": compute,
        "route_dead_nodes": sorted(route_dead),
        "route_dead_links": dead_links,
        "route_adj": adj,
        "n_compute": len(compute),
        "n_originally_good": mx * my - len(fault_set),
    }


def healthy_pg(mx: int = MX, my: int = MY) -> dict[str, Any]:
    scen = {
        "name": "healthy",
        "fault_class": "none",
        "region": "none",
        "detail": "0",
        "dead_nodes": [],
        "dead_links": [],
        "desc": "healthy mesh, no faults",
    }
    return expand_pg(scen, "dead", mx, my)


if __name__ == "__main__":
    scens = all_scenarios()
    print(f"{len(scens)} scenarios on {MX}x{MY}")
    for s in scens:
        d = expand_pg(s, "dead")
        t = expand_pg(s, "transit")
        print(f"  {s['name']:22s} dead_comp={d['n_compute']:2d} "
              f"transit_comp={t['n_compute']:2d} "
              f"route_nodes_dead={len(d['route_adj'])}/"
              f"{len(t['route_adj'])}")
