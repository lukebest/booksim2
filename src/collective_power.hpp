// Calendar collective area and power evaluation

#ifndef _COLLECTIVE_POWER_HPP_
#define _COLLECTIVE_POWER_HPP_

#include <map>
#include <string>
#include "power_module.hpp"
#include "mesh_graph.hpp"
#include "calendar_scheduler.hpp"

struct CollectivePowerResult {
  double total_power;
  double dynamic_power;
  double leakage_power;
  double total_area;
  double energy_per_flit;

  double channel_dynamic;
  double channel_leakage;
  double router_dynamic;
  double router_leakage;
  double buffer_dynamic;
  double buffer_leakage;
  double output_dynamic;
  double calendar_dynamic;
  double calendar_area;
  double forkreduce_dynamic;
  double forkreduce_area;

  double channel_area;
  double router_area;
  double buffer_area;
  double output_area;
};

class CollectivePowerModule : public Power_Module {
public:
  CollectivePowerModule(Network * net, const Configuration & config);

  CollectivePowerResult run(const MeshGraph & graph,
                            const CalendarResult & result,
                            const std::string & collective_type);

private:
  void buildLinkEndpoints(const MeshGraph & graph,
                          std::map<int, std::pair<int,int> > & endpoints);

  int countMeshLinks(const MeshGraph & graph);

  void calcLinkPowerArea(const MeshGraph & graph,
                         const std::map<int,int> & link_flits,
                         int makespan,
                         CollectivePowerResult & out);

  void calcRouterPowerArea(const MeshGraph & graph,
                           const std::map<int,int> & node_hops,
                           int makespan,
                           CollectivePowerResult & out);

  void calcCalendarTable(const MeshGraph & graph,
                         int total_hops,
                         int makespan,
                         CollectivePowerResult & out);

  void calcForkReduce(const std::string & collective_type,
                      const CalendarResult & result,
                      int makespan,
                      const MeshGraph & graph,
                      CollectivePowerResult & out);

  void fillActivityFromPeakOccupancy(const MeshGraph & graph,
                                       const CalendarResult & result,
                                       std::map<int,int> & link_flits,
                                       std::map<int,int> & node_hops,
                                       int & total_hops);

  int nodeForLink(int link_id, const MeshGraph & graph,
                  const std::map<int, std::pair<int,int> > & endpoints,
                  bool src_side);
};

#endif
