#!/usr/bin/env python3
"""
Reference (oracle) implementation.

Reads /app/data/inventory_export.dat and writes:
  /app/output/inventory_normalized.jsonl
  /app/output/rejected_rows.log

Design note: this parses the pipe-delimited feed using the stdlib `csv` module
with a custom Dialect (rather than manual str.split), and represents each
candidate record as a small state machine (RecordBuilder) that accumulates
failures instead of raising early -- this keeps every field's validation
independent so a single bad field doesn't mask what else is wrong with a row.
"""
import csv
import io
import json
import re
import datetime

SRC = "/app/data/inventory_export.dat"
DST_OK = "/app/output/inventory_normalized.jsonl"
DST_BAD = "/app/output/rejected_rows.log"

COLUMNS = ("store_id", "sku", "quantity", "export_ts", "extra_attrs")
NULLISH = frozenset({"", "NULL", "N/A", "-1"})

_EPOCH_RE = re.compile(r"^\d{9,10}$")
_MDY_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_DMY_RE = re.compile(r"^(\d{1,2})-(\d{1,2})-(\d{4})$")


class PipeDialect(csv.Dialect):
    delimiter = "|"
    quoting = csv.QUOTE_NONE
    lineterminator = "\n"
    escapechar = None


def sniff_and_decode(raw: bytes) -> str:
    """utf-8 first; cp1252 as the fallback encoding for legacy POS exports."""
    for codec in ("utf-8", "cp1252"):
        try:
            return raw.decode(codec)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


class RecordBuilder:
    """Accumulates one row's parsed fields; ok() is False if anything failed."""

    def __init__(self, fields):
        self.fields = fields
        self.errors = []
        self.store_id = None
        self.sku = None
        self.quantity = None
        self.export_ts = None
        self.attrs = None

    def ok(self):
        return not self.errors

    def run(self):
        if len(self.fields) != len(COLUMNS):
            self.errors.append("field_count")
            return self
        store_id, sku, qty_raw, date_raw, attrs_raw = self.fields
        self.store_id, self.sku = store_id, sku
        self._resolve_quantity(qty_raw)
        self._resolve_timestamp(date_raw)
        self._resolve_attrs(attrs_raw)
        return self

    def _resolve_quantity(self, raw):
        raw = raw.strip()
        if raw in NULLISH:
            self.quantity = None
            return
        try:
            self.quantity = int(raw)
        except ValueError:
            self.errors.append("quantity")

    def _resolve_timestamp(self, raw):
        raw = raw.strip()
        m = _EPOCH_RE.match(raw)
        if m:
            try:
                dt = datetime.datetime.fromtimestamp(int(raw), tz=datetime.timezone.utc)
                self.export_ts = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                return
            except (ValueError, OSError):
                pass
        m = _MDY_RE.match(raw)
        if m:
            mm, dd, yyyy = (int(x) for x in m.groups())
            self._set_date(yyyy, mm, dd)
            return
        m = _DMY_RE.match(raw)
        if m:
            dd, mm, yyyy = (int(x) for x in m.groups())
            self._set_date(yyyy, mm, dd)
            return
        self.errors.append("timestamp")

    def _set_date(self, yyyy, mm, dd):
        try:
            dt = datetime.datetime(yyyy, mm, dd)
            self.export_ts = dt.strftime("%Y-%m-%dT00:00:00Z")
        except ValueError:
            self.errors.append("timestamp")

    def _resolve_attrs(self, raw):
        # trailing " [note-###]" style suffix is decorative and not JSON
        cutoff = raw.rfind(" [")
        blob = raw[:cutoff] if cutoff != -1 and raw.rstrip().endswith("]") else raw
        blob = blob.strip()

        for candidate in (blob, blob.replace("'", '"')):
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                self.attrs = parsed
                return
        self.errors.append("attrs")

    def as_record(self):
        return {
            "store_id": self.store_id,
            "sku": self.sku,
            "quantity": self.quantity,
            "export_ts": self.export_ts,
            "extra_attrs": self.attrs,
        }

    def dedupe_key(self):
        return (self.store_id, self.sku, self.export_ts)


def iter_rows(path):
    with open(path, "rb") as fh:
        body = fh.read()
    lines = [ln for ln in body.split(b"\n") if ln.strip() != b""]
    if lines:
        lines = lines[1:]  # header
    for raw_line in lines:
        text = sniff_and_decode(raw_line)
        reader = csv.reader(io.StringIO(text), dialect=PipeDialect)
        try:
            fields = next(reader)
        except StopIteration:
            fields = []
        yield text, fields


def main():
    seen = set()
    accepted = []
    rejected = []

    for original_text, fields in iter_rows(SRC):
        rec = RecordBuilder(fields).run()
        if not rec.ok():
            rejected.append(original_text)
            continue
        key = rec.dedupe_key()
        if key in seen:
            continue
        seen.add(key)
        accepted.append(rec.as_record())

    with open(DST_OK, "w") as fh:
        for rec in accepted:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")

    with open(DST_BAD, "w") as fh:
        for line in rejected:
            fh.write(line + "\n")


if __name__ == "__main__":
    main()
