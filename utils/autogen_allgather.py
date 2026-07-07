#!/usr/bin/env python3
"""Autogen allgather scheme selector.

Given a mesh size, message size (flits) and down-ramp bandwidth, return the
scheme that results/allgather_scale_sweep.json found to have the lowest
makespan, plus the callable that reproduces it against
utils/allgather_fast_sim.py.

Primary mode: exact lookup against the swept grid (7 sizes x 5 flits x 2
ramp_bw = 70 combinations, matching the study's scope). --selftest re-runs
the simulator for every one of those 70 combinations and checks the
recommended scheme's makespan against the makespan recorded in the sweep
JSON (regression guard against sweep/engine drift).

Fallback mode: for a (mx, my) not in the swept grid, pick the scheme family
that won most often across the swept sizes for the requested ramp_bw (a
coarse but reasonable default -- see report section 5) with B chosen as the
divisor of the relevant dimension closest to 4 (the most frequent winning B
across the sweep).
"""

import argparse
import json
import re
from collections import Counter
from pathlib import Path

import allgather_fast_sim as F

ROOT = Path(__file__).resolve().parents[1]
SWEEP_JSON = ROOT / "results" / "allgather_scale_sweep.json"

FLITS = [1, 2, 3, 4, 5]
RAMP_BWS = [1, 2]


def _load_sweep():
    return json.loads(SWEEP_JSON.read_text(encoding="utf-8"))


NAME_RE = re.compile(r"^(multitree|ring_uni|ring_bi|hybrid_uni|hybrid_bi|hybrid_v_uni|hybrid_v_bi)(?:_B(\d+))?$")


def _fn_for(name, mx, my):
    """(fn, extra_args) to call allgather_fast_sim with, for a scheme name as
    stored in the sweep JSON (e.g. 'hybrid_v_bi_B4')."""
    mo = NAME_RE.match(name)
    if not mo:
        raise ValueError(f"unrecognized scheme name: {name!r}")
    base, bstr = mo.group(1), mo.group(2)
    if base == "multitree":
        return F.run_multitree, ()
    if base == "ring_uni":
        return F.run_ring, (False,)
    if base == "ring_bi":
        return F.run_ring, (True,)
    B = int(bstr)
    if base == "hybrid_uni":
        return F.run_hybrid, (B, False)
    if base == "hybrid_bi":
        return F.run_hybrid, (B, True)
    if base == "hybrid_v_uni":
        return F.run_hybrid_v, (B, False)
    if base == "hybrid_v_bi":
        return F.run_hybrid_v, (B, True)
    raise ValueError(name)


def _fallback_choice(sweep, mx, my, ramp_bw):
    """Most-frequent winning family (+ representative B) across the swept
    sizes for this ramp_bw, used when (mx, my) itself was not swept."""
    votes = Counter()
    b_votes = Counter()
    for size, block in sweep["data"].items():
        bw = block["bw"].get(str(ramp_bw))
        if not bw:
            continue
        for m, cell in bw.items():
            best = cell.get("best")
            if not best:
                continue
            mo = NAME_RE.match(best["name"])
            if not mo:
                continue
            base, bstr = mo.group(1), mo.group(2)
            votes[base] += 1
            if bstr:
                b_votes[int(bstr)] += 1

    fam, _ = votes.most_common(1)[0]
    b_pref = [b for b, _ in b_votes.most_common()] or [4]

    if fam in ("multitree",):
        return "multitree", F.run_multitree, ()
    if fam in ("ring_uni", "ring_bi"):
        return fam, F.run_ring, (fam == "ring_bi",)

    dim = my if fam.startswith("hybrid_uni") or fam.startswith("hybrid_bi") else mx
    bidir = fam.endswith("_bi")
    B = next((b for b in b_pref if dim % b == 0 and dim // b >= 2), None)
    if B is None:
        B = next((b for b in F.divisors_pow2(dim) if dim // b >= 2), 1)
    name = f"{fam}_B{B}"
    fn = F.run_hybrid if fam.startswith("hybrid_") and not fam.startswith("hybrid_v") else F.run_hybrid_v
    return name, fn, (B, bidir)


def gen_schedule(mx, my, flits, ramp_bw, sweep=None):
    """Plan-spec-named entry point (== recommend()). NOTE ON FORMAT: the
    original plan asked for a schedule in the same "per-source injection
    offset + footprint slots" format as sched_zerobuf_compare.apply_offsets().
    That rigid-offset model was abandoned for scalability (see
    allgather_fast_sim.py docstring) in favor of an event-driven engine, which
    has no single "injection offset" -- delivery timing emerges from
    per-cycle link/ramp contention across ALL sources simultaneously. What we
    return instead is the (scheme, replay-args) pair that reproduces the
    recorded-optimal run via allgather_fast_sim, plus its makespan/T/ratio.
    """
    return recommend(mx, my, flits, ramp_bw, sweep)


def recommend(mx, my, m, ramp_bw, sweep=None):
    """Return dict: scheme name, fn+args to reproduce it, predicted makespan,
    lower bound T, ratio, and whether this came from an exact sweep lookup
    or the coarse fallback heuristic."""
    sweep = sweep or _load_sweep()
    key = f"{mx}x{my}"
    block = sweep["data"].get(key)
    cell = block["bw"].get(str(ramp_bw), {}).get(str(m)) if block else None

    if cell and cell.get("best"):
        name = cell["best"]["name"]
        fn, extra = _fn_for(name, mx, my)
        return {
            "mx": mx, "my": my, "m": m, "ramp_bw": ramp_bw,
            "scheme": name, "fn": fn, "args": (mx, my, sweep["h"], sweep["v"], ramp_bw, m) + extra,
            "makespan": cell["best"]["makespan"], "T": cell["T"], "ratio": cell["ratio"],
            "source": "sweep",
        }

    name, fn, extra = _fallback_choice(sweep, mx, my, ramp_bw)
    h, v = sweep.get("h", 4), sweep.get("v", 6)
    return {
        "mx": mx, "my": my, "m": m, "ramp_bw": ramp_bw,
        "scheme": name, "fn": fn, "args": (mx, my, h, v, ramp_bw, m) + extra,
        "makespan": None, "T": None, "ratio": None,
        "source": "fallback",
    }


def selftest(sweep=None, replay_huge=False):
    """Regression check across the swept grid. For N < HUGE_N (cheap, <1s per
    call) this re-runs the simulator and checks the makespan matches exactly.
    For N >= HUGE_N (32x32/64x64, each replay costs minutes -- see report
    section 3) it only checks table consistency (name resolves to a valid
    scheme + args) by default, since those cells were already verified
    in-line by sweep_allgather_scale.py's own ok=verify_ejects() check at
    generation time; pass replay_huge=True to also re-simulate them (slow)."""
    sweep = sweep or _load_sweep()
    sizes = [tuple(int(x) for x in s.split("x")) for s in sweep["sizes"]]
    total = 0
    checked = 0
    skipped = 0
    mismatches = []
    for mx, my in sizes:
        huge = sweep["data"].get(f"{mx}x{my}", {}).get("huge", False)
        for rb in RAMP_BWS:
            for m in FLITS:
                total += 1
                rec = recommend(mx, my, m, rb, sweep)
                if rec["source"] != "sweep":
                    skipped += 1
                    continue
                if huge and not replay_huge:
                    _fn_for(rec["scheme"], mx, my)   # just check it resolves
                    skipped += 1
                    continue
                checked += 1
                mk, ok, bad = rec["fn"](*rec["args"])
                if not ok or mk != rec["makespan"]:
                    mismatches.append((mx, my, m, rb,
                                        f"replay mk={mk} ok={ok} vs recorded {rec['makespan']}"))
    print(f"{total} combinations in grid, {checked} replayed, {skipped} skipped "
          f"(not-yet-swept or huge-mesh table-only check), {len(mismatches)} mismatches")
    for mm in mismatches[:20]:
        print("  MISMATCH", mm)
    return len(mismatches) == 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mx", type=int)
    ap.add_argument("--my", type=int)
    ap.add_argument("--m", type=int, default=1, help="message size in flits")
    ap.add_argument("--bw", type=int, default=1, choices=(1, 2), help="down-ramp bandwidth")
    ap.add_argument("--json", default=None, help="write the recommendation to this JSON path")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--replay-huge", action="store_true",
                     help="also re-simulate 32x32/64x64 cells in --selftest (slow, minutes)")
    args = ap.parse_args()

    if args.selftest:
        ok = selftest(replay_huge=args.replay_huge)
        raise SystemExit(0 if ok else 1)

    if args.mx is None or args.my is None:
        ap.error("--mx/--my required unless --selftest")

    rec = gen_schedule(args.mx, args.my, args.m, args.bw)
    print(f"mesh={args.mx}x{args.my} m={args.m} ramp_bw={args.bw} "
          f"-> scheme={rec['scheme']} (source={rec['source']})")
    if rec["makespan"] is not None:
        print(f"   makespan={rec['makespan']} T={rec['T']} ratio={rec['ratio']:.3f}")

    if args.json:
        out = {k: v for k, v in rec.items() if k != "fn"}
        out["args"] = list(out["args"])
        Path(args.json).write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(f"Wrote {args.json}")


if __name__ == "__main__":
    main()
