#include "fork_iq_router.hpp"
#include <iostream>

using namespace std;

ForkIQRouter::ForkIQRouter(Configuration const & config, Module *parent,
                           string const & name, int id, int inputs, int outputs)
  : IQRouter(config, parent, name, id, inputs, outputs), _fork_failures(0)
{
  string fork_path = config.GetStr("fork_file");
  if(!fork_path.empty()) {
    if(!_fork_table.Load(fork_path)) {
      Error("ForkIQRouter: failed to load fork_file " + fork_path);
    }
  }
}

ForkIQRouter::~ForkIQRouter() {}

Flit * ForkIQRouter::_CloneForkFlit(Flit const * f, int dest) const
{
  Flit * c = Flit::New();
  c->type = f->type;
  c->vc = f->vc;
  c->cl = f->cl;
  c->head = f->head;
  c->tail = f->tail;
  c->ctime = f->ctime;
  c->itime = f->itime;
  c->atime = f->atime;
  c->id = f->id;
  c->pid = f->pid;
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
  c->trace_ph = 0;
  c->data = f->data;
  c->vc = -1;
  return c;
}

void ForkIQRouter::_PreInputQueuing()
{
  multimap<int, Flit *> expanded;
  for(multimap<int, Flit *>::iterator iter = _in_queue_flits.begin();
      iter != _in_queue_flits.end(); ++iter) {
    int const input = iter->first;
    Flit * f = iter->second;
    if(!f || f->trace_ph != 100) {
      expanded.insert(*iter);
      continue;
    }
    ForkAction act;
    if(!_fork_table.Lookup(f->gather_src >= 0 ? f->gather_src : f->src, _id, act)) {
      expanded.insert(*iter);
      continue;
    }
    bool ok = true;
    (void)ok;
    vector<Flit *> clones;
    if(act.eject) {
      Flit * e = _CloneForkFlit(f, _id);
      e->trace_ph = 0;
      clones.push_back(e);
    }
    for(size_t i = 0; i < act.forwards.size(); ++i) {
      Flit * c = _CloneForkFlit(f, act.forwards[i]);
      c->trace_ph = 100;
      clones.push_back(c);
    }
    if(clones.empty()) {
      ++_fork_failures;
      f->Free();
      continue;
    }
    for(size_t i = 0; i < clones.size(); ++i) {
      clones[i]->id = f->id + (int)((i + 1) * 1000000);
      clones[i]->pid = f->pid + (int)((i + 1) * 1000000);
      clones[i]->vc = -1;
      expanded.insert(make_pair(input, clones[i]));
    }
    f->Free();
  }
  _in_queue_flits.swap(expanded);
}
