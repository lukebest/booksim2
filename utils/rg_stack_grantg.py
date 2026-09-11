#!/usr/bin/env python3
"""S16 at group granularity: the completer schedules top dies, not cores.

Why the granularity has to change
---------------------------------
`GrantMixin` equalises cumulative granted flits *per requester*. On a single
ring the requester and the schedulable unit are the same thing, so that is the
right axis. On the stacked fabric they are not. Ten AI cores share one top-die
ring, one group of eight attach points and one set of eight D2D bridges, so a
core is not a unit anybody can schedule: if a top die is short of bandwidth,
the whole die is, and no ordering inside it recovers the shortfall. Meanwhile
the study scores per top die, and an HA equalising 60 cores does not equalise
6 groups -- with all dies offering the same number of requests, per-core
least-served is satisfied by any per-group split at all.

So the completer keeps exactly the same protocol behaviour and only changes
what it counts:

  * `served` is indexed by top die, not by core, so the HA hands its next
    grant to the group it has served least.
  * Inside the chosen group a plain round robin picks the core, which keeps
    the intra-die fairness the per-core policy used to provide.
  * `group_quota` optionally reserves each group a floor of
    `overcommit // n_groups` concurrent grants, so a group that arrives late
    cannot find the whole overcommit window already committed to others.

Cost moves the same way. The per-HA service table shrinks from one counter
per core to one per group -- 96 HAs x 6 entries instead of 96 x 60 -- and the
arbiter picks a minimum over 6 rather than 60. Nothing else about S16
changes: no bus, no broadcast, no slot reservation, and the control signal is
still a packet the protocol already sends.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from rg_ring2_grant import GrantKnobs, GrantMixin
from rg_stack_base import StackBaseParams
from rg_stack_fc import StackGrantSim
from rg_stack_topo import StackTopology, Txn


@dataclass
class GroupGrantKnobs(GrantKnobs):
    # Reserve every group a floor of concurrent grants at each HA, so a late
    # arrival is not locked out by an overcommit window already spent.
    group_quota: bool = False


@dataclass
class StackGroupGrantParams(StackBaseParams, GroupGrantKnobs):
    """Baseline stacked fabric plus the group-granular receiver knobs."""


class GroupGrantMixin(GrantMixin):
    """Receiver-driven admission arbitrated over top-die groups."""

    def _grant_group_init(self, group_of: list[int], n_groups: int) -> None:
        self._group_of = group_of
        self._n_groups = n_groups
        # completer -> group -> DAT flits granted so far
        self.served_group: dict[int, dict[int, int]] = defaultdict(
            lambda: defaultdict(int))
        # completer -> group -> grants currently outstanding
        self.outst_group: dict[int, dict[int, int]] = defaultdict(
            lambda: defaultdict(int))
        # completer -> group -> requesters waiting, round robin within a group
        self.grr: dict[Any, int] = defaultdict(int)
        self.quota = max(1, self.gp.overcommit // max(1, n_groups))

    # -- accounting ---------------------------------------------------------

    def _grant(self, txn: Txn) -> None:
        g = self._group_of[txn.core]
        self.outst_group[txn.ha][g] += 1
        self.served_group[txn.ha][g] += (txn.m_wdata
                                         if getattr(txn, "op", "write") == "write"
                                         else txn.m_resp)
        super()._grant(txn)

    def _retire_group(self, txn: Txn) -> None:
        g = self._group_of[txn.core]
        cur = self.outst_group[txn.ha][g]
        self.outst_group[txn.ha][g] = max(0, cur - 1)

    def _on_write_data_complete(self, txn: Txn) -> None:
        self._retire_group(txn)
        super()._on_write_data_complete(txn)

    def _on_txn_done(self, txn: Txn, last) -> None:
        if getattr(txn, "op", "write") == "write":
            return          # already retired when its WriteData landed
        self._retire_group(txn)
        super()._on_txn_done(txn, last)

    # -- arbitration --------------------------------------------------------

    def _pick(self, mem: int) -> int | None:
        """Least-served group first, then round robin inside it."""
        waiting: dict[int, list[int]] = defaultdict(list)
        for c, q in self.gq[mem].items():
            if q:
                waiting[self._group_of[c]].append(c)
        if not waiting:
            return None
        groups = sorted(waiting)
        if self.gp.group_quota:
            # A group already at its floor stands aside while any group below
            # its floor is waiting. The window above the floors stays common.
            under = [g for g in groups
                     if self.outst_group[mem][g] < self.quota]
            if under:
                groups = under
        if self.gp.policy == "round_robin":
            nxt = self.rr[mem]
            g = next((x for x in groups if x >= nxt), groups[0])
            self.rr[mem] = g + 1
        else:
            g = min(groups, key=lambda x: (self.served_group[mem][x], x))
        cores = sorted(waiting[g])
        key = (mem, g)
        start = self.grr[key]
        core = next((c for c in cores if c >= start), cores[0])
        self.grr[key] = core + 1
        return core

    # -- reporting ----------------------------------------------------------

    def fc_summary(self) -> dict[str, Any]:
        out = super().fc_summary()
        spread = []
        for per in self.served_group.values():
            vals = [v for v in per.values() if v]
            if len(vals) > 1:
                spread.append(max(vals) / max(1, min(vals)))
        out.update({
            "mode": "s16g",
            "grain": "group",
            "n_groups": self._n_groups,
            "group_quota": self.gp.group_quota,
            "quota": self.quota if self.gp.group_quota else 0,
            "table_entries": self._n_groups,
            "group_spread_max": round(max(spread), 4) if spread else 1.0,
        })
        return out


class StackGroupGrantSim(GroupGrantMixin, StackGrantSim):
    """S16 over the stacked fabric, arbitrated per top-die group.

    Inherits the stacked-fabric completer plumbing -- tracker credit before
    admission, and one `_emit` for either grant -- and replaces only the
    choice of who is granted next.
    """

    def __init__(self, topo: StackTopology,
                 params: StackGroupGrantParams | None = None, *,
                 seed: int = 0) -> None:
        super().__init__(topo, params or StackGroupGrantParams(), seed=seed)
        group_of = [max(0, nd.die) for nd in topo.nodes]
        self._grant_group_init(group_of, topo.n_die)
