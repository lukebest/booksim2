#!/usr/bin/env python3
"""Budget fault model for 8×6 mesh: ≤4 dead routers, ≤8 undirected links.

Replaces the fixed link_*/node_* catalogue for e2e evaluation. A bidirectional
physical link counts as one fault. Router faults and link faults never overlap:
every sampled dead link has both endpoints among live routers. Sampling is
stratified by (n_routers, n_links) and reproducible via --seed.
"""
from __future__ import annotations

import argparse
import itertools
import json
import random
from pathlib import Path
from typing import Any

import pg_faults_8x6 as F
from pg_faults_8x6 import MX, MY, N, coord, nid

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "results" / "pg_faults_budget_8x6.json"

MAX_ROUTERS = 4
MAX_LINKS = 8


def all_undirected_links() -> list[tuple[int, int]]:
    out = []
    for n in range(N):
        x, y = coord(n)
        if x + 1 < MX:
            out.append((n, nid(x + 1, y)))
        if y + 1 < MY:
            out.append((n, nid(x, y + 1)))
    return out


LINKS = all_undirected_links()


def sample_scenario(rng: random.Random, n_routers: int, n_links: int,
                    idx: int) -> dict[str, Any]:
    dead_nodes = sorted(rng.sample(range(N), n_routers)) if n_routers else []
    dead_set = set(dead_nodes)
    # Non-overlap: a link fault must not touch any dead router (both ends live).
    pool = [l for l in LINKS
            if l[0] not in dead_set and l[1] not in dead_set]
    n_take = min(n_links, len(pool))
    dead_links = sorted(rng.sample(pool, n_take)) if n_take else []
    return {
        "name": f"b_r{n_routers}_l{n_links}_{idx:04d}",
        "fault_class": "budget",
        "n_routers": n_routers,
        "n_links": len(dead_links),
        "n_links_requested": n_links,
        "dead_nodes": dead_nodes,
        "dead_links": [list(l) for l in dead_links],
        "desc": f"budget ≤{MAX_ROUTERS}R/≤{MAX_LINKS}L: "
                f"{n_routers} routers + {len(dead_links)} links "
                f"(no router–link overlap)",
    }


def stratified_scenarios(n_per_cell: int = 4, seed: int = 0,
                         include_healthy: bool = False
                         ) -> list[dict[str, Any]]:
    """n_per_cell samples for every (nr, nl) with 0≤nr≤4, 0≤nl≤8.

    Skips (0,0) unless include_healthy. Total default = 4*5*9 - 4 = 176.
    """
    rng = random.Random(seed)
    out = []
    for nr, nl in itertools.product(range(MAX_ROUTERS + 1),
                                    range(MAX_LINKS + 1)):
        if nr == 0 and nl == 0 and not include_healthy:
            continue
        for i in range(n_per_cell):
            out.append(sample_scenario(rng, nr, nl, i))
    return out


def _loc_class(n: int) -> str:
    x, y = coord(n)
    on_x = x in (0, MX - 1)
    on_y = y in (0, MY - 1)
    if on_x and on_y:
        return "corner"
    if on_x or on_y:
        return "edge"
    return "center"


def single_router_scenarios() -> list[dict[str, Any]]:
    """At most one dead router; positions cover corner / edge / center.

    No extra link faults — isolates the location of a single router hole.
    Includes the healthy mesh (0 routers) because the budget is "at most one".
    """
    # 4 corners, 1 midpoint per side, 2 interior (near geometric centre).
    picks = [
        (0, 0, "corner"), (MX - 1, 0, "corner"),
        (0, MY - 1, "corner"), (MX - 1, MY - 1, "corner"),
        (MX // 2, 0, "edge"), (MX // 2, MY - 1, "edge"),
        (0, MY // 2, "edge"), (MX - 1, MY // 2, "edge"),
        (MX // 2 - 1, MY // 2 - 1, "center"),
        (MX // 2, MY // 2, "center"),
    ]
    out = [{
        "name": "sr_healthy",
        "fault_class": "single_router",
        "region": "healthy",
        "n_routers": 0, "n_links": 0,
        "dead_nodes": [], "dead_links": [],
        "desc": "healthy 8×6 (0 dead routers)",
    }]
    for x, y, region in picks:
        n = nid(x, y)
        assert _loc_class(n) == region, (n, x, y, _loc_class(n), region)
        out.append({
            "name": f"sr_{region}_{x}x{y}",
            "fault_class": "single_router",
            "region": region,
            "n_routers": 1, "n_links": 0,
            "dead_nodes": [n], "dead_links": [],
            "desc": f"1 dead router @ {region} ({x},{y}) nid={n}",
        })
    return out


def expand_budget(scen: dict, semantics: str = "dead") -> dict:
    return F.expand_pg({
        "name": scen["name"],
        "dead_nodes": list(scen["dead_nodes"]),
        "dead_links": [tuple(l) for l in scen["dead_links"]],
        "fault_class": "budget",
        "region": f"r{scen.get('n_routers', 0)}_l{scen.get('n_links', 0)}",
        "detail": scen.get("sample_id", scen["name"]),
        "desc": scen.get("desc", ""),
    }, semantics)


def write_catalog(path: Path = DEFAULT_OUT, n_per_cell: int = 4,
                  seed: int = 0) -> dict:
    scens = stratified_scenarios(n_per_cell=n_per_cell, seed=seed)
    doc = {
        "meta": {
            "mx": MX, "my": MY,
            "max_routers": MAX_ROUTERS, "max_links": MAX_LINKS,
            "n_per_cell": n_per_cell, "seed": seed,
            "n_scenarios": len(scens),
            "note": "undirected link = 1 fault; router/link faults non-overlapping; "
                    "replaces fixed link_*/node_* catalogue for e2e evaluation",
            "non_overlap": True,
        },
        "scenarios": scens,
    }
    path.write_text(json.dumps(doc, indent=1))
    return doc


def load_catalog(path: Path = DEFAULT_OUT) -> list[dict[str, Any]]:
    if not path.exists():
        write_catalog(path)
    return json.loads(path.read_text())["scenarios"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-per-cell", type=int, default=4)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("-o", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    doc = write_catalog(args.o, args.n_per_cell, args.seed)
    print(f"Wrote {args.o}  ({doc['meta']['n_scenarios']} scenarios)")


if __name__ == "__main__":
    main()
