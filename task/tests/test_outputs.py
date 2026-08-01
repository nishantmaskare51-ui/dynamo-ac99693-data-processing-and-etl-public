"""
Verifier for the inventory-normalization task.

Structured around pytest fixtures rather than free functions: a session-scoped
`public_result` fixture runs once and is shared, an `oracle_answers` fixture
loads both ground-truth sets, and each test asserts one property of the
agent's output. Record-level comparison goes through a canonical hash
(`_fingerprint`) instead of a positional diff, so the checks are order- and
whitespace-insensitive by construction rather than by explicit sorting logic.
"""
import hashlib
import json
import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DATA = "/app/data/inventory_export.dat"
OUT_OK = "/app/output/inventory_normalized.jsonl"
OUT_BAD = "/app/output/rejected_rows.log"
RUN_ENTRY = "/app/run.sh"

REQUIRED_KEYS = {"store_id", "sku", "quantity", "export_ts", "extra_attrs"}


def _fingerprint(record: dict) -> str:
    """Order-independent identity for a normalized record."""
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _load_multiset(path: str):
    """Return {fingerprint: record} for every JSON object in a jsonl file."""
    table = {}
    if not os.path.exists(path):
        return table
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            table[_fingerprint(rec)] = rec
    return table


def _nonblank_line_count(path: str) -> int:
    if not os.path.exists(path):
        return 0
    with open(path, "rb") as fh:
        return sum(1 for ln in fh if ln.strip())


class Fixture:
    """Bundles a data file with its independently-computed ground truth."""

    def __init__(self, name: str, data_path: str, expected_dir: str):
        self.name = name
        self.data_path = data_path
        with open(os.path.join(expected_dir, "counts.json")) as fh:
            self.counts = json.load(fh)
        self.expected_records = _load_multiset(
            os.path.join(expected_dir, "expected_output.jsonl")
        )


@pytest.fixture(scope="session")
def public_fixture():
    return Fixture(
        "public",
        APP_DATA,
        os.path.join(HERE, "expected"),
    )


@pytest.fixture(scope="session")
def hidden_fixture():
    return Fixture(
        "hidden",
        os.path.join(HERE, "hidden", "inventory_export_hidden.dat"),
        os.path.join(HERE, "hidden"),
    )


def _run_against(fixture: Fixture):
    """Point /app/data/inventory_export.dat at `fixture` and invoke run.sh."""
    if fixture.data_path != APP_DATA:
        shutil.copyfile(fixture.data_path, APP_DATA)
    for path in (OUT_OK, OUT_BAD):
        if os.path.exists(path):
            os.remove(path)
    result = subprocess.run([RUN_ENTRY], capture_output=True, text=True, timeout=120)
    return result


class TestAgentInterface:
    def test_run_entrypoint_exists_and_is_executable(self):
        assert os.path.exists(RUN_ENTRY), "Agent did not create /app/run.sh"
        assert os.access(RUN_ENTRY, os.X_OK), "/app/run.sh exists but is not executable"


class TestPublicFixture:
    """
    Checks against the fixture the agent was actually given
    (/app/data/inventory_export.dat, output already produced by the agent's
    own run -- no re-invocation needed here).
    """

    def test_output_file_present(self):
        assert os.path.exists(OUT_OK), f"{OUT_OK} was never created"

    def test_every_expected_record_is_present(self, public_fixture):
        got = _load_multiset(OUT_OK)
        missing = set(public_fixture.expected_records) - set(got)
        assert not missing, (
            f"{len(missing)} expected record(s) missing from output, e.g. "
            f"{next(iter([public_fixture.expected_records[k] for k in list(missing)[:1]]), None)}"
        )

    def test_no_extraneous_or_incorrect_records(self, public_fixture):
        got = _load_multiset(OUT_OK)
        extra = set(got) - set(public_fixture.expected_records)
        assert not extra, (
            f"{len(extra)} record(s) in output do not match any expected "
            f"record (wrong values, bad dedup, or fabricated rows), e.g. "
            f"{next(iter([got[k] for k in list(extra)[:1]]), None)}"
        )

    def test_output_schema_is_well_formed(self):
        got = _load_multiset(OUT_OK)
        for rec in got.values():
            assert REQUIRED_KEYS.issubset(rec.keys()), f"record missing keys: {rec}"
            assert rec["quantity"] is None or isinstance(rec["quantity"], int), (
                f"quantity must be int or null, got {rec['quantity']!r}"
            )
            assert isinstance(rec["extra_attrs"], dict), (
                f"extra_attrs must be a JSON object, got {rec['extra_attrs']!r}"
            )

    def test_rejected_line_count(self, public_fixture):
        n = _nonblank_line_count(OUT_BAD)
        expected = public_fixture.counts["n_rejected_total"]
        assert n == expected, f"expected {expected} rejected line(s), found {n}"


class TestGeneralization:
    """
    Re-runs the agent's own /app/run.sh against a second, previously-unseen
    fixture and checks the result independently. A solution that memorized
    or hardcoded answers for the public fixture will fail here even though
    it passes every TestPublicFixture check above.
    """

    def test_solution_reproduces_correctly_on_unseen_data(self, hidden_fixture):
        assert os.path.exists(RUN_ENTRY), "/app/run.sh must exist for this check"
        backup = APP_DATA + ".bak"
        shutil.copyfile(APP_DATA, backup)
        try:
            result = _run_against(hidden_fixture)
            assert result.returncode == 0, (
                f"/app/run.sh failed on the hidden fixture "
                f"(exit {result.returncode}).\nstderr: {result.stderr}"
            )
            got = _load_multiset(OUT_OK)
            expected = hidden_fixture.expected_records
            assert got.keys() == expected.keys(), (
                "hidden-fixture output does not match its independent ground "
                f"truth (got {len(got)} records, expected {len(expected)})"
            )
            n_rejected = _nonblank_line_count(OUT_BAD)
            assert n_rejected == hidden_fixture.counts["n_rejected_total"], (
                f"hidden fixture: expected {hidden_fixture.counts['n_rejected_total']} "
                f"rejected line(s), found {n_rejected}"
            )
        finally:
            shutil.copyfile(backup, APP_DATA)
            os.remove(backup)
