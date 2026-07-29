#!/usr/bin/env python3
"""Budget fault model for 8×6 mesh: ≤4 dead routers, ≤8 undirected links.

Replaces the fixed 36-scenario catalogue for the super-turn / Pareto track.
A bidirectional physical link counts as one fault. Sampling is stratified by
(n_routers, n_links) and reproducible via --seed.
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
    # Links whose both ends are already dead are redundant; still allow them
    # (they don't change the residual graph) but prefer live-touching links.
    live = set(range(N)) - set(dead_nodes)
    useful = [l for l in LINKS
              if l[0] in live or l[1] in live]
    pool = useful or LINKS
    dead_links = sorted(rng.sample(pool, min(n_links, len(pool)))) if n_links else []
    return {
        "name": f"b_r{n_routers}_l{n_links}_{idx:04d}",
        "fault_class": "budget",
        "n_routers": n_routers,
        "n_links": n_links,
        "dead_nodes": dead_nodes,
        "dead_links": [list(l) for l in dead_links],
        "desc": f"budget ≤{MAX_ROUTERS}R/≤{MAX_LINKS}L: "
                f"{n_routers} routers + {n_links} links",
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
            "note": "undirected link = 1 fault; replaces the fixed 36-catalogue "
                    "for the budget / super-turn Pareto track",
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
