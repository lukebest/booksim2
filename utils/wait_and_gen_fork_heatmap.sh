#!/bin/bash
# Wait for sweep_fork_msg_size to finish, then generate heatmap report.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$ROOT/results/fork_msg_size.log"
JSON="$ROOT/results/buffer_pareto_msg_size.json"

echo "Monitoring sweep (expect m=2,3,4,5 in $JSON)..."
while true; do
  if grep -q "Wrote.*buffer_pareto_msg_size" "$LOG" 2>/dev/null; then
    echo "Sweep finished (log marker)."
    break
  fi
  if python3 -c "
import json, sys
from pathlib import Path
p=Path('$JSON')
if not p.exists(): sys.exit(1)
d=json.loads(p.read_text())
need=set(str(m) for m in d.get('msg_sizes',[2,3,4,5]))
have=set(d.get('by_msg_size',{}).keys())
sys.exit(0 if need<=have else 1)
" 2>/dev/null; then
    echo "All msg sizes present in JSON."
    break
  fi
  done=$(grep -c "done m=" "$LOG" 2>/dev/null || true)
  last=$(grep "=== m=" "$LOG" 2>/dev/null | tail -1 || true)
  echo "$(date -Iseconds)  completed_blocks=$done  $last"
  sleep 120
done

cd "$ROOT/utils"
python3 gen_fork_msg_size_heatmap.py
python3 gen_fork_msg_size_report.py
