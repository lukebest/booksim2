#ifndef _TRACETRAFFICMANAGER_HPP_
#define _TRACETRAFFICMANAGER_HPP_

#include "trafficmanager.hpp"
#include "fork_table.hpp"
#include <vector>

class TraceTrafficManager : public TrafficManager {
protected:
  struct HopEvent {
    int inject;
    int dest;
    int cycle;
    int gather_src;
    int final_hop;
  };
  struct TreeEvent {
    int gather_src;
    int inject_cycle;
    int num_flits;
  };

  std::string _trace_mode;
  std::string _trace_file;
  std::string _fork_file;
  std::string _result_csv;
  int _expected_makespan;
  int _msg_flits;
  int _max_cycle;
  int _drain_slack;
  size_t _hop_idx;
  size_t _tree_idx;

  std::vector<HopEvent> _hop_events;
  std::vector<TreeEvent> _tree_events;
  ForkTable _fork_table;

  std::vector<std::vector<int> > _recv_count;
  int _total_expected;
  int _total_received;
  int _sim_makespan;

#ifdef TRACK_STALLS
  long _stall_buffer_full;
#endif

  virtual void _Inject();
  virtual bool _SingleSim();
  virtual void _RetireFlit(Flit *f, int dest);

  void _LoadHopTrace(const std::string & path);
  void _LoadTreeTrace(const std::string & path);
  void _InjectHopEvent(const HopEvent & e);
  void _InjectTreeEvent(const TreeEvent & e);
  bool _AllGatherComplete() const;
  void _WriteResult() const;

public:
  TraceTrafficManager(const Configuration &config, const vector<Network *> & net);
  virtual ~TraceTrafficManager();
};

#endif
