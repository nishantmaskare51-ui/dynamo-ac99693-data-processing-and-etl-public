#!/bin/bash
# Verifier entry point. Runs the pytest suite against the agent's outputs
# and writes 1/0 to /logs/verifier/reward.txt. All dependencies (pytest,
# pytest-json-ctrf) are baked into environment/Dockerfile -- nothing is
# installed here.
set -uo pipefail

mkdir -p /logs/verifier
cd /tests

pytest test_outputs.py -v --ctrf=/logs/verifier/ctrf.json
status=$?

if [ "$status" -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi

exit 0
