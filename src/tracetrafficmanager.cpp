// Trace-driven allgather traffic manager (Route A hop / Route B tree).

#include "tracetrafficmanager.hpp"
#include <fstream>
#include <iostream>
#include <sstream>
#include <limits>

using namespace std;

TraceTrafficManager::TraceTrafficManager(const Configuration &config,
                                         const vector<Network *> & net)
  : TrafficManager(config, net),
    _hop_idx(0), _tree_idx(0), _total_expected(0), _total_received(0),
    _sim_makespan(-1)
#ifdef TRACK_STALLS
    , _stall_buffer_full(0)
#endif
{
  _trace_mode = config.GetStr("trace_mode");
  _trace_file = config.GetStr("trace_file");
  _fork_file = config.GetStr("fork_file");
  _result_csv = config.GetStr("result_csv");
  _expected_makespan = config.GetInt("expected_makespan");
  _msg_flits = config.GetInt("msg_size");
  _drain_slack = config.GetInt("trace_drain_slack");
  if(_drain_slack <= 0) _drain_slack = 256;
  _max_cycle = 0;

  _recv_count.assign(_nodes, vector<int>(_nodes, 0));
  _total_expected = _nodes * (_nodes - 1) * _msg_flits;

  if(_trace_mode == "hop") {
    _LoadHopTrace(_trace_file);
  } else if(_trace_mode == "tree") {
    _LoadTreeTrace(_trace_file);
    if(!_fork_file.empty()) {
      if(!_fork_table.Load(_fork_file)) {
        Error("Failed to load fork_file: " + _fork_file);
      }
    }
  } else {
    Error("Unknown trace_mode: " + _trace_mode);
  }
}

TraceTrafficManager::~TraceTrafficManager() {}

void TraceTrafficManager::_LoadHopTrace(const string & path)
{
  ifstream in(path.c_str());
  if(!in.good()) Error("Cannot open trace_file: " + path);
  string line;
  while(getline(in, line)) {
    if(line.empty() || line[0] == '#') continue;
    istringstream iss(line);
    string tag;
    iss >> tag;
    if(tag != "HOP") continue;
    HopEvent e;
    int flit_idx;
    int is_final;
    iss >> e.inject >> e.dest >> e.cycle >> e.gather_src >> flit_idx >> is_final;
    e.final_hop = is_final;
    _hop_events.push_back(e);
    _max_cycle = max(_max_cycle, e.cycle);
  }
}

void TraceTrafficManager::_LoadTreeTrace(const string & path)
{
  ifstream in(path.c_str());
  if(!in.good()) Error("Cannot open trace_file: " + path);
  string line;
  while(getline(in, line)) {
    if(line.empty() || line[0] == '#') continue;
    istringstream iss(line);
    string tag;
    iss >> tag;
    if(tag != "TREE") continue;
    TreeEvent e;
    iss >> e.gather_src >> e.inject_cycle >> e.num_flits;
    _tree_events.push_back(e);
    _max_cycle = max(_max_cycle, e.inject_cycle + e.num_flits);
  }
}

void TraceTrafficManager::_InjectHopEvent(const HopEvent & e)
{
  int const cl = 0;
  int const time = _time;
  Flit * f = Flit::New();
  f->id = _cur_id++;
  f->pid = _cur_pid++;
  f->watch = false;
  f->subnetwork = 0;
  f->src = e.gather_src;
  f->gather_src = e.gather_src;
  f->ctime = time;
  f->record = false;
  f->cl = cl;
  f->type = Flit::ANY_TYPE;
  f->head = true;
  f->tail = true;
  f->dest = e.dest;
  f->trace_ph = e.final_hop ? 1 : 0;
  f->pri = numeric_limits<int>::max() - time;
  f->vc = -1;
  f->ph = -1;

  _total_in_flight_flits[cl][f->id] = f;
  _partial_packets[e.inject][cl].push_back(f);
}

void TraceTrafficManager::_InjectTreeEvent(const TreeEvent & e)
{
  int const cl = 0;
  int const time = _time;
  for(int i = 0; i < e.num_flits; ++i) {
    Flit * f = Flit::New();
    f->id = _cur_id++;
    f->pid = _cur_pid++;
    f->watch = false;
    f->subnetwork = 0;
    f->src = e.gather_src;
    f->gather_src = e.gather_src;
    f->ctime = time;
    f->record = false;
    f->cl = cl;
    f->type = Flit::ANY_TYPE;
    f->head = true;
    f->tail = true;
    f->dest = e.gather_src;
    f->trace_ph = 100;
    f->pri = numeric_limits<int>::max() - time;
    f->vc = -1;
    f->ph = -1;
    _total_in_flight_flits[cl][f->id] = f;
    _partial_packets[e.gather_src][cl].push_back(f);
  }
}

void TraceTrafficManager::_Inject()
{
  if(_trace_mode == "hop") {
    while(_hop_idx < _hop_events.size() &&
          _hop_events[_hop_idx].cycle == _time) {
      _InjectHopEvent(_hop_events[_hop_idx]);
      ++_hop_idx;
    }
  } else if(_trace_mode == "tree") {
    while(_tree_idx < _tree_events.size() &&
          _tree_events[_tree_idx].inject_cycle == _time) {
      _InjectTreeEvent(_tree_events[_tree_idx]);
      ++_tree_idx;
    }
  }
}

void TraceTrafficManager::_RetireFlit(Flit *f, int dest)
{
  int gs = f->gather_src >= 0 ? f->gather_src : f->src;
  if(f->trace_ph == 1 && gs != dest && gs >= 0 && gs < _nodes && dest >= 0 && dest < _nodes) {
    _recv_count[dest][gs]++;
    _total_received++;
    if(_sim_makespan < 0 || _time > _sim_makespan) {
      _sim_makespan = _time;
    }
  }
  TrafficManager::_RetireFlit(f, dest);
}

bool TraceTrafficManager::_AllGatherComplete() const
{
  if(_total_received < _total_expected) return false;
  for(int d = 0; d < _nodes; ++d) {
    for(int s = 0; s < _nodes; ++s) {
      if(s == d) continue;
      if(_recv_count[d][s] < _msg_flits) return false;
    }
  }
  return true;
}

void TraceTrafficManager::_WriteResult() const
{
  cout << "=== Trace Allgather Simulation ===" << endl;
  cout << "trace_mode: " << _trace_mode << endl;
  cout << "expected_makespan: " << _expected_makespan << endl;
  cout << "sim_makespan: " << _sim_makespan << endl;
  cout << "total_received: " << _total_received << " / " << _total_expected << endl;
#ifdef TRACK_STALLS
  cout << "buffer_full_stalls: " << _stall_buffer_full << endl;
#endif

  if(_result_csv.empty()) return;
  bool hdr = false;
  {
    ifstream chk(_result_csv.c_str());
    hdr = !chk.good() || chk.peek() == ifstream::traits_type::eof();
  }
  ofstream out(_result_csv.c_str(), ios::app);
  if(!out.good()) return;
  if(hdr) {
    out << "trace_mode,expected_makespan,sim_makespan,total_received,total_expected,buffer_full_stalls\n";
  }
  out << _trace_mode << ","
      << _expected_makespan << ","
      << _sim_makespan << ","
      << _total_received << ","
      << _total_expected << ","
#ifdef TRACK_STALLS
      << _stall_buffer_full
#else
      << 0
#endif
      << "\n";
}

bool TraceTrafficManager::_SingleSim()
{
  _sim_state = running;
  int const limit = _max_cycle + _drain_slack + _expected_makespan + 512;

  while(_time < limit) {
    _Step();
    if(_AllGatherComplete() && _total_in_flight_flits[0].empty()) {
      break;
    }
  }

  bool ok = _AllGatherComplete();
  if(!ok) {
    cerr << "Trace sim incomplete at t=" << _time
         << " received=" << _total_received << "/" << _total_expected << endl;
  }
#ifdef TRACK_STALLS
  for(int r = 0; r < _routers; ++r) {
    _stall_buffer_full += _router[0][r]->GetBufferFullStalls(0);
  }
#endif
  _WriteResult();
  _sim_state = draining;
  _drain_time = _time;
  return ok ? 1 : 0;
}
