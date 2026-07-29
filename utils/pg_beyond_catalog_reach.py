#!/usr/bin/env python3
"""Beyond-catalog STRUCT vs disc reachability for PG routing schemes.

STRUCT = residual route graph still connected among live compute, but gen_*
cannot build a legal table (path / CDG). disc = graph already disconnected.
forced_sac = table built only after the scheme's built-in node retirement.

Merges into results/pg_beyond_catalog_reach.json (keeps existing scheme rows).

  python3 utils/pg_beyond_catalog_reach.py --schemes super_turn,super_turn_1vc,fault_half_ring
  python3 utils/pg_beyond_catalog_reach.py --quick   # smaller samples
"""
from __future__ import annotations

import argparse
import itertools
import json
import random
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.setrecursionlimit(10000)

import pg_faults_8x6 as F
import pg_routing as R
from pg_routing import MX, MY, coord, nid

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "pg_beyond_catalog_reach.json"
SEED = 20260728

SCHEME_META = {
    "super_turn": {
        "label": "M0s Super-turn",
        "solution": "连通残图上通常靠 2 VC Glass–Ni；失败时 solve_scheme 牺牲恢复。",
    },
    "super_turn_1vc": {
        "label": "M0s1 Super-turn 1VC",
        "solution": "硬顶 1 VC：转不动就牺牲；覆盖弱于双 VC Super-turn。",
    },
    "fault_half_ring": {
        "label": "M5h half-ring",
        "solution": "链路多靠端点退休（forced_sac）；半环/块失败 → STRUCT 或重牺牲。",
    },
    "updown": {
        "label": "M3 Up*/Down*",
        "solution": "牺牲孤立点 / 小子图内的 compute 节点。",
    },
}


def all_links() -> list[tuple[int, int]]:
    out = []
    for n in range(MX * MY):
        x, y = coord(n)
        if x + 1 < MX:
            out.append((n, nid(x + 1, y)))
        if y + 1 < MY:
            out.append((n, nid(x, y + 1)))
    return out


def mk_pg(dead_nodes=(), dead_links=()) -> dict:
    return F.expand_pg(
        {"name": "probe",
         "dead_nodes": list(dead_nodes),
         "dead_links": [tuple(l) for l in dead_links]},
        "dead")


def is_connected(pg: dict) -> bool:
    adj = pg["route_adj"]
    live = [n for n in pg["compute_nodes"] if adj.get(n)]
    if len(live) < 2:
        return False
    seen = {live[0]}
    q = deque([live[0]])
    while q:
        u = q.popleft()
        for v in adj.get(u, ()):
            if v in live and v not in seen:
                seen.add(v)
                q.append(v)
    return len(seen) == len(live)


def classify(pg: dict, scheme: str) -> str:
    """Return ok | forced_sac | struct | disc."""
    if not is_connected(pg):
        return "disc"
    gen = R.SCHEME_GENERATORS[scheme]
    try:
        raw = gen(pg)
    except RecursionError:
        return "struct"
    if raw is None:
        return "struct"
    forced = set(raw.get("forced_sacrificed") or [])
    compute = [n for n in raw.get("compute_nodes", pg["compute_nodes"])
               if n not in forced]
    adj = raw.get("route_adj", pg["route_adj"])
    paths = {k: v for k, v in raw["paths"].items()
             if k[0] in compute and k[1] in compute}
    if len(compute) < 2 or len(paths) != len(compute) * (len(compute) - 1):
        return "struct"
    ok, why = R.validate_routing(paths, compute, adj, raw.get("vc_of"))
    if not ok:
        return "struct"
    return "forced_sac" if forced else "ok"


def scan_space(scheme: str, cases: list[tuple], label: str) -> dict:
    counts = {"ok": 0, "forced_sac": 0, "struct": 0, "disc": 0}
    t0 = time.time()
    for i, (nodes, links) in enumerate(cases):
        v = classify(mk_pg(nodes, links), scheme)
        counts[v] += 1
        if (i + 1) % 200 == 0 or i + 1 == len(cases):
            print(f"  [{scheme:16s} {label:16s}] {i+1}/{len(cases)} "
                  f"{counts} {time.time()-t0:.0f}s", flush=True)
    # Drop zero forced_sac key if unused (match legacy style for tree schemes)
    if counts["forced_sac"] == 0:
        del counts["forced_sac"]
    return counts


def build_spaces(quick: bool) -> dict[str, list[tuple]]:
    links = all_links()
    nodes = list(range(MX * MY))
    rng = random.Random(SEED)
    spaces: dict[str, list[tuple]] = {
        "1_link": [([], [L]) for L in links],
        "1_node": [([n], []) for n in nodes],
    }
    # 2-fault exhaustive (or subsample in --quick)
    pairs_l = list(itertools.combinations(links, 2))
    pairs_n = list(itertools.combinations(nodes, 2))
    if quick:
        rng.shuffle(pairs_l)
        rng.shuffle(pairs_n)
        pairs_l = pairs_l[:400]
        pairs_n = pairs_n[:400]
    spaces["2_link"] = [([], list(p)) for p in pairs_l]
    spaces["2_node"] = [(list(p), []) for p in pairs_n]

    n3 = 800 if quick else 2000
    triples = list(itertools.combinations(nodes, 3))
    rng.shuffle(triples)
    spaces["3_node_sample"] = [(list(t), []) for t in triples[:n3]]

    mixed = []
    n_mix = 1000 if quick else 2000
    for _ in range(n_mix):
        nr = rng.randint(1, 4)
        nl = rng.randint(0, 4)
        dn = rng.sample(nodes, nr)
        dead = set(dn)
        cand = [L for L in links
                if L[0] not in dead and L[1] not in dead]
        dl = rng.sample(cand, min(nl, len(cand))) if cand else []
        mixed.append((dn, dl))
    spaces["mixed"] = mixed
    return spaces


def summarise(scheme: str, results: dict[str, dict]) -> tuple[bool, str]:
    """struct_possible + one-line summary from counts."""
    total_st = sum(r.get("struct", 0) for r in results.values())
    total_fs = sum(r.get("forced_sac", 0) for r in results.values())
    if scheme == "super_turn":
        if total_st == 0:
            return False, ("连通残图上 Glass–Ni（≤2 VC）实测 STRUCT=0；"
                           "失败集合与断连重合（或仅 forced 牺牲）。")
        return True, (f"连通残图上仍有 STRUCT（合计 {total_st}）；"
                      "多故障时双 VC 转向集不够用。")
    if scheme == "super_turn_1vc":
        if total_st == 0:
            return False, ("硬顶 1 VC 在扫过的空间里 STRUCT=0，"
                           "但常靠 forced 牺牲换通。")
        return True, (f"1 VC 转向空间更窄：STRUCT 合计 {total_st}；"
                      "覆盖明显弱于双 VC Super-turn。")
    if scheme == "fault_half_ring":
        if total_st == 0:
            return (total_fs > 0), (
                "链路故障几乎全靠端点退休（forced_sac）；"
                "扫过的空间未见半环 STRUCT，但牺牲代价高。")
        return True, (f"半环/块失败 STRUCT 合计 {total_st}；"
                      "链路端点退休 forced_sac 很常见。")
    return total_st > 0, SCHEME_META.get(scheme, {}).get("solution", "")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--schemes", default=
                    "super_turn,super_turn_1vc,fault_half_ring",
                    help="comma-separated scheme keys to (re)scan")
    ap.add_argument("--quick", action="store_true",
                    help="subsample 2-fault / 3-node / mixed")
    ap.add_argument("--spaces", default="",
                    help="comma-separated space keys (default: all built)")
    args = ap.parse_args()
    schemes = [s.strip() for s in args.schemes.split(",") if s.strip()]
    spaces = build_spaces(quick=args.quick)
    if args.spaces:
        keep = {s.strip() for s in args.spaces.split(",")}
        spaces = {k: v for k, v in spaces.items() if k in keep}

    doc = {"meta": {}, "spaces": {}, "schemes": {}}
    if OUT.exists():
        doc = json.loads(OUT.read_text())

    # Refresh space labels / sizes for scanned keys
    labels = {
        "1_link": "单链路断（全）",
        "1_node": "单节点死（全）",
        "2_link": "双链路断（全）" if not args.quick else "双链路断（抽样）",
        "2_node": "双节点死（全）" if not args.quick else "双节点死（抽样）",
        "3_node_sample": "三节点死（抽样）",
        "mixed": "随机混合死点+断链（抽样）",
    }
    for k, cases in spaces.items():
        doc.setdefault("spaces", {})[k] = {
            "n": len(cases),
            "label": labels.get(k, k),
        }

    t_all = time.time()
    for sch in schemes:
        print(f"=== {sch} ===", flush=True)
        results = {}
        for sk, cases in spaces.items():
            results[sk] = scan_space(sch, cases, sk)
        struct_possible, summary = summarise(sch, results)
        meta = SCHEME_META.get(sch, {"label": sch, "solution": ""})
        doc.setdefault("schemes", {})[sch] = {
            "label": meta["label"],
            "struct_possible": struct_possible,
            "summary": summary,
            "solution": meta["solution"],
            "results": results,
        }

    doc["meta"] = {
        "note": ("STRUCT = residual route graph still connected among live "
                 "compute, but gen_* cannot build a legal table. disc = graph "
                 "disconnected. Numbers from utils/pg_beyond_catalog_reach.py."),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seed": SEED,
        "quick": args.quick,
        "elapsed_s": round(time.time() - t_all, 1),
        "rescanned": schemes,
    }
    OUT.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {OUT} ({time.time()-t_all:.0f}s)", flush=True)
    for sch in schemes:
        s = doc["schemes"][sch]
        print(f"  {s['label']}: STRUCT={'会' if s['struct_possible'] else '否'} "
              f"— {s['summary']}")


if __name__ == "__main__":
    main()
