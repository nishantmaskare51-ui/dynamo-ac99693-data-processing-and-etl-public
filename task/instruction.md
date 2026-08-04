### Add these lines under **Input Characteristics**

* A logical record must be interpreted independently of the validity of preceding or subsequent records.
* Validation and normalization of one field must not modify the interpretation of any other field except where explicitly required by this specification.

---

### Add this paragraph at the end of **Timestamp Rules**

Successful normalization is part of record validation. A timestamp that cannot be normalized unambiguously invalidates the entire record.

---

### Add this paragraph immediately before **"A row is invalid if a complete JSON object cannot be reconstructed."** in **extra_attrs Rules**

Only syntactic reconstruction of the outermost JSON object is required. Decorative metadata must never contribute to the parsed object, duplicate resolution, or output.

---

### Add this as **Rule 5** under **Duplicate Resolution**

5. All duplicate-resolution decisions must be deterministic and depend exclusively on information contained within the candidate records.

---

### Add these paragraphs at the very end of **Required Outputs**

The generated output files must contain only information derived from the input dataset and the rules defined in this specification.

No implementation-defined behavior may influence which records are accepted, rejected, or retained.
