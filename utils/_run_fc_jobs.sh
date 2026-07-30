#!/bin/bash
# Detached launcher for full-cover probe + e2e Pareto (survive agent disconnects).
set -euo pipefail
cd /home/luke/workspace/booksim2
export PYTHONPATH=utils
rm -f /tmp/pg_full_cover.log /tmp/pg_e2e_fc.log /tmp/pg_fc_done.flag

python3 -u utils/pg_full_cover_probe.py --n-per-cell 1 \
  > /tmp/pg_full_cover.log 2>&1
echo "PROBE_DONE $(date -Iseconds)" >> /tmp/pg_full_cover.log

python3 -u utils/dse_pg_e2e_pareto.py --quick \
  > /tmp/pg_e2e_fc.log 2>&1
echo "E2E_DONE $(date -Iseconds)" >> /tmp/pg_e2e_fc.log

python3 -u utils/gen_pg_e2e_pareto_plot.py >> /tmp/pg_e2e_fc.log 2>&1
PYTHONPATH=utils python3 -u utils/gen_pg_alltoall_report.py >> /tmp/pg_e2e_fc.log 2>&1

echo "ALL_DONE $(date -Iseconds)" | tee /tmp/pg_fc_done.flag
