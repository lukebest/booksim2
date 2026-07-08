// Fork forwarding table loader for Route B allgather.

#include "fork_table.hpp"
#include <fstream>
#include <iostream>
#include <sstream>

using namespace std;

bool ForkTable::Load(const string & path)
{
  _entries.clear();
  _by_src.clear();
  ifstream in(path.c_str());
  if(!in.good()) {
    cerr << "ForkTable: cannot open " << path << endl;
    return false;
  }
  string line;
  while(getline(in, line)) {
    if(line.empty() || line[0] == '#') continue;
    istringstream iss(line);
    string tag;
    iss >> tag;
    if(tag != "FORK") continue;
    int gather_src, node, eject, nf;
    iss >> gather_src >> node >> eject >> nf;
    ForkAction act;
    act.eject = eject != 0;
    for(int i = 0; i < nf; ++i) {
      int nb;
      iss >> nb;
      act.forwards.push_back(nb);
    }
    _entries[make_pair(gather_src, node)] = act;
    _by_src[gather_src] = 1;
  }
  return true;
}

bool ForkTable::Lookup(int gather_src, int node, ForkAction & out) const
{
  map<pair<int,int>, ForkAction>::const_iterator it =
    _entries.find(make_pair(gather_src, node));
  if(it == _entries.end()) return false;
  out = it->second;
  return true;
}
