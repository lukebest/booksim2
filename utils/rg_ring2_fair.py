#!/usr/bin/env python3
"""S23: fair-share deterministic pacing, two timescales.

Why this shape
--------------
Three measured facts on the corrected fabric pin the design down almost
completely.

**1. The unfairness is a persistent rate split, not jitter.** The ten cores form
a fixed six-fast / four-slow group whose long-run rates differ by 1.69x, and the
four slow ones are structurally determined: cores 0, 8, 10 and 18 sit at the
exits of the two HA-less nodes and must board the ring's busiest hops, where
in-ring traffic has absolute priority. With timing jitter perfectly removed, S0
still only reaches Jain 0.953. So **rate has to actually move** -- regularising
*when* a core injects cannot get past 0.953, and S21 demonstrates exactly that
(Jain 0.99 bought with 26% of the bandwidth).

**2. A 50-cycle Jain above 0.99 requires more than fairness -- it requires
regularity.** A perfectly fair but *memoryless* arbiter scores only ~0.970
through a 50-cycle window, because multinomial counting noise alone caps it
there. The ideal controller reaches 0.9997 by being deterministic. So the
actuator has to be an interval process, not a token bucket with slack.

**3. The bus costs 30 cycles, and that is only fatal to a short control window.**
S22 controls on a 2-cycle window, so a 30-cycle-stale deficit is fifteen windows
old and it misfires badly -- Jain 0.740, *worse than no control at all*. Widening
the window to 32 restores the bandwidth but caps Jain at 0.941. The lesson is not
"the bus is unusable", it is that **the two jobs live on different timescales**:
a persistent rate split does not decorrelate in 30 cycles, but 50-cycle
regularity cannot survive any feedback delay at all.

Mechanism: split the controller along that seam
-----------------------------------------------
*Fast loop, purely local, zero delay* -- a deterministic credit pacer per
(core, VC, **direction**):

    credit += rate      every cycle, capped at `fair_burst`
    board               only when credit >= 1, then credit -= 1

`fair_burst = 1` makes injection a strict interval process, which is what buys
the sub-Poisson regularity point 2 demands. Keying on direction as well as VC is
not a detail: with per-direction ports a core owns two board ports per VC and can
legitimately inject two flits in one cycle, so a single shared credit counter
(what S21 does) silently serialises them and throws away half the core's
ceiling. The per-direction ceiling is 1 flit/cycle, so `rate` is capped at 1.

*Slow loop, globally informed, tolerant of 30 cycles* -- every `fair_window`
cycles a core compares its own achieved count against the **mean** of its peers:

    own > share * (1 + tol)  ->  rate -= step        (give the lead back)
    otherwise                ->  rate += step, up to the ceiling

Two choices in there matter. Comparing against the **mean** rather than the
slowest peer is what keeps the bandwidth: trimming everyone down to the slowest
core is what cost S21 21%, whereas trimming only the cores that are *ahead*
hands their slots to the starved ones, and on this fabric a freed injection slot
upstream is exactly what an in-ring-priority-starved core needs. And the rate is
set **only** by comparison with peers, never from the core's own achieved rate --
S21's EWMA feeds back on what it achieved *while the pacer was withholding
credit*, so a slot-limited core ratchets down to the floor and cannot climb back.
That self-reference is the bug; removing it is the fix.

Signalling: `fair_signal`
-------------------------
`"bus"` posts the achieved count on the same 6-bit broadcast S1 uses, charged the
mandated `fair_bus_lat = 30`. Since `fair_window` is >= 64, the staleness is a
fraction of a window rather than many windows, which is what point 3 says is
required.

`"inband"` needs **no bus at all**. Every flit already carries its source, and
every node already inspects every flit crossing it in order to route it, so a
node can simply count crossings per source over the window and take the mean of
what it saw. That is the same fair-share reference, obtained with zero wires and
zero added latency -- and it is arguably the more honest signal, because a node
observes precisely the congestion on the hop it has to inject into.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from rg_ring2_base import Flit, Ring2BaseParams, Ring2BaseSim
from rg_ring2_topo import PlaneId, Ring2Topology, is_core


@dataclass
class Ring2FairParams(Ring2BaseParams):
    fair_window: int = 128           # control window, cycles; must exceed the
                                     # signalling delay by a good margin
    fair_burst: float = 1.0          # credit cap -> strict interval pacing
    fair_tol: float = 0.05           # allowed lead over the fair share
    fair_step: float = 0.05          # rate trim / release step
    fair_init: float = 1.0           # per-direction ceiling is 1 flit/cycle
    fair_floor: float = 0.10         # never pace a core into silence
    fair_vcs: tuple[str, ...] = ("dat",)
    fair_signal: str = "inband"      # "inband" | "bus"
    fair_bus_lat: int = 30           # the mandated bus delay, when used


class Ring2FairSim(Ring2BaseSim):
    """S0 data plane, plus a per-direction pacer on a fair-share reference."""

    CEIL = 1.0                       # one board port per (VC, direction)

    def __init__(self, topo: Ring2Topology,
                 params: Ring2FairParams | None = None, seed: int = 0):
        self.p: Ring2FairParams
        super().__init__(topo, params or Ring2FairParams(), seed=seed)
        p = self.p
        self.rate: dict[Any, float] = {}
        self.credit: dict[Any, float] = defaultdict(lambda: p.fair_burst)
        self.ok_win: dict[Any, int] = defaultdict(int)   # per (node, vc, dir)
        self.own_win: dict[int, int] = defaultdict(int)  # per core, all dirs
        # What each node believes every core achieved last window.
        self.view: dict[int, dict[int, int]] = defaultdict(dict)
        self._bus_pipe: dict[int, dict[int, int]] = defaultdict(dict)
        self.obs: dict[int, dict[int, int]] = defaultdict(
            lambda: defaultdict(int))
        self.bus_posts = 0
        self.st["n_fair_deny"] = 0
        self.st["n_fair_trim"] = 0
        self.st["n_fair_release"] = 0
        self.trace: dict[str, list] = {"t": [], "rate": [], "ok": []}

    # -- helpers ------------------------------------------------------------

    def _paced(self, node: int, vc: str) -> bool:
        return vc in self.p.fair_vcs and is_core(node)

    def _pkey(self, node: int, vc: str, d: int) -> Any:
        return (node, vc, d)

    def _keys(self) -> list[Any]:
        return [(n, vc, d) for n in range(self.n) for vc in self.p.fair_vcs
                for d in (1, -1) if self._paced(n, vc)]

    def _rate(self, k: Any) -> float:
        return self.rate.get(k, self.p.fair_init)

    def _cores(self) -> list[int]:
        return [n for n in range(self.n) if is_core(n)]

    # -- fast loop: the gate ------------------------------------------------

    def _may_inject(self, node: int, plane: PlaneId, f: Flit | None = None
                    ) -> bool:
        if not super()._may_inject(node, plane, f):
            return False
        if f is None or not self._paced(node, f.vc):
            return True
        d = getattr(f, "dir", 1) or 1
        if self.credit[self._pkey(node, f.vc, d)] >= 1.0:
            return True
        self.st["n_fair_deny"] += 1
        self._deny_cause = "fair"
        return False

    def _on_inject(self, f: Flit) -> None:
        super()._on_inject(f)
        if self._paced(f.src, f.vc):
            d = getattr(f, "dir", 1) or 1
            self.credit[self._pkey(f.src, f.vc, d)] -= 1.0
            self.ok_win[self._pkey(f.src, f.vc, d)] += 1
            self.own_win[f.src] += 1

    def _launch(self, f: Flit, *, inring: bool) -> bool:
        """Count crossings per source: the in-band fair-share signal.

        A node inspects every flit it forwards anyway, and the source is already
        in the header, so this costs a counter table and no wires.
        """
        node = f.idx
        ok = super()._launch(f, inring=inring)
        if ok and self.p.fair_signal == "inband" and f.vc in self.p.fair_vcs:
            self.obs[node][f.src] += 1
        return ok

    def _ctrl_deliver(self) -> None:
        p = self.p
        if p.fair_signal == "bus":
            due = self._bus_pipe.pop(self.t, None)
            if due:
                for n in range(self.n):
                    self.view[n] = dict(due)
        for k in self._keys():
            self.credit[k] = min(p.fair_burst, self.credit[k] + self._rate(k))

    def _aimd_tick(self) -> None:
        if (self.t % self.p.fair_window) == self.p.fair_window - 1:
            self._close_window()

    # -- slow loop: the fair-share reference --------------------------------

    def _share_for(self, node: int) -> float | None:
        """Mean achieved count over the cores this node can see."""
        if self.p.fair_signal == "inband":
            seen = {s: c for s, c in self.obs[node].items()
                    if is_core(s) and c > 0}
            # A node always knows its own count exactly.
            seen[node] = self.own_win[node]
            vals = [v for v in seen.values() if v > 0]
        else:
            vals = [v for v in self.view[node].values() if v > 0]
        return (sum(vals) / len(vals)) if vals else None

    def _close_window(self) -> None:
        p = self.p
        rec_r, rec_ok = [], []
        for c in self._cores():
            share = self._share_for(c)
            own = self.own_win[c]
            for vc in p.fair_vcs:
                for d in (1, -1):
                    k = self._pkey(c, vc, d)
                    r = self._rate(k)
                    if share is not None and own > share * (1.0 + p.fair_tol):
                        r -= p.fair_step
                        self.st["n_fair_trim"] += 1
                    else:
                        r += p.fair_step
                        self.st["n_fair_release"] += 1
                    self.rate[k] = max(p.fair_floor, min(self.CEIL, r))
                    rec_r.append(round(self.rate[k], 4))
            rec_ok.append(own)

        if p.fair_signal == "bus":
            post = {c: self.own_win[c] for c in self._cores()}
            self._bus_pipe[self.t + p.fair_bus_lat] = post
            self.bus_posts += len(post)
        self.ok_win.clear()
        self.own_win.clear()
        self.obs.clear()
        self.trace["t"].append(self.t)
        self.trace["rate"].append(rec_r)
        self.trace["ok"].append(rec_ok)

    # -- reporting ----------------------------------------------------------

    def fc_summary(self) -> dict[str, Any]:
        p = self.p
        return {
            "mode": "s23", "window": p.fair_window, "burst": p.fair_burst,
            "tol": p.fair_tol, "step": p.fair_step, "floor": p.fair_floor,
            "signal": p.fair_signal,
            "bus_lat": p.fair_bus_lat if p.fair_signal == "bus" else None,
            "bus_posts": self.bus_posts,
            # 6 bits per post, the same width S1 pays. Zero when in-band.
            "bus_bits": self.bus_posts * 6,
            "paced_vcs": list(p.fair_vcs),
            "n_fair_deny": self.st["n_fair_deny"],
            "n_fair_trim": self.st["n_fair_trim"],
            "n_fair_release": self.st["n_fair_release"],
            "n_windows": len(self.trace["t"]),
        }
