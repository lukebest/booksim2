// Calendar collective traffic manager

#include "calendartrafficmanager.hpp"
#include <fstream>
#include <iomanip>
#include <iostream>

using namespace std;

CalendarTrafficManager::CalendarTrafficManager(const Configuration &config,
                                               const vector<Network *> & net)
  : TrafficManager(config, net),
    _mesh_graph(NULL),
    _planner(NULL),
    _scheduler(NULL),
    _config(&config),
    _collective_power(false),
    _has_power_result(false)
{
  _collective_type = config.GetStr("collective_type");
  _msg_size = config.GetInt("msg_size");
  _root = config.GetInt("collective_root");
  _anytoany_seed = config.GetInt("anytoany_seed");
  _fault_desc = config.GetStr("fault_desc");
  _result_csv = config.GetStr("result_csv");
  _collective_power = config.GetInt("collective_power") != 0;

  _mesh_graph = new MeshGraph(config);
  bool allow_combine = config.GetInt("allow_combine") != 0;
  bool allow_fork = config.GetInt("allow_fork") != 0;
  _planner = new CollectivePlanner(*_mesh_graph, allow_combine, allow_fork);
  _scheduler = new CalendarScheduler(*_mesh_graph);
}

CalendarTrafficManager::~CalendarTrafficManager()
{
  delete _scheduler;
  delete _planner;
  delete _mesh_graph;
}

void CalendarTrafficManager::WriteResultRow(const CalendarResult & result) const
{
  if(_result_csv.empty()) return;

  bool write_header = false;
  {
    ifstream in(_result_csv.c_str());
    write_header = !in.good() || in.peek() == ifstream::traits_type::eof();
  }

  ofstream out(_result_csv.c_str(), ios::app);
  if(!out.good()) {
    cerr << "Unable to open result_csv: " << _result_csv << endl;
    return;
  }

  if(write_header) {
    out << "collective,msg_size,fault_desc,makespan,period,theo_bound,efficiency,feasible";
    if(_collective_power) {
      out << ",total_power_w,dynamic_power_w,leakage_power_w,total_area_mm2,energy_per_flit_pj";
    }
    out << "\n";
  }

  out << _collective_type << ","
      << _msg_size << ","
      << _fault_desc << ","
      << result.makespan << ","
      << result.period << ","
      << result.theo_bound << ","
      << fixed << setprecision(4) << result.efficiency << ","
      << (result.feasible ? 1 : 0);
  if(_collective_power && _has_power_result) {
    out << ","
        << setprecision(6) << _power_result.total_power << ","
        << _power_result.dynamic_power << ","
        << _power_result.leakage_power << ","
        << _power_result.total_area << ","
        << setprecision(4) << _power_result.energy_per_flit;
  } else if(_collective_power) {
    out << ",0,0,0,0,0";
  }
  out << "\n";
}

bool CalendarTrafficManager::_SingleSim()
{
  CollectivePlan plan = _planner->Build(_collective_type, _msg_size, _root, _anytoany_seed);
  CalendarResult result = _scheduler->Schedule(plan);

  cout << "=== Calendar Collective Simulation ===" << endl;
  cout << "Collective: " << _collective_type << endl;
  cout << "Message size (flits): " << _msg_size << endl;
  cout << "Fault scenario: " << _fault_desc << endl;
  cout << "Transfers scheduled: " << result.transfers.size() << endl;
  cout << "Feasible: " << (result.feasible ? "yes" : "no") << endl;
  cout << "Makespan (cycles): " << result.makespan << endl;
  cout << "Calendar period (cycles): " << result.period << endl;
  cout << "Theoretical bound (cycles): " << result.theo_bound << endl;
  cout << "Efficiency (bound/makespan): " << result.efficiency << endl;

  _has_power_result = false;
  if(_collective_power && !_net.empty() && _net[0] != NULL) {
    CollectivePowerModule pmod(_net[0], *_config);
    _power_result = pmod.run(*_mesh_graph, result, _collective_type);
    _has_power_result = true;
  }

  WriteResultRow(result);

  _sim_state = draining;
  _drain_time = _time;
  return 1;
}
