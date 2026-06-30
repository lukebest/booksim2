// Calendar collective area and power evaluation

#include "collective_power.hpp"
#include <cmath>
#include <iostream>
#include <limits>

using namespace std;

CollectivePowerModule::CollectivePowerModule(Network * net, const Configuration & config)
  : Power_Module(net, config)
{
}

int CollectivePowerModule::countMeshLinks(const MeshGraph & graph)
{
  int count = 0;
  for(int n = 0; n < graph.NumNodes(); ++n) {
    const vector<int> & nbs = graph.Neighbors(n);
    count += (int)nbs.size();
  }
  return count;
}

void CollectivePowerModule::buildLinkEndpoints(const MeshGraph & graph,
                                               map<int, pair<int,int> > & endpoints)
{
  endpoints.clear();
  for(int n = 0; n < graph.NumNodes(); ++n) {
    int up = graph.UpLinkId(n);
    endpoints[up] = make_pair(n, n);

    int down = graph.DownLinkId(n);
    endpoints[down] = make_pair(n, n);

    const vector<int> & nbs = graph.Neighbors(n);
    for(size_t i = 0; i < nbs.size(); ++i) {
      int nb = nbs[i];
      int lid = graph.LinkId(n, nb);
      if(lid >= 0) endpoints[lid] = make_pair(n, nb);
    }
  }
}

int CollectivePowerModule::nodeForLink(int link_id, const MeshGraph & graph,
                                       const map<int, pair<int,int> > & endpoints,
                                       bool src_side)
{
  if(link_id < graph.NumNodes()) return link_id;
  if(link_id < 2 * graph.NumNodes()) return link_id - graph.NumNodes();

  map<int, pair<int,int> >::const_iterator it = endpoints.find(link_id);
  if(it == endpoints.end()) return -1;
  return src_side ? it->second.first : it->second.second;
}

void CollectivePowerModule::calcLinkPowerArea(const MeshGraph & graph,
                                              const map<int,int> & link_flits,
                                              int makespan,
                                              CollectivePowerResult & out)
{
  double time = makespan > 0 ? (double)makespan : 1.0;

  for(map<int,int>::const_iterator it = link_flits.begin(); it != link_flits.end(); ++it) {
    int link_id = it->first;
    if(!graph.IsLinkAlive(link_id)) continue;

    double alpha = ((double)it->second) / time;
    if(alpha > 1.0) {
      cerr << "CollectivePower: link activity factor > 1 on link " << link_id << endl;
    }

    double channelLength = graph.LinkLatency(link_id) * wire_length;
    wire const & w = wireOptimize(channelLength);

    out.channel_area += areaChannel(w.K, w.N, w.M);
    out.channel_dynamic += powerRepeatedWire(w.L, w.K, w.M, w.N) * alpha * channel_width;
    out.channel_dynamic += powerWireDFF(w.M, channel_width, alpha);
    out.channel_dynamic += powerWireClk(w.M, channel_width);
    out.channel_leakage += powerRepeatedWireLeak(w.K, w.M, w.N) * channel_width;
  }
}

void CollectivePowerModule::calcRouterPowerArea(const MeshGraph & graph,
                                                const map<int,int> & node_hops,
                                                int makespan,
                                                CollectivePowerResult & out)
{
  double time = makespan > 0 ? (double)makespan : 1.0;
  double depth = numVC * depthVC;
  double Pwl = powerWordLine(channel_width, depth);
  double Prd = powerMemoryBitRead(depth) * channel_width;
  double Pwr = powerMemoryBitWrite(depth) * channel_width;
  double Pleak = powerMemoryBitLeak(depth) * channel_width;

  for(int n = 0; n < graph.NumNodes(); ++n) {
    if(!graph.IsAlive(n)) continue;

    int degree = (int)graph.Neighbors(n).size();
    int ports = degree + 1;

    out.router_area += areaCrossbar(ports, ports);
    out.buffer_area += areaInputModule(depth);
    out.output_area += areaOutputModule(ports);
    out.router_leakage += powerCrossbarLeak(channel_width, ports, ports);
    out.buffer_leakage += Pleak;

    map<int,int>::const_iterator hit = node_hops.find(n);
    int hops = (hit == node_hops.end()) ? 0 : hit->second;
    double alpha = ((double)hops) / time;
    if(alpha > 1.0) {
      cerr << "CollectivePower: router activity factor > 1 at node " << n << endl;
    }

    out.router_dynamic += alpha * channel_width * powerCrossbar(channel_width, ports, ports, 0, 0);
    out.router_dynamic += alpha * powerCrossbarCtrl(channel_width, ports, ports);
    out.buffer_dynamic += alpha * (Pwl + Prd + Pwl + Pwr);
    out.output_dynamic += alpha * powerWireDFF(1, channel_width, 1.0);
    out.output_dynamic += powerWireClk(1, channel_width);
    out.output_dynamic += alpha * powerOutputCtrl(channel_width);
  }
}

void CollectivePowerModule::calcCalendarTable(const MeshGraph & graph,
                                              int total_hops,
                                              int makespan,
                                              CollectivePowerResult & out)
{
  if(total_hops <= 0) return;

  int num_links = 2 * graph.NumNodes() + countMeshLinks(graph);
  int addr_bits = 1;
  while((1 << addr_bits) < num_links) ++addr_bits;

  double entry_width = channel_width + addr_bits;
  double table_depth_area = (double)total_hops;
  double table_depth_pwr = table_depth_area;
  if(table_depth_pwr > (double)makespan) table_depth_pwr = (double)makespan;
  if(table_depth_pwr < 1.0) table_depth_pwr = 1.0;

  out.calendar_area = areaInputModule(table_depth_area) * (entry_width / channel_width);
  out.calendar_dynamic = powerWordLine(entry_width, table_depth_pwr)
                       + powerMemoryBitRead(table_depth_pwr) * entry_width;
}

void CollectivePowerModule::calcForkReduce(const string & collective_type,
                                           const CalendarResult & result,
                                           int makespan,
                                           const MeshGraph & graph,
                                           CollectivePowerResult & out)
{
  int fork_events = 0;
  int reduce_events = 0;

  if(collective_type == "allgather") {
    for(size_t i = 0; i < result.transfers.size(); ++i) {
      if(result.transfers[i].use_down_ramp) ++fork_events;
    }
    for(map<int,int>::const_iterator it = result.link_peak_occupancy.begin();
        it != result.link_peak_occupancy.end(); ++it) {
      if(!graph.IsRampLink(it->first)) fork_events += it->second;
    }
  }

  if(collective_type == "reduce" || collective_type == "allreduce") {
    reduce_events = (int)result.transfers.size();
  }

  double unit_area = channel_width * H_ND2D1 * W_ND2D1 * MetalPitch * MetalPitch;
  double unit_dynamic = 0.5 * Ci * Vdd * Vdd * fCLK * channel_width;
  double time = makespan > 0 ? (double)makespan : 1.0;

  out.forkreduce_area = unit_area * (fork_events + reduce_events);
  out.forkreduce_dynamic = unit_dynamic * ((double)(fork_events + reduce_events) / time);
}

void CollectivePowerModule::fillActivityFromPeakOccupancy(const MeshGraph & graph,
                                                          const CalendarResult & result,
                                                          map<int,int> & link_flits,
                                                          map<int,int> & node_hops,
                                                          int & total_hops)
{
  map<int, pair<int,int> > endpoints;
  buildLinkEndpoints(graph, endpoints);

  total_hops = 0;
  for(map<int,int>::const_iterator it = result.link_peak_occupancy.begin();
      it != result.link_peak_occupancy.end(); ++it) {
    int lid = it->first;
    int count = it->second;
    if(count <= 0 || !graph.IsLinkAlive(lid)) continue;
    link_flits[lid] = count;
    total_hops += count;

    int src_node = nodeForLink(lid, graph, endpoints, true);
    int dst_node = nodeForLink(lid, graph, endpoints, false);
    if(src_node >= 0) node_hops[src_node] += count;
    if(dst_node >= 0 && dst_node != src_node) node_hops[dst_node] += count;
  }
}

CollectivePowerResult CollectivePowerModule::run(const MeshGraph & graph,
                                                 const CalendarResult & result,
                                                 const string & collective_type)
{
  CollectivePowerResult out;
  out.total_power = 0;
  out.dynamic_power = 0;
  out.leakage_power = 0;
  out.total_area = 0;
  out.energy_per_flit = 0;
  out.channel_dynamic = 0;
  out.channel_leakage = 0;
  out.router_dynamic = 0;
  out.router_leakage = 0;
  out.buffer_dynamic = 0;
  out.buffer_leakage = 0;
  out.output_dynamic = 0;
  out.calendar_dynamic = 0;
  out.calendar_area = 0;
  out.forkreduce_dynamic = 0;
  out.forkreduce_area = 0;
  out.channel_area = 0;
  out.router_area = 0;
  out.buffer_area = 0;
  out.output_area = 0;

  if(!result.feasible || result.makespan <= 0) return out;

  map<int,int> link_flits;
  map<int,int> node_hops;
  map<int, pair<int,int> > endpoints;
  buildLinkEndpoints(graph, endpoints);

  int total_hops = 0;
  int total_flits = 0;

  for(size_t t = 0; t < result.transfers.size(); ++t) {
    const ScheduledTransfer & tr = result.transfers[t];
    ++total_flits;
    for(size_t h = 0; h < tr.hops.size(); ++h) {
      int lid = tr.hops[h].link_id;
      ++link_flits[lid];
      ++total_hops;

      int src_node = nodeForLink(lid, graph, endpoints, true);
      int dst_node = nodeForLink(lid, graph, endpoints, false);
      if(src_node >= 0) ++node_hops[src_node];
      if(dst_node >= 0 && dst_node != src_node) ++node_hops[dst_node];
    }
  }

  if(link_flits.empty() && !result.link_peak_occupancy.empty()) {
    fillActivityFromPeakOccupancy(graph, result, link_flits, node_hops, total_hops);
  }

  if(total_flits == 0) {
    int alive = 0;
    for(int n = 0; n < graph.NumNodes(); ++n)
      if(graph.IsAlive(n)) ++alive;
    int msg_size = 1;
    if(result.period > 0 && alive > 1 &&
       (collective_type == "gather" || collective_type == "allgather"))
      msg_size = result.period / (alive - 1);
    else if(result.period > 0 &&
            (collective_type == "broadcast" || collective_type == "reduce" ||
             collective_type == "allreduce"))
      msg_size = result.period;
    total_flits = alive * msg_size;
  }

  if(total_hops == 0) total_hops = total_flits;

  calcLinkPowerArea(graph, link_flits, result.makespan, out);
  calcRouterPowerArea(graph, node_hops, result.makespan, out);
  calcCalendarTable(graph, total_hops, result.makespan, out);
  calcForkReduce(collective_type, result, result.makespan, graph, out);

  out.dynamic_power = out.channel_dynamic + out.router_dynamic + out.buffer_dynamic
                      + out.output_dynamic + out.calendar_dynamic + out.forkreduce_dynamic;
  out.leakage_power = out.channel_leakage + out.router_leakage + out.buffer_leakage;
  out.total_power = out.dynamic_power + out.leakage_power;

  out.total_area = out.channel_area + out.router_area + out.buffer_area + out.output_area
                 + out.calendar_area + out.forkreduce_area;

  if(total_flits > 0 && out.total_power > 0) {
    out.energy_per_flit = out.total_power * result.makespan / (fCLK * total_flits) * 1.0e12;
  }

  cout << "-----------------------------------------" << endl;
  cout << "- Collective Power/Area Summary" << endl;
  cout << "- Makespan (cycles):       " << result.makespan << endl;
  cout << "- Flit width (bits):       " << channel_width << endl;
  cout << "- Total flits transferred: " << total_flits << endl;
  cout << "- Channel dynamic (W):     " << out.channel_dynamic << endl;
  cout << "- Channel leakage (W):     " << out.channel_leakage << endl;
  cout << "- Router dynamic (W):      " << out.router_dynamic << endl;
  cout << "- Router leakage (W):      " << out.router_leakage << endl;
  cout << "- Buffer dynamic (W):      " << out.buffer_dynamic << endl;
  cout << "- Buffer leakage (W):      " << out.buffer_leakage << endl;
  cout << "- Output dynamic (W):      " << out.output_dynamic << endl;
  cout << "- Calendar table dyn (W):  " << out.calendar_dynamic << endl;
  cout << "- Fork/Reduce dyn (W):     " << out.forkreduce_dynamic << endl;
  cout << "- Total dynamic (W):       " << out.dynamic_power << endl;
  cout << "- Total leakage (W):       " << out.leakage_power << endl;
  cout << "- Total power (W):         " << out.total_power << endl;
  cout << "- Energy per flit (pJ):    " << out.energy_per_flit << endl;
  cout << "-----------------------------------------" << endl;
  cout << "- Channel area (mm^2):     " << out.channel_area << endl;
  cout << "- Router area (mm^2):     " << out.router_area << endl;
  cout << "- Buffer area (mm^2):      " << out.buffer_area << endl;
  cout << "- Output area (mm^2):      " << out.output_area << endl;
  cout << "- Calendar table (mm^2):  " << out.calendar_area << endl;
  cout << "- Fork/Reduce (mm^2):     " << out.forkreduce_area << endl;
  cout << "- Total area (mm^2):       " << out.total_area << endl;
  cout << "-----------------------------------------" << endl;

  return out;
}
