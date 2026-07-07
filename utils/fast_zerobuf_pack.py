#!/usr/bin/env python3
"""FastCal: O(1)-amortized uniform-capacity cycle calendar (union-find).

Generalizes sim_hamilton_ring.py's Calendar (which only had the union-find
fast path for capacity==1 links, falling back to a linear per-cycle scan for
capacity>1 ramps) to an arbitrary capacity via union-find over cycles once
they become FULL: querying "smallest free cycle >= X" is O(1) amortized
(path compression) regardless of how many reservations a key has already
accumulated, so it stays fast even when a key (e.g. a hot ring link, or a
node's down-ramp) is touched by thousands of sources.

This is the resource-reservation primitive used by utils/allgather_fast_sim.py
for the >16x16 event-driven allgather sweep (see that module's docstring for
why event-driven simulation, not sched_zerobuf_compare's rigid single-offset
0-buffer packer, is used at scale: the rigid packer requires finding one
offset that simultaneously satisfies O(N) resource constraints per source,
which stays O(N^3) overall even with an O(1) single-resource query; the
event-driven model reserves resources one hop at a time in causal order,
which turns each reservation into a single independent O(1)-amortized query).
"""


class FastCal:
    """Per-key uniform-capacity calendar: union-find over cycles once they
    become FULL, so "next free cycle >= at" is O(1) amortized regardless of
    how many prior reservations that key has accumulated."""

    __slots__ = ("nxt", "cnt")

    def __init__(self):
        self.nxt = {}   # key -> {cyc: next_known_free_or_full_cyc}
        self.cnt = {}   # key -> {cyc: count}  (only for not-yet-full cycles)

    def peek(self, key, at):
        """Smallest cycle >= at that is not yet at capacity for this key."""
        nxt = self.nxt.get(key)
        if not nxt:
            return at
        t = at
        path = None
        while t in nxt:
            if path is None:
                path = []
            path.append(t)
            t = nxt[t]
        if path:
            for p in path:
                nxt[p] = t
        return t

    def reserve(self, key, earliest, cap):
        """Reserve the earliest free cycle >= earliest for key (capacity cap);
        returns the reserved cycle."""
        t = self.peek(key, earliest)
        self.commit(key, t, cap)
        return t

    def commit(self, key, cyc, cap):
        cnt = self.cnt.setdefault(key, {})
        c = cnt.get(cyc, 0) + 1
        if c >= cap:
            self.nxt.setdefault(key, {})[cyc] = cyc + 1
            if cyc in cnt:
                del cnt[cyc]
        else:
            cnt[cyc] = c
