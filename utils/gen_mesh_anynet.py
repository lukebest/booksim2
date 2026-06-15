#!/usr/bin/env python3
"""Generate 12x16 heterogeneous mesh anynet topology file for BookSim2."""

import argparse
import os


def node_id(x, y, mesh_x):
    return x + mesh_x * y


def generate(mesh_x=12, mesh_y=16, h_lat=4, v_lat=8, ramp_lat=1, out_path=None):
    lines = []
    for y in range(mesh_y):
        for x in range(mesh_x):
            nid = node_id(x, y, mesh_x)
            parts = [f"router {nid}", f"node {nid} {ramp_lat}"]
            if x + 1 < mesh_x:
                nb = node_id(x + 1, y, mesh_x)
                parts.extend([f"router {nb} {h_lat}"])
            if x > 0:
                nb = node_id(x - 1, y, mesh_x)
                parts.extend([f"router {nb} {h_lat}"])
            if y + 1 < mesh_y:
                nb = node_id(x, y + 1, mesh_x)
                parts.extend([f"router {nb} {v_lat}"])
            if y > 0:
                nb = node_id(x, y - 1, mesh_x)
                parts.extend([f"router {nb} {v_lat}"])
            lines.append(" ".join(parts))

    text = "\n".join(lines) + "\n"
    if out_path:
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mesh-x", type=int, default=12)
    parser.add_argument("--mesh-y", type=int, default=16)
    parser.add_argument("--h-lat", type=int, default=4)
    parser.add_argument("--v-lat", type=int, default=8)
    parser.add_argument("--ramp-lat", type=int, default=1)
    parser.add_argument(
        "-o",
        "--output",
        default="runfiles/mesh_12x16.anynet",
        help="Output anynet file path",
    )
    args = parser.parse_args()
    generate(
        mesh_x=args.mesh_x,
        mesh_y=args.mesh_y,
        h_lat=args.h_lat,
        v_lat=args.v_lat,
        ramp_lat=args.ramp_lat,
        out_path=args.output,
    )
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
