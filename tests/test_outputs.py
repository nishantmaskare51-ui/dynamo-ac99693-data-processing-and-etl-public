"""
Verifier for the inventory-normalization task.

Structured around pytest fixtures rather than free functions: a session-scoped
`public_result` fixture runs once and is shared, an `oracle_answers` fixture
loads both ground-truth sets, and each test asserts one property of the
agent's output. Record-level comparison goes through a canonical hash
(`_fingerprint`) instead of a positional diff, so the checks are order- and
whitespace-insensitive by construction rather than by explicit sorting logic.

Security note: /app/run.sh is agent-authored and untrusted. During
TestGeneralization it is re-executed after the hidden fixture's ground-truth
files (expected_output.jsonl, counts.json, expected_rejected.log) have already
been read into memory by the `hidden_fixture` fixture -- but those files still
physically exist on disk at that point unless we remove them first, and
nothing stops run.sh from reading them directly instead of solving the task.
`_run_against` therefore temporarily renames the hidden fixture's answer-key
files out of the way for the duration of the subprocess call, and restores
them in a `finally` block. Output paths are also resolved with `os.path.realpath`
before being opened, so a symlinked output can't point at those answer-key
files (or anywhere else) to fake a correct result.
"""
import hashlib
import json
import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DATA = "/app/data/inventory_export.dat"
APP_OUTPUT_DIR = "/app/output"
OUT_OK = "/app/output/inventory_normalized.jsonl"
OUT_BAD = "/app/output/rejected_rows.log"
RUN_ENTRY = "/app/run.sh"

REQUIRED_KEYS = {"store_id", "sku", "quantity", "export_ts", "extra_attrs"}


def _safe_path(path: str, restrict_to_output: bool = True) -> str:
    """Resolve symlinks. When restrict_to_output is True (agent-controlled
    output files), also reject anything that escapes /app/output -- this
    guard does not apply to the verifier's own trusted fixture/ground-truth
    files, which legitimately live elsewhere (under /tests)."""
    real = os.path.realpath(path)
    if not restrict_to_output:
        return real
    real_output_dir = os.path.realpath(APP_OUTPUT_DIR)
    if os.path.islink(path) or not (
        real == real_output_dir or real.startswith(real_output_dir + os.sep)
    ):
        raise AssertionError(
            f"{path} is a symlink or resolves outside {APP_OUTPUT_DIR} "
            f"(resolved to {real}) -- refusing to read it as agent output"
        )
    return real


def _fingerprint(record: dict) -> str:
    """Order-independent identity for a normalized record."""
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _load_multiset(path: str, restrict_to_output: bool = True):
    """Return {fingerprint: record} for every JSON object in a jsonl file."""
    table = {}
    if not os.path.exists(path):
        return table
    real = _safe_path(path, restrict_to_output)
    with open(real) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            table[_fingerprint(rec)] = rec
    return table


def _nonblank_line_count(path: str, restrict_to_output: bool = True) -> int:
    if not os.path.exists(path):
        return 0
    real = _safe_path(path, restrict_to_output)
    with open(real, "rb") as fh:
        return sum(1 for ln in fh if ln.strip())


def _read_lines(path: str, restrict_to_output: bool = True):
    """Non-blank, newline-stripped lines from a text file."""
    if not os.path.exists(path):
        return []
    real = _safe_path(path, restrict_to_output)
    with open(real, encoding="utf-8", errors="replace") as fh:
        return [ln.rstrip("\n") for ln in fh if ln.strip()]


class Fixture:
    """Bundles a data file with its independently-computed ground truth."""

    def __init__(self, name: str, data_path: str, expected_dir: str):
        self.name = name
        self.data_path = data_path
        self.expected_dir = expected_dir
        self.counts_path = os.path.join(expected_dir, "counts.json")
        self.expected_output_path = os.path.join(expected_dir, "expected_output.jsonl")
        self.expected_rejected_path = os.path.join(expected_dir, "expected_rejected.log")
        with open(self.counts_path) as fh:
            self.counts = json.load(fh)
        self.expected_records = _load_multiset(
            self.expected_output_path, restrict_to_output=False
        )
        with open(self.expected_rejected_path, encoding="utf-8") as fh:
            self.expected_rejected_lines = {
                ln.rstrip("\n") for ln in fh if ln.strip()
            }
        # Files whose presence on disk would let an untrusted run.sh read the
        # answer directly instead of computing it; hidden during _run_against.
        self.answer_key_paths = [
            self.counts_path,
            self.expected_output_path,
            self.expected_rejected_path,
        ]


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
    """Point /app/data/inventory_export.dat at `fixture` and invoke run.sh,
    with the fixture's answer-key files hidden from disk for the duration of
    the call so an untrusted run.sh cannot read them directly."""
    if fixture.data_path != APP_DATA:
        shutil.copyfile(fixture.data_path, APP_DATA)
    for path in (OUT_OK, OUT_BAD):
        if os.path.exists(path) or os.path.islink(path):
            os.remove(path)

    hidden_aside = []
    for p in fixture.answer_key_paths:
        if os.path.exists(p):
            tmp = p + ".verifier-hidden"
            os.rename(p, tmp)
            hidden_aside.append((p, tmp))
    try:
        result = subprocess.run(
            [RUN_ENTRY], capture_output=True, text=True, timeout=120
        )
    finally:
        for original, tmp in hidden_aside:
            os.rename(tmp, original)
    return result


class TestAgentInterface:
    def test_run_entrypoint_exists_and_is_executable(self):
        assert os.path.exists(RUN_ENTRY), "Agent did not create /app/run.sh"
        assert os.access(RUN_ENTRY, os.X_OK), "/app/run.sh exists but is not executable"


class TestPublicFixture:
    """
    Checks against the fixture the agent was actually given
    (/app/data/inventory_export.dat). The agent's own run.sh is re-invoked
    here too (rather than trusting whatever output happened to be on disk
    from the agent's own solve-time run), with the answer key hidden the
    same way as in TestGeneralization, so a hardcoded or memorized public
    output can't slip through unverified.
    """

    @pytest.fixture(scope="class", autouse=True)
    def _rerun(self, public_fixture):
        backup = APP_DATA + ".bak"
        shutil.copyfile(APP_DATA, backup)
        try:
            result = _run_against(public_fixture)
            assert result.returncode == 0, (
                f"/app/run.sh failed on the public fixture "
                f"(exit {result.returncode}).\nstderr: {result.stderr}"
            )
            yield
        finally:
            shutil.copyfile(backup, APP_DATA)
            os.remove(backup)

    def test_output_file_present(self):
        assert os.path.exists(OUT_OK), f"{OUT_OK} was never created"

    def test_valid_record_count_matches_exactly(self, public_fixture):
        lines = _read_lines(OUT_OK)
        expected_n = public_fixture.counts["n_valid_unique"]
        assert len(lines) == expected_n, (
            f"expected exactly {expected_n} line(s) in {OUT_OK}, found "
            f"{len(lines)} -- duplicate or extra output lines are not allowed "
            f"even if they fingerprint-match an expected record"
        )

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
            assert set(rec.keys()) == REQUIRED_KEYS, (
                f"record must have exactly these keys {sorted(REQUIRED_KEYS)}, "
                f"got {sorted(rec.keys())}"
            )
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

    def test_rejected_content_matches_exactly(self, public_fixture):
        got_lines = set(_read_lines(OUT_BAD))
        expected_lines = public_fixture.expected_rejected_lines
        missing = expected_lines - got_lines
        extra = got_lines - expected_lines
        assert not missing and not extra, (
            f"rejected_rows.log content does not exactly match the invalid "
            f"input rows: {len(missing)} expected line(s) missing, "
            f"{len(extra)} unexpected line(s) present"
        )


class TestGeneralization:
    """
    Re-runs the agent's own /app/run.sh against a second, previously-unseen
    fixture and checks the result independently. A solution that memorized
    or hardcoded answers for the public fixture will fail here even though
    it passes every TestPublicFixture check above -- and because the answer
    key is hidden from disk during the run (see _run_against), a solution
    that tries to read it directly instead of computing it will fail too.
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
            lines = _read_lines(OUT_OK)
            expected_n = hidden_fixture.counts["n_valid_unique"]
            assert len(lines) == expected_n, (
                f"hidden fixture: expected exactly {expected_n} line(s) in "
                f"{OUT_OK}, found {len(lines)}"
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
            got_rejected = set(_read_lines(OUT_BAD))
            expected_rejected = hidden_fixture.expected_rejected_lines
            missing = expected_rejected - got_rejected
            extra = got_rejected - expected_rejected
            assert not missing and not extra, (
                f"hidden fixture: rejected_rows.log content does not exactly "
                f"match the invalid input rows: {len(missing)} expected line(s) "
                f"missing, {len(extra)} unexpected line(s) present"
            )
        finally:
            shutil.copyfile(backup, APP_DATA)
            os.remove(backup)
