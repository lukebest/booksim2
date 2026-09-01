#!/usr/bin/env python3
"""S28: explicit *rate* feedback from the bottleneck -- the XCP / RCP family.

S1, S18 and S20 all carry explicit feedback, but what they carry is a
*complaint*: "I am at congestion level 5", "your packet crossed a marked
queue". The source then has to guess how much to slow down, and it guesses
with AIMD. The other half of the explicit-feedback family does the opposite:
the network computes the rate the source may use and sends *that number*.
XCP and RCP are the canonical designs, and RCP's core equation is exactly a
per-bottleneck fair share -- link capacity divided by the number of flows
crossing it.

That difference matters here specifically, and it is the reason this family
had to be built rather than assumed to behave like S1. The deck's finding
about S1 is that its trigger has the wrong sign: on a bufferless ring a
node's own board failures are mostly caused by *other* nodes' transit
traffic, so multiplicative decrease punishes the victim and hands its slots
to the cores that were already ahead. An explicit fair share has no such
trigger. The number is computed at the hop that is actually congested, from
the number of contenders there, and it is the *same number for every
contender* -- so it cannot tell the victim and the beneficiary apart in the
wrong direction, because it does not tell them apart at all.

Mechanism
---------
  1. **Detect.** Each node owns two outgoing directed hops. Every
     `rcp_window` cycles, for each of them, it counts the distinct source
     cores whose DAT flits crossed it. `N` is that count. This is a
     `log2(10)`-bit population count over a 10-entry presence bitmap, not a
     rate estimator.
  2. **Compute.** The hop runs RCP's own update on its published share,

         share <- share * [ 1 + alpha * (C_eff - y) / (C_eff * N) ]

     with `C_eff = rcp_target`, `y` the hop's measured occupancy this window
     and the queue term dropped because a bufferless hop has no queue to
     drain. The `1/N` makes the fixed point an equal share; the `(C_eff - y)`
     makes it an equal share of the *whole* link, which matters here: a core
     held down by some other hop leaves capacity behind, and only the
     feedback term hands that capacity to the cores that can still use it.
     The static `rcp_target / N` form is kept as `rcp_mode="static"` because
     it is what the difference is measured against -- it leaves the ring at
     ~64% of its own binding hop. The result is quantised to `RCP_BITS` and
     driven onto the dedicated broadcast bus.
  3. **Propagate / feed back.** Same bus and same fixed `rcp_bus_lat = 30`
     cycles the other bus schemes are charged. Every node reads the whole
     table; a core keeps the entries for the hops its own writes actually
     cross, which it learns at run time by counting -- there is no
     destination prior and no `lambda*` compiled into the hardware.
  4. **Act.** A core is entitled to `share_h` on hop `h`, but only a fraction
     `frac_h` of its own writes cross `h`, and that fraction is purely local
     knowledge. So its allowed total DAT rate is
     `min over h of (share_h / frac_h)`, and its REQ rate is that divided by
     the 2 WriteData flits per transaction. The actuator is a leaky bucket in
     front of the REQ boarding queue -- the same actuator S17 / S18 use, so
     the only thing that differs from those schemes is the signal.

Honest limits, stated up front
------------------------------
`C / N` is RCP's single-bottleneck approximation of max-min, not max-min
itself: a flow already held down by a different hop still counts as a full
contender here, so a shared hop under-issues. Fixing that needs iteration,
which needs several bus round trips per window.

And the bus is wider than S1's. S1 posts one 3-bit level per node per
direction; this posts one `RCP_BITS`-bit share per *hop*, 40 of them. The
number is reported so the cost lands where it belongs -- explicit-rate
feedback is known to be the expensive end of this family, and pretending
otherwise would make the comparison meaningless.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Sequence

from rg_ring2_base import Flit, Ring2BaseParams, Ring2BaseSim
from rg_ring2_topo import Dir, PlaneId, Ring2Topology, Txn, is_core

# Width of one explicit-share word on the bus. 6 bits is S22's width, so the
# per-word cost is comparable; what differs is how many words there are.
RCP_BITS = 6
RCP_MAX = (1 << RCP_BITS) - 1


@dataclass
class Ring2RcpParams(Ring2BaseParams):
    rcp_window: int = 64          # control window, cycles
    rcp_bus_lat: int = 30         # the same bus delay S1 / S22 are charged
    # Target occupancy the share is computed against. 1.0 hands out the whole
    # link and leaves nothing for the transit bursts the ring cannot buffer;
    # below ~0.9 the controller gives away throughput it did not have to.
    rcp_target: float = 0.98
    # "rcp": the multiplicative update above, which drives the hop to
    # `rcp_target`. "static": share = rcp_target / N every window, i.e. the
    # equation with the residual-capacity term removed.
    rcp_mode: str = "rcp"
    # RCP's alpha: how much of the residual capacity is handed out per window.
    rcp_alpha: float = 0.5
    rcp_vcs: tuple[str, ...] = ("dat",)
    # Leaky bucket on fresh REQ boardings, in REQ/cycle. A core owns one REQ
    # board port per direction, so 2.0 is its physical ceiling and the
    # controller can only ever subtract from it.
    rcp_pace_max: float = 2.0
    rcp_pace_min: float = 1.0 / 16
    rcp_pace_burst: float = 1.0
    # EWMA on the rate the core adopts. RCP damps its own feedback loop this
    # way; without it a one-window blip in N steps the rate by a factor of 2.
    rcp_g: float = 0.5
    rcp_trace_every: int = 512


class Ring2RcpSim(Ring2BaseSim):
    """S0 data plane; the bottleneck hop publishes an explicit fair share."""

    def __init__(self, topo: Ring2Topology,
                 params: Ring2RcpParams | None = None, seed: int = 0):
        self.p: Ring2RcpParams
        super().__init__(topo, params or Ring2RcpParams(), seed=seed)
        # (dir, idx) -> set of source cores seen crossing it this window
        self.seen: dict[Any, set[int]] = defaultdict(set)
        # (dir, idx) -> cycles the hop carried a tracked flit this window
        self.busy: dict[Any, int] = defaultdict(int)
        # The hop's own working copy of the share it publishes. Held at the
        # hop, not at the source: this is the state RCP keeps in the router.
        self.hop_share: dict[Any, float] = {}
        # published share per hop, as read off the bus
        self.share: dict[Any, float] = {}
        self._pipe: dict[int, dict[Any, float]] = defaultdict(dict)
        # core -> hop -> own DAT flits crossing it this window (local counter)
        self.own: dict[int, dict[Any, int]] = defaultdict(
            lambda: defaultdict(int))
        self.own_tot: dict[int, int] = defaultdict(int)
        self.rate: dict[int, float] = defaultdict(lambda: self.p.rcp_pace_max)
        self.tokens: dict[int, float] = defaultdict(
            lambda: self.p.rcp_pace_burst)
        self.rate_area: dict[int, float] = defaultdict(float)
        self.bus_posts = 0
        self.st["n_rcp_deny"] = 0
        self._w_dat = 2
        self.trace: dict[str, list] = {"t": [], "rate": [], "n_flow_max": []}

    # -- detect: who crosses each hop --------------------------------------

    def _launch(self, f: Flit, *, inring: bool) -> bool:
        hop = (f.dir, f.idx)
        vc_ok = f.vc in self.p.rcp_vcs
        ok = super()._launch(f, inring=inring)
        if ok and vc_ok:
            self.busy[hop] += 1
            if is_core(f.src):
                self.seen[hop].add(f.src)
        return ok

    def _on_inject(self, f: Flit) -> None:
        super()._on_inject(f)
        if f.kind == "req" and not f.retry and is_core(f.src):
            self.tokens[f.src] -= 1.0
            return
        if f.vc not in self.p.rcp_vcs or not is_core(f.src):
            return
        # The core's own share of each hop on the route it just took. Local
        # knowledge: it computed that route itself.
        txn = self.txn_by_id.get(f.txn_id)
        if txn is not None:
            self._w_dat = txn.m_wdata
        # `_on_inject` runs before `_launch`, so `f.idx` is still the source
        # and `f.target` is the number of hops the flit has yet to cross.
        own = self.own[f.src]
        for k in range(f.target):
            own[(f.dir, (f.idx + f.dir * k) % self.n)] += 1
        self.own_tot[f.src] += 1

    # -- compute + propagate ------------------------------------------------

    def _ctrl_deliver(self) -> None:
        due = self._pipe.pop(self.t, None)
        if due:
            self.share.update(due)
            self._reprice()

    def _ctrl_issue(self) -> None:
        super()._ctrl_issue()
        p = self.p
        for c in self.topo.cores:
            self.tokens[c] = min(p.rcp_pace_burst,
                                 self.tokens[c] + self.rate[c])
            self.rate_area[c] += self.rate[c]
        if (self.t % p.rcp_window) != p.rcp_window - 1:
            return
        post: dict[Any, float] = {}
        c_eff = p.rcp_target
        for d in (1, -1):
            for idx in range(self.n):
                hop = (d, idx)
                n = max(1, len(self.seen.get(hop, ())))
                y = self.busy.get(hop, 0) / p.rcp_window
                if p.rcp_mode == "static":
                    s = c_eff / n
                else:
                    s = self.hop_share.get(hop, c_eff / n)
                    s *= 1.0 + p.rcp_alpha * (c_eff - y) / (c_eff * n)
                s = max(1.0 / RCP_MAX, min(c_eff, s))
                self.hop_share[hop] = s
                post[hop] = min(RCP_MAX, max(1, round(s * RCP_MAX))) / RCP_MAX
                self.bus_posts += 1
        self._pipe[self.t + p.rcp_bus_lat].update(post)
        n_flow_max = max((len(s) for s in self.seen.values()), default=0)
        self.seen.clear()
        self.busy.clear()
        if not (self.t % p.rcp_trace_every):
            cs = self.topo.cores
            self.trace["t"].append(self.t)
            self.trace["rate"].append([round(self.rate[c], 5) for c in cs])
            self.trace["n_flow_max"].append(n_flow_max)

    def _reprice(self) -> None:
        """Turn the published shares into each core's own REQ rate."""
        p = self.p
        for c in self.topo.cores:
            tot = self.own_tot[c]
            own = self.own[c]
            if tot <= 0 or not own:
                continue
            allowed = None
            for hop, cnt in own.items():
                s = self.share.get(hop)
                if s is None or cnt <= 0:
                    continue
                # share_h / frac_h, with frac_h = cnt / tot
                r = s * tot / cnt
                if allowed is None or r < allowed:
                    allowed = r
            if allowed is None:
                continue
            target = max(p.rcp_pace_min,
                         min(p.rcp_pace_max, allowed / max(1, self._w_dat)))
            self.rate[c] += p.rcp_g * (target - self.rate[c])
            own.clear()
            self.own_tot[c] = 0

    # -- act: leaky bucket on fresh REQ ------------------------------------

    def _may_inject(self, node: int, plane: PlaneId, f: Flit | None = None
                    ) -> bool:
        if not super()._may_inject(node, plane, f):
            return False
        if f is None or f.kind != "req" or f.retry or not is_core(f.src):
            return True
        if self.tokens[f.src] < 1.0:
            self.st["n_rcp_deny"] += 1
            self._deny_cause = "rcp_rate"
            return False
        return True

    # -- reporting ----------------------------------------------------------

    def fc_summary(self) -> dict[str, Any]:
        cs = self.topo.cores
        span = max(1, self.t)
        shares = sorted(self.share.items(), key=lambda kv: kv[1])[:8]
        return {
            "mode": "s28",
            "actuator": "req_leaky_bucket",
            "signal": "explicit_rate",
            "window": self.p.rcp_window, "bus_lat": self.p.rcp_bus_lat,
            "target": self.p.rcp_target, "g": self.p.rcp_g,
            "rcp_mode": self.p.rcp_mode, "alpha": self.p.rcp_alpha,
            "pace_burst": self.p.rcp_pace_burst,
            "bus_posts": self.bus_posts,
            "bus_bits": self.bus_posts * RCP_BITS,
            "word_bits": RCP_BITS, "n_words_per_window": 2 * self.n,
            "n_rcp_deny": self.st["n_rcp_deny"],
            "rate_final": {str(c): round(self.rate[c], 5) for c in cs},
            "rate_mean": {str(c): round(self.rate_area[c] / span, 5)
                          for c in cs},
            "rate_mean_all": round(
                sum(self.rate_area[c] for c in cs) / len(cs) / span, 5),
            "tightest_hops": [[f"{d}:{i}", round(s, 4)] for (d, i), s in shares],
            "trace": dict(self.trace) | {"nodes": list(cs)},
        }

    def summary(self) -> dict[str, Any]:
        out = super().summary()
        out["rcp"] = True
        return out


# These names are part of the hook signatures this module overrides.
_ = (Dir, Txn, Sequence)
