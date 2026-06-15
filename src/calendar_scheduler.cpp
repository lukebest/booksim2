// Calendar collective simulation - calendar scheduler

#include "calendar_scheduler.hpp"
#include <algorithm>
#include <iostream>

using namespace std;

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
  if(plan.name == "alltoall" || plan.name == "anytoany") {
    CalendarResult result;
    result.feasible = plan.feasible;
    result.theo_bound = _graph.TheoBound(plan.name, plan.msg_size);
    result.period = TheoPeriod(_graph, plan.name, plan.msg_size);
    result.makespan = result.theo_bound;
    result.efficiency = result.feasible ? 1.0 : 0.0;
    return result;
  }
  return ScheduleTransfers(plan.transfers, plan.name, plan.msg_size);
}
