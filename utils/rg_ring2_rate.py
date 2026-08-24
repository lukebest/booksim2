#!/usr/bin/env python3
"""S17 / S18: rate-based congestion control over the bufferless ring.

S15 and S16 both act on *who may use a slot*. The datacentre-transport
literature attacks the same problem from the other end: leave arbitration
alone and pace the source, so the congestion never forms. Two schemes define
that family, and both map onto stock CHI without a new packet type.

S17 TIMELY (`Ring2TimelySim`)
----------------------------
Delay-based. The signal is the round-trip time, and CHI already measures one
for free: `WriteNoSnp` cannot send data until `DBIDResp` comes back, so
REQ-boarded to DBIDResp-drained *is* an RTT sample taken on packets the
protocol sends anyway. TIMELY's insight is that the RTT *gradient* leads the
absolute delay, so it reacts before a queue has built. The update is the
paper's, with the thresholds expressed as multiples of the measured minimum
RTT because this fabric's numbers are cycles, not microseconds.

Crucially the sample spans any RetryAck round trips, so a bounced request
shows up as a very large RTT and the controller backs off from it.

S18 DCQCN (`Ring2DcqcnSim`)
--------------------------
ECN-based. There is nothing to mark on a bufferless ring -- no queue exists
whose occupancy could cross a threshold. But the congestion that produces
retries is not on the ring at all, it is the *completer's request tracker*,
and that does have an occupancy. So the mark is computed there, RED-style,
and a RetryAck is a mark with probability one because it is the completer
saying outright that it is full.

That makes S18 cheaper than real DCQCN in two ways: the mark needs one bit
on a `DBIDResp` the protocol already sends, and no CNP packet is needed at
all, because the marked response is itself travelling to the source.

Both schemes actuate the same way: a leaky bucket in front of the REQ
boarding queue. Pacing REQs is the right lever because it is REQ arrivals
that overrun the tracker, and because WriteData cannot move ahead of its
grant anyway -- slowing requests slows data by construction. The outstanding
cap is left alone, so what the sweep measures is how much *effective*
outstanding a rate controller recovers out of the same nominal budget.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from rg_ring2_base import Flit, Ring2BaseParams, Ring2BaseSim
from rg_ring2_topo import PlaneId, Ring2Topology, Txn, is_core


@dataclass
class RateKnobs:
    """Actuator shared by every rate-based scheme.

    The `pace_` prefix is not decoration: `Ring2BaseParams` already owns
    `rate_min` / `rate_max` / `rate_init` / `beta` for S1's per-window AIMD
    budget, and a dataclass collects fields base-first, so reusing those names
    would let S1's defaults silently override these.
    """

    # Tokens are REQ boardings per cycle. A core owns one board port per
    # plane, so two per cycle is its physical ceiling on this fabric and
    # `pace_max` sitting there means the controller can only ever subtract.
    pace_max: float = 2.0
    pace_init: float = 2.0
    # The floor cannot be arbitrarily low. Feedback arrives *on* the traffic,
    # so a controller that cuts to near zero stops receiving the samples that
    # would tell it to come back up, and starves itself. One request per
    # sixteen cycles per core still keeps the loop fed.
    pace_min: float = 1.0 / 16
    # Leaky-bucket depth, in tokens. A little burst tolerance keeps a core
    # from losing a slot it was entitled to just because the free cycle came
    # slightly early; a large one defeats the pacing.
    pace_burst: float = 4.0
    trace: bool = True
    trace_every: int = 256


@dataclass
class TimelyKnobs:
    """TIMELY, with the thresholds moved off the published values.

    The paper's `T_low = 1.5*minRTT`, `T_high = 4*minRTT` assume the only
    reason RTT exceeds minRTT is a queue that should not be there. Here the
    dominant term is the completer's own service queue, which *should* be
    there: an idle completer is the thing to avoid. Unloaded RTT is ~20
    cycles while the efficient operating point sits near 150, so thresholds
    at 4*minRTT declare permanent congestion and the controller starves the
    fabric. They are raised to bracket the useful queue instead.

    `delta` is likewise scaled to the sample rate, not to the paper's: a run
    here yields tens of RTT samples per core, so an increase of 1/256 per
    sample could never traverse the range once.
    """

    t_low_mult: float = 8.0       # below this multiple of minRTT, grow freely
    t_high_mult: float = 24.0     # above it, cut in proportion to the excess
    ewma: float = 0.875           # gradient smoothing, TIMELY's alpha
    timely_beta: float = 0.8      # multiplicative-decrease strength
    delta: float = 1.0 / 16       # additive increase, tokens/cycle
    hai_n: int = 5                # consecutive negative gradients before HAI


@dataclass
class DcqcnKnobs:
    """DCQCN, with the RED curve and the timers moved to fabric scale.

    Same correction as TIMELY, for the same reason. A tracker at 40% is not
    congested, it is working, so marking has to start much later and much
    more gently than the published `k_min = 0.4, p_max = 0.5`. And QCN's
    timers are microseconds in a datacentre; at 300 cycles between increase
    events this controller spends whole round trips below the rate it already
    knows is safe.
    """

    # RED on completer tracker occupancy, as a fraction of the tracker.
    k_min: float = 0.8
    k_max: float = 1.0
    p_max: float = 0.05
    g: float = 1.0 / 16           # alpha EWMA weight
    alpha_timer: int = 8          # cycles between alpha decays
    rate_timer: int = 24          # cycles between increase events
    fast_recovery: int = 5        # F: iterations of binary search back up
    r_ai: float = 1.0 / 16
    r_hai: float = 1.0 / 4


@dataclass
class Ring2RateParams(Ring2BaseParams, RateKnobs, TimelyKnobs, DcqcnKnobs):
    """One params object; each scheme reads only the knobs it owns."""


class RateMixin:
    """Leaky-bucket pacing of REQ boardings, per requester."""

    mode = "rate"

    def _rate_init(self) -> None:
        p = self.p                             # type: ignore[attr-defined]
        self.rate: dict[int, float] = defaultdict(lambda: p.pace_init)
        self.tokens: dict[int, float] = defaultdict(lambda: p.pace_burst)
        self.st["n_rate_deny"] = 0             # type: ignore[attr-defined]
        self.st["n_rate_down"] = 0             # type: ignore[attr-defined]
        self.st["n_rate_up"] = 0               # type: ignore[attr-defined]
        self.rtt_min: dict[int, float] = {}
        self.rtt_last: dict[int, float] = {}
        self.n_sample: dict[int, int] = defaultdict(int)
        self.rate_area: dict[int, float] = defaultdict(float)
        self.rtt_area: dict[int, float] = defaultdict(float)
        self.trace: dict[str, list] = {"t": [], "rate": [], "rtt": []}

    # -- actuator -----------------------------------------------------------

    def _clamp(self, r: float) -> float:
        p = self.p                             # type: ignore[attr-defined]
        return max(p.pace_min, min(p.pace_max, r))

    def _may_inject(self, node: int, plane: PlaneId, f: Flit | None = None
                    ) -> bool:
        if not super()._may_inject(node, plane, f):
            return False
        # Only fresh requests are paced. A re-send is already-issued work
        # holding a credit, and delaying it would only lengthen the stall it
        # is there to end.
        if f is None or f.kind != "req" or f.retry or not is_core(f.src):
            return True
        if self.tokens[f.src] < 1.0:
            self.st["n_rate_deny"] += 1
            self._deny_cause = "rate"
            return False
        return True

    def _on_inject(self, f: Flit) -> None:
        super()._on_inject(f)
        if f.kind == "req" and not f.retry and is_core(f.src):
            self.tokens[f.src] -= 1.0

    def _ctrl_issue(self) -> None:
        """Refill the buckets and let the controller run its timers."""
        super()._ctrl_issue()
        p = self.p                             # type: ignore[attr-defined]
        for c in self.topo.cores:
            r = self.rate[c]
            self.tokens[c] = min(p.pace_burst, self.tokens[c] + r)
            self.rate_area[c] += r
        self._rate_tick()
        if p.trace and (self.t % p.trace_every) == 0:
            cs = self.topo.cores
            self.trace["t"].append(self.t)
            self.trace["rate"].append([round(self.rate[c], 5) for c in cs])
            self.trace["rtt"].append(
                [round(self.rtt_last.get(c, 0.0), 1) for c in cs])

    def _rate_tick(self) -> None:
        """Per-cycle controller timers. Delay-based schemes need none."""
        return

    # -- signal -------------------------------------------------------------

    def _note_rtt(self, core: int, rtt: int) -> None:
        self.n_sample[core] += 1
        self.rtt_area[core] += rtt
        self.rtt_last[core] = float(rtt)
        prev = self.rtt_min.get(core)
        if prev is None or rtt < prev:
            self.rtt_min[core] = float(rtt)

    def _on_dbid_at_core(self, txn: Txn) -> None:
        t0 = self.req_inject_t.get(txn.txn_id)
        if t0 is not None:
            self._note_rtt(txn.core, self.t - t0)
        self._on_rtt_sample(txn)

    def _on_rtt_sample(self, txn: Txn) -> None:
        return

    # -- reporting ----------------------------------------------------------

    def fc_summary(self) -> dict[str, Any]:
        cs = self.topo.cores
        span = max(1, self.t)
        out: dict[str, Any] = {
            "mode": self.mode,
            "bus_posts": 0, "bus_bits": 0,
            "actuator": "req_leaky_bucket",
            "n_rate_deny": self.st.get("n_rate_deny", 0),
            "n_rate_down": self.st.get("n_rate_down", 0),
            "n_rate_up": self.st.get("n_rate_up", 0),
            "rate_final": {str(c): round(self.rate[c], 5) for c in cs},
            "rate_mean": {str(c): round(self.rate_area[c] / span, 5)
                          for c in cs},
            "rtt_min": {str(c): self.rtt_min.get(c, 0.0) for c in cs},
            "rtt_mean": {str(c): round(self.rtt_area[c]
                                       / max(1, self.n_sample[c]), 1)
                         for c in cs},
        }
        rm = [self.rate_area[c] / span for c in cs]
        out["rate_mean_all"] = round(sum(rm) / max(1, len(rm)), 5)
        if self.p.trace and self.trace["t"]:
            out["trace"] = dict(self.trace)
            out["trace"]["nodes"] = list(cs)
        return out


# ---------------------------------------------------------------------------
# S17 TIMELY
# ---------------------------------------------------------------------------

class TimelyMixin(RateMixin):
    """RTT-gradient rate control, as specified in the TIMELY paper."""

    mode = "s17"

    def _timely_init(self) -> None:
        self._rate_init()
        self.rtt_diff: dict[int, float] = defaultdict(float)
        self.rtt_prev: dict[int, float] = {}
        self.hai: dict[int, int] = defaultdict(int)

    def _on_rtt_sample(self, txn: Txn) -> None:
        p = self.p
        c = txn.core
        rtt = self.rtt_last[c]
        mn = self.rtt_min.get(c) or rtt or 1.0
        diff = self.rtt_diff
        diff[c] = ((1.0 - p.ewma) * diff[c]
                   + p.ewma * (rtt - self.rtt_prev.get(c, rtt)))
        self.rtt_prev[c] = rtt
        ngrad = diff[c] / mn
        t_low, t_high = p.t_low_mult * mn, p.t_high_mult * mn
        r = self.rate[c]
        if rtt < t_low:
            r += p.delta
            self.hai[c] = 0
            self.st["n_rate_up"] += 1
        elif rtt > t_high:
            r *= 1.0 - p.timely_beta * (1.0 - t_high / rtt)
            self.hai[c] = 0
            self.st["n_rate_down"] += 1
        elif ngrad <= 0.0:
            self.hai[c] += 1
            n = p.hai_n if self.hai[c] >= p.hai_n else 1
            r += n * p.delta
            self.st["n_rate_up"] += 1
        else:
            r *= 1.0 - p.timely_beta * min(1.0, ngrad)
            self.hai[c] = 0
            self.st["n_rate_down"] += 1
        self.rate[c] = self._clamp(r)


class Ring2TimelySim(TimelyMixin, Ring2BaseSim):
    """Baseline ring datapath; each core paces its REQs off the RTT gradient."""

    def __init__(self, topo: Ring2Topology,
                 params: Ring2RateParams | None = None, *,
                 seed: int = 0) -> None:
        super().__init__(topo, params or Ring2RateParams(), seed=seed)
        self._timely_init()


# ---------------------------------------------------------------------------
# S18 DCQCN
# ---------------------------------------------------------------------------

class DcqcnMixin(RateMixin):
    """QCN rate control driven by marks computed at the completer."""

    mode = "s18"

    def _dcqcn_init(self) -> None:
        self._rate_init()
        self.alpha: dict[int, float] = defaultdict(lambda: 1.0)
        self.rate_target: dict[int, float] = defaultdict(
            lambda: self.p.pace_init)
        self.t_alpha: dict[int, int] = defaultdict(int)
        self.t_rate: dict[int, int] = defaultdict(int)
        self.inc_stage: dict[int, int] = defaultdict(int)
        self.marked: dict[int, bool] = defaultdict(bool)
        self.st["n_mark"] = 0

    # -- marking, at the point the congestion actually is ------------------

    def _mark_prob(self, used: int) -> float:
        """RED on the completer's request tracker."""
        cap = self.p.ha_track
        if cap <= 0:
            return 0.0
        lo, hi = self.p.k_min * cap, self.p.k_max * cap
        if used <= lo:
            return 0.0
        if used >= hi:
            return self.p.p_max
        return self.p.p_max * (used - lo) / max(1e-9, hi - lo)

    def _on_dbid_at_core(self, txn: Txn) -> None:
        super()._on_dbid_at_core(txn)
        used = self.acc_used.get(txn.txn_id, 0)
        if self.rng.random() < self._mark_prob(used):
            self._on_mark(txn.core)

    def _on_retry_at_core(self, txn: Txn) -> None:
        """A RetryAck is the completer stating it is full: mark with p = 1."""
        super()._on_retry_at_core(txn)
        self._on_mark(txn.core)

    # -- QCN rate machine --------------------------------------------------

    def _on_mark(self, core: int) -> None:
        self.st["n_mark"] += 1
        if self.marked[core]:
            return          # at most one cut per feedback interval
        self.marked[core] = True
        self.alpha[core] = (1.0 - self.p.g) * self.alpha[core] + self.p.g
        self.rate_target[core] = self.rate[core]
        self.rate[core] = self._clamp(
            self.rate[core] * (1.0 - self.alpha[core] / 2.0))
        self.inc_stage[core] = 0
        self.t_rate[core] = self.t
        self.st["n_rate_down"] += 1

    def _rate_tick(self) -> None:
        p = self.p
        for c in self.topo.cores:
            if self.t - self.t_alpha[c] >= p.alpha_timer:
                self.t_alpha[c] = self.t
                if not self.marked[c]:
                    self.alpha[c] *= 1.0 - p.g
                self.marked[c] = False
            if self.t - self.t_rate[c] < p.rate_timer:
                continue
            self.t_rate[c] = self.t
            stage = self.inc_stage[c] + 1
            self.inc_stage[c] = stage
            if stage > p.fast_recovery:
                # Additive, then hyper-increase once additive keeps winning.
                step = p.r_ai if stage <= 2 * p.fast_recovery else \
                    p.r_hai * (stage - 2 * p.fast_recovery)
                self.rate_target[c] = self._clamp(
                    self.rate_target[c] + step)
            self.rate[c] = self._clamp(
                (self.rate_target[c] + self.rate[c]) / 2.0)
            self.st["n_rate_up"] += 1

    def fc_summary(self) -> dict[str, Any]:
        out = super().fc_summary()
        out["n_mark"] = self.st.get("n_mark", 0)
        out["alpha_final"] = {str(c): round(self.alpha[c], 5)
                              for c in self.topo.cores}
        out["k_min"] = self.p.k_min
        out["k_max"] = self.p.k_max
        out["p_max"] = self.p.p_max
        return out


class Ring2DcqcnSim(DcqcnMixin, Ring2BaseSim):
    """Baseline ring datapath; marks come off the completer's tracker."""

    def __init__(self, topo: Ring2Topology,
                 params: Ring2RateParams | None = None, *,
                 seed: int = 0) -> None:
        super().__init__(topo, params or Ring2RateParams(), seed=seed)
        self._dcqcn_init()
