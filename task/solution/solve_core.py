#!/usr/bin/env python3
"""
Reference (oracle) implementation.

Reads /app/data/inventory_export.dat and writes:
  /app/output/inventory_normalized.jsonl
  /app/output/rejected_rows.log

Design note: fields are split with a bounded maxsplit (extra_attrs is always
the final column, so it may safely contain literal '|' characters), extra_attrs
is parsed via strict JSON with a Python-literal fallback, and its true end is
located by brace/bracket depth-matching rather than a naive rfind. Each
candidate record is represented as a small state machine (RecordBuilder) that
accumulates failures instead of raising early -- this keeps every field's
validation independent so a single bad field doesn't mask what else is wrong
with a row.
"""
import ast
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


def sniff_and_decode(raw: bytes) -> str:
    """utf-8 first; cp1252 as the fallback encoding for legacy POS exports."""
    for codec in ("utf-8", "cp1252"):
        try:
            return raw.decode(codec)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _find_json_boundary(raw):
    """Return (json_blob, suffix) by walking forward from the first '{' and
    tracking brace/bracket depth (while skipping over string contents) until
    the depth returns to zero -- i.e. the true end of the top-level JSON
    object. A one-line rfind('}') or rfind(' [') is fooled whenever the
    object itself contains a nested array or object, since the LAST '}' or
    '[' in the line may belong to that nested structure rather than to the
    decorative suffix.
    """
    raw = raw.strip()
    if not raw.startswith("{"):
        return raw, ""
    depth = 0
    in_str = False
    quote_char = ""
    escape = False
    for i, ch in enumerate(raw):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote_char:
                in_str = False
            continue
        if ch in ("'", '"'):
            in_str = True
            quote_char = ch
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return raw[: i + 1], raw[i + 1 :].strip()
    return raw, ""


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
        # The trailing " [note-###]" style suffix is decorative and not
        # JSON; find the true end of the JSON object by depth-matching
        # rather than a naive rfind, since the object itself may contain
        # nested arrays/objects.
        blob, _ = _find_json_boundary(raw)
        blob = blob.strip()

        try:
            parsed = json.loads(blob)
            if isinstance(parsed, dict):
                self.attrs = parsed
                return
        except json.JSONDecodeError:
            pass

        # Fall back to Python literal syntax. A blind "'" -> '"' character
        # swap corrupts values that legitimately contain an apostrophe (e.g.
        # a double-quoted value inside a single-quoted object); parsing the
        # blob as a Python literal handles mixed/nested quoting correctly.
        try:
            parsed = ast.literal_eval(blob)
        except (ValueError, SyntaxError):
            self.errors.append("attrs")
            return
        if isinstance(parsed, dict) and all(isinstance(k, str) for k in parsed):
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

    def completeness_score(self):
        """(populated attrs key count, has-non-null-quantity) — used to resolve
        conflicting duplicates that share a key but disagree in content."""
        attrs_count = len(self.attrs) if self.attrs else 0
        has_qty = 1 if self.quantity is not None else 0
        return (attrs_count, has_qty)


def iter_rows(path):
    with open(path, "rb") as fh:
        body = fh.read()
    lines = [ln for ln in body.split(b"\n") if ln.strip() != b""]
    if lines:
        lines = lines[1:]  # header
    for raw_line in lines:
        text = sniff_and_decode(raw_line)
        # maxsplit=len(COLUMNS)-1: only extra_attrs (the final column) may
        # legitimately contain a literal '|' inside a quoted JSON string
        # value, so it must not be treated as a further delimiter.
        fields = text.split("|", len(COLUMNS) - 1)
        if fields == [""]:
            fields = []
        yield text, fields


def main():
    best = {}  # key -> RecordBuilder currently kept for that key
    order = []  # preserves first-seen order of keys for stable output ordering
    rejected = []

    for original_text, fields in iter_rows(SRC):
        rec = RecordBuilder(fields).run()
        if not rec.ok():
            rejected.append(original_text)
            continue
        key = rec.dedupe_key()
        if key not in best:
            best[key] = rec
            order.append(key)
            continue
        # Conflicting duplicate: same key, different content. Keep whichever
        # record is more complete rather than always keeping the first-seen
        # one, so a later row can legitimately override an earlier one.
        if rec.completeness_score() > best[key].completeness_score():
            best[key] = rec

    with open(DST_OK, "w") as fh:
        for key in order:
            fh.write(json.dumps(best[key].as_record(), sort_keys=True) + "\n")

    with open(DST_BAD, "w") as fh:
        for line in rejected:
            fh.write(line + "\n")


if __name__ == "__main__":
    main()
