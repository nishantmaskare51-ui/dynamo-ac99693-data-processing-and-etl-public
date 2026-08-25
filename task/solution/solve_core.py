import json
import ast
import re
from datetime import datetime, timezone
import os

def extract_json_blob(s):
    """
    Finds the first '{' and tracks brace depth to find the matching '}',
    skipping characters inside single or double quotes and handling escaped quotes.
    """
    start = s.find('{')
    if start == -1:
        return None
    
    depth = 0
    in_quote = None
    escape = False
    
    for i in range(start, len(s)):
        char = s[i]
        if in_quote:
            if escape:
                escape = False
            elif char == '\\':
                escape = True
            elif char == in_quote:
                in_quote = None
        else:
            if char in '"\'':
                in_quote = char
            elif char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0:
                    return s[start:i+1]
    return None

def parse_extra_attrs(blob):
    """
    Attempts to parse a string as a JSON object or a Python literal dict.
    """
    if not blob:
        return None
    # Try standard JSON first
    try:
        res = json.loads(blob)
        if isinstance(res, dict):
            return res
    except:
        pass
    # Try Python literal for single-quote styles (legacy POS exports)
    try:
        res = ast.literal_eval(blob)
        if isinstance(res, dict):
            return res
    except:
        pass
    return None

def main():
    input_file = '/app/data/inventory_export.dat'
    output_jsonl = '/app/output/inventory_normalized.jsonl'
    output_log = '/app/output/rejected_rows.log'
    
    os.makedirs(os.path.dirname(output_jsonl), exist_ok=True)
    
    if not os.path.exists(input_file):
        return

    with open(input_file, 'rb') as f:
        lines = f.readlines()
        
    if not lines:
        return
        
    # Skip the header line entirely
    data_lines = lines[1:]
    
    valid_candidates = []
    rejected_rows = []
    
    # Regex patterns for strict validation
    int_pattern = re.compile(r'^-?\d+$')
    epoch_pattern = re.compile(r'^\d{9,10}$')
    dash_date_pattern = re.compile(r'^\d{2}-\d{2}-\d{4}$')
    slash_date_pattern = re.compile(r'^\d{2}/\d{2}/\d{4}$')
    
    for idx, line_bytes in enumerate(data_lines):
        # Handle mixed UTF-8 and CP1252 encodings
        try:
            line_str = line_bytes.decode('utf-8')
        except UnicodeDecodeError:
            line_str = line_bytes.decode('cp1252')
        
        clean_line = line_str.rstrip('\r\n')
        
        # 1. Structural validation: Exactly 5 fields
        # extra_attrs is the 5th field and may contain pipes; split at most 4 times.
        parts = clean_line.split('|', 4)
        if len(parts) != 5:
            rejected_rows.append(clean_line)
            continue
            
        store_id, sku, qty_raw, ts_raw, extra_raw = parts
        
        # 2. Quantity validation
        qty = None
        if qty_raw in ("", "NULL", "N/A", "-1"):
            qty = None
        else:
            if int_pattern.match(qty_raw):
                qty = int(qty_raw)
            else:
                rejected_rows.append(clean_line)
                continue
                
        # 3. Timestamp normalization
        normalized_ts = None
        try:
            if epoch_pattern.match(ts_raw):
                dt = datetime.fromtimestamp(int(ts_raw), tz=timezone.utc)
                normalized_ts = dt.strftime('%Y-%m-%dT%H:%M:%SZ')
            elif dash_date_pattern.match(ts_raw):
                # DD-MM-YYYY
                dt = datetime.strptime(ts_raw, '%d-%m-%Y').replace(tzinfo=timezone.utc)
                normalized_ts = dt.strftime('%Y-%m-%dT%H:%M:%SZ')
            elif slash_date_pattern.match(ts_raw):
                # MM/DD/YYYY
                dt = datetime.strptime(ts_raw, '%m/%d/%Y').replace(tzinfo=timezone.utc)
                normalized_ts = dt.strftime('%Y-%m-%dT%H:%M:%SZ')
        except (ValueError, OSError, OverflowError):
            pass
        
        if not normalized_ts:
            rejected_rows.append(clean_line)
            continue
            
        # 4. Extra Attributes validation (stripping suffixes)
        blob = extract_json_blob(extra_raw)
        extra_attrs = parse_extra_attrs(blob)
        
        if extra_attrs is None:
            rejected_rows.append(clean_line)
            continue
            
        # Valid record found; store with scoring for deduplication logic
        valid_candidates.append({
            'store_id': store_id,
            'sku': sku,
            'quantity': qty,
            'export_ts': normalized_ts,
            'extra_attrs': extra_attrs,
            'score': (len(extra_attrs), 1 if qty is not None else 0),
            'index': idx
        })
        
    # 5. Deduplication and Conflict Resolution
    # Group by (store_id, sku, export_ts)
    # Win conditions: 1. Most top-level keys 2. Non-null quantity 3. First in file
    winners = {}
    for cand in valid_candidates:
        key = (cand['store_id'], cand['sku'], cand['export_ts'])
        if key not in winners:
            winners[key] = cand
        else:
            # Use strictly greater than to preserve the first-seen in case of a tie
            if cand['score'] > winners[key]['score']:
                winners[key] = cand
    
    # Sort by original index to keep relative file order if desired
    final_records = sorted(winners.values(), key=lambda x: x['index'])
    
    # Output: Valid records to JSONL
    with open(output_jsonl, 'w', encoding='utf-8') as f:
        for rec in final_records:
            json_line = {
                'store_id': rec['store_id'],
                'sku': rec['sku'],
                'quantity': rec['quantity'],
                'export_ts': rec['export_ts'],
                'extra_attrs': rec['extra_attrs']
            }
            f.write(json.dumps(json_line) + '\n')
            
    # Output: Rejected rows (Original text)
    with open(output_log, 'w', encoding='utf-8') as f:
        for line in rejected_rows:
            f.write(line + '\n')

if __name__ == '__main__':
    main()
