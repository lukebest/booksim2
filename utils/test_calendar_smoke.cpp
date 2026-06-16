// Standalone smoke test for calendar makespan (no flex/bison booksim link).
#include "../src/mesh_graph.hpp"
#include "../src/collective.hpp"
#include "../src/calendar_scheduler.hpp"
#include <iostream>
#include <iomanip>
#include <map>
#include <string>
#include <vector>

using namespace std;

class SmokeConfig : public Configuration {
public:
  SmokeConfig() {
    AddStrField("fault_nodes", "");
    AddStrField("fault_links", "");
    _int_map["mesh_x"] = 12;
    _int_map["mesh_y"] = 16;
    _int_map["h_latency"] = 4;
    _int_map["v_latency"] = 8;
    _int_map["ramp_latency"] = 1;
    _int_map["collective_root"] = 0;
    _int_map["fault_nodes"] = 0;
    _int_map["fault_links"] = 0;
  }
};

static void RunCollective(SmokeConfig & config, const string & name, int msg_size)
{
  MeshGraph graph(config);
  CollectivePlanner planner(graph, true, true);
  CalendarScheduler scheduler(graph);

  CollectivePlan plan = planner.Build(name, msg_size, config.GetInt("collective_root"), 42);
  CalendarResult result = scheduler.Schedule(plan);

  cout << left << setw(12) << name
       << " M=" << setw(3) << msg_size
       << " makespan=" << setw(6) << result.makespan
       << " period=" << setw(6) << result.period
       << " bound=" << setw(6) << result.theo_bound
       << " eff=" << fixed << setprecision(4) << result.efficiency
       << " feasible=" << result.feasible
       << "\n";
}

int main()
{
  SmokeConfig config;

  const char * names[] = {
    "broadcast", "reduce", "gather", "allgather", "allreduce", "alltoall", "anytoany"
  };

  cout << "=== healthy M=1 ===\n";
  for(size_t i = 0; i < sizeof(names)/sizeof(names[0]); ++i)
    RunCollective(config, names[i], 1);

  cout << "\n=== healthy M=4 ===\n";
  for(size_t i = 0; i < sizeof(names)/sizeof(names[0]); ++i)
    RunCollective(config, names[i], 4);

  return 0;
}
