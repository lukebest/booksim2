#!/usr/bin/env python3
"""S21: self-clocked deterministic pacing (rate-based, sender-driven).

Why this shape
--------------
The variance decomposition on this ring says the per-bin unfairness is
almost entirely *timing jitter*, not rate inequality: over 50-cycle bins
97-99% of the variance is a core varying against itself over time, and if
the timing were perfectly regular while every core kept its own long-run
rate, Jain would already be 0.9986-0.99997. So the job is not to
redistribute bandwidth -- it is to stop each core from injecting in bursts.

That is exactly what a deterministic pacer does, and it is why S1 cannot get
there: AIMD's token bucket is allowed a burst of `rate_max * 2` and its rate
sawtooths, so a core alternates between bursting and idling inside one
50-cycle bin. Worse, S1 triggers its multiplicative decrease on the node's
*own* board failures, which on a bufferless ring are caused by other nodes'
transit traffic -- the victim throttles itself and hands its slots to the
cores that were already doing fine.

Mechanism
---------
Per core, per paced VC:

    credit += rate            every cycle, capped at `pace_burst`
    board a flit             only when credit >= 1, then credit -= 1

`pace_burst = 1` makes injection a strict interval process: over a bin of
`B` cycles a core boards `B * rate` flits give or take one, which is what
drives Jain to ~1. The rate itself is *self-clocked*: at each window
boundary a core sets its rate from what the ring actually granted it,

    rate <- (1 - g) * rate + g * (ok_this_window / window) * headroom

so the pacer converges on the rate the fabric was already giving it and
therefore does not cost throughput. `headroom` slightly over-provisions the
interval so a core that is offered a slot early can still take it.

`pace_equalise` optionally adds the one piece that needs the network: every
node broadcasts its achieved count for the window on the *same* dedicated
3-bit-per-direction bus S1 uses, and a core running faster than the ring's
slowest active core by more than `pace_tol` trims its rate by one step.
That converts the residual position-driven rate spread into equality, and
costs no extra wires over S1.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Sequence

from rg_ring2_base import Flit, Ring2BaseParams, Ring2BaseSim
from rg_ring2_topo import PlaneId, Ring2Topology, Txn, is_core, is_ha

# Achieved counts are broadcast at S1's precision: 3 bits per direction.
LEVEL_MAX = 7


@dataclass
class Ring2PaceParams(Ring2BaseParams):
    pace_window: int = 64         # measurement / control window, cycles
    pace_burst: float = 1.0       # leaky-bucket depth, flits
    pace_gain: float = 0.25       # EWMA gain on the measured rate
    pace_headroom: float = 1.05   # over-provision the interval slightly
    pace_floor: float = 0.05      # never pace a core to silence
    pace_init: float = 1.0        # start unthrottled, converge downward
    pace_vcs: tuple[str, ...] = ("dat",)   # which VCs are paced
    pace_scope: str = "core_only"          # "core_only" | "ha_only" | "both"
    # Bus-assisted rate equalisation, reusing S1's broadcast.
    pace_equalise: bool = False
    pace_tol: float = 0.08        # relative lead over the slowest core
    pace_trim: float = 0.02       # rate step when trimming / releasing
    pace_bus_lat: int = 30        # same bus delay S1 is charged


class Ring2PaceSim(Ring2BaseSim):
    """S0 data plane plus a per-core deterministic injection pacer."""

    def __init__(self, topo: Ring2Topology,
                 params: Ring2PaceParams | None = None, seed: int = 0):
        self.p: Ring2PaceParams
        super().__init__(topo, params or Ring2PaceParams(), seed=seed)
        p = self.p
        self.rate: dict[Any, float] = {}
        self.credit: dict[Any, float] = defaultdict(lambda: p.pace_burst)
        self.ok_win: dict[Any, int] = defaultdict(int)
        # Broadcast view of every node's achieved count, `pace_bus_lat` old.
        self.bus_view: dict[int, int] = defaultdict(int)
        self._bus_pipe: dict[int, dict[int, int]] = defaultdict(dict)
        self.bus_posts = 0
        self.trace: dict[str, list] = {"t": [], "rate": [], "ok": []}
        self.st["n_pace_deny"] = 0
        self.st["n_pace_trim"] = 0

    # -- helpers ------------------------------------------------------------

    def _paced(self, node: int, vc: str) -> bool:
        if vc not in self.p.pace_vcs:
            return False
        if self.p.pace_scope == "both":
            return True
        if self.p.pace_scope == "ha_only":
            return is_ha(node)
        return is_core(node)

    def _keys(self) -> list[Any]:
        return [(n, vc) for n in range(self.n) for vc in self.p.pace_vcs
                if self._paced(n, vc)]

    def _pkey(self, node: int, vc: str) -> Any:
        return (node, vc)

    def _rate(self, k: Any) -> float:
        return self.rate.get(k, self.p.pace_init)

    # -- the pacer ----------------------------------------------------------

    def _may_inject(self, node: int, plane: PlaneId, f: Flit | None = None
                    ) -> bool:
        if not super()._may_inject(node, plane, f):
            return False
        if f is None or not self._paced(node, f.vc):
            return True
        if self.credit[self._pkey(node, f.vc)] >= 1.0:
            return True
        self.st["n_pace_deny"] += 1
        self._deny_cause = "pace"
        return False

    def _on_inject(self, f: Flit) -> None:
        super()._on_inject(f)
        if self._paced(f.src, f.vc):
            k = self._pkey(f.src, f.vc)
            self.credit[k] -= 1.0
            self.ok_win[k] += 1

    def _ctrl_deliver(self) -> None:
        """Refill credit, then close the window if this cycle ends one.

        Refilling every cycle and capping at `pace_burst` is the leaky
        bucket: a core that is refused for a while cannot bank the missed
        slots and burst later, which is precisely what would ruin the
        per-bin index.
        """
        p = self.p
        if p.pace_equalise:
            self._bus_deliver()
        for k in self._keys():
            self.credit[k] = min(p.pace_burst,
                                 self.credit[k] + self._rate(k))

    def _aimd_tick(self) -> None:
        if (self.t % self.p.pace_window) == self.p.pace_window - 1:
            self._close_window()

    def _bus_deliver(self) -> None:
        due = self._bus_pipe.pop(self.t, None)
        if due:
            self.bus_view.update(due)

    def _close_window(self) -> None:
        p = self.p
        rec_r, rec_ok = [], []
        # Slowest active core on the bus sets the equalisation reference.
        ref = None
        if p.pace_equalise:
            active = [v for v in self.bus_view.values() if v > 0]
            ref = min(active) if active else None
        for k in self._keys():
            got = self.ok_win[k]
            meas = got / p.pace_window
            r = ((1.0 - p.pace_gain) * self._rate(k)
                 + p.pace_gain * meas * p.pace_headroom)
            if ref is not None and got > 0:
                # Running ahead of the slowest core by more than the
                # tolerance: give the lead back a step at a time.
                if got > ref * (1.0 + p.pace_tol):
                    r -= p.pace_trim
                    self.st["n_pace_trim"] += 1
                elif got < ref * (1.0 + p.pace_tol / 2):
                    r += p.pace_trim
            self.rate[k] = max(p.pace_floor, min(1.0, r))
            rec_r.append(round(self.rate[k], 4))
            rec_ok.append(got)
        if p.pace_equalise:
            for n in range(self.n):
                tot = sum(self.ok_win[self._pkey(n, vc)]
                          for vc in p.pace_vcs)
                self._bus_pipe[self.t + p.pace_bus_lat][n] = tot
                self.bus_posts += 1
        self.ok_win.clear()
        self.trace["t"].append(self.t)
        self.trace["rate"].append(rec_r)
        self.trace["ok"].append(rec_ok)

    # -- reporting ----------------------------------------------------------

    def fc_summary(self) -> dict[str, Any]:
        p = self.p
        cs = [n for n in range(self.n)
              if any(self._paced(n, vc) for vc in p.pace_vcs)]
        n_win = max(1, len(self.trace["t"]))
        return {
            "mode": "s21", "window": p.pace_window,
            "burst": p.pace_burst, "gain": p.pace_gain,
            "headroom": p.pace_headroom, "equalise": p.pace_equalise,
            "tol": p.pace_tol, "trim": p.pace_trim,
            "scope": p.pace_scope, "paced_vcs": list(p.pace_vcs),
            "bus_lat": p.pace_bus_lat if p.pace_equalise else None,
            "bus_posts": self.bus_posts,
            # 3 bits per node per window, the same width S1 pays.
            "bus_bits": self.bus_posts * 3,
            "n_pace_deny": self.st["n_pace_deny"],
            "n_pace_trim": self.st["n_pace_trim"],
            "final_rate": {
                str(n): round(self._rate(self._pkey(n, p.pace_vcs[0])), 4)
                for n in cs},
            "mean_rate": {
                str(k[0]): round(
                    sum(w[i] for w in self.trace["rate"]) / n_win, 4)
                for i, k in enumerate(self._keys())},
        }

    def summary(self) -> dict[str, Any]:
        out = super().summary()
        out["pace"] = True
        return out


def run_batch(topo: Ring2Topology, txns: Sequence[Txn], *,
              params: Ring2PaceParams | None = None,
              t_max: int = 2_000_000, seed: int = 0) -> dict[str, Any]:
    sim = Ring2PaceSim(topo, params or Ring2PaceParams(), seed=seed)
    sim.offer_batch(txns)
    last_progress, last_count = 0, 0
    while sim.t < t_max and not sim.done():
        sim.step()
        if sim.st["n_delivered_flits"] != last_count:
            last_count = sim.st["n_delivered_flits"]
            last_progress = sim.t
        elif sim.t - last_progress > 40_000:
            break
    out = sim.summary()
    out["stall_detected"] = not out["completed"]
    out["fc"] = sim.fc_summary()
    return out
