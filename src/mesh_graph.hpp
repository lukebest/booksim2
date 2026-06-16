// Calendar collective simulation - heterogeneous mesh graph

#ifndef _MESH_GRAPH_HPP_
#define _MESH_GRAPH_HPP_

#include <vector>
#include <map>
#include <set>
#include <utility>
#include "config_utils.hpp"

class MeshGraph {
public:
  static const int UP_LINK   = -1;
  static const int DOWN_LINK = -2;

  MeshGraph(const Configuration & config);

  int NumNodes() const { return _num_nodes; }
  int MeshX() const { return _mesh_x; }
  int MeshY() const { return _mesh_y; }

  bool IsAlive(int node) const;
  bool IsHealthy() const;
  int AllGatherDimMakespan(int msg_size) const;
  int NodeId(int x, int y) const { return x + _mesh_x * y; }
  void NodeCoord(int node, int & x, int & y) const;

  int Latency(int src, int dst) const;
  const std::vector<int> & Neighbors(int node) const;

  int ManhattanLatency(int a, int b) const;
  int DiameterLatency() const;
  int RampLatency() const { return _ramp_lat; }
  int PathLatency(int src, int dst, bool include_ramps = true) const;
  int MaxPathToRoot(int root) const;
  int EffectiveRoot(int preferred = 0) const;
  int BisectionCapacity() const;

  std::vector<int> ShortestPath(int src, int dst) const;

  int LinkId(int src, int dst) const;
  int LinkLatency(int link_id) const;
  bool IsLinkAlive(int link_id) const;

  int UpLinkId(int node) const { return node; }
  int DownLinkId(int node) const { return _num_nodes + node; }
  bool IsRampLink(int link_id) const { return link_id < 2 * _num_nodes; }

  int TheoBound(const std::string & collective_type, int msg_size) const;

private:
  int _mesh_x;
  int _mesh_y;
  int _num_nodes;
  int _h_lat;
  int _v_lat;
  int _ramp_lat;
  int _collective_root;

  std::vector<bool> _alive;
  std::vector<std::vector<int> > _neighbors;
  std::map<std::pair<int,int>, int> _link_latency;
  std::map<std::pair<int,int>, int> _link_id;
  std::vector<int> _link_latencies;
  std::vector<bool> _link_alive;

  void BuildMesh();
  void ApplyFaults(const Configuration & config);
  void RegisterLink(int src, int dst, int latency);
  int GatherSlotBound(int root, int msg_size) const;
  int GatherTheoBound(int msg_size) const;
  int AllGatherTheoBound(int msg_size) const;
  int LineGatherBound(int num_nodes, int link_lat, int flits_per_node) const;
};

#endif
