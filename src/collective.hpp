// Calendar collective simulation - dataflow generation

#ifndef _COLLECTIVE_HPP_
#define _COLLECTIVE_HPP_

#include <string>
#include <vector>
#include "mesh_graph.hpp"

enum CollectiveOp {
  OP_BROADCAST = 0,
  OP_REDUCE,
  OP_GATHER,
  OP_ALLGATHER,
  OP_ALLREDUCE,
  OP_ALLTOALL,
  OP_ANYTOANY
};

CollectiveOp ParseCollectiveType(const std::string & name);

struct ScheduledHop {
  int link_id;
  int send_time;
  int finish_time;
};

struct ScheduledTransfer {
  int src;
  int dst;
  int flit_idx;
  int start_time;
  int finish_time;
  bool use_up_ramp;
  bool use_down_ramp;
  std::vector<ScheduledHop> hops;
};

struct CollectivePlan {
  std::string name;
  int msg_size;
  int root;
  bool feasible;
  std::vector<ScheduledTransfer> transfers;
};

class CollectivePlanner {
public:
  CollectivePlanner(const MeshGraph & graph, bool allow_combine, bool allow_fork);

  CollectivePlan Build(const std::string & type, int msg_size, int root, int anytoany_seed) const;

private:
  const MeshGraph & _graph;
  bool _allow_combine;
  bool _allow_fork;

  std::vector<int> LiveNodes() const;
  std::vector<int> BuildLatencyTree(int root, const std::vector<int> & nodes) const;
  std::vector<int> TreeChildren(int node, const std::vector<int> & tree_parent) const;
  std::vector<int> TreeLatencies(int root, const std::vector<int> & tree_parent,
                                 const std::vector<int> & nodes) const;

  void AddEdgeTransfer(std::vector<ScheduledTransfer> & out,
                       int src, int dst, int flit_idx, int ready_time,
                       bool up, bool down) const;

  CollectivePlan PlanBroadcast(int msg_size, int root, const std::vector<int> & nodes) const;
  CollectivePlan PlanReduce(int msg_size, int root, const std::vector<int> & nodes) const;
  CollectivePlan PlanGather(int msg_size, int root, const std::vector<int> & nodes) const;
  CollectivePlan PlanAllGather(int msg_size, int root, const std::vector<int> & nodes) const;
  CollectivePlan PlanAllReduce(int msg_size, int root, const std::vector<int> & nodes) const;
  CollectivePlan PlanAllToAll(int msg_size, const std::vector<int> & nodes) const;
  CollectivePlan PlanAnyToAny(int msg_size, const std::vector<int> & nodes, int seed) const;
};

#endif
