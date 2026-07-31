"""
Verifies the agent's inventory-normalization solution.

Checks (see instruction.md for the full spec the agent was given):
  1. inventory_normalized.jsonl exists and matches the canonical ground truth
     exactly (schema, values, deduplication) for the public fixture the agent
     was given at /app/data/inventory_export.dat.
  2. rejected_rows.log contains exactly the expected number of rejected rows.
  3. The agent's program is runnable via /app/run.sh with no arguments and
     genuinely re-derives correct output when the input data changes -- this
     is checked by swapping in a hidden fixture (never seen by the agent) and
     re-running /app/run.sh, then diffing against a second, independent
     ground truth. This specifically catches hardcoded/overfit solutions.
"""
import json
import os
import shutil
import subprocess

APP_DATA = "/app/data/inventory_export.dat"
APP_OUTPUT_JSONL = "/app/output/inventory_normalized.jsonl"
APP_OUTPUT_REJECTED = "/app/output/rejected_rows.log"
RUN_SH = "/app/run.sh"

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
EXPECTED_PUBLIC_JSONL = os.path.join(TESTS_DIR, "expected", "expected_output.jsonl")
EXPECTED_PUBLIC_COUNTS = os.path.join(TESTS_DIR, "expected", "counts.json")
HIDDEN_FIXTURE = os.path.join(TESTS_DIR, "hidden", "inventory_export_hidden.dat")
EXPECTED_HIDDEN_JSONL = os.path.join(TESTS_DIR, "hidden", "expected_output.jsonl")
EXPECTED_HIDDEN_COUNTS = os.path.join(TESTS_DIR, "hidden", "counts.json")


def _load_jsonl_sorted(path):
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return sorted(records, key=lambda r: (r.get("store_id", ""), r.get("sku", ""), r.get("export_ts", "")))


def _count_nonblank_lines(path):
    if not os.path.exists(path):
        return 0
    with open(path, "rb") as f:
        return sum(1 for line in f if line.strip())


def test_normalized_output_exists():
    """The agent must produce the normalized JSONL output file."""
    assert os.path.exists(APP_OUTPUT_JSONL), (
        f"Expected output file {APP_OUTPUT_JSONL} was not created."
    )


def test_normalized_output_matches_ground_truth():
    """
    Every valid record (schema, quantity nulls, ISO-8601 dates, deduplication,
    parsed extra_attrs) must exactly match the canonical ground truth for the
    public fixture, regardless of record order.
    """
    assert os.path.exists(APP_OUTPUT_JSONL), "Normalized output file missing."
    got = _load_jsonl_sorted(APP_OUTPUT_JSONL)
    expected = _load_jsonl_sorted(EXPECTED_PUBLIC_JSONL)
    assert len(got) == len(expected), (
        f"Expected {len(expected)} valid records, got {len(got)}."
    )
    for g, e in zip(got, expected):
        assert g == e, f"Record mismatch.\n  got:      {g}\n  expected: {e}"


def test_rejected_row_count_matches():
    """
    rejected_rows.log must contain exactly the expected number of malformed/
    truncated rows -- neither more (over-rejecting valid data) nor fewer
    (silently accepting corrupt data).
    """
    with open(EXPECTED_PUBLIC_COUNTS) as f:
        counts = json.load(f)
    got_count = _count_nonblank_lines(APP_OUTPUT_REJECTED)
    assert got_count == counts["n_rejected_total"], (
        f"Expected {counts['n_rejected_total']} rejected rows, got {got_count}."
    )


def test_agent_program_is_runnable_via_run_sh():
    """The agent must provide an executable /app/run.sh with no arguments."""
    assert os.path.exists(RUN_SH), "/app/run.sh was not created by the agent."
    assert os.access(RUN_SH, os.X_OK), "/app/run.sh exists but is not executable."


def test_generalizes_to_hidden_fixture():
    """
    Swap in a second, previously-unseen fixture (different data, same
    corruption patterns) and re-run /app/run.sh. A solution that hardcoded
    or memorized the public fixture's answers will fail this check even
    though it passed the public checks above.
    """
    assert os.path.exists(RUN_SH), "/app/run.sh must exist to run this check."

    backup_path = APP_DATA + ".public_backup"
    shutil.copyfile(APP_DATA, backup_path)
    try:
        shutil.copyfile(HIDDEN_FIXTURE, APP_DATA)
        if os.path.exists(APP_OUTPUT_JSONL):
            os.remove(APP_OUTPUT_JSONL)
        if os.path.exists(APP_OUTPUT_REJECTED):
            os.remove(APP_OUTPUT_REJECTED)

        result = subprocess.run([RUN_SH], capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, (
            f"/app/run.sh exited with code {result.returncode} on the hidden "
            f"fixture.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        assert os.path.exists(APP_OUTPUT_JSONL), (
            "Normalized output file missing after re-running on hidden fixture."
        )
        got = _load_jsonl_sorted(APP_OUTPUT_JSONL)
        expected = _load_jsonl_sorted(EXPECTED_HIDDEN_JSONL)
        assert len(got) == len(expected), (
            f"Hidden fixture: expected {len(expected)} valid records, got {len(got)}."
        )
        for g, e in zip(got, expected):
            assert g == e, f"Hidden fixture record mismatch.\n  got: {g}\n  expected: {e}"

        with open(EXPECTED_HIDDEN_COUNTS) as f:
            hidden_counts = json.load(f)
        got_rejected = _count_nonblank_lines(APP_OUTPUT_REJECTED)
        assert got_rejected == hidden_counts["n_rejected_total"], (
            f"Hidden fixture: expected {hidden_counts['n_rejected_total']} "
            f"rejected rows, got {got_rejected}."
        )
    finally:
        shutil.copyfile(backup_path, APP_DATA)
        os.remove(backup_path)
