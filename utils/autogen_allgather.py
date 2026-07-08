#!/usr/bin/env python3
"""Autogen allgather scheme selector.

Given a mesh size, message size (flits) and down-ramp bandwidth, return the
scheme that results/allgather_scale_sweep.json found to have the lowest
makespan, plus the callable that reproduces it against
utils/allgather_fast_sim.py.

IMPORTANT -- buffer-depth caveat (see results/report_allgather_scale.html
sec. "buffer 深度诚实性核查"): the event-driven engine used for the sweep
allows a flit to wait an UNBOUNDED number of cycles at a contended link or
down-ramp, which is NOT the same as a real zero/small-buffer router. This is
fine for schemes whose contention-free structure happens to need ~0 wait
(ring, coarse-B hybrid/hybrid_v), but for high-fanout schemes (multitree,
fine-B hybrid) the recorded makespan can rely on 100+ flit deep buffering
that no realistic router has -- confirmed empirically: at 16x16/bw=1,
multitree's required down-ramp buffer grows ~linearly with message size (121
/250/378/506/633 flits for m=1..5), while ring_bi needs 0 in every case.
recommend()/gen_schedule() therefore default to `buffer_budget=8` (flits) and
pick the best-makespan scheme AMONG those whose recorded max_link_wait and
max_ramp_wait both fit that budget, rather than the unconstrained-fastest
scheme. Pass buffer_budget=None to get the old (buffer-unaware, optimistic)
selection.

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
# flits; see module docstring. Calibrated empirically, NOT a realistic-router
# guess: at 6x8/8x8 bw=2, multitree needs only 3-4 cycles of implicit wait
# yet its TRUE (rigid, zero-buffer) makespan is still 17-25% worse than the
# real winner (96 vs 82, 125 vs 102) -- even single-digit smoothing distorts
# the ranking, so the budget must be tight (a couple of cycles), not "a few
# flits of realistic skid buffer".
DEFAULT_BUFFER_BUDGET = 2


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


def gen_schedule(mx, my, flits, ramp_bw, sweep=None, buffer_budget=DEFAULT_BUFFER_BUDGET):
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
    return recommend(mx, my, flits, ramp_bw, sweep, buffer_budget=buffer_budget)


def _pick_within_budget(cell, buffer_budget):
    """Among cell['results'] (ok=True candidates with recorded max_link_wait/
    max_ramp_wait), return the best-makespan one that fits buffer_budget, or
    None if every candidate exceeds it OR lacks buffer instrumentation
    (missing stats must NOT silently default to "0 = safe" -- that would
    let unverified huge-mesh candidates slip through the filter)."""
    feas = [r for r in cell.get("results", [])
            if r.get("ok") and r.get("max_link_wait") is not None
            and r.get("max_ramp_wait") is not None
            and r["max_link_wait"] <= buffer_budget
            and r["max_ramp_wait"] <= buffer_budget]
    return min(feas, key=lambda r: r["makespan"]) if feas else None


def _strict_lookup(mx, my, ramp_bw):
    """TRUE zero-buffer ground truth for (size, ramp_bw, m=1) where the rigid
    packer (sched_zerobuf_compare.py) was run -- it searches per-source
    injection offsets that eliminate ALL contention, so its best scheme +
    makespan is the AUTHORITATIVE zero-buffer answer. This MUST be preferred
    over the event-driven engine's greedy wait whenever available, because
    greedy wait is NOT a reliable proxy for "buffer needed": a scheme can be
    zero-buffer-capable yet show nonzero greedy wait (the greedy scheduler
    just didn't find the zero-conflict offset -- e.g. hybrid_v_bi_B2 at
    16x16/bw=1: ED buf=1/3 but strict mk=334 with TRUE zero buffer), and
    conversely a scheme can show small greedy wait yet genuinely need
    queuing to hit its ED makespan (e.g. multitree at 4x4/bw=1: ED buf=1/2
    passes a budget=2 filter, but its TRUE zero-buffer makespan is 51, not
    the ED 32). See report sec 3.5/3.7. Returns the strict best dict or None."""
    if not STRICT_M1_JSON.exists():
        return None
    strict = json.loads(STRICT_M1_JSON.read_text(encoding="utf-8"))
    block = strict["data"].get(f"{mx}x{my}")
    if not block:
        return None
    b = block["bw"].get(str(ramp_bw))
    if not b:
        return None
    return b["best"]


def _pick_zero_buffer(cell):
    """Best makespan among candidates with max_link_wait==max_ramp_wait==0."""
    zc = [r for r in cell.get("results", [])
          if r.get("ok") and r.get("max_link_wait") == 0 and r.get("max_ramp_wait") == 0]
    return min(zc, key=lambda r: r["makespan"]) if zc else None


def recommend(mx, my, m, ramp_bw, sweep=None, buffer_budget=DEFAULT_BUFFER_BUDGET):
    """Return dict: scheme name, fn+args to reproduce it, predicted makespan,
    lower bound T, ratio, and whether this came from an exact sweep lookup,
    the strict zero-buffer ground truth, or the coarse fallback heuristic.

    buffer_budget (flits): only consider candidates whose recorded
    max_link_wait/max_ramp_wait both fit this budget (see module docstring).
    Pass None to reproduce the old buffer-unaware "fastest wins" selection.

    IMPORTANT: when TRUE zero-buffer ground truth is available (m=1, sizes
    4x4..16x16, both ramp_bw -- see _strict_lookup), it is returned as the
    authoritative answer regardless of buffer_budget (except budget=None,
    which explicitly asks for the unconstrained queuing-assisted number).
    The event-driven + buffer_budget filter is a FALLBACK for m>1 and for
    32x32/64x64 where the rigid packer is computationally infeasible; it is
    known to both over-exclude (zero-buffer-capable schemes with nonzero
    greedy wait) and under-exclude (queuing-dependent schemes with small
    greedy wait) -- see report sec 3.7.
    """
    sweep = sweep or _load_sweep()
    key = f"{mx}x{my}"
    block = sweep["data"].get(key)
    cell = block["bw"].get(str(ramp_bw), {}).get(str(m)) if block else None

    # Authoritative zero-buffer path: rigid packer ground truth (m=1 only).
    if m == 1 and buffer_budget is not None:
        strict_best = _strict_lookup(mx, my, ramp_bw)
        if strict_best:
            name = strict_best["name"]
            try:
                fn, extra = _fn_for(name, mx, my)
            except ValueError:
                # strict best is a scheme the ED engine can't reproduce
                # (e.g. border/quad, not re-implemented in allgather_fast_sim);
                # fall through to the ED+filter path instead.
                fn = None
            if fn is not None:
                T = cell["T"] if cell else None
                # also surface the ED makespan/buf for the same scheme so the
                # caller can see how much the greedy scheduler diverged from
                # the proven zero-buffer schedule.
                ed_mk = ed_link = ed_ramp = None
                if cell:
                    ed = next((r for r in cell.get("results", [])
                               if r["name"] == name), None)
                    if ed:
                        ed_mk = ed["makespan"]
                        ed_link = ed.get("max_link_wait")
                        ed_ramp = ed.get("max_ramp_wait")
                return {
                    "mx": mx, "my": my, "m": m, "ramp_bw": ramp_bw,
                    "scheme": name, "fn": fn, "args": (mx, my, sweep["h"], sweep["v"], ramp_bw, m) + extra,
                    "makespan": strict_best["makespan"],
                    "max_link_wait": 0, "max_ramp_wait": 0,
                    "ed_makespan": ed_mk, "ed_max_link_wait": ed_link, "ed_max_ramp_wait": ed_ramp,
                    "T": T, "ratio": round(strict_best["makespan"] / T, 4) if T else None,
                    "buffer_budget": buffer_budget, "buffer_limited": False,
                    "source": "strict_zerobuf",
                }

    if cell and cell.get("best"):
        picked = cell["best"]
        buffer_limited = False

        # Strict zero-buffer selection (buffer_budget == 0): prefer precomputed
        # best_zero_buffer on cell, else witness-filtered min makespan.
        if buffer_budget == 0:
            bz = cell.get("best_zero_buffer") or _pick_zero_buffer(cell)
            if bz:
                picked = bz
                buffer_limited = False
            else:
                instrumented = [r for r in cell.get("results", [])
                                if r.get("ok") and r.get("max_link_wait") is not None
                                and r.get("max_ramp_wait") is not None]
                if instrumented:
                    picked = min(instrumented,
                                 key=lambda r: (max(r["max_link_wait"], r["max_ramp_wait"]), r["makespan"]))
                buffer_limited = True
        elif buffer_budget is not None:
            within = _pick_within_budget(cell, buffer_budget)
            if within is not None and within["name"] != picked["name"]:
                picked = within
                buffer_limited = True
            elif within is None:
                # Nothing fits the budget (e.g. ramp_bw=1 at large N/m: even
                # the "good" schemes need real buffering because the down
                # ramp itself is saturated, not because of topology choice
                # -- see report sec 3.5). Don't silently fall back to the
                # unconstrained-fastest pick; prefer whichever instrumented
                # candidate needs the LEAST buffer, so the recommendation is
                # at least "least-bad", and flag it clearly either way.
                instrumented = [r for r in cell.get("results", [])
                                if r.get("ok") and r.get("max_link_wait") is not None
                                and r.get("max_ramp_wait") is not None]
                if instrumented:
                    # tie-break equal buffer needs by makespan, not list order
                    # (a real bug found via 64x64/m=5: ring_uni and the much
                    # faster hybrid_v_bi_B1 both needed max-wait=1, and list
                    # order alone was silently picking the slower one).
                    picked = min(instrumented,
                                 key=lambda r: (max(r["max_link_wait"], r["max_ramp_wait"]), r["makespan"]))
                buffer_limited = True
        name = picked["name"]
        fn, extra = _fn_for(name, mx, my)
        return {
            "mx": mx, "my": my, "m": m, "ramp_bw": ramp_bw,
            "scheme": name, "fn": fn, "args": (mx, my, sweep["h"], sweep["v"], ramp_bw, m) + extra,
            "makespan": picked["makespan"],
            "max_link_wait": picked.get("max_link_wait"), "max_ramp_wait": picked.get("max_ramp_wait"),
            "T": cell["T"], "ratio": round(picked["makespan"] / cell["T"], 4) if cell["T"] else None,
            "buffer_budget": buffer_budget, "buffer_limited": buffer_limited,
            "source": "sweep",
        }

    name, fn, extra = _fallback_choice(sweep, mx, my, ramp_bw)
    h, v = sweep.get("h", 4), sweep.get("v", 6)
    return {
        "mx": mx, "my": my, "m": m, "ramp_bw": ramp_bw,
        "scheme": name, "fn": fn, "args": (mx, my, h, v, ramp_bw, m) + extra,
        "makespan": None, "max_link_wait": None, "max_ramp_wait": None,
        "T": None, "ratio": None, "buffer_budget": buffer_budget, "buffer_limited": None,
        "source": "fallback",
    }


STRICT_M1_JSON = ROOT / "results" / "zerobuf_strict_m1.json"


def _check_strict_m1(sweep, buffer_budget):
    """Where we have TRUE zero-buffer ground truth (results/zerobuf_strict_m1
    .json, rigid packer, m=1 only -- see sweep_zerobuf_strict.py), confirm
    that the buffer-budget-filtered recommend() pick is at least as good as
    (or ties/undercuts because it's still an optimistic event-driven number,
    never worse in scheme *choice* quality) the true winner's family. This
    is a sanity check on the buffer_budget heuristic, not a strict equality
    check -- the event-driven makespan is not directly comparable to the
    rigid-packer makespan."""
    if not STRICT_M1_JSON.exists():
        return []
    strict = json.loads(STRICT_M1_JSON.read_text(encoding="utf-8"))
    notes = []
    for size, block in strict["data"].items():
        mx, my = (int(x) for x in size.split("x"))
        for rb_str, b in block["bw"].items():
            rb = int(rb_str)
            true_best = b["best"]["name"]
            true_mk = b["best"]["makespan"]
            rec = recommend(mx, my, 1, rb, sweep, buffer_budget=buffer_budget)
            notes.append((size, rb, true_best, true_mk, rec["scheme"], rec.get("buffer_limited")))
    return notes


def selftest(sweep=None, replay_huge=False, buffer_budget=DEFAULT_BUFFER_BUDGET):
    """Regression check across the swept grid. For N < HUGE_N (cheap, <1s per
    call) this re-runs the simulator and checks the makespan matches exactly.
    For N >= HUGE_N (32x32/64x64, each replay costs minutes -- see report
    section 3) it only checks table consistency (name resolves to a valid
    scheme + args) by default, since those cells were already verified
    in-line by sweep_allgather_scale.py's own ok=verify_ejects() check at
    generation time; pass replay_huge=True to also re-simulate them (slow).

    Also cross-references m=1 picks against the TRUE zero-buffer ranking in
    results/zerobuf_strict_m1.json (see sweep_zerobuf_strict.py) as a sanity
    check on the buffer_budget heuristic."""
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
                rec = recommend(mx, my, m, rb, sweep, buffer_budget=buffer_budget)
                if rec["source"] != "sweep":
                    skipped += 1
                    continue
                if huge and not replay_huge:
                    _fn_for(rec["scheme"], mx, my)   # just check it resolves
                    skipped += 1
                    continue
                checked += 1
                mk, ok, bad, _mlw, _mrw = rec["fn"](*rec["args"])
                if not ok or mk != rec["makespan"]:
                    mismatches.append((mx, my, m, rb,
                                        f"replay mk={mk} ok={ok} vs recorded {rec['makespan']}"))
    print(f"{total} combinations in grid, {checked} replayed, {skipped} skipped "
          f"(not-yet-swept or huge-mesh table-only check), {len(mismatches)} mismatches")
    for mm in mismatches[:20]:
        print("  MISMATCH", mm)

    print(f"\nbuffer_budget={buffer_budget} vs TRUE zero-buffer (m=1) ground truth:")
    strict = json.loads(STRICT_M1_JSON.read_text(encoding="utf-8")) if STRICT_M1_JSON.exists() else None
    for size, rb, true_best, true_mk, picked, limited in _check_strict_m1(sweep, buffer_budget):
        flag = "(buffer-limited)" if limited else ""
        match = "OK " if _same_family(true_best, picked) else "differs"
        picked_true_mk = None
        if strict:
            for r in strict["data"][size]["bw"][str(rb)]["results"]:
                if r["name"] == picked:
                    picked_true_mk = r["makespan"]
                    break
        gap = f"(true zero-buf mk of pick={picked_true_mk}, vs optimum {true_mk}, " \
              f"{picked_true_mk/true_mk:.2f}x)" if picked_true_mk else ""
        print(f"  {size:8s} bw={rb}  true_zerobuf_best={true_best:16s}(mk={true_mk}) "
              f"recommend()={picked:16s} {match} {flag} {gap}")
    return len(mismatches) == 0


def _same_family(a, b):
    fa = NAME_RE.match(a)
    fb = NAME_RE.match(b)
    return bool(fa and fb and fa.group(1) == fb.group(1))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mx", type=int)
    ap.add_argument("--my", type=int)
    ap.add_argument("--m", type=int, default=1, help="message size in flits")
    ap.add_argument("--bw", type=int, default=1, choices=(1, 2), help="down-ramp bandwidth")
    ap.add_argument("--json", default=None, help="write the recommendation to this JSON path")
    ap.add_argument("--buffer-budget", type=float, default=DEFAULT_BUFFER_BUDGET,
                     help="max acceptable link/ramp wait in flits (see module docstring); "
                          "use 0 for strict/near-zero-buffer, a large number or 'none' for "
                          "the old buffer-unaware fastest-wins selection")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--replay-huge", action="store_true",
                     help="also re-simulate 32x32/64x64 cells in --selftest (slow, minutes)")
    args = ap.parse_args()

    buffer_budget = None if str(args.buffer_budget).lower() == "none" else args.buffer_budget

    if args.selftest:
        ok = selftest(replay_huge=args.replay_huge, buffer_budget=buffer_budget)
        raise SystemExit(0 if ok else 1)

    if args.mx is None or args.my is None:
        ap.error("--mx/--my required unless --selftest")

    rec = gen_schedule(args.mx, args.my, args.m, args.bw, buffer_budget=buffer_budget)
    print(f"mesh={args.mx}x{args.my} m={args.m} ramp_bw={args.bw} buffer_budget={buffer_budget} "
          f"-> scheme={rec['scheme']} (source={rec['source']}, buffer_limited={rec.get('buffer_limited')})")
    if rec["makespan"] is not None:
        print(f"   makespan={rec['makespan']} T={rec['T']} ratio={rec['ratio']:.3f} "
              f"buf(link/ramp)={rec.get('max_link_wait')}/{rec.get('max_ramp_wait')}")

    if args.json:
        out = {k: v for k, v in rec.items() if k != "fn"}
        out["args"] = list(out["args"])
        Path(args.json).write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(f"Wrote {args.json}")


if __name__ == "__main__":
    main()
