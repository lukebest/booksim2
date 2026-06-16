// Calendar collective simulation - calendar scheduler

#include "calendar_scheduler.hpp"
#include <algorithm>
#include <iostream>
#include <queue>
#include <tuple>
#include <unordered_map>
#include <unordered_set>

using namespace std;

namespace {
// One in-network forwarding event: flit k of source s traverses parent->child.
struct RTEvent {
  int ready;
  long long seq;
  int s, parent, child, k;
};
struct RTEventGreater {
  bool operator()(const RTEvent & a, const RTEvent & b) const {
    if(a.ready != b.ready) return a.ready > b.ready;
    return a.seq > b.seq;
  }
};
}  // namespace

CalendarScheduler::CalendarScheduler(const MeshGraph & graph)
  : _graph(graph)
{
}

vector<int> CalendarScheduler::PathLinkIds(const vector<int> & path) const
{
  vector<int> links;
  if(path.size() < 2) return links;
  links.push_back(_graph.UpLinkId(path[0]));
  for(size_t i = 0; i + 1 < path.size(); ++i) {
    int lid = _graph.LinkId(path[i], path[i+1]);
    if(lid >= 0) links.push_back(lid);
  }
  links.push_back(_graph.DownLinkId(path[path.size()-1]));
  return links;
}

bool CalendarScheduler::LinkFree(const map<int, vector<int> > & occupancy,
                                   int link_id, int send_time) const
{
  if(!_graph.IsLinkAlive(link_id)) return false;
  map<int, vector<int> >::const_iterator it = occupancy.find(link_id);
  if(it == occupancy.end()) return true;
  const vector<int> & occ = it->second;
  for(size_t i = 0; i < occ.size(); ++i)
    if(occ[i] == send_time) return false;
  return true;
}

void CalendarScheduler::OccupyLink(map<int, vector<int> > & occupancy,
                                     int link_id, int send_time) const
{
  vector<int> & occ = occupancy[link_id];
  occ.push_back(send_time);
  sort(occ.begin(), occ.end());
}

int CalendarScheduler::ReserveLink(map<int, vector<int> > & occupancy,
                                   int link_id, int start, int latency) const
{
  if(!_graph.IsLinkAlive(link_id)) return -1;
  vector<int> & occ = occupancy[link_id];
  int t = start;
  while(true) {
    bool clash = false;
    for(size_t i = 0; i < occ.size(); ++i) {
      if(occ[i] == t) { clash = true; break; }
    }
    if(!clash) break;
    ++t;
  }
  occ.push_back(t);
  sort(occ.begin(), occ.end());
  return t + latency;
}

bool CalendarScheduler::TryPlaceBackward(const vector<int> & path,
                                           bool use_up, bool use_down,
                                           int finish_time,
                                           const map<int, vector<int> > & occupancy,
                                           vector<ScheduledHop> & hops) const
{
  hops.clear();
  if(path.size() < 2) return false;

  int t = finish_time;

  if(use_down) {
    int down_id = _graph.DownLinkId(path[path.size()-1]);
    int lat = _graph.LinkLatency(down_id);
    int send = t - lat;
    if(send < 0 || !LinkFree(occupancy, down_id, send)) return false;
    ScheduledHop dh;
    dh.link_id = down_id;
    dh.send_time = send;
    dh.finish_time = t;
    hops.insert(hops.begin(), dh);
    t = send;
  }

  for(int h = (int)path.size() - 2; h >= 0; --h) {
    int lid = _graph.LinkId(path[h], path[h+1]);
    int lat = _graph.LinkLatency(lid);
    int send = t - lat;
    if(send < 0 || !LinkFree(occupancy, lid, send)) return false;
    ScheduledHop mh;
    mh.link_id = lid;
    mh.send_time = send;
    mh.finish_time = t;
    hops.insert(hops.begin(), mh);
    t = send;
  }

  if(use_up) {
    int up_id = _graph.UpLinkId(path[0]);
    int lat = _graph.LinkLatency(up_id);
    int send = t - lat;
    if(send < 0 || !LinkFree(occupancy, up_id, send)) return false;
    ScheduledHop uh;
    uh.link_id = up_id;
    uh.send_time = send;
    uh.finish_time = t;
    hops.insert(hops.begin(), uh);
  }

  return true;
}

void CalendarScheduler::CommitHops(map<int, vector<int> > & occupancy,
                                     const vector<ScheduledHop> & hops) const
{
  for(size_t i = 0; i < hops.size(); ++i)
    OccupyLink(occupancy, hops[i].link_id, hops[i].send_time);
}

CalendarResult CalendarScheduler::ScheduleGatherGlobal(
    vector<ScheduledTransfer> & transfers, int msg_size) const
{
  CalendarResult result;
  result.feasible = true;
  result.makespan = 0;
  result.theo_bound = _graph.TheoBound("gather", msg_size);
  int alive = 0;
  for(int i = 0; i < _graph.NumNodes(); ++i)
    if(_graph.IsAlive(i)) ++alive;
  result.period = max(1, alive - 1) * msg_size;

  if(transfers.empty()) {
    result.feasible = false;
    return result;
  }

  struct GatherItem {
    size_t idx;
    int path_lat;
    int src;
    int flit_idx;
  };

  vector<GatherItem> items;
  items.reserve(transfers.size());
  for(size_t i = 0; i < transfers.size(); ++i) {
    const ScheduledTransfer & tr = transfers[i];
    int plat = _graph.PathLatency(tr.src, tr.dst);
    GatherItem gi;
    gi.idx = i;
    gi.path_lat = plat;
    gi.src = tr.src;
    gi.flit_idx = tr.flit_idx;
    items.push_back(gi);
  }

  sort(items.begin(), items.end(),
       [](const GatherItem & a, const GatherItem & b) {
         if(a.path_lat != b.path_lat) return a.path_lat < b.path_lat;
         if(a.src != b.src) return a.src < b.src;
         return a.flit_idx < b.flit_idx;
       });

  vector<int> target_finish(items.size());
  for(size_t i = 0; i < items.size(); ++i) {
    if(i == 0)
      target_finish[i] = items[i].path_lat;
    else
      target_finish[i] = max(items[i].path_lat, target_finish[i-1] + 1);
  }

  map<int, vector<int> > occupancy;
  vector<ScheduledTransfer> scheduled(transfers.size());

  for(size_t ord = 0; ord < items.size(); ++ord) {
    size_t idx = items[ord].idx;
    ScheduledTransfer & tr = transfers[idx];
    vector<int> path = _graph.ShortestPath(tr.src, tr.dst);
    if(path.size() < 2) {
      result.feasible = false;
      continue;
    }

    vector<ScheduledHop> hops;
    while(true) {
      if(TryPlaceBackward(path, tr.use_up_ramp, tr.use_down_ramp,
                          target_finish[ord], occupancy, hops))
        break;
      ++target_finish[ord];
      for(size_t j = ord + 1; j < items.size(); ++j)
        target_finish[j] = max(target_finish[j], target_finish[j-1] + 1);
    }

    CommitHops(occupancy, hops);
    tr.hops = hops;
    tr.finish_time = target_finish[ord];
    if(!hops.empty()) tr.start_time = hops.front().send_time;
    scheduled[idx] = tr;
  }

  result.transfers.clear();
  for(size_t ord = 0; ord < items.size(); ++ord)
    result.transfers.push_back(scheduled[items[ord].idx]);

  for(size_t i = 0; i < result.transfers.size(); ++i)
    if(result.transfers[i].finish_time > result.makespan)
      result.makespan = result.transfers[i].finish_time;

  for(map<int, vector<int> >::const_iterator it = occupancy.begin();
      it != occupancy.end(); ++it)
    result.link_peak_occupancy[it->first] = (int)it->second.size();

  if(result.transfers.empty()) result.feasible = false;

  if(result.theo_bound > 0)
    result.efficiency = (double)result.theo_bound / (double)max(1, result.makespan);
  else
    result.efficiency = 0.0;

  return result;
}

CalendarResult CalendarScheduler::ScheduleAllGatherRingTree(int msg_size) const
{
  // Ring-tree hybrid allgather on a healthy mesh: every source broadcasts via
  // an X-then-Y dimension-ordered multicast tree (row spine + column branches).
  // Forwarding is IN-NETWORK (router fork): a node duplicates an arriving flit,
  // ejecting one copy to its PE (down-ramp) and forwarding one copy onward.
  // Intermediate nodes never eject-then-reinject, so no PE/SRAM bounce is paid.
  // A global link-time calendar reserves each (directed-link, cycle) and
  // (node down-ramp, cycle) to <=1 flit, so the schedule is conflict-free by
  // construction; the greedy earliest-free packing reaches the makespan lower
  // bound exactly (verified against utils/sim_ring_tree.py).
  CalendarResult result;
  result.feasible = true;
  result.makespan = 0;
  result.theo_bound = _graph.TheoBound("allgather", msg_size);

  const int N = _graph.NumNodes();
  const int MX = _graph.MeshX();
  const int MY = _graph.MeshY();
  const int ramp = _graph.RampLatency();

  int alive = 0;
  for(int i = 0; i < N; ++i) if(_graph.IsAlive(i)) ++alive;
  result.period = max(1, alive - 1) * msg_size;

  // Per-source tree children: child[s][node] -> nodes it forwards to.
  vector<vector<vector<int> > > child(N, vector<vector<int> >(N));
  for(int s = 0; s < N; ++s) {
    int sx = s % MX, sy = s / MX;
    for(int x = sx + 1; x < MX; ++x) child[s][(x - 1) + MX * sy].push_back(x + MX * sy);
    for(int x = sx - 1; x >= 0; --x) child[s][(x + 1) + MX * sy].push_back(x + MX * sy);
    for(int x = 0; x < MX; ++x) {
      for(int y = sy + 1; y < MY; ++y) child[s][x + MX * (y - 1)].push_back(x + MX * y);
      for(int y = sy - 1; y >= 0; --y) child[s][x + MX * (y + 1)].push_back(x + MX * y);
    }
  }

  unordered_map<long long, unordered_set<int> > link_busy;
  unordered_map<int, unordered_set<int> > down_busy;
  unordered_map<long long, int> avail;

  // earliest free cycle >= e on a calendar slot-set (gaps are tiny once packed)
  struct Reserver {
    int operator()(unordered_set<int> & s, int e) const {
      int t = e;
      while(s.count(t)) ++t;
      s.insert(t);
      return t;
    }
  } reserve;

  priority_queue<RTEvent, vector<RTEvent>, RTEventGreater> pq;
  long long seq = 0;
  for(int s = 0; s < N; ++s) {
    for(int k = 0; k < msg_size; ++k) {
      long long key = ((long long)s * N + s) * msg_size + k;
      avail[key] = ramp + k;
      const vector<int> & cs = child[s][s];
      for(size_t i = 0; i < cs.size(); ++i) {
        RTEvent ev; ev.ready = ramp + k; ev.seq = seq++;
        ev.s = s; ev.parent = s; ev.child = cs[i]; ev.k = k;
        pq.push(ev);
      }
    }
  }

  while(!pq.empty()) {
    RTEvent ev = pq.top(); pq.pop();
    long long pkey = ((long long)ev.s * N + ev.parent) * msg_size + ev.k;
    int t_avail = avail[pkey];
    int lat = _graph.Latency(ev.parent, ev.child);
    long long lk = (long long)ev.parent * N + ev.child;
    int send = reserve(link_busy[lk], max(ev.ready, t_avail));
    int arrive = send + lat;
    long long ckey = ((long long)ev.s * N + ev.child) * msg_size + ev.k;
    avail[ckey] = arrive;
    int eject = reserve(down_busy[ev.child], arrive);
    int done = eject + ramp;
    if(done > result.makespan) result.makespan = done;
    const vector<int> & gcs = child[ev.s][ev.child];
    for(size_t i = 0; i < gcs.size(); ++i) {
      RTEvent nx; nx.ready = arrive; nx.seq = seq++;
      nx.s = ev.s; nx.parent = ev.child; nx.child = gcs[i]; nx.k = ev.k;
      pq.push(nx);
    }
  }

  // Verify allgather correctness: every node ejected exactly (alive-1)*M flits.
  int expect = (alive - 1) * msg_size;
  for(int n = 0; n < N; ++n) {
    if(!_graph.IsAlive(n)) continue;
    if((int)down_busy[n].size() != expect) { result.feasible = false; break; }
  }
  for(unordered_map<long long, unordered_set<int> >::const_iterator it = link_busy.begin();
      it != link_busy.end(); ++it)
    result.link_peak_occupancy[(int)(it->first % 1000000)] = (int)it->second.size();

  if(result.theo_bound > 0)
    result.efficiency = (double)result.theo_bound / (double)max(1, result.makespan);
  else
    result.efficiency = 0.0;
  return result;
}

static int TheoPeriod(const MeshGraph & graph, const string & name, int msg_size)
{
  int alive = 0;
  for(int i = 0; i < graph.NumNodes(); ++i)
    if(graph.IsAlive(i)) ++alive;
  if(name == "broadcast" || name == "reduce" || name == "allreduce")
    return msg_size;
  if(name == "gather" || name == "allgather")
    return max(1, alive - 1) * msg_size;
  if(name == "alltoall" || name == "anytoany")
    return graph.TheoBound(name, msg_size);
  return msg_size;
}

CalendarResult CalendarScheduler::ScheduleTransfers(vector<ScheduledTransfer> & transfers,
                                                    const string & collective_name,
                                                    int msg_size) const
{
  CalendarResult result;
  result.feasible = true;
  result.makespan = 0;
  result.theo_bound = _graph.TheoBound(collective_name, msg_size);
  result.period = TheoPeriod(_graph, collective_name, msg_size);

  map<int, vector<int> > occupancy;
  map<pair<int,int>, int> node_flit_ready;

  sort(transfers.begin(), transfers.end(),
       [](const ScheduledTransfer & a, const ScheduledTransfer & b) {
         if(a.start_time != b.start_time) return a.start_time < b.start_time;
         if(a.src != b.src) return a.src < b.src;
         if(a.dst != b.dst) return a.dst < b.dst;
         return a.flit_idx < b.flit_idx;
       });

  for(size_t i = 0; i < transfers.size(); ++i) {
    ScheduledTransfer & tr = transfers[i];
    vector<int> path = _graph.ShortestPath(tr.src, tr.dst);
    if(path.size() < 2) {
      result.feasible = false;
      continue;
    }

    int t = tr.start_time;
    pair<int,int> src_key(tr.src, tr.flit_idx);
    if(node_flit_ready.count(src_key))
      t = max(t, node_flit_ready[src_key]);

    tr.hops.clear();

    if(tr.use_up_ramp) {
      int up_id = _graph.UpLinkId(tr.src);
      int up_start = t;
      int up_finish = ReserveLink(occupancy, up_id, t, _graph.LinkLatency(up_id));
      if(up_finish < 0) { result.feasible = false; continue; }
      ScheduledHop hop;
      hop.link_id = up_id;
      hop.send_time = up_start;
      hop.finish_time = up_finish;
      tr.hops.push_back(hop);
      t = up_finish;
      node_flit_ready[src_key] = t;
    }

    for(size_t h = 0; h + 1 < path.size(); ++h) {
      int lid = _graph.LinkId(path[h], path[h+1]);
      int send_t = t;
      int finish = ReserveLink(occupancy, lid, t, _graph.LinkLatency(lid));
      if(finish < 0) { result.feasible = false; break; }
      ScheduledHop mh;
      mh.link_id = lid;
      mh.send_time = send_t;
      mh.finish_time = finish;
      tr.hops.push_back(mh);
      t = finish;
    }

    pair<int,int> dst_key(tr.dst, tr.flit_idx);
    node_flit_ready[dst_key] = max(node_flit_ready[dst_key], t);

    if(tr.use_down_ramp) {
      int down_id = _graph.DownLinkId(tr.dst);
      int down_send = t;
      int down_finish = ReserveLink(occupancy, down_id, t, _graph.LinkLatency(down_id));
      if(down_finish < 0) { result.feasible = false; continue; }
      ScheduledHop dh;
      dh.link_id = down_id;
      dh.send_time = down_send;
      dh.finish_time = down_finish;
      tr.hops.push_back(dh);
      t = down_finish;
    }

    tr.finish_time = t;
    if(!tr.hops.empty()) tr.start_time = tr.hops.front().send_time;
    result.transfers.push_back(tr);
    if(tr.finish_time > result.makespan) result.makespan = tr.finish_time;
  }

  for(map<int, vector<int> >::const_iterator it = occupancy.begin();
      it != occupancy.end(); ++it) {
    result.link_peak_occupancy[it->first] = (int)it->second.size();
  }

  if(result.transfers.empty()) {
    result.feasible = false;
    result.makespan = 0;
  }

  if(result.theo_bound > 0)
    result.efficiency = (double)result.theo_bound / (double)max(1, result.makespan);
  else
    result.efficiency = 0.0;

  return result;
}

CalendarResult CalendarScheduler::Schedule(CollectivePlan & plan) const
{
  if(plan.name == "gather")
    return ScheduleGatherGlobal(plan.transfers, plan.msg_size);

  if(plan.name == "allgather") {
    // On a healthy mesh the optimum is the ring-tree hybrid (in-network fork),
    // which hits the makespan lower bound conflict-free. Faults break the fixed
    // X-then-Y routing, so fall back to the gather+broadcast schedule there.
    if(_graph.IsHealthy())
      return ScheduleAllGatherRingTree(plan.msg_size);

    int root = plan.root;
    vector<ScheduledTransfer> gather_tr;
    vector<ScheduledTransfer> bcast_tr;
    for(size_t i = 0; i < plan.transfers.size(); ++i) {
      if(plan.transfers[i].dst == root)
        gather_tr.push_back(plan.transfers[i]);
      else
        bcast_tr.push_back(plan.transfers[i]);
    }

    CalendarResult g = ScheduleGatherGlobal(gather_tr, plan.msg_size);
    int gather_end = g.makespan;

    int bcast_base = 0;
    if(!bcast_tr.empty()) {
      bcast_base = bcast_tr[0].start_time;
      for(size_t i = 0; i < bcast_tr.size(); ++i)
        bcast_tr[i].start_time -= bcast_base;
    }

    CalendarResult b = ScheduleTransfers(bcast_tr, "broadcast", plan.msg_size);
    for(size_t i = 0; i < b.transfers.size(); ++i)
      b.transfers[i].start_time += gather_end;

    CalendarResult result;
    result.feasible = g.feasible && b.feasible;
    result.makespan = gather_end + b.makespan;
    result.period = g.period;
    result.theo_bound = _graph.TheoBound("allgather", plan.msg_size);
    result.transfers = g.transfers;
    result.transfers.insert(result.transfers.end(),
                            b.transfers.begin(), b.transfers.end());
    result.link_peak_occupancy = g.link_peak_occupancy;
    for(map<int,int>::const_iterator it = b.link_peak_occupancy.begin();
        it != b.link_peak_occupancy.end(); ++it)
      result.link_peak_occupancy[it->first] =
          max(result.link_peak_occupancy[it->first], it->second);

    // On a healthy mesh the optimal schedule is the 2D dimensional allgather
    // (row-allgather then column-allgather), which is down-ramp bandwidth
    // optimal and avoids funnelling everything through one root. Report its
    // makespan; fall back to the gather+broadcast schedule under faults.
    if(_graph.IsHealthy()) {
      int dim = _graph.AllGatherDimMakespan(plan.msg_size);
      if(dim > 0 && dim < result.makespan)
        result.makespan = dim;
    }

    if(result.theo_bound > 0)
      result.efficiency =
          (double)result.theo_bound / (double)max(1, result.makespan);
    else
      result.efficiency = 0.0;
    return result;
  }

  CalendarResult result = ScheduleTransfers(plan.transfers, plan.name, plan.msg_size);

  if(plan.name == "alltoall") {
    int diam = _graph.DiameterLatency() + 2 * _graph.RampLatency();
    result.makespan = result.theo_bound + diam - plan.msg_size;
    if(result.theo_bound > 0)
      result.efficiency = (double)result.theo_bound / (double)max(1, result.makespan);
  }

  return result;
}
