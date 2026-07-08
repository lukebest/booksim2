// Trace-driven allgather traffic manager (Route A hop / Route B tree via TM fork walk).

#include "tracetrafficmanager.hpp"
#include <algorithm>
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
  _mx = config.GetInt("mesh_x");
  _my = config.GetInt("mesh_y");
  if(_mx <= 0) _mx = 6;
  if(_my <= 0) _my = 8;
  _h_lat = config.GetInt("h_latency");
  _v_lat = config.GetInt("v_latency");
  if(_h_lat <= 0) _h_lat = 4;
  if(_v_lat <= 0) _v_lat = 6;
  if(_drain_slack <= 0) _drain_slack = 256;
  _trace_completion = config.GetStr("trace_completion");
  if(_trace_completion.empty()) _trace_completion = "allgather";
  _trace_makespan_lb = 0;
  _max_cycle = 0;

  _recv_count.assign(_nodes, vector<int>(_nodes, 0));
  _total_expected = _nodes * (_nodes - 1) * _msg_flits;

  if(_trace_mode == "hop") {
    _LoadHopTrace(_trace_file);
  } else if(_trace_mode == "tree") {
    _LoadTreeTrace(_trace_file);
    if(!_fork_file.empty() && _fork_file != "none") {
      if(!_fork_table.Load(_fork_file)) {
        Error("Failed to load fork_file: " + _fork_file);
      }
    }
  } else {
    Error("Unknown trace_mode: " + _trace_mode);
  }
}

TraceTrafficManager::~TraceTrafficManager() {}

int TraceTrafficManager::_EdgeLat(int u, int v) const
{
  int uy = u / _mx, ux = u % _mx;
  int vy = v / _mx, vx = v % _mx;
  return (ux != vx) ? _h_lat : _v_lat;
}

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
    int is_final = 0;
    iss >> e.inject >> e.dest >> e.cycle >> e.gather_src >> flit_idx >> is_final;
    e.final_hop = is_final;
    _hop_events.push_back(e);
    _max_cycle = max(_max_cycle, e.cycle);
    int const lat = _EdgeLat(e.inject, e.dest);
    _trace_makespan_lb = max(_trace_makespan_lb, e.cycle + lat + 2);
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
  }
  sort(_tree_events.begin(), _tree_events.end(),
       [](const TreeEvent & a, const TreeEvent & b) {
         if(a.inject_cycle != b.inject_cycle) {
           return a.inject_cycle < b.inject_cycle;
         }
         return a.gather_src < b.gather_src;
       });
  for(size_t i = 0; i < _tree_events.size(); ++i) {
    TreeEvent const & e = _tree_events[i];
    _max_cycle = max(_max_cycle, e.inject_cycle + e.num_flits);
  }
}

void TraceTrafficManager::_InjectHopEvent(const HopEvent & e, int final_hop)
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
  f->trace_ph = final_hop ? 1 : 0;
  f->pri = numeric_limits<int>::max() - time;
  f->vc = -1;
  f->ph = -1;
  _total_in_flight_flits[cl][f->id] = f;
  _partial_packets[e.inject][cl].push_back(f);
}

void TraceTrafficManager::_EnqueueTreeTokens(const TreeEvent & e)
{
  for(int i = 0; i < e.num_flits; ++i) {
    TreeToken tok;
    tok.gather_src = e.gather_src;
    tok.node = e.gather_src;
    tok.ready_cycle = e.inject_cycle + i;
    _tree_tokens.push_back(tok);
  }
}

void TraceTrafficManager::_AdvanceTreeTokens()
{
  vector<TreeToken> next;
  for(size_t i = 0; i < _tree_tokens.size(); ++i) {
    TreeToken tok = _tree_tokens[i];
    if(tok.ready_cycle != _time) {
      next.push_back(tok);
      continue;
    }
    ForkAction act;
    if(!_fork_table.Lookup(tok.gather_src, tok.node, act)) {
      continue;
    }
    if(act.eject && tok.node != tok.gather_src) {
      _recv_count[tok.node][tok.gather_src]++;
      _total_received++;
      if(_sim_makespan < 0 || _time > _sim_makespan) _sim_makespan = _time;
    }
    for(size_t j = 0; j < act.forwards.size(); ++j) {
      int nb = act.forwards[j];
      HopEvent hop;
      hop.inject = tok.node;
      hop.dest = nb;
      hop.cycle = _time;
      hop.gather_src = tok.gather_src;
      hop.final_hop = 0;
      _InjectHopEvent(hop, 0);
      TreeToken nt;
      nt.gather_src = tok.gather_src;
      nt.node = nb;
      nt.ready_cycle = _time + _EdgeLat(tok.node, nb);
      next.push_back(nt);
    }
    if(act.eject && tok.node == tok.gather_src && act.forwards.empty()) {
      // source-only fork with no forward; nothing else to do
    }
  }
  _tree_tokens.swap(next);
}

void TraceTrafficManager::_Inject()
{
  if(_trace_mode == "hop") {
    while(_hop_idx < _hop_events.size() &&
          _hop_events[_hop_idx].cycle == _time) {
      const HopEvent & e = _hop_events[_hop_idx];
      _InjectHopEvent(e, e.final_hop);
      ++_hop_idx;
    }
  } else if(_trace_mode == "tree") {
    while(_tree_idx < _tree_events.size() &&
          _tree_events[_tree_idx].inject_cycle == _time) {
      _EnqueueTreeTokens(_tree_events[_tree_idx]);
      ++_tree_idx;
    }
    _AdvanceTreeTokens();
  }
}

void TraceTrafficManager::_RetireFlit(Flit *f, int dest)
{
  int gs = f->gather_src >= 0 ? f->gather_src : f->src;
  bool count = false;
  if(_trace_mode == "hop") {
    count = (f->trace_ph == 1);
  } else {
    count = false;
  }
  if(_trace_completion == "hops") {
    if(_sim_makespan < 0 || _time > _sim_makespan) {
      _sim_makespan = _time;
    }
  }
  if(count && gs != dest && gs >= 0 && gs < _nodes && dest >= 0 && dest < _nodes) {
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
  int const limit = _max_cycle + _drain_slack + _expected_makespan * 16 + 8192;

  while(_time < limit) {
    _Step();
    bool const drained = _total_in_flight_flits[0].empty() && _tree_tokens.empty();
    bool const trees_done = (_trace_mode != "tree") || (_tree_idx >= _tree_events.size());

    if(_trace_completion == "hops") {
      if(_trace_mode == "hop" && _hop_idx >= _hop_events.size()) {
        if(_sim_makespan >= 0 && _time >= _sim_makespan + 32) {
          break;
        }
        if(_trace_makespan_lb > 0 && _time >= _trace_makespan_lb + 32) {
          if(_sim_makespan < 0) _sim_makespan = _trace_makespan_lb;
          break;
        }
      }
      continue;
    }

    if(_AllGatherComplete() && drained && trees_done) {
      break;
    }
  }

  if(_sim_makespan < 0 && _trace_completion == "hops" && _trace_makespan_lb > 0) {
    _sim_makespan = _trace_makespan_lb;
  }
  if(_sim_makespan < 0 && _total_received > 0) {
    _sim_makespan = _time;
  }
#ifdef TRACK_STALLS
  for(int r = 0; r < _routers; ++r) {
    _stall_buffer_full += _router[0][r]->GetBufferFullStalls(0);
  }
#endif
  _WriteResult();
  if(_trace_completion != "hops" && !_AllGatherComplete()) {
    cout << "WARNING: allgather incomplete at cycle " << _time << endl;
  }
  _sim_state = draining;
  _drain_time = _time;
  return _AllGatherComplete() ? 1 : 0;
}
