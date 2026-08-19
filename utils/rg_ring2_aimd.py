#!/usr/bin/env python3
"""S1: AIMD source-rate control on top of the S0 dual-plane ring.

Every boarding and leaving failure is counted on the flit. Request-path
counts ride the response (piggyback); response-path counts are already at
the destination when the transaction completes. The source then does
classic AIMD on its injection token bucket:

    no failure in the epoch  ->  rate += alpha
    any  failure in the epoch ->  rate *= beta
    rate clamped to [rate_min, rate_max]

`aimd_scope=core_only` rate-limits only AI cores (request injection).
`aimd_scope=both` also rate-limits HAs (response injection), using each
HA's local board/eject failures over the epoch -- responses have no
further message on which to piggyback a signal back to the HA.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Sequence

from rg_ring2_base import Ring2BaseParams, Ring2BaseSim, run_batch as _run_s0
from rg_ring2_topo import Ring2Topology, Txn, is_core


class Ring2AimdSim(Ring2BaseSim):
    """S0 data plane + per-node token bucket + epoch AIMD."""

    def __init__(self, topo: Ring2Topology, params: Ring2BaseParams | None = None,
                 seed: int = 0):
        p = params or Ring2BaseParams()
        p.aimd = True
        super().__init__(topo, p, seed=seed)
        self.rate: dict[int, float] = {
            n: p.rate_init for n in range(topo.n)}
        self.tokens: dict[int, float] = {
            n: p.rate_init for n in range(topo.n)}
        self.epoch_fail: dict[int, int] = defaultdict(int)
        self.epoch_ok: dict[int, int] = defaultdict(int)
        self.last_epoch = 0

    def _controlled(self, node: int) -> bool:
        if self.p.aimd_scope == "both":
            return True
        return is_core(node)

    def _may_inject(self, node: int, plane: int, f=None) -> bool:
        if not super()._may_inject(node, plane, f):
            return False
        if not self._controlled(node):
            return True
        if self.tokens[node] >= 1.0:
            self.tokens[node] -= 1.0
            return True
        return False

    def _on_board_fail(self, node: int, f) -> None:
        super()._on_board_fail(node, f)
        self.epoch_fail[node] += 1

    def _deflect(self, f) -> None:
        super()._deflect(f)
        # leaving failed at the destination; charge the *source* of this flit
        # locally so HA AIMD can see response-eject pressure, and so a core
        # sees its own request-eject pressure without waiting for piggyback
        self.epoch_fail[f.src] += 1

    def _on_txn_done(self, txn, last) -> None:
        # piggyback: request-path failures plus whatever the response
        # accumulated on the way home, delivered to the requesting core
        fb = last.fail_board
        fe = last.fail_eject
        if fb or fe:
            self.epoch_fail[txn.core] += fb + fe
        else:
            self.epoch_ok[txn.core] += 1

    def _aimd_tick(self) -> None:
        # refill tokens every cycle (rate flits / cycle)
        for n in range(self.n):
            if not self._controlled(n):
                continue
            self.tokens[n] = min(self.p.rate_max * 2.0,
                                 self.tokens[n] + self.rate[n])
        if self.t - self.last_epoch < self.p.epoch:
            return
        self.last_epoch = self.t
        for n in range(self.n):
            if not self._controlled(n):
                continue
            if self.epoch_fail[n] > 0:
                self.rate[n] = max(self.p.rate_min, self.rate[n] * self.p.beta)
                self.st["n_aimd_decrease"] += 1
            elif self.epoch_ok[n] > 0 or self.st["n_injected"] > 0:
                self.rate[n] = min(self.p.rate_max, self.rate[n] + self.p.alpha)
                self.st["n_aimd_increase"] += 1
            self.epoch_fail[n] = 0
            self.epoch_ok[n] = 0

    def summary(self) -> dict[str, Any]:
        out = super().summary()
        out["aimd"] = True
        out["rate_mean"] = round(sum(self.rate.values()) / max(1, self.n), 4)
        out["rate_min_obs"] = round(min(self.rate.values()), 4)
        out["rate_max_obs"] = round(max(self.rate.values()), 4)
        return out


def run_batch(topo: Ring2Topology, txns: Sequence[Txn], *,
              params: Ring2BaseParams | None = None,
              t_max: int = 2_000_000, seed: int = 0) -> dict[str, Any]:
    p = params or Ring2BaseParams()
    p.aimd = True
    sim = Ring2AimdSim(topo, p, seed=seed)
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
    out["recv_by_core"] = sim.recv_by_core()
    return out


if __name__ == "__main__":
    import json
    from rg_ring2_topo import build_allpairs

    topo = Ring2Topology()
    tx = build_allpairs(m=1, m_resp=4)
    r = run_batch(topo, tx)
    keep = ("completed", "makespan", "n_txn_done", "n_deflections",
            "n_board_fail", "n_aimd_increase", "n_aimd_decrease",
            "rate_mean", "rate_min_obs", "lat_p50", "lat_p99")
    print(json.dumps({k: r.get(k) for k in keep}, indent=2))
    # silence unused-import lint if a caller compares against S0
    _ = _run_s0
