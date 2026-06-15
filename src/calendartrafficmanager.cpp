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
    _scheduler(NULL)
{
  _collective_type = config.GetStr("collective_type");
  _msg_size = config.GetInt("msg_size");
  _root = config.GetInt("collective_root");
  _anytoany_seed = config.GetInt("anytoany_seed");
  _fault_desc = config.GetStr("fault_desc");
  _result_csv = config.GetStr("result_csv");

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
    out << "collective,msg_size,fault_desc,makespan,period,theo_bound,efficiency,feasible\n";
  }

  out << _collective_type << ","
      << _msg_size << ","
      << _fault_desc << ","
      << result.makespan << ","
      << result.period << ","
      << result.theo_bound << ","
      << fixed << setprecision(4) << result.efficiency << ","
      << (result.feasible ? 1 : 0)
      << "\n";
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

  WriteResultRow(result);

  _sim_state = draining;
  _drain_time = _time;
  return 1;
}
