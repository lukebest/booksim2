#!/usr/bin/env python3
"""Export deterministic, topology-correct Trial-1 calendar replay vectors."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "calendars"
MX, MY, H, V, RAMP = 6, 8, 7, 9, 1
PORT = {"north": 0, "east": 1, "south": 2, "west": 3, "local": 4}
OP_FORWARD, OP_ADD = 0, 1


def children(x, y, root):
    """Return a deterministic X-then-Y tree rooted at root."""
    rx, ry = root
    result = []
    for name, dx, dy in (("east", 1, 0), ("west", -1, 0),
                         ("south", 0, 1), ("north", 0, -1)):
        nx, ny = x + dx, y + dy
        if 0 <= nx < MX and 0 <= ny < MY:
            if (nx, ny) == root:
                continue
            if nx != rx:
                parent = (nx - (1 if nx > rx else -1), ny)
            else:
                parent = (nx, ny - (1 if ny > ry else -1))
            if parent == (x, y):
                result.append((name, nx, ny))
    return result


def delay(port):
    return H if port in ("east", "west") else V


def opposite(port):
    return {"north": "south", "south": "north",
            "east": "west", "west": "east"}[port]


def add_entry(table, x, y, slot, in_port, outputs, opcode=OP_FORWARD):
    key = (x, y, slot)
    if key in table:
        raise ValueError(f"calendar conflict at {key}")
    mask = sum(1 << PORT[name] for name in outputs)
    table[key] = {"slot": slot, "valid": True, "in_port": PORT[in_port],
                  "out_port_mask": mask, "opcode": opcode}


def bcast_tree(table, root, start, opcode=OP_FORWARD):
    """Schedule one multicast tree and return terminal ejections and end slot."""
    ejections, max_slot = [], start

    def visit(x, y, arrival, in_port):
        nonlocal max_slot
        kids = children(x, y, root)
        if kids:
            outs = ["local"] + [p for p, _, _ in kids]
            add_entry(table, x, y, arrival, in_port, outs, opcode)
            ejections.append([x, y])
            max_slot = max(max_slot, arrival)
            for port, nx, ny in kids:
                visit(nx, ny, arrival + delay(port), opposite(port))
        else:
            add_entry(table, x, y, arrival, in_port, ["local"], opcode)
            ejections.append([x, y])
            max_slot = max(max_slot, arrival + RAMP)

    visit(root[0], root[1], start, "local")
    return ejections, max_slot


def gather_paths(table, root, start, opcode=OP_FORWARD, eject=True):
    """Serialize every source through the reverse X/Y tree into the root."""
    ejections, max_slot = [], start
    sources = [(x, y) for y in range(MY) for x in range(MX)]
    for index, source in enumerate(sources):
        x, y, slot, in_port = source[0], source[1], start + index * 16, "local"
        while (x, y) != root:
            if x != root[0]:
                port = "east" if x < root[0] else "west"
            else:
                port = "south" if y < root[1] else "north"
            add_entry(table, x, y, slot, in_port, [port], opcode)
            x += 1 if port == "east" else -1 if port == "west" else 0
            y += 1 if port == "south" else -1 if port == "north" else 0
            slot += delay(port)
            in_port = opposite(port)
        if eject:
            add_entry(table, x, y, slot, in_port, ["local"], opcode)
            ejections.append([x, y])
        else:
            add_entry(table, x, y, slot, in_port, [], opcode)
        max_slot = max(max_slot, slot + RAMP)
    return ejections, max_slot, sources


def export(name, collective, table, injections, expected, makespan, baseline):
    routers = []
    for y in range(MY):
        for x in range(MX):
            slots = [entry for (rx, ry, _), entry in sorted(table.items())
                     if (rx, ry) == (x, y)]
            if slots:
                routers.append({"router": [x, y], "slots": slots})
    payload = {
        "schema": "calendar-export/v1",
        "topology": {"mx": MX, "my": MY, "h": H, "v": V, "ramp_bw": RAMP},
        "collective": collective,
        "message_flits": 1,
        "schedule_kind": "synthetic_topology_correct",
        "timing_model": "slots are router-send cycles; H/V link and PE ramp delays are explicit",
        "lower_bound_estimate": 2 * RAMP + (MX - 1) * H + (MY - 1) * V,
        "baseline": baseline,
        "routers": routers,
        "injections": injections,
        "expected_ejections": expected,
        "expected_makespan": makespan,
        "notes": ("Synthetic replay vector. It is reproducible and topology-correct, "
                  "but is not a per-router conversion of superpose_6x8.json."),
    }
    path = OUT / f"{name}_m1.json"
    path.write_text(json.dumps(payload, separators=(",", ":"), indent=None) + "\n",
                    encoding="utf-8")
    print(f"{path.relative_to(ROOT)} makespan={makespan} slots={len(table)}")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    superpose = json.loads((ROOT / "results" / "superpose_6x8.json").read_text())
    solo = superpose["solo"]["1"]
    root = (0, 0)
    bcast_base = {"research_baseline": solo["bcast"]["multitree"],
                  "metric": "solo.bcast.multitree", "comparison": "not equivalent"}
    gather_base = {"research_baseline": solo["gather"]["multitree"],
                   "metric": "solo.gather.multitree", "comparison": "not equivalent"}
    ag_base = {"research_baseline": solo["ag"]["multitree"],
               "metric": "solo.ag.multitree", "comparison": "not equivalent"}

    table = {}
    ejected, end = bcast_tree(table, root, RAMP)
    export("bcast", "broadcast", table, [{"source": list(root), "slot": 0, "value": 1}],
           ejected, end, bcast_base)

    table = {}
    ejected, end, sources = gather_paths(table, root, RAMP)
    export("gather", "gather", table,
           [{"source": list(s), "slot": i * 16, "value": i + 1} for i, s in enumerate(sources)],
           ejected, end, gather_base)

    table = {}
    ejected, end, sources = gather_paths(table, root, RAMP, OP_ADD)
    export("reduce", "reduce", table,
           [{"source": list(s), "slot": i * 16, "value": i + 1} for i, s in enumerate(sources)],
           ejected, end, {"research_baseline": None, "comparison": "no reduce field in superpose"})

    table = {}
    _, reduce_end, sources = gather_paths(table, root, RAMP, OP_ADD, eject=False)
    ejected, end = bcast_tree(table, root, reduce_end + 1)
    export("allreduce", "allreduce", table,
           [{"source": list(s), "slot": i * 16, "value": i + 1} for i, s in enumerate(sources)] +
           [{"source": list(root), "slot": reduce_end, "value": 0}],
           ejected, end, {"research_baseline": None, "comparison": "no allreduce field in superpose"})

    table, expected, injections, end = {}, [], [], 0
    for index, source in enumerate(((0, 0), (5, 0), (0, 7), (5, 7))):
        leaves, finish = bcast_tree(table, source, 1 + index * 200)
        expected.extend(leaves)
        injections.append({"source": list(source), "slot": index * 200, "value": index + 1})
        end = max(end, finish)
    export("allgather", "allgather", table, injections, expected, end, ag_base)


if __name__ == "__main__":
    main()
