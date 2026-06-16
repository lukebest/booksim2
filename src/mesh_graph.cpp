// Calendar collective simulation - heterogeneous mesh graph

#include "mesh_graph.hpp"
#include <algorithm>
#include <cmath>
#include <iostream>
#include <limits>
#include <queue>

using namespace std;

MeshGraph::MeshGraph(const Configuration & config)
  : _mesh_x(config.GetInt("mesh_x")),
    _mesh_y(config.GetInt("mesh_y")),
    _num_nodes(_mesh_x * _mesh_y),
    _h_lat(config.GetInt("h_latency")),
    _v_lat(config.GetInt("v_latency")),
    _ramp_lat(config.GetInt("ramp_latency")),
    _collective_root(config.GetInt("collective_root"))
{
  _alive.assign(_num_nodes, true);
  _neighbors.assign(_num_nodes, vector<int>());
  BuildMesh();
  ApplyFaults(config);
}

void MeshGraph::BuildMesh()
{
  for(int y = 0; y < _mesh_y; ++y) {
    for(int x = 0; x < _mesh_x; ++x) {
      int node = NodeId(x, y);
      if(x + 1 < _mesh_x) {
        int nb = NodeId(x + 1, y);
        RegisterLink(node, nb, _h_lat);
        RegisterLink(nb, node, _h_lat);
      }
      if(y + 1 < _mesh_y) {
        int nb = NodeId(x, y + 1);
        RegisterLink(node, nb, _v_lat);
        RegisterLink(nb, node, _v_lat);
      }
    }
  }
}

void MeshGraph::RegisterLink(int src, int dst, int latency)
{
  pair<int,int> key(src, dst);
  if(_link_id.find(key) != _link_id.end()) return;
  int id = (int)_link_latencies.size() + 2 * _num_nodes;
  _link_id[key] = id;
  _link_latencies.push_back(latency);
  _link_alive.push_back(true);
  _link_latency[key] = latency;
  _neighbors[src].push_back(dst);
}

void MeshGraph::ApplyFaults(const Configuration & config)
{
  vector<int> fault_nodes = config.GetIntArray("fault_nodes");
  if(fault_nodes.empty()) {
    int v = config.GetInt("fault_nodes");
    if(v != 0) fault_nodes.push_back(v);
  }
  for(size_t i = 0; i < fault_nodes.size(); ++i) {
    int n = fault_nodes[i];
    if(n >= 0 && n < _num_nodes) _alive[n] = false;
  }

  vector<int> fault_links = config.GetIntArray("fault_links");
  if(fault_links.empty()) {
    int v = config.GetInt("fault_links");
    if(v != 0) fault_links.push_back(v);
  }
  for(size_t i = 0; i + 1 < fault_links.size(); i += 2) {
    int a = fault_links[i];
    int b = fault_links[i+1];
    map<pair<int,int>, int>::iterator it = _link_id.find(make_pair(a,b));
    if(it != _link_id.end()) {
      _link_alive[it->second] = false;
      _link_alive[_link_id[make_pair(b,a)]] = false;
    }
  }

  for(int n = 0; n < _num_nodes; ++n) {
    if(!_alive[n]) {
      vector<int> keep;
      for(size_t i = 0; i < _neighbors[n].size(); ++i) {
        int nb = _neighbors[n][i];
        if(!IsAlive(nb)) continue;
        int lid = LinkId(n, nb);
        if(lid >= 0 && IsLinkAlive(lid)) keep.push_back(nb);
      }
      _neighbors[n] = keep;
    }
  }
}

bool MeshGraph::IsAlive(int node) const
{
  return node >= 0 && node < _num_nodes && _alive[node];
}

void MeshGraph::NodeCoord(int node, int & x, int & y) const
{
  x = node % _mesh_x;
  y = node / _mesh_x;
}

int MeshGraph::Latency(int src, int dst) const
{
  map<pair<int,int>, int>::const_iterator it = _link_latency.find(make_pair(src,dst));
  if(it == _link_latency.end()) return numeric_limits<int>::max();
  return it->second;
}

const vector<int> & MeshGraph::Neighbors(int node) const
{
  return _neighbors[node];
}

int MeshGraph::ManhattanLatency(int a, int b) const
{
  int ax, ay, bx, by;
  NodeCoord(a, ax, ay);
  NodeCoord(b, bx, by);
  return _h_lat * abs(ax - bx) + _v_lat * abs(ay - by);
}

int MeshGraph::DiameterLatency() const
{
  return _h_lat * (_mesh_x - 1) + _v_lat * (_mesh_y - 1);
}

int MeshGraph::EffectiveRoot(int preferred) const
{
  if(IsAlive(preferred)) return preferred;
  for(int i = 0; i < _num_nodes; ++i)
    if(IsAlive(i)) return i;
  return preferred;
}

int MeshGraph::PathLatency(int src, int dst, bool include_ramps) const
{
  if(!IsAlive(src) || !IsAlive(dst)) return numeric_limits<int>::max();
  if(src == dst) return 0;

  vector<int> path = ShortestPath(src, dst);
  if(path.size() < 2) return numeric_limits<int>::max();

  int lat = 0;
  if(include_ramps) lat += LinkLatency(UpLinkId(src));
  for(size_t h = 0; h + 1 < path.size(); ++h)
    lat += Latency(path[h], path[h+1]);
  if(include_ramps) lat += LinkLatency(DownLinkId(dst));
  return lat;
}

int MeshGraph::MaxPathToRoot(int root) const
{
  int max_lat = 0;
  for(int i = 0; i < _num_nodes; ++i) {
    if(!IsAlive(i) || i == root) continue;
    max_lat = max(max_lat, PathLatency(i, root));
  }
  return max_lat;
}

int MeshGraph::GatherSlotBound(int root, int msg_size) const
{
  int alive = 0;
  for(int i = 0; i < _num_nodes; ++i) if(IsAlive(i)) ++alive;
  if(alive <= 1) return msg_size;

  int ramp_period = (alive - 1) * msg_size;

  vector<int> path_lats;
  for(int i = 0; i < _num_nodes; ++i) {
    if(!IsAlive(i) || i == root) continue;
    for(int k = 0; k < msg_size; ++k)
      path_lats.push_back(PathLatency(i, root));
  }
  sort(path_lats.begin(), path_lats.end());

  int slack = 0;
  for(size_t i = 0; i < path_lats.size(); ++i)
    slack = max(slack, path_lats[i] - (int)i);

  int slot_makespan = slack + (int)path_lats.size() - 1;
  return max(ramp_period, slot_makespan);
}

int MeshGraph::GatherTheoBound(int msg_size) const
{
  int alive = 0;
  for(int i = 0; i < _num_nodes; ++i) if(IsAlive(i)) ++alive;
  if(alive <= 1) return msg_size;
  return GatherSlotBound(EffectiveRoot(_collective_root), msg_size);
}

int MeshGraph::LineGatherBound(int num_nodes, int link_lat, int flits_per_node) const
{
  // The end node of a num_nodes-long line gathers (num_nodes-1)*flits_per_node
  // flits; the node j hops away contributes flits_per_node flits whose arrival
  // lower bound is j*link_lat + 2*ramp (pipelined +0..flits_per_node-1).
  if(num_nodes <= 1 || flits_per_node <= 0) return flits_per_node;
  vector<int> avail;
  for(int j = 1; j < num_nodes; ++j) {
    int base = j * link_lat + 2 * _ramp_lat;
    for(int k = 0; k < flits_per_node; ++k) avail.push_back(base + k);
  }
  sort(avail.begin(), avail.end());
  int t0 = 0;
  for(size_t i = 0; i < avail.size(); ++i)
    t0 = max(t0, avail[i] - (int)i);
  return t0 + (int)avail.size() - 1;
}

int MeshGraph::AllGatherTheoBound(int msg_size) const
{
  int alive = 0;
  for(int i = 0; i < _num_nodes; ++i) if(IsAlive(i)) ++alive;
  if(alive <= 1) return msg_size;

  // (1) per-node down-ramp bandwidth floor: every node ingests (N-1)*M flits.
  int downramp = (alive - 1) * msg_size;

  // (2) bisection: half the nodes' data must cross the min-link balanced cut.
  int bis_links = min(_mesh_x, _mesh_y);
  if(bis_links < 1) bis_links = 1;
  int bisec = ((alive / 2) * msg_size + bis_links - 1) / bis_links;

  // (3) worst-case receiver pipeline; a corner is farthest from the rest.
  int pipe = 0;
  int corners[4] = { NodeId(0, 0), NodeId(_mesh_x - 1, 0),
                     NodeId(0, _mesh_y - 1), NodeId(_mesh_x - 1, _mesh_y - 1) };
  for(int c = 0; c < 4; ++c)
    if(IsAlive(corners[c]))
      pipe = max(pipe, GatherSlotBound(corners[c], msg_size));

  int lb = downramp;
  if(bisec > lb) lb = bisec;
  if(pipe > lb) lb = pipe;
  return lb;
}

int MeshGraph::AllGatherDimMakespan(int msg_size) const
{
  // 2D dimensional allgather: row-allgather (X) then column-allgather (Y).
  // Each phase is a line-allgather bounded by its end node (a line gather).
  int phase_x = LineGatherBound(_mesh_x, _h_lat, msg_size);
  int phase_y = LineGatherBound(_mesh_y, _v_lat, _mesh_x * msg_size);
  return phase_x + phase_y;
}

bool MeshGraph::IsHealthy() const
{
  for(int i = 0; i < _num_nodes; ++i)
    if(!_alive[i]) return false;
  for(size_t i = 0; i < _link_alive.size(); ++i)
    if(!_link_alive[i]) return false;
  return true;
}

int MeshGraph::BisectionCapacity() const
{
  int bisection_h = (_mesh_x / 2) * _v_lat;
  int bisection_v = (_mesh_y / 2) * _h_lat;
  return max(bisection_h, bisection_v);
}

int MeshGraph::LinkId(int src, int dst) const
{
  map<pair<int,int>, int>::const_iterator it = _link_id.find(make_pair(src,dst));
  if(it == _link_id.end()) return -1;
  return it->second;
}

int MeshGraph::LinkLatency(int link_id) const
{
  if(link_id < 0) return _ramp_lat;
  if(link_id < 2 * _num_nodes) return _ramp_lat;
  int idx = link_id - 2 * _num_nodes;
  if(idx < 0 || idx >= (int)_link_latencies.size()) return _ramp_lat;
  return _link_latencies[idx];
}

bool MeshGraph::IsLinkAlive(int link_id) const
{
  if(link_id < 0) return true;
  if(link_id < _num_nodes) return IsAlive(link_id);
  if(link_id < 2 * _num_nodes) return IsAlive(link_id - _num_nodes);
  int idx = link_id - 2 * _num_nodes;
  if(idx >= 0 && idx < (int)_link_alive.size()) return _link_alive[idx];
  return true;
}

vector<int> MeshGraph::ShortestPath(int src, int dst) const
{
  vector<int> path;
  if(!IsAlive(src) || !IsAlive(dst)) return path;
  if(src == dst) {
    path.push_back(src);
    return path;
  }

  vector<int> dist(_num_nodes, numeric_limits<int>::max());
  vector<int> prev(_num_nodes, -1);
  dist[src] = 0;

  typedef pair<int,int> State;
  priority_queue<State, vector<State>, greater<State> > pq;
  pq.push(make_pair(0, src));

  while(!pq.empty()) {
    State top = pq.top(); pq.pop();
    int d = top.first;
    int u = top.second;
    if(d != dist[u]) continue;
    if(u == dst) break;
    const vector<int> & nbs = _neighbors[u];
    for(size_t i = 0; i < nbs.size(); ++i) {
      int v = nbs[i];
      if(!IsAlive(v)) continue;
      int lid = LinkId(u, v);
      if(lid < 0 || !IsLinkAlive(lid)) continue;
      int w = Latency(u, v);
      if(d + w < dist[v]) {
        dist[v] = d + w;
        prev[v] = u;
        pq.push(make_pair(dist[v], v));
      }
    }
  }

  if(prev[dst] == -1) return path;
  int cur = dst;
  while(cur != -1) {
    path.push_back(cur);
    cur = prev[cur];
  }
  reverse(path.begin(), path.end());
  return path;
}

int MeshGraph::TheoBound(const string & collective_type, int msg_size) const
{
  int N = _num_nodes;
  int alive = 0;
  for(int i = 0; i < N; ++i) if(IsAlive(i)) ++alive;
  if(alive <= 1) return msg_size;

  int mesh_diam = DiameterLatency();
  int reduce_diam = mesh_diam + 2 * _ramp_lat;
  int bcast_diam = mesh_diam + 2 * _ramp_lat;
  int bisection = BisectionCapacity();
  if(bisection <= 0) bisection = 1;
  int alltoall_bound = (alive * (alive - 1) * msg_size + bisection - 1) / bisection;

  if(collective_type == "broadcast")
    return bcast_diam + msg_size - 1;
  if(collective_type == "reduce")
    return reduce_diam + msg_size - 1;
  if(collective_type == "allreduce")
    return reduce_diam + bcast_diam + msg_size - 1;
  if(collective_type == "gather")
    return GatherTheoBound(msg_size);
  if(collective_type == "allgather")
    return AllGatherTheoBound(msg_size);
  if(collective_type == "alltoall" || collective_type == "anytoany")
    return alltoall_bound;
  return bcast_diam + msg_size;
}
