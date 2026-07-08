#ifndef _FORK_TABLE_HPP_
#define _FORK_TABLE_HPP_

#include <map>
#include <string>
#include <utility>
#include <vector>

struct ForkAction {
  bool eject;
  std::vector<int> forwards;
};

class ForkTable {
public:
  bool Load(const std::string & path);
  bool Lookup(int gather_src, int node, ForkAction & out) const;
  int NumSources() const { return (int)_by_src.size(); }

private:
  std::map<std::pair<int, int>, ForkAction> _entries;
  std::map<int, int> _by_src;
};

#endif
