#!/usr/bin/env python3
"""
Reference (Oracle) solution.

Reads /app/data/inventory_export.dat and writes:
  /app/output/inventory_normalized.jsonl
  /app/output/rejected_rows.log
"""
import json
import re
import sys
import datetime

INPUT_PATH = "/app/data/inventory_export.dat"
OUT_JSONL = "/app/output/inventory_normalized.jsonl"
OUT_REJECTED = "/app/output/rejected_rows.log"

EXPECTED_FIELDS = 5
NULL_REPRS = {"", "NULL", "N/A", "-1"}


def decode_line(raw_bytes):
    """Try utf-8, fall back to latin-1/cp1252."""
    try:
        return raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return raw_bytes.decode("cp1252", errors="replace")


def parse_date(date_str):
    """Return an ISO-8601 UTC-midnight string, or None if unparseable."""
    date_str = date_str.strip()
    # epoch seconds: all digits
    if re.fullmatch(r"\d{9,10}", date_str):
        try:
            dt = datetime.datetime.fromtimestamp(int(date_str), tz=datetime.timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except (ValueError, OSError):
            return None
    # MM/DD/YYYY
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", date_str)
    if m:
        mm, dd, yyyy = map(int, m.groups())
        try:
            dt = datetime.datetime(yyyy, mm, dd)
            return dt.strftime("%Y-%m-%dT00:00:00Z")
        except ValueError:
            return None
    # DD-MM-YYYY
    m = re.fullmatch(r"(\d{1,2})-(\d{1,2})-(\d{4})", date_str)
    if m:
        dd, mm, yyyy = map(int, m.groups())
        try:
            dt = datetime.datetime(yyyy, mm, dd)
            return dt.strftime("%Y-%m-%dT00:00:00Z")
        except ValueError:
            return None
    return None


def parse_quantity(qty_str):
    """Returns (ok, value). value is None for a recognized null representation."""
    s = qty_str.strip()
    if s in NULL_REPRS:
        return True, None
    try:
        return True, int(s)
    except ValueError:
        return False, None


def extract_attrs(attrs_blob):
    """
    attrs_blob may have trailing ' [note-123]' junk appended (from the fixture);
    strip anything from the last ' [' onward before parsing.
    Returns (ok, dict_or_None).
    """
    blob = attrs_blob
    idx = blob.rfind(" [")
    if idx != -1 and blob.rstrip().endswith("]"):
        blob = blob[:idx]
    blob = blob.strip()

    # try strict JSON first
    try:
        obj = json.loads(blob)
        if isinstance(obj, dict):
            return True, obj
    except json.JSONDecodeError:
        pass

    # try repairing single-quoted, Python-dict-style JSON
    repaired = blob.replace("'", '"')
    try:
        obj = json.loads(repaired)
        if isinstance(obj, dict):
            return True, obj
    except json.JSONDecodeError:
        pass

    return False, None


def main():
    with open(INPUT_PATH, "rb") as f:
        raw_lines = f.read().split(b"\n")

    # drop header + trailing empty line(s)
    lines = [l for l in raw_lines if l.strip() != b""]
    if lines:
        lines = lines[1:]  # drop header row

    valid_records = []
    rejected_raw_lines = []
    seen_keys = set()

    for raw in lines:
        text = decode_line(raw)
        fields = text.split("|")
        if len(fields) != EXPECTED_FIELDS:
            rejected_raw_lines.append(text)
            continue

        store_id, sku, qty_str, date_str, attrs_blob = fields

        ts = parse_date(date_str)
        if ts is None:
            rejected_raw_lines.append(text)
            continue

        qty_ok, qty_val = parse_quantity(qty_str)
        if not qty_ok:
            rejected_raw_lines.append(text)
            continue

        attrs_ok, attrs_val = extract_attrs(attrs_blob)
        if not attrs_ok:
            rejected_raw_lines.append(text)
            continue

        key = (store_id, sku, ts)
        if key in seen_keys:
            continue  # duplicate -- silently drop, not rejected
        seen_keys.add(key)

        valid_records.append({
            "store_id": store_id,
            "sku": sku,
            "quantity": qty_val,
            "export_ts": ts,
            "extra_attrs": attrs_val,
        })

    with open(OUT_JSONL, "w") as f:
        for rec in valid_records:
            f.write(json.dumps(rec, sort_keys=True) + "\n")

    with open(OUT_REJECTED, "w") as f:
        for line in rejected_raw_lines:
            f.write(line + "\n")


if __name__ == "__main__":
    main()
