#include "fork_iq_router.hpp"
#include <iostream>

using namespace std;

ForkIQRouter::ForkIQRouter(Configuration const & config, Module *parent,
                           string const & name, int id, int inputs, int outputs)
  : IQRouter(config, parent, name, id, inputs, outputs), _fork_failures(0)
{
  string fork_path = config.GetStr("fork_file");
  if(!fork_path.empty() && fork_path != "none") {
    if(!_fork_table.Load(fork_path)) {
      Error("ForkIQRouter: failed to load fork_file " + fork_path);
    }
  }
}

ForkIQRouter::~ForkIQRouter() {
  while(!_fork_deferred.empty()) {
    _fork_deferred.front().second->Free();
    _fork_deferred.pop_front();
  }
}

Flit * ForkIQRouter::_CloneForkFlit(Flit const * f, int dest) const
{
  Flit * c = Flit::New();
  c->type = f->type;
  c->cl = f->cl;
  c->head = true;
  c->tail = true;
  c->ctime = f->ctime;
  c->itime = f->itime;
  c->atime = f->atime;
  c->record = f->record;
  c->src = f->src;
  c->gather_src = f->gather_src;
  c->dest = dest;
  c->pri = f->pri;
  c->hops = f->hops;
  c->watch = f->watch;
  c->subnetwork = f->subnetwork;
  c->intm = f->intm;
  c->ph = f->ph;
  c->trace_ph = 100;
  c->data = f->data;
  c->vc = -1;
  static int _fork_seq = 1 << 20;
  c->id = _fork_seq++;
  c->pid = _fork_seq++;
  return c;
}

void ForkIQRouter::_PreInputQueuing()
{
  if(!_fork_deferred.empty()) {
    pair<int, Flit *> item = _fork_deferred.front();
    _fork_deferred.pop_front();
    _in_queue_flits.insert(item);
  }

  multimap<int, Flit *> keep;
  for(multimap<int, Flit *>::iterator iter = _in_queue_flits.begin();
      iter != _in_queue_flits.end(); ++iter) {
    int const input = iter->first;
    Flit * f = iter->second;
    if(!f || f->trace_ph != 100) {
      keep.insert(*iter);
      continue;
    }
    ForkAction act;
    int const gs = f->gather_src >= 0 ? f->gather_src : f->src;
    if(!_fork_table.Lookup(gs, _id, act)) {
      keep.insert(*iter);
      continue;
    }
    vector<int> outs;
    if(act.eject) outs.push_back(_id);
    for(size_t i = 0; i < act.forwards.size(); ++i) {
      outs.push_back(act.forwards[i]);
    }
    if(outs.empty()) {
      ++_fork_failures;
      f->Free();
      continue;
    }
    f->dest = outs[0];
    f->trace_ph = (outs[0] == (int)_id) ? 0 : 100;
    f->vc = -1;
    keep.insert(make_pair(input, f));
    for(size_t i = 1; i < outs.size(); ++i) {
      Flit * c = _CloneForkFlit(f, outs[i]);
      if(outs[i] == (int)_id) c->trace_ph = 0;
      _fork_deferred.push_back(make_pair(input, c));
    }
  }
  _in_queue_flits.swap(keep);
}
