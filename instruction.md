You are given a legacy inventory export file at `/app/data/inventory_export.dat`. It was produced by merging pipe-delimited (`|`) exports from three different point-of-sale systems, and the merge introduced several data-quality problems you must handle correctly. The file has a header line (`store_id|sku|quantity|export_ts|extra_attrs`) followed by one record per line.

**Known issues in the input file:**

- **Encoding:** Some rows are UTF-8, others are Latin-1 (cp1252), mixed within the same file with no per-row marker.

- **Timestamps:** The `export_ts` field appears in one of three formats: `MM/DD/YYYY`, `DD-MM-YYYY`, or Unix epoch seconds (a bare string of 9–10 digits), with no marker of which format a given row uses.

- **`extra_attrs` column:** Holds a JSON object that may be single-quoted, double-quoted, or have a decorative non-JSON suffix appended after the object's closing brace — for example ` [note-945]` or ` [ref-{42}]`. That suffix, which may itself contain various characters including braces, must never contribute to the parsed object. The object may nest arrays or other objects as values. A string value inside the object may contain a literal `|` or a literal apostrophe even when the object as a whole uses single-quote style — `extra_attrs` is always the fifth and final column, so any `|` or `'` inside its value belongs to that value, not to column splitting or to the surrounding quote style.

- **Duplicates:** Some rows share the same `(store_id, sku, export_ts)` after normalization; treat these as duplicates of one record. Duplicates are not always byte-for-byte identical — some are retry artifacts with identical content, but others are conflicting: the same key with a different quantity or `extra_attrs`. When rows for a key conflict, keep the one whose `extra_attrs` object has more top-level keys (it is the more complete record); if the key-counts are tied, keep whichever of the tied rows has a non-null quantity; if still tied, keep the row that appears first in the file.

- **Null quantity sentinels:** The quantity field may be the empty string, the literal text `NULL`, `N/A`, or `-1` to represent a missing value. These must be matched exactly on the raw field value — a padded variant such as `" NULL "` does not match and must be handled as any other non-integer value.

- **Truncated rows:** A small number of rows are cut off mid-line and do not have the full 5 pipe-delimited fields.

---

**Build a program** that reads `/app/data/inventory_export.dat` and produces exactly these two outputs:

**`/app/output/inventory_normalized.jsonl`** — newline-delimited JSON, one object per valid, deduplicated record (any key order), containing exactly these five keys:

- `store_id`: string, copied as-is.
- `sku`: string, copied as-is.
- `quantity`: integer, or `null` if the raw value was exactly `""`, `"NULL"`, `"N/A"`, or `"-1"`. Any other non-integer value (including a decimal such as `"7.5"` or a whitespace-padded sentinel) makes the row invalid.
- `export_ts`: normalized to ISO-8601 UTC formatted exactly as `YYYY-MM-DDTHH:MM:SSZ`. Calendar-date inputs (`MM/DD/YYYY` or `DD-MM-YYYY`) become that date at `00:00:00Z`; epoch-second inputs convert to their exact UTC time. A value matching none of the three formats, or not a real calendar date (e.g. month 2 day 30), makes the row invalid.
- `extra_attrs`: a JSON object reconstructed from the blob after accounting for both quote styles and excluding any trailing decorative suffix. A blob that still does not parse as a JSON object (e.g. truncated or missing a closing brace) makes the row invalid.

**`/app/output/rejected_rows.log`** — the original text of every invalid input row (decoded to a normal readable string, not raw bytes), one per line, in any order. Do not include the header line, and do not include rows that were dropped only because they were duplicates of an already-accepted valid row — only rows that failed validation belong here.

A row is invalid — and must be excluded from `inventory_normalized.jsonl` — if it does not split into exactly 5 pipe-delimited fields, or if `quantity`, `export_ts`, or `extra_attrs` fails to parse per the rules above. Skip the header line entirely (it is never valid or invalid data).

**`/app/run.sh`** — an executable file (no arguments) that reads `/app/data/inventory_export.dat` fresh each time it is invoked and (re)writes both output files above. It will later be re-run against a different input file with the same corruption patterns but different data placed at the same path, so it must implement the parsing logic generally rather than hardcoding anything specific to the file you were given.
