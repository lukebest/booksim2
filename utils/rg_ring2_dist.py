#!/usr/bin/env python3
"""S4: local / distributed injection policies on the S0 datapath.

No central matching. Each node only looks at local queues, I-tag, hop
credit, and (optionally) a per-destination outstanding counter. The
point is to close S0's gap to the analytic bound without S2's arbiter.

Policies (combinable via params)
--------------------------------
resp_bypass_itag   responses ignore a request-held I-tag
no_req_itag        cores never raise I-tag (responses still may)
leave_useful       at a core prefer ejecting resp; at an HA prefer req
ha_outst           extra per-(core, HA) outstanding cap (0 = off)
req_slot           cores may inject requests only in even slots of
                   this many cycles (0 = off). A distributed two-wave.
hol_bypass         dir-VOQ: if the FIFO head cannot board, try the
                   other direction's head (same first-hop is unique
                   per dir on a ring).
lqf                when both dirs can board, inject the longer VOQ
dest_voq           per-destination VOQ + RR (skip a dest that cannot
                   board). Prevents an easy dest from filling the
                   outstanding scoreboard.
dest_credit        in-flight flits allowed toward a destination
                   (0 = off). Credit returns when the flit PE-drains.
kind_req / kind_resp
                   clocked two-wave (shared cycle counter). For
                   kind_req cycles only requests inject, then
                   kind_resp cycles only responses. 0 = off.
circ_tokens        circulating inject tokens per (plane, dir).
                   A node may inject on that dir only while it
                   holds a token. Tokens step one hop / cycle.
                   0 = off. Fair, but throttles injectors.
resp_idle          HA injects a response only if no request was
                   PE-drained here for this many cycles (0 = off).
ej_lock            book the dest leave slot at ETA; deny inject if
                   that (dst, plane, cycle) is already taken.
ej_keep            same-cycle dest clash: "node" (lowest id, S5) or
                   "oldest" (earliest t_gen, S6). Same bitmap.
nbr2               2-hop look-ahead: deny inject if a flit already
                   in flight will take the neighbor's outgoing hop
                   in the same cycle we would arrive.
arc_lock           in-band / neighbor reservation of downstream
                   inject slots along the path ("" = off):
                   "neighbor"  1-bit lock on the next node at
                               t+hop_lat
                   "ctrl1"     hop-by-hop lock, 1 cycle/hop, TTL =
                               remaining hops (stays ahead of data)
                   "instant"   oracle: all downstream slots written
                               in the inject cycle
path_peek / path_mid
                   last-N hop occupancy of in-flight arrivals only
                   (no ghost reservation). Ties S6 makespan; do not
                   ship as a default.
age_sel            local oldest / per-core reorder, never dest-skip.
                   "recv"/"core" hit allpairs 88 but lose on 10k.
age_gap            deny if this core is ahead by >N recv flits.
                   Small gaps collapse uniform; large gaps are no-ops.
cut_tok / cut_phase / cut_credit
                   bisection-gap tokens, CW/CCW TDM, or in-flight
                   credit on the two ring cuts. Tokens/TDM lose.
                   cut_credit=16 wins K=500 but loses 10k vs S6.
resp_space / resp_burst
                   space HA response injects; worse than S6.
ej_hot / ej_slack  dest-hot adaptive leave slack. Check-only
                   h2c allpairs 92 but K=500 3709. Book variants worse.
                   Feedback-hot dests collapse uniform.
hop_tab            first-hop interval from real launches; no-op vs S6.
plane_bounce       dest-clash flips plane; no-op vs S6.
resp_train         first resp books R consecutive dest leave slots.
                   Hard ownership deadlocks (later flits miss ETA).
                   Fallback: allpairs 96 but K=100/500/10k
                   603/2863/13743 vs S6 583/2846/13200. Dest is
                   not the 10k gap. S4+S6 is a no-op vs S6.
hop_bounce         if the assigned plane's first hop is busy, late-bind
                   to the other plane (dest leave must also be free).
                   S7 default. Unlike plane_bounce (dest-clash only).
hop_bounce_age     with hop_bounce: refuse if an older waiting flit
                   would be skipped. "hol"/"node" identical here.
                   allpairs 75, 10k p99 3917→2896, but seed0
                   12924 vs S7 12824. Do not ship as default.
hop_book           book the first N downstream hops (excl. dest).
                   book2 wins K=500 (2676) but 10k 12944.
                   age+book2 seed0 12800 (24 cycles) and loses
                   seed1; extra bits. Not S8.
late_plane         always pick plane at inject among those with hop
                   and dest free. "need" = only if assigned cannot.
                   "occ" / "dest" = both-ok tie-break. S8 default
                   is occ: 10k 11971 vs S7 12824, allpairs 72.
late_plane_sib     after late_plane, if the other srcq at this node
                   peeks to the same first hop, the short/oldest
                   HOL keeps it; the loser takes the other plane
                   when hop+dest are free. Peek-time so dest-then-
                   hop sees two hops. Not hop_hold_late /
                   hop_islip_busy / late_dir_dest / hop_joint.
                   "" = off (S13). 1 = both node kinds:
                   ap 71 K500 2348 10k 11135. Allpairs +3.
                   ha = HA only (S14): ap 64 K500 2370
                   10k 11043/11224/11201. core = cores only:
                   ap 70 K500 2361 10k 11382. Do not ship
                   1 / core.
late_plane_inj     inject-time late_plane. "" = always (S14).
                   match = skip for dest-then-hop sources that are
                   not ej_held/hop_held; persist peek plane only
                   (not dir/target). off = skip inject late_plane
                   for everyone; persist peek plane on not-held.
                   Peek already mutexed that plane; inject re-bind
                   can land on a hop dest-then-hop did not reserve.
                   Not hop_grant freeze of winner routes (10k
                   11679), not hop_islip_peek, not late_plane_sib,
                   not ej_hold_retry.
                   match: ap 78 K500 2307 10k 11358.
                   off: ap 71 K500 2393 10k 11241.
                   K500 wins on match; allpairs +14/+7 and
                   10k lose. Do not ship / retry.
late_plane         age/age_hol: occ only if oldest at node; 10k 12135.
                   live/livedir/liveocc/occlive/injlive/hop0occ:
                   real occupancy tie-breaks; livedir 11986, others
                   worse. resp_live 12002, resp_occ 12111.
hop_yield          yield if neighbor HOL is older. K=500 2746.
hop_yield_free     hop_yield only if neighbor hop still free. Same lose.
hop_cred           deny if live dir occupancy >= N. 16/32 throttle.
hop0_cred          in-flight first-hop inject credit (returns after
                   hop). cred=1 throttles; cred=2 10k ties 11971.
dest_old           wait/bind if older same-dest in flight. allpairs 89,
                   K=500 3372+. Do not ship.
nbr_adv            current-cycle neighbor inject advertise. K=500
                   2511 but 10k 13300. Do not ship.
late_dir           if first hop busy, try the other ring dir.
                   "tie" / slack=1 = no-op. slack=2 is S9 (10k 11809).
                   slack=4 identical to 2; slack=8 10k 12085.
                   late_dir_kind=resp is S10: 10k 11781, allpairs 69.
                   hold (wait if shortest hop frees next) loses K=500.
late_dir_dest      dest-aware late_dir. cooler = flip only if dest
                   leave window on the long path is strictly cooler.
                   pick = among ok planes, take the cooler dest.
late_dir_eager     flip even if the short hop is free, dest cooler.
hop_hold           same-cycle first-hop mutex (oldest keeps the hop).
                   Not a future book / peek / hop0 credit.
                   S11 hop_hold_kind=resp: 10k 11451, allpairs 67.
hop_hold_late      hop_hold still mutexes the assigned hop, but
                   _may_inject may late_dir anyway. Mutex losers
                   of a free hop can flip after the winner boards.
                   Not hop_islip_busy=late (skip hop_hold if
                   phys-busy), not hop_hold_retry, not
                   late_dir_dest, not live-HOL skip.
                   ap 68 K500 2336 10k 11323. K500 wins, 10k
                   loses. Do not ship.
hop_hold_keep      oldest (S11) | dest | dest_old — who wins a clash.
                   dest / dest_old lose K=500 (2503 / 2484).
hop_hold_retry     losers rematch unused plane/dir this cycle.
                   dir 10k 11507; both 10k 11400 but allpairs 78.
ej_hold_retry      after dest-then-hop, dest-held HOLs retry the
                   other plane (same dir). late_plane cannot see
                   same-cycle dest grants. hop_grant freezes the
                   alt route so inject late_plane cannot undo it.
                   Not hop_hold_retry (S11 hop losers), not
                   hopkeep dest occupancy, not late_plane_sib,
                   not plane_bounce (inject-time dest busy).
                   "" = off (S14).
                   plane: ap 69 K500 2458 10k 11133.
                   plane_ha: ap 66 K500 2325 10k 11135.
                   K500 wins on HA-only; allpairs +2/+5 and
                   10k lose. Do not ship / retry.
hop_joint          one oldest-first set over dest-leave AND first-hop
                   (not sequential ej_hold then hop_hold).
                   resp 10k 11502 allpairs 72; both 73/2407. Do not ship.
inj_order          visit HOL injectors in age/node order this cycle.
                   oldest 72/2441; oldest_resp 72/2442; young 77;
                   node 78. Do not ship.
inj_skip_hold      if HOL is hop_held/ej_held, try a later srcq flit.
                   Not HOL bypass (never skip a live busy HOL).
                   dest 10k 11335 but allpairs 70-72 (Pareto loses
                   S11's 67). next 11639/70; hop 77/2491.
                   dest_ha / dest_resp 10k 11358 allpairs 71.
                   dest_core K500 2385 but 10k 11585 allpairs 69.
                   Do not ship: same-area allpairs 69+ is dominated
                   by S11.
hop_islip          I-iteration dest-then-hop request-grant (S2 I=2
                   locally). Dest grant does not commit until hop
                   accept; a dest-grant that fails hop is excluded
                   from that dest's next grant (leftover re-grant).
                   Not hop_joint (one-shot IS) and not hop_hold_retry.
                   S12 hop_islip=1: 10k 11402 / 11458 / 11397,
                   allpairs 68, K500 ties 2419. I=2/4 K500 2385
                   but 10k 11481. S13 hop_islip_hopkeep=short:
                   10k 11288 / 11399 / 11270, allpairs 68,
                   K500 2362.
hop_islip_arb      dest/hop grant: oldest (S12) or rr (McKeown
                   update-on-accept pointers). dest_rr / hop_rr
                   mix one side. rr 10k 11552 ap 71; dest_rr
                   11419/71; hop_rr allpairs 65 but 10k 11446
                   K500 2460. Do not ship.
hop_sticky         last-cycle hop_hold loser wins that hop if it
                   still wants it. One cycle only; no empty-slot
                   ghost book. allpairs 66 K500 2373 but 10k
                   11452 vs S12 11402. Do not ship.
dest_sticky        last-cycle ej_hold loser preferred on that dest
                   (dst, plane) if it still wants it. One cycle;
                   no ghost book. allpairs 68 K500 2422 10k 11467.
                   Do not ship.
hop_islip_order    dest (S12 dest-then-hop) or hop (hop-then-dest).
                   hop-first allpairs 68 K500 2330 but 10k 11501.
                   Do not ship.
hop_islip_left     leftover mutex after the main match: dest (S12)
                   or hop (hop then dest among unmatched only).
                   leftover hop-first allpairs 68 K500 2406
                   10k 11428. Do not ship.
hop_islip_peek     match-time route peek: "" = late_plane+late_dir
                   (S12 10k 11402 / ap 68 / K500 2419). plane =
                   late_plane only; none = assigned route.
                   late_dir still runs at inject. plane: ap 68
                   K500 2444 10k 11575. none: ap 78 K500 2731
                   10k 12807. Do not ship.
hop_islip_pack     dest-grant rank by live first-hop occupants
                   already on the ring (arrivals at next node).
                   Not hop_book / hop_peek deny / hop0_cred /
                   hop_tab. "" = S12 oldest. spread = fewer
                   live first; pack = more live first. *_resp
                   applies the rank to responses only.
                   spread: ap 70 K500 2535 10k 11354.
                   pack: ap 72 K500 2380 10k 11304.
                   spread_resp: ap 67 K500 2386 10k 11436.
                   pack_resp: ap 64 K500 2458 10k
                   11369/11545/11479 (seed0 wins, 1/2 lose).
                   Do not ship mixed-seed 10k.
hop_islip_mutual   dest and hop grant independently; commit only
                   on agreement. Not dest-then-hop, not hop_joint,
                   not hop_hold_retry. allpairs 68 K500 2385
                   10k 11446. Do not ship.
hop_islip_split    main dest-then-hop wave is one kind; the other
                   kind waits for leftover. resp is a no-op vs S12
                   (68/2419/11402). req: ap 68 K500 2410 10k 11439.
                   Do not ship.
hop_islip_hopkeep  hop-grant among dest-granted: "" = oldest (S12),
                   short = fewer remaining hops (S13), long = more.
                   short: ap 68 K500 2362 10k 11288/11399/11270.
                   long: ap 72 K500 2456 10k 11503. Do not ship long.
                   pathlive / pathpack = live occupancy of the
                   remaining path after the first hop (sum of
                   arr_set along later nodes). Dest-granted HOLs
                   that share a hop have different dests.
                   Not hop_islip_pack / hopkeep short / path_peek.
                   pathlive: ap 68 K500 2370 10k 11307.
                   pathpack: ap 75 K500 2425 10k 11539.
                   Do not ship.
                   destlive / destpack = live occupancy at the
                   dest node (in-flight arrivals). Dest-granted
                   HOLs that share a first hop have different
                   dests. Not pathlive / hop_islip_pack /
                   dest_peek deny / hopkeep short.
                   destlive: ap 64 K500 2392 10k 11399.
                   destpack: ap 67 K500 2431 10k 11490.
                   Allpairs wins, 10k loses. Do not ship.
                   short_destlive = S13 short first, dest_live
                   only breaks hop-count ties. Not destlive
                   replacing short.
                   short_destlive: = S13 68/2362/11288. No-op —
                   dest-granted HOLs that share a hop do not
                   tie on remaining hops. Do not retry short_*
                   tie-breaks.
                   ejq / ejqpack = dest eject-queue occupancy
                   len(ejectq[(dst,plane)]). Dest-granted HOLs
                   that share a hop have different dests.
                   Not dest_live / dest_peek / hopkeep short.
                   ejq: ap 63 K500 2411 10k 11414.
                   ejqpack: ap 68 K500 2382 10k 11427.
                   Best allpairs this streak (destlive was 64);
                   10k loses. Same dest-occupancy trap. Do not
                   ship / retry.
                   destbook / destbookpack = dest leave-book
                   occupancy (sum of ej_book for (dst,plane)).
                   Not dest_live / dest_ejq / path_live /
                   hopkeep short.
                   destbook: ap 70 K500 2355 10k 11232.
                   destbookpack: ap 67 K500 2416 10k 11258.
                   Allpairs +6/+3 vs S14 64; 10k loses.
                   Same dest-occupancy trap. Do not ship / retry.
hop_islip_destkeep dest-grant among the same dest slot: "" = oldest
                   (S13), short / long = remaining hops. Dest slot
                   already keys (dst, plane, eta), so remaining hops
                   tie. short/long = S13 68/2362/11288. No-op.
                   free = prefer a physically free first hop,
                   then oldest. Dest-grant can still pick a
                   busy-hop HOL; that winner fails hop accept
                   and dest is not committed. Not leftover dest
                   pick / hop_islip_pack / hop_islip_busy.
                   free: ap 76 K500 2342 10k 11275. Allpairs
                   +8 vs S13 — do not ship.
                   free_resp: ap 68 K500 2376 10k
                   11282/11271/11317 (seed2 loses S13 11270).
                   Do not ship mixed-seed 10k.
hop_islip_leftkeep leftover hop mutex: "" = oldest (S13), short /
                   long = remaining hops. Main-wave hopkeep is S13.
                   short: ap 68 K500 2391 10k 11343.
                   long: ap 68 K500 2342 10k 11370. Do not ship.
hop_islip_busy     late = do not hop_hold a HOL whose first hop is
                   physically busy, so late_dir can still run at
                   inject. Free-hop clashes still mutex. Not
                   hop_hold_retry / hop_book / live-HOL skip.
                   late: ap 70 K500 2358 10k 11367. Do not ship.
hop_islip_leftdest leftover dest-grant among the same dest slot:
                   "" = oldest (S13). spread / pack = live
                   first-hop occupancy (arrivals at next node).
                   Sources differ so hop_live can differ; destkeep
                   is a no-op because eta already ties hops.
                   Not main-wave hop_islip_pack.
                   spread/pack/*_resp: all = S13 68/2362/11288.
                   No-op. Do not retry hop_live leftover dest.
                   free = leftover dest prefers a physically
                   free first hop, then oldest. Leftover
                   dest-then-hop commits dest even if hop fails.
                   Not hop_islip_busy / hop_live / hop_book.
                   free: = S13 68/2362/11288. No-op — leftover
                   HOLs already passed hop_hold, so first hop
                   is free. Do not retry leftover dest pick.
hop_islip_leftcommit leftover dest-then-hop dest commit:
                   "" = dest mutex ej_holds losers immediately
                   (S13). hop = dest commits only on hop accept,
                   like the main wave. Dest winners who lose a
                   leftover hop clash do not ghost-reserve dest.
                   Not hop_islip_left=hop / hop_islip_mutual /
                   leftover dest pick.
                   hop: = S13 68/2362/11288. No-op — leftover
                   dest winners rarely share a first hop after
                   dest mutex. Do not retry leftover dest commit.
hop_islip_match    "" = dest-then-hop (S13). max = dest-hop
                   bipartite max matching: left=dest slot,
                   right=first hop, edge=oldest HOL wanting
                   that pair. Not hop_joint / dest-then-hop /
                   hop_islip_mutual / hop_islip_order=hop.
                   max: ap 66 K500 2407 10k 11390. Allpairs
                   beats S11 67 but 10k loses. Do not ship.
                   max_resp = max matching among responses;
                   requests keep dest-then-hop. Not
                   hop_islip_split=resp (that was a no-op).
                   max_resp: = max 66/2407/11390 (resp-only).
                   max_req: = S13 68/2362/11288. No-op.
                   Do not retry max / max_resp / max_req.
                   weight = same graph, min-cost max-flow:
                   max card then older HOL (cost = t_gen).
                   Not hop_joint / max-card Kuhn / dest-then-hop.
                   weight / weight_resp: ap 66 K500 2411
                   10k 11396. Do not retry weight variants.
                   gs = Gale-Shapley dest↔hop: dests prefer
                   oldest HOL, hops prefer short remaining
                   path. Dest-proposing; commit only stable
                   pairs. Not dest-then-hop / mutual /
                   max / weight / hop_joint.
                   gs_hop = hop-proposing GS (same prefs).
                   gs / gs_hop identical: ap 68 K500 2471
                   10k 11235/11353/11333 (seed0/1 beat S13
                   11288/11399; seed2 loses 11270). Do not
                   ship mixed-seed 10k.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Sequence

from rg_ring2_base import Flit, Ring2BaseParams, Ring2BaseSim
from rg_ring2_topo import Ring2Topology, Txn, hop_count, is_core, is_ha


@dataclass
class Ring2DistParams(Ring2BaseParams):
    resp_bypass_itag: bool = False
    no_req_itag: bool = False
    leave_useful: bool = True
    ha_outst: int = 0
    req_slot: int = 0
    short_first: bool = False
    hol_bypass: bool = False
    lqf: bool = False
    dest_voq: bool = False
    dest_credit: int = 0
    kind_req: int = 0
    kind_resp: int = 0
    circ_tokens: int = 0
    resp_idle: int = 0
    arc_lock: str = ""
    ej_lock: bool = False
    ej_scope: str = "both"   # "both" | "req" | "resp"
    ej_keep: str = "node"    # same-cycle dest clash: "node" | "oldest"
    ej_rebook: bool = False  # slide dest booking +1 if the flit is hop-blocked
    ej_delay: int = 0        # cycles before a dest booking is visible remotely
    hop_peek: bool = False   # deny if next hop already has an in-flight arrival
    dest_peek: bool = False  # deny if dest already has an in-flight arrival at ETA
    path_peek: int = 0       # last-N hops incl. dest: in-flight occupancy only
    path_mid: int = 0        # last-N hops excl. dest (no dest-slot double-count)
    age_sel: str = ""        # "" | "hold" | "core" | "recv" | "ha_recv"
    age_gap: int = 0         # deny inject if this core is ahead by >N recvs
    cut_tok: int = 0         # tokens/ (plane, bisection gap, dir); 0 = off
    cut_credit: int = 0      # max in-flight flits per (plane, gap, dir)
    cut_phase: bool = False  # TDM: even cycles CW cut-cross, odd CCW
    resp_space: int = 0      # min cycles between HA resps to the same dest
    resp_burst: int = 0      # min cycles between any two resp injects at an HA
    ej_hot: int = 0          # after a dest clash, expand leave check for N cycles
    ej_slack: int = 1        # ±slots around ETA while dest is hot
    ej_hot_book: bool = True # also reserve the slack slots (else check-only)
    hop_tab: bool = False    # first-hop busy times from actual launches only
    plane_bounce: bool = False  # if dest slot taken, try the other plane
    hop_bounce: bool = False    # if first hop busy, try the other plane
    hop_bounce_age: str = ""    # "" | "hol" | "node"
    hop_book: int = 0           # book first N path hops (0 = off)
    late_plane: str = ""        # occ=S8; age/live*/hop0occ/resp_* optional
    late_plane_sib: str = ""    # "" | 1 | ha | core — sibling plane yield
    late_plane_inj: str = ""    # "" | match | off — skip inject late_plane
    hop_yield: bool = False     # yield if neighbor HOL is older (no book)
    hop_yield_free: bool = False  # hop_yield only if neighbor hop still free
    hop_cred: int = 0           # deny if live dir occupancy >= N (0 = off)
    hop0_cred: int = 0          # in-flight first-hop injects; credit returns after hop
    dest_old: str = ""          # "" | "wait" | "bind" — older in-flight same dest
    dest_old_kind: str = "resp" # "resp" | "both"
    nbr_adv: bool = False       # yield if neighbor advertises an older inject
    late_dir: str = ""          # "" | "tie" | "slack" — flip dir if hop busy
    late_dir_slack: int = 2     # extra hops allowed in slack mode
    late_dir_kind: str = "both" # "both" | "resp" | "req"
    late_dir_hold: bool = False # skip flip if shortest hop frees next cycle
    late_dir_dest: str = ""     # "" | "cooler" | "pick"
    late_dir_win: int = 2       # dest-book window for late_dir_dest
    late_dir_eager: bool = False  # flip while short hop free if dest cooler
    hop_hold: bool = False      # same-cycle first-hop oldest-keep
    hop_hold_kind: str = "both" # "both" | "resp" | "req"
    hop_hold_keep: str = "oldest"  # oldest | dest | dest_old | node
    hop_hold_retry: str = ""    # "" | "plane" | "dir" | "both"
    hop_hold_late: bool = False # hop_held HOL may still late_dir
    ej_hold_retry: str = ""     # "" | plane | plane_ha — dest-held plane retry
    hop_joint: bool = False     # joint dest+hop oldest-first match
    hop_islip: int = 0          # 0 = off; I dest-then-hop grant/accept iters
    hop_islip_arb: str = "oldest"  # oldest | rr | dest_rr | hop_rr
    hop_islip_order: str = "dest"  # dest | hop — which resource grants first
    hop_islip_left: str = "dest"   # dest | hop — leftover mutex order
    hop_islip_peek: str = ""      # "" | plane | none — match-time route peek
    hop_islip_pack: str = ""      # "" | spread | pack | spread_resp | pack_resp
    hop_islip_mutual: bool = False  # dest+hop grant independently; accept if both
    hop_islip_split: str = ""     # "" | resp | req — main wave kind
    hop_islip_hopkeep: str = ""   # "" | short | long | pathlive | destlive | ejq | destbook
    hop_islip_destkeep: str = ""  # "" | short | long | free — dest-grant rank
    hop_islip_leftkeep: str = ""  # "" | short | long — leftover hop path length
    hop_islip_busy: str = ""      # "" | late — skip hop_hold if hop physically busy
    hop_islip_leftdest: str = ""  # "" | spread | pack | *_resp | free
    hop_islip_leftcommit: str = ""  # "" | hop — leftover dest commit on hop accept
    hop_islip_match: str = ""     # "" | max | max_resp | max_req | weight | gs | gs_hop
    hop_sticky: bool = False    # last-cycle hop_hold loser preferred on that hop
    dest_sticky: bool = False   # last-cycle ej_hold loser preferred on that dest
    inj_order: str = ""         # "" | "oldest" | "young" | "node" | "oldest_resp"
    inj_skip_hold: str = ""     # "" | next | hop | dest | dest_ha | dest_core | dest_resp
    resp_train: bool = False  # first resp books R consecutive dest leave slots
    nbr2: bool = False


class Ring2DistSim(Ring2BaseSim):
    """S0 datapath + local priority / dest-cap / optional request slots."""

    def __init__(self, topo: Ring2Topology,
                 params: Ring2DistParams | Ring2BaseParams | None = None,
                 seed: int = 0):
        p = params or Ring2DistParams()
        if not isinstance(p, Ring2DistParams):
            p = Ring2DistParams(**{k: getattr(p, k) for k in
                                   Ring2BaseParams.__dataclass_fields__})
        super().__init__(topo, p, seed=seed)
        self.ha_used: dict[tuple[int, int], int] = defaultdict(int)
        self.dest_used: dict[int, int] = defaultdict(int)
        self.dir_starve: dict[tuple[int, int, int], int] = defaultdict(int)
        self.voq_rr: dict[tuple[int, int], int] = defaultdict(int)
        self.last_req_drain: dict[int, int] = defaultdict(lambda: -10**9)
        self.last_core_prog: dict[int, int] = defaultdict(int)
        self.last_core_recv: dict[int, int] = defaultdict(int)
        self.core_recv_n: dict[int, int] = defaultdict(int)
        self.tokens: dict[tuple[int, int], list[int]] = {}
        self.last_resp_dest: dict[tuple, int] = defaultdict(lambda: -10**9)
        self.last_resp_ha: dict[int, int] = defaultdict(lambda: -10**9)
        n = topo.n
        mid = n // 2
        self.cut_gaps = ((mid - 1, mid), (n - 1, 0))
        self.cut_pos: dict[tuple, list[int]] = {}
        self.cut_in: dict[tuple, int] = defaultdict(int)
        self.cut_free_at: dict[int, list] = defaultdict(list)
        ncut = getattr(self.p, "cut_tok", 0)
        if ncut > 0:
            k = min(ncut, n)
            for plane in range(topo.n_planes):
                for gi in range(len(self.cut_gaps)):
                    for d in (1, -1):
                        self.cut_pos[(plane, gi, d)] = [
                            (i * n) // k for i in range(k)]
        ntok = getattr(self.p, "circ_tokens", 0)
        if ntok > 0:
            k = min(ntok, n)
            for plane in range(topo.n_planes):
                for d in (1, -1):
                    self.tokens[(plane, d)] = [
                        (i * n) // k for i in range(k)]
        self.arc_block: dict[tuple, set[int]] = defaultdict(set)
        self.ctrl_at: dict[int, list] = defaultdict(list)
        self.ej_book: dict[tuple, int] = {}
        self.ej_hold: set[tuple] = set()
        self.hop_hold: set[tuple] = set()
        self.hop_grant: dict[tuple, tuple] = {}
        self.islip_dest_ptr: dict[tuple, tuple] = {}
        self.islip_hop_ptr: dict[tuple, tuple] = {}
        self.hop_sticky: set[tuple] = set()
        self.dest_sticky: set[tuple] = set()
        self.ej_eta_of: dict[int, int] = {}
        self.ej_vis_at: dict[int, int] = {}
        self.ej_at: dict[int, list] = defaultdict(list)
        self.ej_cancel: set[tuple] = set()
        self.dest_hot: dict[tuple, int] = defaultdict(lambda: -10**9)
        self.hop_at: dict[tuple, set] = defaultdict(set)
        self.hop_next: dict[tuple, set] = defaultdict(set)
        self.ej_owner: dict[tuple, int] = {}
        self.hop_rsv: dict[tuple, int] = {}
        self.hop0: dict[tuple, int] = defaultdict(int)
        self.hop0_of: dict[int, tuple] = {}
        self.inj_live: dict[int, int] = defaultdict(int)
        self.dest_inf: dict[tuple, list] = defaultdict(list)
        self.nbr_age: dict[tuple, int] = {}
        for k in ("n_deny_cap", "n_deny_dest", "n_deny_hold", "n_deny_hop",
                  "n_deny_itag", "n_deny_hop_req", "n_deny_hop_resp",
                  "n_deny_hop_core", "n_deny_hop_ha",
                  "n_deny_dest_req", "n_deny_dest_resp", "n_deny_dest_old"):
            self.st[k] = 0

    def _itag_blocks(self, f: Flit, boarding_node: int) -> bool:
        if getattr(self.p, "resp_bypass_itag", False) and f.kind == "resp":
            return False
        return super()._itag_blocks(f, boarding_node)

    def _should_raise_itag(self, node: int, f: Flit) -> bool:
        if getattr(self.p, "no_req_itag", False) and f.kind == "req":
            return False
        return True

    def _leave_order(self, node: int, plane: int, reqs: list[Flit]):
        if not getattr(self.p, "leave_useful", False) or len(reqs) <= 1:
            return super()._leave_order(node, plane, reqs)
        # useful kind first: resp at cores (recv), req at HAs (unlock resp)
        prefer = "resp" if is_core(node) else "req"
        reqs.sort(key=lambda f: 0 if f.kind == prefer else 1)
        return reqs

    def _hol_held(self, node: int, plane: int, mode: str) -> bool:
        hop = (node, plane) in self.hop_hold
        dest = (node, plane) in self.ej_hold
        if mode == "hop":
            return hop
        if mode in ("dest", "dest_ha", "dest_core", "dest_resp"):
            if mode == "dest_ha" and not is_ha(node):
                return False
            if mode == "dest_core" and not is_core(node):
                return False
            return dest
        return hop or dest

    def _select_inject_flit(self, node: int, plane: int, q):
        skip = getattr(self.p, "inj_skip_hold", "") or ""
        if skip and q and len(q) > 1 and self._hol_held(node, plane, skip):
            nxt = q[1]
            if skip == "dest_resp" and nxt.kind != "resp":
                return q[0]
            return nxt
        age = getattr(self.p, "age_sel", "") or ""
        if age in ("hold", "core", "recv", "ha_recv") and q:
            return self._select_age_hold(node, plane, q, age)
        if getattr(self.p, "dest_voq", False):
            return self._select_dest_voq(node, plane, q)
        if not (getattr(self.p, "hol_bypass", False)
                or getattr(self.p, "lqf", False)):
            return super()._select_inject_flit(node, plane, q)
        heads: dict[int, Flit] = {}
        for f in q:
            if f.dir not in heads:
                heads[f.dir] = f
        ready: list[Flit] = []
        for f in heads.values():
            if (self._ready_to_board(node, plane, f)):
                ready.append(f)
            else:
                self._bump_dir_starve(node, plane, f)
        if not ready:
            return None
        if getattr(self.p, "lqf", False) and len(ready) > 1:
            counts = {1: 0, -1: 0}
            for fl in q:
                counts[fl.dir] = counts.get(fl.dir, 0) + 1
            for fl in self.pending[(node, plane)]:
                counts[fl.dir] = counts.get(fl.dir, 0) + 1
            ready.sort(key=lambda fl: -counts.get(fl.dir, 0))
        return ready[0]

    def _select_dest_voq(self, node: int, plane: int, q):
        heads: dict[int, Flit] = {}
        for f in q:
            if f.dst not in heads:
                heads[f.dst] = f
        dests = sorted(heads)
        if not dests:
            return None
        key = (node, plane)
        start = self.voq_rr[key] % len(dests)
        ready = None
        for i in range(len(dests)):
            f = heads[dests[(start + i) % len(dests)]]
            if self._ready_to_board(node, plane, f):
                ready = f
                self.voq_rr[key] = (start + i + 1) % max(1, len(dests))
                break
            self._bump_dir_starve(node, plane, f)
        return ready

    def _core_of(self, f: Flit) -> int:
        return f.src if f.kind == "req" else f.dst

    def _select_age_hold(self, node: int, plane: int, q, mode: str):
        """Oldest-first among the local boarding queue. If that flit
        cannot board, wait — do not skip to another dest."""
        if mode == "core":
            f = min(q, key=lambda fl: (
                self.last_core_prog[self._core_of(fl)], fl.t_gen, fl.pid))
        elif mode == "recv":
            f = min(q, key=lambda fl: (
                self.last_core_recv[self._core_of(fl)], fl.t_gen, fl.pid))
        elif mode == "ha_recv":
            # only reorder responses at an HA; cores stay FIFO
            if not is_ha(node):
                return q[0]
            f = min(q, key=lambda fl: (
                self.last_core_recv[self._core_of(fl)], fl.t_gen, fl.pid))
        else:
            f = min(q, key=lambda fl: (fl.t_gen, fl.pid))
        return f

    def _ready_to_board(self, node: int, plane: int, f: Flit) -> bool:
        if not super()._may_inject(node, plane, f):
            return False
        if getattr(self.p, "dest_credit", 0) > 0:
            if self.dest_used[f.dst] >= self.p.dest_credit:
                return False
        if self._itag_blocks(f, node):
            return False
        return self._can_board(f.plane, f.dir, f.idx, f.vc)

    def _bump_dir_starve(self, node: int, plane: int, f: Flit) -> None:
        key = (node, plane, f.dir)
        self.dir_starve[key] += 1
        self.st["max_inj_starve"] = max(
            self.st["max_inj_starve"], self.dir_starve[key])
        if (self.dir_starve[key] >= self.p.t_inj
                and self._should_raise_itag(node, f)):
            rk = (f.plane, f.dir, f.vc)
            if node not in self.i_tag[rk]:
                self.i_tag[rk].add(node)
                self.st["n_itag_raised"] += 1

    def _admit(self, key) -> None:
        if not getattr(self.p, "short_first", False):
            return super()._admit(key)
        q, pend = self.srcq[key], self.pending[key]
        while pend and len(q) < self.p.inj_depth:
            best_i, best_h = 0, 10**9
            for i, f in enumerate(pend):
                if f.target < best_h:
                    best_h, best_i = f.target, i
            q.append(pend[best_i])
            del pend[best_i]
        if q:
            self.st["max_srcq"] = max(self.st["max_srcq"], len(q))

    def _may_inject(self, node: int, plane: int, f: Flit | None = None) -> bool:
        if not super()._may_inject(node, plane, f):
            self.st["n_deny_cap"] += 1
            return False
        if f is None:
            return True
        slot = getattr(self.p, "req_slot", 0)
        if slot > 0 and f.kind == "req" and (self.t // slot) % 2 == 1:
            self.st["n_outst_wait"] += 1
            return False
        cap = getattr(self.p, "ha_outst", 0)
        if cap > 0 and f.kind == "req" and is_core(f.src):
            txn = self.txn_by_id[f.txn_id]
            if self.ha_used[(txn.core, txn.ha)] >= cap:
                self.st["n_outst_wait"] += 1
                return False
        dcap = getattr(self.p, "dest_credit", 0)
        if dcap > 0 and self.dest_used[f.dst] >= dcap:
            self.st["n_outst_wait"] += 1
            return False
        kr, ks = getattr(self.p, "kind_req", 0), getattr(self.p, "kind_resp", 0)
        if kr > 0 or ks > 0:
            kr = kr or ks
            ks = ks or kr
            phase = self.t % (kr + ks)
            want = "req" if phase < kr else "resp"
            if f.kind != want:
                self.st["n_outst_wait"] += 1
                return False
        ntok = getattr(self.p, "circ_tokens", 0)
        if ntok > 0:
            held = self.tokens.get((f.plane, f.dir), [])
            if node not in held:
                self.st["n_outst_wait"] += 1
                return False
        idle = getattr(self.p, "resp_idle", 0)
        if idle > 0 and f.kind == "resp" and is_ha(node):
            if self.t - self.last_req_drain[node] < idle:
                self.st["n_outst_wait"] += 1
                return False
        space = getattr(self.p, "resp_space", 0)
        if space > 0 and f.kind == "resp" and is_ha(node):
            if self.t - self.last_resp_dest[(node, f.dst)] < space:
                self.st["n_outst_wait"] += 1
                return False
        burst = getattr(self.p, "resp_burst", 0)
        if burst > 0 and f.kind == "resp" and is_ha(node):
            if self.t - self.last_resp_ha[node] < burst:
                self.st["n_outst_wait"] += 1
                return False
        ncut = getattr(self.p, "cut_tok", 0)
        if ncut > 0 and f.dir is not None:
            gi = self._crosses_gap(f)
            if gi is not None:
                held = self.cut_pos.get((f.plane, gi, f.dir), [])
                if node not in held:
                    self.st["n_outst_wait"] += 1
                    return False
        if getattr(self.p, "cut_phase", False) and f.dir is not None:
            if self._crosses_gap(f) is not None:
                want_cw = (self.t % 2 == 0)
                if (f.dir > 0) != want_cw:
                    self.st["n_outst_wait"] += 1
                    return False
        ccap = getattr(self.p, "cut_credit", 0)
        if ccap > 0 and f.dir is not None:
            gi = self._crosses_gap(f)
            if gi is not None and self.cut_in[(f.plane, gi, f.dir)] >= ccap:
                self.st["n_outst_wait"] += 1
                return False
        if getattr(self.p, "arc_lock", "") and f.dir is not None:
            if self.t in self.arc_block[(node, plane, f.dir)]:
                self.st["n_outst_wait"] += 1
                return False
        if getattr(self.p, "late_plane", "") and f.dir is not None:
            inj = getattr(self.p, "late_plane_inj", "") or ""
            skip_inj = False
            if inj == "off":
                skip_inj = True
            elif inj == "match":
                skip_inj = ((node, plane) not in self.ej_hold
                            and (node, plane) not in self.hop_hold)
            if not skip_inj:
                self._late_bind_plane(node, f)
        grant = self.hop_grant.get((node, plane))
        if grant is not None:
            f.plane, f.dir, f.target = grant
        skip = getattr(self.p, "inj_skip_hold", "") or ""
        q = self.srcq.get((node, plane))
        is_hol = bool(q) and f is q[0]
        hop_held = (getattr(self.p, "hop_hold", False)
                    and (node, plane) in self.hop_hold
                    and not (skip and not is_hol))
        if hop_held and not getattr(self.p, "hop_hold_late", False):
            self.st["n_outst_wait"] += 1
            self.st["n_deny_hop"] += 1
            return False
        if getattr(self.p, "ej_lock", False) and self._ej_applies(f):
            # Hold is per source (node, plane) queue. After late-bind
            # the flit is on another plane's dest slot — do not apply.
            if (node, plane) in self.ej_hold and f.plane == plane:
                if not (skip and not is_hol):
                    self.st["n_outst_wait"] += 1
                    self.st["n_deny_hold"] += 1
                    return False
            if self._ej_slots_busy(f):
                self.dest_hot[(f.dst, f.plane)] = self.t
                self.st["n_deny_dest"] += 1
                if f.kind == "resp":
                    self.st["n_deny_dest_resp"] += 1
                else:
                    self.st["n_deny_dest_req"] += 1
                if getattr(self.p, "plane_bounce", False):
                    alt = 1 - f.plane
                    old = f.plane
                    f.plane = alt
                    if not self._ej_slots_busy(f) and self._can_board(
                            f.plane, f.dir, f.idx, f.vc):
                        return True
                    f.plane = old
                self.st["n_outst_wait"] += 1
                return False
        if getattr(self.p, "hop_peek", False):
            nxt = (f.idx + f.dir) % self.n
            if (self.t + self.topo.hop_lat_from(f.idx, f.dir)) in self.arr_set[(f.plane, f.dir, nxt, f.vc)]:
                self.st["n_outst_wait"] += 1
                return False
        if getattr(self.p, "dest_peek", False):
            eta = self._ej_eta(f)
            for d in (1, -1):
                if eta in self.arr_set[(f.plane, d, f.dst, f.vc)]:
                    self.st["n_outst_wait"] += 1
                    return False
        if getattr(self.p, "nbr2", False) and self._nbr2_busy(f):
            self.st["n_outst_wait"] += 1
            return False
        npeek = getattr(self.p, "path_peek", 0)
        if npeek > 0 and self._path_peek_busy(f, npeek, include_dest=True):
            self.st["n_outst_wait"] += 1
            return False
        nmid = getattr(self.p, "path_mid", 0)
        if nmid > 0 and self._path_peek_busy(f, nmid, include_dest=False):
            self.st["n_outst_wait"] += 1
            return False
        gap = getattr(self.p, "age_gap", 0)
        if gap > 0:
            core = self._core_of(f)
            recvs = [self.core_recv_n[c] for c in self.topo.cores]
            if recvs and self.core_recv_n[core] > min(recvs) + gap:
                self.st["n_outst_wait"] += 1
                return False
        if getattr(self.p, "hop_tab", False) and f.dir is not None:
            key = (f.plane, f.dir, f.idx, f.vc)
            if self.t in self.hop_at[key] or self.t in self.hop_next[key]:
                self.st["n_outst_wait"] += 1
                return False
        nbook = getattr(self.p, "hop_book", 0)
        if nbook > 0 and f.dir is not None and self._hop_booked(f, nbook):
            self.st["n_outst_wait"] += 1
            return False
        if ((getattr(self.p, "hop_yield", False)
                or getattr(self.p, "hop_yield_free", False))
                and f.dir is not None):
            if self._hop_yield_older(node, f):
                self.st["n_outst_wait"] += 1
                return False
        cred = getattr(self.p, "hop_cred", 0)
        if cred > 0 and f.dir is not None:
            if self._live_dir(f.plane, f.dir, f.vc) >= cred:
                self.st["n_outst_wait"] += 1
                return False
        h0 = getattr(self.p, "hop0_cred", 0)
        if h0 > 0 and f.dir is not None:
            if self.hop0.get((f.plane, f.dir, f.idx, f.vc), 0) >= h0:
                self.st["n_outst_wait"] += 1
                return False
        if getattr(self.p, "nbr_adv", False) and f.dir is not None:
            nxt = (f.idx + f.dir) % self.n
            age = self.nbr_age.get((nxt, f.plane, f.dir))
            if age is not None and age < f.t_gen:
                self.st["n_outst_wait"] += 1
                return False
        if getattr(self.p, "dest_old", "") and self._dest_old_applies(f):
            if self._dest_older_inf(f):
                self.st["n_outst_wait"] += 1
                self.st["n_deny_dest_old"] += 1
                return False
        if getattr(self.p, "late_dir", "") and f.dir is not None:
            kind = getattr(self.p, "late_dir_kind", "both") or "both"
            if kind == "both" or f.kind == kind:
                busy = not self._can_board(f.plane, f.dir, f.idx, f.vc)
                if busy:
                    if self._try_late_dir(node, f):
                        return True
                elif getattr(self.p, "late_dir_eager", False):
                    self._try_late_dir(node, f)
        if getattr(self.p, "hop_bounce", False) and not getattr(
                self.p, "late_plane", "") and f.dir is not None:
            if not self._can_board(f.plane, f.dir, f.idx, f.vc):
                if self._try_hop_bounce(node, f):
                    return True
        if hop_held:
            self.st["n_outst_wait"] += 1
            self.st["n_deny_hop"] += 1
            return False
        return True

    def _plane_inject_ok(self, node: int, f: Flit, pl: int) -> bool:
        old = f.plane
        f.plane = pl
        hop = self._can_board(pl, f.dir, f.idx, f.vc)
        dest = True
        if getattr(self.p, "ej_lock", False) and self._ej_applies(f):
            dest = not self._ej_slots_busy(f)
        nbook = getattr(self.p, "hop_book", 0)
        booked = nbook > 0 and self._hop_booked(f, nbook)
        yield_older = ((getattr(self.p, "hop_yield", False)
                        or getattr(self.p, "hop_yield_free", False))
                       and self._hop_yield_older(node, f))
        cred = getattr(self.p, "hop_cred", 0)
        cred_full = cred > 0 and self._live_dir(pl, f.dir, f.vc) >= cred
        h0 = getattr(self.p, "hop0_cred", 0)
        h0_full = h0 > 0 and self.hop0.get((pl, f.dir, f.idx, f.vc), 0) >= h0
        old_dest = (getattr(self.p, "dest_old", "") == "bind"
                    and self._dest_old_applies(f) and self._dest_older_inf(f))
        f.plane = old
        return (hop and dest and not booked and not yield_older
                and not cred_full and not h0_full and not old_dest)

    def _late_bind_plane(self, node: int, f: Flit, sib: bool = True) -> None:
        """Choose plane at inject from hop+dest availability."""
        self._late_bind_plane_pick(node, f)
        mode = getattr(self.p, "late_plane_sib", "") or ""
        if mode is True:
            mode = "1"
        if sib and mode:
            if mode == "ha" and not is_ha(node):
                pass
            elif mode == "core" and not is_core(node):
                pass
            else:
                self._yield_sibling_plane(node, f)

    def _yield_sibling_plane(self, node: int, f: Flit) -> None:
        """If the other srcq at this node late-binds to the same first
        hop, keep the short/oldest HOL and move the loser to the
        other plane when hop+dest are free."""
        src_pl = None
        for pl in (0, 1):
            q = self.srcq.get((node, pl))
            if q and q[0] is f:
                src_pl = pl
                break
        if src_pl is None:
            return
        sq = self.srcq.get((node, 1 - src_pl))
        if not sq:
            return
        g = sq[0]
        if g.dir is None or f.dir is None:
            return
        old_g = (g.plane, g.dir, g.target)
        self._late_bind_plane_pick(node, g)
        clash = (g.plane, g.dir, g.idx) == (f.plane, f.dir, f.idx)
        g.plane, g.dir, g.target = old_g
        if not clash:
            return
        my_key = (0 if f.target is None else f.target, f.t_gen, src_pl)
        sib_key = (0 if g.target is None else g.target, g.t_gen, 1 - src_pl)
        if my_key <= sib_key:
            return
        alt = 1 - f.plane
        if self._plane_inject_ok(node, f, alt):
            f.plane = alt

    def _late_bind_plane_pick(self, node: int, f: Flit) -> None:
        """late_plane occ/need/... pick only. No sibling yield."""
        mode = getattr(self.p, "late_plane", "") or ""
        cur, alt = f.plane, 1 - f.plane
        ok_c = self._plane_inject_ok(node, f, cur)
        ok_a = self._plane_inject_ok(node, f, alt)
        if mode == "need":
            if not ok_c and ok_a:
                f.plane = alt
            return
        cands = ([cur] if ok_c else []) + ([alt] if ok_a else [])
        if not cands:
            return
        if len(cands) == 1:
            f.plane = cands[0]
            return
        if mode == "occ":
            if self.occ.get(alt, 0) < self.occ.get(cur, 0):
                f.plane = alt
            return
        if mode == "dest":
            def dest_load(pl: int) -> int:
                old = f.plane
                f.plane = pl
                eta = self._ej_eta(f)
                n = self.ej_book.get((f.dst, pl, eta), 0)
                f.plane = old
                return n
            if dest_load(alt) < dest_load(cur):
                f.plane = alt
            return
        if mode == "age":
            # Both planes work. Only rebalance like occ if this flit
            # is the oldest waiting at the node — otherwise stay.
            if (not self._older_waiting(node, f, "node")
                    and self.occ.get(alt, 0) < self.occ.get(cur, 0)):
                f.plane = alt
        if mode == "age_hol":
            if (not self._older_waiting(node, f, "hol")
                    and self.occ.get(alt, 0) < self.occ.get(cur, 0)):
                f.plane = alt
            return
        if mode == "live":
            if self._live_plane(alt, f.vc) < self._live_plane(cur, f.vc):
                f.plane = alt
            return
        if mode == "livedir":
            if self._live_dir(alt, f.dir, f.vc) < self._live_dir(cur, f.dir, f.vc):
                f.plane = alt
            return
        if mode == "path":
            if self._path_live(f, alt) < self._path_live(f, cur):
                f.plane = alt
            return
        if mode == "liveocc":
            # live first, assignment occ as tie-break
            la, lc = self._live_plane(alt, f.vc), self._live_plane(cur, f.vc)
            if la < lc or (la == lc and self.occ.get(alt, 0) < self.occ.get(cur, 0)):
                f.plane = alt
            return
        if mode == "occlive":
            # switch only if assignment occ and live dir agree
            da, dc = self._live_dir(alt, f.dir, f.vc), self._live_dir(cur, f.dir, f.vc)
            oa, oc = self.occ.get(alt, 0), self.occ.get(cur, 0)
            if oa < oc and da <= dc:
                f.plane = alt
            return
        if mode == "injlive":
            if self.inj_live.get(alt, 0) < self.inj_live.get(cur, 0):
                f.plane = alt
            return
        if mode == "hop0occ":
            ha = self.hop0.get((alt, f.dir, f.idx, f.vc), 0)
            hc = self.hop0.get((cur, f.dir, f.idx, f.vc), 0)
            if ha < hc or (ha == hc and self.occ.get(alt, 0) < self.occ.get(cur, 0)):
                f.plane = alt
            return
        if mode == "resp_live":
            if f.kind == "resp":
                if self._live_dir(alt, f.dir, f.vc) < self._live_dir(cur, f.dir, f.vc):
                    f.plane = alt
            elif self.occ.get(alt, 0) < self.occ.get(cur, 0):
                f.plane = alt
            return
        if mode == "resp_occ":
            if f.kind == "resp":
                if self.occ.get(alt, 0) < self.occ.get(cur, 0):
                    f.plane = alt
            elif self._live_dir(alt, f.dir, f.vc) < self._live_dir(cur, f.dir, f.vc):
                f.plane = alt

    def _live_plane(self, pl: int, vc: str | None = None) -> int:
        return (self._live_dir(pl, 1, vc) + self._live_dir(pl, -1, vc))

    def _live_dir(self, pl: int, d: int, vc: str | None = None) -> int:
        if vc is None:
            return sum(self._live_dir(pl, d, v) for v in ("req", "dat"))
        return sum(len(self.arr_set[(pl, d, i, vc)]) for i in range(self.n))

    def _path_live(self, f: Flit, pl: int) -> int:
        """In-flight arrivals on this flit's next hops (no ghost book)."""
        n = 0
        take = min(4, max(0, f.target))
        node = f.idx
        tau = self.t
        vc = f.vc
        for k in range(take):
            if tau in self.arr_set[(pl, f.dir, node, vc)]:
                n += 1
            if k + 1 < take:
                tau += self.topo.hop_lat_from(node, f.dir)
                node = (node + f.dir) % self.n
        return n

    def _can_board_at(self, plane: int, direction: int, idx: int,
                      t: int, vc: str = "req") -> bool:
        """_can_board as of cycle t (t >= self.t), in-flight only."""
        seg = self._seg(plane, direction, idx, vc)
        if self.seg_free[seg] > t:
            return False
        key = (plane, direction, idx, vc)
        for dt in range(self.sigma):
            if (t + dt) in self.arr_set[key]:
                return False
        return True

    def _dest_leave_load(self, f: Flit) -> int:
        """Booked dest-leave occupancy in a window around this path's ETA."""
        win = max(0, getattr(self.p, "late_dir_win", 2))
        eta = self._ej_eta(f)
        n = 0
        for d in range(-win, win + 1):
            n += self.ej_book.get((f.dst, f.plane, eta + d), 0)
        return n

    def _try_late_dir(self, node: int, f: Flit) -> bool:
        """If the assigned first hop is busy, try the other ring dir."""
        mode = getattr(self.p, "late_dir", "") or ""
        opts = self.topo.hop_options(f.idx, f.dst)
        alt = -f.dir
        if mode == "tie" and alt not in opts:
            return False
        if getattr(self.p, "late_dir_hold", False):
            if self._can_board_at(f.plane, f.dir, f.idx, self.t + 1, f.vc):
                return False
        old_d, old_t, old_pl = f.dir, f.target, f.plane
        new_t = hop_count(f.idx, f.dst, alt, self.n)
        slack = max(0, getattr(self.p, "late_dir_slack", 2))
        if mode == "slack" and new_t > old_t + slack:
            return False
        dest_mode = getattr(self.p, "late_dir_dest", "") or ""
        if getattr(self.p, "late_dir_eager", False) and not dest_mode:
            dest_mode = "cooler"
        short_load = self._dest_leave_load(f) if dest_mode else 0
        planes = [old_pl, 1 - old_pl]
        cands: list[tuple[int, int]] = []
        for pl in planes:
            f.dir, f.target, f.plane = alt, new_t, pl
            dest_ok = True
            if getattr(self.p, "ej_lock", False) and self._ej_applies(f):
                dest_ok = ((node, pl) not in self.ej_hold
                           and not self._ej_slots_busy(f))
            if not (dest_ok and self._can_board(pl, alt, f.idx, f.vc)):
                continue
            load = self._dest_leave_load(f) if dest_mode else 0
            if dest_mode == "cooler" and load >= short_load:
                continue
            cands.append((load, pl))
        if not cands:
            f.dir, f.target, f.plane = old_d, old_t, old_pl
            return False
        if dest_mode == "pick":
            load, pl = min(cands, key=lambda x: (x[0], 0 if x[1] == old_pl else 1))
            f.dir, f.target, f.plane = alt, new_t, pl
            return True
        f.dir, f.target, f.plane = alt, new_t, cands[0][1]
        return True

    def _dest_old_applies(self, f: Flit) -> bool:
        kind = getattr(self.p, "dest_old_kind", "resp") or "resp"
        return kind == "both" or f.kind == kind

    def _dest_older_inf(self, f: Flit) -> bool:
        ages = self.dest_inf.get((f.dst, f.plane, f.dir))
        return bool(ages) and min(ages) < f.t_gen

    def _hop_yield_older(self, node: int, f: Flit) -> bool:
        """True if injecting would occupy the next hop of a neighbor
        that already has an older waiting flit. Check-only."""
        if f.target <= 1:
            return False
        nxt = (f.idx + f.dir) % self.n
        q = self.srcq.get((nxt, f.plane))
        if not (q and q[0].t_gen < f.t_gen):
            return False
        if getattr(self.p, "hop_yield_free", False):
            arrive = self.t + self.topo.hop_lat_from(f.idx, f.dir)
            key = (f.plane, f.dir, nxt, f.vc)
            if arrive in self.arr_set[key]:
                return False
        return True

    def _older_waiting(self, node: int, f: Flit, mode: str) -> bool:
        """True if bouncing f would skip an older waiting flit."""
        if mode == "hol":
            q = self.srcq.get((node, 1 - f.plane))
            return bool(q) and q[0].t_gen < f.t_gen
        if mode == "node":
            for pl in range(self.n_planes):
                for g in self.srcq.get((node, pl), ()):
                    if g is not f and g.t_gen < f.t_gen:
                        return True
            return False
        return False

    def _hop_slots(self, f: Flit, nbook: int):
        """Downstream hops after inject, excluding dest leave."""
        hops = max(0, f.target - 1)
        take = min(nbook, hops)
        node = f.idx
        tau = self.t
        for _ in range(take):
            tau += self.topo.hop_lat_from(node, f.dir)
            node = (node + f.dir) % self.n
            yield (f.plane, f.dir, node, tau)

    def _hop_booked(self, f: Flit, nbook: int) -> bool:
        return any(self.hop_rsv.get(slot, 0) > 0
                   for slot in self._hop_slots(f, nbook))

    def _try_hop_bounce(self, node: int, f: Flit) -> bool:
        """Late-bind to the other plane if its first hop (and dest
        leave, when S5/S6 is on) is free. Mutates f.plane on success."""
        age = getattr(self.p, "hop_bounce_age", "") or ""
        if age and self._older_waiting(node, f, age):
            return False
        alt = 1 - f.plane
        old = f.plane
        f.plane = alt
        dest_ok = True
        if getattr(self.p, "ej_lock", False) and self._ej_applies(f):
            dest_ok = ((node, alt) not in self.ej_hold
                       and not self._ej_slots_busy(f))
        nbook = getattr(self.p, "hop_book", 0)
        booked = nbook > 0 and self._hop_booked(f, nbook)
        if dest_ok and not booked and self._can_board(alt, f.dir, f.idx, f.vc):
            return True
        f.plane = old
        return False

    def _path_peek_busy(self, f: Flit, last_n: int, *,
                        include_dest: bool) -> bool:
        """True if an in-flight flit already occupies one of our last
        `last_n` hops at the cycle we would arrive there. Read-only:
        does not book empty future slots."""
        hops = max(1, f.target)
        end = hops + 1 if include_dest else hops
        start_k = max(1, hops - last_n + (1 if include_dest else 0))
        node = f.idx
        tau = self.t
        for k in range(end):
            if k >= start_k and tau in self.arr_set[(f.plane, f.dir, node, f.vc)]:
                return True
            if k + 1 < end:
                tau += self.topo.hop_lat_from(node, f.dir)
                node = (node + f.dir) % self.n
        return False


    def _crosses_gap(self, f: Flit) -> int | None:
        """Which bisection gap this flit's remaining path uses, if any."""
        d, hops = f.dir, max(0, f.target)
        for k in range(hops):
            u = (f.idx + k * d) % self.n
            v = (u + d) % self.n
            for gi, (a, b) in enumerate(self.cut_gaps):
                if {u, v} == {a, b}:
                    return gi
        return None

    def _ej_eta(self, f: Flit) -> int:
        hops = max(1, 0 if f.target is None else f.target)
        return self.t + self.topo.remaining_lat(f.idx, f.dir, hops)

    def _ej_is_hot(self, dst: int, plane: int) -> bool:
        win = getattr(self.p, "ej_hot", 0)
        return win > 0 and (self.t - self.dest_hot[(dst, plane)]) < win

    def _ej_slots(self, f: Flit) -> list[int]:
        eta = self._ej_eta(f)
        if not self._ej_is_hot(f.dst, f.plane):
            return [eta]
        slack = max(1, getattr(self.p, "ej_slack", 1))
        return [eta + d for d in range(-slack, slack + 1)]

    def _ej_slot_foreign(self, f: Flit, eta: int) -> bool:
        slot = (f.dst, f.plane, eta)
        if self.ej_book.get(slot, 0) <= 0:
            return False
        if (getattr(self.p, "resp_train", False) and f.kind == "resp"
                and self.ej_owner.get(slot) == f.txn_id):
            return False
        return True

    def _ej_slots_busy(self, f: Flit) -> bool:
        if getattr(self.p, "resp_train", False) and f.kind == "resp":
            eta = self._ej_eta(f)
            if f.seq == 0:
                # Prefer a free R-slot train; if any extra slot is
                # taken, fall back to a single S6 slot at eta.
                for i in range(1, max(1, f.nflit)):
                    if self._ej_slot_foreign(f, eta + i):
                        return self._ej_slot_foreign(f, eta)
                return self._ej_slot_foreign(f, eta)
            if self.ej_owner.get((f.dst, f.plane, eta)) == f.txn_id:
                return False
            return self._ej_slot_foreign(f, eta)
        for eta in self._ej_slots(f):
            if self._ej_slot_foreign(f, eta):
                return True
        return False

    def _ej_applies(self, f: Flit) -> bool:
        scope = getattr(self.p, "ej_scope", "both")
        return scope == "both" or f.kind == scope

    def _nbr2_busy(self, f: Flit) -> bool:
        """True if a flit already in flight will occupy the next hop
        in the same cycle this inject would arrive there."""
        nxt = (f.idx + f.dir) % self.n
        arrive = self.t + self.topo.hop_lat_from(f.idx, f.dir)
        node_key = (f.plane, f.dir, nxt, f.vc)
        if arrive in self.arr_set[node_key]:
            return True
        # 2-hop: a flit arriving at nxt+dir at arrive, having come from nxt
        nxt2 = (nxt + f.dir) % self.n
        if arrive in self.arr_set[(f.plane, f.dir, nxt2, f.vc)]:
            return True
        return False

    def _peek_inject_route(self, node: int, f: Flit) -> None:
        """Late-bind plane/dir as _may_inject would, in place."""
        if getattr(self.p, "late_plane", "") and f.dir is not None:
            self._late_bind_plane(node, f)
        lk = getattr(self.p, "late_dir_kind", "both") or "both"
        if (getattr(self.p, "late_dir", "")
                and (lk == "both" or f.kind == lk)
                and not self._can_board(f.plane, f.dir, f.idx, f.vc)):
            self._try_late_dir(node, f)

    def _hop_hold_key(self, rec: dict) -> tuple:
        keep = getattr(self.p, "hop_hold_keep", "oldest") or "oldest"
        if keep == "dest":
            return (rec["load"], rec["node"])
        if keep == "dest_old":
            return (rec["load"], rec["age"], rec["node"])
        if keep == "node":
            return (rec["node"], rec["age"])
        return (rec["age"], rec["node"], rec["src_pl"])

    def _hop_hold_alt(self, node: int, f: Flit, claimed: set[tuple]
                      ) -> tuple | None:
        """Unused same-cycle hop for a loser. Mutates f if found."""
        retry = getattr(self.p, "hop_hold_retry", "") or ""
        if not retry:
            return None
        old = (f.plane, f.dir, f.target)
        planes = [f.plane]
        if retry in ("plane", "both"):
            planes.append(1 - f.plane)
        dirs = [f.dir]
        if retry in ("dir", "both"):
            alt = -f.dir
            new_t = hop_count(f.idx, f.dst, alt, self.n)
            slack = max(0, getattr(self.p, "late_dir_slack", 2))
            kind = getattr(self.p, "late_dir_kind", "both") or "both"
            if ((kind == "both" or f.kind == kind)
                    and new_t <= f.target + slack):
                dirs.append(alt)
        for pl in planes:
            for d in dirs:
                if (pl, d) == (old[0], old[1]):
                    continue
                tgt = hop_count(f.idx, f.dst, d, self.n) if d != old[1] else old[2]
                f.plane, f.dir, f.target = pl, d, tgt
                hop = (pl, d, f.idx, f.vc)
                dest_ok = True
                if getattr(self.p, "ej_lock", False) and self._ej_applies(f):
                    dest_ok = ((node, pl) not in self.ej_hold
                               and not self._ej_slots_busy(f))
                if dest_ok and hop not in claimed and self._can_board(pl, d, f.idx, f.vc):
                    return hop
        f.plane, f.dir, f.target = old
        return None

    def _build_hop_hold(self) -> None:
        """Same-cycle first-hop mutex; optional dest keep / rematch."""
        self.hop_hold.clear()
        self.hop_grant.clear()
        kind = getattr(self.p, "hop_hold_kind", "both") or "both"
        keep = getattr(self.p, "hop_hold_keep", "oldest") or "oldest"
        retry = getattr(self.p, "hop_hold_retry", "") or ""
        extra = keep not in ("oldest",) or bool(retry)
        groups: dict[tuple, list] = defaultdict(list)
        snaps: dict[tuple, tuple] = {}
        for key in list(self.active_src):
            node, plane = key
            q = self.srcq.get(key)
            if not q or (node, plane) in self.ej_hold:
                continue
            f = q[0]
            if f.dir is None:
                continue
            if kind != "both" and f.kind != kind:
                continue
            old = (f.plane, f.dir, f.target)
            if extra:
                self._peek_inject_route(node, f)
                rec = {"age": f.t_gen, "node": node, "src_pl": plane, "f": f,
                       "load": self._dest_leave_load(f),
                       "route": (f.plane, f.dir, f.target)}
                groups[(f.plane, f.dir, f.idx)].append(rec)
                snaps[key] = old
            else:
                if getattr(self.p, "late_plane", "") and f.dir is not None:
                    self._late_bind_plane(node, f)
                lk = getattr(self.p, "late_dir_kind", "both") or "both"
                if (getattr(self.p, "late_dir", "")
                        and (lk == "both" or f.kind == lk)
                        and not self._can_board(f.plane, f.dir, f.idx, f.vc)):
                    self._try_late_dir(node, f)
                groups[(f.plane, f.dir, f.idx)].append((f.t_gen, node, plane))
            f.plane, f.dir, f.target = old
        if not extra:
            for cand in groups.values():
                if len(cand) <= 1:
                    continue
                cand.sort()
                for _age, node, plane in cand[1:]:
                    self.hop_hold.add((node, plane))
            return
        claimed: set[tuple] = set()
        losers: list[dict] = []
        for hop, cand in groups.items():
            cand.sort(key=self._hop_hold_key)
            claimed.add(hop)
            losers.extend(cand[1:])
        if retry and losers:
            second: dict[tuple, list] = defaultdict(list)
            for rec in losers:
                f = rec["f"]
                old = snaps[(rec["node"], rec["src_pl"])]
                f.plane, f.dir, f.target = rec["route"]
                hop = self._hop_hold_alt(rec["node"], f, claimed)
                if hop is None:
                    f.plane, f.dir, f.target = old
                    self.hop_hold.add((rec["node"], rec["src_pl"]))
                    continue
                rec["route"] = (f.plane, f.dir, f.target)
                rec["load"] = self._dest_leave_load(f)
                second[hop].append(rec)
                f.plane, f.dir, f.target = old
            for hop, cand in second.items():
                if hop in claimed:
                    for rec in cand:
                        self.hop_hold.add((rec["node"], rec["src_pl"]))
                    continue
                cand.sort(key=self._hop_hold_key)
                win = cand[0]
                claimed.add(hop)
                self.hop_grant[(win["node"], win["src_pl"])] = win["route"]
                for rec in cand[1:]:
                    self.hop_hold.add((rec["node"], rec["src_pl"]))
        else:
            for rec in losers:
                self.hop_hold.add((rec["node"], rec["src_pl"]))

    def _build_hop_joint(self) -> None:
        """Oldest-first independent set over dest-leave and first-hop."""
        self.ej_hold.clear()
        self.hop_hold.clear()
        self.hop_grant.clear()
        hop_kind = getattr(self.p, "hop_hold_kind", "both") or "both"
        recs: list[dict] = []
        for key in list(self.active_src):
            node, plane = key
            q = self.srcq.get(key)
            if not q:
                continue
            f = q[0]
            if not super()._may_inject(node, plane, f):
                continue
            old = (f.plane, f.dir, f.target)
            self._peek_inject_route(node, f)
            dest_slot = None
            dest_booked = False
            if getattr(self.p, "ej_lock", False) and self._ej_applies(f):
                dest_slot = (f.dst, f.plane, self._ej_eta(f))
                dest_booked = self.ej_book.get(dest_slot, 0) > 0
            hop = ((f.plane, f.dir, f.idx, f.vc)
                   if f.dir is not None else None)
            recs.append({
                "age": f.t_gen, "node": node, "src_pl": plane,
                "kind": f.kind, "dest_slot": dest_slot,
                "dest_booked": dest_booked, "hop": hop,
            })
            f.plane, f.dir, f.target = old
        recs.sort(key=lambda r: (r["age"], r["node"], r["src_pl"]))
        taken_dest = {r["dest_slot"] for r in recs
                      if r["dest_slot"] is not None and r["dest_booked"]}
        taken_hop: set[tuple] = set()
        for r in recs:
            dest_hit = (r["dest_slot"] is not None
                        and r["dest_slot"] in taken_dest)
            use_hop = (r["hop"] is not None
                       and (hop_kind == "both" or r["kind"] == hop_kind))
            hop_hit = use_hop and r["hop"] in taken_hop
            if dest_hit or hop_hit:
                if dest_hit:
                    self.ej_hold.add((r["node"], r["src_pl"]))
                    if r["dest_slot"] is not None:
                        self.dest_hot[(r["dest_slot"][0],
                                       r["dest_slot"][1])] = self.t
                if hop_hit:
                    self.hop_hold.add((r["node"], r["src_pl"]))
                continue
            if r["dest_slot"] is not None:
                taken_dest.add(r["dest_slot"])
            if use_hop:
                taken_hop.add(r["hop"])

    def _islip_pick(self, cand: list, ptr_map: dict, pkey, age_key,
                    side: str) -> dict:
        if (side == "hop" and getattr(self.p, "hop_sticky", False)
                and cand):
            stuck = [r for r in cand
                     if (pkey, r["node"], r["src_pl"]) in self.hop_sticky]
            if stuck:
                stuck.sort(key=age_key)
                return stuck[0]
        if (side == "dest" and getattr(self.p, "dest_sticky", False)
                and cand):
            stuck = [r for r in cand
                     if (pkey, r["node"], r["src_pl"]) in self.dest_sticky]
            if stuck:
                stuck.sort(key=age_key)
                return stuck[0]
        arb = getattr(self.p, "hop_islip_arb", "oldest") or "oldest"
        use_rr = arb == "rr" or arb == f"{side}_rr"
        if not use_rr or len(cand) == 1:
            cand.sort(key=age_key)
            return cand[0]
        cand = sorted(cand, key=lambda r: (r["node"], r["src_pl"]))
        last = ptr_map.get(pkey)
        if last is None:
            return cand[0]
        after = [r for r in cand if (r["node"], r["src_pl"]) > last]
        return after[0] if after else cand[0]

    def _islip_advance(self, ptr_map: dict, pkey, rec: dict) -> None:
        ptr_map[pkey] = (rec["node"], rec["src_pl"])

    def _mcmf_bipartite(self, lefts: list, rights: list,
                        edges: list[tuple]) -> dict:
        """Max-flow min-cost assignment. edges: (left, right, cost).
        Returns mate[right] = left."""
        li = {u: i for i, u in enumerate(lefts)}
        ri = {v: i for i, v in enumerate(rights)}
        nl, nr = len(lefts), len(rights)
        s, t = nl + nr, nl + nr + 1
        n = t + 1
        g: list[list[list]] = [[] for _ in range(n)]

        def add(u: int, v: int, cap: int, cost: int) -> None:
            g[u].append([v, cap, cost, len(g[v])])
            g[v].append([u, 0, -cost, len(g[u]) - 1])

        for i in range(nl):
            add(s, i, 1, 0)
        for j in range(nr):
            add(nl + j, t, 1, 0)
        for u, v, cost in edges:
            add(li[u], nl + ri[v], 1, int(cost))
        mate: dict = {}
        inf = 10 ** 18
        while True:
            dist = [inf] * n
            prev: list[tuple | None] = [None] * n
            dist[s] = 0
            q = [s]
            inq = [False] * n
            inq[s] = True
            while q:
                u = q.pop(0)
                inq[u] = False
                for ei, e in enumerate(g[u]):
                    v, cap, cost, _rev = e
                    if cap <= 0 or dist[u] + cost >= dist[v]:
                        continue
                    dist[v] = dist[u] + cost
                    prev[v] = (u, ei)
                    if not inq[v]:
                        q.append(v)
                        inq[v] = True
            if dist[t] >= inf:
                break
            v = t
            while v != s:
                u, ei = prev[v]
                e = g[u][ei]
                e[1] -= 1
                g[v][e[3]][1] += 1
                v = u
        for j, right in enumerate(rights):
            for e in g[nl + j]:
                if e[0] < nl and e[1] == 1:
                    mate[right] = lefts[e[0]]
                    break
        return mate

    def _islip_dest_hop_max(self, recs: list, use_hop, age_key,
                            weighted: bool = False) -> None:
        """Dest-hop bipartite matching. Edge = oldest HOL for that pair.
        weighted: max card then older HOL (min-cost max-flow)."""
        pair_hol: dict[tuple, dict] = {}
        for r in recs:
            if r["dest_slot"] is None or r["dest_booked"]:
                continue
            if not use_hop(r) or not r.get("hop_free", True):
                continue
            pk = (r["dest_slot"], r["hop"])
            if pk not in pair_hol or age_key(r) < age_key(pair_hol[pk]):
                pair_hol[pk] = r
        adj: dict[tuple, list] = defaultdict(list)
        dest_age: dict[tuple, tuple] = {}
        for (dest, hop), r in pair_hol.items():
            adj[dest].append(hop)
            ak = age_key(r)
            if dest not in dest_age or ak < dest_age[dest]:
                dest_age[dest] = ak
        for dest, hops in adj.items():
            hops.sort(key=lambda h, d=dest: age_key(pair_hol[(d, h)]))
        dests = sorted(adj.keys(), key=lambda d: dest_age[d])
        mate: dict[tuple, tuple] = {}
        if weighted and dests:
            hops = sorted({h for hs in adj.values() for h in hs})
            edges = [(d, h, pair_hol[(d, h)]["age"])
                     for d, hs in adj.items() for h in hs]
            mate = self._mcmf_bipartite(dests, hops, edges)
        else:
            def dfs(u: tuple, seen: set) -> bool:
                for v in adj[u]:
                    if v in seen:
                        continue
                    seen.add(v)
                    if v not in mate or dfs(mate[v], seen):
                        mate[v] = u
                        return True
                return False

            for dest in dests:
                dfs(dest, set())
        matched_dest = set(mate.values())
        matched_hop = set(mate.keys())
        matched_keys = {pair_hol[(mate[h], h)]["key"] for h in mate}
        for r in sorted(
                (x for x in recs
                 if x["dest_slot"] is not None and not use_hop(x)
                 and not x["dest_booked"]),
                key=age_key):
            if r["dest_slot"] in matched_dest:
                continue
            matched_dest.add(r["dest_slot"])
            matched_keys.add(r["key"])
        for r in sorted(
                (x for x in recs
                 if x["dest_slot"] is None and use_hop(x)
                 and x.get("hop_free", True)),
                key=age_key):
            if r["hop"] in matched_hop:
                continue
            matched_hop.add(r["hop"])
            matched_keys.add(r["key"])
        for r in recs:
            if r["key"] in matched_keys:
                continue
            dest_hit = (r["dest_slot"] is not None
                        and (r["dest_booked"] or r["dest_slot"] in matched_dest))
            hop_hit = use_hop(r) and (
                r["hop"] in matched_hop or not r.get("hop_free", True))
            if dest_hit:
                self.ej_hold.add((r["node"], r["src_pl"]))
                self.dest_hot[(r["dest_slot"][0], r["dest_slot"][1])] = self.t
            if hop_hit:
                self.hop_hold.add((r["node"], r["src_pl"]))

    def _islip_dest_hop_gs(self, recs: list, use_hop, age_key,
                           hop_propose: bool = False) -> None:
        """Gale-Shapley dest↔hop. Dests prefer oldest HOL; hops
        prefer fewer remaining hops. hop_propose=False is
        dest-optimal; True is hop-optimal."""
        pair_hol: dict[tuple, dict] = {}
        for r in recs:
            if r["dest_slot"] is None or r["dest_booked"]:
                continue
            if not use_hop(r) or not r.get("hop_free", True):
                continue
            pk = (r["dest_slot"], r["hop"])
            if pk not in pair_hol or age_key(r) < age_key(pair_hol[pk]):
                pair_hol[pk] = r
        dests = sorted({d for d, _h in pair_hol})
        hops = sorted({h for _d, h in pair_hol})

        def dest_rank(d, h) -> tuple:
            r = pair_hol[(d, h)]
            return (r["age"], r["node"], r["src_pl"])

        def hop_rank(d, h) -> tuple:
            r = pair_hol[(d, h)]
            return (r["hops"], r["age"], r["node"], r["src_pl"])

        dest_pref = {
            d: sorted((h for (dd, h) in pair_hol if dd == d),
                      key=lambda h, d=d: dest_rank(d, h))
            for d in dests}
        hop_pref = {
            h: sorted((d for (d, hh) in pair_hol if hh == h),
                      key=lambda d, h=h: hop_rank(d, h))
            for h in hops}
        mate: dict[tuple, tuple] = {}
        if hop_propose:
            nxt = {h: 0 for h in hops}
            engaged: dict = {}
            free = list(hops)
            while free:
                h = free.pop()
                prefs = hop_pref[h]
                if nxt[h] >= len(prefs):
                    continue
                d = prefs[nxt[h]]
                nxt[h] += 1
                if d not in engaged:
                    engaged[d] = h
                elif dest_rank(d, h) < dest_rank(d, engaged[d]):
                    free.append(engaged[d])
                    engaged[d] = h
                else:
                    free.append(h)
            mate = {h: d for d, h in engaged.items()}
        else:
            nxt = {d: 0 for d in dests}
            engaged = {}
            free = list(dests)
            while free:
                d = free.pop()
                prefs = dest_pref[d]
                if nxt[d] >= len(prefs):
                    continue
                h = prefs[nxt[d]]
                nxt[d] += 1
                if h not in engaged:
                    engaged[h] = d
                elif hop_rank(d, h) < hop_rank(engaged[h], h):
                    free.append(engaged[h])
                    engaged[h] = d
                else:
                    free.append(d)
            mate = engaged
        matched_dest = set(mate.values())
        matched_hop = set(mate.keys())
        matched_keys = {pair_hol[(mate[h], h)]["key"] for h in mate}
        for r in sorted(
                (x for x in recs
                 if x["dest_slot"] is not None and not use_hop(x)
                 and not x["dest_booked"]),
                key=age_key):
            if r["dest_slot"] in matched_dest:
                continue
            matched_dest.add(r["dest_slot"])
            matched_keys.add(r["key"])
        for r in sorted(
                (x for x in recs
                 if x["dest_slot"] is None and use_hop(x)
                 and x.get("hop_free", True)),
                key=age_key):
            if r["hop"] in matched_hop:
                continue
            matched_hop.add(r["hop"])
            matched_keys.add(r["key"])
        for r in recs:
            if r["key"] in matched_keys:
                continue
            dest_hit = (r["dest_slot"] is not None
                        and (r["dest_booked"] or r["dest_slot"] in matched_dest))
            hop_hit = use_hop(r) and (
                r["hop"] in matched_hop or not r.get("hop_free", True))
            if dest_hit:
                self.ej_hold.add((r["node"], r["src_pl"]))
                self.dest_hot[(r["dest_slot"][0], r["dest_slot"][1])] = self.t
            if hop_hit:
                self.hop_hold.add((r["node"], r["src_pl"]))

    def _build_hop_islip(self) -> None:
        """I-iteration dest-then-hop request-grant. Dest grant commits
        only on hop accept; a failed dest-grant is excluded from that
        dest's next wave so leftovers can take the slot."""
        self.ej_hold.clear()
        self.hop_hold.clear()
        self.hop_grant.clear()
        iters = max(1, int(getattr(self.p, "hop_islip", 0) or 0))
        hop_kind = getattr(self.p, "hop_hold_kind", "both") or "both"
        recs: list[dict] = []
        dest_book_n: dict[tuple, int] = defaultdict(int)
        for (dst, pl, _eta), n in self.ej_book.items():
            dest_book_n[(dst, pl)] += n
        for key in list(self.active_src):
            node, plane = key
            q = self.srcq.get(key)
            if not q:
                continue
            f = q[0]
            if not super()._may_inject(node, plane, f):
                continue
            old = (f.plane, f.dir, f.target)
            peek = getattr(self.p, "hop_islip_peek", "") or ""
            if peek == "none":
                pass
            elif peek == "plane":
                if getattr(self.p, "late_plane", "") and f.dir is not None:
                    self._late_bind_plane(node, f)
            else:
                self._peek_inject_route(node, f)
            dest_slot = None
            dest_booked = False
            if getattr(self.p, "ej_lock", False) and self._ej_applies(f):
                dest_slot = (f.dst, f.plane, self._ej_eta(f))
                dest_booked = self.ej_book.get(dest_slot, 0) > 0
            hop = ((f.plane, f.dir, f.idx, f.vc)
                   if f.dir is not None else None)
            hop_live = 0
            path_live = 0
            dest_live = 0
            if hop is not None:
                nxt = (hop[2] + hop[1]) % self.n
                hop_live = len(self.arr_set[(hop[0], hop[1], nxt, hop[3])])
                dest_live = len(self.arr_set[(hop[0], hop[1], f.dst, hop[3])])
                hops_left = 0 if f.target is None else f.target
                if hops_left >= 2:
                    for k in range(2, hops_left + 1):
                        node_k = (hop[2] + k * hop[1]) % self.n
                        path_live += len(self.arr_set[(hop[0], hop[1], node_k, hop[3])])
            recs.append({
                "key": key, "age": f.t_gen, "node": node, "src_pl": plane,
                "kind": f.kind, "dest_slot": dest_slot,
                "dest_booked": dest_booked, "hop": hop,
                "hop_live": hop_live,
                "path_live": path_live,
                "dest_live": dest_live,
                "dest_ejq": len(self.ejectq[(f.dst, f.plane)]),
                "dest_book": dest_book_n.get((f.dst, f.plane), 0),
                "hop_free": hop is None or self._can_board(*hop),
                "hops": 0 if f.target is None else f.target,
            })
            f.plane, f.dir, f.target = old

        def use_hop(r: dict) -> bool:
            return (r["hop"] is not None
                    and (hop_kind == "both" or r["kind"] == hop_kind))

        def age_key(r: dict) -> tuple:
            return (r["age"], r["node"], r["src_pl"])

        match = getattr(self.p, "hop_islip_match", "") or ""
        if match == "max":
            self._islip_dest_hop_max(recs, use_hop, age_key)
            return
        if match == "gs":
            self._islip_dest_hop_gs(recs, use_hop, age_key)
            return
        if match == "gs_hop":
            self._islip_dest_hop_gs(recs, use_hop, age_key, hop_propose=True)
            return
        if match == "weight":
            self._islip_dest_hop_max(recs, use_hop, age_key, weighted=True)
            return
        if match == "weight_resp":
            self._islip_dest_hop_max(
                [r for r in recs if r["kind"] == "resp"],
                use_hop, age_key, weighted=True)
            recs = [r for r in recs if r["kind"] != "resp"]
        if match == "max_resp":
            self._islip_dest_hop_max(
                [r for r in recs if r["kind"] == "resp"], use_hop, age_key)
            recs = [r for r in recs if r["kind"] != "resp"]
        elif match == "max_req":
            self._islip_dest_hop_max(
                [r for r in recs if r["kind"] == "req"], use_hop, age_key)
            recs = [r for r in recs if r["kind"] != "req"]

        pack = getattr(self.p, "hop_islip_pack", "") or ""
        pack_resp = pack.endswith("_resp")
        pack_base = pack[:-5] if pack_resp else pack
        destkeep = getattr(self.p, "hop_islip_destkeep", "") or ""

        def dest_key(r: dict) -> tuple:
            if destkeep == "short":
                return (r["hops"], r["age"], r["node"], r["src_pl"])
            if destkeep == "long":
                return (-r["hops"], r["age"], r["node"], r["src_pl"])
            if destkeep == "free":
                return (0 if r.get("hop_free", True) else 1,
                        r["age"], r["node"], r["src_pl"])
            if destkeep == "free_resp":
                if r["kind"] != "resp":
                    return age_key(r)
                return (0 if r.get("hop_free", True) else 1,
                        r["age"], r["node"], r["src_pl"])
            if pack_resp and r["kind"] != "resp":
                return age_key(r)
            if pack_base == "spread":
                return (r["hop_live"], r["age"], r["node"], r["src_pl"])
            if pack_base == "pack":
                return (-r["hop_live"], r["age"], r["node"], r["src_pl"])
            return age_key(r)

        hop_key = dest_key if pack_base else age_key
        split = getattr(self.p, "hop_islip_split", "") or ""
        hopkeep = getattr(self.p, "hop_islip_hopkeep", "") or ""

        def in_main(r: dict) -> bool:
            return (not split) or r["kind"] == split

        def hop_pick_key(r: dict) -> tuple:
            if hopkeep == "short":
                return (r["hops"], r["age"], r["node"], r["src_pl"])
            if hopkeep == "short_destlive":
                return (r["hops"], r.get("dest_live", 0),
                        r["age"], r["node"], r["src_pl"])
            if hopkeep == "long":
                return (-r["hops"], r["age"], r["node"], r["src_pl"])
            if hopkeep == "pathlive":
                return (r.get("path_live", 0), r["age"], r["node"], r["src_pl"])
            if hopkeep == "pathpack":
                return (-r.get("path_live", 0), r["age"], r["node"], r["src_pl"])
            if hopkeep == "destlive":
                return (r.get("dest_live", 0), r["age"], r["node"], r["src_pl"])
            if hopkeep == "destpack":
                return (-r.get("dest_live", 0), r["age"], r["node"], r["src_pl"])
            if hopkeep == "ejq":
                return (r.get("dest_ejq", 0), r["age"], r["node"], r["src_pl"])
            if hopkeep == "ejqpack":
                return (-r.get("dest_ejq", 0), r["age"], r["node"], r["src_pl"])
            if hopkeep == "destbook":
                return (r.get("dest_book", 0), r["age"], r["node"], r["src_pl"])
            if hopkeep == "destbookpack":
                return (-r.get("dest_book", 0), r["age"], r["node"], r["src_pl"])
            return hop_key(r)

        matched: set[tuple] = set()
        taken_dest = {r["dest_slot"] for r in recs
                      if r["dest_slot"] is not None and r["dest_booked"]}
        taken_hop: set[tuple] = set()
        phys_busy: set[tuple] = set()
        for r in recs:
            if r["hop"] is not None and not self._can_board(*r["hop"]):
                taken_hop.add(r["hop"])
                phys_busy.add(r["hop"])
        busy_late = (getattr(self.p, "hop_islip_busy", "") or "") == "late"

        def hold_hop(r: dict) -> None:
            if busy_late and r.get("hop") in phys_busy:
                return
            self.hop_hold.add((r["node"], r["src_pl"]))

        failed_dest: dict[tuple, set] = defaultdict(set)
        failed_hop: dict[tuple, set] = defaultdict(set)
        order = getattr(self.p, "hop_islip_order", "dest") or "dest"
        mutual = bool(getattr(self.p, "hop_islip_mutual", False))

        for _ in range(iters):
            dest_grant: dict[tuple, dict] = {}
            hop_grant: dict[tuple, dict] = {}
            if mutual:
                dest_groups: dict[tuple, list] = defaultdict(list)
                dest_ok: dict[tuple, dict] = {}
                for r in recs:
                    if r["key"] in matched:
                        continue
                    if r["dest_slot"] is None:
                        dest_ok[r["key"]] = r
                        continue
                    if r["dest_slot"] in taken_dest:
                        continue
                    if r["key"] in failed_dest[r["dest_slot"]]:
                        continue
                    dest_groups[r["dest_slot"]].append(r)
                for slot, cand in dest_groups.items():
                    win = self._islip_pick(
                        cand, self.islip_dest_ptr, (slot[0], slot[1]),
                        dest_key, "dest")
                    dest_grant[slot] = win
                    dest_ok[win["key"]] = win
                hop_groups: dict[tuple, list] = defaultdict(list)
                hop_ok: dict[tuple, dict] = {}
                for r in recs:
                    if r["key"] in matched:
                        continue
                    if not use_hop(r):
                        hop_ok[r["key"]] = r
                        continue
                    if r["hop"] in taken_hop:
                        continue
                    if r["key"] in failed_hop[r["hop"]]:
                        continue
                    hop_groups[r["hop"]].append(r)
                hop_win: dict[tuple, dict] = {}
                for hop, cand in hop_groups.items():
                    win = self._islip_pick(
                        cand, self.islip_hop_ptr, hop, hop_key, "hop")
                    hop_grant[hop] = win
                    hop_ok[win["key"]] = win
                    hop_win[hop] = win
            elif order == "hop":
                hop_ok: dict[tuple, dict] = {}
                hop_groups: dict[tuple, list] = defaultdict(list)
                for r in recs:
                    if r["key"] in matched:
                        continue
                    if not use_hop(r):
                        hop_ok[r["key"]] = r
                        continue
                    if r["hop"] in taken_hop:
                        continue
                    if r["key"] in failed_hop[r["hop"]]:
                        continue
                    hop_groups[r["hop"]].append(r)
                for hop, cand in hop_groups.items():
                    win = self._islip_pick(
                        cand, self.islip_hop_ptr, hop, hop_key, "hop")
                    hop_grant[hop] = win
                    hop_ok[win["key"]] = win
                dest_groups: dict[tuple, list] = defaultdict(list)
                dest_ok: dict[tuple, dict] = {}
                for r in hop_ok.values():
                    if r["dest_slot"] is None:
                        dest_ok[r["key"]] = r
                        continue
                    if r["dest_slot"] in taken_dest:
                        continue
                    dest_groups[r["dest_slot"]].append(r)
                for slot, cand in dest_groups.items():
                    win = self._islip_pick(
                        cand, self.islip_dest_ptr, (slot[0], slot[1]),
                        dest_key, "dest")
                    dest_grant[slot] = win
                    dest_ok[win["key"]] = win
                hop_win = hop_grant
            else:
                dest_groups = defaultdict(list)
                dest_ok = {}
                for r in recs:
                    if r["key"] in matched:
                        continue
                    if not in_main(r):
                        continue
                    if r["dest_slot"] is None:
                        dest_ok[r["key"]] = r
                        continue
                    if r["dest_slot"] in taken_dest:
                        continue
                    if r["key"] in failed_dest[r["dest_slot"]]:
                        continue
                    dest_groups[r["dest_slot"]].append(r)
                for slot, cand in dest_groups.items():
                    win = self._islip_pick(
                        cand, self.islip_dest_ptr, (slot[0], slot[1]),
                        dest_key, "dest")
                    dest_grant[slot] = win
                    dest_ok[win["key"]] = win
                hop_groups = defaultdict(list)
                for r in dest_ok.values():
                    if not use_hop(r) or r["hop"] in taken_hop:
                        continue
                    hop_groups[r["hop"]].append(r)
                hop_win = {}
                for hop, cand in hop_groups.items():
                    hop_win[hop] = self._islip_pick(
                        cand, self.islip_hop_ptr, hop, hop_pick_key, "hop")

            accepted: set[tuple] = set()
            for r in dest_ok.values():
                if use_hop(r):
                    w = hop_win.get(r["hop"])
                    if w is None or w["key"] != r["key"]:
                        continue
                    taken_hop.add(r["hop"])
                    self._islip_advance(self.islip_hop_ptr, r["hop"], r)
                matched.add(r["key"])
                accepted.add(r["key"])
                if r["dest_slot"] is not None:
                    taken_dest.add(r["dest_slot"])
                    self._islip_advance(
                        self.islip_dest_ptr,
                        (r["dest_slot"][0], r["dest_slot"][1]), r)
            for slot, r in dest_grant.items():
                if r["key"] not in accepted:
                    failed_dest[slot].add(r["key"])
            for hop, r in hop_grant.items():
                if r["key"] not in accepted:
                    failed_hop[hop].add(r["key"])

        for r in recs:
            if r["key"] in matched:
                continue
            dest_hit = (r["dest_slot"] is not None
                        and r["dest_slot"] in taken_dest)
            hop_hit = use_hop(r) and r["hop"] in taken_hop
            if dest_hit:
                self.ej_hold.add((r["node"], r["src_pl"]))
                self.dest_hot[(r["dest_slot"][0], r["dest_slot"][1])] = self.t
            if hop_hit:
                hold_hop(r)

        leftover = [r for r in recs
                    if r["key"] not in matched
                    and (r["node"], r["src_pl"]) not in self.ej_hold
                    and (r["node"], r["src_pl"]) not in self.hop_hold]
        left = getattr(self.p, "hop_islip_left", "dest") or "dest"
        leftkeep = getattr(self.p, "hop_islip_leftkeep", "") or ""
        leftdest = getattr(self.p, "hop_islip_leftdest", "") or ""
        leftdest_resp = leftdest.endswith("_resp")
        leftdest_base = leftdest[:-5] if leftdest_resp else leftdest

        def left_hop_key(r: dict) -> tuple:
            if leftkeep == "short":
                return (r["hops"], r["age"], r["node"], r["src_pl"])
            if leftkeep == "long":
                return (-r["hops"], r["age"], r["node"], r["src_pl"])
            return age_key(r)

        def left_dest_key(r: dict) -> tuple:
            if leftdest_resp and r["kind"] != "resp":
                return age_key(r)
            if leftdest_base == "spread":
                return (r["hop_live"], r["age"], r["node"], r["src_pl"])
            if leftdest_base == "pack":
                return (-r["hop_live"], r["age"], r["node"], r["src_pl"])
            if leftdest_base == "free":
                return (0 if r.get("hop_free", True) else 1,
                        r["age"], r["node"], r["src_pl"])
            return age_key(r)
        if left == "hop":
            by_hop: dict[tuple, list] = defaultdict(list)
            free: list[dict] = []
            for r in leftover:
                if use_hop(r):
                    by_hop[r["hop"]].append(r)
                else:
                    free.append(r)
            keep: list[dict] = []
            for hop, cand in by_hop.items():
                win = self._islip_pick(
                    cand, self.islip_hop_ptr, hop, left_hop_key, "hop")
                keep.append(win)
                self._islip_advance(self.islip_hop_ptr, hop, win)
                for r in cand:
                    if r["key"] == win["key"]:
                        continue
                    hold_hop(r)
            keep.extend(free)
            by_dest: dict[tuple, list] = defaultdict(list)
            for r in keep:
                if (r["node"], r["src_pl"]) in self.hop_hold:
                    continue
                if r["dest_slot"] is not None:
                    by_dest[r["dest_slot"]].append(r)
            for slot, cand in by_dest.items():
                win = self._islip_pick(
                    cand, self.islip_dest_ptr, (slot[0], slot[1]),
                    left_dest_key, "dest")
                self._islip_advance(
                    self.islip_dest_ptr, (slot[0], slot[1]), win)
                if len(cand) <= 1:
                    continue
                for r in cand:
                    if r["key"] == win["key"]:
                        continue
                    self.ej_hold.add((r["node"], r["src_pl"]))
                    self.dest_hot[(slot[0], slot[1])] = self.t
        else:
            by_dest = defaultdict(list)
            free = []
            for r in leftover:
                if r["dest_slot"] is not None:
                    by_dest[r["dest_slot"]].append(r)
                else:
                    free.append(r)
            leftcommit = getattr(self.p, "hop_islip_leftcommit", "") or ""
            if leftcommit == "hop":
                dest_grant: dict[tuple, dict] = {}
                dest_cands: dict[tuple, list] = {}
                dest_ok: dict[tuple, dict] = {}
                for slot, cand in by_dest.items():
                    win = self._islip_pick(
                        cand, self.islip_dest_ptr, (slot[0], slot[1]),
                        left_dest_key, "dest")
                    dest_grant[slot] = win
                    dest_cands[slot] = cand
                    dest_ok[win["key"]] = win
                for r in free:
                    dest_ok[r["key"]] = r
                by_hop = defaultdict(list)
                hop_win: dict[tuple, dict] = {}
                for r in dest_ok.values():
                    if use_hop(r):
                        by_hop[r["hop"]].append(r)
                for hop, cand in by_hop.items():
                    hop_win[hop] = self._islip_pick(
                        cand, self.islip_hop_ptr, hop, left_hop_key, "hop")
                accepted: set[tuple] = set()
                for r in dest_ok.values():
                    if use_hop(r):
                        w = hop_win.get(r["hop"])
                        if w is None or w["key"] != r["key"]:
                            continue
                    accepted.add(r["key"])
                    if r["dest_slot"] is not None:
                        self._islip_advance(
                            self.islip_dest_ptr,
                            (r["dest_slot"][0], r["dest_slot"][1]), r)
                    if use_hop(r):
                        self._islip_advance(self.islip_hop_ptr, r["hop"], r)
                for slot, cand in dest_cands.items():
                    win = dest_grant[slot]
                    if win["key"] not in accepted:
                        continue
                    if len(cand) <= 1:
                        continue
                    for r in cand:
                        if r["key"] == win["key"]:
                            continue
                        self.ej_hold.add((r["node"], r["src_pl"]))
                        self.dest_hot[(slot[0], slot[1])] = self.t
                for hop, cand in by_hop.items():
                    w = hop_win[hop]
                    if w["key"] not in accepted:
                        continue
                    if len(cand) <= 1:
                        continue
                    for r in cand:
                        if r["key"] == w["key"]:
                            continue
                        hold_hop(r)
            else:
                keep = []
                for slot, cand in by_dest.items():
                    win = self._islip_pick(
                        cand, self.islip_dest_ptr, (slot[0], slot[1]),
                        left_dest_key, "dest")
                    keep.append(win)
                    self._islip_advance(
                        self.islip_dest_ptr, (slot[0], slot[1]), win)
                    for r in cand:
                        if r["key"] == win["key"]:
                            continue
                        self.ej_hold.add((r["node"], r["src_pl"]))
                        self.dest_hot[(slot[0], slot[1])] = self.t
                keep.extend(free)
                by_hop = defaultdict(list)
                for r in keep:
                    if (r["node"], r["src_pl"]) in self.ej_hold:
                        continue
                    if use_hop(r):
                        by_hop[r["hop"]].append(r)
                for hop, cand in by_hop.items():
                    win = self._islip_pick(
                        cand, self.islip_hop_ptr, hop, left_hop_key, "hop")
                    self._islip_advance(self.islip_hop_ptr, hop, win)
                    if len(cand) <= 1:
                        continue
                    for r in cand:
                        if r["key"] == win["key"]:
                            continue
                        hold_hop(r)

        retry = getattr(self.p, "ej_hold_retry", "") or ""
        if retry:
            taken_dest = {r["dest_slot"] for r in recs
                          if r["dest_slot"] is not None and (
                              r["dest_booked"]
                              or (r["node"], r["src_pl"]) not in self.ej_hold)}
            taken_hop = set(phys_busy)
            for r in recs:
                if (r["hop"] is not None
                        and (r["node"], r["src_pl"]) not in self.hop_hold
                        and (r["node"], r["src_pl"]) not in self.ej_hold):
                    taken_hop.add(r["hop"])
            self._ej_hold_retry_plane(recs, taken_dest, taken_hop, age_key)

        nxt: set[tuple] = set()
        if getattr(self.p, "hop_sticky", False):
            for r in recs:
                if (r["hop"] is not None
                        and (r["node"], r["src_pl"]) in self.hop_hold):
                    nxt.add((r["hop"], r["node"], r["src_pl"]))
        self.hop_sticky = nxt
        dnext: set[tuple] = set()
        if getattr(self.p, "dest_sticky", False):
            for r in recs:
                if (r["dest_slot"] is not None
                        and (r["node"], r["src_pl"]) in self.ej_hold):
                    dnext.add(((r["dest_slot"][0], r["dest_slot"][1]),
                               r["node"], r["src_pl"]))
        self.dest_sticky = dnext
        inj = getattr(self.p, "late_plane_inj", "") or ""
        if inj in ("match", "off"):
            for r in recs:
                if r["hop"] is None:
                    continue
                if ((r["node"], r["src_pl"]) in self.ej_hold
                        or (r["node"], r["src_pl"]) in self.hop_hold):
                    continue
                q = self.srcq.get(r["key"])
                if q:
                    q[0].plane = r["hop"][0]

    def _ej_hold_retry_plane(
            self, recs: list[dict], taken_dest: set[tuple],
            taken_hop: set[tuple], age_key) -> None:
        """Dest-held HOLs try the other plane after dest-then-hop.

        late_plane ran before dest grants, so a same-cycle dest_slot
        clash is invisible to occ pick. Default hop_islip (retry="")
        must stay bit-identical to S14.
        """
        mode = getattr(self.p, "ej_hold_retry", "") or ""
        cands: list[dict] = []
        for r in recs:
            if (r["node"], r["src_pl"]) not in self.ej_hold:
                continue
            if r["hop"] is None or r["dest_slot"] is None:
                continue
            if mode in ("plane_ha", "ha") and not is_ha(r["node"]):
                continue
            if mode in ("plane_core", "core") and not is_core(r["node"]):
                continue
            if mode in ("plane_resp", "resp") and r["kind"] != "resp":
                continue
            if mode in ("plane_req", "req") and r["kind"] != "req":
                continue
            q = self.srcq.get(r["key"])
            if not q:
                continue
            f = q[0]
            old = (f.plane, f.dir, f.target)
            pl, d, _idx = r["hop"][:3]
            alt = 1 - pl
            hops = r["hops"]
            f.plane, f.dir, f.target = alt, d, hops
            alt_slot = (f.dst, alt, self._ej_eta(f))
            alt_hop = (alt, d, f.idx, f.vc)
            dest_ok = not self._ej_slots_busy(f)
            hop_ok = self._can_board(*alt_hop)
            f.plane, f.dir, f.target = old
            if (dest_ok and hop_ok
                    and alt_slot not in taken_dest
                    and alt_hop not in taken_hop):
                cands.append({
                    **r, "alt_slot": alt_slot, "alt_hop": alt_hop,
                    "alt_route": (alt, d, hops),
                })
        cands.sort(key=age_key)
        for r in cands:
            if r["alt_slot"] in taken_dest or r["alt_hop"] in taken_hop:
                continue
            taken_dest.add(r["alt_slot"])
            taken_hop.add(r["alt_hop"])
            self.ej_hold.discard((r["node"], r["src_pl"]))
            self.hop_hold.discard((r["node"], r["src_pl"]))
            self.hop_grant[(r["node"], r["src_pl"])] = r["alt_route"]

    def _inject_keys(self) -> list:
        keys = list(self.active_src)
        mode = getattr(self.p, "inj_order", "") or ""
        if not mode:
            return keys

        def keyfn(k):
            q = self.srcq.get(k)
            f = q[0] if q else None
            age = f.t_gen if f is not None else 10**18
            node, plane = k
            if mode == "young":
                return (-age, node, plane)
            if mode == "node":
                return (node, plane)
            if mode == "oldest_resp":
                kind = 0 if f is not None and f.kind == "resp" else 1
                return (kind, age, node, plane)
            return (age, node, plane)

        keys.sort(key=keyfn)
        return keys

    def _pre_inject(self) -> None:
        self.ej_hold.clear()
        if self.t % 64 == 0 and (self.ej_book or self.ej_owner or self.hop_rsv):
            cut = self.t
            self.ej_book = {k: v for k, v in self.ej_book.items()
                            if k[2] >= cut}
            self.ej_owner = {k: v for k, v in self.ej_owner.items()
                             if k[2] >= cut}
            self.hop_rsv = {k: v for k, v in self.hop_rsv.items()
                            if k[3] >= cut}
        if getattr(self.p, "hop_islip", 0):
            self._build_hop_islip()
            return
        if getattr(self.p, "hop_joint", False):
            self._build_hop_joint()
            return
        if not getattr(self.p, "ej_lock", False):
            return
        groups: dict[tuple, list] = defaultdict(list)
        for key in list(self.active_src):
            node, plane = key
            q = self.srcq[key]
            if not q:
                continue
            f = q[0]
            if (f.kind and self._ej_applies(f)
                    and super()._may_inject(node, plane, f)):
                eta = self._ej_eta(f)
                groups[(f.dst, f.plane, eta)].append((node, plane, f))
        for slot, cand in groups.items():
            booked = self.ej_book.get(slot, 0)
            owner = self.ej_owner.get(slot)
            if (getattr(self.p, "resp_train", False) and owner is not None
                    and any(c[2].txn_id == owner for c in cand)):
                booked = 0
            keep = max(0, 1 - booked)
            if booked > 0:
                self.dest_hot[(slot[0], slot[1])] = self.t
            keep_mode = getattr(self.p, "ej_keep", "node")
            if keep_mode == "oldest":
                cand.sort(key=lambda x: (x[2].t_gen, x[0]))
            else:
                cand.sort(key=lambda x: x[0])
            for node, plane, _f in cand[keep:]:
                self.ej_hold.add((node, plane))
        if getattr(self.p, "nbr_adv", False):
            self.nbr_age = {}
            for key in list(self.active_src):
                node, plane = key
                q = self.srcq.get(key)
                if not q or (node, plane) in self.ej_hold:
                    continue
                g = q[0]
                if g.dir is None:
                    continue
                if self._can_board(g.plane, g.dir, g.idx, g.vc):
                    self.nbr_age[(node, g.plane, g.dir)] = g.t_gen
        if getattr(self.p, "hop_hold", False):
            self._build_hop_hold()

    def _ej_visible(self, slot: tuple, delta: int = 1) -> None:
        self.ej_book[slot] = self.ej_book.get(slot, 0) + delta
        if self.ej_book[slot] <= 0:
            self.ej_book.pop(slot, None)
            self.ej_owner.pop(slot, None)

    def _ej_post(self, pid: int, slot: tuple, delay: int | None = None) -> None:
        if delay is None:
            delay = getattr(self.p, "ej_delay", 0)
        self.ej_vis_at[pid] = self.t + max(0, delay)
        if delay <= 0:
            self._ej_visible(slot, 1)
            return
        self.ej_at[self.t + delay].append((pid, slot))

    def _ej_drop(self, pid: int, eta: int, f: Flit) -> None:
        slot = (f.dst, f.plane, eta)
        vis = self.ej_vis_at.get(pid, self.t)
        if vis > self.t:
            self.ej_cancel.add((pid, slot))
        else:
            self._ej_visible(slot, -1)

    def _on_inring_block(self, f: Flit) -> None:
        if not (getattr(self.p, "ej_rebook", False)
                and getattr(self.p, "ej_lock", False)
                and self._ej_applies(f)):
            return
        old = self.ej_eta_of.get(f.pid)
        if old is None:
            return
        visible = self.ej_vis_at.get(f.pid, self.t) <= self.t
        self._ej_drop(f.pid, old, f)
        new = old + 1
        self.ej_eta_of[f.pid] = new
        # keep the slot visible if the old one already was; don't re-hide it
        self._ej_post(f.pid, (f.dst, f.plane, new),
                      delay=0 if visible else None)

    def _ctrl_deliver(self) -> None:
        super()._ctrl_deliver()
        for pid, slot in self.ej_at.pop(self.t, []):
            if (pid, slot) in self.ej_cancel:
                self.ej_cancel.discard((pid, slot))
                continue
            self._ej_visible(slot, 1)
        if not getattr(self.p, "arc_lock", ""):
            return
        for rec in self.ctrl_at.pop(self.t, []):
            node, plane, d, block_t, ttl = rec
            self.arc_block[(node, plane, d)].add(block_t)
            if ttl <= 1:
                continue
            nxt = (node + d) % self.n
            self.ctrl_at[self.t + 1].append(
                (nxt, plane, d, block_t + self.topo.hop_lat_from(node, d),
                 ttl - 1))
        # drop past blocks so the sets stay small
        if self.t % 64 == 0:
            cut = self.t
            for key, times in list(self.arc_block.items()):
                if times:
                    self.arc_block[key] = {x for x in times if x >= cut}

    def _launch(self, f: Flit, *, inring: bool) -> bool:
        if inring:
            self._release_hop0(f)
        node, plane, d = f.idx, f.plane, f.dir
        ok = super()._launch(f, inring=inring)
        if ok and getattr(self.p, "hop_tab", False):
            self.hop_at[(plane, d, node, f.vc)].add(self.t)
            if f.target > 0:
                self.hop_next[(plane, d, f.idx, f.vc)].add(
                    self.t + self.topo.hop_lat_from(node, d))
            if self.t % 64 == 0:
                cut = self.t
                for key, times in list(self.hop_at.items()):
                    if times:
                        self.hop_at[key] = {x for x in times if x >= cut}
                for key, times in list(self.hop_next.items()):
                    if times:
                        self.hop_next[key] = {x for x in times if x >= cut}
        return ok

    def _reserve_arc(self, f: Flit) -> None:
        mode = getattr(self.p, "arc_lock", "") or ""
        if not mode or f.target <= 1:
            return
        n, d, p = self.n, f.dir, f.plane
        if mode == "neighbor":
            nxt = (f.idx + d) % n
            self.ctrl_at[self.t + 1].append(
                (nxt, p, d, self.t + self.topo.hop_lat_from(f.idx, d), 1))
            return
        if mode == "instant":
            node = f.idx
            tau = self.t
            for _ in range(1, f.target):
                tau += self.topo.hop_lat_from(node, d)
                node = (node + d) % n
                self.arc_block[(node, p, d)].add(tau)
            return
        if mode == "ctrl1":
            nxt = (f.idx + d) % n
            self.ctrl_at[self.t + 1].append(
                (nxt, p, d, self.t + self.topo.hop_lat_from(f.idx, d),
                 f.target - 1))

    def _release_hop0(self, f: Flit) -> None:
        key = self.hop0_of.pop(f.pid, None)
        if key is not None:
            self.hop0[key] = max(0, self.hop0[key] - 1)

    def _on_inject(self, f: Flit) -> None:
        super()._on_inject(f)
        if f.dir is not None:
            key = (f.plane, f.dir, f.idx, f.vc)
            self.hop0[key] += 1
            self.hop0_of[f.pid] = key
        if getattr(self.p, "late_plane", "") == "injlive":
            self.inj_live[f.plane] += 1
        if getattr(self.p, "dest_old", "") and f.dir is not None:
            self.dest_inf[(f.dst, f.plane, f.dir)].append(f.t_gen)
        self._reserve_arc(f)
        if getattr(self.p, "ej_lock", False) and self._ej_applies(f):
            eta = self._ej_eta(f)
            self.ej_eta_of[f.pid] = eta
            if getattr(self.p, "resp_train", False) and f.kind == "resp":
                if f.seq == 0:
                    extra_free = all(
                        not self._ej_slot_foreign(f, eta + i)
                        for i in range(1, max(1, f.nflit)))
                    nbook = max(1, f.nflit) if extra_free else 1
                    for i in range(nbook):
                        slot = (f.dst, f.plane, eta + i)
                        self._ej_post(f.pid, slot)
                        if nbook > 1:
                            self.ej_owner[slot] = f.txn_id
                elif self.ej_owner.get((f.dst, f.plane, eta)) != f.txn_id:
                    self._ej_post(f.pid, (f.dst, f.plane, eta))
            else:
                slots = (self._ej_slots(f) if getattr(self.p, "ej_hot_book", True)
                         else [eta])
                for e in slots:
                    self._ej_post(f.pid, (f.dst, f.plane, e))
        nbook = getattr(self.p, "hop_book", 0)
        if nbook > 0 and f.dir is not None:
            for slot in self._hop_slots(f, nbook):
                self.hop_rsv[slot] = self.hop_rsv.get(slot, 0) + 1
        self.dir_starve[(f.src, f.plane, f.dir)] = 0
        if f.kind == "resp" and is_ha(f.src):
            self.last_resp_dest[(f.src, f.dst)] = self.t
            self.last_resp_ha[f.src] = self.t
        if getattr(self.p, "cut_credit", 0) > 0:
            gi = self._crosses_gap(f)
            if gi is not None:
                key = (f.plane, gi, f.dir)
                self.cut_in[key] += 1
                a, b = self.cut_gaps[gi]
                u = f.idx
                acc = 0
                for _ in range(max(0, f.target)):
                    acc += self.topo.hop_lat_from(u, f.dir)
                    v = (u + f.dir) % self.n
                    if {u, v} == {a, b}:
                        self.cut_free_at[self.t + acc].append(key)
                        break
                    u = v
        if f.kind == "req" and is_core(f.src):
            self.last_core_prog[f.src] = self.t
        if getattr(self.p, "dest_credit", 0) > 0:
            self.dest_used[f.dst] += 1
        if f.kind != "req" or getattr(self.p, "ha_outst", 0) <= 0:
            return
        txn = self.txn_by_id[f.txn_id]
        self.ha_used[(txn.core, txn.ha)] += 1

    def _aimd_tick(self) -> None:
        super()._aimd_tick()
        for key in self.cut_free_at.pop(self.t, []):
            self.cut_in[key] = max(0, self.cut_in[key] - 1)
        n = self.topo.n
        if getattr(self.p, "cut_tok", 0) > 0:
            for (plane, gi, d), pos in self.cut_pos.items():
                self.cut_pos[(plane, gi, d)] = [
                    ((x + d) % n + n) % n for x in pos]
        if getattr(self.p, "circ_tokens", 0) <= 0:
            return
        for (plane, d), pos in self.tokens.items():
            self.tokens[(plane, d)] = [((x + d) % n + n) % n for x in pos]

    def _on_arrive_station(self, f: Flit) -> None:
        self._release_hop0(f)
        super()._on_arrive_station(f)

    def _on_board_fail(self, node: int, f: Flit) -> None:
        super()._on_board_fail(node, f)
        hop = f.dir is not None and not self._can_board(f.plane, f.dir, f.idx, f.vc)
        if hop:
            self.st["n_deny_hop"] += 1
            self.st["n_deny_hop_resp" if f.kind == "resp" else "n_deny_hop_req"] += 1
            self.st["n_deny_hop_ha" if is_ha(node) else "n_deny_hop_core"] += 1
        if self._itag_blocks(f, node):
            self.st["n_deny_itag"] += 1

    def _on_pe_drain(self, f: Flit) -> None:
        if getattr(self.p, "late_plane", "") == "injlive":
            self.inj_live[f.plane] = max(0, self.inj_live[f.plane] - 1)
        if getattr(self.p, "dest_old", "") and f.dir is not None:
            ages = self.dest_inf.get((f.dst, f.plane, f.dir))
            if ages:
                try:
                    ages.remove(f.t_gen)
                except ValueError:
                    pass
        super()._on_pe_drain(f)
        if f.kind == "req":
            self.last_req_drain[f.dst] = self.t
        elif f.kind == "resp" and is_core(f.dst):
            self.last_core_prog[f.dst] = self.t
            self.last_core_recv[f.dst] = self.t
            self.core_recv_n[f.dst] += 1
        if getattr(self.p, "dest_credit", 0) > 0:
            self.dest_used[f.dst] = max(0, self.dest_used[f.dst] - 1)
        if (f.kind == "resp" and getattr(self.p, "ha_outst", 0) > 0
                and self.resp_left.get(f.txn_id, 0) == 0):
            txn = self.txn_by_id[f.txn_id]
            key = (txn.core, txn.ha)
            self.ha_used[key] = max(0, self.ha_used[key] - 1)

    def summary(self) -> dict[str, Any]:
        out = super().summary()
        out["dist"] = True
        out["resp_bypass_itag"] = getattr(self.p, "resp_bypass_itag", False)
        out["no_req_itag"] = getattr(self.p, "no_req_itag", False)
        out["leave_useful"] = getattr(self.p, "leave_useful", False)
        out["ha_outst"] = getattr(self.p, "ha_outst", 0)
        out["req_slot"] = getattr(self.p, "req_slot", 0)
        out["hol_bypass"] = getattr(self.p, "hol_bypass", False)
        out["lqf"] = getattr(self.p, "lqf", False)
        out["dest_voq"] = getattr(self.p, "dest_voq", False)
        out["dest_credit"] = getattr(self.p, "dest_credit", 0)
        out["kind_req"] = getattr(self.p, "kind_req", 0)
        out["kind_resp"] = getattr(self.p, "kind_resp", 0)
        out["circ_tokens"] = getattr(self.p, "circ_tokens", 0)
        out["resp_idle"] = getattr(self.p, "resp_idle", 0)
        out["arc_lock"] = getattr(self.p, "arc_lock", "")
        out["ej_lock"] = getattr(self.p, "ej_lock", False)
        out["ej_scope"] = getattr(self.p, "ej_scope", "both")
        out["ej_keep"] = getattr(self.p, "ej_keep", "node")
        out["ej_rebook"] = getattr(self.p, "ej_rebook", False)
        out["ej_delay"] = getattr(self.p, "ej_delay", 0)
        out["hop_peek"] = getattr(self.p, "hop_peek", False)
        out["nbr2"] = getattr(self.p, "nbr2", False)
        out["resp_train"] = getattr(self.p, "resp_train", False)
        out["hop_bounce"] = getattr(self.p, "hop_bounce", False)
        out["hop_bounce_age"] = getattr(self.p, "hop_bounce_age", "")
        out["hop_book"] = getattr(self.p, "hop_book", 0)
        out["late_plane"] = getattr(self.p, "late_plane", "")
        out["late_plane_sib"] = getattr(self.p, "late_plane_sib", "")
        out["late_plane_inj"] = getattr(self.p, "late_plane_inj", "")
        out["hop_yield"] = getattr(self.p, "hop_yield", False)
        out["hop_cred"] = getattr(self.p, "hop_cred", 0)
        out["hop0_cred"] = getattr(self.p, "hop0_cred", 0)
        out["dest_old"] = getattr(self.p, "dest_old", "")
        out["nbr_adv"] = getattr(self.p, "nbr_adv", False)
        out["late_dir"] = getattr(self.p, "late_dir", "")
        out["late_dir_slack"] = getattr(self.p, "late_dir_slack", 2)
        out["late_dir_kind"] = getattr(self.p, "late_dir_kind", "both")
        out["late_dir_hold"] = getattr(self.p, "late_dir_hold", False)
        out["late_dir_dest"] = getattr(self.p, "late_dir_dest", "")
        out["late_dir_eager"] = getattr(self.p, "late_dir_eager", False)
        out["hop_hold"] = getattr(self.p, "hop_hold", False)
        out["hop_hold_keep"] = getattr(self.p, "hop_hold_keep", "oldest")
        out["hop_hold_retry"] = getattr(self.p, "hop_hold_retry", "")
        out["hop_hold_late"] = getattr(self.p, "hop_hold_late", False)
        out["ej_hold_retry"] = getattr(self.p, "ej_hold_retry", "")
        out["hop_joint"] = getattr(self.p, "hop_joint", False)
        out["hop_islip"] = getattr(self.p, "hop_islip", 0)
        out["hop_islip_arb"] = getattr(self.p, "hop_islip_arb", "oldest")
        out["hop_islip_order"] = getattr(self.p, "hop_islip_order", "dest")
        out["hop_islip_left"] = getattr(self.p, "hop_islip_left", "dest")
        out["hop_islip_peek"] = getattr(self.p, "hop_islip_peek", "")
        out["hop_islip_pack"] = getattr(self.p, "hop_islip_pack", "")
        out["hop_islip_mutual"] = getattr(self.p, "hop_islip_mutual", False)
        out["hop_islip_split"] = getattr(self.p, "hop_islip_split", "")
        out["hop_islip_hopkeep"] = getattr(self.p, "hop_islip_hopkeep", "")
        out["hop_islip_destkeep"] = getattr(self.p, "hop_islip_destkeep", "")
        out["hop_islip_leftkeep"] = getattr(self.p, "hop_islip_leftkeep", "")
        out["hop_islip_busy"] = getattr(self.p, "hop_islip_busy", "")
        out["hop_islip_leftdest"] = getattr(self.p, "hop_islip_leftdest", "")
        out["hop_islip_leftcommit"] = getattr(self.p, "hop_islip_leftcommit", "")
        out["hop_islip_match"] = getattr(self.p, "hop_islip_match", "")
        out["hop_sticky"] = getattr(self.p, "hop_sticky", False)
        out["dest_sticky"] = getattr(self.p, "dest_sticky", False)
        out["inj_order"] = getattr(self.p, "inj_order", "")
        out["inj_skip_hold"] = getattr(self.p, "inj_skip_hold", "")
        return out


def s5_params(**kw) -> Ring2DistParams:
    """Leave-slot reservation; node-id dest clash; no kind-aware leave."""
    kw.setdefault("leave_useful", False)
    kw.setdefault("ej_lock", True)
    kw.setdefault("ej_keep", "node")
    return Ring2DistParams(**kw)


def s6_params(**kw) -> Ring2DistParams:
    """S5 + oldest-first among same-cycle dest-leave candidates."""
    kw.setdefault("leave_useful", False)
    kw.setdefault("ej_lock", True)
    kw.setdefault("ej_keep", "oldest")
    return Ring2DistParams(**kw)


def s7_params(**kw) -> Ring2DistParams:
    """S6 + hop_bounce: late-bind to the other plane if first hop is busy."""
    kw.setdefault("hop_bounce", True)
    return s6_params(**kw)


def s8_params(**kw) -> Ring2DistParams:
    """S7 + always late-bind plane by hop+dest, occupancy tie-break."""
    kw.setdefault("late_plane", "occ")
    return s7_params(**kw)


def s9_params(**kw) -> Ring2DistParams:
    """S8 + late_dir slack: other ring dir if first hop busy, ≤+2 hops."""
    kw.setdefault("late_dir", "slack")
    return s8_params(**kw)


def s10_params(**kw) -> Ring2DistParams:
    """S9 + late_dir only on responses (requests stay on the shortest dir)."""
    kw.setdefault("late_dir_kind", "resp")
    return s9_params(**kw)


def s11_params(**kw) -> Ring2DistParams:
    """S10 + same-cycle first-hop mutex, responses only (oldest keeps hop)."""
    kw.setdefault("hop_hold", True)
    kw.setdefault("hop_hold_kind", "resp")
    return s10_params(**kw)


def s12_params(**kw) -> Ring2DistParams:
    """S11 + one dest-then-hop request-grant wave (leftover dest re-grant)."""
    kw.setdefault("hop_islip", 1)
    return s11_params(**kw)


def s13_params(**kw) -> Ring2DistParams:
    """S12 + hop-grant prefers shorter remaining path among dest-granted."""
    kw.setdefault("hop_islip_hopkeep", "short")
    return s12_params(**kw)


def s14_params(**kw) -> Ring2DistParams:
    """S13 + HA sibling plane yield on same-node first-hop clash."""
    kw.setdefault("late_plane_sib", "ha")
    return s13_params(**kw)


def run_batch(topo: Ring2Topology, txns: Sequence[Txn], *,
              params: Ring2DistParams | Ring2BaseParams | None = None,
              t_max: int = 2_000_000, seed: int = 0) -> dict[str, Any]:
    p = params or Ring2DistParams()
    sim = Ring2DistSim(topo, p, seed=seed)
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
    out["hop_starts"] = sim.hop_starts
    return out


def _silence_unused() -> None:
    _ = is_ha
