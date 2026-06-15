// Calendar collective simulation - calendar scheduler

#ifndef _CALENDAR_SCHEDULER_HPP_
#define _CALENDAR_SCHEDULER_HPP_

#include <map>
#include <vector>
#include "collective.hpp"
#include "mesh_graph.hpp"

struct CalendarResult {
  bool feasible;
  int makespan;
  int period;
  int theo_bound;
  double efficiency;
  std::vector<ScheduledTransfer> transfers;
  std::map<int, int> link_peak_occupancy;
};

class CalendarScheduler {
public:
  CalendarScheduler(const MeshGraph & graph);

  CalendarResult Schedule(CollectivePlan & plan) const;
  CalendarResult ScheduleTransfers(std::vector<ScheduledTransfer> & transfers,
                                   const std::string & collective_name,
                                   int msg_size) const;

private:
  const MeshGraph & _graph;

  int ReserveLink(std::map<int, std::vector<int> > & occupancy,
                  int link_id, int start, int latency) const;

  std::vector<int> PathLinkIds(const std::vector<int> & path) const;
};

#endif
