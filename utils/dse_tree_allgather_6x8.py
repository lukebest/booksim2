#!/usr/bin/env python3
"""Formal bounds + router-area/makespan DSE for tree allgather on 6x8.

Target model:
  * 6x8 mesh, H=7, V=9, PE ramp latency=1, ramp_bw=2.
  * One flit/cycle per directed mesh link.
  * Strict down-ramp capacity of two flits/cycle; no eject burst relaxation.
  * Every candidate is validated as a source-rooted spanning arborescence.
  * Makespans are feasible rigid-pack upper bounds, not optimality proofs.

Area is the repository's Arch-A5 analytic model, normalized to IQ-XY=1.0.
The direct SparseCal entry count is N*m actions/router; a calendar issue-width
factor is included when multiple input actions occur at a router in one cycle.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

import ppa_analytic_model as PPA
import sched_zerobuf_compare as S
import slide_metrics as SM

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "tree_allgather_6x8_dse.json"

MX, MY, H, V = 6, 8, 7, 9
N, RAMP, RAMP_BW = MX * MY, 1, 2
MESSAGE_FLITS = tuple(range(1, 6))

ROUTE_DECODER_AREA = PPA.SPARSE_MATCH_CTRL_DELTA
AREA_UNCERTAINTY = PPA.CALIBRATION_UNCERTAINTY


def nid(x: int, y: int) -> int:
    return x + MX * y


def coord(node: int) -> tuple[int, int]:
    return node % MX, node // MX


def edge_latency(p: int, c: int) -> int:
    return H if coord(p)[1] == coord(c)[1] else V


def dim_tree(source: int, order: str) -> list[tuple[int, int]]:
    sx, sy = coord(source)
    edges: list[tuple[int, int]] = []
    if order == "xy":
        for x in range(sx + 1, MX):
            edges.append((nid(x - 1, sy), nid(x, sy)))
        for x in range(sx - 1, -1, -1):
            edges.append((nid(x + 1, sy), nid(x, sy)))
        for x in range(MX):
            for y in range(sy + 1, MY):
                edges.append((nid(x, y - 1), nid(x, y)))
            for y in range(sy - 1, -1, -1):
                edges.append((nid(x, y + 1), nid(x, y)))
    elif order == "yx":
        for y in range(sy + 1, MY):
            edges.append((nid(sx, y - 1), nid(sx, y)))
        for y in range(sy - 1, -1, -1):
            edges.append((nid(sx, y + 1), nid(sx, y)))
        for y in range(MY):
            for x in range(sx + 1, MX):
                edges.append((nid(x - 1, y), nid(x, y)))
            for x in range(sx - 1, -1, -1):
                edges.append((nid(x + 1, y), nid(x, y)))
    else:
        raise ValueError(order)
    return edges


def axis_ccw_tree(source: int) -> list[tuple[int, int]]:
    return [
        (key // 100000, key % 100000)
        for kind, key, _ in S.fp_axis_ccw(source)
        if kind == "L"
    ]


def hamilton_tree(source: int, bidirectional: bool) -> list[tuple[int, int]]:
    order, pos = S.RING_ORDER, S.RING_POS
    i = pos[source]
    if not bidirectional:
        chain = [order[(i + k) % N] for k in range(N)]
        return list(zip(chain, chain[1:]))
    nf = N // 2
    nb = (N - 1) - nf
    fwd = [order[(i + k) % N] for k in range(nf + 1)]
    bwd = [order[(i - k) % N] for k in range(nb + 1)]
    return list(zip(fwd, fwd[1:])) + list(zip(bwd, bwd[1:]))


def edge_comb_tree(
    source: int,
    *,
    fanout_two: bool = False,
    fixed_edge: int | None = None,
) -> list[tuple[int, int]]:
    """Source row -> one boundary spine -> inward row branches.

    NEC-3 chooses the nearest boundary.  NEC-2 sends a boundary source to the
    opposite boundary so no source ever emits E/W + N + S simultaneously.
    """
    sx, sy = coord(source)
    if fixed_edge is not None:
        edge = fixed_edge
    elif fanout_two and sx in (0, MX - 1):
        edge = MX - 1 - sx
    else:
        edge = 0 if sx <= (MX - 1) // 2 else MX - 1

    edges: list[tuple[int, int]] = []
    for x in range(sx - 1, -1, -1):
        edges.append((nid(x + 1, sy), nid(x, sy)))
    for x in range(sx + 1, MX):
        edges.append((nid(x - 1, sy), nid(x, sy)))
    for y in range(sy - 1, -1, -1):
        edges.append((nid(edge, y + 1), nid(edge, y)))
    for y in range(sy + 1, MY):
        edges.append((nid(edge, y - 1), nid(edge, y)))

    xs = range(1, MX) if edge == 0 else range(MX - 2, -1, -1)
    for y in range(MY):
        if y == sy:
            continue
        p = edge
        for x in xs:
            edges.append((nid(p, y), nid(x, y)))
            p = x
    return edges


SCHEMES = {
    "dim_xy": lambda s: dim_tree(s, "xy"),
    "dim_yx": lambda s: dim_tree(s, "yx"),
    "axis_ccw": axis_ccw_tree,
    "nec3": lambda s: edge_comb_tree(s),
    "nec2": lambda s: edge_comb_tree(s, fanout_two=True),
    "comb_fixed_west": lambda s: edge_comb_tree(s, fixed_edge=0),
    "hamilton_bi_tree": lambda s: hamilton_tree(s, True),
    "hamilton_uni_tree": lambda s: hamilton_tree(s, False),
}


def validate_tree(source: int, edges: list[tuple[int, int]]) -> dict:
    errors: list[str] = []
    children: dict[int, list[int]] = defaultdict(list)
    indegree = [0] * N
    if len(edges) != N - 1:
        errors.append(f"edge_count={len(edges)} expected={N - 1}")
    for p, c in edges:
        px, py = coord(p)
        cx, cy = coord(c)
        if abs(px - cx) + abs(py - cy) != 1:
            errors.append(f"non_adjacent={p}->{c}")
        children[p].append(c)
        indegree[c] += 1
    if indegree[source] != 0:
        errors.append(f"root_indegree={indegree[source]}")
    wrong = [n for n in range(N) if n != source and indegree[n] != 1]
    if wrong:
        errors.append(f"bad_indegree_nodes={wrong}")

    distance = {source: 0}
    queue = deque([source])
    while queue:
        p = queue.popleft()
        for c in children.get(p, []):
            if c in distance:
                errors.append(f"cycle_or_duplicate_reach={c}")
                continue
            distance[c] = distance[p] + edge_latency(p, c)
            queue.append(c)
    if len(distance) != N:
        errors.append(f"reachable={len(distance)} expected={N}")
    max_fanout = max((len(v) for v in children.values()), default=0)
    return {
        "ok": not errors,
        "errors": errors,
        "distance": distance,
        "children": children,
        "max_mesh_fanout": max_fanout,
    }


def footprint(source: int, edges: list[tuple[int, int]], check: dict) -> list[tuple[str, int, int]]:
    slots: list[tuple[str, int, int]] = [("U", source, 0)]
    distance = check["distance"]
    for p, c in edges:
        slots.append(("L", S.lk(p, c), RAMP + distance[p]))
    for d in range(N):
        if d != source:
            slots.append(("D", d, RAMP + distance[d]))
    return slots


def receiver_release_bound(m: int) -> tuple[int, list[int]]:
    """Exact relaxed receiver bound with Manhattan release times.

    Each (source, flit) is a unit eject job released no earlier than
    RAMP + Manhattan(source,dest) + flit_index.  Scheduling these jobs on
    RAMP_BW identical down-ramp lanes is a relaxation of the network problem.
    """
    best, worst = 0, []
    for dx in range(MX):
        for dy in range(MY):
            releases: list[int] = []
            for sx in range(MX):
                for sy in range(MY):
                    if (sx, sy) == (dx, dy):
                        continue
                    dist = abs(sx - dx) * H + abs(sy - dy) * V
                    releases.extend(RAMP + dist + k for k in range(m))
            lanes = [-10**9] * RAMP_BW
            for release in sorted(releases):
                lane = min(range(RAMP_BW), key=lambda j: lanes[j])
                lanes[lane] = max(release, lanes[lane] + 1)
            done = max(lanes) + RAMP
            if done > best:
                best, worst = done, [nid(dx, dy)]
            elif done == best:
                worst.append(nid(dx, dy))
    return best, worst


def formal_bounds(m: int) -> dict:
    diameter = (MX - 1) * H + (MY - 1) * V
    latency = 2 * RAMP + diameter + m - 1
    eject_duration = math.ceil((N - 1) * m / RAMP_BW)
    corner_cut = math.ceil((N - 1) * m / 2)
    vertical_cut = math.ceil((N // 2) * m / MY)
    horizontal_cut = math.ceil((N // 2) * m / MX)
    bisection = max(vertical_cut, horizontal_cut)
    injection = math.ceil(m / RAMP_BW)
    release, worst = receiver_release_bound(m)
    components = {
        "diameter_serialization": latency,
        "receiver_release": release,
        "eject_duration": eject_duration,
        "corner_cut": corner_cut,
        "bisection": bisection,
        "source_injection_duration": injection,
    }
    total = max(components.values())
    return {
        **components,
        "T_lb": total,
        "binding": [k for k, v in components.items() if v == total],
        "worst_receivers": worst,
    }


def actions_metrics(
    trees: dict[int, dict],
    inject_offsets: dict[int, int],
    m: int,
) -> dict:
    actions: dict[tuple[int, int], list[int]] = defaultdict(list)
    per_router_entries = [0] * N
    for source, tree in trees.items():
        distance = tree["distance"]
        children = tree["children"]
        off = inject_offsets[source]
        for node in range(N):
            fanout = len(children.get(node, [])) + (0 if node == source else 1)
            for k in range(m):
                cycle = off + RAMP + distance[node] + k
                actions[(node, cycle)].append(fanout)
                per_router_entries[node] += 1
    return {
        "calendar_entries_max": max(per_router_entries),
        "calendar_entries_min": min(per_router_entries),
        "calendar_issue_width": max(map(len, actions.values())),
        "crossbar_outputs_peak": max(sum(v) for v in actions.values()),
        "single_input_outputs_peak": max(max(v) for v in actions.values()),
    }


def pack_scheme(name: str, builder, m: int) -> dict:
    edges_by_source: dict[int, list[tuple[int, int]]] = {}
    trees: dict[int, dict] = {}
    footprints = {}
    for source in range(N):
        edges = builder(source)
        check = validate_tree(source, edges)
        if not check["ok"]:
            raise ValueError(f"{name} source={source}: {check['errors']}")
        edges_by_source[source] = edges
        trees[source] = check
        footprints[source] = footprint(source, edges, check)

    best = None
    for order_name, order_gen in S.SRC_ORDERS.items():
        try:
            order = order_gen()
        except TypeError:
            continue
        rec = S.export_events(footprints, RAMP_BW, order, flits=m)
        mk, max_off, busy, inject_offsets, events = rec
        if not S.verify(busy, RAMP_BW, flits=m):
            continue
        if best is None or mk < best[0]:
            best = (mk, max_off, order_name, busy, inject_offsets, events)
    if best is None:
        raise RuntimeError(f"no feasible pack for {name}, m={m}")

    mk, max_off, order_name, busy, inject_offsets, events = best
    link_busy, _, down_busy = busy
    link_load: dict[int, int] = defaultdict(int)
    for edges in edges_by_source.values():
        for p, c in edges:
            link_load[S.lk(p, c)] += m
    max_tree_distance = max(
        max(tree["distance"].values()) for tree in trees.values()
    )
    route_lb = {
        "tree_dilation": 2 * RAMP + max_tree_distance + m - 1,
        "directed_link_congestion": max(link_load.values()),
    }
    global_lb = formal_bounds(m)
    route_lb["scheme_lb"] = max(
        global_lb["T_lb"],
        route_lb["tree_dilation"],
        route_lb["directed_link_congestion"],
    )
    action = actions_metrics(trees, inject_offsets, m)
    slot = SM.slot_table_depth(events, MX, MY, mk)
    return {
        "makespan": mk,
        "gap_to_formal_lb": mk - global_lb["T_lb"],
        "gap_to_scheme_lb": mk - route_lb["scheme_lb"],
        "max_inject_offset": max_off,
        "source_order": order_name,
        "tree": {
            "valid_all_sources": True,
            "max_mesh_fanout": max(t["max_mesh_fanout"] for t in trees.values()),
            "max_weighted_distance": max_tree_distance,
        },
        "routing_lower_bounds": route_lb,
        "microarchitecture": {
            **action,
            "topology_period_max": slot["max_period"],
            "topology_period_mean": round(slot["mean_period"], 4),
            "down_ramp_peak": max(
                (ct for d in down_busy.values() for ct in d.values()), default=0
            ),
            "directed_link_peak": max(
                (ct for d in link_busy.values() for ct in d.values()), default=0
            ),
        },
    }


def next_power_of_two(value: int) -> int:
    return 1 if value <= 1 else 1 << (value - 1).bit_length()


def area_record(depth: int, issue_width: int, fanout: int, template: bool) -> dict:
    calendar = PPA.sparse_calendar_area(depth) * issue_width
    # Accepted ADR-005 charges one fixed CalFork mask-expander area whenever an
    # input may select multiple outputs.  It does not calibrate area by popcount.
    multicast = PPA.CALFORK_MC_DELTA if fanout > 1 else 0.0
    decoder = ROUTE_DECODER_AREA if template else 0.0
    common = PPA.BASELINE_CROSSBAR + PPA.ARCH_A5_BUFFERS + PPA.ARCH_A5_CONTROL
    incremental = calendar + multicast + decoder
    total = common + incremental
    return {
        "normalized_total": round(total, 6),
        "uncertainty_low": round(common + incremental * (1 - AREA_UNCERTAINTY), 6),
        "uncertainty_high": round(common + incremental * (1 + AREA_UNCERTAINTY), 6),
        "components": {
            "crossbar": PPA.BASELINE_CROSSBAR,
            "buffers": PPA.ARCH_A5_BUFFERS,
            "control": PPA.ARCH_A5_CONTROL,
            "calendar": round(calendar, 6),
            "multicast": multicast,
            "route_decoder": decoder,
        },
        "calendar_depth": depth,
        "calendar_issue_width": issue_width,
        "fanout_class": fanout,
        "template_decode": template,
    }


def architecture_variants(records: dict[str, dict]) -> dict:
    m1 = records["1"]
    max_issue = max(r["microarchitecture"]["calendar_issue_width"] for r in records.values())
    max_entries = max(r["microarchitecture"]["calendar_entries_max"] for r in records.values())
    fanout = max(
        r["microarchitecture"]["single_input_outputs_peak"]
        for r in records.values()
    )
    return {
        "sparse_direct": area_record(
            next_power_of_two(max_entries), max_issue, fanout, False
        ),
        "sparse_replay_m1": area_record(
            next_power_of_two(m1["microarchitecture"]["calendar_entries_max"]),
            m1["microarchitecture"]["calendar_issue_width"],
            fanout,
            False,
        ),
        "template_direct": area_record(8, max_issue, fanout, True),
    }


def pareto(points: list[dict]) -> list[dict]:
    result = []
    for p in points:
        dominated = any(
            (q["area"] <= p["area"] and q["makespan"] <= p["makespan"])
            and (q["area"] < p["area"] or q["makespan"] < p["makespan"])
            for q in points
        )
        if not dominated:
            result.append(p)
    return sorted(result, key=lambda p: (p["area"], p["makespan"], p["scheme"]))


def build_pareto(schemes: dict[str, dict]) -> dict:
    out = {"sparse_only": {}, "expanded_with_template": {}}
    for m in MESSAGE_FLITS:
        sparse, expanded = [], []
        for name, scheme in schemes.items():
            rec = scheme["messages"][str(m)]
            mk1 = scheme["messages"]["1"]["makespan"]
            for arch_name, area in scheme["architectures"].items():
                mk = mk1 * m if arch_name == "sparse_replay_m1" else rec["makespan"]
                point = {
                    "scheme": name,
                    "architecture": arch_name,
                    "area": area["normalized_total"],
                    "makespan": mk,
                    "slowdown_vs_lb": round(mk / formal_bounds(m)["T_lb"], 4),
                }
                expanded.append(point)
                if arch_name != "template_direct":
                    sparse.append(point)
        out["sparse_only"][str(m)] = pareto(sparse)
        out["expanded_with_template"][str(m)] = pareto(expanded)
    return out


def main() -> None:
    S.cfg(MX, MY, H, V)
    S.init_ring()
    S.init_quadrants()

    bounds = {str(m): formal_bounds(m) for m in MESSAGE_FLITS}
    schemes = {}
    for name, builder in SCHEMES.items():
        print(f"== {name} ==", flush=True)
        messages = {}
        for m in MESSAGE_FLITS:
            messages[str(m)] = pack_scheme(name, builder, m)
            print(
                f"  m={m} mk={messages[str(m)]['makespan']} "
                f"issue={messages[str(m)]['microarchitecture']['calendar_issue_width']} "
                f"fanout={messages[str(m)]['tree']['max_mesh_fanout']}",
                flush=True,
            )
        schemes[name] = {
            "messages": messages,
            "architectures": architecture_variants(messages),
        }

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": {
            "mesh": [MX, MY],
            "nodes": N,
            "H": H,
            "V": V,
            "ramp": RAMP,
            "ramp_bw": RAMP_BW,
            "message_flits": list(MESSAGE_FLITS),
            "strict_zero_buffer": True,
            "directed_link_capacity": 1,
            "area_baseline": "IQ-XY router = 1.0; Arch-A5 analytic calibration",
            "area_uncertainty_on_incremental": AREA_UNCERTAINTY,
        },
        "formal_lower_bounds": bounds,
        "schemes": schemes,
        "pareto": build_pareto(schemes),
        "proof_obligations": {
            "tree": "N-1 adjacent directed edges, root indegree 0, all others indegree 1, all nodes reachable",
            "schedule": "directed link <=1; up/down ramp <=2; every receiver ejects (N-1)*m flits",
            "formal_bound": "relaxes network coupling, therefore every component is necessary but not sufficient",
            "area": "analytic only; calendar and multicast incremental terms carry +/-30% sensitivity",
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
