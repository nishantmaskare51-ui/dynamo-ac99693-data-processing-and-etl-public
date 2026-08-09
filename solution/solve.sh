#!/bin/bash
set -euo pipefail

mkdir -p /app/output

cp /solution/solve_core.py /app/run_core.py

cat > /app/run.sh << 'RUNSH'
#!/bin/bash
set -euo pipefail
python3 /app/run_core.py
RUNSH

chmod +x /app/run.sh

/app/run.sh
