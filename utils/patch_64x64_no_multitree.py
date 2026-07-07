#!/usr/bin/env python3
"""One-shot patch: replace results/allgather_scale_sweep.json's 64x64 block
with the multitree-excluded, buffer-witness-verified data from
results/zerobuf_64x64_witness.json (see sweep_64x64_zerobuf_witness.py).

Why: the pre-existing 64x64 cells were computed BEFORE buffer instrumentation
existed, so they carry no max_link_wait/max_ramp_wait -> autogen_allgather.
recommend() cannot buffer-filter them and silently falls back to raw
"fastest" (= multitree), exactly the artifact the user flagged. This patch
makes 64x64 self-consistent with 32x32 (which already carries buffer stats)
and drops multitree from the candidate pool entirely, per explicit request.

m=2 and m=4 had NO cell at all before (never swept); this patch adds them
using the one candidate that was extended to full m=1..5 for that ramp_bw
(hybrid_v_uni_B1 for bw=1, hybrid_v_bi_B1 for bw=2 -- both certified exact
zero-buffer for m=1..4, see report sec 3.6).
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SWEEP_JSON = ROOT / "results" / "allgather_scale_sweep.json"
WITNESS_JSON = ROOT / "results" / "zerobuf_64x64_witness.json"
LB_JSON = ROOT / "results" / "allgather_lb.json"


def main():
    sweep = json.loads(SWEEP_JSON.read_text(encoding="utf-8"))
    witness = json.loads(WITNESS_JSON.read_text(encoding="utf-8"))
    lb = json.loads(LB_JSON.read_text(encoding="utf-8"))
    t_by_bw_m = lb["data"]["64x64"]["bw"]

    by_bw_m = {}
    for r in witness:
        by_bw_m.setdefault((r["ramp_bw"], r["m"]), []).append(r)

    block = sweep["data"]["64x64"]
    for bw in (1, 2):
        for m in (1, 2, 3, 4, 5):
            recs = by_bw_m.get((bw, m), [])
            if not recs:
                continue
            results = [{"name": r["name"], "makespan": r["makespan"], "ok": r["ok"],
                        "bad": [], "max_link_wait": r["max_link_wait"],
                        "max_ramp_wait": r["max_ramp_wait"]} for r in recs]
            best = min(results, key=lambda r: r["makespan"])
            T = t_by_bw_m[str(bw)][str(m)]["T"]
            block["bw"].setdefault(str(bw), {})[str(m)] = {
                "T": T, "best": best, "results": results,
                "note": "multitree excluded; zero-buffer-witness verified "
                        "(see sweep_64x64_zerobuf_witness.py / report sec 3.6)",
            }

    sweep.setdefault("notes", {})["64x64"] = (
        "2026-07-07 起 64x64 数据已整体替换：排除 multitree（已证实需要单节点 10233 flit "
        "下ramp缓冲，见 3.5 节），改为对 ring_uni/ring_bi/hybrid_(v_)uni/bi 的 B∈{1,2,4} "
        "变体做 zero-buffer witness 复核（max_link_wait==max_ramp_wait==0 时视为已证明的严格"
        "零buffer调度，见 3.6 节方法说明）。m=2/4 此前从未被扫描，现按同方法补齐（只复核了当前"
        "ramp_bw 下 m=1 的 zero-buffer 冠军方案的 m=2..4 数据点，非全量候选重扫）。"
    )

    SWEEP_JSON.write_text(json.dumps(sweep, indent=2), encoding="utf-8")
    print(f"Patched 64x64 in {SWEEP_JSON}")
    for bw in (1, 2):
        for m in (1, 2, 3, 4, 5):
            cell = block["bw"][str(bw)].get(str(m))
            if cell:
                print(f"  bw={bw} m={m}: best={cell['best']['name']} mk={cell['best']['makespan']} T={cell['T']}")


if __name__ == "__main__":
    main()
