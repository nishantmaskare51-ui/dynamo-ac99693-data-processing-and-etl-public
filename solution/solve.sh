#!/bin/bash
# Executable entrypoint for the inventory normalization task.
set -e

# Ensure output directory exists
mkdir -p /app/output

# Invoke the core processing logic
python3 /app/run_core.py
