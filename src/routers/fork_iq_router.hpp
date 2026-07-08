#ifndef _FORK_IQ_ROUTER_HPP_
#define _FORK_IQ_ROUTER_HPP_

#include "iq_router.hpp"
#include "fork_table.hpp"
#include <deque>

class ForkIQRouter : public IQRouter {
  ForkTable _fork_table;
  int _fork_failures;
  deque<pair<int, Flit *> > _fork_deferred;

  Flit * _CloneForkFlit(Flit const * f, int dest) const;
  virtual void _PreInputQueuing();

public:
  ForkIQRouter(Configuration const & config, Module *parent,
               string const & name, int id, int inputs, int outputs);
  virtual ~ForkIQRouter();

  int ForkFailures() const { return _fork_failures; }
};

#endif
