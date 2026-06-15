#!/usr/bin/env python3
"""Run calendar collective experiments and aggregate CSV results."""

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
BOOKSIM = SRC / "booksim"
CONFIG = ROOT / "runfiles" / "calendarconfig"
RESULTS = ROOT / "results"
RESULTS_CSV = RESULTS / "results.csv"

COLLECTIVES = [
    "broadcast",
    "reduce",
    "gather",
    "allgather",
    "allreduce",
    "alltoall",
    "anytoany",
]

MSG_SIZES_HEALTHY = [1, 4, 16, 64]
MSG_SIZE_FAULT = 16

MESH_X = 12
MESH_Y = 16


def node_id(x, y):
    return x + MESH_X * y


CORNER_NODES = [node_id(0, 0), node_id(11, 0), node_id(0, 15), node_id(11, 15)]
EDGE_NODES = [node_id(6, 0), node_id(0, 8), node_id(11, 8), node_id(6, 15)]
CENTER_NODES = [node_id(5, 7), node_id(6, 7), node_id(5, 8), node_id(6, 8)]


def h_link(x, y):
    return (node_id(x, y), node_id(x + 1, y))


def v_link(x, y):
    return (node_id(x, y), node_id(x, y + 1))


CORNER_H_LINKS = [h_link(0, 0), h_link(10, 0), h_link(0, 15), h_link(10, 15)]
EDGE_H_LINKS = [h_link(5, 0), h_link(0, 7), h_link(10, 7), h_link(5, 15)]
CENTER_H_LINKS = [h_link(5, 7), h_link(5, 8), h_link(4, 7), h_link(4, 8)]

CORNER_V_LINKS = [v_link(0, 0), v_link(11, 0), v_link(0, 14), v_link(11, 14)]
EDGE_V_LINKS = [v_link(0, 7), v_link(11, 7), v_link(5, 0), v_link(5, 14)]
CENTER_V_LINKS = [v_link(5, 7), v_link(6, 7), v_link(5, 6), v_link(6, 6)]


def run_cmd(cmd, cwd=None):
    print("+", " ".join(cmd))
    rc = subprocess.call(cmd, cwd=cwd or ROOT)
    if rc not in (0, 255):
        raise subprocess.CalledProcessError(rc, cmd)


def build():
    run_cmd(["make", "-j"], cwd=SRC)


def gen_topology():
    run_cmd(
        [
            sys.executable,
            str(ROOT / "utils" / "gen_mesh_anynet.py"),
            "-o",
            str(ROOT / "runfiles" / "mesh_12x16.anynet"),
        ]
    )


def fault_nodes_arg(nodes):
    return "{" + ",".join(str(n) for n in nodes) + "}"


def fault_links_arg(links):
    flat = []
    for a, b in links:
        flat.extend([str(a), str(b)])
    return "{" + ",".join(flat) + "}"


def run_sim(collective, msg_size, fault_desc, fault_nodes=None, fault_links=None):
    overrides = [
        f"collective_type={collective}",
        f"msg_size={msg_size}",
        f"fault_desc={fault_desc}",
        f"result_csv={RESULTS_CSV}",
    ]
    if fault_nodes:
        overrides.append(f"fault_nodes={fault_nodes_arg(fault_nodes)}")
    if fault_links:
        overrides.append(f"fault_links={fault_links_arg(fault_links)}")

    cmd = [str(BOOKSIM), str(CONFIG)] + overrides
    run_cmd(cmd)


def pick(pool, count):
    return pool[:count]


def fault_scenarios():
    scenarios = [("healthy", None, None)]

    for loc, pool in [
        ("corner", CORNER_NODES),
        ("edge", EDGE_NODES),
        ("center", CENTER_NODES),
    ]:
        for count in [1, 2, 4]:
            nodes = pick(pool, count)
            scenarios.append((f"node_{loc}_{count}", nodes, None))

    for loc, pool in [
        ("corner", CORNER_H_LINKS),
        ("edge", EDGE_H_LINKS),
        ("center", CENTER_H_LINKS),
    ]:
        for count in [1, 2, 4]:
            links = pick(pool, count)
            scenarios.append((f"linkH_{loc}_{count}", None, links))

    for loc, pool in [
        ("corner", CORNER_V_LINKS),
        ("edge", EDGE_V_LINKS),
        ("center", CENTER_V_LINKS),
    ]:
        for count in [1, 2, 4]:
            links = pick(pool, count)
            scenarios.append((f"linkV_{loc}_{count}", None, links))

    return scenarios


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--healthy-only", action="store_true")
    parser.add_argument("--fault-only", action="store_true")
    args = parser.parse_args()

    RESULTS.mkdir(parents=True, exist_ok=True)
    if RESULTS_CSV.exists():
        RESULTS_CSV.unlink()

    if not args.skip_build:
        build()
    gen_topology()

    if not args.fault_only:
        for collective in COLLECTIVES:
            for msg_size in MSG_SIZES_HEALTHY:
                run_sim(collective, msg_size, "healthy")

    if not args.healthy_only:
        for collective in COLLECTIVES:
            for fault_desc, fault_nodes, fault_links in fault_scenarios():
                if fault_desc == "healthy":
                    continue
                run_sim(
                    collective,
                    MSG_SIZE_FAULT,
                    fault_desc,
                    fault_nodes=fault_nodes,
                    fault_links=fault_links,
                )

    print(f"Results written to {RESULTS_CSV}")


if __name__ == "__main__":
    main()
