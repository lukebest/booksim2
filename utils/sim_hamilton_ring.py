#!/usr/bin/env python3
"""Event-driven allgather simulator over a Hamiltonian ring on a mesh.

Given a ring order (a Hamiltonian cycle or open path produced by
hamilton_ring.find_ring), every node performs an allgather by pumping its own
message around the ring. Forwarding is IN-NETWORK: when a flit reaches a node it
is ejected once via the down-ramp (a copy lands in that node's SRAM) AND
forwarded onward on the next ring link; intermediate nodes never re-inject, so
the PE/SRAM bounce is paid only once per (flit, node).

Two modes, matching the project's ramp-bandwidth assumption:

  uni  unidirectional ring (closed cycle only): each source streams its message
       one way around the whole ring (L-1 hops). PE<->router ramps move
       1 flit/cycle.
  bi   bidirectional ring: each source streams its message BOTH ways; every
       other node is reached by the shorter arc, so each flit travels at most
       ceil((L-1)/2) hops. PE<->router ramps move 2 flit/cycle. Works on a
       closed cycle and on an open path (a node sends toward both ends).

Resource model (one global calendar, conflict-free by construction):
  * each directed ring link carries <= 1 flit/cycle;
  * each node up-ramp injects <= ramp_bw flit/cycle, down-ramp ejects
    <= ramp_bw flit/cycle;
  * link latency is H cycles (horizontal hop) or V cycles (vertical hop);
  * ramp latency is `ramp` cycles.

makespan = last cycle at which any flit finishes its down-ramp eject.
"""

import heapq
from collections import defaultdict

MX, MY, H, V, RAMP = 12, 16, 4, 8, 1


def edge_lat(u, v, mx, h, vlat):
    return h if (u // mx) == (v // mx) else vlat


class Calendar:
    """Per-key cycle allocator with a fixed per-cycle capacity.

    cap == 1 uses union-find style path compression for O(1) amortized
    allocation; cap > 1 keeps per-cycle counts (capacities here are tiny).
    """

    def __init__(self, cap):
        self.cap = cap
        if cap == 1:
            self._nxt = defaultdict(dict)
        else:
            self._cnt = defaultdict(dict)

    def reserve(self, key, earliest):
        if self.cap == 1:
            nxt = self._nxt[key]
            t = earliest
            chain = []
            while t in nxt:
                chain.append(t)
                t = nxt[t]
            nxt[t] = t + 1
            for c in chain:
                nxt[c] = t + 1
            return t
        cnt = self._cnt[key]
        t = earliest
        while cnt.get(t, 0) >= self.cap:
            t += 1
        cnt[t] = cnt.get(t, 0) + 1
        return t


def source_chains(order, is_cycle, mode):
    """For each source, the list of forwarding chains (node sequences starting
    at the source). uni -> one chain; bi -> two chains (one per direction)."""
    L = len(order)
    chains = {}
    for i, src in enumerate(order):
        if mode == "uni":
            seq = [order[(i + k) % L] for k in range(L)]  # src then L-1 others
            chains[src] = [seq]
        else:  # bi
            if is_cycle:
                fwd_count = L // 2                 # nodes reached going forward
                bwd_count = (L - 1) - fwd_count    # nodes reached going backward
                fwd = [order[(i + k) % L] for k in range(fwd_count + 1)]
                bwd = [order[(i - k) % L] for k in range(bwd_count + 1)]
                chains[src] = [fwd, bwd]
            else:  # open path: send toward both ends
                fwd = [order[j] for j in range(i, L)]
                bwd = [order[j] for j in range(i, -1, -1)]
                cs = []
                if len(fwd) > 1:
                    cs.append(fwd)
                if len(bwd) > 1:
                    cs.append(bwd)
                chains[src] = cs
    return chains


def simulate(order, is_cycle, mode, mx=MX, my=MY, h=H, vlat=V, ramp=RAMP,
             ramp_bw=None, msg_size=1, collect=False):
    """Run the ring allgather. Returns a result dict.

    mode: 'uni' (ramp_bw default 1) or 'bi' (ramp_bw default 2).
    """
    if mode == "uni" and not is_cycle:
        raise ValueError("unidirectional ring requires a closed cycle")
    if ramp_bw is None:
        ramp_bw = 1 if mode == "uni" else 2

    L = len(order)
    nodes = set(order)
    chains = source_chains(order, is_cycle, mode)

    link_cal = Calendar(1)
    up_cal = Calendar(ramp_bw)
    down_cal = Calendar(ramp_bw)

    eject_count = defaultdict(int)
    link_load = defaultdict(int)
    edges = []   # (src, p, c, send, arrive) when collect
    ejects = []  # (node, eject, src) when collect

    # Event = (ready_time, seq, src, chain_id, hop_index, flit_k)
    pq = []
    seq = 0
    for src, cs in chains.items():
        for ci, chain in enumerate(cs):
            if len(chain) < 2:
                continue
            for k in range(msg_size):
                inj = up_cal.reserve(src, k)      # up-ramp, pipelined from cycle k
                ready = inj + ramp
                heapq.heappush(pq, (ready, seq, src, ci, 0, k))
                seq += 1

    makespan = 0
    while pq:
        ready, _, src, ci, idx, k = heapq.heappop(pq)
        chain = chains[src][ci]
        p = chain[idx]
        c = chain[idx + 1]
        send = link_cal.reserve((p, c), ready)
        arrive = send + edge_lat(p, c, mx, h, vlat)
        ej = down_cal.reserve(c, arrive)
        done = ej + ramp
        if done > makespan:
            makespan = done
        eject_count[c] += 1
        link_load[(p, c)] += 1
        if collect:
            edges.append((src, p, c, send, arrive))
            ejects.append((c, ej, src))
        if idx + 2 < len(chain):
            heapq.heappush(pq, (arrive, seq, src, ci, idx + 1, k))
            seq += 1

    expected = (L - 1) * msg_size
    bad_eject = sorted(n for n in nodes if eject_count[n] != expected)
    busiest_link = max(link_load.values(), default=0)

    res = {
        "mode": mode,
        "is_cycle": is_cycle,
        "ramp_bw": ramp_bw,
        "msg_size": msg_size,
        "ring_len": L,
        "makespan": makespan,
        "expected_eject_per_node": expected,
        "eject_ok": not bad_eject,
        "bad_eject_nodes": bad_eject[:5],
        "busiest_link_flits": busiest_link,
    }
    if collect:
        res["edges"] = edges
        res["ejects"] = ejects
    return res


def build_traces(res, mx=MX, my=MY, h=H, vlat=V):
    """Active-only per-cycle traces (summary / links / routers) from a collected
    simulate() result. Only cycles/links/routers with activity are emitted, to
    keep the CSVs compact for the long ring makespans."""
    edges = res["edges"]
    ejects = res["ejects"]
    makespan = res["makespan"]

    link_inject = defaultdict(lambda: defaultdict(int))    # link -> cycle -> count
    link_inflight = defaultdict(lambda: defaultdict(int))
    link_sources = defaultdict(lambda: defaultdict(list))
    for src, p, c, send, arrive in edges:
        link_inject[(p, c)][send] += 1
        link_sources[(p, c)][send].append(src)
        for t in range(send, arrive):
            link_inflight[(p, c)][t] += 1

    router_eject = defaultdict(lambda: defaultdict(int))
    router_eject_src = defaultdict(lambda: defaultdict(list))
    router_arrive = defaultdict(lambda: defaultdict(int))
    router_forward = defaultdict(lambda: defaultdict(int))
    for node, ej, src in ejects:
        router_eject[node][ej] += 1
        router_eject_src[node][ej].append(src)
    for src, p, c, send, arrive in edges:
        router_arrive[c][arrive] += 1
        router_forward[p][send] += 1

    link_rows = []
    per_cycle_inject = defaultdict(int)
    per_cycle_eject = defaultdict(int)
    per_cycle_inflight = defaultdict(int)
    for (p, c) in sorted(set(list(link_inject) + list(link_inflight))):
        px, py = p % mx, p // mx
        cx, cy = c % mx, c // mx
        kind = "H" if (p // mx) == (c // mx) else "V"
        cycles = set(link_inject[(p, c)]) | set(link_inflight[(p, c)])
        for t in sorted(cycles):
            inj = link_inject[(p, c)].get(t, 0)
            inf = link_inflight[(p, c)].get(t, 0)
            per_cycle_inject[t] = max(per_cycle_inject[t], inj)
            per_cycle_inflight[t] = max(per_cycle_inflight[t], inf)
            srcs = link_sources[(p, c)].get(t, [])
            link_rows.append({
                "cycle": t,
                "link": f"({px},{py})->({cx},{cy})",
                "kind": kind,
                "inject": inj,
                "inFlight": inf,
                "sources": ",".join(map(str, srcs)) if srcs else "—",
            })

    router_rows = []
    all_routers = set(list(router_eject) + list(router_arrive) + list(router_forward))
    for node in sorted(all_routers):
        nx, ny = node % mx, node // mx
        cycles = (set(router_eject[node]) | set(router_arrive[node])
                  | set(router_forward[node]))
        for t in sorted(cycles):
            ec = router_eject[node].get(t, 0)
            per_cycle_eject[t] = max(per_cycle_eject[t], ec)
            esrc = router_eject_src[node].get(t, [])
            router_rows.append({
                "cycle": t,
                "router": f"({nx},{ny})",
                "eject": ec,
                "eject_sources": ",".join(map(str, esrc)) if esrc else "—",
                "arrive": router_arrive[node].get(t, 0),
                "forward": router_forward[node].get(t, 0),
            })

    summary = []
    active = sorted(set(per_cycle_inject) | set(per_cycle_eject)
                    | set(per_cycle_inflight))
    for t in active:
        mi = per_cycle_inject.get(t, 0)
        me = per_cycle_eject.get(t, 0)
        mf = per_cycle_inflight.get(t, 0)
        summary.append({
            "cycle": t,
            "max_inject": mi,
            "max_inFlight": mf,
            "max_eject": me,
            "conflict_free": 1 if mi <= 1 and me <= res["ramp_bw"] else 0,
            "pipelined": 1 if mf > 1 else 0,
        })
    return summary, link_rows, router_rows


if __name__ == "__main__":
    import hamilton_ring as hr

    order = hr.snake_cycle(MX, MY)
    for mode in ("uni", "bi"):
        r = simulate(order, True, mode, msg_size=1)
        print(f"healthy {mode:3s} ring: makespan={r['makespan']:5d} "
              f"ramp_bw={r['ramp_bw']} eject_ok={r['eject_ok']} "
              f"busiest_link={r['busiest_link_flits']}")
