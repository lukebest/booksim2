// Calendar collective traffic manager

#ifndef _CALENDARTRAFFICMANAGER_HPP_
#define _CALENDARTRAFFICMANAGER_HPP_

#include "trafficmanager.hpp"
#include "mesh_graph.hpp"
#include "collective.hpp"
#include "calendar_scheduler.hpp"
#include "collective_power.hpp"

class CalendarTrafficManager : public TrafficManager {
protected:
  MeshGraph * _mesh_graph;
  CollectivePlanner * _planner;
  CalendarScheduler * _scheduler;

  std::string _collective_type;
  int _msg_size;
  int _root;
  int _anytoany_seed;
  std::string _fault_desc;
  std::string _result_csv;
  const Configuration * _config;
  bool _collective_power;
  bool _has_power_result;
  CollectivePowerResult _power_result;

  virtual bool _SingleSim();

  void WriteResultRow(const CalendarResult & result) const;

public:
  CalendarTrafficManager(const Configuration &config, const vector<Network *> & net);
  virtual ~CalendarTrafficManager();
};

#endif
