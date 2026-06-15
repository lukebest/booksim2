// Calendar collective simulation - dataflow generation

#include "collective.hpp"
#include <algorithm>
#include <cstdlib>
#include <limits>
#include <map>
#include <queue>
#include <random>

using namespace std;

CollectiveOp ParseCollectiveType(const string & name)
{
  if(name == "broadcast") return OP_BROADCAST;
  if(name == "reduce") return OP_REDUCE;
  if(name == "gather") return OP_GATHER;
  if(name == "allgather") return OP_ALLGATHER;
  if(name == "allreduce") return OP_ALLREDUCE;
  if(name == "alltoall") return OP_ALLTOALL;
  if(name == "anytoany") return OP_ANYTOANY;
  return OP_ALLREDUCE;
}

CollectivePlanner::CollectivePlanner(const MeshGraph & graph, bool allow_combine, bool allow_fork)
  : _graph(graph), _allow_combine(allow_combine), _allow_fork(allow_fork)
{
}

vector<int> CollectivePlanner::LiveNodes() const
{
  vector<int> nodes;
  for(int i = 0; i < _graph.NumNodes(); ++i)
    if(_graph.IsAlive(i)) nodes.push_back(i);
  return nodes;
}

vector<int> CollectivePlanner::BuildLatencyTree(int root, const vector<int> & nodes) const
{
  vector<int> parent(_graph.NumNodes(), -1);
  vector<int> dist(_graph.NumNodes(), numeric_limits<int>::max());
  if(find(nodes.begin(), nodes.end(), root) == nodes.end()) return parent;

  dist[root] = 0;
  typedef pair<int,int> State;
  priority_queue<State, vector<State>, greater<State> > pq;
  pq.push(make_pair(0, root));

  while(!pq.empty()) {
    State top = pq.top(); pq.pop();
    int d = top.first;
    int u = top.second;
    if(d != dist[u]) continue;
    const vector<int> & nbs = _graph.Neighbors(u);
    for(size_t i = 0; i < nbs.size(); ++i) {
      int v = nbs[i];
      if(find(nodes.begin(), nodes.end(), v) == nodes.end()) continue;
      int w = _graph.Latency(u, v);
      if(d + w < dist[v]) {
        dist[v] = d + w;
        parent[v] = u;
        pq.push(make_pair(dist[v], v));
      }
    }
  }
  return parent;
}

vector<int> CollectivePlanner::TreeChildren(int node, const vector<int> & tree_parent) const
{
  vector<int> children;
  for(int i = 0; i < _graph.NumNodes(); ++i) {
    if(tree_parent[i] == node) children.push_back(i);
  }
  return children;
}

vector<int> CollectivePlanner::TreeDepths(int root, const vector<int> & tree_parent) const
{
  vector<int> depth(_graph.NumNodes(), -1);
  depth[root] = 0;
  for(int n = 0; n < _graph.NumNodes(); ++n) {
    if(tree_parent[n] == -1 && n != root) continue;
    if(n == root) continue;
    int cur = n;
    int d = 0;
    while(cur != root && tree_parent[cur] != -1) {
      ++d;
      cur = tree_parent[cur];
    }
    depth[n] = d;
  }
  return depth;
}

void CollectivePlanner::AddEdgeTransfer(vector<ScheduledTransfer> & out,
                                        int src, int dst, int flit_idx, int ready_time,
                                        bool up, bool down) const
{
  ScheduledTransfer t;
  t.src = src;
  t.dst = dst;
  t.flit_idx = flit_idx;
  t.start_time = ready_time;
  t.finish_time = -1;
  t.use_up_ramp = up;
  t.use_down_ramp = down;
  out.push_back(t);
}

CollectivePlan CollectivePlanner::PlanBroadcast(int msg_size, int root,
                                                const vector<int> & nodes) const
{
  CollectivePlan plan;
  plan.name = "broadcast";
  plan.msg_size = msg_size;
  plan.root = root;
  plan.feasible = true;

  vector<int> parent = BuildLatencyTree(root, nodes);

  for(int k = 0; k < msg_size; ++k) {
    for(size_t i = 0; i < nodes.size(); ++i) {
      int child = nodes[i];
      if(child == root) continue;
      int p = parent[child];
      if(p < 0) { plan.feasible = false; continue; }
      bool up = (p == root);
      vector<int> ch = TreeChildren(child, parent);
      bool down = ch.empty();
      int ready = k;
      if(up) ready = k;
      AddEdgeTransfer(plan.transfers, p, child, k, ready, up, down);
    }
  }
  return plan;
}

CollectivePlan CollectivePlanner::PlanReduce(int msg_size, int root,
                                             const vector<int> & nodes) const
{
  CollectivePlan plan;
  plan.name = "reduce";
  plan.msg_size = msg_size;
  plan.root = root;
  plan.feasible = true;

  vector<int> parent = BuildLatencyTree(root, nodes);
  vector<int> depth = TreeDepths(root, parent);
  int max_depth = 0;
  for(size_t i = 0; i < nodes.size(); ++i)
    if(depth[nodes[i]] > max_depth) max_depth = depth[nodes[i]];

  for(int k = 0; k < msg_size; ++k) {
    for(size_t i = 0; i < nodes.size(); ++i) {
      int node = nodes[i];
      if(node == root) continue;
      int p = parent[node];
      if(p < 0) { plan.feasible = false; continue; }
      bool up = true;
      bool down = (p == root);
      int ready = k + (max_depth - depth[node]);
      AddEdgeTransfer(plan.transfers, node, p, k, ready, up, down);
    }
  }
  return plan;
}

CollectivePlan CollectivePlanner::PlanGather(int msg_size, int root,
                                             const vector<int> & nodes) const
{
  CollectivePlan plan;
  plan.name = "gather";
  plan.msg_size = msg_size;
  plan.root = root;
  plan.feasible = true;

  vector<pair<int,int> > sources;
  for(size_t i = 0; i < nodes.size(); ++i) {
    int src = nodes[i];
    if(src == root) continue;
    vector<int> path = _graph.ShortestPath(src, root);
    if(path.empty()) { plan.feasible = false; continue; }
    int path_lat = 2 * _graph.LinkLatency(_graph.UpLinkId(src));
    for(size_t h = 0; h + 1 < path.size(); ++h)
      path_lat += _graph.Latency(path[h], path[h+1]);
    sources.push_back(make_pair(path_lat, src));
  }
  sort(sources.begin(), sources.end());

  int root_slot = 0;
  for(size_t i = 0; i < sources.size(); ++i) {
    int src = sources[i].second;
    int path_lat = sources[i].first;
    for(int k = 0; k < msg_size; ++k) {
      int ready = root_slot - path_lat;
      if(ready < 0) ready = 0;
      AddEdgeTransfer(plan.transfers, src, root, k, ready, true, true);
      ++root_slot;
    }
  }
  return plan;
}

CollectivePlan CollectivePlanner::PlanAllGather(int msg_size, int root,
                                                const vector<int> & nodes) const
{
  CollectivePlan gather = PlanGather(msg_size, root, nodes);
  CollectivePlan bcast = PlanBroadcast(msg_size, root, nodes);

  CollectivePlan plan;
  plan.name = "allgather";
  plan.msg_size = msg_size;
  plan.root = root;
  plan.feasible = gather.feasible && bcast.feasible;

  int gather_end = 0;
  if(!gather.transfers.empty())
    gather_end = gather.transfers.back().start_time + 1;

  for(size_t i = 0; i < gather.transfers.size(); ++i)
    plan.transfers.push_back(gather.transfers[i]);

  for(size_t i = 0; i < bcast.transfers.size(); ++i) {
    ScheduledTransfer t = bcast.transfers[i];
    t.start_time += gather_end;
    plan.transfers.push_back(t);
  }
  return plan;
}

CollectivePlan CollectivePlanner::PlanAllReduce(int msg_size, int root,
                                                const vector<int> & nodes) const
{
  CollectivePlan reduce = PlanReduce(msg_size, root, nodes);
  CollectivePlan bcast = PlanBroadcast(msg_size, root, nodes);

  CollectivePlan plan;
  plan.name = "allreduce";
  plan.msg_size = msg_size;
  plan.root = root;
  plan.feasible = reduce.feasible && bcast.feasible;

  int reduce_end = 0;
  if(!reduce.transfers.empty())
    reduce_end = reduce.transfers.back().start_time + 1;

  for(size_t i = 0; i < reduce.transfers.size(); ++i)
    plan.transfers.push_back(reduce.transfers[i]);

  for(size_t i = 0; i < bcast.transfers.size(); ++i) {
    ScheduledTransfer t = bcast.transfers[i];
    t.start_time += reduce_end;
    plan.transfers.push_back(t);
  }
  return plan;
}

CollectivePlan CollectivePlanner::PlanAllToAll(int msg_size,
                                               const vector<int> & nodes) const
{
  CollectivePlan plan;
  plan.name = "alltoall";
  plan.msg_size = msg_size;
  plan.root = 0;
  plan.feasible = true;

  int slot = 0;
  for(size_t i = 0; i < nodes.size(); ++i) {
    int src = nodes[i];
    for(size_t j = 0; j < nodes.size(); ++j) {
      int dst = nodes[j];
      if(src == dst) continue;
      for(int k = 0; k < msg_size; ++k) {
        AddEdgeTransfer(plan.transfers, src, dst, k, slot, true, true);
        ++slot;
      }
    }
  }
  return plan;
}

CollectivePlan CollectivePlanner::PlanAnyToAny(int msg_size,
                                                 const vector<int> & nodes,
                                                 int seed) const
{
  CollectivePlan plan;
  plan.name = "anytoany";
  plan.msg_size = msg_size;
  plan.root = 0;
  plan.feasible = true;

  if(nodes.size() < 2) return plan;

  mt19937 rng(seed);
  vector<int> perm = nodes;
  bool valid = false;
  for(int attempt = 0; attempt < 32 && !valid; ++attempt) {
    shuffle(perm.begin(), perm.end(), rng);
    valid = true;
    for(size_t i = 0; i < nodes.size(); ++i) {
      if(perm[i] == nodes[i]) { valid = false; break; }
    }
  }

  int slot = 0;
  for(size_t i = 0; i < nodes.size(); ++i) {
    int src = nodes[i];
    int dst = perm[i];
    for(int k = 0; k < msg_size; ++k) {
      AddEdgeTransfer(plan.transfers, src, dst, k, slot, true, true);
      ++slot;
    }
  }
  return plan;
}

CollectivePlan CollectivePlanner::Build(const string & type, int msg_size,
                                        int root, int anytoany_seed) const
{
  vector<int> nodes = LiveNodes();
  if(nodes.empty()) {
    CollectivePlan plan;
    plan.name = type;
    plan.msg_size = msg_size;
    plan.root = root;
    plan.feasible = false;
    return plan;
  }
  int effective_root = root;
  if(find(nodes.begin(), nodes.end(), effective_root) == nodes.end())
    effective_root = nodes[0];

  if(type == "broadcast") return PlanBroadcast(msg_size, effective_root, nodes);
  if(type == "reduce") return PlanReduce(msg_size, effective_root, nodes);
  if(type == "gather") return PlanGather(msg_size, effective_root, nodes);
  if(type == "allgather") return PlanAllGather(msg_size, effective_root, nodes);
  if(type == "allreduce") return PlanAllReduce(msg_size, effective_root, nodes);
  if(type == "alltoall") return PlanAllToAll(msg_size, nodes);
  if(type == "anytoany") return PlanAnyToAny(msg_size, nodes, anytoany_seed);

  CollectivePlan plan;
  plan.name = type;
  plan.msg_size = msg_size;
  plan.root = root;
  plan.feasible = false;
  return plan;
}
