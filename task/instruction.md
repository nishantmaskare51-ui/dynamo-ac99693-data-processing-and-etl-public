# Normalize a Corrupted Multi-Source Inventory Export

You are given a legacy inventory export file at `/app/data/inventory_export.dat`. It
was produced by merging pipe-delimited (`|`) exports from three different point-of-
sale systems, and the merge introduced several data-quality problems that you must
handle correctly.

## Known issues in the input file

- **Mixed encodings**: some rows are UTF-8, others are Latin-1 (cp1252), mixed within
  the same file with no per-row marker.
- **Inconsistent dates**: the `export_ts` field appears in one of three formats:
  `MM/DD/YYYY`, `DD-MM-YYYY`, or Unix epoch seconds (as a string of digits).
- **Malformed embedded JSON**: the `extra_attrs` column contains a JSON blob that is
  sometimes single-quoted, sometimes double-quoted, and sometimes truncated
  (missing closing braces).
- **Duplicate rows**: some rows are byte-for-byte duplicates (retry artifacts). Treat
  two rows as duplicates if they share the same `(store_id, sku, export_ts)` after
  normalization.
- **Inconsistent nulls**: the `quantity` field may be an empty string, the literal
  text `NULL`, `-1`, or `N/A` to represent a missing value.
- **Truncated rows**: a small number of rows are cut off mid-line (incomplete
  export) and do not have the full expected number of pipe-delimited fields.

## What to build

Write a program that reads `/app/data/inventory_export.dat` and produces two output
files:

1. `/app/output/inventory_normalized.jsonl` — newline-delimited JSON, one object per
   valid, deduplicated record, with exactly this schema:
   
2. `/app/run.sh` — an executable entry point (no arguments) that produces the two files above when invoked.
